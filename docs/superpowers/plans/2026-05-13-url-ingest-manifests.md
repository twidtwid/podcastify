# URL Ingest Manifests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `/podcastextract` accept one known podcast episode URL and automatically assemble the source file plus searchable transcript needed by the existing pipeline.

**Architecture:** Add a small URL ingest layer ahead of the existing file-based pipeline. A single JSON manifest routes known domains to three built-in extraction strategies, writes normalized files into the episode package, and then `extract_one.py` continues through the current parser/build flow.

**Tech Stack:** Python 3 standard library, `unittest`, JSON manifest, direct HTTP via `urllib.request`, existing `extract_one.py` and `parse_source.py` pipeline.

---

## File Structure

- Create `podcast-transformer/providers.json`: one small manifest file containing domain routing and only the hints proven necessary by fixture tests.
- Create `podcast-transformer/scripts/url_ingest.py`: URL matcher, fetch cache, HTML/text extraction helpers, provider implementations, and CLI entry point.
- Consider later `podcast-transformer/scripts/dom_fetch.py`: optional rendered-DOM fetcher behind a flag, starting with Chrome headless `--dump-dom`, not part of the v1 happy path unless direct HTTP fixture/live tests prove it is required.
- Create `tests/test_url_ingest.py`: fixture-only unit tests for the manifest, each provider kind, output contract, and error behavior.
- Create `tests/fixtures/url_ingest/lenny_substack/page.html`: minimal Substack page with title, links, chapters, and a `substackcdn.com/.../transcription.json` reference.
- Create `tests/fixtures/url_ingest/lenny_substack/transcription.json`: minimal Substack transcript JSON fixture.
- Create `tests/fixtures/url_ingest/new_yorker/page.html`: minimal New Yorker page with metadata and a transcript link.
- Create `tests/fixtures/url_ingest/new_yorker/transcript.txt`: plain transcript fixture.
- Create `tests/fixtures/url_ingest/foundmyfitness/page.html`: article page fixture with transcript section inline.
- Create `tests/fixtures/url_ingest/tim_blog/page.html`: Tim.blog page fixture with transcript link.
- Create `tests/fixtures/url_ingest/tim_blog/transcript.html`: transcript page fixture.
- Create `tests/fixtures/url_ingest/99pi/page.html`: 99PI page fixture with a direct transcript link.
- Create `tests/fixtures/url_ingest/99pi/transcript.html`: 99PI transcript page fixture.
- Modify `podcast-transformer/scripts/extract_one.py`: detect HTTP(S) input, call `url_ingest.py`, and use the generated `_source_input.txt`; keep local-file behavior unchanged.
- Modify `README.md`: document URL-first use, known provider scope, and manual fallback.
- Modify `SKILL.md`: update the skill workflow so URL ingest is the default happy path and `browse-cli` is an advanced fallback only.

## Rendered DOM Fallback Research Decision

Managed browser APIs are plausible replacements for the specific thing `browse-cli` gives us: a browser-rendered DOM without asking the user to install and trust a local Chrome extension.

Browserless is the simplest cloud fit for this repo because its REST APIs can return rendered HTML without local browser setup. Prefer `/smart-scrape` over `/content` as the first Browserless integration: `/smart-scrape` escalates from HTTP fetch to proxied fetch, headless browser, and captcha-solving when needed, while still returning raw HTML in the `content` field. Request `formats: ["html", "markdown", "links"]` so the pipeline gets the DOM, a readable fallback, and resolved links in one call. `/content` remains a narrower fallback when we explicitly want only a rendered HTML document. `/screenshot` is useful for visual debugging, but it should not be the primary integration because the extractor needs DOM HTML, not pixels.

Browserless BrowserQL is a later, more precise fallback when REST output is not enough. It can return full page HTML with the `html` mutation, cleaned HTML for smaller payloads, selected DOM subtrees with selectors, and intercepted network responses with the `response` mutation. That last capability is useful if a page renders transcript data by calling a JSON endpoint that is easier to capture from network traffic than to discover from static HTML. Do not use BrowserQL in v1 unless a failing provider test proves that Smart Scrape and plain rendered HTML cannot expose the needed data.

Firecrawl is another reasonable cloud fallback, but it is extraction-first rather than browser-session-first. Its scrape endpoint can return `markdown`, processed `html`, `rawHtml`, `links`, screenshots, and structured JSON; it also supports actions such as wait, click, scroll, and `executeJavascript` before scraping. That makes it useful when the desired output is cleaned article text or when a small JavaScript expression can extract embedded page data. Keep it behind `FIRECRAWL_API_KEY`, and prefer deterministic local/provider parsing first. Do not use Firecrawl's LLM JSON extraction or agent endpoint in v1; those add another interpretation layer where the current pipeline wants source artifacts.

Browser Use Cloud is also viable, but its docs separate two modes:

- Agent mode: natural-language browser tasks with structured output.
- Browser mode: raw browser sessions over CDP, usable from Playwright, Puppeteer, or Selenium.

For this repo, Browser Use Browser mode is the better fit. The pipeline wants a deterministic page snapshot that local parsing code can inspect, not an extra agent interpreting transcript content. If we add Browser Use later, use it as an optional CDP-backed DOM fetcher, gated by `BROWSER_USE_API_KEY`, and save the rendered HTML under `working/fetches/` just like direct HTTP fetches.

Chrome headless `--dump-dom` should be tested before Playwright or Browser Use Cloud. It is a true CLI DOM grabber, it executes page JavaScript before serializing the DOM, and on many Macs it only requires the Chrome app the user already has installed.

Do not add Browserless, Firecrawl, Browser Use Cloud, or any cloud browser to the v1 required install path. Tokens must only come from environment variables such as `BROWSERLESS_TOKEN`, `FIRECRAWL_API_KEY`, or `BROWSER_USE_API_KEY`; never commit tokens, examples with real tokens, or captured request URLs containing tokens.

The default path remains:

