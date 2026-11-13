"""Offline tests for cache policies, legacy keys, and path confinement."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from fund_data_core.cache import CacheRepository
from fund_data_core.cache_policies import (
    CachePolicy,
    FUND_LIST_TTL_SECONDS,
    PROVISIONAL_QUARTER_TTL_SECONDS,
    SETTLED_QUARTER_TTL_SECONDS,
    cache_key_hash,
    fund_list_policy,
    historical_year_policy,
    quarter_policy,
    safe_cache_path,
    sharded_key_path,
    shibor_cache_key,
    snapshot_policy,
)


class CachePolicyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fund-policy-test-")
        self.root = Path(self.temporary.name)
        self.repository = CacheRepository(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_policy_validation_and_no_silent_stale_default(self):
        self.assertFalse(CachePolicy().allow_stale)
        self.assertTrue(CachePolicy(ttl_seconds=None).is_fresh(0, time.time()))
        self.assertFalse(CachePolicy(ttl_seconds=10).is_fresh(100, 111))
        self.assertTrue(CachePolicy(ttl_seconds=10).is_fresh(100, 110))
        for kwargs in (
            {"ttl_seconds": -1},
            {"retry_after_seconds": -1},
            {"memory_ttl_seconds": -1},
            {"schema_version": ""},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CachePolicy(**kwargs)

    def test_snapshot_schema_is_decoupled_from_software_version(self):
        key = "snapshots/000001.json"
        path = self.repository.cache_path(key)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "_meta": {"version": "0.7.1"},
            "name": "legacy",
        }))
        calls = []

        def loader():
            calls.append(1)
            return {"_meta": {"version": "0.0.1"}, "name": "new"}

        policy = snapshot_policy(schema_version="fund-snapshot-v1")
        first = self.repository.get_or_load(key, policy, loader)
        self.assertEqual(calls, [1])
        self.assertEqual(first.data["_meta"]["version"], "0.0.1")
        self.assertEqual(first.data["_meta"]["schema_version"], "fund-snapshot-v1")

        second = self.repository.get_or_load(
            key,
            policy,
            lambda: (_ for _ in ()).throw(AssertionError("fresh schema must not reload")),
        )
        self.assertTrue(second.from_cache)
        self.assertEqual(second.data["_meta"]["version"], "0.0.1")

        incompatible = snapshot_policy(schema_version="fund-snapshot-v2")
        third = self.repository.get_or_load(key, incompatible, loader)
        self.assertEqual(third.data["_meta"]["schema_version"], "fund-snapshot-v2")
        self.assertEqual(third.data["_meta"]["version"], "0.0.1")
        self.assertEqual(calls, [1, 1])

    def test_fund_list_memory_ttl_and_force_bypass(self):
        monotonic = [0.0]
        repository = CacheRepository(self.root, monotonic_clock=lambda: monotonic[0])
        key = "fund_list.json"
        policy = fund_list_policy(memory_ttl_seconds=10)
        self.assertEqual(policy.ttl_seconds, FUND_LIST_TTL_SECONDS)
        calls = []

        def loader():
            calls.append(len(calls) + 1)
            return [{"code": f"00000{calls[-1]}"}]

        first = repository.get_or_load(key, policy, loader)
        self.assertFalse(first.from_cache)
        repository.cache_path(key).unlink()
        second = repository.get_or_load(
            key, policy, lambda: (_ for _ in ()).throw(AssertionError("memory miss"))
        )
        self.assertTrue(second.from_cache)
        self.assertEqual(second.data, [{"code": "000001"}])
        self.assertEqual(calls, [1])

        monotonic[0] = 11
        third = repository.get_or_load(key, policy, loader)
        self.assertEqual(third.data, [{"code": "000002"}])
        self.assertEqual(calls, [1, 2])
        forced = repository.get_or_load(key, policy, loader, force_refresh=True)
        self.assertEqual(forced.data, [{"code": "000003"}])
        self.assertEqual(calls, [1, 2, 3])

    def test_quarter_provisional_and_settled_ttl(self):
        now = date(2026, 11, 13)
        current = quarter_policy(2026, 4, now=now)
        past = quarter_policy(2026, 3, now=now)
        older = quarter_policy(2025, 4, now=now)
        future = quarter_policy(2027, 1, now=now)
        self.assertEqual(current.ttl_seconds, PROVISIONAL_QUARTER_TTL_SECONDS)
        self.assertEqual(future.ttl_seconds, PROVISIONAL_QUARTER_TTL_SECONDS)
        self.assertEqual(past.ttl_seconds, SETTLED_QUARTER_TTL_SECONDS)
        self.assertEqual(older.ttl_seconds, SETTLED_QUARTER_TTL_SECONDS)
        self.assertLess(current.ttl_seconds, past.ttl_seconds)
        with self.assertRaises(ValueError):
            quarter_policy(2026, 5, now=now)

    def test_historical_year_never_expires_periodically(self):
        now = date(2026, 11, 13)
        settled = historical_year_policy(2025, now=now)
        current = historical_year_policy(2026, now=now)
        self.assertIsNone(settled.ttl_seconds)
        self.assertEqual(current.ttl_seconds, PROVISIONAL_QUARTER_TTL_SECONDS)
        with self.assertRaises(ValueError):
            historical_year_policy(2027, now=now)

        key = "history/2024.json"
        path = self.repository.cache_path(key)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps([{"year": 2024, "value": 9}]))
        os.utime(path, (0, 0))
        result = self.repository.get_or_load(
            key,
            settled,
            lambda: (_ for _ in ()).throw(AssertionError("settled year expired")),
        )
        self.assertTrue(result.from_cache)
        self.assertEqual(result.data, [{"year": 2024, "value": 9}])

    def test_shibor_legacy_keys_and_raw_json_compatibility(self):
        self.assertEqual(shibor_cache_key(days=30), "macro/shibor_all_240.json")
        self.assertEqual(shibor_cache_key(days=100), "macro/shibor_all_500.json")
        self.assertEqual(
            shibor_cache_key(period="1年(1Y)", days=30),
            "macro/shibor_1年(1Y)_30.json",
        )
        self.assertEqual(shibor_cache_key(period="隔夜(O/N)", days=1), "macro/shibor_隔夜(O/N)_1.json")
        for days in (0, -1, True, 1.5):
            with self.subTest(days=days), self.assertRaises(ValueError):
                shibor_cache_key(days=days)
        for period in ('bad"period', "bad\\period"):
            with self.subTest(period=period), self.assertRaises(ValueError):
                shibor_cache_key(period=period)

        key = shibor_cache_key(days=30)
        path = self.repository.cache_path(key)
        path.parent.mkdir(parents=True)
        legacy_rows = [{
            "REPORT_DATE": "2026-11-12 00:00:00",
            "REPORT_PERIOD": "隔夜(O/N)",
            "IR_RATE": 1.4,
        }]
        path.write_text(json.dumps(legacy_rows, ensure_ascii=False), encoding="utf-8")
        result = self.repository.get_or_load(
            key,
            CachePolicy(ttl_seconds=None),
            lambda: (_ for _ in ()).throw(AssertionError("legacy SHIBOR cache missed")),
        )
        self.assertTrue(result.from_cache)
        self.assertEqual(result.data, legacy_rows)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), legacy_rows)

    def test_hash_sharding_is_deterministic_and_confined(self):
        first = cache_key_hash("macro/shibor_all_240.json")
        self.assertEqual(first, cache_key_hash("macro/shibor_all_240.json"))
        self.assertNotEqual(first, cache_key_hash("macro/shibor_all_30.json"))
        self.assertEqual(len(first), 64)
        path = sharded_key_path(self.root, "macro/shibor_all_240.json", "locks", ".lock")
        self.assertEqual(path.parent.name, first[2:4])
        self.assertEqual(path.parent.parent.name, first[:2])
        self.assertTrue(path.is_relative_to(self.root.resolve()))
        self.assertTrue(str(path).endswith(first + ".lock"))

    def test_path_attacks_are_rejected_before_loader(self):
        attacks = (
            "../outside.json",
            "a/../../outside.json",
            "/tmp/outside.json",
            "..\\outside.json",
            "C:\\outside.json",
            "C:/outside.json",
            "bad\x00.json",
            "",
        )
        for key in attacks:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    safe_cache_path(self.root, key)
                with self.assertRaises(ValueError):
                    self.repository.get_or_load(
                        key,
                        CachePolicy(),
                        lambda: (_ for _ in ()).throw(AssertionError("unsafe loader called")),
                    )

        with tempfile.TemporaryDirectory(prefix="fund-cache-outside-") as outside:
            link = self.root / "linked"
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                safe_cache_path(self.root, "linked/escape.json")
            with self.assertRaises(ValueError):
                self.repository.get_or_load(
                    "linked/escape.json", CachePolicy(), lambda: {"unsafe": True}
                )
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_validator_is_applied_to_legacy_and_loaded_data(self):
        policy = CachePolicy(
            ttl_seconds=None,
            validator=lambda data: isinstance(data, list) and bool(data),
        )
        key = "validated.json"
        path = self.repository.cache_path(key)
        path.write_text("[]")
        result = self.repository.get_or_load(key, policy, lambda: [{"valid": True}])
        self.assertEqual(result.data, [{"valid": True}])
        self.assertFalse(result.from_cache)


if __name__ == "__main__":
    unittest.main()
