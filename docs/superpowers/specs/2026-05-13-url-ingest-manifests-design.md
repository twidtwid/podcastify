# URL Ingest Manifests Design

## Goal

Make `podcastextract` accept a single podcast episode URL for known publisher pages and produce the same local source package the existing pipeline already consumes.

The first version should be deliberately small: one manifest file, one ingest script, no browser dependency on the happy path, and red/green tests for the real episodes Todd uses.

## Non-Goals

- Do not solve arbitrary podcast URLs.
- Do not add raw-audio transcription.
- Do not require `browse-cli`, Chrome extensions, Playwright, `yt-dlp`, or browser automation for normal installs.
- Do not build a generic extraction DSL.
- Do not use an LLM to interpret manifests.

`browse-cli` may remain documented as an advanced fallback when direct HTTP fetches cannot access a transcript the user can view in a browser.

## Scope

V1 supports URL-only ingest for these real test cases:

- Lenny/Substack: `https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands`
- New Yorker Radio Hour: `https://www.newyorker.com/podcast/the-new-yorker-radio-hour/sam-altmans-trust-issues-at-openai`
- FoundMyFitness: `https://www.foundmyfitness.com/episodes/arthur-brooks`
- 99% Invisible: `https://99percentinvisible.org/episode/666-enshittification/`
- Tim Ferriss: `https://tim.blog/2026/04/29/elad-gil/`

The bar is not perfect universal extraction. The bar is: these URLs produce enough local input files for the existing pipeline to continue without the user hand-assembling URLs, transcripts, chapters, and notes.

## Architecture

Add:

```text
podcast-transformer/providers.json
podcast-transformer/scripts/url_ingest.py
tests/fixtures/url_ingest/
tests/test_url_ingest.py
```

Update:

```text
podcast-transformer/scripts/extract_one.py
README.md
SKILL.md
```

`extract_one.py` accepts either a local path or an HTTP(S) URL:

```bash
python3 podcast-transformer/scripts/extract_one.py https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands
```

If the input is a URL, `extract_one.py` calls `url_ingest.py` first. `url_ingest.py` creates the episode directory and writes normalized source files. `extract_one.py` then continues using the existing file-based pipeline.

Existing local-file behavior remains unchanged.

## Manifest

Use one small JSON file:

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
      "kind": "article_with_transcript"
    }
  ]
}
```

This is not a DSL. It is a routing table plus one or two hints. If a site fails, add the smallest possible hint or one small built-in `kind`. Avoid per-site Python until repeated failures prove it is needed.

## Provider Kinds

### `substack`

1. Fetch the canonical URL once.
2. Try to discover `substackcdn.com/.../transcription.json` in the HTML.
3. If found, fetch it and convert it using the existing `ingest_substack_json.py` logic or a shared helper extracted from that script.
4. If not found, fetch `{url}?showTranscript=true` and parse the transcript-view text.
5. Extract title, description, date, show-note links, chapters, Apple link, and YouTube link from the canonical page text where present.

### `direct_transcript_link`

1. Fetch the canonical URL once.
2. Find the first link whose text or href contains the configured transcript hint.
3. Fetch that transcript URL once.
4. Convert transcript text to `Speaker: text` when possible.
5. Extract metadata and links from the canonical page.

### `article_with_transcript`

1. Fetch the canonical URL once.
2. Look for a transcript section in the page text.
3. Extract transcript text from that section when present.
4. Extract metadata, chapters, and links from the page.
5. If no transcript section is present, fail cleanly with a message that the provider found metadata but no transcript.

## Fetching Rules

- Use direct HTTP first.
- Fetch each URL once per run.
- Save raw responses under `working/fetches/`.
- Parse only cached files after fetching.
- Detect blocked responses by status code and obvious interstitial text.
- Do not retry repeatedly against soft-paywalled pages.
- New Yorker soft paywall behavior should be reported as blocked when it happens, not worked around with browser automation by default.
- 99PI should be treated as public but possibly protected by bot/interstitial behavior, not as paywalled.

## Output Contract

For a successful URL ingest, write:

```text
podcast-output/<slug>/
  source/_source_input.txt
  source/user-provided-transcript.txt
  working/_url_ingest.json
  working/fetches/
```

`_source_input.txt` should be a human-readable assembled resource file containing:

- canonical URL
- discovered transcript URL, if any
- Apple/Spotify/YouTube links, if any
- episode title/date/duration/host/guest hints
- show-note links
- chapter timeline

`_url_ingest.json` should contain machine-readable provenance:

```json
{
  "provider_id": "lenny_substack",
  "input_url": "",
  "canonical_url": "",
  "transcript_path": "source/user-provided-transcript.txt",
  "transcript_source_url": "",
  "metadata": {},
  "chapters": [],
  "links": [],
  "warnings": []
}
```

## Error Behavior

- Unknown domain: fail with a clear message listing supported domains.
- Metadata found but transcript missing: fail with a message saying what was found and what the user can provide.
- Blocked fetch: fail with URL, status code if available, and detector reason.
- YouTube enrichment missing: warn only.
- Optional fields missing: warn only.

## TDD Plan

Use red/green TDD around fixtures.

1. Add a fixture for each real URL under `tests/fixtures/url_ingest/<provider>/`.
2. Write a failing test for provider matching from URL to manifest entry.
3. Write failing tests for each provider fixture:
   - returns expected provider id
   - writes `_source_input.txt`
   - writes `_url_ingest.json`
   - writes `source/user-provided-transcript.txt` when the fixture exposes a transcript
   - extracts title/date/links/chapters where available
4. Implement the smallest code to pass one provider at a time.
5. Keep network out of unit tests. Live network smoke tests can be a separate manual command.

Completion for v1 means all five fixture tests pass and `extract_one.py URL --show-config` / normal file input behavior still works.

## Documentation Changes

README and `SKILL.md` should describe the new happy path:

```text
/podcastextract https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands
```

They should also say:

- URL ingest supports known publisher pages first.
- Local resource files are still supported.
- `browse-cli` is an advanced fallback, not an install prerequisite.
- If a provider cannot fetch a transcript directly, the user may provide the transcript file manually.

## Open Decisions

- Whether to add `beautifulsoup4` immediately or first attempt stdlib `html.parser` plus regexes. Default: start with stdlib; add `beautifulsoup4` only if fixtures prove stdlib is too fragile.
- Whether `url_ingest.py` should expose a `--fixture-dir` test mode or tests should monkeypatch fetch calls. Default: tests monkeypatch fetch calls and keep CLI simple.

