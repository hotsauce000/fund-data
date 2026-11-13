"""Offline tests for cache concurrency and failure semantics."""

from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from fund_data_core.cache import (
    CacheRepository,
    CacheRetryAfterError,
    EmptyLoaderResult,
    StaleDataError,
)
from fund_data_core.cache_policies import CachePolicy


def _append_call(path: str, label: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, f"{label}\n".encode("utf-8"))
    finally:
        os.close(descriptor)


def _cold_process_worker(root, key, start, calls_path, queue, label):
    try:
        repository = CacheRepository(root)

        def loader():
            _append_call(calls_path, label)
            time.sleep(0.2)
            return {"value": "loaded-once"}

        start.wait(10)
        result = repository.get_or_load(key, CachePolicy(ttl_seconds=60), loader)
        queue.put({
            "ok": True,
            "data": result.data,
            "from_cache": result.from_cache,
            "generation": result.generation,
        })
    except Exception as exc:
        queue.put({"ok": False, "type": type(exc).__name__, "message": str(exc)})


def _failing_process_worker(root, key, loader_started, release, calls_path, queue):
    try:
        repository = CacheRepository(root)

        def loader():
            _append_call(calls_path, "failure")
            loader_started.set()
            release.wait(10)
            raise RuntimeError("process leader failed")

        repository.get_or_load(key, CachePolicy(ttl_seconds=0, retry_after_seconds=60), loader)
        queue.put({"ok": True})
    except Exception as exc:
        queue.put({
            "ok": False,
            "type": type(exc).__name__,
            "retry_after": getattr(exc, "retry_after", None),
        })


def _crashing_process_worker(root, key, loader_started, allow_crash, calls_path):
    repository = CacheRepository(root)

    def loader():
        _append_call(calls_path, "crash")
        loader_started.set()
        allow_crash.wait(10)
        os._exit(23)

    repository.get_or_load(key, CachePolicy(ttl_seconds=0), loader)


class CacheRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fund-cache-test-")
        self.root = Path(self.temporary.name)
        self.repository = CacheRepository(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_legacy_read_write_thin_api(self):
        # 直写：原子替换、无 schema 标记、路径=legacy 相对路径
        self.repository.write("snapshots/000001.json", [{"code": "000001"}])
        path = self.root / "snapshots" / "000001.json"
        raw = path.read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw), [{"code": "000001"}])
        self.assertNotIn("_meta", raw)
        # 直读：命中
        self.assertEqual(self.repository.read("snapshots/000001.json", 3600.0),
                         [{"code": "000001"}])
        # miss → None
        self.assertIsNone(self.repository.read("snapshots/missing.json", 3600.0))
        # 过期 → None（回拨 mtime）
        past = time.time() - 7200
        os.utime(path, (past, past))
        self.assertIsNone(self.repository.read("snapshots/000001.json", 3600.0))
        # ttl=None 永不过期
        self.assertEqual(self.repository.read("snapshots/000001.json", None),
                         [{"code": "000001"}])
        # 损坏 → None（不抛）
        path.write_text("{broken", encoding="utf-8")
        self.assertIsNone(self.repository.read("snapshots/000001.json", 3600.0))
        # 覆盖写后恢复
        self.repository.write("snapshots/000001.json", {"fresh": True})
        self.assertEqual(self.repository.read("snapshots/000001.json", 3600.0),
                         {"fresh": True})

    def test_fresh_read_expired_load_and_raw_json(self):
        calls = []

        def loader():
            calls.append(len(calls) + 1)
            return {"call": calls[-1]}

        policy = CachePolicy(ttl_seconds=60)
        first = self.repository.get_or_load("snapshots/000001.json", policy, loader)
        second = self.repository.get_or_load("snapshots/000001.json", policy, loader)

        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(second.data, {"call": 1})
        self.assertEqual(second.metadata["from_cache"], True)
        self.assertEqual(second.metadata["generation"], second.generation)
        self.assertEqual(calls, [1])
        raw = json.loads(self.repository.cache_path("snapshots/000001.json").read_text())
        self.assertEqual(raw, {"call": 1})
        self.assertNotIn("from_cache", raw)

        expired = time.time() - 120
        os.utime(self.repository.cache_path("snapshots/000001.json"), (expired, expired))
        third = self.repository.get_or_load("snapshots/000001.json", policy, loader)
        self.assertEqual(third.data, {"call": 2})
        self.assertFalse(third.from_cache)
        self.assertGreater(third.generation, first.generation)
        self.assertEqual(calls, [1, 2])

    def test_thread_cold_start_single_load_and_double_check(self):
        workers = 12
        barrier = threading.Barrier(workers)
        calls = []
        calls_lock = threading.Lock()

        def loader():
            with calls_lock:
                calls.append(1)
            time.sleep(0.15)
            return {"value": 42}

        def call():
            barrier.wait(10)
            return self.repository.get_or_load(
                "fund_list.json", CachePolicy(ttl_seconds=60), loader
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(lambda _: call(), range(workers)))

        self.assertEqual(len(calls), 1)
        self.assertEqual(sum(not result.from_cache for result in results), 1)
        self.assertTrue(all(result.data == {"value": 42} for result in results))
        self.assertEqual(len({result.generation for result in results}), 1)

    def test_concurrent_force_refresh_merges_only_updated_generation(self):
        self.repository.get_or_load(
            "snapshots/force.json", CachePolicy(ttl_seconds=3600), lambda: {"call": 0}
        )
        workers = 8
        barrier = threading.Barrier(workers)
        calls = []
        calls_lock = threading.Lock()

        def loader():
            with calls_lock:
                calls.append(len(calls) + 1)
                number = calls[-1]
            time.sleep(0.2)
            return {"call": number}

        def refresh():
            barrier.wait(10)
            return self.repository.get_or_load(
                "snapshots/force.json",
                CachePolicy(ttl_seconds=3600),
                loader,
                force_refresh=True,
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(lambda _: refresh(), range(workers)))

        self.assertEqual(calls, [1])
        self.assertEqual(sum(not result.from_cache for result in results), 1)
        self.assertTrue(all(result.data == {"call": 1} for result in results))
        self.assertEqual(len({result.generation for result in results}), 1)

        sequential = self.repository.get_or_load(
            "snapshots/force.json",
            CachePolicy(ttl_seconds=3600),
            loader,
            force_refresh=True,
        )
        self.assertEqual(sequential.data, {"call": 2})
        self.assertFalse(sequential.from_cache)
        self.assertEqual(calls, [1, 2])

    def test_retry_after_prevents_consecutive_upstream_calls(self):
        now = [1000.0]
        repository = CacheRepository(self.root, clock=lambda: now[0])
        policy = CachePolicy(ttl_seconds=0, retry_after_seconds=30)
        calls = []

        def failing_loader():
            calls.append("failed")
            raise RuntimeError("upstream unavailable")

        with self.assertRaisesRegex(RuntimeError, "upstream unavailable"):
            repository.get_or_load("macro/retry.json", policy, failing_loader)
        with self.assertRaises(CacheRetryAfterError) as caught:
            repository.get_or_load("macro/retry.json", policy, failing_loader)
        self.assertGreater(caught.exception.retry_after, 29)
        self.assertEqual(calls, ["failed"])

        now[0] += 31
        loaded = repository.get_or_load(
            "macro/retry.json", policy, lambda: {"recovered": True}
        )
        self.assertEqual(loaded.data, {"recovered": True})
        self.assertFalse(repository.failure_path("macro/retry.json").exists())

    def test_stale_error_carries_data_and_explicit_fallback(self):
        policy = CachePolicy(ttl_seconds=1, retry_after_seconds=60)
        key = "snapshots/stale.json"
        original = self.repository.get_or_load(key, policy, lambda: {"nav": 1.23})
        expired = time.time() - 10
        path = self.repository.cache_path(key)
        os.utime(path, (expired, expired))
        expected_fetched_at = (
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        expected_generation = path.stat().st_mtime_ns

        def failing_loader():
            raise RuntimeError("no upstream")

        with self.assertRaises(StaleDataError) as caught:
            self.repository.get_or_load(key, policy, failing_loader)
        error = caught.exception
        self.assertEqual(error.stale_data, {"nav": 1.23})
        self.assertEqual(error.fetched_at, expected_fetched_at)
        self.assertEqual(error.generation, expected_generation)
        self.assertEqual(error.key, key)
        self.assertGreater(error.retry_after, 0)

        with self.assertRaises(StaleDataError):
            self.repository.get_or_load(key, policy, failing_loader)
        fallback = self.repository.get_or_load(
            key, replace(policy, allow_stale=True), failing_loader
        )
        self.assertTrue(fallback.from_cache)
        self.assertTrue(fallback.stale)
        self.assertEqual(fallback.data, {"nav": 1.23})
        self.assertIn("no upstream", fallback.error)

    def test_force_refresh_failure_does_not_silently_use_fresh_cache(self):
        key = "snapshots/force-failure.json"
        policy = CachePolicy(ttl_seconds=3600, retry_after_seconds=60)
        self.repository.get_or_load(key, policy, lambda: {"version": 1})

        def fail():
            raise RuntimeError("forced refresh failed")

        with self.assertRaises(StaleDataError) as caught:
            self.repository.get_or_load(key, policy, fail, force_refresh=True)
        self.assertEqual(caught.exception.stale_data, {"version": 1})
        fallback = self.repository.get_or_load(
            key,
            replace(policy, allow_stale=True),
            fail,
            force_refresh=True,
        )
        self.assertTrue(fallback.stale)
        self.assertEqual(fallback.data, {"version": 1})

    def test_zero_bytes_none_and_exceptions_do_not_write_cache(self):
        policy = CachePolicy(ttl_seconds=0, retry_after_seconds=0)
        for index, value in enumerate((b"", "", None)):
            key = f"empty/{index}.json"
            with self.subTest(value=value):
                with self.assertRaises(EmptyLoaderResult):
                    self.repository.get_or_load(key, policy, lambda value=value: value)
                self.assertFalse(self.repository.cache_path(key).exists())

        key = "empty/preserved.json"
        self.repository.get_or_load(key, CachePolicy(ttl_seconds=3600), lambda: {"good": 1})
        before = self.repository.cache_path(key).read_bytes()
        expired = time.time() - 7200
        os.utime(self.repository.cache_path(key), (expired, expired))
        with self.assertRaises(StaleDataError):
            self.repository.get_or_load(
                key,
                CachePolicy(ttl_seconds=1),
                lambda: (_ for _ in ()).throw(RuntimeError("bad load")),
            )
        self.assertEqual(self.repository.cache_path(key).read_bytes(), before)

    def test_corrupt_cache_is_reloaded_without_temp_files(self):
        key = "snapshots/corrupt.json"
        path = self.repository.cache_path(key)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"{not-json")
        result = self.repository.get_or_load(
            key, CachePolicy(ttl_seconds=60), lambda: {"valid": True}
        )
        self.assertEqual(result.data, {"valid": True})
        self.assertEqual(json.loads(path.read_text()), {"valid": True})
        self.assertEqual(list(path.parent.glob("*.tmp")), [])
        self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_multiprocess_cold_start_loads_once(self):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        queue = context.Queue()
        calls_path = str(self.root / "process-calls.txt")
        key = "macro/cold.json"
        processes = [
            context.Process(
                target=_cold_process_worker,
                args=(str(self.root), key, start, calls_path, queue, str(index)),
            )
            for index in range(3)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(20)
        try:
            self.assertTrue(all(process.exitcode == 0 for process in processes))
            results = [queue.get(timeout=2) for _ in processes]
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)

        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(len(Path(calls_path).read_text().splitlines()), 1)
        self.assertEqual(sum(not result["from_cache"] for result in results), 1)
        self.assertEqual(len({result["generation"] for result in results}), 1)

    def test_process_leader_failure_is_shared_with_waiter(self):
        context = multiprocessing.get_context("spawn")
        loader_started = context.Event()
        release = context.Event()
        queue = context.Queue()
        calls_path = str(self.root / "failure-calls.txt")
        args = (str(self.root), "macro/failure.json", loader_started, release, calls_path, queue)
        leader = context.Process(target=_failing_process_worker, args=args)
        waiter = context.Process(target=_failing_process_worker, args=args)
        leader.start()
        self.assertTrue(loader_started.wait(10))
        waiter.start()
        time.sleep(0.1)
        release.set()
        leader.join(20)
        waiter.join(20)
        try:
            self.assertEqual(leader.exitcode, 0)
            self.assertEqual(waiter.exitcode, 0)
            results = [queue.get(timeout=2), queue.get(timeout=2)]
        finally:
            for process in (leader, waiter):
                if process.is_alive():
                    process.terminate()
                    process.join(5)

        self.assertEqual(len(Path(calls_path).read_text().splitlines()), 1)
        self.assertEqual(
            sorted(result["type"] for result in results),
            ["CacheRetryAfterError", "RuntimeError"],
        )
        retry = next(result for result in results if result["type"] == "CacheRetryAfterError")
        self.assertGreater(retry["retry_after"], 0)

    def test_process_leader_crash_releases_lock_for_successor(self):
        context = multiprocessing.get_context("spawn")
        loader_started = context.Event()
        allow_crash = context.Event()
        start = context.Event()
        queue = context.Queue()
        calls_path = str(self.root / "crash-calls.txt")
        key = "macro/crash.json"
        crasher = context.Process(
            target=_crashing_process_worker,
            args=(str(self.root), key, loader_started, allow_crash, calls_path),
        )
        successor = context.Process(
            target=_cold_process_worker,
            args=(str(self.root), key, start, calls_path, queue, "successor"),
        )
        crasher.start()
        self.assertTrue(loader_started.wait(10))
        successor.start()
        start.set()
        time.sleep(0.1)
        allow_crash.set()
        crasher.join(20)
        successor.join(20)
        try:
            self.assertEqual(crasher.exitcode, 23)
            self.assertEqual(successor.exitcode, 0)
            result = queue.get(timeout=2)
        finally:
            for process in (crasher, successor):
                if process.is_alive():
                    process.terminate()
                    process.join(5)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"], {"value": "loaded-once"})
        self.assertFalse(result["from_cache"])
        self.assertEqual(
            Path(calls_path).read_text().splitlines(), ["crash", "successor"]
        )
        self.assertEqual(
            json.loads(self.repository.cache_path(key).read_text()),
            {"value": "loaded-once"},
        )


if __name__ == "__main__":
    unittest.main()
