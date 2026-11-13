# -*- coding: utf-8 -*-
"""跨进程共享请求许可、配额租约与风险熔断协调器。"""

import math
import os
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from .errors import (BackpressureError, CircuitOpenError, CoordinatorStateError,
                     LeaseError)
from .policies import DEFAULT_POLICIES, ProviderPolicy, normalize_provider, resolve_provider
from .state import JsonStateStore


BACKPRESSURE_SECONDS = 60.0


def _new_provider_state():
    return {
        "reservations": [],
        "network_slots": [],
        "last_reserved": None,
        "last_network_started": None,
        "reserved_slots": 0,
        "network_started": 0,
        "responses": 0,
        "in_flight": {},
        "consecutive_risk": 0,
        "circuit": "CLOSED",
        "opened_at": None,
        "open_until": None,
    }


def _number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CoordinatorStateError(f"协调器字段 {field} 非有限数值；已 fail-closed")
    return float(value)


@dataclass(frozen=True)
class RequestPermit:
    """一次独立网络尝试的许可；重试必须重新 acquire。"""

    coordinator: object
    provider: str
    lease_id: str
    reserved_at: float
    scheduled_for: float
    probe: bool = False

    @property
    def not_before(self):
        return self.scheduled_for

    def start(self):
        return self.coordinator.mark_network_started(self)

    def success(self):
        return self.coordinator.record_success(self)

    def risk_control(self, reason=""):
        return self.coordinator.record_risk_control(self, reason=reason)

    def failure(self, reason="", response_received=False):
        return self.coordinator.record_failure(
            self, reason=reason, response_received=response_received)

    def cancel(self):
        return self.coordinator.cancel(self)

    def renew(self, seconds=None):
        return self.coordinator.renew(self, seconds=seconds)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.cancel()
        except LeaseError:
            pass
        return False


