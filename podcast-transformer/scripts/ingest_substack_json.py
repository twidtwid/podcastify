#!/usr/bin/env python3
"""Ingest a Substack `transcription.json` into the pipeline's source/working layout.

Substack publishes the underlying alignment file at
`https://substackcdn.com/video_upload/post/<id>/<uuid>/<ts>/transcription.json`
(signed CloudFront URL discoverable by regex-grepping the article HTML — see
SKILL.md and reference_chrome_blob_download.md memory). The file is an array of
segments, each with:

    {start, end, text, speaker, words: [{word, start, end, score}], chars}

This script consumes that JSON and produces the same artifacts our pipeline
expects from a publisher-provided transcript, but with three upgrades over the
prose-scraping path:

- **Per-word timestamps** for sub-second chapter anchoring (consumed by
  `anchor_chapters.py --word-timestamps`).
- **Speaker diarization** (SPEAKER_0/SPEAKER_1) auto-mapped to real names via
  dominance heuristic + sidecar host/guest, or via explicit --speaker flags.
- **Confidence scores** that auto-populate `verification.uncertain_spans` for
  any word whose alignment confidence is below `--confidence-threshold`
  (default 0.4).

Outputs (relative to `<episode_dir>`):
- `source/user-provided-transcript.txt`  (Speaker: text turns)
- `working/_turn_timestamps.json`         (per-turn structure used downstream)
- `working/_word_timestamps.json`         (per-word for chapter anchoring)
- Optionally: append to `final/metadata.sidecar.json` uncertain_spans block

Usage:
  scripts/ingest_substack_json.py path/to/transcription.json podcast-output/<slug>
  scripts/ingest_substack_json.py ... --speaker SPEAKER_0='Lenny Rachitsky' \\
                                       --speaker SPEAKER_1='Eric Ries'
  scripts/ingest_substack_json.py ... --confidence-threshold 0.3 \\
                                       --write-uncertain-spans

If --speaker flags are omitted, the script maps anonymous speakers using the
sidecar's host/guest fields plus a word-share heuristic:
- Single guest interview: guest = dominant speaker (>55% of words is common).
- Multiple guests, or balanced share: emits the mapping decision and asks
  the user to re-run with explicit --speaker flags.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_segments(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"ERROR: {path} does not look like a Substack transcription.json (expected non-empty array)")
    needed = {"start", "end", "text", "speaker"}
    missing = needed - set(data[0].keys())
    if missing:
        raise SystemExit(f"ERROR: first segment missing keys: {missing}")
    return data


def coalesce_turns(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group consecutive same-speaker segments into turns.

    Substack's segments are roughly sentence-sized; turns are the maximal runs
    of the same speaker, which is what readers expect to see as a single
    "paragraph block" in the rendered transcript.
    """
    turns: list[dict[str, Any]] = []
    for seg in segments:
        if turns and turns[-1]["speaker"] == seg["speaker"]:
            turns[-1]["end"] = seg["end"]
            turns[-1]["segments"].append(seg)
        else:
            turns.append({
                "speaker": seg["speaker"],
                "start": seg["start"],
                "end": seg["end"],
                "segments": [seg],
            })
    return turns


