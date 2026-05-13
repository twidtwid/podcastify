#!/usr/bin/env python3
"""Populate `verification.terminology[]` in the sidecar by asking a local
LLM to enumerate every named entity worth linking in the episode.

The renderer's inspector turns each terminology entry into a clickable
chip in the briefing. The hand-crafted reference for this episode has ~30
entries spanning people, organizations, books, and concepts — each with a
short note that explains why the reader should know about it. The
show-notes link harvester only catches the ~16 entries the publisher
linked. This script closes that gap.

What it does:
  1. Reads the transcript, the chapter outline, and any existing
     terminology already in the sidecar (e.g. show-notes URL entries
     written by `merge_terminology_urls.py`).
  2. Calls Ollama (gemma4:e4b-nvfp4 by default, format=json) with a
     focused prompt that asks for a single JSON object
     `{"terminology": [...]}` of 25-35 entries: `{term, category,
     confidence, notes}`.
  3. Merges results into the sidecar:
       - existing entries (matched case-insensitively on `term`) keep
         their `url` and any richer fields they already had;
       - new entries are appended with their LLM-authored notes.

Usage:
  python3 scripts/populate_terminology.py <episode_dir> [--model M]
                                          [--num-ctx N] [--use-cli|--use-api]

By default this script uses the SAME backend as draft_notes.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from pipeline_config import (
    TERMINOLOGY_MODEL as DEFAULT_MODEL,
    TERMINOLOGY_NUM_CTX as DEFAULT_NUM_CTX,
    OLLAMA_URL,
)

SYSTEM_PROMPT = """You are populating the `verification.terminology` array for a podcast briefing page.

The briefing's right-column "inspector" shows every entry as a clickable chip with a short note. A reader scanning the briefing should be able to click any unfamiliar name and see "ah, that's the one-line context I needed."

# Schema (output exactly this shape)

```json
{
  "terminology": [
    {
      "term": "canonical spelling, capitalized correctly",
      "category": "person | organization | company | book | paper | concept | product | podcast",
      "confidence": "primary",
      "notes": "one-sentence description: WHO they are / WHAT it is / WHY the listener should care. 12–25 words."
    }
  ]
}
```

# What to include

- **People named in the episode**: hosts, guests, executives, founders, historical figures, authors. Use the full canonical name (e.g. "Marie Krogh", not "Marie").
- **Companies and organizations**: any business, foundation, or institution the guest references, including ones used as examples (Anthropic, Novo Nordisk, Cloudflare, Groupon, Vectura, Carl Zeiss, Costco). Use "organization" for nonprofits/foundations, "company" for for-profits.
- **Books, papers**: titles mentioned, with author context in the notes if relevant.
- **Concepts**: distinctive terminology the guest uses or coins — "financial gravity", "Long-Term Benefit Trust", "shareholder primacy", "industrial foundation", "torchbearers", "culture bank". Concepts that exist outside the guest's framework (Conway's law, public benefit corporation) also belong here.

# Required counts

- Aim for **25–35 entries** total. Cover every distinct entity worth a click. Under 20 is too sparse; over 40 is over-cataloging.
- Mix of categories — typically: ~8 people, ~12 organizations/companies, ~2–3 books, ~6–8 concepts.

# Hard rules

- **Use the guest's actual phrasing** for concept names. If the guest said "financial gravity", the term is "financial gravity" (not "Financial Gravity" or "the financial-gravity principle").
- **No URLs** — those are merged in by a downstream script from the publisher's show notes. Omit the `url` field entirely from your output.
- **Notes are 12-25 words**. They orient the reader, not summarize the conversation. Bad: "Eric Ries discusses this concept in detail." Good: "The predictable force that drags successful companies into mediocrity once their golden goose attracts butchers."
- **Output ONLY valid JSON.** First character `{`, last character `}`. No markdown fences. No commentary.
"""

USER_TEMPLATE = """# Episode metadata

Title: {title}
Podcast: {podcast_title}
Host: {host}
Guest: {guest}

# Existing terminology (already in sidecar — DO include them in your output, with proper category and note for each)

{existing_terms}

# Show-notes link candidates (the publisher linked these — INCLUDE every entity from this list in your output with a real category. Drop a label only if it's clearly not an entity worth featuring.)

{link_candidates}

# Chapter outline

{chapters}

# Verified transcript

{transcript}

---

