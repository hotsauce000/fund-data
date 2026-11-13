# -*- coding: utf-8 -*-
"""可注入 requests/urllib/curl transport 的协调 Gateway。

本模块不 import requests，也不调用主模块；requests.Session、urllib opener 和
curl runner 均可注入，因而离线测试不会意外访问真实上游。
"""

import json
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .errors import RiskControlError
from .policies import provider_for_url


class CurlHTTPError(RuntimeError):
    def __init__(self, response):
        super().__init__(f"HTTP {response.status_code}: {response.url}")
        self.response = response
        self.status_code = response.status_code


class CurlResponse:
    """与 requests 响应的最小常用接口对齐。"""

    def __init__(self, url, status_code, content, headers=None):
        self.url = url
        self.status_code = int(status_code)
        self.content = content
        self.headers = headers or {}
        self.encoding = "utf-8"

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    @property
    def text(self):
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    def json(self):
        return json.loads(self.content.decode(self.encoding or "utf-8"))

    def raise_for_status(self):
        if self.status_code >= 400:
            raise CurlHTTPError(self)


class Gateway:
    """每次网络尝试都独立 acquire/start/finish 的请求出口。

    retries=0 表示只发一次；retries=1 表示失败后最多再发一次。403/429 与
    风控正文绝不重试，CircuitOpenError/BackpressureError 也直接上抛。

    成功语义（Sprint 1 口径，与主模块 assert_not_risk_control/_cbreak_success
    一致）：本层只分类传输风险与可达性，绝不因 HTTP 2xx 清零风控连击。
    2xx/3xx 非风险响应返回时把待裁决的 permit 挂在 response.permit 上：

    - 业务完整性校验通过后调用 ``response.permit.success()`` —— 此刻才清零
      连击 / 关闭 half-open 探针；
    - 结构异常等业务失败调用 ``response.permit.failure(..., response_received=True)``
      —— 入账响应但不动连击；
    - 业务层自行识别的风控正文调用 ``response.permit.risk_control()``；
    - 不裁决则租约到期自动作废（中性，fail-safe：宁可不清零）。
    """

    def __init__(self, coordinator, *, session=None, opener=None, curl_runner=None,
                 default_timeout=20, risk_content_markers=("无访问权限",)):
        self.coordinator = coordinator
        self.session = session
        self.opener = opener
        self.curl_runner = curl_runner or subprocess.run
        self.default_timeout = default_timeout
        self.risk_content_markers = tuple(risk_content_markers or ())

    @staticmethod
    def _status(response):
        value = getattr(response, "status_code", None)
        if value is None:
            value = getattr(response, "status", None)
        if value is None and hasattr(response, "getcode"):
            value = response.getcode()
        if value is None:
            raise ValueError("transport 响应缺少 HTTP status")
        return int(value)

    def _risk_reason(self, response, status):
        if status in (403, 429):
            return f"HTTP {status}"
        if self.risk_content_markers:
            try:
                text = response.text
            except (AttributeError, UnicodeError, ValueError):
                text = ""
            for marker in self.risk_content_markers:
                if marker and marker in text:
                    return f"风控正文 {marker!r}"
        return None

    @staticmethod
    def _url_with_params(url, params):
        if not params:
            return url
        query = urllib.parse.urlencode(params, doseq=True)
        separator = "&" if urllib.parse.urlsplit(url).query else "?"
        return url + separator + query

    def _requests_transport(self, method, url, kwargs):
        session = kwargs.pop("session", None) or self.session
        if session is None:
            raise ValueError("requests transport 需要注入 session（本核心不依赖 requests）")
        if hasattr(session, "request"):
            return session.request(method, url, **kwargs)
        return getattr(session, method.lower())(url, **kwargs)

    def _urllib_transport(self, method, url, kwargs):
        opener = kwargs.pop("opener", None) or self.opener
        if opener is None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        params = kwargs.pop("params", None)
        data = kwargs.pop("data", None)
        json_data = kwargs.pop("json", None)
        headers = dict(kwargs.pop("headers", None) or {})
        timeout = kwargs.pop("timeout", self.default_timeout)
        if kwargs:
            raise TypeError(f"urllib 不支持参数: {', '.join(sorted(kwargs))}")
        if json_data is not None:
            if data is not None:
                raise ValueError("data 与 json 不能同时提供")
            data = json.dumps(json_data, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        if isinstance(data, str):
            data = data.encode("utf-8")
        request = urllib.request.Request(
            self._url_with_params(url, params), data=data, headers=headers, method=method)
        return opener.open(request, timeout=timeout)

    def _curl_transport(self, method, url, kwargs):
        runner = kwargs.pop("curl_runner", None) or self.curl_runner
        params = kwargs.pop("params", None)
        data = kwargs.pop("data", None)
        json_data = kwargs.pop("json", None)
        headers = dict(kwargs.pop("headers", None) or {})
        timeout = kwargs.pop("timeout", self.default_timeout)
        if kwargs:
            raise TypeError(f"curl 不支持参数: {', '.join(sorted(kwargs))}")
        if json_data is not None:
            if data is not None:
                raise ValueError("data 与 json 不能同时提供")
            data = json.dumps(json_data, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        target = self._url_with_params(url, params)
        with tempfile.TemporaryDirectory(prefix="fund-data-curl-") as tmp:
            body_path = Path(tmp) / "body"
            header_path = Path(tmp) / "headers"
            upload_path = None
            argv = ["curl", "--silent", "--show-error", "--location",
                    "--request", method, "--output", str(body_path),
                    "--dump-header", str(header_path), "--write-out", "%{http_code}"]
            if timeout is not None:
                argv.extend(["--max-time", str(timeout)])
            for name, value in headers.items():
                argv.extend(["--header", f"{name}: {value}"])
            if data is not None:
                if isinstance(data, str):
                    data = data.encode("utf-8")
                if isinstance(data, bytes):
                    upload_path = Path(tmp) / "upload"
                    upload_path.write_bytes(data)
                    argv.extend(["--data-binary", "@" + str(upload_path)])
                else:
                    argv.extend(["--data-binary", str(data)])
            argv.append(target)
            completed = runner(argv, timeout=timeout, capture_output=True, check=False)
            stdout = getattr(completed, "stdout", b"")
            if isinstance(stdout, str):
                stdout = stdout.encode("ascii", errors="replace")
            try:
                status = int((stdout or b"0").strip() or b"0")
            except ValueError:
                status = 0
            returncode = getattr(completed, "returncode", 0)
            if returncode != 0 or status <= 0:
                stderr = getattr(completed, "stderr", b"")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                raise OSError(f"curl 失败(returncode={returncode}, status={status}): {stderr}")
            content = body_path.read_bytes() if body_path.exists() else b""
            response_headers = {}
            if header_path.exists():
                for line in header_path.read_text(
                        encoding="iso-8859-1", errors="replace").splitlines():
                    if ":" in line and not line.startswith("HTTP/"):
                        name, value = line.split(":", 1)
                        response_headers[name.strip()] = value.strip()
            return CurlResponse(target, status, content, response_headers)

    def _send(self, transport, method, url, kwargs):
        if transport == "requests":
            return self._requests_transport(method, url, dict(kwargs))
        if transport == "urllib":
            try:
                return self._urllib_transport(method, url, dict(kwargs))
            except urllib.error.HTTPError as response:
                return response
        if transport == "curl":
            return self._curl_transport(method, url, dict(kwargs))
        raise ValueError(f"未知 transport: {transport!r}")

    def request(self, method, url, *, transport="requests", retries=0,
                retry_statuses=(500, 502, 503, 504), **kwargs):
        """执行请求；任何 retry 都重新预约并独立消耗配额。

        返回 2xx/3xx 非风险响应时附带 response.permit（裁决待定，租约仍在
        in_flight）；业务校验后的显式裁决才决定是否清零，详见类文档。
        4xx/5xx 非风险终态直接记中性 failure 后原样返回，不附 permit。
        """

        method = str(method).upper()
        if transport not in ("requests", "urllib", "curl"):
            raise ValueError(f"未知 transport: {transport!r}")
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            raise ValueError("retries 必须是非负整数")
        # 在消耗配额前完成 host 与 transport 配置校验。
        provider_for_url(url)
        if transport == "requests" and not (kwargs.get("session") or self.session):
            raise ValueError("requests transport 需要注入 session（本核心不依赖 requests）")
        kwargs.setdefault("timeout", self.default_timeout)
        last_error = None
        for attempt in range(retries + 1):
            permit = self.coordinator.acquire(url)
            self.coordinator.mark_network_started(permit)
            try:
                response = self._send(transport, method, url, kwargs)
            except Exception as exc:
                last_error = exc
                self.coordinator.record_failure(permit, reason=type(exc).__name__)
                if attempt < retries:
                    continue
                raise
            status = self._status(response)
            risk_reason = self._risk_reason(response, status)
            if risk_reason:
                self.coordinator.record_risk_control(permit, reason=risk_reason)
                raise RiskControlError(
                    f"{risk_reason}（{method} {url}）：疑似上游风控，不重试不降级")
            if status in retry_statuses and attempt < retries:
                last_error = RuntimeError(f"HTTP {status}")
                self.coordinator.record_failure(
                    permit, reason=f"HTTP {status}", response_received=True)
                continue
            if 200 <= status < 400:
                # 传输层只确认可达，不判定业务成功（Sprint 1 语义）：
                # 裁决待定的许可挂在响应上，业务校验通过后显式 success()
                # 才清零；不裁决则租约到期自动作废，绝不清零。
                response.permit = permit
                return response
            self.coordinator.record_failure(
                permit, reason=f"HTTP {status}", response_received=True)
            return response
        if last_error is not None:
            raise last_error
        raise RuntimeError("请求未产生响应")

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def requests_request(self, method, url, **kwargs):
        return self.request(method, url, transport="requests", **kwargs)

    def urllib_request(self, method, url, **kwargs):
        return self.request(method, url, transport="urllib", **kwargs)

    def curl_request(self, method, url, **kwargs):
        return self.request(method, url, transport="curl", **kwargs)
