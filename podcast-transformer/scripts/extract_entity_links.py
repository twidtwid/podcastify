#!/usr/bin/env python3
"""Extract entity → URL pairs from a combined source file (or a separate
show-notes file) so the renderer's inspector can link entity names out to
canonical pages.

Picks up two common formats:

    • Vectura: https://en.wikipedia.org/wiki/Vectura
    - [Mary Parker Follett](https://en.wikipedia.org/wiki/Mary_Parker_Follett)

Plus YouTube-style truncated "...more" links (https://example.com/p/ho...) are
discarded — anything ending in `…`, `...`, or that's an obvious truncation.

Output: `working/_entity_links.json` — a list of `{label, url}` pairs.

Usage:

    python3 scripts/extract_entity_links.py <episode_dir> [--source FILE]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# • Label: https://...  /  - Label: https://...
BULLET_RE = re.compile(r"^\s*[••\-\*]\s*([^:\n]{2,80}?):\s*(https?://\S+)")
# [Label](https://...)
MD_LINK_RE = re.compile(r"\[([^\]]{2,80})\]\((https?://[^)\s]+)\)")
# Drop truncated YouTube show-notes links: end with ... or … (often with /)
TRUNCATED_RE = re.compile(r"(\.\.\.|…)/?$")

# Social-link section headers in publisher show notes are NOT entity names —
# they're labels for "Where to find <person>" link blocks. The matching X /
# LinkedIn / Newsletter / etc. URL belongs to the *person*, not to a concept
# called "X" or "Newsletter".
SOCIAL_LINK_LABELS = {
    "x", "twitter", "linkedin", "website", "site", "newsletter", "podcast",
    "youtube", "facebook", "instagram", "tiktok", "threads", "github",
    "email", "rss", "blog", "substack", "patreon", "discord",
    # publisher-page navigation labels:
    "episode transcript", "archive of all", "official transcript",
    "archive", "transcript",
}


def is_truncated_url(u: str) -> bool:
    """True if the URL was sliced off by a 'more' button — ends in `...`,
    `…`, has a suspiciously short Substack `/p/<slug>`, or is a Wikipedia
    `/wiki/<slug>` whose slug ends in `_` or whose last underscore-segment
    is too short to be a real name."""
    if TRUNCATED_RE.search(u):
        return True
    m = re.search(r"/p/([^/?#]*)/?$", u)
    if m and len(m.group(1)) < 5:
        return True
    m = re.search(r"/wiki/([^/?#]+)/?$", u)
    if m:
        slug = m.group(1)
        if slug.endswith("_"):
            return True
        parts = slug.split("_")
        if parts and len(parts[-1]) < 3:
            return True
    return False


def repair_wiki_url(url: str, label: str) -> str | None:
    """When a /wiki/<slug> URL was truncated and the label is the entity's
    canonical name (e.g. "Marie Krogh"), reconstruct the proper wiki URL
    `/wiki/Marie_Krogh`. Returns the new URL or None if we can't be sure."""
    m = re.match(r"(https?://[^/]+/wiki/)", url)
    if not m:
        return None
    parts = label.strip().split()
    if not parts or len(parts) > 4:
        return None
    if not all(p[:1].isupper() for p in parts):
        return None
    return m.group(1) + "_".join(parts)


def clean_url(u: str) -> str:
    u = u.rstrip(",.;)…")
    return u


def is_useful(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    if TRUNCATED_RE.search(url):
        return False
    # Skip obvious sponsor / utm links to keep the inspector clean — these are
    # almost never canonical entity pages.
    if "?utm_" in url or "utm_source=" in url:
        return False
    return True


def is_entity_label(label: str) -> bool:
    """False for social-link section headers and other non-entity labels."""
    norm = label.strip().lower().rstrip(":")
    if norm in SOCIAL_LINK_LABELS:
        return False
    if len(norm) < 2:
        return False
    return True


def extract(text: str) -> list[dict]:
    pairs: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _consider(label: str, raw_url: str) -> None:
        if is_truncated_url(raw_url):
            # Try to repair a truncated wiki URL from the label before giving up
            repaired = repair_wiki_url(raw_url, label)
            if not repaired:
                return
            url = repaired
        else:
            url = clean_url(raw_url)
        if not (is_useful(url) and is_entity_label(label)):
            return
        if (label, url) not in seen:
            seen.add((label, url))
            pairs.append({"label": label, "url": url})

    for line in text.splitlines():
        m = BULLET_RE.match(line)
        if m:
            _consider(m.group(1).strip(), m.group(2))

    for m in MD_LINK_RE.finditer(text):
        _consider(m.group(1).strip(), m.group(2))

    return pairs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episode_dir", type=Path)
    p.add_argument("--source", type=Path,
                   help="explicit source file (default: tries _source_input.txt, "
                        "source/_combined.txt, then any .txt in episode_dir root)")
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)

    ep = args.episode_dir.resolve()
    candidates: list[Path] = []
    if args.source:
        candidates.append(args.source)
    else:
        # Conventional locations
        candidates.extend([
            ep / "source" / "_combined.txt",
            ep / "_combined.txt",
            ep / "source" / "user-notes.txt",
        ])
        # Plus any text files at the episode root
        candidates.extend(sorted(ep.glob("*.txt")))

    src = next((c for c in candidates if c.exists()), None)
    if src is None:
        print(f"ERROR: no source file found in {ep}", file=sys.stderr)
        return 2

    pairs = extract(src.read_text(encoding="utf-8"))
    out = args.out or ep / "working" / "_entity_links.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pairs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"extracted {len(pairs)} entity links from {src.name} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
