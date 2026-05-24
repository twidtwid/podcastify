#!/usr/bin/env python3
"""Map anonymous diarization labels (`SPEAKER_00`, `SPEAKER_01`, ...) to the
real participant names for an episode.

Most providers arrive with usable speaker labels already: the New Yorker and
tim.blog transcripts carry `HOST:` / `Tim Ferriss:` lines, and a Substack
post with a populated `speaker_map` is resolved to names at ingest time
(`url_ingest.substack_json_to_turns`). But Substack diarizes every episode
whether or not the publisher names the speakers — when the publisher skips
that step, `speaker_map` is `null` and the transcript turns carry raw
`SPEAKER_NN` labels. The Dwarkesh Podcast is the first supported provider
that ships transcripts this way.

This step closes that gap. It runs AFTER `resolve_speakers.py` (so the host
and headline guests are known) and BEFORE `sidecar_init` / `draft_notes`, so
every downstream stage — the briefing, the annotated transcript, the notes
draft — sees real names.

Behaviour:
  - Reads `source/transcript.turns.json` (and keeps `user-provided-transcript.txt`
    in sync with it).
  - If no generic `SPEAKER_NN` labels are present, this is a NO-OP exit 0 —
    every named-speaker provider is untouched and carries zero regression risk.
  - Otherwise, asks the local model which label is which known participant,
    validates the answer against the known names (a hallucinated name is
    dropped, never written), and rewrites the turns.
  - With exactly two generic labels and two participants, falls back to the
    "host opens the show" convention when the model is unsure.

Usage:
    python3 scripts/resolve_speaker_aliases.py <episode_dir> \\
        --host "Host Name" --guest "Guest Name" [--guest ...] [--model M]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from pipeline_config import (
    DRAFT_MODEL as DEFAULT_MODEL,
    OLLAMA_URL,
)

# A diarization placeholder label: bare `SPEAKER`, or `SPEAKER` + an optional
# separator + digits (`SPEAKER_00`, `SPEAKER 1`, `SPEAKER-12`). Case-insensitive.
GENERIC_LABEL_RE = re.compile(r"^SPEAKER(?:[ _-]?\d+)?$", re.IGNORECASE)

# How much labeled transcript to show the model. ~12k chars ≈ 10 minutes of
# dialog — enough turns from every speaker for the model to tell them apart.
TRANSCRIPT_HEAD_CHARS = 12000
NUM_CTX = 16384
NUM_PREDICT = 600

SYSTEM_PROMPT = """You map anonymous transcript speaker labels to real people.

You are given the list of people in a podcast episode (the host and the \
guest(s)) and an excerpt of the transcript where each turn is tagged with an \
anonymous label like SPEAKER_00. Decide which label belongs to which person.

Output STRICT JSON only — no prose, no markdown. Schema:

{"SPEAKER_00": "Full Name", "SPEAKER_01": "Full Name"}

Rules:
- Use ONLY names from the supplied participant list. Never invent a name.
- The HOST asks the questions, frames the episode, and usually speaks first.
- The GUEST gives the long, substantive answers.
- Map only labels you are confident about. Omit a label entirely if unsure.
- If you cannot tell anyone apart, return an empty object {}.
"""


def call_ollama_json(model: str, user_prompt: str) -> str:
    body = json.dumps({
        "model": model,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_ctx": NUM_CTX,
            "num_predict": NUM_PREDICT,
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("message", {}).get("content", "")


def _parse_json_tolerant(raw: str) -> dict:
    """Parse model output that may be wrapped in a markdown fence."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json\n"):
            s = s[5:]
        s = s.rstrip("`").strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"No JSON object in model output. Head: {raw[:200]!r}")
    return json.loads(s[start : end + 1])


def is_generic_label(label: str) -> bool:
    return bool(GENERIC_LABEL_RE.match((label or "").strip()))


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def generic_labels_in_order(turns: list[dict]) -> list[str]:
    """Distinct generic labels, in first-appearance order."""
    seen: list[str] = []
    for turn in turns:
        label = (turn.get("speaker") or "").strip()
        if is_generic_label(label) and label not in seen:
            seen.append(label)
    return seen


def build_user_prompt(host: str, guests: list[str], labels: list[str],
                      transcript_head: str) -> str:
    people = []
    if host:
        people.append(f"- {host} (host)")
    for guest in guests:
        people.append(f"- {guest} (guest)")
    return "\n".join([
        "Participants in this episode:",
        *people,
        "",
        f"Anonymous labels to map: {', '.join(labels)}",
        "",
        "Transcript excerpt:",
        "---",
        transcript_head,
        "---",
        "",
        'Return ONLY the JSON object mapping each label to a participant name.',
    ])


def validate_map(raw_map: dict, labels: list[str],
                 participants: list[str]) -> dict[str, str]:
    """Keep only entries that map a known generic label to a known participant.

    A hallucinated name (not in the participant list) is dropped rather than
    written into the transcript — a wrong identity poisons every downstream
    stage, so an unmapped label is strictly safer than a guessed one.
    """
    by_lower = {p.lower(): p for p in participants if p}
    label_set = set(labels)
    clean: dict[str, str] = {}
    for label, name in (raw_map or {}).items():
        label = str(label).strip()
        name = str(name).strip()
        if label not in label_set:
            continue
        canonical = by_lower.get(name.lower())
        if canonical:
            clean[label] = canonical
    return clean


