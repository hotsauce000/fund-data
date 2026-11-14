"""Cache policy, legacy key, and path-safety primitives (standard library only)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Optional

from .locks import drop_extended_prefix


SNAPSHOT_SCHEMA_VERSION = "1"
SNAPSHOT_TTL_SECONDS = 20 * 60 * 60
FUND_LIST_TTL_SECONDS = 30 * 24 * 60 * 60
FUND_LIST_MEMORY_TTL_SECONDS = 5 * 60
PROVISIONAL_QUARTER_TTL_SECONDS = 60 * 60
SETTLED_QUARTER_TTL_SECONDS = 180 * 24 * 60 * 60


@dataclass(frozen=True)
class CachePolicy:
    """Declarative freshness and failure behavior for one cache entry.

    A ``None`` TTL never expires periodically. Schema versions belong to the
    payload contract, not to the application's software release version.
    """

    ttl_seconds: Optional[float] = SNAPSHOT_TTL_SECONDS
    retry_after_seconds: float = 30.0
    allow_stale: bool = False
    memory_ttl_seconds: Optional[float] = None
    schema_version: Optional[str] = None
    validator: Optional[Callable[[Any], bool]] = None

    def __post_init__(self) -> None:
        if self.ttl_seconds is not None and self.ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative or None")
        if self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be non-negative")
        if self.memory_ttl_seconds is not None and self.memory_ttl_seconds < 0:
            raise ValueError("memory_ttl_seconds must be non-negative or None")
        if self.schema_version is not None and not self.schema_version:
            raise ValueError("schema_version must be non-empty")

    def is_fresh(self, fetched_at: float, now: float) -> bool:
        if self.ttl_seconds is None:
            return True
        return now - fetched_at <= self.ttl_seconds

    def accepts(self, data: Any) -> bool:
        if data is None:
            return False
        if self.schema_version is not None:
            if not isinstance(data, dict):
                return False
            meta = data.get("_meta")
            actual = data.get("schema_version")
            if actual is None and isinstance(meta, dict):
                actual = meta.get("schema_version")
            if actual != self.schema_version:
                return False
        return self.validator(data) if self.validator is not None else True

    def stamp(self, data: Any) -> Any:
        """Add schema metadata without changing any software-version field."""

        if self.schema_version is None:
            return data
        if not isinstance(data, dict):
            raise TypeError("schema-versioned cache payloads must be dictionaries")
        stamped = dict(data)
        meta = dict(stamped.get("_meta") or {})
        meta["schema_version"] = self.schema_version
        stamped["_meta"] = meta
        return stamped


def snapshot_policy(
    schema_version: str = SNAPSHOT_SCHEMA_VERSION,
    ttl_seconds: Optional[float] = SNAPSHOT_TTL_SECONDS,
    **kwargs: Any,
) -> CachePolicy:
    return CachePolicy(
        ttl_seconds=ttl_seconds,
        schema_version=schema_version,
        **kwargs,
    )


def fund_list_policy(
    memory_ttl_seconds: Optional[float] = FUND_LIST_MEMORY_TTL_SECONDS,
    ttl_seconds: Optional[float] = FUND_LIST_TTL_SECONDS,
    **kwargs: Any,
) -> CachePolicy:
    return CachePolicy(
        ttl_seconds=ttl_seconds,
        memory_ttl_seconds=memory_ttl_seconds,
        **kwargs,
    )


def _as_date(now: Optional[date | datetime]) -> date:
    if now is None:
        return date.today()
    return now.date() if isinstance(now, datetime) else now


def quarter_policy(
    year: int,
    quarter: int,
    now: Optional[date | datetime] = None,
    provisional_ttl_seconds: Optional[float] = PROVISIONAL_QUARTER_TTL_SECONDS,
    settled_ttl_seconds: Optional[float] = SETTLED_QUARTER_TTL_SECONDS,
    **kwargs: Any,
) -> CachePolicy:
    """Use a short TTL for the current quarter and a long TTL for settled ones."""

    year = int(year)
    quarter = int(quarter)
    if not 1 <= quarter <= 4:
        raise ValueError("quarter must be in 1..4")
    today = _as_date(now)
    current_quarter = (today.month - 1) // 3 + 1
    is_provisional = (year, quarter) >= (today.year, current_quarter)
    return CachePolicy(
        ttl_seconds=provisional_ttl_seconds if is_provisional else settled_ttl_seconds,
        **kwargs,
    )


def historical_year_policy(
    year: int,
    now: Optional[date | datetime] = None,
    current_ttl_seconds: Optional[float] = PROVISIONAL_QUARTER_TTL_SECONDS,
    **kwargs: Any,
) -> CachePolicy:
    """Past years are immutable and therefore have no periodic expiration."""

    year = int(year)
    today = _as_date(now)
    if year > today.year:
        raise ValueError("historical_year_policy does not accept future years")
    return CachePolicy(
        ttl_seconds=current_ttl_seconds if year == today.year else None,
        **kwargs,
    )


def shibor_cache_key(period: Optional[str] = None, days: int = 30) -> str:
    """Preserve the legacy SHIBOR key, including the all-term days x 8 window."""

    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise ValueError("days must be a positive integer")
    if period and any(char in period for char in ('"', "\\")):
        raise ValueError("period contains an invalid character")
    window = min(days * (1 if period else 8), 500)
    return f"macro/shibor_{period or 'all'}_{window}.json"


def cache_key_hash(key: str) -> str:
    """The single canonical hash used for lock and failure sidecar paths."""

    if not isinstance(key, str) or not key:
        raise ValueError("cache key must be a non-empty string")
    return sha256(key.encode("utf-8")).hexdigest()


def safe_cache_path(root: str | Path, key: str) -> Path:
    """Resolve a legacy relative key while preventing root escape and symlinks."""

    if not isinstance(key, str) or not key or "\x00" in key:
        raise ValueError("cache key must be a non-empty string without NUL")
    if "\\" in key:
        raise ValueError("backslashes are not allowed in cache keys")
    relative = PurePosixPath(key)
    windows = PureWindowsPath(key)
    if relative.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError("absolute cache keys are not allowed")
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("cache key contains an unsafe path component")

    root_path = drop_extended_prefix(
        str(Path(root).expanduser().resolve(strict=False)))
    try:
        candidate = Path(root_path).joinpath(*relative.parts).resolve(strict=False)
    except (RuntimeError, OSError) as exc:
        # IO/解析瞬态（如并发创建目录时的瞬时失败）≠ 路径逃逸：分开上报，
        # 不许伪装成安全拦截（曾致多进程冷启动偶发误报 escapes）。
        raise ValueError(f"cache path resolve 失败: {key!r} ({exc})") from exc
    try:
        Path(drop_extended_prefix(str(candidate))).relative_to(Path(root_path))
    except ValueError as exc:
        raise ValueError(f"cache key escapes cache root: {key!r}") from exc
    # 返回剥前缀形态（跨调用可比、可作字典键）；>260 字符的缓存根会因此
    # 失去扩展前缀保护——缓存根勿深埋（README 平台支持节已声明该边界）。
    return Path(drop_extended_prefix(str(candidate)))


def sharded_key_path(
    root: str | Path,
    key: str,
    category: str = "keys",
    suffix: str = ".json",
) -> Path:
    """Map an arbitrary key to a deterministic two-level hash-sharded path."""

    if not category or not category.replace("_", "").replace("-", "").isalnum():
        raise ValueError("invalid sidecar category")
    if not suffix or "/" in suffix or "\\" in suffix:
        raise ValueError("invalid sidecar suffix")
    digest = cache_key_hash(key)
    return safe_cache_path(
        root,
        f".{category}/{digest[:2]}/{digest[2:4]}/{digest}{suffix}",
    )


snapshot_cache_policy = snapshot_policy
fund_list_cache_policy = fund_list_policy
quarter_cache_policy = quarter_policy
historical_year_cache_policy = historical_year_policy


__all__ = [
    "CachePolicy",
    "FUND_LIST_MEMORY_TTL_SECONDS",
    "FUND_LIST_TTL_SECONDS",
    "PROVISIONAL_QUARTER_TTL_SECONDS",
    "SETTLED_QUARTER_TTL_SECONDS",
    "SNAPSHOT_SCHEMA_VERSION",
    "SNAPSHOT_TTL_SECONDS",
    "cache_key_hash",
    "fund_list_cache_policy",
    "fund_list_policy",
    "historical_year_cache_policy",
    "historical_year_policy",
    "quarter_cache_policy",
    "quarter_policy",
    "safe_cache_path",
    "sharded_key_path",
    "shibor_cache_key",
    "snapshot_cache_policy",
    "snapshot_policy",
]
