#!/usr/bin/env python3
"""Score an extraction run.

Reads `working/_pipeline_metrics.jsonl` (produced by `pipeline_log.py`) and
validates the deliverables in `final/`. Emits a scorecard:

    {
      "episode_dir": "...",
      "total_seconds": 87.3,
      "script_seconds": 84.1,
      "llm_seconds": 3.2,
      "automation_pct": 96.3,
      "step_count": 11,
      "validators": {
        "sidecar":    {"ok": true,  "warnings": 0},
        "transcript": {"ok": true,  "warnings": 1},
        "notes":      {"ok": true,  "warnings": 0},
        "artifacts":  {"ok": true,  "warnings": 2}
      },
      "deliverables": {
        "annotated-transcript.html": true,
        "podcast-at-a-glance.html":  true,
        "episode.package.json":      true,
        "transcript.verified.md":    true,
        "metadata.sidecar.json":     true
      },
      "ok": true,
      "round_label": "round-1"
    }

A run is `ok` if all required deliverables exist AND the artifact validator
reports no failures (warnings are tolerated).

Usage:
  python3 scripts/score_extraction.py <episode_dir> [--label round-1] \
      [--scorecard-out PATH] [--reference-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "podcast-transformer" / "scripts"

REQUIRED_DELIVERABLES = [
    "final/annotated-transcript.html",
    "final/podcast-at-a-glance.html",
    "final/episode.package.json",
    "final/transcript.verified.md",
    "final/metadata.sidecar.json",
]


def run_validators(episode_dir: Path) -> dict:
    """Run the bundle of validators that `podcast_build.py validate` invokes,
    plus our notes_lint. Capture pass/warn/fail per validator."""
    out = {}

    # sidecar.py validate
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "sidecar.py"), "validate",
         str(episode_dir / "final" / "metadata.sidecar.json")],
        capture_output=True, text=True,
    )
    out["sidecar"] = {
        "ok": r.returncode == 0,
        "stdout": r.stdout[-2000:],
        "stderr": r.stderr[-2000:],
        "warnings": r.stdout.lower().count("warn") + r.stderr.lower().count("warn"),
    }

    # transcript_lint.py
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "transcript_lint.py"),
         str(episode_dir / "final" / "transcript.verified.md")],
        capture_output=True, text=True,
    )
    out["transcript"] = {
        "ok": r.returncode == 0,
        "stdout": r.stdout[-2000:],
        "stderr": r.stderr[-2000:],
        "warnings": r.stdout.lower().count("warn") + r.stderr.lower().count("warn"),
    }

    # notes_lint.py
    notes_path = episode_dir / "source" / "episode.notes.json"
    if notes_path.exists():
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "notes_lint.py"), str(notes_path)],
            capture_output=True, text=True,
        )
        out["notes"] = {
            "ok": r.returncode == 0,
            "stdout": r.stdout[-2000:],
            "stderr": r.stderr[-2000:],
            "warnings": r.stdout.lower().count("warn") + r.stderr.lower().count("warn"),
        }
    else:
        out["notes"] = {"ok": False, "stdout": "", "stderr": "notes.json missing", "warnings": 0}

    # podcast_build.py validate (artifact + sidecar + transcript banned-text guard)
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "podcast_build.py"), "validate", str(episode_dir)],
        capture_output=True, text=True,
    )
    out["artifacts"] = {
        "ok": r.returncode == 0,
        "stdout": r.stdout[-3000:],
        "stderr": r.stderr[-3000:],
        "warnings": r.stdout.lower().count("warn") + r.stderr.lower().count("warn"),
    }

    return out


def check_deliverables(episode_dir: Path) -> dict[str, bool]:
    return {Path(p).name: (episode_dir / p).exists() for p in REQUIRED_DELIVERABLES}


def read_metrics_summary(episode_dir: Path) -> dict:
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "pipeline_log.py"), "summary",
         str(episode_dir), "--json"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return {"error": r.stderr}
    return json.loads(r.stdout)


def diff_against_reference(episode_dir: Path, reference_dir: Path) -> dict:
    """Compare key artifacts against a reference output. Returns a coarse
    similarity report, not a byte-for-byte diff — the API draft will vary
    between runs."""
    out: dict = {}

    def _line_count(p: Path) -> int:
        return len(p.read_text(encoding="utf-8", errors="replace").splitlines()) if p.exists() else 0

    def _byte_size(p: Path) -> int:
        return p.stat().st_size if p.exists() else 0

    for sub in (
        "final/transcript.verified.md",
        "final/annotated-transcript.html",
        "final/podcast-at-a-glance.html",
        "final/episode.package.json",
        "final/metadata.sidecar.json",
        "source/episode.notes.json",
    ):
        a = episode_dir / sub
        b = reference_dir / sub
        out[sub] = {
            "this_lines": _line_count(a),
            "ref_lines": _line_count(b),
            "this_bytes": _byte_size(a),
            "ref_bytes": _byte_size(b),
            "delta_pct": (
                None if _byte_size(b) == 0
                else round(100.0 * (_byte_size(a) - _byte_size(b)) / _byte_size(b), 1)
            ),
        }

    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episode_dir", type=Path)
    p.add_argument("--label", default="round-?")
    p.add_argument("--scorecard-out", type=Path,
                   help="where to write the scorecard JSON (default: working/_scorecard.json)")
    p.add_argument("--reference-dir", type=Path,
                   help="compare deliverables against this reference dir")
    args = p.parse_args(argv)

    episode_dir = args.episode_dir.resolve()
    if not episode_dir.is_dir():
        print(f"ERROR: episode dir not found: {episode_dir}", file=sys.stderr)
        return 2

    summary = read_metrics_summary(episode_dir)
    deliverables = check_deliverables(episode_dir)
    validators = run_validators(episode_dir)
    reference = diff_against_reference(episode_dir, args.reference_dir.resolve()) if args.reference_dir else None

    ok = all(deliverables.values()) and validators["artifacts"]["ok"]

    scorecard = {
        "round_label": args.label,
        "episode_dir": str(episode_dir),
        "step_count": summary.get("step_count", 0),
        "total_seconds": round(summary.get("total_seconds", 0.0), 2),
        "measured_seconds": round(summary.get("measured_seconds", 0.0), 2),
        "script_seconds": round(summary.get("script_seconds", 0.0), 2),
        "llm_seconds": round(summary.get("llm_seconds", 0.0), 2),
        "automation_pct": round(summary.get("automation_pct", 0.0), 1),
        "steps": [
            {"step": s["step"], "kind": s["kind"],
             "seconds": round(s["duration_seconds"], 2),
             "status": s["status"]}
            for s in summary.get("steps", [])
        ],
        "deliverables": deliverables,
        "validators": {
            k: {"ok": v["ok"], "warnings": v["warnings"]}
            for k, v in validators.items()
        },
        "validators_detail": validators,
        "reference_diff": reference,
        "ok": ok,
    }

    out_path = args.scorecard_out or (episode_dir / "working" / "_scorecard.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(scorecard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Human-readable summary
    print(f"=== {args.label} ===")
    print(f"Episode dir:   {episode_dir}")
    print(f"OK:            {'YES' if ok else 'NO'}")
    print(f"Total time:    {scorecard['total_seconds']:.1f}s")
    print(f"  script:      {scorecard['script_seconds']:.1f}s")
    print(f"  llm:         {scorecard['llm_seconds']:.1f}s")
    print(f"Automation:    {scorecard['automation_pct']:.1f}%")
    print(f"Steps:         {scorecard['step_count']}")
    print(f"Deliverables:  {sum(deliverables.values())}/{len(deliverables)} present")
    print("Validators:")
    for k, v in validators.items():
        flag = "OK " if v["ok"] else "FAIL"
        print(f"  {flag}  {k:<11}  warnings={v['warnings']}")
    print(f"\nScorecard:     {out_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
