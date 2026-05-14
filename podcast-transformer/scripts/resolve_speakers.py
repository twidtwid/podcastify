#!/usr/bin/env python3
"""Ask the local Ollama model who hosted the episode and who the guest(s) were.

Replaces the prose-mining heuristics that lived in `parse_source.derive_guest`
/ `derive_host` and (later) the URL-slug / title-pipe fall-backs. The signal
that's actually reliable is the conversation itself: the host introduces
themselves and their guest in the opening minute, and the publisher's title
either names the guest outright or makes them the topic. A small structured
JSON call on that material is more robust than any regex.

Inputs (all already on disk by the time this runs):
  - `<episode_dir>/final/metadata.sidecar.json` — for episode.title, podcast_title
  - `<episode_dir>/source/user-provided-transcript.txt` — for the opening turns

Output:
  - `<episode_dir>/working/_speakers.json` with:
        {"host": "Name or empty string", "guests": ["Name", ...]}

extract_one reads this file and feeds the values to `sidecar.py init` as the
fallback for `--host` / `--guest` (CLI overrides still win). If the model is
uncertain it must return empty values — a wrong identity poisons every
downstream stage of the briefing.

Usage:
    python3 scripts/resolve_speakers.py <episode_dir>
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from pipeline_config import (
    DRAFT_MODEL as DEFAULT_MODEL,
    OLLAMA_URL,
)


# num_ctx must comfortably accommodate the system prompt + transcript head +
# room for the model to actually produce output. 8192 with num_predict=400
# was tight enough that gemma4:e4b-nvfp4 sometimes emitted an empty
# response on a 6,000-char head (observed empirically with the
# Lenny/Eric Ries episode). 16384 gives the model breathing room.
#
# TRANSCRIPT_HEAD_CHARS = 12000 covers ~10 minutes of dialog, which is
# enough to catch the main guest's self-introduction even when the episode
# opens with a 4-5 minute host monologue or a Jared-Wilson-style tangential
# interview before the headline interview begins (99pi/enshittification
# introduces Cory Doctorow at char 9553).
TRANSCRIPT_HEAD_CHARS = 12000
NUM_CTX = 16384
NUM_PREDICT = 2000

SYSTEM_PROMPT = """You identify the HOST and HEADLINE GUEST(s) of a single podcast episode from the title and the opening minutes of the transcript.

Output STRICT JSON only — no prose, no markdown, no preamble. Schema:

{
  "host": "Full Name" | "",
  "guests": ["Full Name", ...]
}

Rules:
- The HOST is the show's recurring interviewer/anchor — the voice that opens with "I'm <Name>, welcome to <show>", or "This is <show>. I'm <Name>", or signs off as the host. Co-hosts and regular producers (a name introduced as "producer <Name>" or "my co-host <Name>") belong in `guests` only when they are NOT a recurring fixture of the show.
- HEADLINE GUESTS are the substantive interview subjects this episode is built around. Their conversation drives the episode. They almost always self-introduce ("Hi, I'm <Name>, I'm a <bio>") and the host names them as the guest in the open.
- A person interviewed for a sentence or two as supporting color (e.g. a farmer cited as evidence, an analyst quoted briefly) is NOT a headline guest — leave them out.
- An ensemble panel episode (3+ regular contributors trading lines) is a host monologue equivalent — return guests: [].
- Use names exactly as the speakers introduce themselves — keep honorifics ("Dr.") if used.
- If you cannot identify the host or a headline guest with high confidence from the supplied material, return an empty string / empty list rather than guessing.
- Never invent names that don't appear in the transcript or title.
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
        OLLAMA_URL,
        data=body,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("message", {}).get("content", "")


def _parse_json_tolerant(raw: str) -> dict:
    """Parse model output that may be wrapped in markdown fences.

    Despite Ollama's `format: "json"` mode, gemma4:e4b-nvfp4 occasionally
    wraps the payload in ```json … ``` (observed live against the Lenny /
    Eric Ries transcript). Strip the fence and extract the outermost JSON
    object before parsing. Raises if no `{...}` block is found.
    """
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


def build_user_prompt(podcast_title: str, episode_title: str, url: str, transcript_head: str) -> str:
    parts = []
    if podcast_title:
        parts.append(f"Podcast: {podcast_title}")
    if episode_title:
        parts.append(f"Episode title: {episode_title}")
    if url:
        parts.append(f"URL: {url}")
    parts.append("")
    parts.append("Opening minutes of the transcript:")
    parts.append("---")
    parts.append(transcript_head)
    parts.append("---")
    parts.append("")
    parts.append('Return ONLY the JSON object {"host": ..., "guests": [...]}.')
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episode_dir", type=Path)
    p.add_argument("--model", default=DEFAULT_MODEL)
    # extract_one passes these explicitly because resolve_speakers runs BEFORE
    # sidecar_init — the sidecar doesn't exist yet. We still fall back to the
    # sidecar if it's already there (re-running an existing episode).
    p.add_argument("--title", default="")
    p.add_argument("--podcast-title", default="")
    p.add_argument("--episode-url", default="")
    args = p.parse_args(argv)

    ep = args.episode_dir.resolve()
    sidecar_path = ep / "final" / "metadata.sidecar.json"
    transcript_path = ep / "source" / "user-provided-transcript.txt"
    out_path = ep / "working" / "_speakers.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    podcast_title = args.podcast_title
    episode_title = args.title
    url = args.episode_url
    if (not podcast_title or not episode_title or not url) and sidecar_path.is_file():
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            episode = sidecar.get("episode", {}) or {}
            podcast_title = podcast_title or episode.get("podcast_title", "") or ""
            episode_title = episode_title or episode.get("title", "") or ""
            url = url or episode.get("url", "") or ""
        except json.JSONDecodeError:
            pass

    transcript_head = ""
    if transcript_path.is_file():
        transcript_head = transcript_path.read_text(encoding="utf-8")[:TRANSCRIPT_HEAD_CHARS]
    if not transcript_head:
        # No transcript yet — emit empty result so downstream just sees the
        # bundle-declared values (or nothing). Don't fabricate.
        out_path.write_text(
            json.dumps({"host": "", "guests": []}, indent=2) + "\n",
            encoding="utf-8",
        )
        print("resolve_speakers: no transcript yet; wrote empty result", file=sys.stderr)
        return 0

    user_prompt = build_user_prompt(podcast_title, episode_title, url, transcript_head)
    print(
        f"Calling Ollama: {args.model} (resolve_speakers, ctx={NUM_CTX}, "
        f"{len(user_prompt):,} chars)",
        file=sys.stderr,
    )
    try:
        raw = call_ollama_json(args.model, user_prompt)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"resolve_speakers: Ollama call failed: {exc}", file=sys.stderr)
        out_path.write_text(
            json.dumps({"host": "", "guests": []}, indent=2) + "\n",
            encoding="utf-8",
        )
        return 0

    try:
        parsed = _parse_json_tolerant(raw)
    except (json.JSONDecodeError, ValueError):
        # Save raw for debugging; emit empty so the pipeline can keep moving.
        (ep / "working" / "_speakers_raw.txt").write_text(raw, encoding="utf-8")
        print("resolve_speakers: model returned non-JSON; wrote empty result", file=sys.stderr)
        parsed = {"host": "", "guests": []}

    host = (parsed.get("host") or "").strip()
    guests_raw = parsed.get("guests") or []
    if not isinstance(guests_raw, list):
        guests_raw = []
    guests = [str(g).strip() for g in guests_raw if str(g).strip()]
    result = {"host": host, "guests": guests}
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"resolve_speakers: host={host!r}, guests={guests}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
