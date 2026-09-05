"""Thin wrapper over the local `llm-quota` CLI for Codex weekly usage.

Reads ONLY: `llm-quota --json --providers codex`. Never switches to a metered
API, never guesses. Returns a structured result whose `weekly_used` is None when
usage is unknown (CLI failure, provider not ok, weekly window absent) so callers
can keep time/count guards active without blocking on quota. No silent fallback:
the reason for `unknown` is always reported.

`read_codex_quota_cached` wraps the above with a short-lived, file-backed,
flock-serialized cache (`quota.cache_seconds` in config, default 30s) so a
PreToolUse-heavy turn doesn't spawn `llm-quota` on every single tool call. A
cache hit re-reports the exact same `QuotaResult` it last fetched — `ok` or
`unknown` alike, `reason` included — it never fabricates a value. Because
repeated identical `weekly_used` samples contribute a zero delta to
`guard.weekly_increment`, caching can never manufacture a false weekly-reset
or false consumption; it only reduces how often the real number is refreshed.
"""
from __future__ import annotations

import json
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config
from .locking import file_lock


@dataclass
class QuotaResult:
    weekly_used: float | None  # percent used of the weekly Codex pool, or None
    resets_at: str | None
    mode: str | None           # normal / conserve / critical, per llm-quota
    ok: bool                   # True when weekly_used is a real number
    reason: str                # why unknown (empty when ok)
    raw: dict | None = None


def read_codex_quota(cfg: dict) -> QuotaResult:
    qcfg = cfg.get("quota", {})
    cmd = qcfg.get("cmd", ["llm-quota", "--json", "--providers", "codex"])
    timeout = qcfg.get("timeout_seconds", 15)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError:
        return QuotaResult(None, None, None, False, f"llm-quota not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return QuotaResult(None, None, None, False, f"llm-quota timed out after {timeout}s")
    except Exception as e:  # pragma: no cover - defensive
        return QuotaResult(None, None, None, False, f"llm-quota error: {e}")

    exit_reason = f"llm-quota exit {proc.returncode}: {proc.stderr.strip()[:200]}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        if proc.returncode != 0:
            return QuotaResult(None, None, None, False, exit_reason)
        return QuotaResult(None, None, None, False, f"llm-quota bad JSON: {e}")
    if proc.returncode != 0:
        # llm-quota exits non-zero when the provider is not ok but still prints
        # its JSON report; prefer the provider's own error text (e.g. "sample is
        # stale") over a bare exit code. Never trust an "ok" result on a failed run.
        parsed = parse_quota(data) if isinstance(data, dict) else None
        if parsed is not None and not parsed.ok and parsed.reason:
            return parsed
        return QuotaResult(None, None, None, False, exit_reason)
    return parse_quota(data)


def parse_quota(data: dict) -> QuotaResult:
    """Pure parser (unit-testable) over llm-quota's JSON structure."""
    if not isinstance(data, dict):
        return QuotaResult(None, None, None, False, "llm-quota JSON root is not an object")
    codex = (data.get("providers") or {}).get("codex")
    if not codex:
        return QuotaResult(None, None, None, False, "no codex provider in llm-quota output", data)
    if not codex.get("ok", False):
        return QuotaResult(None, None, codex.get("mode"), False,
                           codex.get("error") or "codex provider not ok (main pool unresolved)", data)
    windows = codex.get("windows") or {}
    weekly = windows.get("weekly")
    if not weekly or weekly.get("used_percent") is None:
        return QuotaResult(None, None, codex.get("mode"), False, "weekly window unavailable", data)
    return QuotaResult(
        weekly_used=float(weekly["used_percent"]),
        resets_at=weekly.get("resets_at"),
        mode=codex.get("mode"),
        ok=True,
        reason="",
        raw=data,
    )


# ---- short-lived cache over read_codex_quota --------------------------------
def _cache_path() -> Path:
    return config.data_dir() / "quota-cache.json"


@contextmanager
def _locked_cache():
    """Exclusive transaction over the quota cache file.

    A dedicated lock (separate from guard-state.json's) so quota caching
    never contends with, or depends on, guard's turn/sample bookkeeping. Held
    across the underlying `llm-quota` call on a cache miss too, so concurrent
    hook processes racing a cache expiry collapse into a single subprocess
    spawn instead of a thundering herd.
    """
    lock_path = _cache_path().with_suffix(".lock")
    with file_lock(lock_path):
        yield


def _load_cache_entry() -> dict | None:
    p = _cache_path()
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            entry = json.load(f)
            if not isinstance(entry, dict):
                return None
            cached_at = entry.get("cached_at")
            result = entry.get("result")
            if not isinstance(cached_at, (int, float)) or not isinstance(result, dict):
                return None
            required = {"weekly_used", "resets_at", "mode", "ok", "reason"}
            if not required.issubset(result):
                return None
            return entry
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache_entry(entry: dict) -> None:
    p = _cache_path()
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False)
    tmp.replace(p)


def read_codex_quota_cached(cfg: dict, now: float | None = None) -> QuotaResult:
    """Cached front door for `read_codex_quota`.

    Reuses the most recently fetched `QuotaResult` for `quota.cache_seconds`
    seconds (default 30; 0 or absent disables caching) instead of
    re-invoking `llm-quota` on every call. `now` is injectable for tests.
    """
    qcfg = cfg.get("quota", {})
    ttl = qcfg.get("cache_seconds", 30)
    now = time.time() if now is None else now
    if not ttl or ttl <= 0:
        return read_codex_quota(cfg)
    with _locked_cache():
        entry = _load_cache_entry()
        age = None if entry is None else now - entry["cached_at"]
        if entry is not None and age is not None and 0 <= age < ttl:
            try:
                return QuotaResult(**entry["result"])
            except TypeError:
                # Structurally stale cache from an older/newer schema: refetch.
                pass
        result = read_codex_quota(cfg)
        _save_cache_entry({"cached_at": now, "result": asdict(result)})
        return result
