#!/usr/bin/env python3
"""URL ingest for known podcast publisher pages.

This script turns a supported episode URL into the local source files consumed
by extract_one.py. It intentionally keeps provider configuration small: the
manifest routes domains to built-in provider kinds and only contains hints that
tests prove are needed.
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
PROVIDERS_PATH = REPO_ROOT / "podcast-transformer" / "providers.json"
DEFAULT_OUT_ROOT = REPO_ROOT / "podcast-output"


class UrlIngestError(RuntimeError):
    pass


def load_manifest(path: Path = PROVIDERS_PATH) -> dict[str, Any]:
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
    text = re.sub(
        r"</?(?:p|div|section|article|header|footer|br|h[1-6]|li|ul|ol|blockquote)\b[^>]*>",
        "\n",
        html_text,
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


def text_or_html_to_transcript(body: str) -> str:
    text = html_to_text(body) if "<" in body and ">" in body else body
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines).strip() + "\n"


def select_transcript_link(
    links: list[dict[str, str]],
    *,
    contains: str,
) -> dict[str, str] | None:
    needle = contains.lower()
    for link in links:
        haystack = f"{link.get('text', '')} {link.get('url', '')}".lower()
        if needle in haystack:
            return link
    return None


def metadata_from_page(html_text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    time_match = re.search(r"<time\b[^>]*datetime=[\"']([^\"']+)[\"']", html_text, re.IGNORECASE)
    if time_match:
        metadata["date"] = clean_text(time_match.group(1))
    return metadata


def useful_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    keep: list[dict[str, str]] = []
    for link in links:
        haystack = f"{link.get('text', '')} {link.get('url', '')}".lower()
        if any(marker in haystack for marker in ("apple", "spotify", "youtube", "youtu.be")):
            keep.append(link)
    return keep


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
    title = extract_title(page_html)
    bundle = SourceBundle(
        provider_id=provider["id"],
        input_url=url,
        canonical_url=url,
        slug=slug or slugify(title or provider["id"]),
        title=title,
        transcript_text=text_or_html_to_transcript(transcript_body),
        transcript_source_url=transcript_link["url"],
        metadata=metadata_from_page(page_html),
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
    raise UrlIngestError(f"Provider kind not implemented yet: {kind}")


def format_source_input(bundle: SourceBundle) -> str:
    lines = [
        f"Canonical URL: {bundle.canonical_url}",
        f"Title: {bundle.title}" if bundle.title else "",
        f"Transcript URL: {bundle.transcript_source_url}" if bundle.transcript_source_url else "",
    ]
    for key in ("date", "duration", "host", "guest"):
        value = bundle.metadata.get(key)
        if value:
            lines.append(f"{key.title()}: {value}")
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