1. Direct HTTP fetch.
2. Provider-specific manifest hints and local parsing.
3. Optional local Chrome headless `--dump-dom` fetcher if direct HTTP cannot see rendered transcript markup.
4. Optional local Playwright DOM fetcher if `--dump-dom` needs more wait/click/session control.
5. Optional Browserless `/smart-scrape` fetcher when `BROWSERLESS_TOKEN` is set and local browser tooling is not enough.
6. Optional Browserless BrowserQL for selector-level extraction or network-response capture when Smart Scrape returns incomplete content.
7. Optional Firecrawl scrape fetcher when `FIRECRAWL_API_KEY` is set and cleaned markdown/html/links are more useful than raw browser control.
8. Optional Browser Use Cloud CDP fetcher if Browserless/Firecrawl are not enough or Browser Use is already configured.
9. Existing `browse-cli` fallback only for power users who already have it configured.

This keeps friends on a simple install while preserving an upgrade path for New Yorker/Substack-style pages that expose useful transcript DOM only after browser execution.

## Live URL Exploration Findings

Checked the five target URLs on 2026-05-13 from this workspace. Direct HTTP with a normal browser user-agent is enough for the v1 examples:

- Lenny/Substack: the page HTML contains `speaker_map` plus a signed `cdn_url` for `transcription.json`. The bare unsigned `https://substackcdn.com/.../transcription.json` URL can return 403, so the provider must prefer the signed `cdn_url`. The real JSON is a top-level list of segment objects, not always `{"segments": [...]}`.
- New Yorker: the page exposes a static transcript `.txt` URL on `cn-static-sites.s3.amazonaws.com` through transcript link metadata. Soft paywall behavior remains a runtime risk, but the first direct fetch exposed the transcript URL.
- FoundMyFitness: the full transcript is present in the static HTML inside a hidden tab; no rendered browser was needed for the Arthur Brooks example.
- 99PI: the episode page exposes `/episode/666-enshittification/transcript`; treat 99PI as `direct_transcript_link`, not inline transcript.
- Tim Ferriss: the episode page exposes a direct transcript page at `https://tim.blog/2026/04/30/elad-gil-transcript/`.

Chrome headless `--dump-dom` succeeded for all five pages locally, but it did not expose materially more transcript data than direct HTTP for these examples. Keep it as the first optional rendered-DOM fallback, not as required v1 behavior.

YouTube metadata can be enriched without `yt-dlp`. Two low-complexity options worked:

- `https://www.youtube.com/oembed?url=<watch-url>&format=json` returns title, author, author URL, and thumbnail.
- The watch page HTML contains `ytInitialPlayerResponse.videoDetails.shortDescription`, which includes the description text and show-note links. Use this only as optional enrichment after transcript ingest is green; missing YouTube metadata should warn, not fail the episode.

## Task 1: Manifest And Provider Matching

**Files:**
- Create: `podcast-transformer/providers.json`
- Create: `podcast-transformer/scripts/url_ingest.py`
- Test: `tests/test_url_ingest.py`

- [ ] **Step 1: Write the failing manifest tests**

Add this initial test file:

```python
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
URL_INGEST = REPO_ROOT / "podcast-transformer" / "scripts" / "url_ingest.py"
PROVIDERS = REPO_ROOT / "podcast-transformer" / "providers.json"


def load_url_ingest():
    spec = importlib.util.spec_from_file_location("url_ingest", URL_INGEST)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {URL_INGEST}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UrlIngestManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()

    def test_manifest_contains_expected_seed_providers(self) -> None:
        manifest = self.url_ingest.load_manifest(PROVIDERS)
        ids = {provider["id"] for provider in manifest["providers"]}
        self.assertEqual(
            ids,
            {"lenny_substack", "new_yorker", "foundmyfitness", "tim_blog", "99pi"},
        )

    def test_match_provider_uses_hostname_case_insensitively(self) -> None:
        manifest = self.url_ingest.load_manifest(PROVIDERS)
        provider = self.url_ingest.match_provider(
            "https://WWW.LENNYSNEWSLETTER.COM/p/how-to-build-a-company-that-withstands",
            manifest,
        )
        self.assertEqual(provider["id"], "lenny_substack")
        self.assertEqual(provider["kind"], "substack")

    def test_match_provider_rejects_unknown_domain_with_supported_domains(self) -> None:
        manifest = self.url_ingest.load_manifest(PROVIDERS)
        with self.assertRaisesRegex(
            self.url_ingest.UrlIngestError,
            "Unsupported podcast URL domain.*99percentinvisible.org.*tim.blog",
        ):
            self.url_ingest.match_provider("https://example.com/episode", manifest)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
python3 -m unittest tests/test_url_ingest.py
```

Expected: FAIL with `FileNotFoundError` or import failure for `url_ingest.py`.

- [ ] **Step 3: Add the manifest**

Create `podcast-transformer/providers.json`:

```json
{
  "providers": [
    {
      "id": "lenny_substack",
      "domains": ["www.lennysnewsletter.com"],
      "kind": "substack"
    },
    {
      "id": "new_yorker",
      "domains": ["www.newyorker.com"],
      "kind": "direct_transcript_link",
      "transcript_link_contains": "transcript"
    },
    {
      "id": "foundmyfitness",
      "domains": ["www.foundmyfitness.com"],
      "kind": "article_with_transcript"
    },
    {
      "id": "tim_blog",
      "domains": ["tim.blog"],
      "kind": "direct_transcript_link",
      "transcript_link_contains": "transcript"
    },
    {
      "id": "99pi",
      "domains": ["99percentinvisible.org"],
      "kind": "direct_transcript_link",
      "transcript_link_contains": "transcript"
    }
  ]
}
```

- [ ] **Step 4: Add minimal manifest implementation**

Create `podcast-transformer/scripts/url_ingest.py` with this starting point:

```python
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a supported podcast URL")
    parser.add_argument("url")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--slug")
    args = parser.parse_args(argv)
    manifest = load_manifest()
    provider = match_provider(args.url, manifest)
    raise UrlIngestError(
        f"Provider {provider['id']} matched, but ingest is not implemented yet"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UrlIngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
```

