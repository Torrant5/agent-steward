"""Codex hook entrypoints: UserPromptSubmit / PreToolUse / PreCompact.

Each reads the hook's JSON payload on stdin, updates guard state, consults the
weekly Codex quota (best-effort), evaluates thresholds, and:

* HARD breach on PreToolUse   -> hookSpecificOutput permissionDecision=deny.
* HARD breach on PreCompact / UserPromptSubmit -> official {continue:false,
  stopReason, systemMessage} stop shape (never permissionDecision there).
* SOFT breach -> emit a warning on stderr (non-blocking).
* otherwise    -> allow silently.

Turn state is keyed by (session_id, turn_id) from the hook payload, so
concurrent sessions never share turn counters; usage samples stay global.
State reads/writes go through `guard.locked_state()`, an flock'd
load-mutate-save transaction, so concurrent hook processes can't lose
updates. A corrupt state file is never silently reset: it raises and every
event fails closed (deny / stop).

Quota is read via the cached reader (`quota.read_codex_quota_cached`, TTL =
`quota.cache_seconds`, default 30s), so a tool-call-heavy turn does not spawn
`llm-quota` on every single `PreToolUse`.

Quota `unknown` never blocks on its own: time / tool-count / repeat guards stay
active regardless. Nothing from the conversation body or any secret is stored —
only counters, fingerprints (hashed), and weekly usage percentages.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time

from . import config, feedback_detect, guard
from .quota import read_codex_quota_cached as read_codex_quota

STOP_INSTRUCTION = (
    "STOP now. Do not call more tools. Report current state to the user: what "
    "you were doing, why the budget guard tripped, and what remains. Wait for "
    "the user before resuming."
)


def _read_payload() -> dict:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _extract_tool(payload: dict):
    name = payload.get("tool_name") or payload.get("toolName") or payload.get("name") or ""
    tinput = (
        payload.get("tool_input")
        if "tool_input" in payload
        else payload.get("toolInput", payload.get("input", payload.get("arguments", {})))
    )
    return name, tinput


def _deny(reason: str) -> None:
    """Emit a PreToolUse deny decision (stdout) + visible reason (stderr)."""
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(out, ensure_ascii=False))
    print(f"[codex-guard BLOCK] {reason}", file=sys.stderr)


def _stop(reason: str) -> None:
    """Emit the official hard-stop shape (PreCompact / UserPromptSubmit). Never permissionDecision."""
    out = {"continue": False, "stopReason": reason, "systemMessage": reason}
    print(json.dumps(out, ensure_ascii=False))
    print(f"[codex-guard BLOCK] {reason}", file=sys.stderr)


def _warn(reason: str) -> None:
    print(f"[codex-guard WARN] {reason}", file=sys.stderr)


def _context(event: str, text: str) -> None:
    """Allow the event and hand the agent a short note (official additionalContext shape)."""
    out = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    print(json.dumps(out, ensure_ascii=False))
    print(f"[codex-guard ACK] {text}", file=sys.stderr)


def _reason_text(findings: list) -> str:
    msgs = "; ".join(f"{f['code']}: {f['message']}" for f in findings)
    return f"Codex budget guard tripped ({msgs}). {STOP_INSTRUCTION}"


# ---- block visibility -------------------------------------------------------
# A UserPromptSubmit / PreCompact stop is invisible in the Codex desktop app
# (neither stopReason nor systemMessage is rendered), so every block is also
# (a) appended to <data_dir>/blocks.log as one JSON line — codes, reason,
# session/turn ids, never the prompt — and (b) on macOS surfaced as a
# notification via osascript. Both are best-effort and can never raise or
# block; failures are swallowed so the hook's own decision is unaffected.
def _log_block(cfg: dict, event: str, kind: str, findings: list, reason: str, payload: dict) -> None:
    if not cfg["guard"].get("block_log", True):
        return
    try:
        rec = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": event,
            "kind": kind,  # "stop" | "deny"
            "codes": [f["code"] for f in findings],
            "reason": reason,
            "session_id": payload.get("session_id") or payload.get("sessionId"),
            "turn_id": payload.get("turn_id") or payload.get("turnId"),
        }
        p = config.data_dir() / "blocks.log"
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — logging must never affect the decision
        pass


def _osascript_quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _notify_block(cfg: dict, title: str, text: str) -> None:
    if not cfg["guard"].get("block_notify_macos", True):
        return
    if sys.platform != "darwin":
        return
    osa = shutil.which("osascript")
    if not osa:
        return
    body = text if len(text) <= 240 else text[:237] + "..."
    script = (
        f'display notification "{_osascript_quote(body)}" '
        f'with title "{_osascript_quote(title)}" sound name "Basso"'
    )
    try:
        subprocess.run([osa, "-e", script], timeout=5, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001 — notification must never affect the decision
        pass


def _stop_visible(cfg: dict, event: str, findings: list, reason: str, payload: dict) -> None:
    _stop(reason)
    _log_block(cfg, event, "stop", findings, reason, payload)
    codes = ", ".join(f["code"] for f in findings) or "guard"
    short = "; ".join(f["message"] for f in findings) or reason
    hint = guard.ack_hint(cfg)
    _notify_block(cfg, f"Codex budget guard stopped {event} ({codes})", f"{short} {hint}".strip())


def handle(event: str, argv=None) -> int:
    cfg = config.load_config()
    g = cfg["guard"]
    now = time.time()
    payload = _read_payload()
    retention = g.get("sample_retention_hours", 48)
    tkey = guard.turn_key(payload)

    q = read_codex_quota(cfg)  # best-effort; q.weekly_used is None when unknown

    if event not in ("UserPromptSubmit", "PreToolUse", "PreCompact"):
        return 0  # unknown event: do nothing, never block.

    ack_phrase = None
    if event == "UserPromptSubmit":
        ack_phrase = guard.match_ack(feedback_detect.extract_prompt(payload), g.get("ack_phrases", []))

    try:
        with guard.locked_state() as state:
            if event == "UserPromptSubmit":
                guard.start_turn(state, now, q.weekly_used, tkey)
                guard.record_sample(state, now, q.weekly_used, retention)
                if ack_phrase is not None:
                    guard.grant_ack(state, now, q.weekly_used, ack_phrase)
                # A fresh turn allows freely; surface only a pre-existing 24h HARD state.
                ctx = guard.compute_context(state, tkey, now, cfg)
                ctx["elapsed_minutes"] = None  # brand-new turn: ignore time here
                ctx["tool_count"] = 0
                ctx["max_fingerprint"] = 0
                ctx["turn_pct"] = None
                findings = guard.evaluate(cfg, ctx)  # only h24 can fire
            elif event == "PreToolUse":
                name, tinput = _extract_tool(payload)
                guard.record_tool(state, tkey, name, tinput)
                guard.record_sample(state, now, q.weekly_used, retention)
                ctx = guard.compute_context(state, tkey, now, cfg)
                findings = guard.evaluate(cfg, ctx)
            else:  # PreCompact
                guard.record_sample(state, now, q.weekly_used, retention)
                ctx = guard.compute_context(state, tkey, now, cfg)
                findings = guard.evaluate(cfg, ctx)
            # A user acknowledgement suppresses only the weekly_24h finding, and
            # only while its bounded allowance (pct / hours) lasts.
            ack = guard.ack_status(state, now, cfg)
            findings, suppressed = guard.apply_ack(findings, ack)
    except guard.StateCorruptError as e:
        reason = f"guard state is corrupt and cannot be trusted ({e}); failing closed. {STOP_INSTRUCTION}"
        if event == "PreToolUse":
            _deny(reason)
        else:
            _stop(reason)
        return 0

    level = guard.worst_level(findings)

    if event == "UserPromptSubmit":
        if level == guard.HARD:
            reason = _reason_text(findings) + f" [quota: {q.reason or q.mode or 'ok'}]"
            if any(f["code"] == guard.ACK_CODE for f in findings):
                if ack_phrase is None and ack.get("ack_ts") is not None:
                    reason += f" [previous ack: {ack['reason']}]"
                hint = guard.ack_hint(cfg)
                if hint:
                    reason += " " + hint
            _stop_visible(cfg, event, findings, reason, payload)
            return 0
        if ack_phrase is not None:
            note = (
                f'The prompt starts with the budget-guard acknowledgement "{ack_phrase}"; '
                f"treat that prefix as a control word, not as part of the task. "
                f"The user accepted the weekly budget stop; {ack['reason']}. "
                f"Continue the task, but stay frugal with tool calls."
            )
            _context(event, note)
        elif suppressed:
            _context(event, f"Budget guard weekly_24h stop is suppressed by a user acknowledgement ({ack['reason']}). Stay frugal.")
        return 0

    if event == "PreToolUse":
        if level == guard.HARD:
            suffix = "" if q.ok else f" [quota unknown: {q.reason}; time/count/repeat guards still enforced]"
            reason = _reason_text(findings) + suffix
            _deny(reason)
            _log_block(cfg, event, "deny", findings, reason, payload)
            return 0
        if level == guard.WARN:
            _warn("; ".join(f["message"] for f in findings))
        return 0

    # PreCompact
    if level == guard.HARD:
        # Best-effort hard stop at the compaction boundary.
        _stop_visible(cfg, event, findings, _reason_text(findings), payload)
    return 0
