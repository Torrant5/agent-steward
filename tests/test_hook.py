import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

import conftest_paths  # noqa: F401

from exi import hook, guard
from exi.quota import QuotaResult


def q_known(used):
    return QuotaResult(weekly_used=used, resets_at=None, mode="normal", ok=True, reason="")


def q_unknown(reason="weekly window unavailable"):
    return QuotaResult(weekly_used=None, resets_at=None, mode=None, ok=False, reason=reason)


class _HookBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["EXI_DATA_DIR"] = self.tmp.name
        self._orig_quota = hook.read_codex_quota
        # never pop real desktop notifications from the suite; record calls instead
        self._orig_notify = hook._notify_block
        self.notifications = []
        hook._notify_block = lambda cfg, title, text: self.notifications.append((title, text))

    def tearDown(self):
        hook.read_codex_quota = self._orig_quota
        hook._notify_block = self._orig_notify
        os.environ.pop("EXI_DATA_DIR", None)
        self.tmp.cleanup()

    def _at(self, now, fn):
        """Run fn() with time.time() pinned to `now`."""
        import time as time_mod
        orig = time_mod.time
        time_mod.time = lambda: now
        try:
            return fn()
        finally:
            time_mod.time = orig

    def _seed_h24_hard(self, now):
        """Pre-existing samples: +25% weekly within the last 24h (>= hard 20%)."""
        guard.save_state({
            "turns": {},
            "samples": [
                {"ts": now - 3600, "used": 10.0},
                {"ts": now - 1800, "used": 35.0},
            ],
        })
        hook.read_codex_quota = lambda cfg: q_known(35.0)

    def _blocks_log(self):
        p = os.path.join(self.tmp.name, "blocks.log")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _run(self, event, payload=None):
        hook_in = io.StringIO(json.dumps(payload or {}))
        out, err = io.StringIO(), io.StringIO()
        import sys
        orig = sys.stdin
        sys.stdin = hook_in
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = hook.handle(event)
        finally:
            sys.stdin = orig
        return rc, out.getvalue(), err.getvalue()

    def _is_deny(self, stdout):
        stdout = stdout.strip()
        if not stdout:
            return False
        obj = json.loads(stdout)
        return obj.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def _is_stop(self, stdout):
        stdout = stdout.strip()
        if not stdout:
            return False
        obj = json.loads(stdout)
        return obj.get("continue") is False and "stopReason" in obj and "systemMessage" in obj