- [ ] **Step 5: Run the manifest tests**

Run:

```bash
python3 -m unittest tests/test_url_ingest.py
```

Expected: PASS for the three manifest tests.

- [ ] **Step 6: Commit**

Run:

```bash
git add podcast-transformer/providers.json podcast-transformer/scripts/url_ingest.py tests/test_url_ingest.py
git commit -m "Add URL ingest provider manifest"
```

## Task 2: Cached Fetching And Output Scaffold

**Files:**
- Modify: `podcast-transformer/scripts/url_ingest.py`
- Modify: `tests/test_url_ingest.py`

- [ ] **Step 1: Add failing tests for fetch cache and scaffold output**

Append these tests to `tests/test_url_ingest.py` inside a new class:

```python
class UrlIngestScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_root = Path(self.tmp.name) / "out"

    def test_slugify_keeps_episode_dirs_safe_and_readable(self) -> None:
        self.assertEqual(
            self.url_ingest.slugify("How to Build a Company: Eric Ries!"),
            "how-to-build-a-company-eric-ries",
        )

    def test_fetch_once_writes_cached_response_under_working_fetches(self) -> None:
        episode_dir = self.out_root / "episode"

        def fake_fetcher(url: str) -> tuple[int, str, bytes]:
            self.assertEqual(url, "https://example.com/episode")
            return 200, "text/html; charset=utf-8", b"<html><h1>Episode</h1></html>"

        path = self.url_ingest.fetch_once(
            "https://example.com/episode",
            episode_dir,
            "page.html",
            fetcher=fake_fetcher,
        )

        self.assertEqual(path, episode_dir / "working" / "fetches" / "page.html")
        self.assertEqual(path.read_text(encoding="utf-8"), "<html><h1>Episode</h1></html>")

    def test_write_bundle_creates_output_contract(self) -> None:
        bundle = self.url_ingest.SourceBundle(
            provider_id="example",
            input_url="https://example.com/episode",
            canonical_url="https://example.com/episode",
            slug="example-episode",
            title="Example Episode",
            transcript_text="HOST: Hello\nGUEST: Hi\n",
            transcript_source_url="https://example.com/transcript",
            metadata={"date": "2026-05-13"},
            chapters=[{"time": "00:00", "title": "Intro"}],
            links=[{"url": "https://youtube.com/watch?v=abc", "text": "YouTube"}],
            warnings=["YouTube metadata not fetched"],
        )
        episode_dir = self.url_ingest.write_bundle(bundle, self.out_root)

        self.assertEqual(episode_dir, self.out_root / "example-episode")
        self.assertTrue((episode_dir / "source" / "_source_input.txt").exists())
        self.assertEqual(
            (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8"),
            "HOST: Hello\nGUEST: Hi\n",
        )
        provenance = json.loads(
            (episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(provenance["provider_id"], "example")
        self.assertEqual(provenance["transcript_path"], "source/user-provided-transcript.txt")
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
python3 -m unittest tests/test_url_ingest.py
```

Expected: FAIL with missing `slugify`, `fetch_once`, `SourceBundle`, or `write_bundle`.

- [ ] **Step 3: Implement scaffold helpers**

Add these imports near the top of `url_ingest.py`:

```python
from dataclasses import dataclass, field
```

Add these helpers below `match_provider`:

```python
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
    return (slug[:80].strip("-") or fallback)


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
        body = exc.read()
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
```

- [ ] **Step 4: Run the scaffold tests**

Run:

```bash
python3 -m unittest tests/test_url_ingest.py
```

Expected: PASS for manifest and scaffold tests.

- [ ] **Step 5: Commit**

Run:

```bash
git add podcast-transformer/scripts/url_ingest.py tests/test_url_ingest.py
git commit -m "Add URL ingest output scaffold"
```

## Task 3: HTML Extraction Utilities

**Files:**
- Modify: `podcast-transformer/scripts/url_ingest.py`
- Modify: `tests/test_url_ingest.py`

- [ ] **Step 1: Add failing utility tests**

Append this class to `tests/test_url_ingest.py`:

```python
class UrlIngestHtmlUtilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()

    def test_extract_title_prefers_og_title_then_h1_then_title_tag(self) -> None:
        html_text = """
        <html><head>
          <title>Fallback Title</title>
          <meta property="og:title" content="Open Graph Episode">
        </head><body><h1>Heading Episode</h1></body></html>
        """
        self.assertEqual(self.url_ingest.extract_title(html_text), "Open Graph Episode")

    def test_extract_links_resolves_relative_urls_and_keeps_text(self) -> None:
        html_text = '<a href="/transcript">Download a Transcript</a><a href="https://youtu.be/abc">Watch</a>'
        links = self.url_ingest.extract_links(html_text, "https://example.com/episode")
        self.assertEqual(
            links,
            [
                {"url": "https://example.com/transcript", "text": "Download a Transcript"},
                {"url": "https://youtu.be/abc", "text": "Watch"},
            ],
        )

    def test_html_to_text_preserves_block_breaks_and_unescapes_entities(self) -> None:
        text = self.url_ingest.html_to_text("<h2>Transcript</h2><p>HOST:&nbsp;Hello</p><p>GUEST: Hi</p>")
        self.assertIn("Transcript\nHOST: Hello\nGUEST: Hi", text)

    def test_extract_chapters_finds_common_timestamp_lines(self) -> None:
        text = "00:00 Intro\n12:34 Building durable teams\n1:02:03 Closing thoughts"
        self.assertEqual(
            self.url_ingest.extract_chapters(text),
            [
                {"time": "00:00", "title": "Intro"},
                {"time": "12:34", "title": "Building durable teams"},
                {"time": "1:02:03", "title": "Closing thoughts"},
            ],
        )
```

- [ ] **Step 2: Run the failing utility tests**

Run:

```bash
python3 -m unittest tests.test_url_ingest.UrlIngestHtmlUtilityTests
```

Expected: FAIL with missing extraction helper names.

