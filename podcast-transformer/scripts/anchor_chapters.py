#!/usr/bin/env python3
"""Generate chapter_queries from per-turn timestamps and a chapter timeline.

When a transcript source (e.g. Substack's transcript view) exposes a
timestamp per speaker turn AND a separate chapter timeline (e.g. from
YouTube's video description) is available, the renderer doesn't have to
substring-search the prose to anchor chapters — every chapter timestamp
maps directly to the earliest turn whose start time matches or exceeds it.

This helper takes those two inputs and emits a `chapter_queries` array
suitable for paste-or-script into `source/episode.notes.json`. Each query
is the first few words of the anchored turn, distinctive enough for
`build_chapters` to land the chapter on the correct turn even if turn
indices shift after later edits.

Inputs:
  per-turn timestamps file — JSON list of
    [{i: int, timestamp: "HH:MM:SS", start: int, speaker: str,
      first_words: str}, ...]
  chapter timeline file — JSON list of
    [{start: int, title: str}, ...]
    or a markdown-bullet timeline (see --markdown-timeline)

Usage:
  python3 scripts/anchor_chapters.py \\
      working/_turn_timestamps.json \\
      working/_chapters.json

or:
  python3 scripts/anchor_chapters.py \\
      working/_turn_timestamps.json \\
      --markdown-timeline 'path/to/timeline.txt'

Output: prints the chapter_queries array as JSON to stdout. Also prints
a side-by-side summary on stderr so you can sanity-check the anchoring
before pasting into notes.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MD_CHAPTER_RE = re.compile(
    r"^\s*[-*]?\s*\(?(\d{1,2}(?::\d{2}){1,2})\)?\s*[-–—]?\s*(.+?)\s*$"
)


def parse_ts(s: str) -> int:
    parts = [int(p) for p in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def load_chapters(path: Path | None, markdown_path: Path | None) -> list[dict[str, Any]]:
    if path:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit("ERROR: chapters JSON must be a list")
        return [{"start": int(c["start"]), "title": str(c["title"])} for c in data]
    if markdown_path:
        chapters = []
        for line in markdown_path.read_text(encoding="utf-8").splitlines():
            m = MD_CHAPTER_RE.match(line)
            if m and m.group(2) and ":" in m.group(1):
                chapters.append({"start": parse_ts(m.group(1)), "title": m.group(2).strip()})
        return chapters
    raise SystemExit("ERROR: provide either a chapters JSON file or --markdown-timeline")


def anchor(turns: list[dict[str, Any]], chapters: list[dict[str, Any]], query_words: int) -> list[str]:
    """Turn-boundary anchoring (fallback when no per-word timestamps available).

    Collides when multiple chapters fall inside the same speaker turn — chapter
    queries end up identical (or near-identical), the validator emits warnings,
    and the user has to hand-write distinctive substrings. The word-level
    anchor below eliminates this.
    """
    queries: list[str] = []
    for chapter in chapters:
        candidates = [t for t in turns if t["start"] >= chapter["start"]]
        pick = candidates[0] if candidates else turns[-1]
        words = pick.get("first_words", "").split()
        queries.append(" ".join(words[:query_words]) if words else "")
    return queries


def anchor_by_word(
    words: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    query_words: int,
) -> list[str]:
    """Word-level anchoring: each chapter lands on the exact word spoken at its
    timestamp, and the query is N words starting from that position.

    Two chapters inside the same speaker turn no longer collide, because the
    queries are pulled from distinct word offsets within the turn.
    """
    if not words:
        raise SystemExit("ERROR: empty word list for --word-timestamps mode")
    queries: list[str] = []
    for chapter in chapters:
        target = chapter["start"]
        # Linear scan is fine — 20k words × 28 chapters = <1ms
        idx = next((i for i, w in enumerate(words) if (w.get("start") or 0) >= target), len(words) - 1)
        # Pull the next N words as the query. Strip trailing punctuation so the
        # substring-match in podcast_build.find_turn is robust.
        slice_ = words[idx : idx + query_words]
        queries.append(" ".join(w["word"] for w in slice_).strip())
    return queries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("turns_json", type=Path, help="Per-turn timestamps JSON (from ingest_combined.py or a custom parser).")
    parser.add_argument("chapters_json", type=Path, nargs="?", help="Chapter timeline JSON, list of {start, title}.")
    parser.add_argument("--markdown-timeline", type=Path, help="Plain-text timeline with `- (HH:MM:SS) Title` lines (e.g. a YouTube description paste).")
    parser.add_argument("--query-words", type=int, default=8, help="How many leading words of the anchored turn to use as the query (default 8).")
    parser.add_argument("--word-timestamps", type=Path, help="Optional per-word timestamps JSON (from ingest_substack_json.py). When supplied, anchoring uses sub-second word matching instead of turn boundaries — eliminates chapter-collision problems.")
    args = parser.parse_args(argv)

    if not args.turns_json.is_file():
        print(f"ERROR: turns JSON not found: {args.turns_json}", file=sys.stderr)
        return 2
    turns = json.loads(args.turns_json.read_text(encoding="utf-8"))
    if not isinstance(turns, list) or not turns:
        print("ERROR: turns JSON must be a non-empty list", file=sys.stderr)
        return 2

    chapters = load_chapters(args.chapters_json, args.markdown_timeline)
    if not chapters:
        print("ERROR: no chapters parsed from the provided input", file=sys.stderr)
        return 2

    if args.word_timestamps:
        if not args.word_timestamps.is_file():
            print(f"ERROR: word-timestamps JSON not found: {args.word_timestamps}", file=sys.stderr)
            return 2
        words = json.loads(args.word_timestamps.read_text(encoding="utf-8"))
        if not isinstance(words, list) or not words:
            print("ERROR: word-timestamps JSON must be a non-empty list", file=sys.stderr)
            return 2
        queries = anchor_by_word(words, chapters, args.query_words)
        print(f"Anchored {len(chapters)} chapters using per-word timestamps ({len(words)} words):", file=sys.stderr)
    else:
        queries = anchor(turns, chapters, args.query_words)
        print(f"Anchored {len(chapters)} chapters using turn-boundary fallback ({len(turns)} turns):", file=sys.stderr)

    # Side-by-side preview on stderr so you can eyeball the anchoring
    for chapter, query in zip(chapters, queries):
        h, rem = divmod(chapter["start"], 3600)
        mi, s = divmod(rem, 60)
        ts = f"{h:02d}:{mi:02d}:{s:02d}"
        print(f"  {ts}  {chapter['title']:55}  → {query!r}", file=sys.stderr)

    # The pasteable array goes to stdout
    print(json.dumps(queries, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
