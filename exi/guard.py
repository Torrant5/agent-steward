"""Budget-guard core: turn/tool/fingerprint tracking + weekly-quota increments.

Split into (a) pure evaluation (`evaluate`) that takes a plain context dict so it
is trivially unit-testable without a clock or the quota CLI, and (b) stateful
helpers that persist per-turn counters and a rolling window of weekly-usage
samples to `guard-state.json`.

Weekly consumption is measured as the sum of *positive* deltas between usage
samples. That is reset-safe: when the weekly pool resets, used% drops, the delta
is negative and simply ignored — a reset never looks like consumption, and
post-reset consumption is still counted. See `weekly_increment`.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

from . import config
from .locking import file_lock

# finding levels
WARN = "warn"
HARD = "hard"

# key used to migrate the pre-multi-turn state shape {"turn": ..., "samples": ...}
LEGACY_TURN_KEY = "legacy"


class StateCorruptError(Exception):
    """Raised when guard-state.json exists but cannot be parsed. Never silently reset."""


# ---- fingerprinting --------------------------------------------------------
def fingerprint(tool_name: str, tool_input) -> str:
    try:
        payload = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        payload = str(tool_input)
    return hashlib.sha1(f"{tool_name}\x1f{payload}".encode("utf-8")).hexdigest()[:16]


def turn_key(payload: dict) -> str:
    """Derive a per-session, per-turn state key from a hook payload."""
    session_id = payload.get("session_id") or payload.get("sessionId") or "unknown-session"
    turn_id = (
        payload.get("turn_id")
        or payload.get("turnId")
        or payload.get("prompt_id")
        or payload.get("promptId")
        or "unknown-turn"
    )
    return f"{session_id}\x1f{turn_id}"


# ---- state I/O -------------------------------------------------------------
def state_path() -> Path:
    return config.data_dir() / "guard-state.json"


def _fresh_state() -> dict:
    return {"turns": {}, "samples": []}


def _migrate_legacy(state: dict) -> dict:
    """Move the old single-turn shape {"turn": {...}|None, "samples": [...]} to "turns"."""
    if "turns" not in state and "turn" in state:
        legacy_turn = state.pop("turn")
        state["turns"] = {LEGACY_TURN_KEY: legacy_turn} if legacy_turn is not None else {}
    state.setdefault("turns", {})
    state.setdefault("samples", [])
    return state


def load_state() -> dict:
    p = state_path()
    if not p.exists():
        return _fresh_state()
    try:
        with open(p, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        raise StateCorruptError(f"cannot read guard state {p}: {e}") from e
    if not raw.strip():
        return _fresh_state()
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as e:
        raise StateCorruptError(f"corrupt guard state {p}: {e}") from e
    if not isinstance(state, dict):
        raise StateCorruptError(f"corrupt guard state {p}: expected a JSON object")
    return _migrate_legacy(state)


def save_state(state: dict) -> None:
    p = state_path()
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    tmp.replace(p)


@contextmanager
def locked_state():
    """Exclusive load-mutate-save transaction over guard-state.json.

    Holds an flock for the whole critical section so concurrent hook
    invocations (separate processes) can't interleave a load/save and lose
    each other's updates.
    """
    lock_path = state_path().with_suffix(".lock")
    with file_lock(lock_path):
        state = load_state()
        yield state
        save_state(state)


def _prune_samples(state: dict, now: float, retention_hours: float) -> None:
    cutoff = now - retention_hours * 3600.0
    state["samples"] = [s for s in state.get("samples", []) if s.get("ts", 0) >= cutoff]


def record_sample(state: dict, now: float, weekly_used, retention_hours: float) -> None:
    if weekly_used is not None:
        state.setdefault("samples", []).append({"ts": now, "used": float(weekly_used)})
    _prune_samples(state, now, retention_hours)


def start_turn(state: dict, now: float, weekly_used, turn_key: str) -> None:
    state.setdefault("turns", {})[turn_key] = {
        "id": turn_key,
        "started_at": now,
        "tool_count": 0,
        "fingerprints": {},
        "start_used": (None if weekly_used is None else float(weekly_used)),
    }


def record_tool(state: dict, turn_key: str, tool_name: str, tool_input) -> int:
    """Register a tool call on the given turn; return this fingerprint's count."""
    turns = state.setdefault("turns", {})
    turn = turns.get(turn_key)
    if turn is None:
        # No UserPromptSubmit seen (e.g. hook trust just granted mid-turn).
        # Synthesize a turn so counters still work.
        turn = {"id": turn_key, "started_at": None, "tool_count": 0, "fingerprints": {}, "start_used": None}
        turns[turn_key] = turn
    turn["tool_count"] = turn.get("tool_count", 0) + 1
    fp = fingerprint(tool_name, tool_input)
    fps = turn.setdefault("fingerprints", {})
    fps[fp] = fps.get(fp, 0) + 1
    return fps[fp]


