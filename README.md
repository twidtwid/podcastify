# podcastify

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](#install)

Turn a podcast episode into two self-contained HTML artifacts you can actually keep — a one-page **briefing** that replaces the podcast for a reader who isn't going to listen, and an **annotated transcript** with a sticky chapter rail and in-text search.

One command. ~3 minutes per episode. Fully local. Zero cloud API costs.

```
/podcastextract /path/to/episode-resource.txt
```

## What you get

Two HTML files per episode, no build step, no external dependencies at view time:

| | Purpose |
|---|---|
| `podcast-at-a-glance.html` | Three-column briefing — episode hero · thesis + 8 takeaways + 8 claims with evidence · entity inspector with canonical links |
| `annotated-transcript.html` | Long-form reading view — sticky chapter rail, scroll-spy, in-text search with prev/next |

Generated episode packages land under `podcast-output/`, which is intentionally not committed because transcripts and show artifacts may not be redistributable.

## Known limitations

- **Transcript fetching is publisher-specific.** Substack episode pages are the tested auto-fetch path today. For Apple Podcasts, Spotify, YouTube, and most other publishers, include the transcript in the resource file or place it at `source/user-provided-transcript.txt` and run with `--skip-fetch`.
- **Raw audio is not part of `/podcastextract` yet.** The optional audio requirements file documents the pieces for local transcription and diarization, but the main pipeline expects text.
- **The default local models are large.** The two Ollama pulls are about 30 GB combined; point `PODCAST_OLLAMA_URL` at a remote Ollama host if you do not want them on your laptop.

## How it works

1. **Parse** the dropped resource file (canonical URL + chapter timeline + show-notes links).
2. **Fetch** the transcript via `browse-cli` (your logged-in browser session — handles paywalls).
3. **Draft** the briefing's claims, takeaways, and bottom-line via a local Ollama call.
4. **Sharpen** each claim topic and takeaway via a second local Ollama call (think=false on a larger model — sharp, short outputs).
5. **Enumerate + enrich** the entity inspector (people, companies, books, concepts) with categorization and one-line bios.
6. **Render** both HTML artifacts from shared CSS+JS templates with all assets inlined.

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

- **[browse-cli](https://github.com/pepijnsenders/browse-cli)** — scrapes paywalled transcript pages via your logged-in browser session:
  ```bash
  brew install pepijnsenders/tap/browse && browse init
  ```
- **[Ollama](https://ollama.com)** with two models pulled (~30 GB combined):
  ```bash
  brew install ollama && ollama serve     # leave running in a separate terminal
  ollama pull gemma4:e4b-nvfp4
  ollama pull qwen3.6:35B-a3b-nvfp4
  ```

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
/podcastextract /path/to/episode-resource.txt
```

In Codex:

```
Use the podcastextract skill on /path/to/episode-resource.txt
```

The resource file should contain the episode's canonical URL plus its show notes (chapter timeline + entity link list). For tested Substack pages, the transcript is fetched from the URL. For other publishers, include the transcript in the resource file or pre-place it in the episode's `source/user-provided-transcript.txt`.

Outside an agent skill runtime, run the orchestrator directly from whichever skills directory you cloned into:

```bash
# Claude Code install
python3 ~/.claude/skills/podcastextract/podcast-transformer/scripts/extract_one.py \
    /path/to/episode-resource.txt

# Codex install
python3 ~/.codex/skills/podcastextract/podcast-transformer/scripts/extract_one.py \
    /path/to/episode-resource.txt
```

Output lands in `podcast-output/<slug>/final/`.

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

## Configure

Three env vars cover the model knobs. Defaults work out of the box.

| Env var | Default | Used by |
|---|---|---|
| `PODCAST_DRAFT_MODEL` | `gemma4:e4b-nvfp4` | bulk drafting + entity enumeration |
| `PODCAST_SHARPEN_MODEL` | `qwen3.6:35B-a3b-nvfp4` | per-item rewrites + entity categorization |
| `PODCAST_OLLAMA_URL` | `http://localhost:11434/api/chat` | Ollama endpoint (point at a remote LAN host if you don't want both models locally) |

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

1. **More publisher scrapers.** Today the pipeline knows Lenny's Substack and handles inline transcripts for everything else. Adding handlers for Apple Podcasts, Spotify, or YouTube auto-captions would expand the input surface significantly.
2. **Publisher coverage reports.** Notes from episodes shaped from different publishers help exercise the pipeline against new edge cases without committing transcripts.
3. **Renderer polish.** The briefing and transcript views are minimal by design; better mobile, accessibility, or a print stylesheet would all be welcome.

The [`SKILL.md`](SKILL.md) doc captures the durable design rules — read that before opening a renderer PR.

## License

[MIT](LICENSE).
