# -*- coding: utf-8 -*-
"""跨进程文件锁抽象层：平台差异封闭在本文件，命名只描述机制。

设计契约（所有后端一致，由 test_locks.py 契约套件钉死）：
- 默认无限阻塞；进程死亡由操作系统自动释放锁；
- 同进程多实例经注册表互斥（注册表 key 为归一化路径——剥扩展前缀、
  折叠大小写，杜绝同一锁文件出现两把「进程内锁」）；
- 后端按能力检测（模块可导入性）选择，绝不按操作系统字符串分支；
- 多进程请用 spawn 语义（干净解释器）：fork 会连同进程内注册表状态
  （RLock/owner）一并继承，持锁期间 fork 出的子进程再锁同路径会自锁。

机制与环境的对应关系只属于文档：flock 由 POSIX 系环境提供，字节范围锁
（LockFile 家族）由 Windows CRT 提供；报错与日志只出现机制名。
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator, Optional, Protocol

from .errors import LockBackendError, LockTimeoutError

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - 仅在无 flock 机制的环境触发
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - 仅在无字节范围锁机制的环境触发
    _msvcrt = None


def drop_extended_prefix(text: str) -> str:
    """剥离扩展长度路径前缀（``\\\\?\\`` 与 ``\\\\?\\UNC\\``）。

    同一路径在「文件刚被并发创建」的瞬间可能解析出扩展前缀形态，与普通
    形态做 containment/相等比较会假性不等（坑：多进程冷启动偶发
    "cache key escapes cache root"）。本函数是全库唯一的归一入口。
    """
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


class LockBackend(Protocol):
    """文件锁后端契约：机制命名；bounded 表示 acquire 自身会放弃（需外层补重试）。"""

    name: str
    bounded: bool

    def acquire(self, handle: IO[bytes]) -> None: ...

    def release(self, handle: IO[bytes]) -> None: ...


class FlockBackend:
    """POSIX 文件锁：原生无限阻塞、崩溃自动释放（acquire 失败即真失败）。"""

    name = "flock"
    bounded = False

    @staticmethod
    def acquire(handle: IO[bytes]) -> None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)

    @staticmethod
    def release(handle: IO[bytes]) -> None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


class ByteRangeBackend:
    """CRT 字节范围锁：锁首字节 1 个，范围在 acquire/release 间精确匹配。

    锁原语内部仅重试约 10 秒即抛 OSError——无限阻塞语义由 file_lock 的
    外层重试补齐（这是本后端 bounded=True 的原因）。
    """

    name = "byte-range"
    bounded = True

    @staticmethod
    def acquire(handle: IO[bytes]) -> None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:      # 空文件垫 1 字节，保证有可锁范围
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        _msvcrt.locking(handle.fileno(), _msvcrt.LK_LOCK, 1)

    @staticmethod
    def release(handle: IO[bytes]) -> None:
        handle.seek(0)
        _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)


def resolve_backend() -> LockBackend:
    """能力检测选择后端：flock 优先，其次字节范围锁；两者皆缺则 fail-closed。"""
    if _fcntl is not None:
        return FlockBackend()
    if _msvcrt is not None:
        return ByteRangeBackend()
    raise LockBackendError(
        "无可用文件锁后端（flock 与字节范围锁机制均不可导入）；已 fail-closed 拒绝")


_DEFAULT_BACKEND: Optional[LockBackend] = None


def _default_backend() -> LockBackend:
    """惰性单例：file_lock 在每次请求的热路径上取默认后端，避免反复
    经历失败 import 的异常开销（能力检测只需做一次；后端无状态，
    并发首调的双写竞争无害，后到者覆盖等价对象）。"""
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = resolve_backend()
    return _DEFAULT_BACKEND


_REGISTRY: dict = {}
_REGISTRY_GUARD = threading.Lock()


class _RegistryEntry:
    """进程内每锁文件一条：RLock 串行化线程；owner 把重入死锁变显式报错。"""

    def __init__(self) -> None:
        self.rlock = threading.RLock()
        self.owner = None            # 持锁线程 ident；空闲为 None


def _registry_key(path: Path) -> str:
    """归一化路径键：resolve 消解相对段/短名，剥扩展前缀，折叠大小写。"""
    resolved = path.expanduser().resolve(strict=False)
    return os.path.normcase(drop_extended_prefix(str(resolved)))


def _registry_entry(key: str) -> "_RegistryEntry":
    """取或建注册表条目（guard 保证不依赖 GIL 的 setdefault 原子性）。"""
    with _REGISTRY_GUARD:
        entry = _REGISTRY.get(key)
        if entry is None:
            entry = _RegistryEntry()
            _REGISTRY[key] = entry
        return entry


@contextmanager
def file_lock(path, *, timeout: Optional[float] = None,
              backend: Optional[LockBackend] = None) -> Iterator[None]:
    """跨进程独占锁；语义在所有后端一致（见模块 docstring 契约）。

    不可重入：同线程嵌套同路径锁会立即抛 RuntimeError（与 flock 经不同
    fd 的语义一致——两个后端在二次句柄上都会自锁，与其死锁不如显式报错）。
    timeout=None 无限等待（对齐 flock）；有界后端在期限内未取得锁抛
    LockTimeoutError。release 失败原样上抛（fail-closed 由消费方包装）。
    backend 参数供测试注入伪后端，统一验证重试/超时共享逻辑。
    """
    selected = backend if backend is not None else _default_backend()
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    entry = _registry_entry(_registry_key(lock_path))
    with entry.rlock:
        if entry.owner == threading.get_ident():
            raise RuntimeError(
                f"file_lock 不可重入（同线程嵌套同路径锁）: {lock_path}")
        deadline = None if timeout is None else time.monotonic() + timeout
        with lock_path.open("a+b") as handle:
            while True:
                try:
                    selected.acquire(handle)
                    break
                except OSError as busy:
                    if not selected.bounded:
                        raise                     # 无界后端：失败即真失败
                    if deadline is not None and time.monotonic() >= deadline:
                        raise LockTimeoutError(
                            f"文件锁等待超时（{timeout}s）: {lock_path}",
                            path=str(lock_path), timeout=timeout) from busy
                    time.sleep(0.2)
            entry.owner = threading.get_ident()
            try:
                yield
            finally:
                entry.owner = None
                selected.release(handle)


__all__ = [
    "ByteRangeBackend", "FlockBackend", "LockBackend", "drop_extended_prefix",
    "file_lock", "resolve_backend",
]
