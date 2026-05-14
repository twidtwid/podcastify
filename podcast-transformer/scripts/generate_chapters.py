#!/usr/bin/env python3
"""Generate a chapter timeline for an episode that has none.

The transcript browser's left rail is driven by `episode.chapters` in the
sidecar. Publisher pages rarely embed YouTube-style `(00:00) Title` chapter
timestamps in their HTML (Substack/New Yorker/FoundMyFitness/99pi/Tim Ferriss
all skip them), so without this step the chapter rail is empty and the
transcript browser collapses to a single search box.

Strategy: ask the local Ollama model to pick 8-12 chapter boundaries from
the full transcript. The LLM has full context — it sees who introduces a
new topic and where one ends. For each chapter it returns:

  * `title`     — punchy chapter heading (≤8 words)
  * `query`     — a verbatim ~6-12 word phrase from the chapter's first turn
                  (used by anchor_chapters to find the matching transcript
                  turn — substring matching is cheap and deterministic)
  * `start_min` — rough minute marker (helps `anchor_chapters_proportional`
                  pick the right turn when the query matches in multiple
                  places)

Output schema (written to `working/_generated_chapters.json` AND merged
straight into the sidecar's `episode.chapters`):

  [{"start": <seconds>, "timestamp": "MM:SS", "title": str, "query": str}]

Usage:

    python3 scripts/generate_chapters.py <episode_dir>
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from pipeline_config import (
    DRAFT_MODEL as DEFAULT_MODEL,
    OLLAMA_URL,
)


TRANSCRIPT_BUDGET_CHARS = 100_000  # keep room in the 65k-token Gemma ctx
NUM_CTX = 65536
NUM_PREDICT = 3000
MIN_CHAPTERS = 6
MAX_CHAPTERS = 14

SYSTEM_PROMPT = """You segment a podcast transcript into a punchy chapter timeline.

Output STRICT JSON only — no prose, no markdown. Schema:

{
  "chapters": [
    {"start_min": 0, "title": "Cold open", "query": "verbatim phrase that appears in the first turn of this chapter"},
    {"start_min": 4, "title": "Why founders get ousted", "query": "another verbatim phrase"},
    ...
  ]
}

Rules:
- Produce 8-12 chapters that match the natural topic boundaries of the conversation.
- The FIRST chapter MUST have start_min: 0.
- Chapters must be in strictly ascending order by start_min.
- `title` is 3-8 words, no end punctuation, mirrors the chapter's content
  (not generic labels like "Section 1" or "Continued discussion").
- `query` is a VERBATIM substring (6-12 words) copied from the chapter's
  first or second turn of speech. Do not invent phrasing; the downstream
  pipeline substring-matches this against the transcript to find the
  exact turn. Pick something distinctive (proper nouns, specific numbers,
  a memorable verb phrase) — never generic filler.
- Skip ads / pre-roll sponsor reads. Real content starts where the
  episode's substantive conversation begins.
"""


def call_ollama_json(model: str, user_prompt: str) -> str:
    body = json.dumps({
        "model": model,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.2,
            "num_ctx": NUM_CTX,
            "num_predict": NUM_PREDICT,
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("message", {}).get("content", "")


def _parse_json_tolerant(raw: str) -> dict:
    """Strip optional ```json fences, return outermost {...} parsed."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json\n"):
            s = s[5:]
        s = s.rstrip("`").strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"No JSON object found in model output. Head: {raw[:200]!r}")
    return json.loads(s[start : end + 1])


