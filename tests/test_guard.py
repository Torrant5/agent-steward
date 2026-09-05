import multiprocessing as mp
import os
import tempfile
import unittest

import conftest_paths  # noqa: F401

from exi import guard

CFG = {
    "guard": {
        "turn_soft_minutes": 45,
        "turn_hard_minutes": 120,
        "weekly_turn_soft_pct": 3.0,
        "weekly_turn_hard_pct": 5.0,
        "weekly_24h_soft_pct": 12.0,
        "weekly_24h_hard_pct": 20.0,
        "tool_hard_count": 150,
        "fingerprint_repeat_max": 3,
        "sample_retention_hours": 48,
    }
}


def ctx(**kw):
    base = {"elapsed_minutes": 1.0, "tool_count": 1, "max_fingerprint": 1, "turn_pct": 0.0, "h24_pct": 0.0}
    base.update(kw)
    return base


class EvaluateTest(unittest.TestCase):
    def test_normal_no_findings(self):
        self.assertEqual(guard.evaluate(CFG, ctx()), [])

    def test_time_soft_then_hard(self):
        self.assertEqual(guard.worst_level(guard.evaluate(CFG, ctx(elapsed_minutes=50))), guard.WARN)
        self.assertEqual(guard.worst_level(guard.evaluate(CFG, ctx(elapsed_minutes=130))), guard.HARD)

    def test_turn_quota(self):
        self.assertEqual(guard.worst_level(guard.evaluate(CFG, ctx(turn_pct=3.5))), guard.WARN)
        self.assertEqual(guard.worst_level(guard.evaluate(CFG, ctx(turn_pct=6.0))), guard.HARD)

    def test_rolling_24h(self):
        self.assertEqual(guard.worst_level(guard.evaluate(CFG, ctx(h24_pct=13.0))), guard.WARN)
        self.assertEqual(guard.worst_level(guard.evaluate(CFG, ctx(h24_pct=25.0))), guard.HARD)

    def test_tool_count_hard(self):
        self.assertEqual(guard.worst_level(guard.evaluate(CFG, ctx(tool_count=151))), guard.HARD)
        self.assertEqual(guard.evaluate(CFG, ctx(tool_count=150)), [])

    def test_repeat_fingerprint_hard(self):
        self.assertEqual(guard.worst_level(guard.evaluate(CFG, ctx(max_fingerprint=3))), guard.HARD)
        self.assertEqual(guard.evaluate(CFG, ctx(max_fingerprint=2)), [])

    def test_quota_unknown_keeps_time_guard(self):
        # quota unknown => turn_pct/h24_pct None; time still blocks
        c = ctx(turn_pct=None, h24_pct=None, elapsed_minutes=130)
        self.assertEqual(guard.worst_level(guard.evaluate(CFG, c)), guard.HARD)

    def test_quota_unknown_alone_does_not_block(self):
        c = ctx(turn_pct=None, h24_pct=None, elapsed_minutes=1, tool_count=1, max_fingerprint=1)
        self.assertEqual(guard.evaluate(CFG, c), [])


class WeeklyIncrementTest(unittest.TestCase):
    def test_positive_increment(self):
        samples = [{"ts": 100, "used": 30.0}, {"ts": 200, "used": 34.0}]
        inc, n = guard.weekly_increment(samples, 0, 300)
        self.assertAlmostEqual(inc, 4.0)
        self.assertEqual(n, 2)

    def test_reset_ignored(self):
        # used drops from 60 -> 5 (weekly reset), then climbs to 12
        samples = [
            {"ts": 100, "used": 60.0},
            {"ts": 200, "used": 5.0},   # reset: negative delta ignored
            {"ts": 300, "used": 12.0},  # +7 real consumption post-reset
        ]
        inc, n = guard.weekly_increment(samples, 0, 400)
        self.assertAlmostEqual(inc, 7.0)  # only the positive delta counts

    def test_single_sample_unknown(self):
        inc, n = guard.weekly_increment([{"ts": 100, "used": 30.0}], 0, 300)
        self.assertIsNone(inc)

    def test_none_samples_skipped(self):
        samples = [{"ts": 100, "used": None}, {"ts": 200, "used": 40.0}, {"ts": 300, "used": 42.0}]
        inc, n = guard.weekly_increment(samples, 0, 400)
        self.assertAlmostEqual(inc, 2.0)
        self.assertEqual(n, 2)