# ---- weekly increment math -------------------------------------------------
def weekly_increment(samples: list, since_ts: float, now: float):
    """Sum of positive deltas among usage samples in [since_ts, now].

    Returns (increment_or_None, n_samples). None means "not enough weekly data"
    (0 or 1 sample) — the caller must then skip the quota check, not block.
    """
    xs = sorted(
        (s for s in samples if s.get("used") is not None and since_ts <= s.get("ts", -1) <= now),
        key=lambda s: s["ts"],
    )
    if len(xs) < 2:
        return None, len(xs)
    inc = 0.0
    for a, b in zip(xs, xs[1:]):
        d = b["used"] - a["used"]
        if d > 0:
            inc += d
    return inc, len(xs)


# ---- context assembly ------------------------------------------------------
def compute_context(state: dict, turn_key: str, now: float, cfg: dict) -> dict:
    turn = state.get("turns", {}).get(turn_key) or {}
    started_at = turn.get("started_at")
    elapsed_min = None
    if started_at is not None:
        elapsed_min = max(0.0, (now - started_at) / 60.0)

    samples = state.get("samples", [])
    # per-turn increment: only samples taken during the current turn
    if started_at is not None:
        turn_inc, _ = weekly_increment(samples, started_at, now)
    else:
        turn_inc = None
    # rolling 24h increment
    h24_inc, _ = weekly_increment(samples, now - 24 * 3600.0, now)

    fps = turn.get("fingerprints", {})
    max_fp = max(fps.values()) if fps else 0

    return {
        "elapsed_minutes": elapsed_min,
        "tool_count": turn.get("tool_count", 0),
        "max_fingerprint": max_fp,
        "turn_pct": turn_inc,
        "h24_pct": h24_inc,
    }


# ---- user acknowledgement of a budget stop ---------------------------------
# A HARD `weekly_24h` stop is meant to hand control back to the human. But the
# UserPromptSubmit stop also swallows the human's *answer*, and the Codex
# desktop app shows neither stopReason nor systemMessage, so without an escape
# hatch the guard is a silent dead end until old samples age out of the 24h
# window. The escape hatch: a prompt that *starts with* one of
# `guard.ack_phrases` grants a bounded allowance — `ack_grant_pct` more weekly
# usage (measured as positive deltas since the ack, reset-safe like everything
# else) or `ack_grant_hours`, whichever runs out first. Only the `weekly_24h`
# finding is suppressed; time / tool-count / repeat / per-turn guards are
# untouched. When the allowance is spent the guard stops again and asks for a
# fresh ack.
ACK_CODE = "weekly_24h"


def _normalize_ack_text(s: str) -> str:
    # casefold + collapse whitespace; also fold full-width ASCII so "ＯＫ" == "ok"
    s = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in s)
    return " ".join(s.casefold().split())


def match_ack(prompt: str, phrases) -> str | None:
    """Return the matched ack phrase when `prompt` starts with one, else None."""
    if not prompt or not phrases:
        return None
    text = _normalize_ack_text(prompt)
    for phrase in phrases:
        if not isinstance(phrase, str) or not phrase.strip():
            continue
        p = _normalize_ack_text(phrase)
        # plain prefix match: Japanese has no word boundary to key on
        # ("予算OK続けて"), so "guard ok" also accepts "guard ok, go on".
        if p and text.startswith(p):
            return phrase
    return None


def grant_ack(state: dict, now: float, weekly_used, phrase: str) -> dict:
    ack = {
        "ts": now,
        "phrase": phrase,
        "used_at_ack": (None if weekly_used is None else float(weekly_used)),
    }
    state["ack"] = ack
    return ack


