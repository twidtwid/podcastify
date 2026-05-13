#!/usr/bin/env python3
"""Build podcast-transformer/references/terminology_library.json from every
episode's sidecar. The library is the cross-episode source of truth for stable
entities (people, organizations, books, concepts) so we stop re-typing the
same Wikipedia URL and one-line bio for Anthropic / Costco / Whole Foods /
WorkOS / Vanta / etc. on every new episode.

Conflict resolution rules:
- A term's `category` is canonical from the first appearance — if a later
  episode disagrees, we keep the original and log a warning.
- `notes` and `url` prefer the longest / most specific value seen so far.
- `seen_in` accumulates a list of episode slugs that referenced the term,
  so we can later surface "this term appears in 5 episodes" affordances.

Run from repo root:

    python3 podcast-transformer/scripts/build_terminology_library.py

Output: `podcast-transformer/references/terminology_library.json` (overwrites).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    episodes_dir = repo_root / "podcast-output"
    out_path = repo_root / "podcast-transformer" / "references" / "terminology_library.json"

    if not episodes_dir.is_dir():
        print(f"ERROR: {episodes_dir} not found", file=sys.stderr)
        return 2

    library: dict[str, dict] = {}
    conflicts: list[str] = []

    for episode_dir in sorted(episodes_dir.iterdir()):
        sidecar = episode_dir / "final" / "metadata.sidecar.json"
        if not sidecar.exists():
            continue
        slug = episode_dir.name
        sc = json.loads(sidecar.read_text(encoding="utf-8"))
        for entry in sc.get("verification", {}).get("terminology") or []:
            term = entry.get("term", "").strip()
            if not term:
                continue
            existing = library.get(term)
            new_notes = entry.get("notes") or ""
            new_url = entry.get("url") or ""
            new_category = entry.get("category") or "concept"
            if existing is None:
                library[term] = {
                    "term": term,
                    "category": new_category,
                    "notes": new_notes,
                    "url": new_url,
                    "confidence": entry.get("confidence", "primary"),
                    "seen_in": [slug],
                }
            else:
                if existing["category"] != new_category:
                    conflicts.append(f"{term}: category {existing['category']!r} vs {new_category!r} (kept original)")
                if len(new_notes) > len(existing["notes"]):
                    existing["notes"] = new_notes
                if new_url and not existing["url"]:
                    existing["url"] = new_url
                if slug not in existing["seen_in"]:
                    existing["seen_in"].append(slug)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Stable ordering: alphabetize for readable diffs
    ordered = {k: library[k] for k in sorted(library)}
    out_path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {len(ordered)} terms from {sum(1 for d in episodes_dir.iterdir() if (d / 'final/metadata.sidecar.json').exists())} sidecars to {out_path}")
    if conflicts:
        print(f"WARN: {len(conflicts)} category conflicts (resolved by keeping original):", file=sys.stderr)
        for c in conflicts:
            print(f"  {c}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
