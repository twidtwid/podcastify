#!/usr/bin/env python3
"""Test harness for the /podcastextract pipeline.

Each invocation:

  1. Snapshots the existing episode dir to `_harness/round-N/before/` (if any)
     so we never lose the reference output.
  2. Runs `extract_one.py` against the source file with a fresh episode dir.
  3. Runs `score_extraction.py` to produce the scorecard.
  4. Copies the produced episode dir to `_harness/round-N/after/`.
  5. If a previous round's scorecard exists, prints a delta comparison.

The harness lives at `podcast-output/_harness/`. Each round is a tagged dir:

  _harness/
    round-1/
      scorecard.json
      after/         (snapshot of the produced episode dir)
    round-2/
      scorecard.json
      after/
    rounds.jsonl     (one-line summary per round for trend tracking)

Usage:
  python3 scripts/extraction_harness.py <source_file> [--label round-N] \
                                         [--no-snapshot] [--reference DIR]

`--reference DIR` compares produced output against an external reference (e.g.,
the original hand-authored `lenny-ries-incorruptible` snapshot taken before
this work began).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "podcast-transformer" / "scripts"
HARNESS_ROOT = REPO_ROOT / "podcast-output" / "_harness"


def next_round_label() -> str:
    HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
    existing = sorted(int(p.name.split("-")[1]) for p in HARNESS_ROOT.glob("round-*") if p.name.split("-")[1].isdigit())
    n = (existing[-1] + 1) if existing else 1
    return f"round-{n}"


def derive_slug(source_file: Path) -> str:
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "parse_source.py"),
         str(source_file), "/tmp/_slug_probe", "--print-slug"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"slug derivation failed: {r.stderr}")
    return r.stdout.strip()


def previous_scorecard(exclude_label: str | None = None) -> tuple[Path, dict] | None:
    rounds = sorted(HARNESS_ROOT.glob("round-*"),
                    key=lambda p: int(p.name.split("-")[1]) if p.name.split("-")[1].isdigit() else -1)
    if exclude_label:
        rounds = [r for r in rounds if r.name != exclude_label]
    for r in reversed(rounds):
        sc = r / "scorecard.json"
        if sc.exists():
            return r, json.loads(sc.read_text(encoding="utf-8"))
    return None


def pct_delta(new: float, old: float) -> str:
    if old == 0:
        return "(n/a — prior round was 0)"
    delta = (new - old) / old * 100.0
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source_file", type=Path)
    p.add_argument("--label", default=None)
    p.add_argument("--no-snapshot", action="store_true",
                   help="don't copy the produced episode dir into the round folder")
    p.add_argument("--reference", type=Path,
                   help="reference dir to compare deliverables against")
    p.add_argument("--draft-model", default=None,
                   help="forwarded to extract_one.py: model alias for draft_notes")
    p.add_argument("--draft-backend", choices=["ollama", "cli", "api"], default=None,
                   help="forwarded to extract_one.py: ollama (default) / cli / api")
    args = p.parse_args(argv)

    if not args.source_file.is_file():
        print(f"ERROR: source file not found: {args.source_file}", file=sys.stderr)
        return 2

    HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
    label = args.label or next_round_label()
    round_dir = HARNESS_ROOT / label
    round_dir.mkdir(parents=True, exist_ok=True)

    slug = derive_slug(args.source_file)
    episode_dir = REPO_ROOT / "podcast-output" / slug

    prior = previous_scorecard(exclude_label=label)

    # Run extract_one.py (this wipes and rebuilds episode_dir)
    print(f"\n=== {label} ===", file=sys.stderr)
    print(f"Source: {args.source_file}", file=sys.stderr)
    print(f"Slug:   {slug}", file=sys.stderr)
    t0 = time.time()
    extract_cmd = [sys.executable, str(SCRIPTS / "extract_one.py"), str(args.source_file)]
    if args.draft_model:
        extract_cmd += ["--draft-model", args.draft_model]
    if args.draft_backend:
        extract_cmd += ["--draft-backend", args.draft_backend]
    extract_proc = subprocess.run(extract_cmd, capture_output=False)
    wall = time.time() - t0
    print(f"\nextract_one.py wall-clock: {wall:.1f}s (rc={extract_proc.returncode})", file=sys.stderr)

    # Score, even if extract failed (so we capture partial progress)
    scorecard_path = round_dir / "scorecard.json"
    score_cmd = [
        sys.executable, str(SCRIPTS / "score_extraction.py"),
        str(episode_dir),
        "--label", label,
        "--scorecard-out", str(scorecard_path),
    ]
    if args.reference:
        score_cmd += ["--reference-dir", str(args.reference)]
    score_proc = subprocess.run(score_cmd)

    if not scorecard_path.exists():
        print(f"ERROR: scorecard missing at {scorecard_path}", file=sys.stderr)
        return 2

    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))

    # Snapshot
    if not args.no_snapshot:
        after = round_dir / "after"
        if after.exists():
            shutil.rmtree(after)
        shutil.copytree(episode_dir, after, symlinks=True)
        print(f"snapshot: {after}", file=sys.stderr)

    # Round summary
    print("\n=== summary ===")
    print(f"Round:         {label}")
    print(f"OK:            {'YES' if scorecard['ok'] else 'NO'}")
    print(f"Total time:    {scorecard['total_seconds']:.1f}s")
    print(f"Automation:    {scorecard['automation_pct']:.1f}%")
    print(f"Scripts time:  {scorecard['script_seconds']:.1f}s")
    print(f"LLM time:      {scorecard['llm_seconds']:.1f}s")
    print(f"Steps:         {scorecard['step_count']}")

    delta_summary = {}
    if prior is not None:
        prior_dir, prior_card = prior
        print(f"\nvs {prior_card['round_label']}:")
        prior_total = prior_card.get("total_seconds", 0.0)
        prior_auto = prior_card.get("automation_pct", 0.0)
        new_total = scorecard["total_seconds"]
        new_auto = scorecard["automation_pct"]
        total_delta = pct_delta(new_total, prior_total)
        auto_delta = pct_delta(new_auto, prior_auto)
        print(f"  total_seconds: {prior_total:.1f}s → {new_total:.1f}s  ({total_delta})")
        print(f"  automation_pct:{prior_auto:.1f}% → {new_auto:.1f}%  ({auto_delta})")
        delta_summary = {
            "prior_round": prior_card["round_label"],
            "prior_total_seconds": prior_total,
            "new_total_seconds": new_total,
            "total_delta_pct": (new_total - prior_total) / prior_total * 100.0 if prior_total else None,
            "prior_automation_pct": prior_auto,
            "new_automation_pct": new_auto,
            "automation_delta_pct_points": new_auto - prior_auto,
        }
        # Convergence test: <5% absolute improvement on BOTH dimensions
        # Time improvement is negative delta; automation improvement is positive delta
        time_improved = -(delta_summary["total_delta_pct"] or 0) >= 5
        auto_improved = (delta_summary["automation_delta_pct_points"] or 0) >= 5
        converged = (not time_improved) and (not auto_improved)
        delta_summary["converged_below_5pct"] = converged
        if converged:
            print("\n>>> CONVERGED: improvements on both metrics are below 5% threshold")
        else:
            unimproved = []
            if not time_improved:
                unimproved.append("time")
            if not auto_improved:
                unimproved.append("automation")
            if unimproved == []:
                print("\n>>> Both metrics improved by ≥5%; iterate again")
            else:
                print(f"\n>>> Improvement <5% on: {', '.join(unimproved)}; can iterate or stop")

    # Append to rounds.jsonl for trend tracking
    rounds_log = HARNESS_ROOT / "rounds.jsonl"
    rec = {
        "label": label,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "total_seconds": scorecard["total_seconds"],
        "automation_pct": scorecard["automation_pct"],
        "script_seconds": scorecard["script_seconds"],
        "llm_seconds": scorecard["llm_seconds"],
        "step_count": scorecard["step_count"],
        "ok": scorecard["ok"],
        "delta": delta_summary,
    }
    with rounds_log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return 0 if scorecard["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