def fmt_timestamp(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def speaker_share(segments: list[dict[str, Any]]) -> dict[str, int]:
    """Word counts per anonymous speaker label."""
    share: dict[str, int] = defaultdict(int)
    for seg in segments:
        words = seg.get("words") or []
        share[seg["speaker"]] += len(words) if words else len(seg["text"].split())
    return dict(share)


def auto_map_speakers(
    segments: list[dict[str, Any]],
    host: str | None,
    guest: str | None,
) -> dict[str, str]:
    """Map anonymous SPEAKER_N labels to real names using word-share heuristic.

    Single-guest interviews: guest is typically the dominant speaker (>55%);
    host asks questions and runs sponsor reads but accumulates fewer words.
    Returns mapping; raises SystemExit if the call is ambiguous and explicit
    --speaker flags are needed.
    """
    share = speaker_share(segments)
    if not host or not guest:
        raise SystemExit(
            "ERROR: sidecar missing host or guest; pass --speaker flags explicitly.\n"
            f"Anonymous labels observed: {sorted(share.keys())} with word counts {share}"
        )
    if len(share) != 2:
        raise SystemExit(
            f"ERROR: expected exactly 2 speakers, found {len(share)}: {share}.\n"
            "Pass --speaker flags explicitly to disambiguate."
        )
    ordered = sorted(share.items(), key=lambda kv: kv[1], reverse=True)
    dominant_label, dominant_words = ordered[0]
    other_label, other_words = ordered[1]
    total = dominant_words + other_words
    if total == 0 or dominant_words / total < 0.55:
        raise SystemExit(
            f"ERROR: speaker share too balanced for dominance heuristic ({share}).\n"
            "Pass --speaker flags explicitly."
        )
    return {dominant_label: guest, other_label: host}


def build_turn_records(turns: list[dict[str, Any]], speaker_map: dict[str, str]) -> list[dict[str, Any]]:
    """Per-turn records matching the shape anchor_chapters.py expects."""
    records: list[dict[str, Any]] = []
    for i, t in enumerate(turns):
        text = " ".join(s["text"] for s in t["segments"])
        first_words = " ".join(text.split()[:10])
        records.append({
            "i": i,
            "timestamp": fmt_timestamp(t["start"]),
            "start": int(t["start"]),
            "end": int(t["end"]),
            "speaker": speaker_map.get(t["speaker"], t["speaker"]),
            "speaker_raw": t["speaker"],
            "first_words": first_words,
        })
    return records


def build_word_records(turns: list[dict[str, Any]], speaker_map: dict[str, str]) -> list[dict[str, Any]]:
    """Flat per-word stream with offsets back to turns."""
    out: list[dict[str, Any]] = []
    for turn_i, t in enumerate(turns):
        for seg in t["segments"]:
            for w in seg.get("words") or []:
                out.append({
                    "word": w["word"],
                    "start": w.get("start"),
                    "end": w.get("end"),
                    "score": w.get("score"),
                    "turn_i": turn_i,
                    "speaker": speaker_map.get(t["speaker"], t["speaker"]),
                })
    return out


def write_transcript_md(turns: list[dict[str, Any]], speaker_map: dict[str, str], path: Path) -> int:
    lines: list[str] = []
    for t in turns:
        text = " ".join(s["text"] for s in t["segments"]).strip()
        if not text:
            continue
        speaker = speaker_map.get(t["speaker"], t["speaker"])
        lines.append(f"{speaker}: {text}")
    body = "\n\n".join(lines) + "\n"
    path.write_text(body, encoding="utf-8")
    return len(lines)


def collect_uncertain_spans(words: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    """Group runs of consecutive low-confidence words into uncertain_spans."""
    spans: list[dict[str, Any]] = []
    current: list[dict[str, Any]] | None = None
    for w in words:
        score = w.get("score")
        if score is not None and score < threshold:
            if current is None:
                current = [w]
            else:
                # Continue current span if same turn and contiguous
                if w["turn_i"] == current[-1]["turn_i"]:
                    current.append(w)
                else:
                    spans.append(_finalize_span(current))
                    current = [w]
        else:
            if current is not None:
                spans.append(_finalize_span(current))
                current = None
    if current is not None:
        spans.append(_finalize_span(current))
    return spans


def _finalize_span(words: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join(w["word"] for w in words)
    return {
        "timestamp": fmt_timestamp(words[0]["start"] or 0),
        "speaker": words[0]["speaker"],
        "text": text,
        "min_score": round(min((w["score"] or 0) for w in words), 3),
        "reason": "low_alignment_confidence",
        "resolution_needed": "verify against audio",
    }


def load_sidecar_meta(episode_dir: Path) -> tuple[str | None, str | None]:
    sidecar_path = episode_dir / "final" / "metadata.sidecar.json"
    if not sidecar_path.exists():
        return None, None
    sc = json.loads(sidecar_path.read_text(encoding="utf-8"))
    hosts = sc.get("episode", {}).get("hosts") or []
    guests = sc.get("episode", {}).get("guests") or []
    host = hosts[0] if hosts else None
    guest = guests[0] if guests else None
    return host, guest


def parse_speaker_flag(values: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for spec in values or []:
        if "=" not in spec:
            raise SystemExit(f"ERROR: --speaker expects LABEL=Name, got: {spec!r}")
        k, v = spec.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("transcription_json", type=Path, help="Path to Substack transcription.json")
    ap.add_argument("episode_dir", type=Path, help="Episode directory (podcast-output/<slug>)")
    ap.add_argument("--speaker", action="append", help="Override label, e.g. SPEAKER_0='Lenny Rachitsky' (repeatable).")
    ap.add_argument("--confidence-threshold", type=float, default=0.4,
                    help="Words with alignment score below this are flagged as uncertain (default 0.4).")
    ap.add_argument("--write-uncertain-spans", action="store_true",
                    help="Merge auto-detected uncertain spans into sidecar verification.uncertain_spans.")
    ap.add_argument("--no-word-timestamps", action="store_true",
                    help="Skip writing _word_timestamps.json (smaller pipeline).")
    args = ap.parse_args(argv)

    episode_dir = args.episode_dir.expanduser().resolve()
    (episode_dir / "source").mkdir(parents=True, exist_ok=True)
    (episode_dir / "working").mkdir(parents=True, exist_ok=True)

    segments = load_segments(args.transcription_json)
    print(f"Loaded {len(segments)} segments from {args.transcription_json}", file=sys.stderr)

    explicit_speakers = parse_speaker_flag(args.speaker)
    if not explicit_speakers:
        host, guest = load_sidecar_meta(episode_dir)
        speaker_map = auto_map_speakers(segments, host, guest)
        print(f"Auto-mapped speakers via dominance: {speaker_map}", file=sys.stderr)
    else:
        speaker_map = explicit_speakers
        print(f"Using explicit speaker map: {speaker_map}", file=sys.stderr)

    turns = coalesce_turns(segments)
    turn_records = build_turn_records(turns, speaker_map)
    word_records = build_word_records(turns, speaker_map)

    transcript_path = episode_dir / "source" / "user-provided-transcript.txt"
    n_turns = write_transcript_md(turns, speaker_map, transcript_path)
    print(f"Wrote {n_turns} turns to {transcript_path}", file=sys.stderr)

    turns_path = episode_dir / "working" / "_turn_timestamps.json"
    turns_path.write_text(json.dumps(turn_records, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote turn timestamps to {turns_path}", file=sys.stderr)

    if not args.no_word_timestamps:
        words_path = episode_dir / "working" / "_word_timestamps.json"
        words_path.write_text(json.dumps(word_records, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {len(word_records)} word timestamps to {words_path}", file=sys.stderr)

    if args.write_uncertain_spans:
        spans = collect_uncertain_spans(word_records, args.confidence_threshold)
        sidecar_path = episode_dir / "final" / "metadata.sidecar.json"
        if sidecar_path.exists():
            sc = json.loads(sidecar_path.read_text(encoding="utf-8"))
            existing = sc.setdefault("verification", {}).setdefault("uncertain_spans", [])
            existing.extend(spans)
            sidecar_path.write_text(json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Appended {len(spans)} uncertain spans to {sidecar_path}", file=sys.stderr)
        else:
            print(f"WARN: --write-uncertain-spans set but {sidecar_path} missing; spans not written", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
