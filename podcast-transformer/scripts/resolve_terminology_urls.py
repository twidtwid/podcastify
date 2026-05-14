#!/usr/bin/env python3
"""Resolve canonical Wikipedia URLs for terminology entries that don't have a
URL after show-notes link matching.

`merge_terminology_urls.py` attaches URLs harvested from `_source_input.txt`'s
bullet-list (when the publisher's show notes ship per-entity links — Lenny
sometimes does, most providers don't). After that step, the renderer's entity
inspector still shows zero outlinks for typical url_ingest bundles because
their show notes are mostly platform links (Apple/Spotify/YouTube), not
entity links.

This script fills the gap by querying Wikipedia's REST summary API for each
terminology entry where:

  * `category` is one of: person, company, book, organization, podcast
  * `url` is empty / missing

It accepts a result only when:

  * HTTP 200,
  * `type` is "standard" (not "disambiguation"),
  * the returned title roughly matches the entry's `term` (case-insensitive,
    after stripping accents/punctuation).

Network calls are bounded — each entry gets one ~2-second request. Failures
(timeout, 404, disambiguation, name mismatch) just leave `url` empty so the
renderer falls back to plain text.

Usage:

    python3 scripts/resolve_terminology_urls.py <episode_dir>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = "podcastextract-resolve-terminology-urls/1.0 (https://github.com/twidtwid/podcastify)"
RESOLVABLE_CATEGORIES = {"person", "company", "organization", "book", "podcast"}
REQUEST_TIMEOUT_SECONDS = 4.0
INTER_REQUEST_DELAY_SECONDS = 0.1


def _normalize(text: str) -> str:
    """Lowercase, strip accents and non-alphanumeric chars for loose matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


_PAREN_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _looks_like_match(query: str, title: str) -> bool:
    """Decide whether the Wikipedia page title corresponds to the query.

    Wikipedia titles for ambiguous entities use a parenthetical disambig
    suffix — "Anthropic (company)", "Spotify (company)". Strip it before
    comparing and require strict normalized equality so partial-name
    queries ("Sam" → "Samuel L. Jackson") never accidentally match. The
    normalization step lowercases, drops accents, and discards
    non-alphanumerics so "Éric Ries" and "Eric Ries" compare equal.
    """
    title_base = _PAREN_SUFFIX_RE.sub("", title).strip()
    q = _normalize(query)
    t = _normalize(title_base)
    if not q or not t:
        return False
    return q == t


def lookup_wikipedia(term: str) -> str:
    """Return the canonical Wikipedia page URL for `term`, or "" if no match."""
    quoted = urllib.parse.quote(term.replace(" ", "_"), safe="")
    req = urllib.request.Request(
        WIKIPEDIA_SUMMARY + quoted,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return ""
    if data.get("type") != "standard":
        return ""
    title = data.get("title", "")
    if not _looks_like_match(term, title):
        return ""
    content_urls = data.get("content_urls") or {}
    desktop = content_urls.get("desktop") or {}
    return desktop.get("page", "") or ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episode_dir", type=Path)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing the sidecar",
    )
    args = p.parse_args(argv)

    sidecar_path = args.episode_dir / "final" / "metadata.sidecar.json"
    if not sidecar_path.is_file():
        print(f"ERROR: sidecar not found: {sidecar_path}", file=sys.stderr)
        return 2

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    terminology = sidecar.get("verification", {}).get("terminology", []) or []

    candidates = [
        t for t in terminology
        if (t.get("category") or "") in RESOLVABLE_CATEGORIES and not (t.get("url") or "").strip()
    ]
    if not candidates:
        print("resolve_terminology_urls: nothing to do (all resolvable entries already have URLs)")
        return 0

    resolved = 0
    for idx, entry in enumerate(candidates):
        term = (entry.get("term") or entry.get("name") or "").strip()
        if not term:
            continue
        if idx > 0:
            time.sleep(INTER_REQUEST_DELAY_SECONDS)
        url = lookup_wikipedia(term)
        if not url:
            continue
        entry["url"] = url
        resolved += 1
        print(f"  [{resolved}/{len(candidates)}] {term!r} -> {url}")

    print(f"resolve_terminology_urls: matched {resolved} of {len(candidates)} candidates")

    if args.dry_run or resolved == 0:
        return 0

    sidecar_path.write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