def deterministic_two_speaker_map(labels: list[str], host: str,
                                  guests: list[str]) -> dict[str, str]:
    """Fallback for the common two-person interview the model couldn't call.

    With exactly two generic labels and exactly two participants, the host
    almost always speaks first. `labels` is in first-appearance order, so the
    first label is the host and the second is the guest.
    """
    if len(labels) != 2 or not host or len(guests) != 1:
        return {}
    return {labels[0]: host, labels[1]: guests[0]}


def merge_turns(turns: list[dict]) -> list[dict]:
    """Concatenate consecutive same-speaker turns into one and recount words."""
    merged: list[dict] = []
    for turn in turns:
        speaker = turn.get("speaker")
        text = (turn.get("text") or "").strip()
        if merged and merged[-1].get("speaker") == speaker:
            prev = merged[-1]
            prev["text"] = f"{prev['text']} {text}".strip()
            if turn.get("end") is not None:
                prev["end"] = turn["end"]
        else:
            merged.append(dict(turn))
    for turn in merged:
        if "word_count" in turn:
            turn["word_count"] = _word_count(turn.get("text", ""))
    return merged


def apply_aliases(turns: list[dict], alias_map: dict[str, str]) -> list[dict]:
    relabeled = []
    for turn in turns:
        turn = dict(turn)
        label = (turn.get("speaker") or "").strip()
        if label in alias_map:
            turn["speaker"] = alias_map[label]
        relabeled.append(turn)
    return merge_turns(relabeled)


def turns_to_transcript(turns: list[dict]) -> str:
    lines = [f"{t.get('speaker', 'SPEAKER')}: {t.get('text', '')}".strip()
             for t in turns]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episode_dir", type=Path)
    p.add_argument("--host", default="")
    p.add_argument("--guest", action="append", default=[])
    p.add_argument("--model", default=DEFAULT_MODEL)
    args = p.parse_args(argv)

    ep = args.episode_dir.resolve()
    turns_path = ep / "source" / "transcript.turns.json"
    transcript_path = ep / "source" / "user-provided-transcript.txt"

    if not turns_path.is_file():
        # No structured turns — nothing diarized to relabel.
        print("resolve_speaker_aliases: no transcript.turns.json; skipping",
              file=sys.stderr)
        return 0

    try:
        turns = json.loads(turns_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"resolve_speaker_aliases: turns.json unreadable ({exc}); skipping",
              file=sys.stderr)
        return 0
    if not isinstance(turns, list) or not turns:
        return 0

    labels = generic_labels_in_order(turns)
    if not labels:
        # Every named-speaker provider lands here — a clean no-op.
        print("resolve_speaker_aliases: no generic speaker labels; skipping",
              file=sys.stderr)
        return 0

    host = (args.host or "").strip()
    guests = [g.strip() for g in args.guest if g and g.strip()]
    participants = ([host] if host else []) + guests
    if len(participants) < 2:
        print("resolve_speaker_aliases: fewer than 2 known participants; "
              f"leaving {len(labels)} label(s) as-is", file=sys.stderr)
        return 0

    transcript_head = ""
    if transcript_path.is_file():
        transcript_head = transcript_path.read_text(encoding="utf-8")[:TRANSCRIPT_HEAD_CHARS]

    alias_map: dict[str, str] = {}
    if transcript_head:
        user_prompt = build_user_prompt(host, guests, labels, transcript_head)
        print(f"Calling Ollama: {args.model} (resolve_speaker_aliases, "
              f"{len(labels)} labels, {len(participants)} participants)",
              file=sys.stderr)
        try:
            raw = call_ollama_json(args.model, user_prompt)
            alias_map = validate_map(_parse_json_tolerant(raw), labels, participants)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"resolve_speaker_aliases: model call failed ({exc})",
                  file=sys.stderr)

    if not alias_map:
        # The model gave nothing usable — try the host-opens-the-show fallback.
        alias_map = deterministic_two_speaker_map(labels, host, guests)
        if alias_map:
            print("resolve_speaker_aliases: model unsure; used the "
                  "two-speaker first-speaker-is-host fallback", file=sys.stderr)

    if not alias_map:
        print(f"resolve_speaker_aliases: could not map any of {labels}; "
              "leaving transcript as-is", file=sys.stderr)
        return 0

    new_turns = apply_aliases(turns, alias_map)
    turns_path.write_text(
        json.dumps(new_turns, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    transcript_path.write_text(turns_to_transcript(new_turns), encoding="utf-8")
    (ep / "working" / "_speaker_aliases.json").write_text(
        json.dumps(alias_map, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    mapped = ", ".join(f"{k}→{v}" for k, v in alias_map.items())
    unmapped = [lbl for lbl in labels if lbl not in alias_map]
    print(f"resolve_speaker_aliases: mapped {mapped}"
          + (f"; left as-is: {', '.join(unmapped)}" if unmapped else ""),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
