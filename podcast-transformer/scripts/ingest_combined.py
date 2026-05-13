#!/usr/bin/env python3
"""Split a single combined input file into clean per-episode source files.

Users frequently hand off a single text file that contains:

  1. The episode URL on the first non-empty line (https://...).
  2. An optional markdown chapter timeline (lines like
     `- [00:02:04](https://www.youtube.com/watch?v=...&t=124s) - Title`).
  3. The transcript proper (starts at the first `Speaker:` line and runs
     to the end of the file).

This script separates those three components, writes the transcript to
`<episode-dir>/source/user-provided-transcript.txt`, and stashes the
parsed URL + chapter list in `<episode-dir>/working/_parsed.json` so
the next step (sidecar init + chapter population) can pick them up.

Run it like:

    python3 scripts/ingest_combined.py <combined-file> <episode-dir>

The episode directory will be created (with source/, working/, final/
subdirectories) if it doesn't already exist.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

CHAPTER_RE = re.compile(
    r"^\s*-\s*\[(\d{1,2}):(\d{2}):(\d{2})\]\s*(?:\([^)]*\))?\s*-\s*(.+?)\s*$"
)
CHAPTER_RE_SHORT = re.compile(
    r"^\s*-\s*\[(\d{1,2}):(\d{2})\]\s*(?:\([^)]*\))?\s*-\s*(.+?)\s*$"
)
SPEAKER_RE = re.compile(r"^[A-Z][a-zA-Z .'-]{0,48}:\s+\S")
URL_RE = re.compile(r"https?://[^\s)]+")


def parse(text: str) -> dict[str, Any]:
    lines = text.splitlines()

    url = ""
    for line in lines:
        match = URL_RE.search(line)
        if match:
            url = match.group(0).rstrip(",.;")
            break

    chapters: list[dict[str, Any]] = []
    for line in lines:
        m = CHAPTER_RE.match(line)
        if m:
            h, mi, s, title = m.groups()
            chapters.append({
                "start": int(h) * 3600 + int(mi) * 60 + int(s),
                "timestamp": f"{int(h):02d}:{mi}:{s}",
                "title": title.strip(),
            })
            continue
        m = CHAPTER_RE_SHORT.match(line)
        if m:
            mi, s, title = m.groups()
            chapters.append({
                "start": int(mi) * 60 + int(s),
                "timestamp": f"00:{mi}:{s}",
                "title": title.strip(),
            })

    transcript_start = next(
        (i for i, line in enumerate(lines) if SPEAKER_RE.match(line)),
        None,
    )
    transcript = (
        "\n".join(lines[transcript_start:]).strip() + "\n"
        if transcript_start is not None
        else ""
    )

    return {"url": url, "chapters": chapters, "transcript": transcript}


def emit(parsed: dict[str, Any], episode_dir: Path) -> dict[str, Path]:
    source_dir = episode_dir / "source"
    working_dir = episode_dir / "working"
    (episode_dir / "final").mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    working_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = source_dir / "user-provided-transcript.txt"
    if not parsed["transcript"]:
        raise SystemExit("ERROR: could not find a transcript section (no 'Speaker:' line)")
    transcript_path.write_text(parsed["transcript"], encoding="utf-8")

    parsed_path = working_dir / "_parsed.json"
    parsed_path.write_text(
        json.dumps(
            {"episode_url": parsed["url"], "chapters": parsed["chapters"]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {"transcript": transcript_path, "parsed": parsed_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file", type=Path, help="Combined input file with URL, timeline, transcript")
    parser.add_argument("episode_dir", type=Path, help="Target episode directory (will be created)")
    args = parser.parse_args(argv)

    if not args.input_file.is_file():
        print(f"ERROR: input file not found: {args.input_file}", file=sys.stderr)
        return 2

    parsed = parse(args.input_file.read_text(encoding="utf-8"))
    paths = emit(parsed, args.episode_dir.resolve())

    transcript_words = len(parsed["transcript"].split())
    print(f"URL:        {parsed['url'] or '(none found)'}")
    print(f"Chapters:   {len(parsed['chapters'])}")
    print(f"Transcript: {transcript_words} words -> {paths['transcript']}")
    print(f"Parsed:     {paths['parsed']}")
    if not parsed["url"]:
        print("WARN: no URL found in the input — pass --episode-url to sidecar.py init manually.", file=sys.stderr)
    if not parsed["chapters"]:
        print("WARN: no chapter timeline found — episode will have no chapter rail. See notes-schema.md.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