def ack_status(state: dict, now: float, cfg: dict) -> dict:
    """Describe the current ack: {"active": bool, "reason": str, ...details}."""
    g = cfg["guard"]
    ack = state.get("ack")
    if not isinstance(ack, dict) or ack.get("ts") is None:
        return {"active": False, "reason": "no ack on record"}
    grant_pct = float(g.get("ack_grant_pct", 0.0) or 0.0)
    grant_hours = float(g.get("ack_grant_hours", 0.0) or 0.0)
    elapsed_h = max(0.0, (now - float(ack["ts"])) / 3600.0)
    inc, _ = weekly_increment(state.get("samples", []), float(ack["ts"]), now)
    used_since = 0.0 if inc is None else inc
    details = {
        "ack_ts": ack["ts"],
        "elapsed_hours": elapsed_h,
        "used_since_ack_pct": used_since,
        "grant_pct": grant_pct,
        "grant_hours": grant_hours,
    }
    if grant_hours > 0 and elapsed_h >= grant_hours:
        return {"active": False, "reason": f"ack expired: {elapsed_h:.1f}h elapsed >= {grant_hours:g}h", **details}
    if grant_pct > 0 and used_since >= grant_pct:
        return {"active": False, "reason": f"ack spent: +{used_since:.1f}% weekly since ack >= {grant_pct:g}%", **details}
    remaining_pct = None if grant_pct <= 0 else max(0.0, grant_pct - used_since)
    remaining_h = None if grant_hours <= 0 else max(0.0, grant_hours - elapsed_h)
    parts = []
    if remaining_pct is not None:
        parts.append(f"+{remaining_pct:.1f}% weekly")
    if remaining_h is not None:
        parts.append(f"{remaining_h:.1f}h")
    return {"active": True, "reason": "ack active; remaining " + (" / ".join(parts) or "unbounded"), **details}


def apply_ack(findings: list, ack: dict):
    """Drop `weekly_24h` findings while an ack is active. Returns (kept, suppressed)."""
    if not ack.get("active"):
        return findings, []
    kept = [f for f in findings if f["code"] != ACK_CODE]
    suppressed = [f for f in findings if f["code"] == ACK_CODE]
    return kept, suppressed


def ack_hint(cfg: dict) -> str:
    g = cfg["guard"]
    phrases = [p for p in g.get("ack_phrases", []) if isinstance(p, str) and p.strip()]
    if not phrases:
        return ""
    quoted = " or ".join(f'"{p}"' for p in phrases)
    return (
        f"To resume, send a prompt that starts with {quoted} "
        f"(grants +{float(g.get('ack_grant_pct', 0)):g}% weekly usage or "
        f"{float(g.get('ack_grant_hours', 0)):g}h, whichever ends first)."
    )


# ---- pure evaluation -------------------------------------------------------
def evaluate(cfg: dict, ctx: dict) -> list:
    """Return a list of findings: {level, code, message}. HARD => should deny."""
    g = cfg["guard"]
    findings = []

    elapsed = ctx.get("elapsed_minutes")
    if elapsed is not None:
        if elapsed >= g["turn_hard_minutes"]:
            findings.append(_f(HARD, "turn_time", f"turn running {elapsed:.0f} min >= hard {g['turn_hard_minutes']} min"))
        elif elapsed >= g["turn_soft_minutes"]:
            findings.append(_f(WARN, "turn_time", f"turn running {elapsed:.0f} min >= soft {g['turn_soft_minutes']} min"))

    tc = ctx.get("tool_count", 0)
    if tc > g["tool_hard_count"]:
        findings.append(_f(HARD, "tool_count", f"{tc} tool calls this turn > hard {g['tool_hard_count']}"))

    fp = ctx.get("max_fingerprint", 0)
    if fp >= g["fingerprint_repeat_max"]:
        findings.append(_f(HARD, "repeat", f"identical tool call repeated {fp}x >= {g['fingerprint_repeat_max']} (loop suspected)"))

    tp = ctx.get("turn_pct")
    if tp is not None:
        if tp >= g["weekly_turn_hard_pct"]:
            findings.append(_f(HARD, "weekly_turn", f"weekly Codex usage +{tp:.1f}% this turn >= hard {g['weekly_turn_hard_pct']}%"))
        elif tp >= g["weekly_turn_soft_pct"]:
            findings.append(_f(WARN, "weekly_turn", f"weekly Codex usage +{tp:.1f}% this turn >= soft {g['weekly_turn_soft_pct']}%"))

    h = ctx.get("h24_pct")
    if h is not None:
        if h >= g["weekly_24h_hard_pct"]:
            findings.append(_f(HARD, "weekly_24h", f"weekly Codex usage +{h:.1f}% in last 24h >= hard {g['weekly_24h_hard_pct']}%"))
        elif h >= g["weekly_24h_soft_pct"]:
            findings.append(_f(WARN, "weekly_24h", f"weekly Codex usage +{h:.1f}% in last 24h >= soft {g['weekly_24h_soft_pct']}%"))

    return findings


def _f(level, code, message):
    return {"level": level, "code": code, "message": message}


def worst_level(findings: list) -> str | None:
    if any(f["level"] == HARD for f in findings):
        return HARD
    if any(f["level"] == WARN for f in findings):
        return WARN
    return None
