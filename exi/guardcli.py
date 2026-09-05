"""`codex-guard` command line: hook / managed-run / status / install-hooks / check.

Thin dispatcher over hook.py, managed_run.py and hookmerge.py.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import config, feedback_hook, guard, hook, hookmerge, managed_run
from .quota import read_codex_quota_cached as read_codex_quota


def cmd_hook(args) -> int:
    return hook.handle(args.event)


def cmd_feedback_hook(args) -> int:
    return feedback_hook.handle(args.event)


def cmd_managed_run(args) -> int:
    if args.status:
        return managed_run.status()
    if not args.cmd:
        print("usage: codex-guard managed-run [--dry-run] -- <command...>", file=sys.stderr)
        return 2
    return managed_run.run(args.cmd, dry_run=args.dry_run)


def cmd_status(args) -> int:
    cfg = config.load_config()
    now = time.time()
    try:
        state = guard.load_state()
    except guard.StateCorruptError as e:
        report = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"status: ERROR — guard state is corrupt: {e}")
        return 1
    q = read_codex_quota(cfg)
    guard.record_sample(state, now, q.weekly_used, cfg["guard"].get("sample_retention_hours", 48))
    turns = state.get("turns", {})
    # Show the most recently started turn across all sessions (best-effort summary).
    tkey = max(turns, key=lambda k: turns[k].get("started_at") or -1) if turns else ""
    ctx = guard.compute_context(state, tkey, now, cfg)
    raw_findings = guard.evaluate(cfg, ctx)
    ack = guard.ack_status(state, now, cfg)
    findings, suppressed = guard.apply_ack(raw_findings, ack)
    report = {
        "quota": {"weekly_used": q.weekly_used, "ok": q.ok, "reason": q.reason, "mode": q.mode, "resets_at": q.resets_at},
        "context": ctx,
        "findings": findings,
        "suppressed_by_ack": suppressed,
        "ack": ack,
        "level": guard.worst_level(findings),
        "thresholds": cfg["guard"],
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"quota: weekly_used={q.weekly_used} ok={q.ok} mode={q.mode} reason={q.reason or '-'}")
    print(f"turn: elapsed_min={ctx['elapsed_minutes']} tools={ctx['tool_count']} "
          f"max_repeat={ctx['max_fingerprint']} turn_pct={ctx['turn_pct']} h24_pct={ctx['h24_pct']}")
    print(f"ack: {ack['reason']}")
    print(f"level: {guard.worst_level(findings) or 'ok'}")
    for f in findings:
        print(f"  [{f['level']}] {f['code']}: {f['message']}")
    for f in suppressed:
        print(f"  [suppressed by ack] {f['code']}: {f['message']}")
    return 0


def cmd_install_hooks(args) -> int:
    res = hookmerge.install(path=args.path, dry_run=args.dry_run)
    if args.dry_run:
        print("DRY RUN — would write:")
        print(json.dumps(res["result"], ensure_ascii=False, indent=2))
        # confirm Stop preserved
        stop = res["existing"].get("hooks", {}).get("Stop")
        print(f"\nexisting Stop hook preserved: {stop is not None}")
        return 0
    print(f"installed guard hooks into {res['path']} (backup at {res['path']}.bak)")
    print("NOTE: Codex requires you to TRUST/approve these hooks before they run.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codex-guard", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    h = sub.add_parser("hook", help="run a hook event (reads payload on stdin)")
    h.add_argument("event", choices=["UserPromptSubmit", "PreToolUse", "PreCompact"])
    h.set_defaults(func=cmd_hook)

    fh = sub.add_parser("feedback-hook", help="run a feedback-enforcement hook event (reads payload on stdin)")
    fh.add_argument("event", choices=["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"])
    fh.set_defaults(func=cmd_feedback_hook)

    m = sub.add_parser("managed-run", help="launch+supervise a process group we own")
    m.add_argument("--dry-run", action="store_true", help="monitor only; log kills instead of sending them")
    m.add_argument("--status", action="store_true", help="list owned managed runs and exit")
    m.add_argument("cmd", nargs=argparse.REMAINDER, help="-- <command...>")
    m.set_defaults(func=cmd_managed_run)

    s = sub.add_parser("status", help="show current guard state + quota + findings")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    i = sub.add_parser("install-hooks", help="merge guard hooks into .codex/hooks.json (preserves existing)")
    i.add_argument("--path", help="path to hooks.json (default: ~/.codex/hooks.json)")
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(func=cmd_install_hooks)

    return p


def _strip_leading_dashdash(cmd: list) -> list:
    # argparse.REMAINDER keeps the leading '--'; drop it.
    if cmd and cmd[0] == "--":
        return cmd[1:]
    return cmd


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    if getattr(args, "cmd", None) is not None and isinstance(args.cmd, list):
        args.cmd = _strip_leading_dashdash(args.cmd)
    try:
        return args.func(args)
    except PermissionError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