class ContextResetTest(unittest.TestCase):
    def test_compute_context_reset_safe(self):
        now = 10_000.0
        state = {
            "turns": {
                "s1\x1ft": {"id": "s1\x1ft", "started_at": now - 600, "tool_count": 4,
                            "fingerprints": {"a": 2}, "start_used": 60.0},
            },
            "samples": [
                {"ts": now - 600, "used": 60.0},
                {"ts": now - 300, "used": 3.0},   # reset mid-turn
                {"ts": now - 60, "used": 6.0},
            ],
        }
        c = guard.compute_context(state, "s1\x1ft", now, CFG)
        self.assertAlmostEqual(c["turn_pct"], 3.0)  # only +3 post-reset, not negative
        self.assertEqual(c["tool_count"], 4)
        self.assertEqual(c["max_fingerprint"], 2)
        self.assertAlmostEqual(c["elapsed_minutes"], 10.0)


class TurnKeyTest(unittest.TestCase):
    def test_session_and_turn_id_combine(self):
        k1 = guard.turn_key({"session_id": "s1", "turn_id": "a"})
        k2 = guard.turn_key({"session_id": "s1", "turn_id": "b"})
        k3 = guard.turn_key({"session_id": "s2", "turn_id": "a"})
        self.assertNotEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    def test_camel_case_and_missing_fallback(self):
        self.assertEqual(
            guard.turn_key({"sessionId": "s1", "turnId": "a"}),
            guard.turn_key({"session_id": "s1", "turn_id": "a"}),
        )
        self.assertEqual(guard.turn_key({}), guard.turn_key({}))


class LegacyMigrationTest(unittest.TestCase):
    def test_legacy_turn_moved_to_turns(self):
        old = {"turn": {"id": "orphan", "tool_count": 2}, "samples": [{"ts": 1, "used": 5.0}]}
        migrated = guard._migrate_legacy(old)
        self.assertNotIn("turn", migrated)
        self.assertEqual(migrated["turns"][guard.LEGACY_TURN_KEY]["tool_count"], 2)
        self.assertEqual(migrated["samples"], [{"ts": 1, "used": 5.0}])

    def test_legacy_none_turn_becomes_empty_turns(self):
        migrated = guard._migrate_legacy({"turn": None, "samples": []})
        self.assertEqual(migrated["turns"], {})


class StateCorruptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["EXI_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("EXI_DATA_DIR", None)
        self.tmp.cleanup()

    def test_corrupt_json_raises_instead_of_silently_resetting(self):
        guard.state_path().write_text("{not json", encoding="utf-8")
        with self.assertRaises(guard.StateCorruptError):
            guard.load_state()

    def test_non_object_json_raises(self):
        guard.state_path().write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(guard.StateCorruptError):
            guard.load_state()

    def test_empty_file_is_treated_as_fresh(self):
        guard.state_path().write_text("", encoding="utf-8")
        self.assertEqual(guard.load_state(), {"turns": {}, "samples": []})


def _bump_tool(n: int, key: str) -> None:
    for i in range(n):
        with guard.locked_state() as state:
            guard.record_tool(state, key, "Bash", {"i": i})


class LockedStateConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["EXI_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("EXI_DATA_DIR", None)
        self.tmp.cleanup()

    def test_concurrent_hooks_do_not_lose_updates(self):
        n_procs, n_each = 6, 40
        key = "s1\x1ft1"
        procs = [mp.Process(target=_bump_tool, args=(n_each, key)) for _ in range(n_procs)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        state = guard.load_state()
        self.assertEqual(state["turns"][key]["tool_count"], n_procs * n_each)


ACK_CFG = {"guard": {**CFG["guard"], "ack_phrases": ["guard ok", "予算OK"], "ack_grant_pct": 5.0, "ack_grant_hours": 3.0}}


class AckPureTest(unittest.TestCase):
    def test_match_ack_prefix_only(self):
        phrases = ACK_CFG["guard"]["ack_phrases"]
        self.assertEqual(guard.match_ack("guard ok", phrases), "guard ok")
        self.assertEqual(guard.match_ack("  Guard OK, continue", phrases), "guard ok")
        self.assertEqual(guard.match_ack("予算OK続けて", phrases), "予算OK")
        self.assertEqual(guard.match_ack("予算ＯＫ", phrases), "予算OK")  # full-width folded
        self.assertIsNone(guard.match_ack("please guard ok", phrases))
        self.assertIsNone(guard.match_ack("", phrases))
        self.assertIsNone(guard.match_ack("guard ok", []))
        self.assertIsNone(guard.match_ack("guard ok", ["", None, "   "]))

    def test_ack_status_none_active_expired_spent(self):
        st = {"turns": {}, "samples": []}
        self.assertFalse(guard.ack_status(st, 100.0, ACK_CFG)["active"])
        guard.grant_ack(st, 100.0, 30.0, "guard ok")
        a = guard.ack_status(st, 200.0, ACK_CFG)
        self.assertTrue(a["active"])
        self.assertIn("remaining +5.0% weekly", a["reason"])
        # time expiry
        self.assertFalse(guard.ack_status(st, 100.0 + 3 * 3600, ACK_CFG)["active"])
        # pct expiry: +5 since ack (reset-safe: a drop in between is ignored)
        st["samples"] = [
            {"ts": 100.0, "used": 30.0},
            {"ts": 150.0, "used": 33.0},
            {"ts": 160.0, "used": 1.0},   # weekly reset
            {"ts": 170.0, "used": 3.0},
        ]
        self.assertFalse(guard.ack_status(st, 200.0, ACK_CFG)["active"])
        self.assertIn("ack spent", guard.ack_status(st, 200.0, ACK_CFG)["reason"])
        # samples before the ack do not count
        st["samples"] = [{"ts": 10.0, "used": 0.0}, {"ts": 90.0, "used": 30.0}, {"ts": 150.0, "used": 31.0}]
        self.assertTrue(guard.ack_status(st, 200.0, ACK_CFG)["active"])

    def test_ack_status_ignores_zero_or_missing_bounds(self):
        st = {"turns": {}, "samples": [{"ts": 100.0, "used": 0.0}, {"ts": 150.0, "used": 90.0}]}
        guard.grant_ack(st, 100.0, 0.0, "guard ok")
        cfg = {"guard": {"ack_grant_pct": 0, "ack_grant_hours": 0}}
        a = guard.ack_status(st, 100.0 + 100 * 3600, cfg)
        self.assertTrue(a["active"])
        self.assertIn("unbounded", a["reason"])

    def test_apply_ack_only_suppresses_weekly_24h(self):
        findings = guard.evaluate(CFG, ctx(h24_pct=25.0, elapsed_minutes=130, max_fingerprint=3))
        kept, suppressed = guard.apply_ack(findings, {"active": True})
        self.assertEqual([f["code"] for f in suppressed], ["weekly_24h"])
        self.assertEqual(sorted(f["code"] for f in kept), ["repeat", "turn_time"])
        kept2, suppressed2 = guard.apply_ack(findings, {"active": False})
        self.assertEqual(kept2, findings)
        self.assertEqual(suppressed2, [])

    def test_ack_hint_lists_phrases_and_bounds(self):
        hint = guard.ack_hint(ACK_CFG)
        self.assertIn('"guard ok" or "予算OK"', hint)
        self.assertIn("+5% weekly", hint)
        self.assertIn("3h", hint)
        self.assertEqual(guard.ack_hint({"guard": {"ack_phrases": []}}), "")


if __name__ == "__main__":
    unittest.main()