class Coordinator:
    """用同一 JSON 状态文件协调本机所有进程。

    clock 可传 callable 或带 time() 的对象，sleeper 可传 callable 或带
    sleep() 的对象，便于确定性测试。生产环境默认使用 time.time/time.sleep。
    """

    def __init__(self, state_dir=None, *, store=None, policies=None, clock=None,
                 sleeper=None, backpressure_seconds=BACKPRESSURE_SECONDS,
                 poll_interval=0.05):
        if store is None:
            root = (Path.home() / ".fund_data_cache" / "coordinator"
                    if state_dir is None else Path(state_dir))
            store = JsonStateStore(root)
        self.store = store
        self.policies = dict(DEFAULT_POLICIES)
        for name, policy in (policies or {}).items():
            provider = normalize_provider(name)
            base = self.policies[provider]
            if isinstance(policy, dict):
                policy = replace(base, **policy)
            if not isinstance(policy, ProviderPolicy):
                raise TypeError("policy 必须是 ProviderPolicy 或字段 dict")
            self.policies[provider] = policy
        self._clock = clock or time
        self._sleeper = sleeper or time
        if (isinstance(backpressure_seconds, bool)
                or not isinstance(backpressure_seconds, (int, float))
                or not math.isfinite(backpressure_seconds) or backpressure_seconds < 0):
            raise ValueError("backpressure_seconds 必须是 >= 0 的有限数值")
        if (isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float))
                or not math.isfinite(poll_interval) or poll_interval <= 0):
            raise ValueError("poll_interval 必须是 > 0 的有限数值")
        self.backpressure_seconds = float(backpressure_seconds)
        self.poll_interval = float(poll_interval)

    def _now(self):
        fn = self._clock.time if hasattr(self._clock, "time") else self._clock
        try:
            return _number(fn(), "clock")
        except CoordinatorStateError:
            raise
        except Exception as exc:
            raise CoordinatorStateError(f"时钟不可用: {exc}；已 fail-closed") from exc

    def _sleep(self, seconds):
        if seconds <= 0:
            return
        fn = self._sleeper.sleep if hasattr(self._sleeper, "sleep") else self._sleeper
        try:
            fn(seconds)
        except Exception as exc:
            raise CoordinatorStateError(f"背压等待被中断: {exc}；已 fail-closed") from exc

    def policy(self, provider):
        return self.policies[normalize_provider(provider)]

    def _provider_state(self, state, provider):
        providers = state["providers"]
        if provider not in providers:
            providers[provider] = _new_provider_state()
        current = providers[provider]
        # 对缺失的新字段不静默补零：损坏状态必须拒绝，而不是放开限制。
        expected = set(_new_provider_state())
        if set(current) != expected:
            raise CoordinatorStateError(
                f"provider {provider} 状态字段缺失或未知；已 fail-closed")
        if current["last_reserved"] is not None:
            _number(current["last_reserved"], f"{provider}.last_reserved")
        if current["last_network_started"] is not None:
            _number(current["last_network_started"], f"{provider}.last_network_started")
        if not isinstance(current["network_slots"], list):
            raise CoordinatorStateError(f"{provider}.network_slots 损坏；已 fail-closed")
        for slot in current["network_slots"]:
            _number(slot, f"{provider}.network_slots")
        for field in ("opened_at", "open_until"):
            if current[field] is not None:
                _number(current[field], f"{provider}.{field}")
        for item in current["reservations"]:
            if not isinstance(item, dict) or set(item) != {"slot", "lease_id"}:
                raise CoordinatorStateError(f"{provider}.reservations 损坏；已 fail-closed")
            _number(item["slot"], f"{provider}.reservations.slot")
            if not isinstance(item["lease_id"], str) or not item["lease_id"]:
                raise CoordinatorStateError("reservation lease_id 损坏；已 fail-closed")
        for lease_id, lease in current["in_flight"].items():
            required = {"reserved_at", "scheduled_for", "started_at", "expires_at",
                        "probe", "owner_pid", "response_recorded"}
            if not isinstance(lease, dict) or set(lease) != required:
                raise CoordinatorStateError(f"{provider}.in_flight 损坏；已 fail-closed")
            if not isinstance(lease_id, str) or not lease_id:
                raise CoordinatorStateError("in_flight lease_id 损坏；已 fail-closed")
            for field in ("reserved_at", "scheduled_for", "expires_at"):
                _number(lease[field], f"{provider}.{field}")
            if lease["started_at"] is not None:
                _number(lease["started_at"], f"{provider}.started_at")
            if not isinstance(lease["probe"], bool) or not isinstance(
                    lease["response_recorded"], bool):
                raise CoordinatorStateError("lease 布尔字段损坏；已 fail-closed")
        return current

    def _prune(self, current, provider, now):
        policy = self.policies[provider]
        cutoff = now - policy.window_seconds
        current["reservations"] = [
            item for item in current["reservations"]
            if _number(item["slot"], "reservation.slot") > cutoff]
        current["network_slots"] = [
            slot for slot in current["network_slots"]
            if _number(slot, "network_slots") > cutoff]
        expired = [lease_id for lease_id, lease in current["in_flight"].items()
                   if _number(lease["expires_at"], "expires_at") <= now]
        for lease_id in expired:
            del current["in_flight"][lease_id]
        return len(expired)

    def _advance_circuit(self, current, now):
        if current["circuit"] == "OPEN" and current["open_until"] is not None:
            if now >= _number(current["open_until"], "open_until"):
                current["circuit"] = "HALF_OPEN"
        return current["circuit"]

    def _circuit_error(self, provider, current, now):
        retry = None
        if current["open_until"] is not None:
            retry = max(0.0, _number(current["open_until"], "open_until") - now)
        state = current["circuit"]
        detail = "等待 half-open 探针恢复" if state == "HALF_OPEN" else "熔断冷却中"
        return CircuitOpenError(
            f"provider {provider} {state}（{detail}），请求已取消且不会访问网络",
            provider=provider, retry_after=retry)

    def _attempt_reserve(self, target, slot_limit=None):
        provider = resolve_provider(target)
        policy = self.policies[provider]
        now = self._now()
        allowed_delay = self.backpressure_seconds if slot_limit is None else max(0.0, slot_limit)
        error = None
        congested = False
        permit = None
        with self.store.transaction() as state:
            current = self._provider_state(state, provider)
            self._prune(current, provider, now)
            circuit = self._advance_circuit(current, now)
            probes = [lease for lease in current["in_flight"].values() if lease["probe"]]
            if circuit == "OPEN" or (circuit == "HALF_OPEN" and probes):
                error = self._circuit_error(provider, current, now)
            elif len(current["in_flight"]) >= policy.max_in_flight:
                congested = True
            else:
                candidate = now
                if current["last_reserved"] is not None:
                    candidate = max(candidate, _number(
                        current["last_reserved"], "last_reserved") + policy.min_interval)
                reservations = current["reservations"]
                if len(reservations) >= policy.max_requests:
                    ordered = sorted(_number(item["slot"], "reservation.slot")
                                     for item in reservations)
                    candidate = max(candidate, ordered[len(reservations) - policy.max_requests]
                                    + policy.window_seconds)
                delay = max(0.0, candidate - now)
                if delay > allowed_delay + 1e-9:
                    error = BackpressureError(
                        f"provider {provider} 配额需等待 {delay:.3f}s，超过剩余 "
                        f"{allowed_delay:.3f}s 背压上限",
                        provider=provider, retry_after=delay)
                else:
                    lease_id = uuid.uuid4().hex
                    probe = circuit == "HALF_OPEN"
                    current["reservations"].append({"slot": candidate, "lease_id": lease_id})
                    current["last_reserved"] = (candidate if current["last_reserved"] is None
                                                else max(current["last_reserved"], candidate))
                    current["reserved_slots"] += 1
                    current["in_flight"][lease_id] = {
                        "reserved_at": now,
                        "scheduled_for": candidate,
                        "started_at": None,
                        "expires_at": candidate + policy.lease_seconds,
                        "probe": probe,
                        "owner_pid": os.getpid(),
                        "response_recorded": False,
                    }
                    permit = RequestPermit(self, provider, lease_id, now, candidate, probe)
        return permit, congested, error

    def reserve(self, target):
        """原子预留一次尝试，不等待 future slot，也不等待在途许可。"""

        permit, congested, error = self._attempt_reserve(target)
        if error is not None:
            raise error
        if congested:
            provider = resolve_provider(target)
            raise BackpressureError(
                f"provider {provider} 在途许可已满", provider=provider, retry_after=None)
        return permit

    def acquire(self, target, *, wait=True):
        """取得许可；容量等待和 future slot 等待合计最多 60s（可配置）。"""

        provider = resolve_provider(target)
        deadline = self._now() + self.backpressure_seconds
        while True:
            permit, congested, error = self._attempt_reserve(
                provider, slot_limit=deadline - self._now())
            if error is not None:
                raise error
            if permit is not None:
                break
            if not congested:
                raise CoordinatorStateError("预约未返回许可或错误；已 fail-closed")
            now = self._now()
            if now >= deadline:
                raise BackpressureError(
                    f"provider {provider} 在途许可等待超过 {self.backpressure_seconds}s",
                    provider=provider, retry_after=None)
            self._sleep(min(self.poll_interval, deadline - now))
        if wait:
            self._wait_until(permit.scheduled_for)
            self.validate(permit)
        return permit

    def _wait_until(self, timestamp):
        while True:
            remaining = _number(timestamp, "scheduled_for") - self._now()
            if remaining <= 1e-9:
                return
            self._sleep(remaining)

    def _lease_for_start(self, current, permit, now):
        circuit = self._advance_circuit(current, now)
        if circuit == "OPEN" or (circuit == "HALF_OPEN" and not permit.probe):
            raise self._circuit_error(permit.provider, current, now)
        lease = current["in_flight"].get(permit.lease_id)
        if lease is None:
            raise LeaseError(f"许可 {permit.lease_id} 已过期、取消或完成")
        if lease["started_at"] is not None:
            raise LeaseError(f"许可 {permit.lease_id} 已开始网络，不能重复 start")
        if circuit == "HALF_OPEN" and not lease["probe"]:
            raise self._circuit_error(permit.provider, current, now)
        return lease

    def validate(self, permit):
        """发包前验证许可；OPEN 时已预留但未开始的请求在此被取消。"""

        now = self._now()
        with self.store.transaction() as state:
            current = self._provider_state(state, permit.provider)
            self._prune(current, permit.provider, now)
            self._lease_for_start(current, permit, now)
        return permit

    def mark_network_started(self, permit):
        """实际 transport 入口；延迟许可仍须满足真实起始滑窗与最小间隔。"""

        self._wait_until(permit.scheduled_for)
        policy = self.policies[permit.provider]
        while True:
            now = self._now()
            wait_for = 0.0
            with self.store.transaction() as state:
                current = self._provider_state(state, permit.provider)
                self._prune(current, permit.provider, now)
                lease = self._lease_for_start(current, permit, now)
                earliest = now
                if current["last_network_started"] is not None:
                    earliest = max(earliest, _number(
                        current["last_network_started"], "last_network_started")
                        + policy.min_interval)
                started_slots = current["network_slots"]
                if len(started_slots) >= policy.max_requests:
                    ordered = sorted(_number(slot, "network_slots") for slot in started_slots)
                    earliest = max(earliest, ordered[len(started_slots) - policy.max_requests]
                                   + policy.window_seconds)
                if earliest > now + 1e-9:
                    wait_for = earliest - now
                else:
                    lease["started_at"] = now
                    lease["expires_at"] = now + policy.lease_seconds
                    current["network_started"] += 1
                    current["network_slots"].append(now)
                    current["last_network_started"] = now
                    current["last_reserved"] = (
                        now if current["last_reserved"] is None
                        else max(current["last_reserved"], now))
                    for item in current["reservations"]:
                        if item["lease_id"] == permit.lease_id:
                            item["slot"] = now
                            break
                    return permit
            self._sleep(wait_for)

    start = mark_network_started

    def _open_circuit(self, current, provider, now):
        current["circuit"] = "OPEN"
        current["opened_at"] = now
        current["open_until"] = now + self.policies[provider].open_seconds
        # 已发包请求保留到响应/租约结束；未开始请求全部撤销，绝不能再穿透 OPEN。
        for lease_id in list(current["in_flight"]):
            if current["in_flight"][lease_id]["started_at"] is None:
                del current["in_flight"][lease_id]

    def _finish(self, permit, *, success, risk_control, response_received, reason):
        now = self._now()
        with self.store.transaction() as state:
            current = self._provider_state(state, permit.provider)
            self._prune(current, permit.provider, now)
            lease = current["in_flight"].get(permit.lease_id)
            if lease is None:
                raise LeaseError(f"许可 {permit.lease_id} 已过期、取消或完成")
            if lease["started_at"] is None:
                raise LeaseError("未开始网络的许可不能记录响应；请使用 cancel")
            if lease["response_recorded"]:
                raise LeaseError("该许可已记录响应")
            lease["response_recorded"] = True
            if response_received:
                current["responses"] += 1
            del current["in_flight"][permit.lease_id]
            circuit = self._advance_circuit(current, now)
            if success:
                if permit.probe and circuit == "HALF_OPEN":
                    current["circuit"] = "CLOSED"
                    current["opened_at"] = None
                    current["open_until"] = None
                    current["consecutive_risk"] = 0
                elif circuit == "CLOSED":
                    current["consecutive_risk"] = 0
                # OPEN 中旧请求成功绝不关闸；只有 half-open 探针能恢复。
            elif risk_control:
                if circuit == "CLOSED":
                    current["consecutive_risk"] += 1
                if circuit == "OPEN":
                    current["open_until"] = max(
                        _number(current["open_until"], "open_until"),
                        now + self.policies[permit.provider].open_seconds)
                elif permit.probe or current["consecutive_risk"] >= self.policies[
                        permit.provider].circuit_threshold:
                    self._open_circuit(current, permit.provider, now)
            elif permit.probe and circuit == "HALF_OPEN" and not response_received:
                # 探针未获响应（传输失败）保守重开；已获非风控响应的探针保持
                # HALF_OPEN 待业务裁决——业务成功走 clear_risk_streak 关闸，
                # 不裁决则租约到期中性清理（绝不因「拿到健康响应」惩罚恢复）。
                self._open_circuit(current, permit.provider, now)
        return {
            "provider": permit.provider, "success": success,
            "risk_control": risk_control, "response_received": response_received,
            "reason": reason,
        }

    def record_success(self, permit):
        return self._finish(permit, success=True, risk_control=False,
                            response_received=True, reason="")

    def record_risk_control(self, permit, reason=""):
        return self._finish(permit, success=False, risk_control=True,
                            response_received=True, reason=reason)

    def record_failure(self, permit, reason="", response_received=False):
        return self._finish(permit, success=False, risk_control=False,
                            response_received=response_received, reason=reason)

    def record_response(self, permit, *, success=False, risk_control=False,
                        response_received=True, reason=""):
        """记录一次响应并结算许可；默认中性，不清零也不计风控。

        success=True（清零连击/关闭探针）只能来自业务完整性校验通过的
        显式结论，绝不能由 HTTP 状态码直接推导。
        """
        if success and risk_control:
            raise ValueError("success 与 risk_control 不能同时为真")
        return self._finish(permit, success=success, risk_control=risk_control,
                            response_received=response_received, reason=reason)

    def signal_risk(self, provider, reason=""):
        """业务层在无在途许可时上报的风控信号（与 record_risk_control 同语义）。

        与许可路径的差别：不占用配额、无响应可入账，只累计连击并在达到
        阈值时开闸；HALF_OPEN 窗口内的信号视同探针被风控，直接重开。
        返回 {"provider", "opened", "circuit", "reason"}；是否抛错由调用方
        决定（网关路径已在 record_risk_control 抛过）。
        """
        name = resolve_provider(provider)
        now = self._now()
        with self.store.transaction() as state:
            current = self._provider_state(state, name)
            self._prune(current, name, now)
            circuit = self._advance_circuit(current, now)
            if circuit == "CLOSED":
                current["consecutive_risk"] += 1
            if circuit == "OPEN":
                current["open_until"] = max(
                    _number(current["open_until"], "open_until"),
                    now + self.policies[name].open_seconds)
            elif circuit == "HALF_OPEN" or current["consecutive_risk"] >= \
                    self.policies[name].circuit_threshold:
                self._open_circuit(current, name, now)
            return {"provider": name, "opened": current["circuit"] == "OPEN"
                    and circuit != "OPEN",
                    "circuit": current["circuit"], "reason": reason}

    def clear_risk_streak(self, provider):
        """业务完整性校验成功后的显式清零（Sprint 1 语义，provider 级）。

        HALF_OPEN 时同时关闸：半开窗口只有探针能过网关，此刻收到业务成功
        结论等价于「探针业务校验通过」——关闸恢复是探针路径的正规出口
        （否则中性结算的探针永远无法恢复，闸门退化为永久熔断）。OPEN
        （冷却未满）不动闸门——冷却期内不应有业务成功发生，防御性保持。
        """
        name = resolve_provider(provider)
        now = self._now()
        with self.store.transaction() as state:
            current = self._provider_state(state, name)
            circuit = self._advance_circuit(current, now)
            current["consecutive_risk"] = 0
            if circuit == "HALF_OPEN":
                current["circuit"] = "CLOSED"
                current["opened_at"] = None
                current["open_until"] = None
            return {"provider": name, "circuit": current["circuit"],
                    "consecutive_risk": 0}

    def cancel(self, permit):
        """取消未开始许可；已开始许可按无响应失败结算。"""

        now = self._now()
        with self.store.transaction() as state:
            current = self._provider_state(state, permit.provider)
            self._prune(current, permit.provider, now)
            lease = current["in_flight"].get(permit.lease_id)
            if lease is None:
                raise LeaseError(f"许可 {permit.lease_id} 已过期、取消或完成")
            if lease["started_at"] is not None:
                # 状态写事务内不能调用 _finish 再次加锁写文件。
                del current["in_flight"][permit.lease_id]
                if permit.probe and self._advance_circuit(current, now) == "HALF_OPEN":
                    self._open_circuit(current, permit.provider, now)
                return False
            del current["in_flight"][permit.lease_id]
            return True

    release = cancel

    def renew(self, permit, seconds=None):
        """延长活跃租约；长响应可定期 heartbeat，崩溃进程则无法续租。"""

        duration = (self.policies[permit.provider].lease_seconds
                    if seconds is None else seconds)
        if isinstance(duration, bool) or duration <= 0:
            raise ValueError("租约时长必须 > 0")
        now = self._now()
        with self.store.transaction() as state:
            current = self._provider_state(state, permit.provider)
            self._prune(current, permit.provider, now)
            lease = current["in_flight"].get(permit.lease_id)
            if lease is None:
                raise LeaseError(f"许可 {permit.lease_id} 已过期、取消或完成")
            lease["expires_at"] = now + float(duration)
            return lease["expires_at"]

    heartbeat = renew

    def reap(self, provider):
        """主动清理过期租约与滑出窗口的 reservation，返回清理数量。"""

        name = resolve_provider(provider)
        now = self._now()
        with self.store.transaction() as state:
            current = self._provider_state(state, name)
            before = len(current["reservations"])
            expired = self._prune(current, name, now)
            self._advance_circuit(current, now)
            return {"expired_leases": expired,
                    "expired_reservations": before - len(current["reservations"])}

    def snapshot(self, provider=None):
        """返回可 JSON 序列化的审计快照，不泄露可变的内部状态对象。"""

        names = list(self.policies) if provider is None else [resolve_provider(provider)]
        now = self._now()
        result = {}
        with self.store.transaction() as state:
            for name in names:
                current = self._provider_state(state, name)
                self._prune(current, name, now)
                circuit = self._advance_circuit(current, now)
                result[name] = {
                    "provider": name,
                    "circuit": circuit,
                    "opened_at": current["opened_at"],
                    "open_until": current["open_until"],
                    "reserved_slots": current["reserved_slots"],
                    "network_started": current["network_started"],
                    "responses": current["responses"],
                    "consecutive_risk": current["consecutive_risk"],
                    "active_in_flight": len(current["in_flight"]),
                    "active_reservations": len(current["reservations"]),
                    "active_network_starts": len(current["network_slots"]),
                    "reservations": [dict(item) for item in current["reservations"]],
                    "network_slots": list(current["network_slots"]),
                    "in_flight": {key: dict(value)
                                  for key, value in current["in_flight"].items()},
                    "last_reserved": current["last_reserved"],
                    "last_network_started": current["last_network_started"],
                }
        return result if provider is None else result[names[0]]

    metrics = snapshot