def _format_timestamp(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _normalize_chapters(payload: dict, duration_minutes: int = 0) -> list[dict]:
    raw = payload.get("chapters") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    last_min = -1
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = (entry.get("title") or "").strip()
        query = (entry.get("query") or "").strip()
        try:
            start_min = int(entry.get("start_min"))
        except (TypeError, ValueError):
            continue
        # gemma4 occasionally produces start_min values an order of
        # magnitude past the episode duration (e.g. 300, 430, 800 for a
        # 61-minute episode). Each one then anchors to the final transcript
        # turn and the chapter rail collapses — 10 entries pointing at the
        # same place. Drop anything past the episode's actual length
        # (when known) so the LLM's confusion doesn't poison the rail.
        if duration_minutes > 0 and start_min > duration_minutes:
            continue
        if start_min <= last_min:
            # Maintain strict ascending order; nudge to last+1 minute.
            start_min = last_min + 1
        last_min = start_min
        seconds = max(0, start_min) * 60
        if not title:
            continue
        out.append({
            "start": seconds,
            "timestamp": _format_timestamp(seconds),
            "title": title,
            "query": query,
            "summary": "",
            "tags": [],
        })
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episode_dir", type=Path)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument(
        "--only-when-empty",
        action="store_true",
        default=True,
        help="(default) skip if the sidecar already has chapters from another source",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="run even if the sidecar already has chapters (overwrites them)",
    )
    args = p.parse_args(argv)

    ep = args.episode_dir.resolve()
    sidecar_path = ep / "final" / "metadata.sidecar.json"
    transcript_path = ep / "source" / "user-provided-transcript.txt"
    out_path = ep / "working" / "_generated_chapters.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not sidecar_path.is_file():
        print(f"ERROR: sidecar not found: {sidecar_path}", file=sys.stderr)
        return 2
    if not transcript_path.is_file():
        print("generate_chapters: no transcript on disk; skipping", file=sys.stderr)
        return 0

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    episode = sidecar.setdefault("episode", {})
    existing = episode.get("chapters") or []
    if existing and not args.force:
        print(
            f"generate_chapters: sidecar already has {len(existing)} chapters; skipping",
            file=sys.stderr,
        )
        return 0

    transcript = transcript_path.read_text(encoding="utf-8")
    if not transcript.strip():
        print("generate_chapters: empty transcript; skipping", file=sys.stderr)
        return 0
    trimmed = transcript[:TRANSCRIPT_BUDGET_CHARS]
    truncation_note = (
        ""
        if len(transcript) <= TRANSCRIPT_BUDGET_CHARS
        else f"\n\n[transcript truncated at {TRANSCRIPT_BUDGET_CHARS:,} chars for chapter generation]"
    )

    podcast_title = episode.get("podcast_title", "")
    episode_title = episode.get("title", "")
    duration_seconds = episode.get("duration_seconds") or 0

    duration_minutes = duration_seconds // 60 if duration_seconds else 0
    bound_note = (
        f"All start_min values MUST be integers in [0, {duration_minutes}]. "
        f"The episode is {duration_minutes} minutes long — any start_min "
        f"greater than {duration_minutes} is an error.\n"
        if duration_minutes
        else ""
    )
    user_prompt = (
        (f"Podcast: {podcast_title}\n" if podcast_title else "")
        + (f"Episode: {episode_title}\n" if episode_title else "")
        + (f"Duration: ~{duration_minutes} minutes\n" if duration_minutes else "")
        + bound_note
        + "\nTranscript:\n---\n"
        + trimmed
        + truncation_note
        + "\n---\n\n"
        + f'Return JSON: {{"chapters": [{{start_min, title, query}}, ...]}} '
        + f"with {MIN_CHAPTERS}-{MAX_CHAPTERS} entries."
    )

    print(
        f"Calling Ollama: {args.model} (generate_chapters, ctx={NUM_CTX}, "
        f"{len(user_prompt):,} chars)",
        file=sys.stderr,
    )
    try:
        raw = call_ollama_json(args.model, user_prompt)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"generate_chapters: Ollama call failed: {exc}", file=sys.stderr)
        return 0

    try:
        payload = _parse_json_tolerant(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        debug = ep / "working" / "_generated_chapters_raw.txt"
        debug.write_text(raw, encoding="utf-8")
        print(f"generate_chapters: model returned non-JSON ({exc}); saved {debug}", file=sys.stderr)
        return 0

    chapters = _normalize_chapters(payload, duration_minutes=duration_minutes)
    if len(chapters) < MIN_CHAPTERS:
        print(
            f"generate_chapters: got only {len(chapters)} chapters (need >={MIN_CHAPTERS}); skipping",
            file=sys.stderr,
        )
        return 0

    out_path.write_text(
        json.dumps({"chapters": chapters}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Strip the `query` field from the sidecar copy — that field is for the
    # downstream chapter-anchoring step, not for storage in the schema. Keep
    # the rich form on disk for debugging.
    episode["chapters"] = [
        {k: v for k, v in c.items() if k != "query"}
        for c in chapters
    ]
    sidecar["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Also write the parallel `chapter_queries` array into source/episode.notes.json
    # — podcast_build's build_chapters() pairs sidecar.episode.chapters[i] with
    # notes.chapter_queries[i] for substring → turn anchoring. The LLM picked
    # verbatim phrases from each chapter's opening turn so anchoring is exact.
    notes_path = ep / "source" / "episode.notes.json"
    if notes_path.is_file():
        try:
            notes = json.loads(notes_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            notes = {}
        notes["chapter_queries"] = [c.get("query", "") for c in chapters]
        notes_path.write_text(json.dumps(notes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"generate_chapters: wrote {len(chapters)} chapters", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
