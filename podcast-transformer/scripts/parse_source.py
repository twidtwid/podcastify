#!/usr/bin/env python3
"""Parse a dropped source/resource file and extract every fact we can use
to bootstrap the pipeline without an LLM round-trip.

This is the upgraded form of `ingest_combined.py`. Where `ingest_combined`
requires an inline transcript and only captures URL + chapters, this script
also extracts:

  - all URLs, ranked: Substack/publisher > podcast platforms > YouTube
  - the **canonical episode URL** (chosen from the ranked list)
  - the **companion YouTube URL** if present
  - **guest name** from "<First Last> is the [author|founder|CEO|...]" prose
  - **podcast title** from an Apple Podcasts URL slug, when present
  - **chapter timeline** (HH:MM:SS or MM:SS bullet lines)
  - **show-notes link list** in either `• Label: URL` or `[Label](URL)` form
  - **inline transcript** if the file contains `Speaker: …` lines

Plus it does NOT throw when a transcript isn't present — it just records
the absence and moves on, leaving transcript fetching to the next step.

Writes to:
  <episode_dir>/working/_parsed.json
  <episode_dir>/source/user-provided-transcript.txt  (only if inline)
  <episode_dir>/source/_source_input.txt             (copy of original)

Usage:
  python3 scripts/parse_source.py <input_file> <episode_dir>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

URL_RE = re.compile(r"https?://[^\s)\]]+")
CHAPTER_RE = re.compile(
    r"^\s*[-*•]?\s*\(?\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?\)?\s*(?:[-–—]\s*)?(.+?)\s*$"
)
SPEAKER_RE = re.compile(r"^[A-Z][a-zA-Z .'-]{0,48}:\s+\S")

# Colon-prefixes that look like SPEAKER_RE matches but are bundle metadata, not
# transcript speakers. url_ingest writes `Canonical URL: ...`, `Title: ...`,
# `Transcript URL: ...`, `Date: ...` and a `- YouTube: ...` link list into
# _source_input.txt; without this guard, a metadata-only bundle triggers a
# false-positive inline-transcript detection and clobbers the real transcript
# url_ingest staged at source/user-provided-transcript.txt.
METADATA_SPEAKER_PREFIXES: frozenset[str] = frozenset({
    "canonical url",
    "transcript url",
    "title",
    "podcast",
    "podcast title",
    "episode",
    "episode title",
    "episode number",
    "episode url",
    "date",
    "published",
    "published at",
    "duration",
    "duration seconds",
    "host",
    "guest",
    "links",
    "show notes",
    "show notes links",
    "source",
    "publisher",
    "url",
    "youtube",
    "spotify",
    "apple podcasts",
    "rss",
    "language",
    "format",
})
BULLET_LINK_RE = re.compile(r"^\s*[••\-\*]\s*([^:\n]{2,80}?):\s*(https?://\S+)")
MD_LINK_RE = re.compile(r"\[([^\]]{2,80})\]\((https?://[^)\s]+)\)")
# Guest detection runs on structured signals only — the publisher's title and
# the canonical URL slug. The legacy prose miners (`<Name> is the founder/CEO/…`,
# `Where to find <Name>`) overfit Lenny's specific show-notes voice and broke
# on every other publisher; we'd rather report "no guest" than a wrong guest.
# Host comes from the provider manifest (per-publisher constant) and
# `derive_host_from_provider` below. Both are wired in main().
# Book extraction also lived as a prose miner ("new book, <Capitalized Run>")
# and was just as fragile as the guest miner. populate_terminology's LLM call
# already categorizes books with full transcript context, so derive_book has
# been removed and the slug derivation now uses guest-name-only.


def clean_url(u: str) -> str:
    return u.rstrip(",.;)…")


def rank_url(url: str) -> int:
    """Lower is better. We want the URL most likely to have transcripts and
    canonical metadata as the primary episode URL."""
    if "lennysnewsletter.com" in url or "/p/" in url:
        return 0
    if "podcasts.apple.com" in url:
        return 1
    if "tim.blog" in url:
        return 1
    if "open.spotify.com" in url:
        return 2
    if "youtube.com" in url or "youtu.be" in url:
        return 3
    return 4


def extract_chapters(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = CHAPTER_RE.match(line)
        if not m:
            continue
        a, b, c, title = m.groups()
        if c is None:
            # MM:SS
            sec = int(a) * 60 + int(b)
            ts = f"00:{a.zfill(2)}:{b}"
        else:
            sec = int(a) * 3600 + int(b) * 60 + int(c)
            ts = f"{int(a):02d}:{b}:{c}"
        title = title.strip()
        # Filter out lines that are clearly not chapters (URLs, link rows).
        if URL_RE.search(title):
            continue
        if len(title) < 2:
            continue
        out.append({"start": sec, "timestamp": ts, "title": title})
    return out


def extract_links(text: str) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in text.splitlines():
        m = BULLET_LINK_RE.match(line)
        if m:
            label = m.group(1).strip()
            url = clean_url(m.group(2))
            key = (label.lower(), url)
            if key not in seen and url.startswith(("http://", "https://")):
                seen.add(key)
                pairs.append({"label": label, "url": url})
    for m in MD_LINK_RE.finditer(text):
        label = m.group(1).strip()
        url = clean_url(m.group(2))
        if len(label) < 2:
            continue
        if url.endswith("...") or url.endswith("…"):
            continue
        key = (label.lower(), url)
        if key not in seen:
            seen.add(key)
            pairs.append({"label": label, "url": url})
    return pairs


def is_speaker_line(line: str) -> bool:
    """True if line matches SPEAKER_RE and the colon-prefix isn't bundle metadata."""
    if not SPEAKER_RE.match(line):
        return False
    prefix = line.split(":", 1)[0].strip().lower()
    return prefix not in METADATA_SPEAKER_PREFIXES


