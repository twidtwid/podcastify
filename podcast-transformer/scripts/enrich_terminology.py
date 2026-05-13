#!/usr/bin/env python3
"""Enrich `verification.terminology` entries that have empty notes.

`merge_terminology_urls.py` adds show-notes link candidates that the
populate_terminology LLM missed, but it can only guess the category
("concept") and writes no note — leaving entries like Martin Shkreli
mis-categorized as a concept in the briefing's inspector.

This script scans the sidecar for entries with empty notes (or a
suspiciously generic "concept" category) and runs a per-entry qwen
call (think=False, ~2-3s each) to set the right category and write a
12-25 word description grounded in the transcript context.

Inputs:
  - sidecar at <episode_dir>/final/metadata.sidecar.json
  - transcript at <episode_dir>/source/user-provided-transcript.txt
    (used as context to ground the note)

Usage:
  python3 scripts/enrich_terminology.py <episode_dir>
                                        [--model qwen3.6:35B-a3b-nvfp4]
                                        [--num-ctx 16384]
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
    ENRICH_MODEL as DEFAULT_MODEL,
    ENRICH_NUM_CTX as DEFAULT_NUM_CTX,
    OLLAMA_URL,
)

VALID_CATEGORIES = {
    "person", "company", "organization", "book", "paper",
    "concept", "product", "podcast", "quote",
}

SYSTEM_PROMPT = """You're categorizing and writing a one-line description for ONE named entity that appears in a podcast episode.

Input: the entity name, an optional canonical URL, and a short slice of transcript context.

Task: emit a JSON object with two fields:

```json
{
  "category": "person | company | organization | book | paper | concept | product | podcast | quote",
  "notes": "12-25 word description: WHO they are / WHAT it is / WHY they appear in this episode."
}
```

# Category rules

- A real human → "person" (Martin Shkreli, Marie Krogh, Eric Ries).
- A for-profit business → "company" (Costco, Cloudflare, Quibi).
- A nonprofit / institution / govt entity → "organization" (Anthropic if structured as a foundation, British Thoracic Society).
- A book title → "book". A research paper → "paper".
- A podcast → "podcast".
- A famous quote referenced by name → "quote".
- A product name → "product".
- An abstract idea or term of art → "concept" (financial gravity, shareholder primacy).

# Notes rules

- 12-25 words. One sentence. WHO/WHAT first, then WHY they matter in THIS episode.
- Use the transcript context to ground the note. If the entity was cited as an example, name what it exemplifies.
- Don't lead with "This is..." / "An example of..." — start with the noun. Bad: "An example of a company that…". Good: "Pharma giant whose 100-year industrial-foundation governance…"

# Output

Exactly the JSON object. No markdown fence. No commentary.
"""


def call_ollama(model: str, system: str, user: str, num_ctx: int) -> str:
    body = json.dumps({
        "model": model,
        "stream": False,
        "format": "json",
        "think": False,
        "options": {
            "temperature": 0.3,
            "num_ctx": num_ctx,
            "num_predict": 400,
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
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("message", {}).get("content", "")


def transcript_context(transcript: str, term: str, window: int = 700) -> str:
    """Return a snippet around the first mention of `term` in the transcript.
    Falls back to the first 500 chars if not found."""
    # Try the term verbatim first, then case-insensitive.
    idx = transcript.find(term)
    if idx < 0:
        m = re.search(re.escape(term), transcript, re.IGNORECASE)
        idx = m.start() if m else -1
    if idx < 0:
        return transcript[:500]
    start = max(0, idx - window // 2)
    end = min(len(transcript), idx + window // 2)
    return ("…" if start > 0 else "") + transcript[start:end] + ("…" if end < len(transcript) else "")


def enrich(model: str, term: dict, transcript: str, num_ctx: int) -> dict | None:
    name = term.get("term", "")
    if not name:
        return None
    context = transcript_context(transcript, name)
    user = (
        f"# Entity name\n{name}\n\n"
        f"# Canonical URL (for hints about what this is)\n{term.get('url') or '(none)'}\n\n"
        f"# Transcript context\n{context}\n\n"
        f"Output the JSON now."
    )
    raw = call_ollama(model, SYSTEM_PROMPT, user, num_ctx)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    cat = (payload.get("category") or "").lower().strip()
    notes = (payload.get("notes") or "").strip()
    if cat not in VALID_CATEGORIES:
        # Fall back to concept if the model returned something off-spec
        cat = "concept"
    if not notes:
        return None
    return {"category": cat, "notes": notes}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("episode_dir", type=Path)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    ap.add_argument("--force-all", action="store_true",
                    help="re-enrich every entry, not just ones with empty notes")
    args = ap.parse_args(argv)

    ep = args.episode_dir.resolve()
    sidecar_path = ep / "final" / "metadata.sidecar.json"
    transcript_path = ep / "source" / "user-provided-transcript.txt"
    for p in (sidecar_path, transcript_path):
        if not p.exists():
            print(f"ERROR: {p} not found", file=sys.stderr)
            return 2

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    transcript = transcript_path.read_text(encoding="utf-8")
    terms = sidecar.get("verification", {}).get("terminology", [])

    to_enrich = [t for t in terms if args.force_all or not (t.get("notes") or "").strip()]
    if not to_enrich:
        print("nothing to enrich — all entries have notes.")
        return 0

    print(f"enriching {len(to_enrich)} of {len(terms)} entries via {args.model}...",
          file=sys.stderr)
    fixed = 0
    for i, term in enumerate(to_enrich):
        print(f"  [{i+1}/{len(to_enrich)}] {term.get('term','?')!r}...", file=sys.stderr, end=" ")
        result = enrich(args.model, term, transcript, args.num_ctx)
        if result is None:
            print("(skipped — empty/invalid model output)", file=sys.stderr)
            continue
        old_cat = term.get("category", "")
        term["category"] = result["category"]
        term["notes"] = result["notes"]
        print(f"→ {result['category']!r} (was {old_cat!r})", file=sys.stderr)
        fixed += 1

    if fixed == 0:
        print("no entries were successfully enriched; sidecar untouched.", file=sys.stderr)
        return 0
    sidecar["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"enriched {fixed} entries -> {sidecar_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