- [ ] **Step 3: Implement utility helpers**

Add these helpers below `fetch_once`:

```python
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
        r"<meta\b[^>]*(?:property|name)=[\"']og:title[\"'][^>]*content=[\"']([^\"']+)[\"']",
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
```

- [ ] **Step 4: Run tests**

Run:

```bash
python3 -m unittest tests/test_url_ingest.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add podcast-transformer/scripts/url_ingest.py tests/test_url_ingest.py
git commit -m "Add URL ingest HTML extraction helpers"
```

## Task 4: Direct Transcript Link Providers

**Files:**
- Create: `tests/fixtures/url_ingest/new_yorker/page.html`
- Create: `tests/fixtures/url_ingest/new_yorker/transcript.txt`
- Create: `tests/fixtures/url_ingest/tim_blog/page.html`
- Create: `tests/fixtures/url_ingest/tim_blog/transcript.html`
- Create: `tests/fixtures/url_ingest/99pi/page.html`
- Create: `tests/fixtures/url_ingest/99pi/transcript.html`
- Modify: `podcast-transformer/scripts/url_ingest.py`
- Modify: `tests/test_url_ingest.py`

- [ ] **Step 1: Add fixture files**

Create `tests/fixtures/url_ingest/new_yorker/page.html`:

```html
<html>
  <head><meta property="og:title" content="Sam Altman's Trust Issues at OpenAI"></head>
  <body>
    <time datetime="2025-01-10">January 10, 2025</time>
    <a href="/podcast/the-new-yorker-radio-hour/sam-altmans-trust-issues-at-openai/transcript">Download a Transcript</a>
    <a href="https://podcasts.apple.com/us/podcast/the-new-yorker-radio-hour/id1050430296">Apple Podcasts</a>
  </body>
</html>
```

Create `tests/fixtures/url_ingest/new_yorker/transcript.txt`:

```text
HOST: This is the New Yorker Radio Hour.
GUEST: Trust is the central question.
```

Create `tests/fixtures/url_ingest/tim_blog/page.html`:

```html
<html>
  <head><title>Elad Gil - The Tim Ferriss Show</title></head>
  <body>
    <h1>Elad Gil, Startup Lessons</h1>
    <a href="https://tim.blog/2026/04/29/elad-gil-transcript/">Transcript</a>
    <a href="https://www.youtube.com/watch?v=tim123">YouTube</a>
    <p>00:00 Intro</p>
    <p>10:15 Startup markets</p>
  </body>
</html>
```

Create `tests/fixtures/url_ingest/tim_blog/transcript.html`:

```html
<html><body>
<h1>Transcript</h1>
<p>Tim Ferriss: Welcome back.</p>
<p>Elad Gil: Thanks for having me.</p>
</body></html>
```

Create `tests/fixtures/url_ingest/99pi/page.html`:

```html
<html>
  <head><title>666- Enshittification - 99% Invisible</title></head>
  <body>
    <h1>666- Enshittification</h1>
    <a href="https://podcasts.apple.com/us/podcast/99-invisible/id394775318">Apple Podcasts</a>
    <a href="https://open.spotify.com/show/2VRS1IJCTn2Nlkg33ZVfkM">Spotify</a>
    <a href="/episode/666-enshittification/transcript" class="transcript">Transcript</a>
  </body>
</html>
```

Create `tests/fixtures/url_ingest/99pi/transcript.html`:

```html
<html><body>
<h1>Enshittification - Episode Text Transcript</h1>
<p>ROMAN MARS: This is 99% Invisible.</p>
<p>CHRIS BERUBE: Today I want to talk about enshittification.</p>
</body></html>
```

- [ ] **Step 2: Add failing direct transcript tests**

Append this class to `tests/test_url_ingest.py`:

```python
class UrlIngestDirectTranscriptProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_root = Path(self.tmp.name) / "out"
        self.fixtures = REPO_ROOT / "tests" / "fixtures" / "url_ingest"

    def fixture_fetcher(self, mapping: dict[str, Path]):
        def fetcher(url: str) -> tuple[int, str, bytes]:
            if url not in mapping:
                self.fail(f"unexpected fetch URL: {url}")
            return 200, "text/html; charset=utf-8", mapping[url].read_bytes()
        return fetcher

    def test_new_yorker_direct_transcript_link_writes_bundle(self) -> None:
        url = "https://www.newyorker.com/podcast/the-new-yorker-radio-hour/sam-altmans-trust-issues-at-openai"
        transcript_url = url + "/transcript"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher(
                {
                    url: self.fixtures / "new_yorker" / "page.html",
                    transcript_url: self.fixtures / "new_yorker" / "transcript.txt",
                }
            ),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("HOST: This is the New Yorker Radio Hour.", transcript)
        source = (episode_dir / "source" / "_source_input.txt").read_text(encoding="utf-8")
        self.assertIn("Sam Altman's Trust Issues at OpenAI", source)
        self.assertIn("Apple Podcasts", source)

    def test_tim_blog_direct_transcript_link_converts_html_transcript(self) -> None:
        url = "https://tim.blog/2026/04/29/elad-gil/"
        transcript_url = "https://tim.blog/2026/04/29/elad-gil-transcript/"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher(
                {
                    url: self.fixtures / "tim_blog" / "page.html",
                    transcript_url: self.fixtures / "tim_blog" / "transcript.html",
                }
            ),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("Tim Ferriss: Welcome back.", transcript)
        provenance = json.loads((episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider_id"], "tim_blog")
        self.assertEqual(provenance["chapters"][1], {"time": "10:15", "title": "Startup markets"})

    def test_99pi_direct_transcript_link_writes_bundle(self) -> None:
        url = "https://99percentinvisible.org/episode/666-enshittification/"
        transcript_url = "https://99percentinvisible.org/episode/666-enshittification/transcript"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher(
                {
                    url: self.fixtures / "99pi" / "page.html",
                    transcript_url: self.fixtures / "99pi" / "transcript.html",
                }
            ),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("ROMAN MARS: This is 99% Invisible.", transcript)
        provenance = json.loads((episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider_id"], "99pi")
```

