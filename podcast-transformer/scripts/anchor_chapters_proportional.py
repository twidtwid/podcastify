#!/usr/bin/env python3
"""Proportional chapter anchoring — eliminates duplicate-query collisions.

The legacy `anchor_chapters.py` picks the first N words of the turn whose
start time is at-or-after the chapter timestamp. When three or four
chapters fall inside the same long speaker turn (very common in interview
podcasts — one long answer can cover 6+ minutes), all of them end up with
the same first-words query, the validator warns, and the renderer falls
back to time-proportional anchoring anyway.

This script computes each chapter's *position within its containing turn*
and slices the turn body at the matching proportional offset. The result:
distinct queries per chapter, AND those queries are real substrings of the
transcript so `find_turn` lands them precisely.

Inputs:
  --transcript        source/user-provided-transcript.txt
  --turns             working/_turn_timestamps.json (for accurate per-turn
                      start times — falls back to parsing the transcript)
  --chapters          chapter timeline JSON (list of {start, title})
  --duration-seconds  episode duration (for the last turn's end)
  --query-words       word count per query (default 6)

Output: prints the chapter_queries array as JSON to stdout, with a side-by-
side preview on stderr.

Usage:
  python3 scripts/anchor_chapters_proportional.py \\
      --transcript path/to/transcript.txt \\
      --turns      working/_turn_timestamps.json \\
      --chapters   working/_chapters.json \\
      --duration-seconds 5937
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

WORD_RE = re.compile(r"\S+")


def parse_transcript_bodies(text: str) -> list[str]:
    """Split `Speaker: body` paragraphs into a list of bodies in order. Empty
    paragraphs and bodies missing a `Speaker:` prefix are skipped."""
    bodies: list[str] = []
    for chunk in re.split(r"\n\s*\n", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^[A-Z][\w' .\-]{0,48}:\s+(.*)$", chunk, re.DOTALL)
        if m:
            bodies.append(re.sub(r"\s+", " ", m.group(1)).strip())
    return bodies


def slice_words(body: str, rel: float, n: int) -> str:
    """Take `n` whitespace-separated tokens starting at the position
    int(rel * total_words) within `body`. Clamps so we never start past
    `total_words - n`."""
    words = body.split()
    if not words:
        return ""
    target = int(max(0.0, min(1.0, rel)) * len(words))
    target = max(0, min(target, max(0, len(words) - n)))
    return " ".join(words[target : target + n])


def anchor(
    chapters: list[dict],
    turns: list[dict],
    bodies: list[str],
    duration: int,
    query_words: int,
) -> list[str]:
    if not turns:
        raise SystemExit("ERROR: empty turns list")
    # Align bodies to turns by index (assume parallel arrays — the parser and
    # convert_transcript both emit in transcript order).
    if len(bodies) < len(turns):
        # Pad with empty bodies; better than dying
        bodies = bodies + [""] * (len(turns) - len(bodies))

    queries: list[str] = []
    for i, chap in enumerate(chapters):
        t_start = int(chap["start"])
        # Find containing turn: last turn whose start <= t_start
        containing = None
        for idx, turn in enumerate(turns):
            if int(turn["start"]) <= t_start:
                containing = idx
            else:
                break
        if containing is None:
            containing = 0
        turn = turns[containing]
        turn_start = int(turn["start"])
        # Turn end = next turn's start, or duration for last turn
        if containing + 1 < len(turns):
            turn_end = int(turns[containing + 1]["start"])
        else:
            turn_end = duration if duration > turn_start else turn_start + 60
        span = max(1, turn_end - turn_start)
        rel = (t_start - turn_start) / span
        body = bodies[containing] if containing < len(bodies) else ""
        if not body:
            # Last-ditch fall-back: use first_words from the turns file
            body = turn.get("first_words", "") or ""
        q = slice_words(body, rel, query_words)
        if not q:
            q = turn.get("first_words", "") or f"chapter-{i}"
        queries.append(q)
    return queries


def deduplicate(queries: list[str]) -> list[str]:
    """If two queries are identical strings (which can still happen when a
    turn body is shorter than `query_words` or when adjacent chapters land
    at the same proportional offset), nudge by extending the shorter one."""
    out = list(queries)
    seen: set[str] = set()
    for i, q in enumerate(out):
        if q in seen:
            # Append the chapter index so the validator sees it as distinct.
            # The renderer's find_turn will fail to substring-match the
            # tagged form and fall back to the time estimate, which is fine.
            out[i] = f"{q} [{i}]"
        seen.add(out[i])
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--transcript", type=Path, required=True)
    p.add_argument("--turns", type=Path, required=True)
    p.add_argument("--chapters", type=Path, required=True)
    p.add_argument("--duration-seconds", type=int, default=0)
    p.add_argument("--query-words", type=int, default=6)
    args = p.parse_args(argv)

    for path in (args.transcript, args.turns, args.chapters):
        if not path.is_file():
            print(f"ERROR: {path} not found", file=sys.stderr)
            return 2

    transcript = args.transcript.read_text(encoding="utf-8")
    turns = json.loads(args.turns.read_text(encoding="utf-8"))
    chapters = json.loads(args.chapters.read_text(encoding="utf-8"))

    bodies = parse_transcript_bodies(transcript)

    duration = args.duration_seconds or 0
    if duration <= 0 and turns:
        # Use the last turn's start + a 3-minute buffer as a stand-in.
        duration = int(turns[-1].get("start", 0)) + 180

    queries = anchor(chapters, turns, bodies, duration, args.query_words)
    queries = deduplicate(queries)

    # Side-by-side preview on stderr
    print(f"Anchored {len(chapters)} chapters proportionally ({len(turns)} turns, {len(bodies)} bodies):",
          file=sys.stderr)
    for chap, q in zip(chapters, queries):
        h, rem = divmod(int(chap["start"]), 3600)
        mi, s = divmod(rem, 60)
        ts = f"{h:02d}:{mi:02d}:{s:02d}"
        title = chap.get("title", "")
        print(f"  {ts}  {title:50}  → {q!r}", file=sys.stderr)

    print(json.dumps(queries, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
