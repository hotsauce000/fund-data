# -*- coding: utf-8 -*-
"""共享请求协调器确定性测试；全部状态位于独立临时目录，零真实网络。"""

import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fund_data_core import (BackpressureError, CircuitOpenError, Coordinator,
                            CoordinatorStateError, JsonStateStore, LeaseError,
                            ProviderPolicy, UnknownHostError, provider_for_host,
                            provider_for_url, resolve_provider)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = float(now)
        self.lock = threading.Lock()

    def time(self):
        with self.lock:
            return self.now

    def sleep(self, seconds):
        with self.lock:
            self.now += max(0.0, seconds)

    def advance(self, seconds):
        self.sleep(seconds)


def policy(**overrides):
    values = dict(max_requests=100, window_seconds=10.0, min_interval=0.0,
                  max_in_flight=4, lease_seconds=30.0, circuit_threshold=3,
                  open_seconds=60.0)
    values.update(overrides)
    return ProviderPolicy(**values)


def process_success(state_dir, provider_name, queue, delay=0.0):
    try:
        coordinator = Coordinator(state_dir, policies={provider_name: policy(
            min_interval=0.01, max_in_flight=2, window_seconds=0.5)},
            backpressure_seconds=5.0)
        permit = coordinator.acquire(provider_name)
        coordinator.mark_network_started(permit)
        if delay:
            time.sleep(delay)
        coordinator.record_success(permit)
        queue.put(("ok", permit.lease_id))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def process_hold(state_dir, queue):
    try:
        coordinator = Coordinator(state_dir, policies={"eastmoney": policy(
            min_interval=0, max_in_flight=1, lease_seconds=5)})
        permit = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(permit)
        queue.put(("started", permit.lease_id))
        time.sleep(0.35)
        coordinator.record_success(permit)
        queue.put(("done", permit.lease_id))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def process_crash_after_start(state_dir, queue):
    coordinator = Coordinator(state_dir, policies={"eastmoney": policy(
        min_interval=0, max_in_flight=1, lease_seconds=0.15)})
    permit = coordinator.acquire("eastmoney")
    coordinator.mark_network_started(permit)
    queue.put(("started", permit.lease_id))
    queue.close()
    queue.join_thread()
    os._exit(0)  # 模拟进程崩溃：不能执行 record_success/failure。


def process_open_circuit(state_dir, queue):
    try:
        coordinator = Coordinator(state_dir, policies={"eastmoney": policy(
            min_interval=0, circuit_threshold=1)})
        permit = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(permit)
        coordinator.record_risk_control(permit, "test")
        queue.put(("opened", coordinator.snapshot("eastmoney")["circuit"]))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


