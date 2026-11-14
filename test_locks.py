# -*- coding: utf-8 -*-
"""锁抽象层契约测试：同一套断言对原生后端与伪后端都成立。

契约（模块 docstring 钉死的语义）：跨进程互斥、崩溃自动释放、默认无限
阻塞、同进程多实例互斥、超时语义、锁文件自动创建。伪后端（有界）用于
在任意平台验证重试/超时的共享逻辑，不依赖特定机制的存在。
"""

from __future__ import annotations

import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fund_data_core.errors import LockTimeoutError
from fund_data_core.locks import (ByteRangeBackend, FlockBackend,
                                  _registry_key, drop_extended_prefix,
                                  file_lock, resolve_backend)


def _append_line(path: str, label: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, f"{label}\n".encode("utf-8"))
    finally:
        os.close(descriptor)


def _hold_then_signal(lock_path: str, started_path: str, release_event_path: str) -> None:
    """子进程持锁报到，等父进程放行文件出现后释放退出。"""
    with file_lock(Path(lock_path)):
        _append_line(started_path, "held")
        while not os.path.exists(release_event_path):
            time.sleep(0.05)


def _ready_then_acquire(lock_path: str, ready_path: str, report_path: str) -> None:
    """先报到 ready（证明已启动、即将抢锁），拿锁后写 report。"""
    _append_line(ready_path, "ready")
    with file_lock(Path(lock_path)):
        _append_line(report_path, "acquired")


class DropExtendedPrefixTests(unittest.TestCase):
    def test_local_and_unc_forms(self):
        self.assertEqual(drop_extended_prefix("\\\\?\\C:\\a\\b"), "C:\\a\\b")
        self.assertEqual(drop_extended_prefix("\\\\?\\UNC\\server\\share\\x"),
                         "\\\\server\\share\\x")
        self.assertEqual(drop_extended_prefix("/plain/path"), "/plain/path")
        self.assertEqual(drop_extended_prefix("\\\\server\\share"), "\\\\server\\share")


class RegistryKeyTests(unittest.TestCase):
    def test_equivalent_spellings_share_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(_registry_key(base / "x.lock"),
                             _registry_key(base / "." / "x.lock"))
            self.assertEqual(_registry_key(Path(str(base / "x.lock"))),
                             _registry_key(base / "x.lock"))

    def test_extended_prefix_form_shares_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = str(Path(tmp) / "x.lock")
            extended = "\\\\?\\" + plain
            self.assertEqual(_registry_key(Path(plain)),
                             _registry_key(Path(extended)))


class BackendResolutionTests(unittest.TestCase):
    def test_resolves_to_a_known_mechanism(self):
        backend = resolve_backend()
        self.assertIn(backend.name, ("flock", "byte-range"))

    def test_backend_classes_are_consistent_with_capability(self):
        try:
            import fcntl  # noqa: F401
            self.assertIsInstance(resolve_backend(), FlockBackend)
            return
        except ImportError:
            pass
        try:
            import msvcrt  # noqa: F401
            self.assertIsInstance(resolve_backend(), ByteRangeBackend)
        except ImportError as exc:
            raise AssertionError("两大锁机制至少应存在其一") from exc


class _FakeBoundedBackend:
    """伪有界后端：acquire 抛 OSError 若 gate 未打开——验证重试/超时共享逻辑。"""

    name = "fake-bounded"
    bounded = True

    def __init__(self, gate: threading.Event):
        self.gate = gate
        self.calls = 0

    def acquire(self, handle):
        self.calls += 1
        if not self.gate.is_set():
            raise OSError("lock busy (fake)")

    def release(self, handle):
        pass


class FileLockContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fund-locks-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "a" / "b" / "test.lock"

    def test_lock_file_created(self):
        with file_lock(self.path):
            self.assertTrue(self.path.exists())

    def test_nested_same_path_raises_loudly(self):
        """不可重入契约：与 flock 经不同 fd 的语义一致，宁可显式报错不死锁。"""
        with file_lock(self.path):
            with self.assertRaises(RuntimeError):
                with file_lock(self.path):
                    pass

    def test_same_process_instances_mutually_exclusive(self):
        active = 0
        peak = 0
        guard = threading.Lock()
        barrier = threading.Barrier(4)

        def worker():
            nonlocal active, peak
            barrier.wait()
            with file_lock(self.path):
                with guard:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.15)
                with guard:
                    active -= 1

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(peak, 1)

    def test_waiter_proceeds_after_release(self):
        entered = threading.Event()
        done = threading.Event()

        def waiter():
            with file_lock(self.path):
                entered.set()
            done.set()

        with file_lock(self.path):
            thread = threading.Thread(target=waiter)
            thread.start()
            time.sleep(0.2)
            self.assertFalse(entered.is_set())   # 持锁期间等待者不得进入
        done.wait(5)
        self.assertTrue(entered.is_set())

    def test_timeout_raises_for_bounded_backend(self):
        gate = threading.Event()
        backend = _FakeBoundedBackend(gate)
        with self.assertRaises(LockTimeoutError):
            with file_lock(self.path, timeout=0.4, backend=backend):
                pass
        self.assertGreaterEqual(backend.calls, 2)       # 重试循环确实在转

    def test_bounded_backend_retries_until_gate_opens(self):
        gate = threading.Event()
        backend = _FakeBoundedBackend(gate)
        threading.Timer(0.5, gate.set).start()
        start = time.monotonic()
        with file_lock(self.path, timeout=10, backend=backend):
            elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.4)
        self.assertLess(elapsed, 5)

    def test_byte_range_backend_pads_empty_lock_file(self):
        if _msvcrt_missing():
            self.skipTest("字节范围锁机制不可用")
        backend = ByteRangeBackend()
        with file_lock(self.path, backend=backend):
            self.assertGreaterEqual(self.path.stat().st_size, 1)


def _msvcrt_missing() -> bool:
    try:
        import msvcrt  # noqa: F401
        return False
    except ImportError:
        return True


class MultiProcessContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fund-locks-mp-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_cross_process_mutual_exclusion(self):
        lock_path = self.root / "mp.lock"
        started = str(self.root / "started.txt")
        release = str(self.root / "release.txt")
        context = multiprocessing.get_context("spawn")
        holder = context.Process(
            target=_hold_then_signal, args=(str(lock_path), started, release))
        holder.start()
        deadline = time.monotonic() + 10
        while not os.path.exists(started) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(started), "持锁子进程未报到")

        # 持锁进程存活期间，另一进程就绪后不得进入临界区
        ready = str(self.root / "waiter_ready.txt")
        report = str(self.root / "waiter_done.txt")
        waiter = context.Process(
            target=_ready_then_acquire, args=(str(lock_path), ready, report))
        waiter.start()
        deadline = time.monotonic() + 15
        while not os.path.exists(ready) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(ready), "等待子进程未就绪")
        time.sleep(0.6)
        self.assertFalse(os.path.exists(report), "跨进程互斥失效")
        Path(release).write_text("go")            # 放持锁者退出（锁随之释放）
        holder.join(15)
        waiter.join(15)
        self.assertFalse(holder.is_alive())
        self.assertFalse(waiter.is_alive())
        self.assertEqual(holder.exitcode, 0)
        self.assertEqual(waiter.exitcode, 0)
        self.assertEqual(Path(report).read_text().strip(), "acquired")

    def test_process_death_releases_lock(self):
        lock_path = self.root / "crash.lock"
        started = str(self.root / "crash_started.txt")
        context = multiprocessing.get_context("spawn")
        crashed = context.Process(
            target=_crash_while_holding, args=(str(lock_path), started))
        crashed.start()
        deadline = time.monotonic() + 10
        while not os.path.exists(started) and time.monotonic() < deadline:
            time.sleep(0.05)
        crashed.join(10)

        # 持锁进程死亡后锁必须自动释放：父进程可直接取得
        with file_lock(lock_path, timeout=15):
            pass


def _crash_while_holding(lock_path: str, started_path: str) -> None:
    with file_lock(Path(lock_path)):
        _append_line(started_path, "held")
        time.sleep(0.1)
        os._exit(0)       # 持锁中硬退出——锁只能由操作系统自动释放


if __name__ == "__main__":
    unittest.main()
