"""Concurrent raw-JSON cache with generation-based refresh coalescing."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Generic, Iterator, Optional, TypeVar

from .cache_policies import CachePolicy, safe_cache_path, sharded_key_path

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - available only on Windows
    msvcrt = None


T = TypeVar("T")


class CacheError(RuntimeError):
    pass


class EmptyLoaderResult(CacheError, ValueError):
    pass


class CacheRetryAfterError(CacheError):
    """A different leader recently failed; the caller must not hit upstream yet."""

    def __init__(
        self,
        key: str,
        retry_until: float,
        now: float,
        cause: str = "",
    ) -> None:
        self.key = key
        self.retry_until = retry_until
        self.retry_after = max(0.0, retry_until - now)
        self.cause = cause
        detail = f": {cause}" if cause else ""
        super().__init__(
            f"cache leader for {key!r} recently failed; "
            f"retry after {self.retry_after:.3f}s{detail}"
        )


class StaleDataError(CacheError):
    """A refresh failed and stale data exists but was not silently accepted."""

    def __init__(
        self,
        message: str,
        stale_data: Any,
        fetched_at: str,
        key: Optional[str] = None,
        generation: Optional[int] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        self.stale_data = stale_data
        self.fetched_at = fetched_at
        self.key = key
        self.generation = generation
        self.retry_after = retry_after
        super().__init__(message)


@dataclass(frozen=True)
class CacheResult(Generic[T]):
    data: T
    from_cache: bool
    fetched_at: str
    generation: int
    stale: bool = False
    key: str = ""
    error: Optional[str] = None

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "from_cache": self.from_cache,
            "fetched_at": self.fetched_at,
            "generation": self.generation,
            "stale": self.stale,
            "error": self.error,
        }

    def unwrap(self) -> T:
        return self.data


@dataclass(frozen=True)
class _Entry:
    data: Any
    fetched_at_ts: float
    generation: int


@dataclass(frozen=True)
class _Failure:
    retry_until: float
    error_type: str
    message: str


@dataclass
class _MemoryItem:
    entry: _Entry
    stored_at: float


_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _iso_timestamp(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


class CacheRepository:
    """A coordinator-independent cache using loader callbacks.

    Data files remain raw JSON at their legacy relative paths. Locks and failure
    markers use hashed, sharded sidecars. ``clock`` and ``monotonic_clock`` are
    injectable for deterministic tests and embedding applications.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._memory: Dict[str, _MemoryItem] = {}
        self._memory_lock = threading.RLock()

    def cache_path(self, key: str) -> Path:
        return safe_cache_path(self.root, key)

    def read(self, key: str, ttl_seconds: Optional[float]) -> Any:
        """Legacy 直读薄接口：miss/损坏/过期一律 None，无锁不加内存层。

        与 get_or_load 的差别：不做装载、不做失败标记、不抛 StaleData——
        供既有「先读后写」调用形态平移使用（与磁盘 JSON 直读语义等价；
        差异均为防御性增强：路径指向目录返回 None 而非抛 IsADirectoryError、
        字节解码可识别 UTF-16/32、负 TTL 抛 ValueError）。
        """
        policy = CachePolicy(ttl_seconds=ttl_seconds)
        entry = self._read_entry(key, policy)
        if entry is None or not policy.is_fresh(entry.fetched_at_ts, self._clock()):
            return None
        return entry.data

    def write(self, key: str, payload: Any) -> None:
        """Legacy 直写薄接口：原子替换（tmp+rename），无 schema 标记。

        与 legacy 直写的差异：分隔符为紧凑格式（JSON 语义等价）；``None``
        载荷与 NaN/Inf 直接拒绝（legacy 会落盘 ``null``/``NaN`` 字面量）。
        """
        if payload is None:
            raise ValueError("write 载荷不能是 None（legacy null 文件无读取价值）")
        self._write_entry(key, CachePolicy(ttl_seconds=None), payload,
                          previous_generation=None)

    def lock_path(self, key: str) -> Path:
        return sharded_key_path(self.root, key, "locks", ".lock")

    def failure_path(self, key: str) -> Path:
        return sharded_key_path(self.root, key, "failures", ".json")

    def clear_memory(self, key: Optional[str] = None) -> None:
        with self._memory_lock:
            if key is None:
                self._memory.clear()
            else:
                self._memory.pop(key, None)

    def get_or_load_data(
        self,
        key: str,
        policy: CachePolicy,
        loader: Callable[[], T],
        *,
        force_refresh: bool = False,
    ) -> T:
        return self.get_or_load(
            key, policy, loader, force_refresh=force_refresh
        ).data

    def get_or_load(
        self,
        key: str,
        policy: CachePolicy,
        loader: Callable[[], T],
        *,
        force_refresh: bool = False,
    ) -> CacheResult[T]:
        """Return a fresh entry or serialize a load across threads/processes.

        A forced call records the generation it observed before locking. After
        waiting, only a different generation satisfies that forced call without
        invoking its own loader. Failures are never silently hidden by stale
        data unless ``policy.allow_stale`` is explicitly true.
        """

        self.cache_path(key)
        if not isinstance(policy, CachePolicy):
            raise TypeError("policy must be a CachePolicy")
        if not callable(loader):
            raise TypeError("loader must be callable")

        if not force_refresh:
            memory_result = self._memory_get(key, policy)
            if memory_result is not None:
                return memory_result  # type: ignore[return-value]

        initial = self._read_entry(key, policy)
        initial_generation = initial.generation if initial is not None else None
        now = self._clock()

        if not force_refresh and initial is not None and policy.is_fresh(
            initial.fetched_at_ts, now
        ):
            self._memory_set(key, policy, initial)
            return self._result(key, initial, from_cache=True)

        failure = self._read_failure(key, now)
        if failure is not None:
            return self._handle_failure(key, policy, initial, failure, now)

        with self._exclusive_lock(key):
            current = self._read_entry(key, policy)
            now = self._clock()

            if not force_refresh and current is not None and policy.is_fresh(
                current.fetched_at_ts, now
            ):
                self._memory_set(key, policy, current)
                return self._result(key, current, from_cache=True)

            if (
                force_refresh
                and current is not None
                and current.generation != initial_generation
            ):
                self._memory_set(key, policy, current)
                return self._result(key, current, from_cache=True)

            failure = self._read_failure(key, now)
            if failure is not None:
                return self._handle_failure(key, policy, current, failure, now)

            try:
                loaded = loader()
                if loaded is None:
                    raise EmptyLoaderResult("loader returned None; cache was not written")
                if isinstance(loaded, (str, bytes, bytearray)) and len(loaded) == 0:
                    raise EmptyLoaderResult(
                        "loader returned zero bytes; cache was not written"
                    )
                stamped = policy.stamp(loaded)
                written = self._write_entry(
                    key,
                    policy,
                    stamped,
                    previous_generation=(
                        current.generation if current is not None else initial_generation
                    ),
                )
            except Exception as exc:
                failure = self._record_failure(key, policy, exc, self._clock())
                return self._handle_failure(
                    key,
                    policy,
                    current,
                    failure,
                    self._clock(),
                    leader_error=exc,
                )

            self._clear_failure(key)
            self._memory_set(key, policy, written)
            return self._result(key, written, from_cache=False)

    def _result(
        self,
        key: str,
        entry: _Entry,
        *,
        from_cache: bool,
        stale: bool = False,
        error: Optional[str] = None,
    ) -> CacheResult[Any]:
        return CacheResult(
            data=entry.data,
            from_cache=from_cache,
            fetched_at=_iso_timestamp(entry.fetched_at_ts),
            generation=entry.generation,
            stale=stale,
            key=key,
            error=error,
        )

    def _memory_get(self, key: str, policy: CachePolicy) -> Optional[CacheResult[Any]]:
        if not policy.memory_ttl_seconds:
            return None
        with self._memory_lock:
            item = self._memory.get(key)
            if item is None:
                return None
            age = self._monotonic_clock() - item.stored_at
            if age > policy.memory_ttl_seconds or not policy.is_fresh(
                item.entry.fetched_at_ts, self._clock()
            ):
                self._memory.pop(key, None)
                return None
            return self._result(key, item.entry, from_cache=True)

    def _memory_set(self, key: str, policy: CachePolicy, entry: _Entry) -> None:
        if not policy.memory_ttl_seconds:
            return
        with self._memory_lock:
            self._memory[key] = _MemoryItem(entry, self._monotonic_clock())

    def _read_entry(self, key: str, policy: CachePolicy) -> Optional[_Entry]:
        path = self.cache_path(key)
        for _ in range(3):
            try:
                before = path.stat()
                raw = path.read_bytes()
                after = path.stat()
            except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
                return None
            before_state = (before.st_mtime_ns, before.st_size, before.st_ino)
            after_state = (after.st_mtime_ns, after.st_size, after.st_ino)
            if before_state != after_state:
                continue
            if not raw:
                return None
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
            if not policy.accepts(data):
                return None
            return _Entry(data, after.st_mtime_ns / 1_000_000_000, after.st_mtime_ns)
        return None

    def _write_entry(
        self,
        key: str,
        policy: CachePolicy,
        data: Any,
        previous_generation: Optional[int],
    ) -> _Entry:
        path = self.cache_path(key)
        serialized = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        if not serialized:
            raise EmptyLoaderResult("serialized cache payload is empty")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            stat = path.stat()
            if previous_generation is not None and stat.st_mtime_ns <= previous_generation:
                generation = previous_generation + 1
                os.utime(path, ns=(stat.st_atime_ns, generation))
        finally:
            temporary.unlink(missing_ok=True)
        entry = self._read_entry(key, policy)
        if entry is None:
            raise OSError(f"cache entry could not be read after atomic write: {key!r}")
        return entry

    def _read_failure(self, key: str, now: float) -> Optional[_Failure]:
        path = self.failure_path(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            retry_until = float(payload["retry_until"])
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return None
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
            path.unlink(missing_ok=True)
            return None
        if retry_until <= now:
            path.unlink(missing_ok=True)
            return None
        return _Failure(
            retry_until=retry_until,
            error_type=str(payload.get("error_type", "CacheError")),
            message=str(payload.get("message", "")),
        )

    def _record_failure(
        self,
        key: str,
        policy: CachePolicy,
        error: Exception,
        now: float,
    ) -> _Failure:
        failure = _Failure(
            retry_until=now + policy.retry_after_seconds,
            error_type=type(error).__name__,
            message=str(error),
        )
        path = self.failure_path(key)
        payload = json.dumps(
            {
                "retry_until": failure.retry_until,
                "error_type": failure.error_type,
                "message": failure.message,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return failure

    def _clear_failure(self, key: str) -> None:
        try:
            self.failure_path(key).unlink(missing_ok=True)
        except OSError:
            pass

    def _handle_failure(
        self,
        key: str,
        policy: CachePolicy,
        entry: Optional[_Entry],
        failure: _Failure,
        now: float,
        leader_error: Optional[Exception] = None,
    ) -> CacheResult[Any]:
        cause = f"{failure.error_type}: {failure.message}"
        retry_after = max(0.0, failure.retry_until - now)
        if entry is not None:
            if policy.allow_stale:
                return self._result(
                    key,
                    entry,
                    from_cache=True,
                    stale=True,
                    error=cause,
                )
            error = StaleDataError(
                f"refresh for {key!r} failed and stale data is disabled: {cause}",
                stale_data=entry.data,
                fetched_at=_iso_timestamp(entry.fetched_at_ts),
                key=key,
                generation=entry.generation,
                retry_after=retry_after,
            )
            if leader_error is not None:
                raise error from leader_error
            raise error
        if leader_error is not None:
            raise leader_error
        raise CacheRetryAfterError(key, failure.retry_until, now, cause)

    @contextmanager
    def _exclusive_lock(self, key: str) -> Iterator[None]:
        path = self.lock_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_name = str(path)
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(lock_name, threading.Lock())
        with thread_lock:
            with path.open("a+b") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                elif msvcrt is not None:  # pragma: no cover - Windows only
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    elif msvcrt is not None:  # pragma: no cover - Windows only
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


__all__ = [
    "CacheError",
    "CacheRepository",
    "CacheResult",
    "CacheRetryAfterError",
    "EmptyLoaderResult",
    "StaleDataError",
]
