# -*- coding: utf-8 -*-
"""Gateway transport 适配测试；所有 transport 均为 fake，禁止真实网络。"""

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace

from fund_data_core import (CircuitOpenError, Coordinator, CoordinatorStateError,
                            Gateway, JsonStateStore, ProviderPolicy, RiskControlError,
                            UnknownHostError)
from test_coordinator import FakeClock


class FakeResponse:
    def __init__(self, status=200, text="ok", content=None, url="https://fund.eastmoney.com/x"):
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8") if content is None else content
        self.url = url
        self.headers = {}

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeCurlRunner:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        outcome = self.outcomes.pop(0)
        output = Path(argv[argv.index("--output") + 1])
        headers = Path(argv[argv.index("--dump-header") + 1])
        output.write_bytes(outcome.get("body", b"ok"))
        headers.write_text(outcome.get("headers", "HTTP/1.1 200 OK\r\nX-Test: yes\r\n"),
                           encoding="iso-8859-1")
        return SimpleNamespace(returncode=outcome.get("returncode", 0),
                               stdout=outcome.get("stdout", b"200"),
                               stderr=outcome.get("stderr", b""))


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fund-gateway-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = FakeClock()
        self.url = "https://fund.eastmoney.com/test"

    def coordinator(self, **overrides):
        values = dict(max_requests=100, window_seconds=10, min_interval=0,
                      max_in_flight=4, lease_seconds=30, circuit_threshold=1,
                      open_seconds=60)
        values.update(overrides)
        return Coordinator(self.root, policies={"eastmoney": ProviderPolicy(**values)},
                           clock=self.clock, sleeper=self.clock, backpressure_seconds=1)

    def test_requests_success_and_accounting(self):
        session = FakeSession([FakeResponse(200, '{"ok": true}')])
        coordinator = self.coordinator()
        gateway = Gateway(coordinator, session=session)
        response = gateway.get(self.url, headers={"X-Test": "1"})
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(session.calls[0][0], "GET")
        self.assertEqual(session.calls[0][2]["headers"], {"X-Test": "1"})
        # 2xx 只代表可达：裁决待定，租约仍在 in_flight，responses 未入账。
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (1, 1, 0))
        self.assertEqual(snap["active_in_flight"], 1)
        response.permit.success()
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["responses"], snap["active_in_flight"],
                          snap["consecutive_risk"]), (1, 0, 0))

    def test_403_is_risk_control_and_never_retried(self):
        session = FakeSession([FakeResponse(403, "forbidden")])
        coordinator = self.coordinator(circuit_threshold=1)
        gateway = Gateway(coordinator, session=session)
        with self.assertRaises(RiskControlError) as caught:
            gateway.get(self.url, retries=3)
        self.assertNotIsInstance(caught.exception, CircuitOpenError)
        self.assertEqual(len(session.calls), 1)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual(snap["circuit"], "OPEN")
        self.assertEqual(snap["responses"], 1)
        with self.assertRaises(CircuitOpenError):
            gateway.get(self.url, retries=3)
        self.assertEqual(len(session.calls), 1)

    def test_risk_content_even_with_http_200(self):
        session = FakeSession([FakeResponse(200, "您无访问权限")])
        coordinator = self.coordinator(circuit_threshold=1)
        with self.assertRaises(RiskControlError):
            Gateway(coordinator, session=session).get(self.url)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["circuit"], snap["responses"]), ("OPEN", 1))

    def test_transport_retries_independently_consume_quota(self):
        session = FakeSession([OSError("reset"), OSError("timeout")])
        coordinator = self.coordinator()
        gateway = Gateway(coordinator, session=session)
        with self.assertRaises(OSError):
            gateway.get(self.url, retries=1)
        self.assertEqual(len(session.calls), 2)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (2, 2, 0))

    def test_retry_then_success_counts_each_attempt(self):
        session = FakeSession([OSError("reset"), FakeResponse(200)])
        coordinator = self.coordinator()
        response = Gateway(coordinator, session=session).get(self.url, retries=1)
        self.assertEqual(response.status_code, 200)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (2, 2, 0))
        response.permit.success()
        self.assertEqual(coordinator.snapshot("eastmoney")["responses"], 1)

    def test_http_500_retry_counts_both_responses(self):
        session = FakeSession([FakeResponse(500), FakeResponse(200)])
        coordinator = self.coordinator()
        response = Gateway(coordinator, session=session).get(self.url, retries=1)
        self.assertEqual(response.status_code, 200)
        # 500 已中性入账（responses=1），200 待业务裁决（仍差一票）。
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (2, 2, 1))
        response.permit.success()
        self.assertEqual(coordinator.snapshot("eastmoney")["responses"], 2)

    def test_final_http_error_is_returned_like_requests(self):
        session = FakeSession([FakeResponse(500)])
        coordinator = self.coordinator()
        response = Gateway(coordinator, session=session).get(self.url, retries=0)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(session.calls), 1)
        # 4xx/5xx 非风险终态：中性入账，不附 permit，不动风控连击。
        self.assertIsNone(getattr(response, "permit", None))
        self.assertEqual(coordinator.snapshot("eastmoney")["responses"], 1)

    def test_unknown_host_consumes_no_quota_and_no_transport(self):
        session = FakeSession([])
        coordinator = self.coordinator()
        with self.assertRaises(UnknownHostError):
            Gateway(coordinator, session=session).get("https://example.com")
        self.assertEqual(session.calls, [])
        self.assertEqual(coordinator.snapshot("eastmoney")["reserved_slots"], 0)

    def test_missing_requests_session_fails_before_reservation(self):
        coordinator = self.coordinator()
        with self.assertRaises(ValueError):
            Gateway(coordinator).get(self.url)
        self.assertEqual(coordinator.snapshot("eastmoney")["reserved_slots"], 0)

    def test_corrupt_state_fails_closed_before_transport(self):
        store = JsonStateStore(self.root)
        store.state_path.parent.mkdir(parents=True, exist_ok=True)
        store.state_path.write_text("not-json", encoding="utf-8")
        session = FakeSession([FakeResponse(200)])
        with self.assertRaises(CoordinatorStateError):
            Gateway(self.coordinator(), session=session).get(self.url)
        self.assertEqual(session.calls, [])

    def test_invalid_retry_argument_consumes_no_quota(self):
        coordinator = self.coordinator()
        gateway = Gateway(coordinator, session=FakeSession([]))
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gateway.get(self.url, retries=value)
        self.assertEqual(coordinator.snapshot("eastmoney")["reserved_slots"], 0)

    def test_urllib_wrapper_builds_request_without_network(self):
        opener = FakeOpener([FakeResponse(200)])
        coordinator = self.coordinator()
        gateway = Gateway(coordinator, opener=opener)
        response = gateway.urllib_request("POST", self.url, params={"a": [1, 2]},
                                          json={"x": "值"}, headers={"X-A": "b"}, timeout=3)
        self.assertEqual(response.status_code, 200)
        request, timeout = opener.calls[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, self.url + "?a=1&a=2")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"x": "值"})
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(timeout, 3)
        response.permit.success()
        self.assertEqual(coordinator.snapshot("eastmoney")["responses"], 1)

    def test_urllib_http_error_is_classified_as_risk(self):
        error = urllib.error.HTTPError(self.url, 429, "too many", None, io.BytesIO(b"limited"))
        opener = FakeOpener([error])
        coordinator = self.coordinator(circuit_threshold=1)
        with self.assertRaises(RiskControlError):
            Gateway(coordinator, opener=opener).get(self.url, transport="urllib", retries=2)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(coordinator.snapshot("eastmoney")["circuit"], "OPEN")

    def test_curl_wrapper_parses_body_headers_and_status(self):
        runner = FakeCurlRunner([{"body": b'{"value": 7}', "stdout": b"200"}])
        coordinator = self.coordinator()
        response = Gateway(coordinator, curl_runner=runner).curl_request(
            "GET", self.url, params={"q": "a b"}, headers={"Referer": "x"})
        self.assertEqual(response.json(), {"value": 7})
        self.assertEqual(response.headers["X-Test"], "yes")
        argv, kwargs = runner.calls[0]
        self.assertEqual(argv[0], "curl")
        self.assertNotIn("shell", kwargs)
        self.assertIn(self.url + "?q=a+b", argv)
        self.assertIn("Referer: x", argv)
        response.permit.success()
        self.assertEqual(coordinator.snapshot("eastmoney")["responses"], 1)

    def test_curl_process_failure_retries_with_new_permit(self):
        runner = FakeCurlRunner([
            {"returncode": 7, "stdout": b"000", "stderr": b"connect"},
            {"body": b"ok", "stdout": b"200"},
        ])
        coordinator = self.coordinator()
        response = Gateway(coordinator, curl_runner=runner).get(
            self.url, transport="curl", retries=1)
        self.assertEqual(response.text, "ok")
        self.assertEqual(len(runner.calls), 2)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["reserved_slots"], snap["network_started"], snap["responses"]),
                         (2, 2, 0))
        response.permit.success()
        self.assertEqual(coordinator.snapshot("eastmoney")["responses"], 1)

    def test_curl_403_uses_same_risk_semantics(self):
        runner = FakeCurlRunner([{"body": b"forbidden", "stdout": b"403"}])
        coordinator = self.coordinator(circuit_threshold=1)
        with self.assertRaises(RiskControlError):
            Gateway(coordinator, curl_runner=runner).get(
                self.url, transport="curl", retries=3)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(coordinator.snapshot("eastmoney")["circuit"], "OPEN")


