#!/usr/bin/env python3
"""URL ingest for known podcast publisher pages.

This script turns a supported episode URL into the local source files consumed
by extract_one.py. It intentionally keeps provider configuration small:
one JSON file per provider routes domains to built-in provider kinds and only
contains hints that tests prove are needed.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDERS_PATH = REPO_ROOT / "podcast-transformer" / "providers"
DEFAULT_OUT_ROOT = REPO_ROOT / "podcast-output"


class UrlIngestError(RuntimeError):
    pass


def load_manifest(path: Path = PROVIDERS_PATH) -> dict[str, Any]:
    if path.is_dir():
        providers: list[dict[str, Any]] = []
        for provider_path in sorted(path.glob("*.json")):
            with provider_path.open("r", encoding="utf-8") as fh:
                provider = json.load(fh)
            if not isinstance(provider, dict):
                raise UrlIngestError(f"Invalid provider manifest: {provider_path}")
            providers.append(provider)
        if not providers:
            raise UrlIngestError(f"No provider manifests found in: {path}")
        return {"providers": providers}

    with path.open("r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    if not isinstance(manifest.get("providers"), list):
        raise UrlIngestError(f"Invalid providers manifest: {path}")
    return manifest


def normalize_host(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UrlIngestError(f"Expected an http(s) podcast URL, got: {url}")
    return parsed.hostname.lower() if parsed.hostname else ""


def match_provider(url: str, manifest: dict[str, Any]) -> dict[str, Any]:
    host = normalize_host(url)
    supported: list[str] = []
    for provider in manifest["providers"]:
        domains = [domain.lower() for domain in provider.get("domains", [])]
        supported.extend(domains)
        if host in domains:
            return provider
    supported_text = ", ".join(sorted(set(supported)))
    raise UrlIngestError(
        f"Unsupported podcast URL domain: {host}. Supported domains: {supported_text}"
    )


@dataclass
class SourceBundle:
    provider_id: str
    input_url: str
    canonical_url: str
    slug: str
    title: str = ""
    transcript_text: str = ""
    transcript_source_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    chapters: list[dict[str, str]] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def slugify(text: str, *, fallback: str = "podcast-episode") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:80].strip("-") or fallback


def default_fetcher(url: str) -> tuple[int, str, bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 podcast-transformer-url-ingest "
                "(compatible; direct metadata fetch)"
            )
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = getattr(response, "status", 200)
            content_type = response.headers.get("content-type", "")
            body = response.read()
    except urllib.error.HTTPError as exc:
        exc.read()
        raise UrlIngestError(f"Fetch blocked or failed for {url}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise UrlIngestError(f"Fetch failed for {url}: {exc.reason}") from exc
    return status, content_type, body


def fetch_once(
    url: str,
    episode_dir: Path,
    name: str,
    *,
    fetcher: Callable[[str], tuple[int, str, bytes]] = default_fetcher,
) -> Path:
    fetch_dir = episode_dir / "working" / "fetches"
    fetch_dir.mkdir(parents=True, exist_ok=True)
    path = fetch_dir / name
    if path.exists():
        return path
    status, _content_type, body = fetcher(url)
    if status >= 400:
        raise UrlIngestError(f"Fetch blocked or failed for {url}: HTTP {status}")
    if b"enable javascript" in body.lower() or b"access denied" in body.lower():
        raise UrlIngestError(f"Fetch blocked for {url}: interstitial response detected")
    path.write_bytes(body)
    return path


TAG_RE = re.compile(r"<[^>]+>")
HREF_RE = re.compile(
    r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)


def clean_text(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_tags(text: str) -> str:
    return clean_text(TAG_RE.sub(" ", text))


def strip_title_suffix(title: str, provider: dict[str, Any]) -> str:
    """Drop a publisher-specific SEO suffix from the page's og:title.

    Tim.blog renders og:title as `<episode> - The Blog of Author Tim Ferriss`
    — a ~32-char suffix that breaks the briefing layout. Providers can
    declare `title_strip_suffix` (string or list of strings) to peel those
    off. Substring match at the END of the title, case-insensitive,
    repeated until no more suffixes apply.
    """
    suffixes = provider.get("title_strip_suffix") or []
    if isinstance(suffixes, str):
        suffixes = [suffixes]
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if not suffix:
                continue
            if title.lower().endswith(suffix.lower()):
                title = title[: -len(suffix)].rstrip(" -–—|·:")
                changed = True
    return title


def extract_title(html_text: str) -> str:
    og = re.search(
        r"<meta\b[^>]*(?:property|name)=[\"']og:title[\"'][^>]*content=\"([^\"]+)\"",
        html_text,
        re.IGNORECASE,
    )
    if not og:
        og = re.search(
            r"<meta\b[^>]*(?:property|name)=[\"']og:title[\"'][^>]*content='([^']+)'",
            html_text,
            re.IGNORECASE,
        )
    if og:
        return clean_text(og.group(1))
    h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", html_text, re.IGNORECASE | re.DOTALL)
    if h1:
        return strip_tags(h1.group(1))
    title = re.search(r"<title\b[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    return strip_tags(title.group(1)) if title else ""


def extract_links(html_text: str, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for href, body in HREF_RE.findall(html_text):
        url = urllib.parse.urljoin(base_url, html.unescape(href))
        text = strip_tags(body)
        links.append({"url": url, "text": text})
    return links


def html_to_text(html_text: str) -> str:
    # Drop the entire contents of `<script>` and `<style>` blocks before any
    # other processing. WordPress podcast sites (99percentinvisible.org and
    # tim.blog both) embed schema.org JSON-LD inside `<script type=
    # "application/ld+json">` that otherwise survives the tag-stripping pass
    # and gets fed downstream as if it were transcript prose — corrupting
    # resolve_speakers (which sees JSON instead of dialogue) and the entire
    # transcript browser. Same hazard for inline `<style>`.
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        "\n",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"</?(?:p|div|section|article|header|footer|br|h[1-6]|li|ul|ol|blockquote)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    compact: list[str] = []
    for line in lines:
        if line:
            compact.append(line)
    return "\n".join(compact)


def extract_chapters(text: str) -> list[dict[str, str]]:
    chapters: list[dict[str, str]] = []
    chapter_re = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+?)\s*$")
    for line in text.splitlines():
        match = chapter_re.match(line)
        if match:
            chapters.append({"time": match.group(1), "title": clean_text(match.group(2))})
    return chapters


# Lines a publisher transcript page renders ABOVE the actual dialog that we
# never want flowing downstream as if it were speech. 99pi's WordPress shell
# pads ~100 lines of nav chrome before the first speaker line; Tim Ferriss's
# transcript pages do similar. Keep this list narrow — generic markers that
# only appear in page chrome, not in real conversation.
_TRANSCRIPT_LEADING_CHROME_MARKERS = (
    "skip to content",
    "skip to main content",
    "toggle navigation",
    "you are using an outdated browser",
    "subscribe to the newsletter",
    "search this site",
)

# A speaker line: 1-4 Capitalized words, then `:`, then content. Matches
# "Tim Ferriss: ...", "Elad Gil: ...", "CORY DOCTOROW: ...", "Dr. Arthur Brooks:
# ...". Rejects long page titles like "The Tim Ferriss Show Transcripts:
# Elad Gil, Consigliere..." that share the colon shape but have more
# than 4 capitalized tokens before the colon.
_TRANSCRIPT_SPEAKER_LINE_RE = re.compile(
    r"^[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,3}:\s+\S",
)
# A speaker-looking line whose colon-prefix is actually a publisher metadata
# label, not a speaker. tim.blog renders "Topics: The Tim Ferriss Show
# Transcripts" above the real transcript; without this, the speaker regex
# would lock onto that line and we'd still ship 80+ lines of nav chrome.
_TRANSCRIPT_METADATA_PREFIXES = frozenset({
    "topics",
    "topic",
    "tags",
    "tag",
    "category",
    "categories",
    "filed under",
    "posted in",
    "published",
    "date",
    "by",
    "author",
    "share",
})


def _is_real_speaker_line(line: str) -> bool:
    if not _TRANSCRIPT_SPEAKER_LINE_RE.match(line):
        return False
    prefix = line.split(":", 1)[0].strip().lower()
    return prefix not in _TRANSCRIPT_METADATA_PREFIXES


def _find_dialog_start(lines: list[str]) -> int | None:
    """Find the first line where a real conversation begins.

    A single line whose shape matches `Speaker: text` is not enough — paper
    titles ("Just Think: The Challenges Of The Disengaged Mind") and
    publisher headlines ("TARGET ARTICLE: ...") match the same shape but
    are one-off references buried in show-notes citations on the
    FoundMyFitness page. Real dialog has the SAME speaker reappear within a
    short window. Require the candidate line's colon-prefix to repeat at
    least twice in the next 50 lines.
    """
    for i, line in enumerate(lines):
        if not _is_real_speaker_line(line):
            continue
        prefix = line.split(":", 1)[0].strip().lower()
        same_prefix = 0
        for follow in lines[i + 1 : i + 51]:
            if not _is_real_speaker_line(follow):
                continue
            if follow.split(":", 1)[0].strip().lower() == prefix:
                same_prefix += 1
                if same_prefix >= 2:
                    return i
    return None


# An inline `[HH:MM:SS]` or `[MM:SS]` marker dropped mid-paragraph by the
# publisher's transcript (the New Yorker's S3 transcripts sprinkle one
# every ~60 seconds even though the surrounding sentence is mid-thought).
# Cosmetic noise once we have proper turn anchoring — strip when cleaning
# the raw transcript so the rendered prose flows instead of having
# `[00:11:00] insane,` litter mid-sentence.
_INLINE_TIMESTAMP_RE = re.compile(r"\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*")


def _strip_inline_timestamps(line: str) -> str:
    cleaned = _INLINE_TIMESTAMP_RE.sub(" ", line)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def text_or_html_to_transcript(body: str) -> str:
    text = html_to_text(body) if "<" in body and ">" in body else body
    lines = [_strip_inline_timestamps(line.strip()) for line in text.splitlines() if line.strip()]

    # Find the first line that looks like a speaker turn. Page chrome (nav
    # links, "skip to content", "you are using an outdated browser", schema
    # breadcrumbs, etc.) never matches the `Name: prose` shape. Trimming
    # everything before that first speaker turn cuts ~100 lines of WordPress
    # chrome out of the 99pi/Tim Ferriss outputs without touching the New
    # Yorker (plain-text S3 file, already clean) or FoundMyFitness (uses
    # extract_transcript_section, not this function) flows.
    speaker_start = _find_dialog_start(lines)
    if speaker_start is None:
        # Fall back to dropping lines matching known chrome markers.
        lines = [
            line for line in lines
            if not any(marker in line.lower() for marker in _TRANSCRIPT_LEADING_CHROME_MARKERS)
        ]
    else:
        lines = lines[speaker_start:]

    return "\n".join(lines).strip() + "\n"


def select_transcript_link(
    links: list[dict[str, str]],
    *,
    contains: str,
) -> dict[str, str] | None:
    """Pick the link most likely to point at THIS episode's transcript.

    A naive `first match for "transcript"` scan picked the wrong link on
    tim.blog episode pages, which carry three transcript-flavored links:

        - "The Tim Ferriss Show Transcripts" -> /category/...transcripts/
        - "This episode"                     -> /YYYY/MM/DD/<slug>-transcript/
        - "All episodes"                     -> /YYYY/MM/DD/all-transcripts.../

    The first match was the index page, which then yielded a "list of every
    transcript ever" instead of the Elad Gil dialog. Score candidates so
    per-episode permalinks beat indexes and category pages.
    """
    needle = contains.lower()
    candidates: list[tuple[int, dict[str, str]]] = []
    for link in links:
        url = (link.get("url") or "").lower()
        text = (link.get("text") or "").lower()
        haystack = f"{text} {url}"
        if needle not in haystack:
            continue
        score = 0
        # Negative: archive / index / category pages — these are NOT one
        # episode's transcript, they're a list of many.
        if any(
            marker in url
            for marker in (
                "/category/",
                "/tag/",
                "all-transcripts",
                "/transcripts/",  # plural-suffixed list
                "/transcripts-from-",
            )
        ):
            score -= 100
        # Positive: dated permalink — tim.blog uses /YYYY/MM/DD/<slug>-transcript/.
        if re.search(r"/\d{4}/\d{1,2}/\d{1,2}/[^/]+-transcript", url):
            score += 50
        # Positive: an explicit per-episode label.
        if "this episode" in text or "episode transcript" in text:
            score += 30
        # Slight bonus for the singular "transcript" anywhere in the URL
        # path's terminal slug (e.g. ".../elad-gil-transcript/").
        if re.search(r"-transcript/?$", url) or re.search(r"/transcript/?$", url):
            score += 10
        candidates.append((score, link))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def metadata_from_page(html_text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    time_match = re.search(r"<time\b[^>]*datetime=[\"']([^\"']+)[\"']", html_text, re.IGNORECASE)
    if time_match:
        metadata["date"] = clean_text(time_match.group(1))
    return metadata


# Patterns that mark a link as obvious chrome / boilerplate rather than show
# notes (newsletter signup, login, social-share buttons, legal footer, etc.).
# Keeping these out lets extract_entity_links downstream pull real entity links
# (Wikipedia, product pages, personal sites) from `_source_input.txt` without
# drowning in header/footer noise — and prevents fake "Privacy" / "Terms" /
# "[email protected]" terminology entries from being introduced downstream.
_LINK_NOISE_URL_SUBSTRINGS = (
    "twitter.com/intent",
    "x.com/intent",
    "facebook.com/sharer",
    "linkedin.com/share",
    "reddit.com/submit",
    "mailto:",
    # Publisher legal / footer
    "substack.com/privacy",
    "substack.com/tos",
    "substack.com/ccpa",
    "substack.com/dmca",
    "substack.com/copyright",
    "substack.com/about",
    "substack.com/app",
    "substack.com/home",
    "substack.com/start",
    "substack.com/sitemap",
    "/cdn-cgi/l/email-protection",  # Cloudflare email obfuscation rewrites
    "enable-javascript.com",         # noscript fallback
    "javascript:",
    # Auth / account chrome
    "/login", "/signin", "/sign-in", "/signup", "/sign-up", "/subscribe",
    "/account", "/feed/rss", ".rss",
)


def _link_is_noise(link: dict[str, str]) -> bool:
    url = (link.get("url") or "").lower()
    if not url.startswith(("http://", "https://")):
        return True
    text = (link.get("text") or "").strip().lower()
    # Drop link bullets whose ANCHOR TEXT is a known chrome label. Catches
    # the case where the URL alone looks innocuous but the rendered label is
    # something like "Privacy" / "Terms" / "Collection notice".
    chrome_labels = {
        "privacy", "privacy policy",
        "terms", "terms of service", "terms of use",
        "collection notice", "data collection",
        "ccpa", "gdpr",
        "cookies", "cookie policy",
        "dmca", "copyright", "copyright policy",
        "sitemap", "rss", "rss feed",
        "[email protected]", "email protected",
        "turn on javascript", "enable javascript",
        "sign in", "log in", "subscribe", "sign up",
    }
    if text in chrome_labels:
        return True
    return any(noisy in url for noisy in _LINK_NOISE_URL_SUBSTRINGS)


def useful_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    """Filter the page's `<a>` extraction down to what's useful as show notes.

    Previously this kept ONLY Apple / Spotify / YouTube platform links, which
    silently dropped every Wikipedia, vendor, and personal-site link a real
    publisher's show notes include — so `extract_entity_links` later had
    nothing to attach to terminology entries and the briefing's inspector
    showed zero outlinks. Now we keep everything except obvious chrome
    (auth, share intents, RSS feeds) and de-dupe by URL, preserving order.
    The downstream bullet-list extractor (extract_entity_links.py) does the
    real entity matching against an `Eric Ries: https://…` pattern.
    """
    seen: set[str] = set()
    keep: list[dict[str, str]] = []
    for link in links:
        if _link_is_noise(link):
            continue
        url = link.get("url") or ""
        if url in seen:
            continue
        seen.add(url)
        keep.append(link)
    return keep


def extract_transcript_section(html_text: str) -> str:
    text = html_to_text(html_text)
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().lower() in {"transcript", "transcription", "episode transcript", "full transcript"}:
            start = index + 1
            break
    if start is None:
        raise UrlIngestError("Page fetched, but no transcript section heading was found")
    stop_headings = {"credits", "show notes", "references", "related", "newsletter"}
    transcript_lines: list[str] = []
    for line in lines[start:]:
        normalized = line.strip().lower()
        if normalized in stop_headings:
            break
        if line.strip():
            transcript_lines.append(line.strip())
    # The "Transcription" heading on a FoundMyFitness page sits ABOVE the
    # show notes / chapter timeline AND the references list. The simple
    # "everything after the heading" capture above includes 350+ lines of
    # citations and chapter timestamps as if they were transcript. Trim
    # leading non-speaker lines so the first kept line is a real speaker
    # turn — and require it to repeat (paper titles like "Just Think:"
    # match the speaker shape once but never recur).
    speaker_start = _find_dialog_start(transcript_lines)
    if speaker_start is not None:
        transcript_lines = transcript_lines[speaker_start:]
    transcript = "\n".join(transcript_lines).strip()
    if not transcript:
        raise UrlIngestError("Transcript section was found but contained no transcript text")
    return transcript + "\n"


def ingest_article_with_transcript(
    url: str,
    provider: dict[str, Any],
    out_root: Path,
    *,
    slug: str | None,
    fetcher: Callable[[str], tuple[int, str, bytes]],
) -> Path:
    temp_slug = slug or slugify(urllib.parse.urlparse(url).path.strip("/") or provider["id"])
    episode_dir = out_root / temp_slug
    page_path = fetch_once(url, episode_dir, "page.html", fetcher=fetcher)
    page_html = page_path.read_text(encoding="utf-8")
    title = strip_title_suffix(extract_title(page_html), provider)
    links = extract_links(page_html, url)
    page_metadata = metadata_from_page(page_html)
    if provider.get("podcast_title"):
        page_metadata.setdefault("podcast_title", provider["podcast_title"])
    if provider.get("host"):
        page_metadata.setdefault("host", provider["host"])
    bundle = SourceBundle(
        provider_id=provider["id"],
        input_url=url,
        canonical_url=url,
        slug=slug or slugify(title or provider["id"]),
        title=title,
        transcript_text=extract_transcript_section(page_html),
        metadata=page_metadata,
        chapters=extract_chapters(html_to_text(page_html)),
        links=useful_links(links),
    )
    return write_bundle(bundle, out_root)


def find_substack_transcription_url(html_text: str, base_url: str) -> str:
    escaped_cdn_match = re.search(
        r'\\\"cdn_url\\\":\\\"(https://substackcdn\.com/[^"\\]+transcription\.json\?[^"\\]+)\\\"',
        html_text,
    )
    if escaped_cdn_match:
        return html.unescape(
            escaped_cdn_match.group(1).replace("\\/", "/").replace("\\u0026", "&")
        )
    cdn_match = re.search(
        r'["\\]cdn_url["\\]\s*:\s*["\\](https://substackcdn\.com/[^"\\]+transcription\.json\?[^"\\]+)',
        html_text,
    )
    if cdn_match:
        return html.unescape(cdn_match.group(1).replace("\\/", "/").replace("\\u0026", "&"))
    match = re.search(
        r"https://substackcdn\.com/[^\"'<> ]+/transcription\.json",
        html_text,
    )
    if match:
        return html.unescape(match.group(0))
    for link in extract_links(html_text, base_url):
        if "substackcdn.com" in link["url"] and link["url"].endswith("transcription.json"):
            return link["url"]
    raise UrlIngestError("Substack page fetched, but no transcription.json URL was found")


def extract_substack_speaker_map(html_text: str) -> dict[str, str]:
    speaker_map: dict[str, str] = {}
    # The speaker_map embed in a Substack post page lives inside HTML-escaped
    # JSON: bytes look like `\"speaker_map\":{\"SPEAKER_0\":\"Eric Ries\"...}`,
    # so the chars flanking `speaker_map` are `\` then `"` (two characters),
    # not a single quote. The old `["\\]` char class only consumed one char and
    # then choked on the next `"` before the `:`. Use `["\\]+` to absorb any
    # run of quote/backslash escape characters on either side. Stays compatible
    # with un-escaped JSON in case Substack ever serves a plain embed.
    match = re.search(r'["\\]+speaker_map["\\]+\s*:\s*(\{.*?\})', html_text)
    if not match:
        return speaker_map
    raw = match.group(1).replace('\\"', '"')
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return speaker_map
    for key, value in parsed.items():
        speaker_map[clean_text(str(key))] = clean_text(str(value))
    return speaker_map


def _segment_speaker(segment: dict[str, Any]) -> str:
    """Pick the speaker label for a Substack-shape transcription.json segment.

    Real Substack payloads put the speaker on individual word entries inside
    `words`, not on the segment itself, so a naive `segment.get("speaker")`
    returns None and every segment collapses to the literal "SPEAKER"
    fallback. Read it from the first labeled word and fall back to the
    explicit segment-level field for older / non-Substack shapes.
    """
    explicit = segment.get("speaker") or segment.get("speaker_label")
    if explicit:
        return str(explicit)
    for word in segment.get("words") or []:
        if isinstance(word, dict) and word.get("speaker"):
            return str(word["speaker"])
    return "SPEAKER"


def substack_json_to_transcript(data: Any, speaker_map: dict[str, str]) -> str:
    """Render a Substack transcription.json into `Speaker: text` lines.

    Merges consecutive same-speaker segments into a single turn. The raw
    payload chunks a single speaker's monologue across dozens of ~3-second
    segments (one per sentence); without merging, the transcript browser
    re-tags every sentence with the speaker name, drowning the page in
    `Eric Ries: ...` repetitions instead of one labeled turn followed by
    flowing prose.
    """
    raw_segments = data
    if isinstance(data, dict):
        raw_segments = data.get("segments") or data.get("transcript") or []
    turns: list[tuple[str, list[str]]] = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue
        speaker_key = clean_text(_segment_speaker(segment))
        speaker = speaker_map.get(speaker_key, speaker_key)
        text = clean_text(str(segment.get("text") or ""))
        if not text:
            continue
        if turns and turns[-1][0] == speaker:
            turns[-1][1].append(text)
        else:
            turns.append((speaker, [text]))
    if not turns:
        raise UrlIngestError("Substack transcription JSON contained no transcript segments")
    lines = [f"{speaker}: {' '.join(chunks).strip()}" for speaker, chunks in turns]
    return "\n".join(lines) + "\n"


def ingest_substack(
    url: str,
    provider: dict[str, Any],
    out_root: Path,
    *,
    slug: str | None,
    fetcher: Callable[[str], tuple[int, str, bytes]],
) -> Path:
    temp_slug = slug or slugify(urllib.parse.urlparse(url).path.strip("/") or provider["id"])
    episode_dir = out_root / temp_slug
    page_path = fetch_once(url, episode_dir, "page.html", fetcher=fetcher)
    page_html = page_path.read_text(encoding="utf-8")
    transcript_url = find_substack_transcription_url(page_html, url)
    transcript_path = fetch_once(transcript_url, episode_dir, "transcription.json", fetcher=fetcher)
    transcript_json = json.loads(transcript_path.read_text(encoding="utf-8"))
    title = strip_title_suffix(extract_title(page_html), provider)
    links = extract_links(page_html, url)
    speaker_map = extract_substack_speaker_map(page_html)
    page_metadata = metadata_from_page(page_html)
    # Substack transcription.json is a flat list of {start, end, text, ...}
    # segments — the wall-clock duration is the last segment's `end`. Surface
    # it as bundle metadata so sidecar.episode.duration_seconds isn't None
    # (the renderer otherwise shows "0M" on the library index card).
    duration_seconds = _substack_duration_seconds(transcript_json)
    if duration_seconds:
        page_metadata.setdefault("duration_seconds", str(duration_seconds))
    if provider.get("podcast_title"):
        page_metadata.setdefault("podcast_title", provider["podcast_title"])
    if provider.get("host"):
        page_metadata.setdefault("host", provider["host"])
    bundle = SourceBundle(
        provider_id=provider["id"],
        input_url=url,
        canonical_url=url,
        slug=slug or slugify(title or provider["id"]),
        title=title,
        transcript_text=substack_json_to_transcript(transcript_json, speaker_map),
        transcript_source_url=transcript_url,
        metadata=page_metadata,
        chapters=extract_chapters(html_to_text(page_html)),
        links=useful_links(links),
    )
    return write_bundle(bundle, out_root)


def _substack_duration_seconds(transcript_json: Any) -> int:
    """Return the wall-clock duration in seconds from a Substack-style
    transcription.json. The payload is either a list of segments or a dict
    wrapping `segments` / `transcript`. The last segment's `end` field is the
    authoritative duration; round up to a whole second."""
    segments = transcript_json
    if isinstance(transcript_json, dict):
        segments = transcript_json.get("segments") or transcript_json.get("transcript") or []
    if not segments:
        return 0
    try:
        last_end = segments[-1].get("end") if isinstance(segments[-1], dict) else None
        return int(float(last_end)) if last_end is not None else 0
    except (TypeError, ValueError):
        return 0


def ingest_direct_transcript_link(
    url: str,
    provider: dict[str, Any],
    out_root: Path,
    *,
    slug: str | None,
    fetcher: Callable[[str], tuple[int, str, bytes]],
) -> Path:
    temp_slug = slug or slugify(urllib.parse.urlparse(url).path.strip("/") or provider["id"])
    episode_dir = out_root / temp_slug
    page_path = fetch_once(url, episode_dir, "page.html", fetcher=fetcher)
    page_html = page_path.read_text(encoding="utf-8")
    links = extract_links(page_html, url)
    transcript_link = select_transcript_link(
        links,
        contains=provider.get("transcript_link_contains", "transcript"),
    )
    if not transcript_link:
        raise UrlIngestError(f"{provider['id']} page fetched, but no transcript link was found")
    transcript_path = fetch_once(
        transcript_link["url"],
        episode_dir,
        "transcript.html",
        fetcher=fetcher,
    )
    transcript_body = transcript_path.read_text(encoding="utf-8")
    title = strip_title_suffix(extract_title(page_html), provider)
    page_metadata = metadata_from_page(page_html)
    if provider.get("podcast_title"):
        page_metadata.setdefault("podcast_title", provider["podcast_title"])
    if provider.get("host"):
        page_metadata.setdefault("host", provider["host"])
    bundle = SourceBundle(
        provider_id=provider["id"],
        input_url=url,
        canonical_url=url,
        slug=slug or slugify(title or provider["id"]),
        title=title,
        transcript_text=text_or_html_to_transcript(transcript_body),
        transcript_source_url=transcript_link["url"],
        metadata=page_metadata,
        chapters=extract_chapters(html_to_text(page_html)),
        links=useful_links(links),
    )
    return write_bundle(bundle, out_root)


def ingest_url(
    url: str,
    out_root: Path = DEFAULT_OUT_ROOT,
    *,
    slug: str | None = None,
    fetcher: Callable[[str], tuple[int, str, bytes]] = default_fetcher,
) -> Path:
    manifest = load_manifest()
    provider = match_provider(url, manifest)
    kind = provider["kind"]
    if kind == "direct_transcript_link":
        return ingest_direct_transcript_link(
            url,
            provider,
            out_root,
            slug=slug,
            fetcher=fetcher,
        )
    if kind == "article_with_transcript":
        return ingest_article_with_transcript(
            url,
            provider,
            out_root,
            slug=slug,
            fetcher=fetcher,
        )
    if kind == "substack":
        return ingest_substack(
            url,
            provider,
            out_root,
            slug=slug,
            fetcher=fetcher,
        )
    raise UrlIngestError(f"Provider kind not implemented yet: {kind}")


def format_source_input(bundle: SourceBundle) -> str:
    lines = [
        f"Canonical URL: {bundle.canonical_url}",
        f"Title: {bundle.title}" if bundle.title else "",
        f"Transcript URL: {bundle.transcript_source_url}" if bundle.transcript_source_url else "",
    ]
    # Display label per bundle metadata key. `podcast_title` -> `Podcast:` and
    # `duration_seconds` -> `Duration seconds:` so parse_source can pick them
    # up by exact prefix and feed sidecar.episode.podcast_title /
    # episode.duration_seconds — both required for strict validation + the
    # library-index duration chip.
    metadata_labels = {
        "date": "Date",
        "duration": "Duration",
        "duration_seconds": "Duration seconds",
        "host": "Host",
        "guest": "Guest",
        "podcast_title": "Podcast",
    }
    for key, label in metadata_labels.items():
        value = bundle.metadata.get(key)
        if value:
            lines.append(f"{label}: {value}")
    if bundle.links:
        lines.append("")
        lines.append("Links:")
        for link in bundle.links:
            label = link.get("text") or link.get("url", "")
            lines.append(f"- {label}: {link.get('url', '')}")
    if bundle.chapters:
        lines.append("")
        lines.append("Chapters:")
        for chapter in bundle.chapters:
            lines.append(f"- {chapter.get('time', '')} {chapter.get('title', '')}".rstrip())
    if bundle.warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in bundle.warnings:
            lines.append(f"- {warning}")
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def write_bundle(bundle: SourceBundle, out_root: Path) -> Path:
    episode_dir = out_root / bundle.slug
    source_dir = episode_dir / "source"
    working_dir = episode_dir / "working"
    source_dir.mkdir(parents=True, exist_ok=True)
    working_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "_source_input.txt").write_text(
        format_source_input(bundle),
        encoding="utf-8",
    )
    if bundle.transcript_text:
        (source_dir / "user-provided-transcript.txt").write_text(
            bundle.transcript_text,
            encoding="utf-8",
        )
    provenance = {
        "provider_id": bundle.provider_id,
        "input_url": bundle.input_url,
        "canonical_url": bundle.canonical_url,
        "transcript_path": "source/user-provided-transcript.txt" if bundle.transcript_text else "",
        "transcript_source_url": bundle.transcript_source_url,
        "metadata": bundle.metadata,
        "chapters": bundle.chapters,
        "links": bundle.links,
        "warnings": bundle.warnings,
    }
    (working_dir / "_url_ingest.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return episode_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a supported podcast URL")
    parser.add_argument("url")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--slug")
    args = parser.parse_args(argv)
    episode_dir = ingest_url(args.url, Path(args.out_root), slug=args.slug)
    print(episode_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UrlIngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