class ProviderTests(unittest.TestCase):
    def test_eastmoney_aliases_share_one_group(self):
        for value in ("tt", "EM", "push2", "push2his", "eastmoney"):
            with self.subTest(value=value):
                self.assertEqual(resolve_provider(value), "eastmoney")
        for url in ("https://fund.eastmoney.com/a", "https://push2.eastmoney.com/api",
                    "https://push2his.eastmoney.com/api", "http://fundmobapi.eastmoney.com/x"):
            with self.subTest(url=url):
                self.assertEqual(provider_for_url(url), "eastmoney")

    def test_independent_provider_hosts(self):
        cases = {
            "https://webapi.cninfo.com.cn/api": "cninfo",
            "https://yield.chinabond.com.cn/cb": "chinabond",
            "https://danjuanfunds.com/djapi": "danjuan",
            "https://qt.gtimg.cn/q=sh600000": "tencent",
            "https://web.ifzq.gtimg.cn/appstock": "tencent",
            "https://gu.qq.com/sh600000": "tencent",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(provider_for_url(url), expected)

    def test_unknown_host_and_lookalike_are_rejected(self):
        values = ("https://example.com/x", "https://eastmoney.com.evil.test/x",
                  "https://evil.eastmoney.com.evil/x", "file:///tmp/a",
                  "https://user:pass@fund.eastmoney.com/x", "not-a-url")
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(UnknownHostError):
                    provider_for_url(value)
        with self.assertRaises(UnknownHostError):
            provider_for_host("not-eastmoney.com")

    def test_policy_validation(self):
        with self.assertRaises(ValueError):
            policy(max_requests=0)
        with self.assertRaises(ValueError):
            policy(window_seconds=0)
        with self.assertRaises(ValueError):
            policy(lease_seconds=-1)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fund-coordinator-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = FakeClock()

    def coordinator(self, **policy_overrides):
        backpressure = policy_overrides.pop("backpressure_seconds", 60.0)
        poll = policy_overrides.pop("poll_interval", 0.05)
        return Coordinator(self.root, policies={"eastmoney": policy(**policy_overrides)},
                           clock=self.clock, sleeper=self.clock,
                           backpressure_seconds=backpressure, poll_interval=poll)

    def test_minimum_interval_is_shared_across_instances(self):
        first = self.coordinator(min_interval=2.0)
        second = self.coordinator(min_interval=2.0)
        one = first.acquire("tt")
        first.mark_network_started(one)
        first.record_success(one)
        self.assertEqual(self.clock.time(), 1000.0)
        two = second.acquire("push2")
        self.assertEqual(self.clock.time(), 1002.0)
        second.mark_network_started(two)
        second.record_success(two)
        snap = first.snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (2, 2, 2))
        self.assertEqual(first.snapshot("danjuan")["reserved_slots"], 0)

    def test_sliding_window_and_exact_expiry(self):
        coordinator = self.coordinator(max_requests=2, window_seconds=10,
                                       backpressure_seconds=2)
        for _ in range(2):
            permit = coordinator.acquire("eastmoney")
            coordinator.mark_network_started(permit)
            coordinator.record_success(permit)
        with self.assertRaises(BackpressureError) as caught:
            coordinator.acquire("eastmoney")
        self.assertEqual(caught.exception.retry_after, 10.0)
        self.assertEqual(self.clock.time(), 1000.0)
        self.clock.advance(10.0)
        permit = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(permit)
        coordinator.record_success(permit)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual(snap["reserved_slots"], 3)
        self.assertEqual(snap["active_reservations"], 1)

    def test_backpressure_rejects_slot_beyond_60_seconds(self):
        coordinator = self.coordinator(max_requests=1, window_seconds=61,
                                       backpressure_seconds=60)
        coordinator.reserve("eastmoney")
        with self.assertRaises(BackpressureError) as caught:
            coordinator.acquire("eastmoney")
        self.assertAlmostEqual(caught.exception.retry_after, 61.0)
        self.assertEqual(self.clock.time(), 1000.0)
        self.assertEqual(coordinator.snapshot("eastmoney")["reserved_slots"], 1)

    def test_backpressure_boundary_allows_exact_60_seconds(self):
        coordinator = self.coordinator(max_requests=1, window_seconds=60,
                                       backpressure_seconds=60)
        coordinator.reserve("eastmoney")
        permit = coordinator.acquire("eastmoney")
        self.assertEqual(self.clock.time(), 1060.0)
        coordinator.cancel(permit)

    def test_backpressure_limit_covers_permit_and_slot_wait_together(self):
        coordinator = self.coordinator(max_requests=1, max_in_flight=1,
                                       window_seconds=100, lease_seconds=5,
                                       backpressure_seconds=60, poll_interval=1)
        coordinator.reserve("eastmoney")
        with self.assertRaises(BackpressureError):
            coordinator.acquire("eastmoney")
        self.assertEqual(self.clock.time(), 1005.0)
        self.assertEqual(coordinator.snapshot("eastmoney")["reserved_slots"], 1)

    def test_in_flight_permit_backpressure_then_release(self):
        coordinator = self.coordinator(max_in_flight=1, lease_seconds=100,
                                       backpressure_seconds=0.2, poll_interval=0.05)
        first = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(first)
        with self.assertRaises(BackpressureError):
            coordinator.acquire("eastmoney")
        self.assertAlmostEqual(self.clock.time(), 1000.2)
        coordinator.record_success(first)
        second = coordinator.acquire("eastmoney")
        coordinator.cancel(second)
        self.assertEqual(coordinator.snapshot("eastmoney")["active_in_flight"], 0)

    def test_lease_expiry_recovers_from_missing_process(self):
        coordinator = self.coordinator(max_in_flight=1, lease_seconds=10)
        crashed = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(crashed)
        self.clock.advance(10.0)
        replacement = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(replacement)
        coordinator.record_success(replacement)
        with self.assertRaises(LeaseError):
            coordinator.record_success(crashed)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (2, 2, 1))

    def test_delayed_permits_still_obey_real_start_minimum_interval(self):
        coordinator = self.coordinator(min_interval=10, window_seconds=100,
                                       lease_seconds=100)
        first = coordinator.reserve("eastmoney")
        second = coordinator.reserve("eastmoney")
        self.clock.advance(20)
        coordinator.mark_network_started(first)
        coordinator.mark_network_started(second)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual(snap["network_slots"], [1020.0, 1030.0])
        self.assertEqual(snap["last_network_started"], 1030.0)

    def test_delayed_permits_still_obey_real_start_sliding_window(self):
        coordinator = self.coordinator(max_requests=1, window_seconds=10,
                                       lease_seconds=100)
        first = coordinator.reserve("eastmoney")
        second = coordinator.reserve("eastmoney")
        self.clock.advance(5)
        coordinator.mark_network_started(first)
        coordinator.mark_network_started(second)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual(snap["network_started"], 2)
        self.assertEqual(snap["last_network_started"], 1015.0)

    def test_lease_heartbeat_prevents_expiry(self):
        coordinator = self.coordinator(max_in_flight=1, lease_seconds=10,
                                       backpressure_seconds=0.1)
        permit = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(permit)
        self.clock.advance(8)
        coordinator.renew(permit)
        self.clock.advance(8)
        self.assertEqual(coordinator.snapshot("eastmoney")["active_in_flight"], 1)
        coordinator.record_success(permit)
        self.clock.advance(20)
        with self.assertRaises(LeaseError):
            coordinator.record_success(permit)

    def test_reserved_started_response_counters_are_independent(self):
        coordinator = self.coordinator()
        one = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(one)
        coordinator.record_success(one)
        two = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(two)
        coordinator.record_failure(two, "boom", response_received=False)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (2, 2, 1))
        self.assertEqual(snap["active_in_flight"], 0)

    def test_cancel_still_consumes_reserved_slot_but_not_network(self):
        coordinator = self.coordinator(max_requests=1, backpressure_seconds=0)
        permit = coordinator.reserve("eastmoney")
        self.assertTrue(coordinator.cancel(permit))
        with self.assertRaises(BackpressureError):
            coordinator.reserve("eastmoney")
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (1, 0, 0))
        self.assertEqual(snap["active_reservations"], 1)

    def test_open_cancels_reserved_but_not_started_request(self):
        coordinator = self.coordinator(min_interval=10, circuit_threshold=1,
                                       open_seconds=50)
        started = coordinator.reserve("eastmoney")
        pending = coordinator.reserve("eastmoney")
        coordinator.mark_network_started(started)
        coordinator.record_risk_control(started, "403")
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual(snap["circuit"], "OPEN")
        self.assertEqual(snap["active_in_flight"], 0)
        with self.assertRaises(CircuitOpenError):
            coordinator.mark_network_started(pending)
        self.assertEqual(coordinator.snapshot("eastmoney")["network_started"], 1)
        with self.assertRaises(CircuitOpenError):
            coordinator.acquire("eastmoney")

    def test_half_open_allows_only_one_probe_and_success_closes(self):
        coordinator = self.coordinator(circuit_threshold=1, open_seconds=10)
        permit = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(permit)
        coordinator.record_risk_control(permit)
        with self.assertRaises(CircuitOpenError):
            coordinator.acquire("eastmoney")
        self.clock.advance(10)
        probe = coordinator.acquire("eastmoney")
        self.assertTrue(probe.probe)
        with self.assertRaises(CircuitOpenError):
            coordinator.acquire("eastmoney")
        coordinator.mark_network_started(probe)
        coordinator.record_success(probe)
        self.assertEqual(coordinator.snapshot("eastmoney")["circuit"], "CLOSED")
        normal = coordinator.acquire("eastmoney")
        self.assertFalse(normal.probe)
        coordinator.cancel(normal)

    def test_old_success_cannot_close_half_open_only_probe_can(self):
        coordinator = self.coordinator(circuit_threshold=1, open_seconds=10,
                                       max_in_flight=3)
        first = coordinator.reserve("eastmoney")
        old = coordinator.reserve("eastmoney")
        coordinator.mark_network_started(first)
        coordinator.mark_network_started(old)
        coordinator.record_risk_control(first)
        self.clock.advance(10)
        probe = coordinator.acquire("eastmoney")
        coordinator.record_success(old)
        self.assertEqual(coordinator.snapshot("eastmoney")["circuit"], "HALF_OPEN")
        coordinator.mark_network_started(probe)
        coordinator.record_success(probe)
        self.assertEqual(coordinator.snapshot("eastmoney")["circuit"], "CLOSED")

    def test_half_open_transport_failure_reopens(self):
        coordinator = self.coordinator(circuit_threshold=1, open_seconds=10)
        permit = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(permit)
        coordinator.record_risk_control(permit)
        self.clock.advance(10)
        probe = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(probe)
        coordinator.record_failure(probe, "timeout")
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual(snap["circuit"], "OPEN")
        self.assertEqual(snap["open_until"], self.clock.time() + 10)
        self.assertEqual(snap["responses"], 1)

    def test_success_resets_consecutive_risk_count(self):
        coordinator = self.coordinator(circuit_threshold=2)
        for outcome in ("risk", "success", "risk", "risk"):
            permit = coordinator.acquire("eastmoney")
            coordinator.mark_network_started(permit)
            if outcome == "risk":
                coordinator.record_risk_control(permit)
            else:
                coordinator.record_success(permit)
            if outcome == "success":
                self.assertEqual(coordinator.snapshot("eastmoney")["consecutive_risk"], 0)
        self.assertEqual(coordinator.snapshot("eastmoney")["circuit"], "OPEN")

    def test_provider_risk_signal_without_permit_counts_and_opens(self):
        coordinator = self.coordinator(circuit_threshold=2)
        self.assertEqual(coordinator.signal_risk("eastmoney", "seed")["opened"], False)
        self.assertEqual(coordinator.snapshot("eastmoney")["consecutive_risk"], 1)
        outcome = coordinator.signal_risk("eastmoney", "again")
        self.assertEqual((outcome["opened"], outcome["circuit"]), (True, "OPEN"))
        self.assertEqual(coordinator.snapshot("eastmoney")["consecutive_risk"], 2)
        # OPEN 期间的 provider 级信号只延长冷却，不再叠连击。
        self.assertEqual(coordinator.signal_risk("eastmoney", "extend")["opened"], False)
        self.assertEqual(coordinator.snapshot("eastmoney")["consecutive_risk"], 2)

    def test_provider_clear_closes_half_open_gate(self):
        coordinator = self.coordinator(circuit_threshold=1)
        coordinator.signal_risk("eastmoney", "trip")       # 立即 OPEN
        self.clock.advance(61)                             # 过冷却 → HALF_OPEN
        self.assertEqual(coordinator.snapshot("eastmoney")["circuit"], "HALF_OPEN")
        result = coordinator.clear_risk_streak("eastmoney")
        # 半开窗口的业务成功结论 = 探针业务校验通过 → 关闸恢复。
        self.assertEqual((result["circuit"], result["consecutive_risk"]), ("CLOSED", 0))
        self.assertFalse(coordinator.acquire("eastmoney").probe)

    def test_probe_neutral_settlement_keeps_half_open(self):
        coordinator = self.coordinator(circuit_threshold=1)
        coordinator.signal_risk("eastmoney", "trip")
        self.clock.advance(61)                             # → HALF_OPEN
        probe = coordinator.acquire("eastmoney")
        self.assertTrue(probe.probe)
        coordinator.mark_network_started(probe)
        # 探针拿到非风控响应但业务未裁决（中性结算）：不得重开闸。
        coordinator.record_failure(probe, reason="pending", response_received=True)
        self.assertEqual(coordinator.snapshot("eastmoney")["circuit"], "HALF_OPEN")
        # 业务校验通过后的显式清零关闸，恢复正常流量。
        coordinator.clear_risk_streak("eastmoney")
        self.assertEqual(coordinator.snapshot("eastmoney")["circuit"], "CLOSED")

    def test_corrupt_json_fails_closed(self):
        store = JsonStateStore(self.root)
        store.state_path.parent.mkdir(parents=True, exist_ok=True)
        store.state_path.write_text("{broken", encoding="utf-8")
        coordinator = self.coordinator()
        with self.assertRaises(CoordinatorStateError):
            coordinator.acquire("eastmoney")
        self.assertEqual(coordinator.policies["eastmoney"].max_requests, 100)

    def test_unknown_state_schema_fails_closed(self):
        store = JsonStateStore(self.root)
        store.state_path.parent.mkdir(parents=True, exist_ok=True)
        store.state_path.write_text(json.dumps({"version": 1, "providers": {
            "eastmoney": {"circuit": "CLOSED"}}}), encoding="utf-8")
        with self.assertRaises(CoordinatorStateError):
            self.coordinator().acquire("eastmoney")

    def test_lock_error_fails_closed(self):
        store = JsonStateStore(self.root)
        store.lock_path.mkdir(parents=True)
        with self.assertRaises(CoordinatorStateError):
            self.coordinator().acquire("eastmoney")

    def test_snapshot_returns_copies(self):
        coordinator = self.coordinator()
        permit = coordinator.reserve("eastmoney")
        snap = coordinator.snapshot("eastmoney")
        snap["in_flight"].clear()
        snap["reservations"].clear()
        self.assertEqual(coordinator.snapshot("eastmoney")["active_in_flight"], 1)
        coordinator.cancel(permit)

    def test_explicit_reap_reports_expired_entries(self):
        coordinator = self.coordinator(window_seconds=5, lease_seconds=10)
        permit = coordinator.reserve("eastmoney")
        self.clock.advance(11)
        result = coordinator.reap("eastmoney")
        self.assertEqual(result, {"expired_leases": 1, "expired_reservations": 1})
        with self.assertRaises(LeaseError):
            coordinator.cancel(permit)