class GatewayVerdictTests(unittest.TestCase):
    """Sprint 1 成功语义回归：HTTP 2xx 绝不清零风控连击，显式裁决才清零。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fund-gateway-verdict-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = FakeClock()
        self.url = "https://fund.eastmoney.com/test"

    def coordinator(self, **overrides):
        values = dict(max_requests=100, window_seconds=10, min_interval=0,
                      max_in_flight=4, lease_seconds=30, circuit_threshold=3,
                      open_seconds=60)
        values.update(overrides)
        return Coordinator(self.root, policies={"eastmoney": ProviderPolicy(**values)},
                           clock=self.clock, sleeper=self.clock, backpressure_seconds=1)

    def seed_risk_signals(self, coordinator, times):
        """不经 Gateway 直接注入历史风控信号，模拟既有连击。"""
        for _ in range(times):
            permit = coordinator.acquire(self.url)
            coordinator.mark_network_started(permit)
            coordinator.record_risk_control(permit, reason="HTTP 403")

    def test_http_200_alone_never_resets_risk_streak(self):
        coordinator = self.coordinator()  # circuit_threshold=3
        self.seed_risk_signals(coordinator, 2)
        self.assertEqual(coordinator.snapshot("eastmoney")["consecutive_risk"], 2)
        # 200 + 空/乱正文，不做任何业务裁决 → 连击必须保持。
        session = FakeSession([FakeResponse(200, "garbage-not-json")])
        Gateway(coordinator, session=session).get(self.url)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["consecutive_risk"], snap["circuit"]), (2, "CLOSED"))
        # 第 3 次风控信号必须仍能触发熔断（若 200 曾清零则只会回到 1）。
        session = FakeSession([FakeResponse(403, "forbidden")])
        with self.assertRaises(RiskControlError):
            Gateway(coordinator, session=session).get(self.url)
        self.assertEqual(coordinator.snapshot("eastmoney")["circuit"], "OPEN")

    def test_permit_success_resets_risk_streak(self):
        coordinator = self.coordinator()
        self.seed_risk_signals(coordinator, 2)
        baseline = coordinator.snapshot("eastmoney")["responses"]
        session = FakeSession([FakeResponse(200, '{"records": [1, 2]}')])
        response = Gateway(coordinator, session=session).get(self.url)
        self.assertEqual(coordinator.snapshot("eastmoney")["responses"], baseline)
        response.permit.success()  # 业务完整性校验通过后的显式清零
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["consecutive_risk"], snap["circuit"],
                          snap["active_in_flight"], snap["responses"]),
                         (0, "CLOSED", 0, baseline + 1))

    def test_structure_failure_keeps_streak_and_releases_lease(self):
        coordinator = self.coordinator()
        self.seed_risk_signals(coordinator, 2)
        baseline = coordinator.snapshot("eastmoney")["responses"]
        session = FakeSession([FakeResponse(200, "[]")])  # 结构异常：空列表
        response = Gateway(coordinator, session=session).get(self.url)
        response.permit.failure(reason="结构异常：空记录", response_received=True)
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["consecutive_risk"], snap["circuit"],
                          snap["active_in_flight"], snap["responses"]),
                         (2, "CLOSED", 0, baseline + 1))

    def test_business_risk_verdict_counts_as_signal(self):
        # 业务层识别的 200+风控正文（网关默认标记之外）按风控信号入账。
        coordinator = self.coordinator(circuit_threshold=1)
        session = FakeSession([FakeResponse(200, "请求过于频繁，请稍后再试")])
        response = Gateway(coordinator, session=session).get(self.url)
        response.permit.risk_control(reason="内容风控：请求过于频繁")
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["consecutive_risk"], snap["circuit"]), (1, "OPEN"))

    def test_unfinished_verdict_expires_neutrally(self):
        coordinator = self.coordinator()
        session = FakeSession([FakeResponse(200, '{"ok": 1}')])
        Gateway(coordinator, session=session).get(self.url)  # 调用方忘记裁决
        self.assertEqual(coordinator.snapshot("eastmoney")["active_in_flight"], 1)
        self.clock.advance(31)  # 越过 lease_seconds=30
        snap = coordinator.snapshot("eastmoney")
        self.assertEqual((snap["active_in_flight"], snap["consecutive_risk"],
                          snap["circuit"], snap["responses"]), (0, 0, "CLOSED", 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
