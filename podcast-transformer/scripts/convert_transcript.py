#!/usr/bin/env python3
"""Convert a `browse-cli` Substack transcript scrape into the canonical
`Speaker: text` per-turn format the renderer expects.

`browse "https://www.lennysnewsletter.com/p/<slug>?showTranscript=true"`
emits the Substack transcript pane as repeating blocks of:

    0:00
    ERIC RIES
    ... paragraph 1 ...

    ... paragraph 2 (still same speaker) ...

    0:07
    LENNY RACHITSKY
    ... text ...

This script flattens each contiguous-paragraph block to a single
`Title-Case Name: body` line, and records `(speaker, seconds, char_offset)`
to `working/_turn_timestamps.json` so chapters can anchor against per-turn
timestamps rather than substring grep.

Usage:

    python3 scripts/convert_transcript.py <episode_dir> [--raw FILE] \
        [--out FILE] [--timestamps FILE]

Defaults:
  --raw         <episode_dir>/source/transcript_raw.txt
  --out         <episode_dir>/source/user-provided-transcript.txt
  --timestamps  <episode_dir>/working/_turn_timestamps.json

Idempotent: re-running overwrites both outputs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TIMESTAMP_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
# Match either ALL CAPS (legacy) or Title Case (current Substack) name lines
# of 2-4 word tokens. The body following will be normal prose.
SPEAKER_ALLCAPS_RE = re.compile(r"^[A-Z][A-Z .'-]{1,}[A-Z]$")
SPEAKER_TITLECASE_RE = re.compile(
    r"^[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){1,3}$"
)


def is_speaker_line(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 60:
        return False
    if SPEAKER_ALLCAPS_RE.match(line):
        return True
    return bool(SPEAKER_TITLECASE_RE.match(line))


def to_seconds(s: str) -> int:
    m = TIMESTAMP_RE.match(s.strip())
    if not m:
        raise ValueError(f"bad timestamp: {s!r}")
    h, mi, sec = m.groups()
    if sec is None:
        return int(h) * 60 + int(mi)
    return int(h) * 3600 + int(mi) * 60 + int(sec)


def title_case(name: str) -> str:
    """Normalize a name line to Title Case. Works for both ALL CAPS legacy
    (ERIC RIES → Eric Ries) and existing Title Case (Eric Ries → Eric Ries)."""
    parts = []
    for token in name.split():
        if "'" in token:
            head, sep, tail = token.partition("'")
            parts.append(head.capitalize() + sep + tail.lower())
        else:
            parts.append(token.capitalize())
    return " ".join(parts)


def parse_blocks(raw_text: str) -> list[tuple[int, str, str]]:
    """Return a list of (seconds, speaker_titlecase, body_text) tuples.

    A block starts at a timestamp line, followed by a speaker line (allowing
    blank lines between), followed by one or more body paragraphs until the
    next timestamp line. Browse-cli emits markdown with extra blank lines,
    plus a leading audio-player UI block we need to skip past."""
    lines = [ln.rstrip() for ln in raw_text.splitlines()]
    blocks: list[tuple[int, str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if TIMESTAMP_RE.match(line):
            ts = to_seconds(line)
            # Next non-blank line should be the speaker
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines):
                break
            speaker_line = lines[j].strip()
            if not is_speaker_line(speaker_line):
                # Not a real block (e.g. the audio-player UI line)
                i = j
                continue
            speaker = title_case(speaker_line)
            # Collect body until next timestamp line
            body_parts: list[str] = []
            k = j + 1
            while k < len(lines):
                lk = lines[k].strip()
                if TIMESTAMP_RE.match(lk):
                    break
                if lk:
                    body_parts.append(lk)
                k += 1
            body = " ".join(body_parts).strip()
            if body:
                blocks.append((ts, speaker, body))
            i = k
        else:
            i += 1
    return blocks


def render_canonical(blocks: list[tuple[int, str, str]]) -> tuple[str, list[dict]]:
    """Emit the canonical transcript text plus per-turn timestamp records."""
    out_lines: list[str] = []
    timestamps: list[dict] = []
    char_offset = 0
    for i, (sec, speaker, body) in enumerate(blocks):
        prefix = f"{speaker}: "
        line = prefix + body
        # Emit the shape anchor_chapters.py expects: i, start, timestamp,
        # speaker, first_words.
        first_words = " ".join(body.split()[:12])
        timestamps.append({
            "i": i,
            "start": sec,
            "timestamp": f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}",
            "speaker": speaker,
            "first_words": first_words,
            "char_offset": char_offset,
        })
        out_lines.append(line)
        # +2 for the trailing blank-line separator we'll emit between turns
        char_offset += len(line) + 2
    text = "\n\n".join(out_lines) + "\n"
    return text, timestamps


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episode_dir", type=Path)
    p.add_argument("--raw", type=Path, help="raw browse-cli output")
    p.add_argument("--out", type=Path, help="canonical transcript output")
    p.add_argument("--timestamps", type=Path, help="per-turn timestamps JSON")
    args = p.parse_args(argv)

    ep = args.episode_dir.resolve()
    raw_path = args.raw or ep / "source" / "transcript_raw.txt"
    out_path = args.out or ep / "source" / "user-provided-transcript.txt"
    ts_path = args.timestamps or ep / "working" / "_turn_timestamps.json"

    if not raw_path.exists():
        print(f"ERROR: {raw_path} not found", file=sys.stderr)
        return 2

    raw = raw_path.read_text(encoding="utf-8")
    blocks = parse_blocks(raw)
    if not blocks:
        print("ERROR: could not parse any speaker blocks from raw transcript", file=sys.stderr)
        return 2
    text, timestamps = render_canonical(blocks)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    ts_path.write_text(json.dumps(timestamps, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Turns:      {len(blocks)}")
    print(f"Speakers:   {sorted(set(s for _, s, _ in blocks))}")
    print(f"Wrote:      {out_path} ({len(text):,} chars)")
    print(f"Timestamps: {ts_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
