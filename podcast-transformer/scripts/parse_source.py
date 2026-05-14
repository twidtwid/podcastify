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
BULLET_LINK_RE = re.compile(r"^\s*[••\-\*]\s*([^:\n]{2,80}?):\s*(https?://\S+)")
MD_LINK_RE = re.compile(r"\[([^\]]{2,80})\]\((https?://[^)\s]+)\)")
GUEST_RE = re.compile(
    r"\b([A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){1,3})\s+is\s+"
    r"(?:the\s+|a\s+|an\s+)?"
    r"(?:author|founder|co-founder|cofounder|CEO|creator|host|director|inventor)"
)
HOST_PATTERN = re.compile(
    r"Where to find\s+([A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+)?)\s*:?",
)
BOOK_PATTERN = re.compile(
    r"\b(?:new book|book)[,\s]+([A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){0,3})\b"
)


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


def extract_inline_transcript(text: str) -> str:
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if SPEAKER_RE.match(line)),
        None,
    )
    if start is None:
        return ""
    # Only treat as a transcript if there are at least 3 speaker turns.
    speakers = sum(1 for line in lines[start:] if SPEAKER_RE.match(line))
    if speakers < 3:
        return ""
    return "\n".join(lines[start:]).strip() + "\n"


def derive_podcast_title(urls: list[str]) -> str:
    for url in urls:
        m = re.search(r"podcasts\.apple\.com/[a-z]+/podcast/([^/]+)/", url)
        if m:
            slug = m.group(1)
            words = [w.capitalize() for w in slug.split("-")]
            return " ".join(words)
        m2 = re.search(r"lennysnewsletter\.com", url)
        if m2:
            return "Lenny's Podcast: Product | Career | Growth"
    return ""


def derive_episode_title_from_url(url: str) -> str:
    m = re.search(r"/p/([a-z0-9-]+)", url)
    if not m:
        return ""
    slug = m.group(1)
    return " ".join(w.capitalize() for w in slug.split("-"))


def derive_guest(text: str) -> str:
    m = GUEST_RE.search(text)
    return m.group(1) if m else ""


def derive_host(text: str, guest: str) -> str:
    """Collect every 'Where to find X' candidate, skip any that matches the
    guest's first name, and use the last remaining (publisher convention is
    `Where to find <guest>` first, then `Where to find <host>`)."""
    guest_first = guest.split()[0].lower() if guest else ""
    candidates: list[str] = []
    for m in HOST_PATTERN.finditer(text):
        name = m.group(1).strip()
        if not name:
            continue
        if guest_first and name.split()[0].lower() == guest_first:
            continue
        candidates.append((name, m.end()))
    if not candidates:
        return ""
    first, idx = candidates[-1]
    if " " in first:
        return first
    # Single first name — look for "First Last" elsewhere
    pat = re.compile(rf"\b({re.escape(first)} [A-Z][a-zA-Z'’\-]+)\b")
    m2 = pat.search(text)
    if m2:
        return m2.group(1)
    # LinkedIn handle slug — common publisher pattern. Look only within the
    # next 500 chars after the "Where to find" match and require a "LinkedIn"
    # label within ~80 chars of the slug.
    chunk = text[idx : idx + 500]
    handle_pat = re.compile(
        rf"linkedin[^a-zA-Z0-9]{{1,80}}?/?\s*{re.escape(first.lower())}([a-z]+)",
        re.IGNORECASE,
    )
    m3 = handle_pat.search(chunk)
    if m3:
        return f"{first} {m3.group(1).capitalize()}"
    return first


def derive_book(text: str) -> str:
    m = BOOK_PATTERN.search(text)
    return m.group(1) if m else ""


def slugify(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:maxlen]


def derive_slug(canonical_url: str, guest: str, title_words: str, book: str = "") -> str:
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

    # Prefer the book title as the topic word; fall back to a content word
    # from the URL slug.
    topic = ""
    if book:
        topic = slugify(book)
    if not topic:
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

    text = args.input_file.read_text(encoding="utf-8")
    urls = [clean_url(u) for u in URL_RE.findall(text)]
    # Dedupe preserving order
    seen: set[str] = set()
    ordered_urls = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            ordered_urls.append(u)
    sorted_urls = sorted(ordered_urls, key=rank_url)
    canonical_url = sorted_urls[0] if sorted_urls else ""
    youtube_url = next((u for u in ordered_urls if "youtube.com" in u or "youtu.be" in u), "")
    apple_url = next((u for u in ordered_urls if "podcasts.apple.com" in u), "")

    chapters = extract_chapters(text)
    links = extract_links(text)
    transcript = extract_inline_transcript(text)
    guest = derive_guest(text)
    host = derive_host(text, guest)
    book = derive_book(text)
    podcast_title = derive_podcast_title(ordered_urls)
    episode_title = derive_episode_title_from_url(canonical_url)
    slug = derive_slug(canonical_url, guest, episode_title, book)

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
        "book_guess": book,
        "slug_guess": slug,
        "chapters": chapters,
        "links": links,
        "inline_transcript": bool(transcript),
        "inline_transcript_chars": len(transcript),
    }

    (ep / "working" / "_parsed.json").write_text(
        json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if transcript:
        (ep / "source" / "user-provided-transcript.txt").write_text(transcript, encoding="utf-8")

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