@unittest.skipUnless(hasattr(os, "fork"), "需要 POSIX 多进程语义")
class MultiProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fund-coordinator-mp-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.context = multiprocessing.get_context("spawn")

    def finish(self, processes, queue, expected_messages):
        for process in processes:
            process.join(timeout=15)
            self.assertFalse(process.is_alive(), "子进程超时")
            self.assertEqual(process.exitcode, 0)
        messages = [queue.get(timeout=2) for _ in range(expected_messages)]
        self.assertFalse(any(message[0] == "error" for message in messages), messages)
        return messages

    def test_concurrent_processes_have_atomic_counters(self):
        queue = self.context.Queue()
        processes = [self.context.Process(
            target=process_success, args=(str(self.root), "eastmoney", queue))
            for _ in range(6)]
        for process in processes:
            process.start()
        messages = self.finish(processes, queue, 6)
        self.assertEqual(len({message[1] for message in messages}), 6)
        snap = Coordinator(self.root).snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (6, 6, 6))
        self.assertEqual(snap["active_in_flight"], 0)

    def test_in_flight_limit_is_enforced_across_processes(self):
        queue = self.context.Queue()
        process = self.context.Process(target=process_hold, args=(str(self.root), queue))
        process.start()
        self.assertEqual(queue.get(timeout=5)[0], "started")
        contender = Coordinator(self.root, policies={"eastmoney": policy(
            min_interval=0, max_in_flight=1, lease_seconds=5)},
            backpressure_seconds=0.05, poll_interval=0.01)
        with self.assertRaises(BackpressureError):
            contender.acquire("eastmoney")
        self.finish([process], queue, 1)
        snap = contender.snapshot("eastmoney")
        self.assertEqual((snap["network_started"], snap["responses"]), (1, 1))

    def test_crashed_process_lease_is_reaped(self):
        queue = self.context.Queue()
        process = self.context.Process(target=process_crash_after_start,
                                       args=(str(self.root), queue))
        process.start()
        self.assertEqual(queue.get(timeout=5)[0], "started")
        process.join(timeout=10)
        self.assertEqual(process.exitcode, 0)
        coordinator = Coordinator(self.root, policies={"eastmoney": policy(
            min_interval=0, max_in_flight=1, lease_seconds=0.15)})
        time.sleep(0.22)
        replacement = coordinator.acquire("eastmoney")
        coordinator.mark_network_started(replacement)
        coordinator.record_success(replacement)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (2, 2, 1))
        self.assertEqual(snap["active_in_flight"], 0)

    def test_circuit_open_is_visible_to_another_process(self):
        queue = self.context.Queue()
        process = self.context.Process(target=process_open_circuit,
                                       args=(str(self.root), queue))
        process.start()
        messages = self.finish([process], queue, 1)
        self.assertEqual(messages[0], ("opened", "OPEN"))
        coordinator = Coordinator(self.root, policies={"eastmoney": policy(
            min_interval=0, circuit_threshold=1)})
        with self.assertRaises(CircuitOpenError):
            coordinator.acquire("eastmoney")


if __name__ == "__main__":
    unittest.main(verbosity=2)
