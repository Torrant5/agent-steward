import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import conftest_paths  # noqa: F401

from exi import guard
from exi.quota import parse_quota, read_codex_quota_cached


def make(used=31.0, ok=True, weekly=True, mode="normal"):
    codex = {"ok": ok, "mode": mode, "windows": {"5h": None}}
    if weekly:
        codex["windows"]["weekly"] = {"used_percent": used, "resets_at": "2026-08-20T15:00:00+09:00"}
    return {"providers": {"codex": codex}}


class QuotaParseTest(unittest.TestCase):
    def test_ok(self):
        q = parse_quota(make(used=31.0))
        self.assertTrue(q.ok)
        self.assertEqual(q.weekly_used, 31.0)
        self.assertEqual(q.mode, "normal")

    def test_no_provider(self):
        q = parse_quota({"providers": {}})
        self.assertFalse(q.ok)
        self.assertIsNone(q.weekly_used)
        self.assertIn("codex", q.reason)

    def test_not_ok(self):
        q = parse_quota(make(ok=False))
        self.assertFalse(q.ok)
        self.assertIsNone(q.weekly_used)

    def test_weekly_absent(self):
        q = parse_quota(make(weekly=False))
        self.assertFalse(q.ok)
        self.assertIsNone(q.weekly_used)
        self.assertIn("weekly", q.reason)

    def test_stale_provider_preserves_reason(self):
        data = make(ok=False, mode="unknown")
        data["providers"]["codex"]["error"] = "Codex quota sample is stale (1800s > 900s)"
        q = parse_quota(data)
        self.assertFalse(q.ok)
        self.assertIsNone(q.weekly_used)
        self.assertEqual(q.reason, data["providers"]["codex"]["error"])

    def test_non_object_root_is_unknown(self):
        q = parse_quota([])
        self.assertFalse(q.ok)
        self.assertIsNone(q.weekly_used)
        self.assertIn("root", q.reason)


def _fake_completed(cmd, *args, used_percent=42.0, **kwargs):
    # subprocess.run is invoked as run(cmd, capture_output=True, text=True,
    # timeout=..., check=False) — accept and ignore those kwargs.
    payload = {
        "providers": {
            "codex": {
                "ok": True,
                "mode": "normal",
                "windows": {"weekly": {"used_percent": used_percent, "resets_at": None}},
            }
        }
    }
    return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")


class QuotaCacheTest(unittest.TestCase):
    """Regression for `read_codex_quota_cached`: it must reduce how often
    `llm-quota` is actually spawned without ever fabricating a value or
    losing the `unknown` reason.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["EXI_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("EXI_DATA_DIR", None)
        self.tmp.cleanup()

    def _cfg(self, cache_seconds=30):
        return {"quota": {"cmd": ["llm-quota-stub"], "timeout_seconds": 5, "cache_seconds": cache_seconds}}

    def test_cache_hit_within_ttl_skips_subprocess(self):
        cfg = self._cfg(cache_seconds=30)
        with mock.patch("exi.quota.subprocess.run", side_effect=_fake_completed) as m:
            q1 = read_codex_quota_cached(cfg, now=1000.0)
            q2 = read_codex_quota_cached(cfg, now=1010.0)  # within TTL
            self.assertEqual(m.call_count, 1, "second call within TTL must not re-invoke llm-quota")
            self.assertTrue(q1.ok)
            self.assertEqual(q1.weekly_used, 42.0)
            self.assertEqual(q1.weekly_used, q2.weekly_used)

    def test_cache_expires_after_ttl(self):
        cfg = self._cfg(cache_seconds=5)
        with mock.patch("exi.quota.subprocess.run", side_effect=_fake_completed) as m:
            q1 = read_codex_quota_cached(cfg, now=1000.0)
            q2 = read_codex_quota_cached(cfg, now=1006.0)  # past TTL
            self.assertEqual(m.call_count, 2, "a call after TTL expiry must re-fetch")
            self.assertTrue(q1.ok and q2.ok)

    def test_cache_disabled_when_zero(self):
        cfg = self._cfg(cache_seconds=0)
        with mock.patch("exi.quota.subprocess.run", side_effect=_fake_completed) as m:
            q1 = read_codex_quota_cached(cfg, now=1000.0)
            q2 = read_codex_quota_cached(cfg, now=1000.001)
            self.assertEqual(m.call_count, 2, "cache_seconds<=0 must disable caching")
            self.assertTrue(q1.ok and q2.ok)

    def test_unknown_result_is_cached_with_reason_preserved(self):
        cfg = self._cfg(cache_seconds=30)
        cfg["quota"]["cmd"] = ["/nonexistent/exi-test-llm-quota-stub"]
        q1 = read_codex_quota_cached(cfg, now=1000.0)
        q2 = read_codex_quota_cached(cfg, now=1005.0)
        self.assertFalse(q1.ok)
        self.assertIsNone(q1.weekly_used)
        self.assertEqual(q1.reason, q2.reason)
        self.assertIn("not found", q1.reason)

    def test_malformed_cache_is_ignored_and_refetched(self):
        cfg = self._cfg(cache_seconds=30)
        cache = Path(self.tmp.name) / "quota-cache.json"
        cache.write_text('{"cached_at": 1000, "result": {"broken": true}}', encoding="utf-8")
        with mock.patch("exi.quota.subprocess.run", side_effect=_fake_completed) as m:
            q = read_codex_quota_cached(cfg, now=1001.0)
        self.assertEqual(m.call_count, 1)
        self.assertTrue(q.ok)

    def test_weekly_reset_and_positive_delta_unaffected_by_repeated_cache_hits(self):
        # A cache hit re-reports the same weekly_used repeatedly; feeding that
        # into guard.weekly_increment must contribute zero delta each time —
        # never a false reset (negative) nor manufactured consumption.
        cfg = self._cfg(cache_seconds=30)
        samples = []
        now = 1000.0
        with mock.patch("exi.quota.subprocess.run", side_effect=_fake_completed):
            for _ in range(5):
                q = read_codex_quota_cached(cfg, now=now)
                guard.record_sample({"samples": samples}, now, q.weekly_used, 48)
                now += 1.0
        inc, n = guard.weekly_increment(samples, 0, now)
        self.assertEqual(inc, 0.0)
        self.assertEqual(n, 5)


if __name__ == "__main__":
    unittest.main()
