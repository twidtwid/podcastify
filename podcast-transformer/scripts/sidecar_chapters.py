#!/usr/bin/env python3
"""Populate `episode.chapters` in `metadata.sidecar.json` from either
`working/_parsed.json` (produced by `ingest_combined.py`) or an inline
chapter list passed on the command line.

`sidecar.py init` only takes scalar metadata, so this is the bridge from
the parsed chapter timeline to the sidecar's `episode.chapters` array. It
also fills `episode.duration_seconds` from the last chapter's start time
when the sidecar doesn't already have one.

Usage:

    python3 scripts/sidecar_chapters.py <episode_dir> \
        [--parsed-json PATH] [--sidecar PATH] [--dry-run]

Idempotent: replaces any existing `episode.chapters` array.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_chapters_from_parsed(parsed_path: Path) -> list[dict]:
    data = json.loads(parsed_path.read_text(encoding="utf-8"))
    raw = data.get("chapters") or []
    out: list[dict] = []
    for c in raw:
        out.append({
            "start": int(c["start"]),
            "timestamp": c.get("timestamp") or "",
            "title": c.get("title") or "",
            "summary": "",
            "tags": [],
        })
    return out


def estimate_duration(chapters: list[dict], existing: int) -> int:
    if existing:
        return existing
    if not chapters:
        return 0
    last = chapters[-1].get("start", 0)
    # Rough estimate: assume a final chapter that's ~3 minutes long.
    return last + 180


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episode_dir", type=Path)
    p.add_argument("--parsed-json", type=Path)
    p.add_argument("--sidecar", type=Path)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    ep = args.episode_dir.resolve()
    parsed_path = args.parsed_json or ep / "working" / "_parsed.json"
    sidecar_path = args.sidecar or ep / "final" / "metadata.sidecar.json"

    if not parsed_path.exists():
        print(f"ERROR: parsed JSON not found: {parsed_path}", file=sys.stderr)
        return 2
    if not sidecar_path.exists():
        print(f"ERROR: sidecar not found: {sidecar_path}", file=sys.stderr)
        return 2

    chapters = load_chapters_from_parsed(parsed_path)
    if not chapters:
        print("WARN: parsed JSON had no chapters; sidecar untouched.", file=sys.stderr)
        return 0

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    episode = sidecar.setdefault("episode", {})
    episode["chapters"] = chapters
    existing_duration = int(episode.get("duration_seconds") or 0)
    episode["duration_seconds"] = estimate_duration(chapters, existing_duration)
    sidecar["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")

    if args.dry_run:
        print(f"would write {len(chapters)} chapters to {sidecar_path}", file=sys.stderr)
        return 0

    sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(chapters)} chapters (duration {episode['duration_seconds']}s) -> {sidecar_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
