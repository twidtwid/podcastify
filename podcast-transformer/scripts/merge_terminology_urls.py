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
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CLEAN_TRAIL_RE = re.compile(
    r"\s*(?:[—\-–]\s*(?:wikipedia|podcast|book site|book|newsletter|substack|website)|"
    r"\(podcast\)|\(wikipedia\))\s*$",
    re.IGNORECASE,
)
BY_AUTHOR_RE = re.compile(r"\s+by\s+[A-Z][\w'.\- ]+$")
URL_PROBE_TIMEOUT_SECONDS = 4.0
URL_PROBE_USER_AGENT = (
    "podcastextract-merge-terminology-urls/1.0 "
    "(checks show-notes link liveness; https://github.com/twidtwid/podcastify)"
)


def clean_label(label: str) -> str:
    label = CLEAN_TRAIL_RE.sub("", label).strip()
    label = BY_AUTHOR_RE.sub("", label).strip()
    return label


def url_is_live(url: str) -> bool:
    """Return True iff `url` answers HEAD with a 2xx/3xx final status.

    Show notes on publisher pages routinely point at URLs that 404'd
    months ago (FoundMyFitness linked Robert Waldinger's now-removed
    `/the-book` path, for example). Attaching dead links to terminology
    entries gives the briefing's inspector a broken outlink button —
    visually worse than no link at all. Reject 4xx/5xx, timeouts, and
    network failures defensively. Some servers reject HEAD; fall back to
    a tiny ranged GET so we don't blow false-negatives on those.
    """
    if not url:
        return False
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": URL_PROBE_USER_AGENT, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=URL_PROBE_TIMEOUT_SECONDS) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError as exc:
        # Some publishers (including a fair number of WordPress sites)
        # disallow HEAD with 405. Retry with a ranged GET.
        if exc.code in (405, 501):
            return _url_is_live_get(url)
        return False
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _url_is_live_get(url: str) -> bool:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": URL_PROBE_USER_AGENT,
            "Accept": "*/*",
            "Range": "bytes=0-0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=URL_PROBE_TIMEOUT_SECONDS) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return False


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
    dead = 0

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
                # Probe the URL before attaching: publisher show notes are
                # littered with stale links (404s, moved pages) and a dead
                # outlink in the briefing inspector is worse than no link.
                if not url_is_live(url):
                    dead += 1
                    continue
                existing["url"] = url
                matched += 1
            else:
                skipped += 1
        elif args.add_missing:
            if not url_is_live(url):
                dead += 1
                continue
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

    print(
        f"matched {matched} URLs, added {added}, "
        f"skipped {skipped} already-set, rejected {dead} dead (of {len(links)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