class HookTest(_HookBase):
    def test_normal_allows(self):
        hook.read_codex_quota = lambda cfg: q_known(30.0)
        self._run("UserPromptSubmit", {"prompt": "hi"})
        rc, out, err = self._run("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(rc, 0)
        self.assertFalse(self._is_deny(out))

    def test_repeat_fingerprint_denies(self):
        hook.read_codex_quota = lambda cfg: q_known(30.0)
        self._run("UserPromptSubmit", {"prompt": "go"})
        payload = {"tool_name": "Bash", "tool_input": {"command": "same"}}
        self._run("PreToolUse", payload)
        _, out2, _ = self._run("PreToolUse", payload)
        self.assertFalse(self._is_deny(out2))  # 2nd repeat: still allowed (max=3)
        _, out3, _ = self._run("PreToolUse", payload)
        self.assertTrue(self._is_deny(out3))   # 3rd identical call: deny

    def test_time_hard_denies(self):
        hook.read_codex_quota = lambda cfg: q_known(30.0)
        self._run("UserPromptSubmit", {"prompt": "go"})
        # rewind the turn start ~3h into the past
        st = guard.load_state()
        key = guard.turn_key({})
        st["turns"][key]["started_at"] -= 3 * 3600
        guard.save_state(st)
        _, out, _ = self._run("PreToolUse", {"tool_name": "Read", "tool_input": {"p": "x"}})
        self.assertTrue(self._is_deny(out))

    def test_quota_unknown_does_not_deny_but_time_still_guards(self):
        hook.read_codex_quota = lambda cfg: q_unknown()
        self._run("UserPromptSubmit", {"prompt": "go"})
        _, out, _ = self._run("PreToolUse", {"tool_name": "Read", "tool_input": {"p": "1"}})
        self.assertFalse(self._is_deny(out))  # unknown quota alone must not block
        # but a stale/old turn still trips the time guard
        st = guard.load_state()
        key = guard.turn_key({})
        st["turns"][key]["started_at"] -= 3 * 3600
        guard.save_state(st)
        _, out2, _ = self._run("PreToolUse", {"tool_name": "Read", "tool_input": {"p": "2"}})
        self.assertTrue(self._is_deny(out2))

    def test_weekly_turn_increment_denies(self):
        # weekly usage jumps within the turn beyond hard (5%)
        seq = iter([30.0, 30.0, 40.0])  # UPS sample, PTU1 sample, PTU2 sample
        hook.read_codex_quota = lambda cfg: q_known(next(seq))
        self._run("UserPromptSubmit", {"prompt": "go"})
        self._run("PreToolUse", {"tool_name": "A", "tool_input": {"i": 1}})
        _, out, _ = self._run("PreToolUse", {"tool_name": "B", "tool_input": {"i": 2}})
        self.assertTrue(self._is_deny(out))  # +10% this turn >= hard 5%

    def test_reset_within_turn_not_blocked(self):
        # usage drops (weekly reset) then small climb -> must NOT deny
        seq = iter([60.0, 61.0, 3.0, 4.0])
        hook.read_codex_quota = lambda cfg: q_known(next(seq))
        self._run("UserPromptSubmit", {"prompt": "go"})
        self._run("PreToolUse", {"tool_name": "A", "tool_input": {"i": 1}})
        _, out, _ = self._run("PreToolUse", {"tool_name": "B", "tool_input": {"i": 2}})
        self.assertFalse(self._is_deny(out))

    def test_turn_key_from_payload_session_and_turn_id(self):
        hook.read_codex_quota = lambda cfg: q_known(30.0)
        self._run("UserPromptSubmit", {"prompt": "go", "session_id": "sX", "turn_id": "tY"})
        st = guard.load_state()
        self.assertIn(guard.turn_key({"session_id": "sX", "turn_id": "tY"}), st["turns"])

    def test_two_sessions_do_not_cross_contaminate(self):
        hook.read_codex_quota = lambda cfg: q_known(30.0)
        self._run("UserPromptSubmit", {"prompt": "go", "session_id": "s1", "turn_id": "t1"})
        self._run("UserPromptSubmit", {"prompt": "go", "session_id": "s2", "turn_id": "t1"})

        same_call = {"tool_name": "Bash", "tool_input": {"command": "same"}}
        # session s1 makes the call twice (allowed, max repeat is 3)
        self._run("PreToolUse", {**same_call, "session_id": "s1", "turn_id": "t1"})
        _, out_s1_2, _ = self._run("PreToolUse", {**same_call, "session_id": "s1", "turn_id": "t1"})
        self.assertFalse(self._is_deny(out_s1_2))
        # session s2's own first call must not be affected by s1's fingerprint count
        _, out_s2_1, _ = self._run("PreToolUse", {**same_call, "session_id": "s2", "turn_id": "t1"})
        self.assertFalse(self._is_deny(out_s2_1))
        # third identical call in s1 trips repeat guard; s2 stays independent (still 1st call)
        _, out_s1_3, _ = self._run("PreToolUse", {**same_call, "session_id": "s1", "turn_id": "t1"})
        self.assertTrue(self._is_deny(out_s1_3))
        _, out_s2_2, _ = self._run("PreToolUse", {**same_call, "session_id": "s2", "turn_id": "t1"})
        self.assertFalse(self._is_deny(out_s2_2))  # s2's 2nd call, still under threshold

    def test_precompact_hard_uses_stop_shape_not_permission_decision(self):
        hook.read_codex_quota = lambda cfg: q_known(30.0)
        self._run("UserPromptSubmit", {"prompt": "go"})
        st = guard.load_state()
        key = guard.turn_key({})
        st["turns"][key]["started_at"] -= 3 * 3600  # push past turn_hard_minutes
        guard.save_state(st)
        _, out, _ = self._run("PreCompact", {})
        self.assertTrue(self._is_stop(out))
        self.assertFalse(self._is_deny(out))
        obj = json.loads(out.strip())
        self.assertNotIn("hookSpecificOutput", obj)

    def test_precompact_allows_when_no_hard_finding(self):
        hook.read_codex_quota = lambda cfg: q_known(30.0)
        self._run("UserPromptSubmit", {"prompt": "go"})
        rc, out, _ = self._run("PreCompact", {})
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_user_prompt_submit_24h_hard_stops(self):
        # pre-existing samples show a big rolling-24h jump before this new turn starts
        now = 10_000.0
        state = {
            "turns": {},
            "samples": [
                {"ts": now - 3600, "used": 10.0},
                {"ts": now - 1800, "used": 35.0},  # +25% within 24h >= hard 20%
            ],
        }
        guard.save_state(state)
        hook.read_codex_quota = lambda cfg: q_known(35.0)
        import time as time_mod
        orig_time = time_mod.time
        time_mod.time = lambda: now
        try:
            _, out, _ = self._run("UserPromptSubmit", {"prompt": "go", "session_id": "s1", "turn_id": "t1"})
        finally:
            time_mod.time = orig_time
        self.assertTrue(self._is_stop(out))
        # a new turn must still have been initialized despite the stop
        st = guard.load_state()
        self.assertIn(guard.turn_key({"session_id": "s1", "turn_id": "t1"}), st["turns"])

    def test_corrupt_state_fails_closed_for_all_events(self):
        guard.state_path().write_text("{not json", encoding="utf-8")
        hook.read_codex_quota = lambda cfg: q_known(30.0)

        _, out, _ = self._run("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertTrue(self._is_deny(out))

        _, out, _ = self._run("PreCompact", {})
        self.assertTrue(self._is_stop(out))

        _, out, _ = self._run("UserPromptSubmit", {"prompt": "go"})
        self.assertTrue(self._is_stop(out))


class AckTest(_HookBase):
    """User acknowledgement of a weekly_24h stop: bounded resume, visibility."""

    def _context_text(self, stdout):
        stdout = stdout.strip()
        if not stdout:
            return None
        obj = json.loads(stdout)
        return obj.get("hookSpecificOutput", {}).get("additionalContext")

    def test_stop_reason_carries_ack_hint_and_is_logged_and_notified(self):
        now = 10_000.0
        self._seed_h24_hard(now)
        _, out, err = self._at(now, lambda: self._run("UserPromptSubmit", {"prompt": "go", "session_id": "s1", "turn_id": "t1"}))
        self.assertTrue(self._is_stop(out))
        obj = json.loads(out.strip())
        self.assertIn('"guard ok"', obj["stopReason"])
        self.assertIn("To resume", obj["stopReason"])
        # blocks.log: one JSON line, codes + ids, never the prompt
        log = self._blocks_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["kind"], "stop")
        self.assertEqual(log[0]["event"], "UserPromptSubmit")
        self.assertEqual(log[0]["codes"], ["weekly_24h"])
        self.assertEqual(log[0]["session_id"], "s1")
        self.assertNotIn("prompt", log[0])
        # desktop notification requested with the hint
        self.assertEqual(len(self.notifications), 1)
        self.assertIn("weekly_24h", self.notifications[0][0])
        self.assertIn("guard ok", self.notifications[0][1])

    def test_ack_prefix_resumes_and_records_ack(self):
        now = 10_000.0
        self._seed_h24_hard(now)
        _, out, err = self._at(now, lambda: self._run(
            "UserPromptSubmit", {"prompt": "guard ok まあ一旦進めていいよ", "session_id": "s1", "turn_id": "t1"}))
        self.assertFalse(self._is_stop(out))
        ctx = self._context_text(out)
        self.assertIsNotNone(ctx)
        self.assertIn("guard ok", ctx)
        self.assertIn("ack active", ctx)
        self.assertIn("[codex-guard ACK]", err)
        st = guard.load_state()
        self.assertEqual(st["ack"]["phrase"], "guard ok")
        self.assertEqual(st["ack"]["ts"], now)
        self.assertEqual(self._blocks_log(), [])
        self.assertEqual(self.notifications, [])

    def test_japanese_phrase_without_space_boundary_resumes(self):
        now = 10_000.0
        self._seed_h24_hard(now)
        _, out, _ = self._at(now, lambda: self._run("UserPromptSubmit", {"prompt": "予算OK続けて"}))
        self.assertFalse(self._is_stop(out))
        self.assertIn("予算OK", self._context_text(out))

    def test_ack_must_be_a_prefix(self):
        now = 10_000.0
        self._seed_h24_hard(now)
        _, out, _ = self._at(now, lambda: self._run("UserPromptSubmit", {"prompt": "続けて guard ok"}))
        self.assertTrue(self._is_stop(out))

    def test_following_prompts_and_tools_pass_while_ack_active(self):
        now = 10_000.0
        self._seed_h24_hard(now)
        self._at(now, lambda: self._run("UserPromptSubmit", {"prompt": "guard ok", "session_id": "s1", "turn_id": "t1"}))
        # tool calls in the acked turn: h24 HARD suppressed, no deny
        _, out, _ = self._at(now + 10, lambda: self._run(
            "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "session_id": "s1", "turn_id": "t1"}))
        self.assertFalse(self._is_deny(out))
        # a later plain prompt (new turn) still passes, with a note
        _, out2, _ = self._at(now + 600, lambda: self._run("UserPromptSubmit", {"prompt": "次は？", "session_id": "s1", "turn_id": "t2"}))
        self.assertFalse(self._is_stop(out2))
        self.assertIn("suppressed by a user acknowledgement", self._context_text(out2))
        # PreCompact honours the ack too
        _, out3, _ = self._at(now + 601, lambda: self._run("PreCompact", {"session_id": "s1", "turn_id": "t2"}))
        self.assertEqual(out3.strip(), "")

    def test_ack_only_covers_weekly_24h(self):
        now = 10_000.0
        self._seed_h24_hard(now)
        self._at(now, lambda: self._run("UserPromptSubmit", {"prompt": "guard ok", "session_id": "s1", "turn_id": "t1"}))
        payload = {"tool_name": "Bash", "tool_input": {"command": "same"}, "session_id": "s1", "turn_id": "t1"}
        self._at(now + 1, lambda: self._run("PreToolUse", payload))
        self._at(now + 2, lambda: self._run("PreToolUse", payload))
        _, out, _ = self._at(now + 3, lambda: self._run("PreToolUse", payload))
        self.assertTrue(self._is_deny(out))  # repeat guard still enforced
        log = self._blocks_log()
        self.assertEqual(log[-1]["kind"], "deny")
        self.assertIn("repeat", log[-1]["codes"])

    def test_ack_spent_by_weekly_pct_stops_again_with_previous_ack_note(self):
        now = 10_000.0
        self._seed_h24_hard(now)
        self._at(now, lambda: self._run("UserPromptSubmit", {"prompt": "guard ok", "session_id": "s1", "turn_id": "t1"}))
        # usage climbs +6% since the ack (>= grant 5%)
        seq = iter([38.0, 41.0])
        hook.read_codex_quota = lambda cfg: q_known(next(seq))
        self._at(now + 60, lambda: self._run("PreToolUse", {"tool_name": "A", "tool_input": {"i": 1}, "session_id": "s1", "turn_id": "t1"}))
        self._at(now + 120, lambda: self._run("PreToolUse", {"tool_name": "B", "tool_input": {"i": 2}, "session_id": "s1", "turn_id": "t1"}))
        hook.read_codex_quota = lambda cfg: q_known(41.0)
        _, out, _ = self._at(now + 200, lambda: self._run("UserPromptSubmit", {"prompt": "続けて", "session_id": "s1", "turn_id": "t2"}))
        self.assertTrue(self._is_stop(out))
        obj = json.loads(out.strip())
        self.assertIn("previous ack: ack spent", obj["stopReason"])
        # a fresh ack resumes again
        _, out2, _ = self._at(now + 201, lambda: self._run("UserPromptSubmit", {"prompt": "guard ok", "session_id": "s1", "turn_id": "t3"}))
        self.assertFalse(self._is_stop(out2))

    def test_ack_expires_by_hours(self):
        now = 10_000.0
        self._seed_h24_hard(now)
        self._at(now, lambda: self._run("UserPromptSubmit", {"prompt": "guard ok", "session_id": "s1", "turn_id": "t1"}))
        later = now + 3 * 3600 + 1
        # keep the 24h window HARD at `later` (seeded samples are still inside it)
        _, out, _ = self._at(later, lambda: self._run("UserPromptSubmit", {"prompt": "続けて", "session_id": "s1", "turn_id": "t2"}))
        self.assertTrue(self._is_stop(out))
        self.assertIn("previous ack: ack expired", json.loads(out.strip())["stopReason"])

    def test_ack_when_not_blocked_is_harmless_note(self):
        hook.read_codex_quota = lambda cfg: q_known(30.0)
        _, out, _ = self._run("UserPromptSubmit", {"prompt": "guard ok go"})
        self.assertFalse(self._is_stop(out))
        self.assertIn("control word", self._context_text(out))

    def test_weekly_reset_after_ack_does_not_count_as_spent(self):
        now = 10_000.0
        self._seed_h24_hard(now)
        self._at(now, lambda: self._run("UserPromptSubmit", {"prompt": "guard ok", "session_id": "s1", "turn_id": "t1"}))
        seq = iter([36.0, 2.0, 4.0])  # small climb, weekly reset, small climb
        hook.read_codex_quota = lambda cfg: q_known(next(seq))
        for i in range(3):
            self._at(now + 60 * (i + 1), lambda: self._run(
                "PreToolUse", {"tool_name": "A", "tool_input": {"i": i}, "session_id": "s1", "turn_id": "t1"}))
        st = guard.load_state()
        ack = guard.ack_status(st, now + 300, {"guard": {"ack_grant_pct": 5.0, "ack_grant_hours": 3.0}})
        self.assertTrue(ack["active"])
        self.assertAlmostEqual(ack["used_since_ack_pct"], 3.0)  # +1 then +2; the drop is ignored


class NotifyTest(unittest.TestCase):
    """_notify_block shells out to osascript on macOS only, and never raises."""

    def setUp(self):
        self._which, self._run, self._platform = hook.shutil.which, hook.subprocess.run, hook.sys.platform

    def tearDown(self):
        hook.shutil.which, hook.subprocess.run, hook.sys.platform = self._which, self._run, self._platform

    def test_darwin_calls_osascript_with_escaped_text(self):
        calls = []
        hook.sys.platform = "darwin"
        hook.shutil.which = lambda name: "/usr/bin/osascript"
        hook.subprocess.run = lambda argv, **kw: calls.append((argv, kw))
        hook._notify_block({"guard": {"block_notify_macos": True}}, 'T "q"', 'body \\ "x"')
        self.assertEqual(len(calls), 1)
        argv, kw = calls[0]
        self.assertEqual(argv[0], "/usr/bin/osascript")
        self.assertIn('display notification "body \\\\ \\"x\\""', argv[2])
        self.assertIn('with title "T \\"q\\""', argv[2])
        self.assertEqual(kw.get("timeout"), 5)

    def test_disabled_or_non_darwin_does_nothing(self):
        calls = []
        hook.shutil.which = lambda name: "/usr/bin/osascript"
        hook.subprocess.run = lambda argv, **kw: calls.append(argv)
        hook.sys.platform = "linux"
        hook._notify_block({"guard": {"block_notify_macos": True}}, "t", "b")
        hook.sys.platform = "darwin"
        hook._notify_block({"guard": {"block_notify_macos": False}}, "t", "b")
        self.assertEqual(calls, [])

    def test_subprocess_failure_is_swallowed(self):
        hook.sys.platform = "darwin"
        hook.shutil.which = lambda name: "/usr/bin/osascript"

        def boom(argv, **kw):
            raise OSError("no gui")

        hook.subprocess.run = boom
        hook._notify_block({"guard": {"block_notify_macos": True}}, "t", "b")  # must not raise


if __name__ == "__main__":
    unittest.main()
