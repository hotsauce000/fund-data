# -*- coding: utf-8 -*-
"""Provider 分组、host 白名单与默认配额策略。"""

import math
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import UnknownHostError


@dataclass(frozen=True)
class ProviderPolicy:
    """一个 provider 组的共享限制；时间单位均为秒。"""

    max_requests: int = 300
    window_seconds: float = 300.0
    min_interval: float = 1.5
    max_in_flight: int = 4
    lease_seconds: float = 30.0
    circuit_threshold: int = 3
    open_seconds: float = 600.0

    def __post_init__(self):
        for name in ("max_requests", "max_in_flight", "circuit_threshold"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} 必须是 >= 1 的整数")
        for name in ("window_seconds", "min_interval", "lease_seconds", "open_seconds"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} 必须是 >= 0 的有限数值")
        if self.window_seconds == 0 or self.lease_seconds == 0 or self.open_seconds == 0:
            raise ValueError("window_seconds/lease_seconds/open_seconds 必须 > 0")


DEFAULT_POLICIES = {
    "eastmoney": ProviderPolicy(),
    "cninfo": ProviderPolicy(max_requests=120, min_interval=1.0, max_in_flight=2),
    "chinabond": ProviderPolicy(max_requests=120, min_interval=1.5, max_in_flight=2),
    "danjuan": ProviderPolicy(max_requests=120, min_interval=1.5, max_in_flight=2),
    "tencent": ProviderPolicy(max_requests=300, min_interval=0.2, max_in_flight=4),
}

_PROVIDER_ALIASES = {
    "eastmoney": "eastmoney", "em": "eastmoney", "tt": "eastmoney",
    "ttfund": "eastmoney", "push2": "eastmoney", "push2his": "eastmoney",
    "cninfo": "cninfo", "chinabond": "chinabond", "cb": "chinabond",
    "danjuan": "danjuan", "dj": "danjuan", "tencent": "tencent", "tx": "tencent",
}

_HOST_PROVIDERS = {
    "eastmoney.com": "eastmoney",
    "cninfo.com.cn": "cninfo",
    "chinabond.com.cn": "chinabond",
    "danjuanfunds.com": "danjuan",
    "danjuanapp.com": "danjuan",
    "gtimg.cn": "tencent",
    "gu.qq.com": "tencent",
}


def normalize_provider(provider):
    """把 TT/EM/push2 等历史线名归一为 eastmoney。"""

    key = str(provider).strip().lower()
    try:
        return _PROVIDER_ALIASES[key]
    except KeyError:
        raise UnknownHostError(f"未知 provider: {provider!r}") from None


def provider_for_host(host):
    """按精确域名或其子域名匹配白名单，不使用模糊子串。"""

    value = str(host).strip().lower().rstrip(".")
    if "@" in value or "/" in value or not value:
        raise UnknownHostError(f"非法或未知 host: {host!r}")
    if value.startswith("[") and value.endswith("]"):
        raise UnknownHostError(f"未知 host: {host!r}")
    if value.count(":") == 1:
        value = value.rsplit(":", 1)[0]
    for domain, provider in _HOST_PROVIDERS.items():
        if value == domain or value.endswith("." + domain):
            return provider
    raise UnknownHostError(f"未知 host，已拒绝请求: {host!r}")


def provider_for_url(url):
    """从绝对 HTTP(S) URL 解析 provider；未知 host 一律拒绝。"""

    try:
        parts = urlsplit(str(url))
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise ValueError
        # 访问 port 可同时拒绝畸形端口；userinfo 不属于本工具白名单用法。
        _ = parts.port
        if parts.username is not None or parts.password is not None:
            raise ValueError
        host = parts.hostname
    except (TypeError, ValueError):
        raise UnknownHostError(f"非法 URL 或未知 host: {url!r}") from None
    return provider_for_host(host)


def resolve_provider(value):
    """兼容传入 URL、host 或 provider 线名。"""

    text = str(value).strip()
    if "://" in text:
        return provider_for_url(text)
    try:
        return normalize_provider(text)
    except UnknownHostError:
        return provider_for_host(text)