def extract_inline_transcript(text: str) -> str:
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if is_speaker_line(line)),
        None,
    )
    if start is None:
        return ""
    # Only treat as a transcript if there are at least 3 real speaker turns.
    speakers = sum(1 for line in lines[start:] if is_speaker_line(line))
    if speakers < 3:
        return ""
    return "\n".join(lines[start:]).strip() + "\n"


def _normalize_date(value: str) -> str:
    """Normalize a bundle `Date:` value to YYYY-MM-DD for sidecar.published_at.

    url_ingest writes whatever `<time datetime>` exposes (often a full ISO
    timestamp like `2026-04-13T06:00:00-04:00`); the sidecar schema wants a
    bare calendar date. Keep the first 10 chars if they look like a date,
    otherwise return the original (lets a hand-written `Date: 2026-05-10`
    pass through untouched).
    """
    if not value:
        return ""
    head = value[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", head):
        return head
    return value


def parse_bundle_metadata(text: str) -> dict[str, str]:
    """Read url_ingest bundle metadata back out of `_source_input.txt`.

    url_ingest writes `Title: …`, `Podcast: …`, `Date: …`, `Host: …`,
    `Guest: …`, `Duration seconds: …` as `Foo: bar` lines at the top of
    `_source_input.txt`. parse_source's older heuristics derived these from
    the URL slug, which failed for any publisher whose URL pattern wasn't
    hard-coded (e.g. The New Yorker's `/podcast/the-new-yorker-radio-hour/…`)
    and produced empty `episode.title` / `episode.podcast_title` in the
    sidecar — which strict validation then rejects. Read the bundle's own
    declarations so they're the authoritative source.

    Only colon-prefixes in METADATA_SPEAKER_PREFIXES are recognized, so
    prose lines like `Doctorow's three-stage platform decay: …` cannot
    masquerade as metadata.
    """
    meta: dict[str, str] = {}
    for line in text.splitlines():
        if not SPEAKER_RE.match(line):
            continue
        prefix, _, value = line.partition(":")
        key = prefix.strip().lower()
        if key in METADATA_SPEAKER_PREFIXES:
            meta.setdefault(key, value.strip())
    return meta


def derive_podcast_title(urls: list[str]) -> str:
    for url in urls:
        m = re.search(r"podcasts\.apple\.com/[a-z]+/podcast/([^/]+)/", url)
        if m:
            slug = m.group(1)
            words = [w.capitalize() for w in slug.split("-")]
            return " ".join(words)
        if re.search(r"lennysnewsletter\.com", url):
            return "Lenny's Podcast: Product | Career | Growth"
        # Generic /podcast/<slug>/ path. Matches New Yorker
        # (newyorker.com/podcast/the-new-yorker-radio-hour/…) and any other
        # publisher that mounts each show under a stable slug.
        m2 = re.search(r"://(?:www\.)?[^/]+/podcast/([a-z0-9-]+)/", url)
        if m2:
            slug = m2.group(1)
            return " ".join(w.capitalize() for w in slug.split("-"))
        if re.search(r"tim\.blog", url):
            return "The Tim Ferriss Show"
        if re.search(r"foundmyfitness\.com", url):
            return "FoundMyFitness"
        if re.search(r"99percentinvisible\.org", url):
            return "99% Invisible"
    return ""


def derive_episode_title_from_url(url: str) -> str:
    m = re.search(r"/p/([a-z0-9-]+)", url)
    if m:
        slug = m.group(1)
        return " ".join(w.capitalize() for w in slug.split("-"))
    # Tim Ferriss style: /YYYY/MM/DD/<guest-slug>/  (no Substack /p/ prefix).
    m2 = re.search(r"/\d{4}/\d{1,2}/\d{1,2}/([a-z0-9-]+)/?$", url)
    if m2:
        slug = m2.group(1)
        return " ".join(w.capitalize() for w in slug.split("-"))
    return ""


# Host/guest resolution lives in `scripts/resolve_speakers.py` — a dedicated
# Ollama call that reads the title + first minutes of transcript. Keeping that
# decision in the LLM (rather than fragile prose regex or URL-slug heuristics)
# is the only thing that survives publisher-format drift.


def slugify(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:maxlen]


def derive_slug(canonical_url: str, guest: str, title_words: str) -> str:
    """A heuristic slug: <publisher>-<lastname>-<topic-word>. Falls back to
    the URL slug if not enough signal."""
    publisher = ""
    if "lennysnewsletter" in canonical_url:
        publisher = "lenny"
    elif "tim.blog" in canonical_url:
        publisher = "tim-ferriss"
    elif "99percentinvisible" in canonical_url:
        publisher = "99pi"
    elif "foundmyfitness" in canonical_url:
        publisher = "foundmyfitness"

    lastname = ""
    if guest:
        parts = guest.split()
        if len(parts) >= 2:
            lastname = slugify(parts[-1])

    # Topic word: a content word from the title/URL slug. (The earlier
    # `book`-as-topic shortcut was removed along with the prose-mining
    # derive_book heuristic.)
    topic = ""
    if True:
        stopwords = {"the", "and", "with", "that", "this", "from", "your", "what", "how", "why", "when", "where", "build", "company", "withstands"}
        for w in title_words.lower().split():
            w_clean = re.sub(r"[^a-z]+", "", w)
            if len(w_clean) >= 5 and w_clean not in stopwords:
                topic = w_clean
                break

    parts = [p for p in (publisher, lastname, topic) if p]
    if parts:
        return "-".join(parts)
    # Fallback: URL slug
    m = re.search(r"/p/([a-z0-9-]+)", canonical_url)
    return slugify(m.group(1)) if m else "episode"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_file", type=Path)
    p.add_argument("episode_dir", type=Path)
    p.add_argument("--print-slug", action="store_true",
                   help="print only the derived slug (use for shell scripting)")
    args = p.parse_args(argv)

    if not args.input_file.is_file():
        print(f"ERROR: input file not found: {args.input_file}", file=sys.stderr)
        return 2

    text = args.input_file.read_text(encoding="utf-8", errors="replace")
    urls = [clean_url(u) for u in URL_RE.findall(text)]
    # Dedupe preserving order
    seen: set[str] = set()
    ordered_urls = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            ordered_urls.append(u)
    sorted_urls = sorted(ordered_urls, key=rank_url)
    bundle_meta = parse_bundle_metadata(text)
    canonical_url = bundle_meta.get("canonical url") or (sorted_urls[0] if sorted_urls else "")
    youtube_url = next((u for u in ordered_urls if "youtube.com" in u or "youtu.be" in u), "")
    apple_url = next((u for u in ordered_urls if "podcasts.apple.com" in u), "")

    chapters = extract_chapters(text)
    links = extract_links(text)
    transcript = extract_inline_transcript(text)
    bundle_title = bundle_meta.get("title", "")
    # Host/guest resolution happens in a downstream LLM step
    # (`resolve_speakers.py`); parse_source only forwards whatever the bundle
    # explicitly declares (rare — most providers don't carry host/guest names).
    guest = bundle_meta.get("guest", "")
    host = bundle_meta.get("host", "")
    podcast_title = (
        bundle_meta.get("podcast")
        or bundle_meta.get("podcast title")
        or derive_podcast_title(ordered_urls)
    )
    episode_title = bundle_title or derive_episode_title_from_url(canonical_url)
    published_at = _normalize_date(bundle_meta.get("date", ""))
    try:
        duration_seconds = (
            int(bundle_meta["duration seconds"]) if "duration seconds" in bundle_meta else 0
        )
    except (TypeError, ValueError):
        duration_seconds = 0
    slug = derive_slug(canonical_url, guest, episode_title)

    if args.print_slug:
        print(slug)
        return 0

    ep = args.episode_dir.resolve()
    (ep / "source").mkdir(parents=True, exist_ok=True)
    (ep / "working").mkdir(parents=True, exist_ok=True)
    (ep / "final").mkdir(parents=True, exist_ok=True)

    # Preserve the original input unless URL ingest already created this file.
    dest_source = ep / "source" / "_source_input.txt"
    if args.input_file.resolve() != dest_source.resolve():
        shutil.copyfile(args.input_file, dest_source)

    parsed = {
        "input_file": str(args.input_file.resolve()),
        "urls_ranked": sorted_urls,
        "canonical_url": canonical_url,
        "youtube_url": youtube_url,
        "apple_podcast_url": apple_url,
        "podcast_title": podcast_title,
        "episode_title_guess": episode_title,
        "host_guess": host,
        "guest_guess": guest,
        "published_at_guess": published_at,
        "duration_seconds_guess": duration_seconds,
        "slug_guess": slug,
        "chapters": chapters,
        "links": links,
        "inline_transcript": bool(transcript),
        "inline_transcript_chars": len(transcript),
    }

    # Atomic write: a kill mid-write must not leave _parsed.json truncated for
    # the downstream extract_one read.
    _parsed_path = ep / "working" / "_parsed.json"
    _parsed_tmp = _parsed_path.with_name(_parsed_path.name + ".tmp")
    _parsed_tmp.write_text(
        json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(_parsed_tmp, _parsed_path)

    if transcript:
        out_path = ep / "source" / "user-provided-transcript.txt"
        # Belt-and-suspenders: parse_source is a metadata extractor, not the
        # authoritative transcript writer. If url_ingest (or the user) already
        # staged a real transcript, leave it alone. The 100-byte floor lets a
        # stub from a previous failed run get overwritten by a real detection.
        if not out_path.exists() or out_path.stat().st_size < 100:
            out_path.write_text(transcript, encoding="utf-8")

    print(f"Episode dir:   {ep}")
    print(f"Slug guess:    {slug}")
    print(f"Canonical URL: {canonical_url}")
    print(f"YouTube URL:   {youtube_url or '(none)'}")
    print(f"Host:          {host or '(unknown)'}")
    print(f"Guest:         {guest or '(unknown)'}")
    print(f"Podcast title: {podcast_title or '(unknown)'}")
    print(f"Chapters:      {len(chapters)}")
    print(f"Show-notes links: {len(links)}")
    print(f"Inline transcript: {'yes (' + str(len(transcript)) + ' chars)' if transcript else 'no — need to fetch'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
