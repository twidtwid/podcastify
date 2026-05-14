#!/usr/bin/env python3
"""Lint podcast transcript text, Markdown, or canonical JSON segments."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Structural timestamps only — those that appear inside brackets like `[00:14:32]`
# or at the very start of a line (chapter headings, segment markers). This avoids
# false positives from in-prose time references like "5:45 every morning" or
# "4:30 in the afternoon" which are not navigational markers.
TIMESTAMP_RE = re.compile(
    r"(?:^|\[)\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\s*(?:\]|$|[\s\-—])",
    re.MULTILINE,
)
# A speaker label is one or more whitespace-separated tokens before a colon, where
# each token is either ALL-CAPS or TitleCase. Reject sentences-with-colons
# like "Doctorow's three-stage platform decay: ..." because "three-stage" / "platform" /
# "decay" begin with lowercase letters. The ALL-CAPS branch admits an underscore
# so diarization labels like SPEAKER_0 / SPEAKER_01 are recognized as speakers
# rather than treated as "no speaker labels found" (false negative); the TitleCase
# branch keeps the original character set (no underscore) because human names
# don't contain them and we don't want to widen the false-positive surface.
_SPEAKER_TOKEN = r"(?:[A-Z][A-Z0-9_.'-]*|[A-Z][a-z][A-Za-z0-9.'-]*)"
# Speaker tokens are separated by horizontal whitespace only — never a newline —
# so a sentence-ending name followed by a real speaker label on the next line
# (e.g. "...like Jerry.\n\nJARED WILSON: ...") doesn't get captured as a single
# multi-word speaker.
SPEAKER_RE = re.compile(
    r"^[ \t]*(?:\[[^\]]+\][ \t]*)?(" + _SPEAKER_TOKEN + r"(?:[ \t]+" + _SPEAKER_TOKEN + r"){0,4}):[ \t]+\S",
    re.MULTILINE,
)
UNCLEAR_RE = re.compile(
    r"\[[^\]]*\b(?:inaudible|unintelligible|unclear)\b[^\]]*\]|\b(?:inaudible|unintelligible)\b|\[\s*\?\s*\]|\?\?\?",
    re.IGNORECASE,
)
GENERIC_SPEAKER_RE = re.compile(r"^(?:Speaker|Host|Guest)\s*\d*$", re.IGNORECASE)


def timestamp_to_seconds(match: re.Match[str]) -> float:
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = match.group(4) or "0"
    return hours * 3600 + minutes * 60 + seconds + int(millis.ljust(3, "0")) / 1000


def read_input(path: Path) -> tuple[str, Any | None]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            return text, json.loads(text)
        except json.JSONDecodeError:
            return text, None
    return text, None


def lint_json_segments(data: Any) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {"format": "json", "segments": 0, "speakers": []}

    if not isinstance(data, dict):
        return ["JSON transcript root must be an object"], warnings, stats
    segments = data.get("segments")
    if not isinstance(segments, list):
        return ["JSON transcript must contain a segments array"], warnings, stats

    speakers: set[str] = set()
    previous_start = -1.0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"segments[{index}] must be an object")
            continue
        text = str(segment.get("text") or "").strip()
        speaker = str(segment.get("speaker") or "").strip()
        start = segment.get("start")
        end = segment.get("end")
        if not text:
            errors.append(f"segments[{index}].text is empty")
        if speaker:
            speakers.add(speaker)
        else:
            warnings.append(f"segments[{index}].speaker is empty")
        if not isinstance(start, (int, float)):
            errors.append(f"segments[{index}].start must be numeric")
            continue
        if end is not None and not isinstance(end, (int, float)):
            errors.append(f"segments[{index}].end must be numeric when present")
        if isinstance(end, (int, float)) and end < start:
            errors.append(f"segments[{index}].end is before start")
        if start < previous_start:
            errors.append(f"segments[{index}].start moves backwards")
        previous_start = float(start)

    stats["segments"] = len(segments)
    stats["speakers"] = sorted(speakers)
    if not segments:
        warnings.append("segments array is empty")
    return errors, warnings, stats


def sidecar_uncertain_span_count(sidecar_path: Path | None) -> int:
    data = read_sidecar(sidecar_path)
    if not data:
        return 0
    spans = data.get("verification", {}).get("uncertain_spans", [])
    return len(spans) if isinstance(spans, list) else 0


def sidecar_chapter_count(sidecar_path: Path | None) -> int:
    data = read_sidecar(sidecar_path)
    if not data:
        return 0
    chapters = data.get("episode", {}).get("chapters", [])
    return len(chapters) if isinstance(chapters, list) else 0


def read_sidecar(sidecar_path: Path | None) -> dict[str, Any]:
    if sidecar_path is None:
        return {}
    try:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def lint_text(text: str, *, sidecar_path: Path | None = None) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    # Chapter-heading lines (`## [12:00] Title`) are rendered metadata, not
    # source transcript timing — they're injected by podcast_build from
    # sidecar.episode.chapters. Strip them before the monotonic timestamp
    # check so an LLM-rough chapter time doesn't fight a publisher's
    # accurate inline timestamps (e.g. New Yorker transcripts include
    # `[00:11:00]` markers that are authoritative). Speaker labels and
    # unclear-marker counts are still scanned over the full text.
    scan_text = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("## ")
    )
    timestamps = [timestamp_to_seconds(match) for match in TIMESTAMP_RE.finditer(scan_text)]
    speakers = [match.group(1).strip() for match in SPEAKER_RE.finditer(text)]
    unclear_count = len(UNCLEAR_RE.findall(text))
    words = re.findall(r"\b[\w'-]+\b", text)

    for index, current in enumerate(timestamps[1:], start=1):
        if current < timestamps[index - 1]:
            errors.append(f"timestamp moves backwards near timestamp #{index + 1}")
            break

    chapter_count = sidecar_chapter_count(sidecar_path)
    if not timestamps and not chapter_count:
        warnings.append("no timestamps found")
    if not speakers:
        warnings.append("no speaker labels found")

    generic_speakers = sorted({speaker for speaker in speakers if GENERIC_SPEAKER_RE.match(speaker)})
    if generic_speakers:
        warnings.append("generic speaker labels remain: " + ", ".join(generic_speakers))

    long_lines = [
        index
        for index, line in enumerate(text.splitlines(), start=1)
        if len(line) > 260 and not line.lstrip().startswith(("http://", "https://"))
    ]
    if long_lines:
        sample = ", ".join(str(line) for line in long_lines[:8])
        warnings.append(f"very long transcript lines at: {sample}")

    covered_unclear_count = sidecar_uncertain_span_count(sidecar_path)
    if unclear_count and covered_unclear_count < unclear_count:
        warnings.append(f"{unclear_count} unclear/inaudible markers found; confirm sidecar uncertain_spans covers material cases")

    stats = {
        "format": "text",
        "words": len(words),
        "timestamps": len(timestamps),
        "speakers": sorted(set(speakers)),
        "unclear_markers": unclear_count,
        "sidecar_uncertain_spans": covered_unclear_count,
        "sidecar_chapters": chapter_count,
    }
    return errors, warnings, stats


def print_report(path: Path, errors: list[str], warnings: list[str], stats: dict[str, Any]) -> None:
    print(f"Transcript lint: {path}")
    for key, value in stats.items():
        if isinstance(value, list):
            value = ", ".join(value) if value else "(none)"
        print(f"  {key}: {value}")
    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARN: {message}")
    if not errors and not warnings:
        print("No transcript lint issues found")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript")
    parser.add_argument("--sidecar", type=Path, default=None, help="metadata.sidecar.json used to confirm unclear-marker coverage")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    path = Path(args.transcript)
    try:
        text, data = read_input(path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if data is not None:
        errors, warnings, stats = lint_json_segments(data)
    else:
        errors, warnings, stats = lint_text(text, sidecar_path=args.sidecar)

    print_report(path, errors, warnings, stats)
    if errors or (warnings and args.strict):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
