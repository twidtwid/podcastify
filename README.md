# podcastify

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](#install)
[![Release: 1.0.3](https://img.shields.io/badge/release-1.0.3-brightgreen.svg)](CHANGELOG.md)

Turn a podcast episode into two self-contained HTML artifacts you can actually keep — a one-page **briefing** that replaces the podcast for a reader who isn't going to listen, and an **annotated transcript** with a sticky chapter rail and in-text search.

One command. ~3 minutes per episode. Fully local by default. Zero cloud API costs.

```
/podcastextract https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands
```

## What you get

Two HTML files per episode, no build step, no external dependencies at view time:

| | Purpose |
|---|---|
| `podcast-at-a-glance.html` | Three-column briefing — episode hero · thesis + 8 takeaways + 8 claims with evidence · entity inspector with canonical links |
| `annotated-transcript.html` | Long-form reading view — sticky chapter rail, scroll-spy, in-text search with prev/next |

Generated episode packages land under `podcast-output/`, which is intentionally not committed because transcripts and show artifacts may not be redistributable.

## 1.0 status

`podcastify` 1.0 is the stable URL-first pipeline for the supported publisher pages below. The 1.0 test set has been run clean and the generated briefings and transcripts have passed manual review.

The 1.0 quality bar is:

- Direct URL ingest for supported publishers, with local resource/transcript fallback when a page is outside the provider set.
- Structured transcript turns preferred when available; malformed optional turn files warn and fall back to text parsing.
- Speaker labels render only when the speaker changes, so long same-speaker runs read like prose instead of a log dump.
- `uncertain_spans` are merged into sidecar metadata before validation and lint checks match the actual unclear marker text, not just span counts.
- Both HTML artifacts are self-contained and validated against the known renderer regressions.

## Known limitations

- **URL ingest is publisher-specific.** Supported publisher pages can be passed directly. For unsupported publishers, use a local resource file with a transcript or place the transcript at `source/user-provided-transcript.txt` and run with `--skip-fetch`.
- **Raw audio is not part of `/podcastextract` yet.** The optional audio requirements file documents the pieces for local transcription and diarization, but the main pipeline expects text.
- **The default local models are large.** The two Ollama pulls are about 30 GB combined; point `PODCAST_OLLAMA_URL` at a remote Ollama host if you do not want them on your laptop.

## How it works

1. **Ingest** a supported episode URL, or parse a dropped resource file (canonical URL + chapter timeline + show-notes links).
2. **Fetch or use** the transcript from provider pages, direct transcript links, inline transcript sections, structured turn bundles, or a user-supplied transcript.
3. **Draft** the briefing's claims, takeaways, and bottom-line via a local Ollama call.
4. **Sharpen** each claim topic and takeaway via a second local Ollama call (think=false on a larger model — sharp, short outputs).
5. **Enumerate + enrich** the entity inspector (people, companies, books, concepts) with categorization and one-line bios.
6. **Render + validate** both HTML artifacts from shared CSS+JS templates with all assets inlined.

The skill contract — when it should trigger, input format, output, the quality bar — lives in [`SKILL.md`](SKILL.md).

## Install

Clone into your agent's skills directory so `podcastextract` lights up:

```bash
# Claude Code
git clone https://github.com/twidtwid/podcastify ~/.claude/skills/podcastextract

# Codex
git clone https://github.com/twidtwid/podcastify ~/.codex/skills/podcastextract
```

Runtime dependencies:

- **[Ollama](https://ollama.com)** with two models pulled (~30 GB combined):
  ```bash
  brew install ollama && ollama serve     # leave running in a separate terminal
  ollama pull gemma4:e4b-nvfp4
  ollama pull qwen3.6:35B-a3b-nvfp4
  ```

`browse-cli` is optional. It is only needed as an advanced fallback for pages where direct HTTP fetching cannot access a transcript that is visible in your own browser.

### Optional: raw-audio dependencies (experimental)

The core pipeline expects a transcript on disk. If you want to experiment with local transcription and diarization outside the main `/podcastextract` path, install the audio extras:

```bash
brew install ffmpeg
pip install -r requirements-audio.txt
```

That gets you `mlx-whisper` for local transcription and `pyannote.audio` for speaker diarization. See [`requirements-audio.txt`](requirements-audio.txt) for HuggingFace auth setup and known limitations.

## Use

In Claude Code:

```
/podcastextract https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands
```

In Codex:

```
Use the podcastextract skill on https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands
```

### URL-first ingest

For supported publisher pages, pass the episode URL directly:

```bash
/podcastextract https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands
```

The URL ingest step fetches the public episode page, discovers a transcript when the provider exposes one, and writes the local source package used by the rest of the pipeline.

Supported 1.0 publisher pages:

- Lenny's Newsletter/Substack
- The New Yorker Radio Hour
- FoundMyFitness
- 99% Invisible
- The Tim Ferriss Show
- Conversations with Tyler
- Dwarkesh Podcast

Supported providers are declared as one JSON file per host under [`podcast-transformer/providers/`](podcast-transformer/providers/). A new site should usually be one small manifest file plus fixture coverage.

Local source files are still supported when a page cannot be fetched directly or when you want to provide a hand-curated transcript. The resource file should contain the episode's canonical URL plus its show notes (chapter timeline + entity link list). If the transcript is pasted into the resource file, the pipeline detects it and skips transcript fetching.

Outside an agent skill runtime, run the orchestrator directly from whichever skills directory you cloned into:

```bash
# Claude Code install
python3 ~/.claude/skills/podcastextract/podcast-transformer/scripts/extract_one.py \
    "https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands"

# Codex install
python3 ~/.codex/skills/podcastextract/podcast-transformer/scripts/extract_one.py \
    "https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands"
```

Output lands in `podcast-output/<slug>/final/`.

Each completed run should leave these files in `final/`:

| File | Purpose |
|---|---|
| `podcast-at-a-glance.html` | Reader-facing briefing |
| `annotated-transcript.html` | Reader-facing transcript |
| `episode.package.json` | Renderer data package |
| `metadata.sidecar.json` | Audit trail, chapters, terminology, uncertainty notes |
| `transcript.verified.md` | Markdown transcript used by validators |

For a JSON-only handoff to another renderer, build just the package:

```bash
python3 podcast-transformer/scripts/podcast_build.py export-json podcast-output/<slug>
```

The command writes `final/episode.package.json`, prints its path, and does not render the built-in HTML artifacts.

For episodes the pipeline can't auto-derive metadata for (non-Substack publishers, missing host/guest in show notes), pass overrides explicitly:

```bash
python3 ... extract_one.py /path/to/resource.txt \
    --slug new-yorker-altman-trust \
    --title "Sam Altman's Trust Issues at OpenAI" \
    --published-at 2026-04-10 \
    --duration-seconds 3257 \
    --host "David Remnick" \
    --guest "Ronan Farrow" --guest "Andrew Marantz"
```

### Rendered page fallback

The normal URL ingest path does not require a browser. If a supported page renders transcript markup with JavaScript or blocks direct HTTP, use a rendered-DOM fallback:

1. Chrome headless `--dump-dom`, when Chrome is installed.
2. Local Playwright DOM capture, when more page control is needed.
3. Browserless `/smart-scrape`, when `BROWSERLESS_TOKEN` is set.
4. Firecrawl scrape, when `FIRECRAWL_API_KEY` is set.
5. Browser Use Cloud CDP, when `BROWSER_USE_API_KEY` is set.
6. `browse-cli`, for users who already have it configured.

These are fallback tools, not install prerequisites.

## Configure

These env vars cover the model and location knobs. Defaults work out of the box.

| Env var | Default | Used by |
|---|---|---|
| `PODCAST_DRAFT_MODEL` | `gemma4:e4b-nvfp4` | bulk drafting + entity enumeration |
| `PODCAST_SHARPEN_MODEL` | `qwen3.6:35B-a3b-nvfp4` | per-item rewrites + entity categorization |
| `PODCAST_OLLAMA_URL` | `http://localhost:11434/api/chat` | Ollama endpoint (point at a remote LAN host if you don't want both models locally) |
| `PODCAST_OUTPUT_ROOT` | `<repo>/podcast-output` | where rendered episode packages and the library `index.html` are written (`--out-root` overrides it) |
| `PODCAST_PUBLIC_BASE_URL` | `http://localhost:8000` | absolute base for `og:url`/`og:image` in the social card. Point at wherever you serve `PODCAST_OUTPUT_ROOT`; a real unfurl needs a publicly reachable URL |

Dump the active config any time:

```bash
python3 ~/.claude/skills/podcastextract/podcast-transformer/scripts/extract_one.py --show-config /dev/null
```

Paid Anthropic API fallback (skip Ollama entirely): `--draft-backend api` with `ANTHROPIC_API_KEY` set.

## Get help

- Bugs, feature requests, install issues → [open an issue](https://github.com/twidtwid/podcastify/issues)
- Architecture, design rules, and the schemas behind the renderer → [`SKILL.md`](SKILL.md) and the reference docs under [`podcast-transformer/references/`](podcast-transformer/references/)

Maintained by Todd. Contributions and worked-example reports are welcome.

## Contributing

PRs welcome. The pipeline is a chain of small, single-purpose scripts under [`podcast-transformer/scripts/`](podcast-transformer/scripts/); each is stdlib-only Python with a clear input/output contract. Useful additions in priority order:

1. **More publisher providers.** Today URL ingest supports a small set of publisher pages. Add one JSON file under [`podcast-transformer/providers/`](podcast-transformer/providers/) for each new host, using the smallest provider kind and hints that work for that site.
2. **Publisher coverage reports.** Notes from episodes shaped from different publishers help exercise the pipeline against new edge cases without committing transcripts.
3. **Renderer polish.** The briefing and transcript views are minimal by design; better mobile, accessibility, or a print stylesheet would all be welcome.

The [`SKILL.md`](SKILL.md) doc captures the durable design rules — read that before opening a renderer PR.

## License

[MIT](LICENSE).
