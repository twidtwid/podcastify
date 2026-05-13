#!/usr/bin/env python3
"""Merge URLs from `working/_entity_links.json` into
`final/metadata.sidecar.json` under `verification.terminology[].url`.

The renderer's inspector turns any terminology entry with a `url` into a
clickable external link. Show-notes link lists are the easiest source for
canonical URLs (Wikipedia, Anthropic.com, etc.).

Matching strategy:
- Case-insensitive substring match between the link label and the
  terminology `name` (after stripping common decorators: "— Wikipedia",
  "by <author>", "(podcast)", etc.).
- If terminology has no entry for a label, optionally append it as a new
  `confidence: medium` term so it still shows up in the inspector.

Usage:

    python3 scripts/merge_terminology_urls.py <episode_dir> [--add-missing]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CLEAN_TRAIL_RE = re.compile(
    r"\s*(?:[—\-–]\s*(?:wikipedia|podcast|book site|book|newsletter|substack|website)|"
    r"\(podcast\)|\(wikipedia\))\s*$",
    re.IGNORECASE,
)
BY_AUTHOR_RE = re.compile(r"\s+by\s+[A-Z][\w'.\- ]+$")


def clean_label(label: str) -> str:
    label = CLEAN_TRAIL_RE.sub("", label).strip()
    label = BY_AUTHOR_RE.sub("", label).strip()
    return label


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episode_dir", type=Path)
    p.add_argument("--add-missing", action="store_true",
                   help="append terminology entries for unmatched labels")
    args = p.parse_args(argv)

    ep = args.episode_dir.resolve()
    links_path = ep / "working" / "_entity_links.json"
    sidecar_path = ep / "final" / "metadata.sidecar.json"

    if not links_path.exists():
        print(f"ERROR: {links_path} not found", file=sys.stderr)
        return 2
    if not sidecar_path.exists():
        print(f"ERROR: {sidecar_path} not found", file=sys.stderr)
        return 2

    links = json.loads(links_path.read_text(encoding="utf-8"))
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))

    verification = sidecar.setdefault("verification", {})
    terminology: list[dict] = verification.setdefault("terminology", [])

    # Index existing terms (lowercase term → entry). The schema spec uses
    # `term` as the canonical key; tolerate legacy `name` for back-compat.
    def _key(t: dict) -> str:
        return ((t.get("term") or t.get("name") or "")).lower()
    by_name = {_key(t): t for t in terminology}

    matched = 0
    added = 0
    skipped = 0

    for link in links:
        label = clean_label(link["label"])
        if not label:
            continue
        url = link["url"]
        existing = by_name.get(label.lower())

        # Try a looser match — but ONLY when the label is short enough that
        # it's likely the canonical name (modulo decorators), not a sentence
        # that happens to mention the entity. "Eric Ries — Incorruptible
        # book site" → matches "Eric Ries". But "Reflections on a movement |
        # Eric Ries (creator of the Lean Startup methodology)" must NOT
        # match Eric Ries, because that URL points to a different page.
        if existing is None and len(label) <= 40:
            for name_lc, term in by_name.items():
                if not name_lc:
                    continue
                # Substring match in either direction, but only short labels
                if name_lc in label.lower() or label.lower() in name_lc:
                    existing = term
                    break

        if existing is not None:
            if not existing.get("url"):
                existing["url"] = url
                matched += 1
            else:
                skipped += 1
        elif args.add_missing:
            new_term = {
                "term": label,
                "category": "concept",
                "confidence": "medium",
                "notes": "",
                "url": url,
            }
            terminology.append(new_term)
            by_name[label.lower()] = new_term
            added += 1
        else:
            skipped += 1

    sidecar["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"matched {matched} URLs, added {added}, skipped {skipped} (of {len(links)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