- [ ] **Step 3: Run the failing direct transcript tests**

Run:

```bash
python3 -m unittest tests.test_url_ingest.UrlIngestDirectTranscriptProviderTests
```

Expected: FAIL with missing `ingest_url`.

- [ ] **Step 4: Implement direct transcript provider**

Add these helpers below `extract_chapters`:

```python
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
    keep = []
    for link in links:
        haystack = f"{link.get('text', '')} {link.get('url', '')}".lower()
        if any(marker in haystack for marker in ("apple", "spotify", "youtube", "youtu.be")):
            keep.append(link)
    return keep


def build_episode_dir(out_root: Path, slug: str | None, title: str, url: str) -> Path:
    chosen_slug = slug or slugify(title or urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
    return out_root / chosen_slug


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
```

Update `main()` to call `ingest_url`:

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a supported podcast URL")
    parser.add_argument("url")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--slug")
    args = parser.parse_args(argv)
    episode_dir = ingest_url(args.url, Path(args.out_root), slug=args.slug)
    print(episode_dir)
    return 0
```

- [ ] **Step 5: Run direct transcript tests**

Run:

```bash
python3 -m unittest tests.test_url_ingest.UrlIngestDirectTranscriptProviderTests
```

Expected: PASS.

- [ ] **Step 6: Run all URL ingest tests**

Run:

```bash
python3 -m unittest tests/test_url_ingest.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add podcast-transformer/scripts/url_ingest.py tests/test_url_ingest.py tests/fixtures/url_ingest/new_yorker tests/fixtures/url_ingest/tim_blog tests/fixtures/url_ingest/99pi
git commit -m "Support direct transcript URL ingest providers"
```

## Task 5: Article-With-Transcript Providers

**Files:**
- Create: `tests/fixtures/url_ingest/foundmyfitness/page.html`
- Modify: `podcast-transformer/scripts/url_ingest.py`
- Modify: `tests/test_url_ingest.py`

- [ ] **Step 1: Add fixture files**

Create `tests/fixtures/url_ingest/foundmyfitness/page.html`:

```html
<html>
  <head><meta property="og:title" content="Arthur Brooks on Happiness"></head>
  <body>
    <h1>Arthur Brooks on Happiness</h1>
    <a href="https://podcasts.apple.com/us/podcast/foundmyfitness/id818198322">Apple Podcasts</a>
    <h2>Show Notes</h2>
    <p>00:00 Introduction</p>
    <p>08:30 Happiness and aging</p>
    <h2>Transcript</h2>
    <p>Rhonda Patrick: Arthur, welcome.</p>
    <p>Arthur Brooks: It is great to be here.</p>
  </body>