Output the JSON object now."""


def call_ollama(model: str, system: str, user: str, num_ctx: int) -> str:
    body = json.dumps({
        "model": model,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.3,
            "num_ctx": num_ctx,
            "num_predict": 6000,
        },
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("message", {}).get("content", "")


def fmt_chapters(sidecar: dict) -> str:
    chapters = sidecar.get("episode", {}).get("chapters") or []
    return "\n".join(
        f"- {c.get('timestamp', '')} {c.get('title', '')}" for c in chapters
    ) or "(no chapters)"


def fmt_existing(terms: list[dict]) -> str:
    if not terms:
        return "(none yet)"
    parts = []
    for t in terms:
        name = t.get("term") or t.get("name") or "?"
        cat = t.get("category", "?")
        parts.append(f"- {name} ({cat})")
    return "\n".join(parts)


def normalize_term_key(s: str) -> str:
    """Loose match: lowercase, strip articles, strip trailing 'Corporation'/'Inc.'."""
    s = s.lower().strip()
    s = re.sub(r"^(the|a|an)\s+", "", s)
    s = re.sub(r"\s+(corporation|corp|inc\.?|ltd\.?|group|llc)$", "", s)
    return s.strip()


def merge_terms(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge new entries into existing. Match on normalized `term`; existing
    entries are preserved (URL etc.) but get their `notes` upgraded when
    the new note is non-empty AND the existing one is empty."""
    by_key: dict[str, dict] = {}
    for t in existing:
        name = t.get("term") or t.get("name")
        if not name:
            continue
        # Normalize to `term` field even if the entry came in with `name`.
        t.setdefault("term", name)
        if "name" in t and "term" in t:
            t.pop("name", None)
        by_key[normalize_term_key(name)] = t

    for t in new:
        name = t.get("term", "").strip()
        if not name:
            continue
        key = normalize_term_key(name)
        if key in by_key:
            existing_t = by_key[key]
            # Upgrade empty fields from the new draft
            if not existing_t.get("notes") and t.get("notes"):
                existing_t["notes"] = t["notes"]
            if not existing_t.get("category") or existing_t.get("category") == "concept":
                # Promote a more specific category if the LLM disagreed with our
                # "concept" default from merge_terminology_urls.py
                if t.get("category") and t["category"] != "concept":
                    existing_t["category"] = t["category"]
        else:
            entry = {
                "term": name,
                "category": t.get("category", "concept"),
                "confidence": t.get("confidence", "primary"),
                "notes": (t.get("notes") or "").strip(),
            }
            by_key[key] = entry

    # Preserve insertion order: existing first, then new
    out: list[dict] = []
    seen: set[str] = set()
    for t in existing:
        key = normalize_term_key(t.get("term") or t.get("name") or "")
        if key and key not in seen:
            seen.add(key)
            out.append(by_key[key])
    for t in new:
        key = normalize_term_key(t.get("term", ""))
        if key and key not in seen:
            seen.add(key)
            out.append(by_key[key])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("episode_dir", type=Path)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the merged terminology without writing")
    args = ap.parse_args(argv)

    ep = args.episode_dir.resolve()
    sidecar_path = ep / "final" / "metadata.sidecar.json"
    transcript_path = ep / "source" / "user-provided-transcript.txt"
    if not sidecar_path.exists() or not transcript_path.exists():
        print(f"ERROR: required files missing under {ep}", file=sys.stderr)
        return 2

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    existing_terms = sidecar.get("verification", {}).get("terminology", []) or []
    episode_meta = sidecar.get("episode", {})

    links_path = ep / "working" / "_entity_links.json"
    link_candidates: list[dict] = []
    if links_path.exists():
        try:
            link_candidates = json.loads(links_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    def _fmt_links(links: list[dict]) -> str:
        if not links:
            return "(none provided)"
        return "\n".join(f"- {l.get('label','?')}  →  {l.get('url','')}" for l in links)

    user_prompt = USER_TEMPLATE.format(
        title=episode_meta.get("title", "(no title)"),
        podcast_title=episode_meta.get("podcast_title", ""),
        host=", ".join(episode_meta.get("hosts") or []) or "(unknown)",
        guest=", ".join(episode_meta.get("guests") or []) or "(unknown)",
        existing_terms=fmt_existing(existing_terms),
        link_candidates=_fmt_links(link_candidates),
        chapters=fmt_chapters(sidecar),
        transcript=transcript_path.read_text(encoding="utf-8"),
    )

    print(f"Calling Ollama: {args.model} ({len(user_prompt):,} chars)", file=sys.stderr)
    raw = call_ollama(args.model, SYSTEM_PROMPT, user_prompt, args.num_ctx)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        debug_path = ep / "working" / "_terminology_draft_raw.txt"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(raw, encoding="utf-8")
        print(f"ERROR parsing model output: {e}", file=sys.stderr)
        print(f"  raw saved to {debug_path}", file=sys.stderr)
        return 2

    new_terms = payload.get("terminology") or []
    if not isinstance(new_terms, list):
        print(f"ERROR: model returned non-list terminology: {type(new_terms).__name__}", file=sys.stderr)
        return 2

    print(f"existing: {len(existing_terms)} entries, new from LLM: {len(new_terms)} entries",
          file=sys.stderr)
    merged = merge_terms(existing_terms, new_terms)
    print(f"after merge: {len(merged)} entries", file=sys.stderr)

    if args.dry_run:
        print(json.dumps(merged, indent=2, ensure_ascii=False))
        return 0

    sidecar.setdefault("verification", {})["terminology"] = merged
    sidecar["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    sidecar_path.write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(merged)} terminology entries -> {sidecar_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
