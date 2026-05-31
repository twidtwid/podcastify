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


# Ad-read sponsor triggers. Podcasts read a paid sponsor spot ("this episode is
# brought to you by Mercury"); the publisher then lists the sponsor's link in the
# show notes. Those brands are advertisements, not episode entities — detect them
# from the transcript so they can be dropped from the inspector link candidates
# (otherwise the terminology step force-includes every show-notes link).
SPONSOR_TRIGGER_RE = re.compile(
    r"(?:brought to you by|sponsored by|thanks to (?:our )?sponsor[s]?,?|"
    r"this episode is supported by|our sponsor[,:]?|presenting sponsor[,:]?|"
    r"with support from)\s+(?:the\s+)?([A-Za-z0-9][\w.&'-]*(?:\s+[A-Za-z0-9][\w.&'-]*)?)",
    re.IGNORECASE,
)


def detect_sponsors(transcript: str) -> set[str]:
    """Lowercased sponsor brand tokens found in ad-read phrases in the transcript.
    Returns both the first word and the first-two-word form so a link labelled
    'Mercury' or domain 'mercury.com' can be matched."""
    out: set[str] = set()
    for m in SPONSOR_TRIGGER_RE.finditer(transcript or ""):
        words: list[str] = []
        for tok in m.group(1).split():
            cleaned = tok.strip(".,;:!?'\"()").lower()
            if not cleaned:
                break
            words.append(cleaned)
            # A trailing sentence/clause punctuation ends the brand name
            # ("...by Mercury. As an AI founder..." -> just "mercury").
            if tok[-1] in ".,;:!?":
                break
        if words:
            out.add(words[0])
            if len(words) >= 2:
                out.add(" ".join(words[:2]))
    return out


def _is_sponsor_link(label: str, url: str, sponsors: set[str]) -> bool:
    if not sponsors:
        return False
    lab = label.strip().lower()
    if lab and (lab in sponsors or lab.split()[0] in sponsors):
        return True
    m = re.search(r"https?://(?:www\.)?([^/.]+)", url)
    domain_root = m.group(1).lower() if m else ""
    return bool(domain_root and domain_root in sponsors)


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


def extract(text: str, sponsors: set[str] | None = None) -> list[dict]:
    pairs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    sponsors = sponsors or set()

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
        if _is_sponsor_link(label, url, sponsors):
            return  # paid ad-read brand, not an episode entity
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

    # Detect ad-read sponsors from the transcript so their show-notes links are
    # dropped before the terminology step force-includes every link candidate.
    sponsors: set[str] = set()
    for tpath in (ep / "source" / "user-provided-transcript.txt",
                  ep / "final" / "transcript.verified.md",
                  ep / "source" / "transcript.verified.md"):
        if tpath.is_file():
            sponsors = detect_sponsors(tpath.read_text(encoding="utf-8"))
            break

    pairs = extract(src.read_text(encoding="utf-8"), sponsors)
    out = args.out or ep / "working" / "_entity_links.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pairs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sp = f" (dropped sponsors: {', '.join(sorted(sponsors))})" if sponsors else ""
    print(f"extracted {len(pairs)} entity links from {src.name} -> {out}{sp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
