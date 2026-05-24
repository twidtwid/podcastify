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

- Output **25–35 entries total. 35 is a hard ceiling** — once you have emitted 35, stop, even if more entities exist in the transcript. Keep the 35 most click-worthy. Under 20 is too sparse.
- Pick the entities a reader is most likely to not recognize and want context on. Skip household names and one-off mentions when you are near the ceiling.
- Mix of categories — typically: ~8 people, ~12 organizations/companies, ~2–3 books, ~6–8 concepts.

# Hard rules

- **Use the guest's actual phrasing** for concept names. If the guest said "financial gravity", the term is "financial gravity" (not "Financial Gravity" or "the financial-gravity principle").
- **No URLs** — those are merged in by a downstream script from the publisher's show notes. Omit the `url` field entirely from your output.
- **Notes are 12-25 words**. They orient the reader, not summarize the conversation. Bad: "Eric Ries discusses this concept in detail." Good: "The predictable force that drags successful companies into mediocrity once their golden goose attracts butchers."
- **Never exceed 35 entries.** A dense, jargon-heavy episode still gets at most 35 — curate, do not catalog.
- **Output ONLY valid JSON.** First character `{`, last character `}`. No markdown fences. No commentary.
"""

# Hard ceiling on terminology entries. The briefing inspector is a scannable
# rail, not an index — past ~35 chips it stops being useful. A dense technical
# episode can push the draft model to emit 80+ entries and blow the
# `num_predict` budget mid-object; `_parse_terminology` salvages the truncated
# array and `_cap_terms` trims the merged result back to this ceiling.
MAX_TERMS = 35

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


def _parse_terminology(raw: str) -> list[dict]:
    """Return the `terminology` array from the model's JSON output.

    Tolerates a truncated array: when a long-output run hits the
    `num_predict` ceiling mid-object, every COMPLETE object emitted before
    the cutoff is kept and the dangling fragment is discarded — rather than
    hard-failing the whole step on an unterminated array. Raises ValueError
    only when no terminology array can be located at all.
    """
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json\n"):
            s = s[5:]
        s = s.rstrip("`").strip()
    # Fast path: well-formed JSON object.
    try:
        payload = json.loads(s)
        terms = payload.get("terminology")
        if isinstance(terms, list):
            return [t for t in terms if isinstance(t, dict)]
    except json.JSONDecodeError:
        pass
    # Salvage path: walk the terminology array object-by-object so a
    # truncated tail loses only the final incomplete entry.
    key_at = s.find('"terminology"')
    array_start = s.find("[", key_at) if key_at >= 0 else -1
    if array_start < 0:
        raise ValueError("No terminology array found in model output")
    decoder = json.JSONDecoder()
    terms: list[dict] = []
    i = array_start + 1
    n = len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n,":
            i += 1
        if i >= n or s[i] == "]":
            break
        try:
            obj, end = decoder.raw_decode(s, i)
        except json.JSONDecodeError:
            break  # dangling truncated object — keep what came before it
        if isinstance(obj, dict):
            terms.append(obj)
        i = end
    return terms


def _cap_terms(terms: list[dict], limit: int = MAX_TERMS) -> list[dict]:
    """Trim an over-long terminology list to `limit`, in original order.

    Url-bearing entries (publisher show-notes links) are prioritized: every
    one is kept before any un-linked LLM entry, in original order. But the
    `limit` is a HARD ceiling even for url-bearing entries — re-running an
    episode whose prior sidecar already carried 35+ linked entries would
    otherwise silently bypass the cap and overflow the briefing inspector.
    When url-bearing entries themselves exceed `limit`, the first `limit`
    of them are kept and the rest dropped along with all non-linked entries.
    """
    if len(terms) <= limit:
        return terms
    url_bearing = [t for t in terms if t.get("url")]
    if len(url_bearing) >= limit:
        # Even publisher-curated entries get capped past the ceiling.
        kept_ids = {id(t) for t in url_bearing[:limit]}
    else:
        kept_ids = {id(t) for t in url_bearing}
        for t in terms:
            if len(kept_ids) >= limit:
                break
            if not t.get("url"):
                kept_ids.add(id(t))
    return [t for t in terms if id(t) in kept_ids]


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
        new_terms = _parse_terminology(raw)
    except ValueError as e:
        debug_path = ep / "working" / "_terminology_draft_raw.txt"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(raw, encoding="utf-8")
        print(f"ERROR parsing model output: {e}", file=sys.stderr)
        print(f"  raw saved to {debug_path}", file=sys.stderr)
        return 2

    if not new_terms:
        debug_path = ep / "working" / "_terminology_draft_raw.txt"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(raw, encoding="utf-8")
        print("ERROR: model produced no usable terminology entries", file=sys.stderr)
        print(f"  raw saved to {debug_path}", file=sys.stderr)
        return 2

    print(f"existing: {len(existing_terms)} entries, new from LLM: {len(new_terms)} entries",
          file=sys.stderr)
    merged = merge_terms(existing_terms, new_terms)
    capped = _cap_terms(merged)
    if len(capped) < len(merged):
        print(f"capped {len(merged)} -> {len(capped)} entries (MAX_TERMS={MAX_TERMS})",
              file=sys.stderr)
    merged = capped
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