</html>
```

- [ ] **Step 2: Add failing article provider tests**

Append this class to `tests/test_url_ingest.py`:

```python
class UrlIngestArticleTranscriptProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_root = Path(self.tmp.name) / "out"
        self.fixtures = REPO_ROOT / "tests" / "fixtures" / "url_ingest"

    def fixture_fetcher(self, mapping: dict[str, Path]):
        def fetcher(url: str) -> tuple[int, str, bytes]:
            if url not in mapping:
                self.fail(f"unexpected fetch URL: {url}")
            return 200, "text/html; charset=utf-8", mapping[url].read_bytes()
        return fetcher

    def test_foundmyfitness_inline_transcript_writes_bundle(self) -> None:
        url = "https://www.foundmyfitness.com/episodes/arthur-brooks"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher({url: self.fixtures / "foundmyfitness" / "page.html"}),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("Rhonda Patrick: Arthur, welcome.", transcript)
        provenance = json.loads((episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider_id"], "foundmyfitness")
        self.assertEqual(provenance["chapters"][0], {"time": "00:00", "title": "Introduction"})

```

- [ ] **Step 3: Run the failing article provider tests**

Run:

```bash
python3 -m unittest tests.test_url_ingest.UrlIngestArticleTranscriptProviderTests
```

Expected: FAIL with `Provider kind not implemented yet: article_with_transcript`.

- [ ] **Step 4: Implement inline transcript extraction**

Add these helpers below `useful_links`:

```python
def extract_transcript_section(html_text: str) -> str:
    text = html_to_text(html_text)
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().lower() in {"transcript", "episode transcript", "full transcript"}:
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
    title = extract_title(page_html)
    links = extract_links(page_html, url)
    bundle = SourceBundle(
        provider_id=provider["id"],
        input_url=url,
        canonical_url=url,
        slug=slug or slugify(title or provider["id"]),
        title=title,
        transcript_text=extract_transcript_section(page_html),
        metadata=metadata_from_page(page_html),
        chapters=extract_chapters(html_to_text(page_html)),
        links=useful_links(links),
    )
    return write_bundle(bundle, out_root)
```

Update `ingest_url`:

```python
    if kind == "article_with_transcript":
        return ingest_article_with_transcript(
            url,
            provider,
            out_root,
            slug=slug,
            fetcher=fetcher,
        )
```

- [ ] **Step 5: Run article provider tests**

Run:

```bash
python3 -m unittest tests.test_url_ingest.UrlIngestArticleTranscriptProviderTests
```

Expected: PASS.

- [ ] **Step 6: Run all URL ingest tests**

Run:

```bash
python3 -m unittest tests/test_url_ingest.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add podcast-transformer/scripts/url_ingest.py tests/test_url_ingest.py tests/fixtures/url_ingest/foundmyfitness
git commit -m "Support inline article transcript URL ingest"
```

## Task 6: Substack Transcript JSON Provider

**Files:**
- Create: `tests/fixtures/url_ingest/lenny_substack/page.html`
- Create: `tests/fixtures/url_ingest/lenny_substack/transcription.json`
- Modify: `podcast-transformer/scripts/url_ingest.py`
- Modify: `tests/test_url_ingest.py`

- [ ] **Step 1: Add fixture files**

Create `tests/fixtures/url_ingest/lenny_substack/page.html`:

```html
<html>
  <head><meta property="og:title" content="How to build a company that withstands any era"></head>
  <body>
    <h1>How to build a company that withstands any era | Eric Ries</h1>
    <a href="https://podcasts.apple.com/us/podcast/lennys-podcast/id1627920305">Apple Podcasts</a>
    <a href="https://www.youtube.com/watch?v=PoJ1vTdHpks">YouTube</a>
    <p>00:00 Introduction</p>
    <p>06:45 Long-term company building</p>
    <script>
      window.__POST__ = {
        "transcription": {
          "speaker_map": {"SPEAKER_0": "Eric Ries", "SPEAKER_1": "Lenny Rachitsky"},
          "cdn_url": "https://substackcdn.com/video_upload/post/12345/abcdef/transcription.json?Expires=9999999999&Signature=test"
        }
      };
    </script>
  </body>
</html>
```

Create `tests/fixtures/url_ingest/lenny_substack/transcription.json`:

```json
[
  {
    "speaker": "SPEAKER_1",
    "start": 0,
    "text": "Eric, welcome to the podcast."
  },
  {
    "speaker": "SPEAKER_0",
    "start": 4.2,
    "text": "Thanks for having me."
  }
]
```

- [ ] **Step 2: Add failing Substack tests**

Append this class to `tests/test_url_ingest.py`:

```python
class UrlIngestSubstackProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_root = Path(self.tmp.name) / "out"
        self.fixtures = REPO_ROOT / "tests" / "fixtures" / "url_ingest"

    def fixture_fetcher(self, mapping: dict[str, Path]):
        def fetcher(url: str) -> tuple[int, str, bytes]:
            if url not in mapping:
                self.fail(f"unexpected fetch URL: {url}")
            return 200, "application/json" if "transcription.json" in url else "text/html", mapping[url].read_bytes()
        return fetcher

    def test_lenny_substack_transcription_json_writes_bundle(self) -> None:
        url = "https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands"
        transcript_url = "https://substackcdn.com/video_upload/post/12345/abcdef/transcription.json?Expires=9999999999&Signature=test"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher(
                {
                    url: self.fixtures / "lenny_substack" / "page.html",
                    transcript_url: self.fixtures / "lenny_substack" / "transcription.json",
                }
            ),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("Lenny Rachitsky: Eric, welcome to the podcast.", transcript)
        self.assertIn("Eric Ries: Thanks for having me.", transcript)
        source = (episode_dir / "source" / "_source_input.txt").read_text(encoding="utf-8")
        self.assertIn("https://www.youtube.com/watch?v=PoJ1vTdHpks", source)
        provenance = json.loads((episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider_id"], "lenny_substack")
        self.assertEqual(provenance["chapters"][1], {"time": "06:45", "title": "Long-term company building"})
```

- [ ] **Step 3: Run the failing Substack tests**

Run:

```bash
python3 -m unittest tests.test_url_ingest.UrlIngestSubstackProviderTests
```

Expected: FAIL with `Provider kind not implemented yet: substack`.

- [ ] **Step 4: Implement Substack provider**

Add these helpers below `ingest_article_with_transcript`:

```python
def find_substack_transcription_url(html_text: str, base_url: str) -> str:
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
    match = re.search(r'["\\]speaker_map["\\]\s*:\s*(\{.*?\})', html_text)
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


def substack_json_to_transcript(data: Any, speaker_map: dict[str, str]) -> str:
    raw_segments = data
    if isinstance(data, dict):
        raw_segments = data.get("segments") or data.get("transcript") or []
    lines: list[str] = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue
        speaker_key = clean_text(str(segment.get("speaker") or segment.get("speaker_label") or "SPEAKER"))
        speaker = speaker_map.get(speaker_key, speaker_key)
        text = clean_text(str(segment.get("text") or ""))
        if text:
            lines.append(f"{speaker}: {text}")
    if not lines:
        raise UrlIngestError("Substack transcription JSON contained no transcript segments")
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
    title = extract_title(page_html)
    links = extract_links(page_html, url)
    speaker_map = extract_substack_speaker_map(page_html)
    bundle = SourceBundle(
        provider_id=provider["id"],
        input_url=url,
        canonical_url=url,
        slug=slug or slugify(title or provider["id"]),
        title=title,
        transcript_text=substack_json_to_transcript(transcript_json, speaker_map),
        transcript_source_url=transcript_url,
        metadata=metadata_from_page(page_html),
        chapters=extract_chapters(html_to_text(page_html)),
        links=useful_links(links),
    )
    return write_bundle(bundle, out_root)
```

Update `ingest_url`:

```python
    if kind == "substack":
        return ingest_substack(
            url,
            provider,
            out_root,
            slug=slug,
            fetcher=fetcher,
        )
```

- [ ] **Step 5: Run Substack tests**

Run:

```bash
python3 -m unittest tests.test_url_ingest.UrlIngestSubstackProviderTests
```

Expected: PASS.

- [ ] **Step 6: Run all URL ingest tests**

Run:

```bash
python3 -m unittest tests/test_url_ingest.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add podcast-transformer/scripts/url_ingest.py tests/test_url_ingest.py tests/fixtures/url_ingest/lenny_substack
git commit -m "Support Substack transcript JSON URL ingest"
```

## Task 7: Integrate URL Ingest Into extract_one.py

**Files:**
- Modify: `podcast-transformer/scripts/extract_one.py`
- Modify: `podcast-transformer/scripts/parse_source.py`
- Modify: `tests/test_extract_one.py`
- Create: `tests/test_parse_source.py`

- [ ] **Step 1: Add failing integration tests**

Append these tests to `ExtractOneSafetyTests` in `tests/test_extract_one.py`:

```python
    def test_is_http_url_detects_only_http_and_https(self) -> None:
        self.assertTrue(self.extract_one.is_http_url("https://example.com/episode"))
        self.assertTrue(self.extract_one.is_http_url("http://example.com/episode"))
        self.assertFalse(self.extract_one.is_http_url("/tmp/source.txt"))
        self.assertFalse(self.extract_one.is_http_url("file:///tmp/source.txt"))

    def test_prepare_source_input_leaves_local_files_unchanged(self) -> None:
        args = argparse.Namespace(
            source_file="/tmp/source.txt",
            out_root=Path("/tmp/out"),
            slug=None,
        )
        self.assertEqual(
            self.extract_one.prepare_source_input(args),
            (Path("/tmp/source.txt"), None),
        )

    def test_prepare_source_input_runs_url_ingest_and_returns_generated_source(self) -> None:
        args = argparse.Namespace(
            source_file="https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands",
            out_root=Path("/tmp/out"),
            slug="lenny-test",
        )
        completed = mock.Mock()
        completed.stdout = b"/tmp/out/lenny-test\n"
        with mock.patch.object(self.extract_one, "run", return_value=completed) as run_mock:
            source, prepared_dir = self.extract_one.prepare_source_input(args)
        self.assertEqual(source, Path("/tmp/out/lenny-test/source/_source_input.txt"))
        self.assertEqual(prepared_dir, Path("/tmp/out/lenny-test"))
        run_mock.assert_called_once()
        self.assertIn("url_ingest.py", run_mock.call_args.args[0][1])

    def test_should_skip_fetch_when_prepared_transcript_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "source" / "user-provided-transcript.txt"
            transcript.parent.mkdir(parents=True)
            transcript.write_text("HOST: Hello\nGUEST: Hi\n", encoding="utf-8")
            self.assertTrue(self.extract_one.has_prepared_transcript(Path(tmp)))
```

Also add `import argparse` and `import tempfile` near the top of `tests/test_extract_one.py`.

Create `tests/test_parse_source.py`:

```python
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PARSE_SOURCE = REPO_ROOT / "podcast-transformer" / "scripts" / "parse_source.py"


def load_parse_source():
    spec = importlib.util.spec_from_file_location("parse_source", PARSE_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {PARSE_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParseSourceSameFileTests(unittest.TestCase):
    def test_input_can_already_be_episode_source_input(self) -> None:
        parse_source = load_parse_source()
        with tempfile.TemporaryDirectory() as tmp:
            episode_dir = Path(tmp) / "episode"
            source_dir = episode_dir / "source"
            source_dir.mkdir(parents=True)
            source_input = source_dir / "_source_input.txt"
            source_input.write_text(
                "Canonical URL: https://tim.blog/2026/04/29/elad-gil/\n"
                "Tim Ferriss: Hello\n"
                "Elad Gil: Hi\n"
                "Tim Ferriss: Welcome\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_source.main([str(source_input), str(episode_dir)]), 0)
            self.assertTrue((episode_dir / "working" / "_parsed.json").exists())
```

- [ ] **Step 2: Run failing integration tests**

Run:

```bash
python3 -m unittest tests.test_extract_one.ExtractOneSafetyTests
```

Expected: FAIL with missing `is_http_url` or `prepare_source_input`.

- [ ] **Step 3: Add URL helpers to extract_one.py**

Add this import:

```python
import urllib.parse
```

Add these helpers below `resolve_episode_dir`:

```python
def is_http_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def prepare_source_input(args: argparse.Namespace) -> tuple[Path, Path | None]:
    source_value = str(args.source_file)
    if not is_http_url(source_value):
        return Path(source_value), None
    cmd = [
        sys.executable,
        str(SCRIPTS / "url_ingest.py"),
        source_value,
        "--out-root",
        str(args.out_root),
    ]
    if args.slug:
        cmd.extend(["--slug", args.slug])
    result = run(cmd)
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    episode_dir = Path(stdout.splitlines()[-1]) if stdout else resolve_episode_dir(args.out_root, args.slug)
    source_input = episode_dir / "source" / "_source_input.txt"
    if not source_input.exists():
        raise StepError(f"URL ingest did not create expected source file: {source_input}")
    return source_input, episode_dir


def has_prepared_transcript(episode_dir: Path) -> bool:
    transcript = episode_dir / "source" / "user-provided-transcript.txt"
    return transcript.exists() and transcript.stat().st_size > 0
```

- [ ] **Step 4: Make parse_source tolerate same-file source input**

In `podcast-transformer/scripts/parse_source.py`, replace:

```python
shutil.copyfile(args.input_file, ep / "source" / "_source_input.txt")
```

with:

```python
dest_source = ep / "source" / "_source_input.txt"
if args.input_file.resolve() != dest_source.resolve():
    shutil.copyfile(args.input_file, dest_source)
```

- [ ] **Step 5: Wire helper into main pipeline**

Change the parser positional argument so URLs are accepted as strings:

```python
p.add_argument("source_file")
```

Near the start of `main()`, replace the existing local-file existence check:

```python
if not args.source_file.is_file():
    print(f"ERROR: source file not found: {args.source_file}", file=sys.stderr)
    return 2
```

with:

```python
source_file, prepared_episode_dir = prepare_source_input(args)
if prepared_episode_dir is None and not source_file.is_file():
    print(f"ERROR: source file not found: {source_file}", file=sys.stderr)
    return 2
```

Use `source_file` everywhere the main function currently uses `args.source_file` for parsing and logging. In slug derivation, skip the `/tmp/_slug_probe` call when URL ingest already chose a slug:

```python
slug = args.slug
if prepared_episode_dir is not None and not slug:
    slug = prepared_episode_dir.name
if not slug:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "parse_source.py"),
         str(source_file), "/tmp/_slug_probe", "--print-slug"],
        capture_output=True, text=True,
    )
```

When preparing the final episode directory, do not wipe the directory that `url_ingest.py` just created:

```python
if episode_dir.exists() and not args.keep_existing and episode_dir != prepared_episode_dir:
    shutil.rmtree(episode_dir)
```

In the transcript-fetch section, skip `browse-cli` whenever URL ingest already placed a transcript:

```python
if parsed.get("inline_transcript") or has_prepared_transcript(episode_dir):
    pass
else:
    ...
```

This matters because `url_ingest.py` writes `source/user-provided-transcript.txt` before `parse_source.py` runs. The URL path must preserve that prepared transcript and avoid falling back to `browse-cli`.

- [ ] **Step 6: Run extract_one tests**

Run:

```bash
python3 -m unittest tests/test_extract_one.py
```

Expected: PASS.

- [ ] **Step 7: Run URL ingest tests**

Run:

```bash
python3 -m unittest tests/test_url_ingest.py
```

Expected: PASS.

- [ ] **Step 8: Run parse_source tests**

Run:

```bash
python3 -m unittest tests/test_parse_source.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

Run:

```bash
git add podcast-transformer/scripts/extract_one.py podcast-transformer/scripts/parse_source.py tests/test_extract_one.py tests/test_parse_source.py
git commit -m "Wire URL ingest into podcast extraction"
```

## Task 8: Documentation And Install Surface

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`

- [ ] **Step 1: Update README usage**

Edit `README.md` so the primary usage section includes:

```markdown
### URL-first ingest

For supported publisher pages, pass the episode URL directly:

```bash
/podcastextract https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands
```

The URL ingest step fetches the public episode page, discovers a transcript when the provider exposes one, and writes the local source package used by the rest of the pipeline.

Supported v1 publisher pages:

- Lenny's Newsletter/Substack
- The New Yorker Radio Hour
- FoundMyFitness
- 99% Invisible
- The Tim Ferriss Show

Local source files are still supported when a page cannot be fetched directly or when you want to provide a hand-curated transcript.
```
```

- [ ] **Step 2: Demote browse-cli from prerequisite to fallback**

In `README.md`, replace any install language that makes `browse-cli` mandatory with:

```markdown
`browse-cli` is optional. It is only needed as an advanced fallback for pages where direct HTTP fetching cannot access a transcript that is visible in your own browser.
```

- [ ] **Step 3: Document optional rendered DOM fallbacks**

Add this to `README.md` near troubleshooting:

```markdown
### Rendered page fallback

The normal URL ingest path does not require a browser. If a supported page renders transcript markup with JavaScript or blocks direct HTTP, use a rendered-DOM fallback:

1. Chrome headless `--dump-dom`, when Chrome is installed.
2. Local Playwright DOM capture, when more page control is needed.
3. Browserless `/smart-scrape` capture, when `BROWSERLESS_TOKEN` is set.
4. Firecrawl scrape capture, when `FIRECRAWL_API_KEY` is set.
5. Browser Use Cloud CDP capture, when `BROWSER_USE_API_KEY` is set.
6. `browse-cli`, for users who already have it configured.

These are fallback tools, not install prerequisites.
```

- [ ] **Step 4: Update the skill workflow**

Edit `SKILL.md` so the first workflow path is:

```markdown
1. If the user provides an episode URL from a supported publisher page, run:

   ```bash
   python3 podcast-transformer/scripts/extract_one.py "<episode-url>"
   ```

2. If URL ingest reports an unsupported domain, blocked fetch, or missing transcript, ask the user for a local resource file or transcript and continue with the existing file-based workflow.
3. If direct HTTP misses rendered transcript content, prefer a rendered-DOM fallback in this order: Chrome headless `--dump-dom`, local Playwright, Browserless `/smart-scrape` if `BROWSERLESS_TOKEN` is available, Browserless BrowserQL for selector or network-response capture, Firecrawl scrape if `FIRECRAWL_API_KEY` is available, Browser Use Cloud CDP if `BROWSER_USE_API_KEY` is available, then `browse-cli` for users who already have it configured.
```

- [ ] **Step 5: Check docs for stale mandatory dependency language**

Run:

```bash
/Users/todd_1/homebrew/bin/rg -n "browse-cli|browse init|pepijnsenders|Chrome extension|required|prerequisite|BROWSERLESS_TOKEN|FIRECRAWL_API_KEY|BROWSER_USE_API_KEY|Playwright|dump-dom" README.md SKILL.md podcast-transformer
```

Expected: any remaining browser-tool references describe them as optional or fallback.

- [ ] **Step 6: Commit**

Run:

```bash
git add README.md SKILL.md
git commit -m "Document URL-first podcast ingest"
```

## Task 9: Final Verification

**Files:**
- Verify: whole repo

- [ ] **Step 1: Run URL ingest tests**

Run:

```bash
python3 -m unittest tests/test_url_ingest.py
```

Expected: PASS.

- [ ] **Step 2: Run extractor tests**

Run:

```bash
python3 -m unittest tests/test_extract_one.py
```

Expected: PASS.

- [ ] **Step 3: Compile Python scripts**

Run:

```bash
python3 -m compileall -q podcast-transformer/scripts tests
```

Expected: command exits 0 with no output.

- [ ] **Step 4: Smoke-test CLI error behavior**

Run:

```bash
python3 podcast-transformer/scripts/url_ingest.py https://example.com/episode
```

Expected: exits 2 and prints a clear unsupported-domain error listing supported domains.

- [ ] **Step 5: Inspect git history and working tree**

Run:

```bash
git status --short
git log --oneline --decorate -6
```

Expected: clean working tree after commits; recent commits are the task commits from this plan.

- [ ] **Step 6: Push branch when verification is green**

Run:

```bash
git push
```

Expected: branch `codex/improve-podcast-skill` pushes successfully.

## Self-Review

- Spec coverage: This plan covers the single manifest, URL ingest script, fixture-only tests for all five user examples, direct HTTP happy path, `browse-cli` demotion, `extract_one.py` URL acceptance, output contract, unknown-domain error behavior, and docs updates.
- Deliberate omissions: Live network tests are not included because the spec explicitly keeps unit tests fixture-only and treats soft paywalls as runtime failures. YouTube enrichment is preserved as extracted link metadata only; no `yt-dlp` or video fetching is added.
- Dependency check: The implementation uses only Python standard library. If fixture tests later prove stdlib parsing too brittle for real pages, add the smallest possible dependency in a new task after a failing test demonstrates the need.
