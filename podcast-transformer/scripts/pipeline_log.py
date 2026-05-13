#!/usr/bin/env python3
"""Step-level instrumentation for the /podcastextract pipeline.

Each step in the pipeline brackets itself with `start` and `end` calls. The
log lives at `<episode_dir>/working/_pipeline_metrics.jsonl` (one JSON object
per line) so failures don't lose the prior step record. The harness reads
the file at the end of a run and computes total wall-clock plus the
script-vs-LLM split.

Usage:

    python3 scripts/pipeline_log.py start <step-name> --kind script|llm \
        [--episode-dir DIR] [--note "free text"]
    python3 scripts/pipeline_log.py end   <step-name> [--episode-dir DIR] \
        [--status ok|fail] [--note "..."]
    python3 scripts/pipeline_log.py summary <episode_dir>
    python3 scripts/pipeline_log.py reset <episode_dir>

A "script" step is anything driven by a single CLI invocation, even when
that CLI internally calls an LLM (e.g. `draft_notes.py` calls Claude). An
"llm" step is something the main agent loop has to think about / type — a
correction pass, a hand-edit, a per-entity decision.

Steps can nest; we keep it simple and require unique step names. The end
call finds the matching start by name (last unclosed one wins).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

METRICS_NAME = "_pipeline_metrics.jsonl"


def _metrics_path(episode_dir: Path) -> Path:
    working = episode_dir / "working"
    working.mkdir(parents=True, exist_ok=True)
    return working / METRICS_NAME


def _append(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def cmd_start(args: argparse.Namespace) -> int:
    ep = Path(args.episode_dir).resolve()
    path = _metrics_path(ep)
    _append(path, {
        "event": "start",
        "step": args.step,
        "kind": args.kind,
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "note": args.note or "",
    })
    print(f"[pipeline_log] start {args.kind}: {args.step}", file=sys.stderr)
    return 0


def cmd_end(args: argparse.Namespace) -> int:
    ep = Path(args.episode_dir).resolve()
    path = _metrics_path(ep)
    _append(path, {
        "event": "end",
        "step": args.step,
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "status": args.status,
        "note": args.note or "",
    })
    print(f"[pipeline_log] end   {args.status}: {args.step}", file=sys.stderr)
    return 0


def pair_records(records: list[dict]) -> list[dict]:
    """Pair up start/end records by step name (last-open wins). Returns a list
    of dicts: {step, kind, start_ts, end_ts, duration_seconds, status, note}.

    Records with no matching end count as duration 0 and status `unclosed`."""
    open_starts: dict[str, list[dict]] = {}
    pairs: list[dict] = []
    for rec in records:
        if rec.get("event") == "start":
            open_starts.setdefault(rec["step"], []).append(rec)
        elif rec.get("event") == "end":
            stack = open_starts.get(rec["step"]) or []
            if stack:
                start = stack.pop()
                pairs.append({
                    "step": rec["step"],
                    "kind": start.get("kind", "unknown"),
                    "start_ts": start["ts"],
                    "end_ts": rec["ts"],
                    "duration_seconds": rec["ts"] - start["ts"],
                    "status": rec.get("status", "ok"),
                    "start_note": start.get("note", ""),
                    "end_note": rec.get("note", ""),
                })
    # Anything left in open_starts is an unclosed step.
    for step, stack in open_starts.items():
        for start in stack:
            pairs.append({
                "step": step,
                "kind": start.get("kind", "unknown"),
                "start_ts": start["ts"],
                "end_ts": None,
                "duration_seconds": 0.0,
                "status": "unclosed",
                "start_note": start.get("note", ""),
                "end_note": "",
            })
    pairs.sort(key=lambda p: p["start_ts"])
    return pairs


def summarize(pairs: list[dict]) -> dict:
    if not pairs:
        return {
            "step_count": 0,
            "total_seconds": 0.0,
            "script_seconds": 0.0,
            "llm_seconds": 0.0,
            "automation_pct": 0.0,
            "first_start": None,
            "last_end": None,
            "steps": [],
        }
    script_s = sum(p["duration_seconds"] for p in pairs if p["kind"] == "script")
    llm_s = sum(p["duration_seconds"] for p in pairs if p["kind"] == "llm")
    starts = [p["start_ts"] for p in pairs if p["start_ts"]]
    ends = [p["end_ts"] for p in pairs if p["end_ts"]]
    first_start = min(starts) if starts else None
    last_end = max(ends) if ends else None
    wall = (last_end - first_start) if (first_start and last_end) else (script_s + llm_s)
    measured = script_s + llm_s
    pct = (script_s / measured * 100.0) if measured > 0 else 0.0
    return {
        "step_count": len(pairs),
        "total_seconds": wall,
        "measured_seconds": measured,
        "script_seconds": script_s,
        "llm_seconds": llm_s,
        "automation_pct": pct,
        "first_start": first_start,
        "last_end": last_end,
        "steps": pairs,
    }


def cmd_summary(args: argparse.Namespace) -> int:
    ep = Path(args.episode_dir).resolve()
    path = _metrics_path(ep)
    pairs = pair_records(_read(path))
    summary = summarize(pairs)
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return 0
    print(f"Steps:            {summary['step_count']}")
    print(f"Wall-clock:       {summary['total_seconds']:.1f}s")
    print(f"  script (any):   {summary['script_seconds']:.1f}s")
    print(f"  llm (agent):    {summary['llm_seconds']:.1f}s")
    print(f"Automation:       {summary['automation_pct']:.1f}%")
    print()
    print(f"{'#':>3}  {'kind':<6}  {'sec':>7}  step")
    for i, p in enumerate(pairs, 1):
        print(f"{i:>3}  {p['kind']:<6}  {p['duration_seconds']:>7.1f}  {p['step']}")
    unclosed = [p for p in pairs if p["status"] == "unclosed"]
    if unclosed:
        print(f"\nWARN: {len(unclosed)} unclosed step(s)", file=sys.stderr)
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    ep = Path(args.episode_dir).resolve()
    path = _metrics_path(ep)
    if path.exists():
        path.unlink()
        print(f"removed {path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--episode-dir", default=os.environ.get("PT_EPISODE_DIR", "."),
                        help="episode directory (default: $PT_EPISODE_DIR or cwd)")

    sp = sub.add_parser("start", parents=[common])
    sp.add_argument("step")
    sp.add_argument("--kind", choices=["script", "llm"], required=True)
    sp.add_argument("--note", default="")
    sp.set_defaults(fn=cmd_start)

    ep = sub.add_parser("end", parents=[common])
    ep.add_argument("step")
    ep.add_argument("--status", default="ok")
    ep.add_argument("--note", default="")
    ep.set_defaults(fn=cmd_end)

    su = sub.add_parser("summary")
    su.add_argument("episode_dir")
    su.add_argument("--json", action="store_true")
    su.set_defaults(fn=cmd_summary)

    rs = sub.add_parser("reset")
    rs.add_argument("episode_dir")
    rs.set_defaults(fn=cmd_reset)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
