---
name: podcastextract
description: Turn a podcast episode URL or resource text file into a self-contained research package — verified transcript, sidecar with chapters and entity terminology, and two rendered HTML artifacts (a briefing and an annotated transcript). Use when the user invokes "/podcastextract <url-or-path>", asks Codex to use the podcastextract skill on a podcast URL or file, drops a podcast resource file, or asks to "extract", "process", "build a briefing for", or "make HTML pages from" a podcast episode. Out of scope: transcribing raw audio (the user must supply a transcript or a publisher URL the pipeline can scrape), hand-authoring per-episode HTML (re-render via the pipeline), or publishing outputs to external services.
---

# podcastextract

Turn one supported episode URL or resource text file into a research package the user can read in five minutes and reference forever. The HTML pages are the artifact — not a preview of a different canonical document. Run fully local via Ollama by default.

## When to invoke

- User types `/podcastextract <url-or-path>` in Claude Code.
- User asks Codex to use the `podcastextract` skill on a podcast URL or file path.
- User drops a path to a `.txt` containing a canonical URL + show notes + chapter timeline and asks to extract / process / build / render.
- User says "make a briefing for this podcast" / "build me HTML for the Eric Ries episode" / "run the podcast pipeline on this".

Do not invoke when:
- The user wants to transcribe raw audio. Ask them to drop a transcript first (or a publisher URL that has one).
- The user wants per-episode HTML hand-edits. The renderer is the source of truth — re-run instead.
- The user is asking general questions about an episode they want to discuss. This skill writes files; it doesn't summarize in chat.

## Input

Preferred input is a supported publisher episode URL. 1.0 supports:

- Lenny's Newsletter/Substack
- The New Yorker Radio Hour
- FoundMyFitness
- 99% Invisible
- The Tim Ferriss Show
- Conversations with Tyler

Supported providers are declared as one JSON file per host under `podcast-transformer/providers/`. Add a new host by contributing one small manifest file there, then add fixture coverage in `tests/fixtures/url_ingest/`.

Local resource files are still supported. The resource file should contain (in any order):

- A **canonical URL** for the episode.
- A **chapter timeline** — markdown bullets like `(00:00) Introduction` or `- [00:02:26] Topic`.
- A **show-notes link list** — `• Label: https://...` lines for canonical entity URLs (people, books, companies).
- Anything else is ignored.

If the transcript itself is pasted into the resource file, the pipeline detects it and skips the scrape. If URL ingest creates `source/transcript.turns.json`, the renderer uses those structured turns before falling back to text speaker-line parsing.

## Prerequisites

Before running, confirm:

1. **Ollama is running** with the two required models pulled:
   ```bash
   ollama list | grep -E "gemma4:e4b-nvfp4|qwen3.6:35B-a3b-nvfp4"
   ```
   If either is missing, ask the user to `ollama pull <model>` and try again. Don't try to install for them.

2. **The input is usable**:
   - URLs must be from a supported publisher page.
   - Local resource files must exist at the path the user gave.

If any check fails, surface the specific failure with the fix command. Do not proceed.

## Output

One episode directory at `podcast-output/<slug>/` containing:

```
final/
  podcast-at-a-glance.html       the briefing — thesis, takeaways, claims, entity inspector
  annotated-transcript.html      the transcript — sticky chapter rail, in-text search
  episode.package.json           single source of truth for both pages
  metadata.sidecar.json          audit trail + verified terminology
  transcript.verified.md         markdown transcript
source/                          inputs + intermediate LLM drafts
working/                         scratch (timestamps, anchor logs)
```

The slug is derived automatically from the URL + guest name (e.g. `lenny-ries-incorruptible`). `podcast-output/index.html` is regenerated to list every episode in the library.

## The bar (run before reporting "done")

Every successful run must satisfy:

- All 5 deliverables present in `final/`.
- All four validators report no failures (warnings tolerated):
  - `sidecar.py validate --strict`
  - `transcript_lint.py`
  - `notes_lint.py`
  - `podcast_build.py validate` (the banned-text guard for AI slop patterns)
- Any unclear transcript markers are either absent or matched by sidecar `verification.uncertain_spans` entries. Coverage is by marker text, not by count.
- `episode.notes.json` has **8 claims** and **8 takeaways** (8±1 acceptable; under 6 means the draft model regressed — re-run).
- Every chapter in the chapter rail anchors to a distinct turn, OR the duplicate-anchor warnings are accepted as "long-answer artifacts" (one Eric-Ries-style monologue covers 3+ chapters).
- Every entity in the inspector has a real category (`person | company | organization | book | concept | quote`). Anything stuck in `concept` with empty notes is a categorization failure — run `enrich_terminology.py` explicitly.
- Spot-check: open the briefing in a browser and look for slop. The validator catches the known patterns but a human read still wins.

## Pipeline

`extract_one.py` runs these steps as instrumented subprocess calls. Don't reimplement any of them in-loop.

| # | Step                              | Kind   | What it does                                                          |
|--:|-----------------------------------|--------|-----------------------------------------------------------------------|
|  0 | `url_ingest.py`                   | code   | For supported URLs, fetch page/transcript and create the local source package. |
|  1 | `parse_source.py`                 | code   | URL ranking, chapter extraction, host/guest/title heuristics, link list. |
|  2 | `fetch_transcript` fallback       | code   | Advanced fallback only when no transcript was prepared. |
|  3 | `convert_transcript.py`           | code   | Substack timestamp+name+body blocks → `Speaker: text` canonical form. |
|  4 | `sidecar.py init`                 | code   | Bootstrap `metadata.sidecar.json` with episode metadata.              |
|  5 | `sidecar_chapters.py`             | code   | Populate `episode.chapters` from the parsed timeline.                 |
|  6 | `draft_notes.py`                  | LLM    | Ollama bulk draft of `episode.notes.json` (bottom_line, takeaways, claims). Retries once if takeaways < 6. |
|  7 | `sharpen_notes.py`                | LLM    | Per-item topic + takeaway rewrite for punchier framing. Each call sees one item only so it can't drift off-subject. |
|  8 | `anchor_chapters_proportional.py` | code   | `chapter_queries` via within-turn proportional offsets (no dup-collisions). |
|  9 | `populate_terminology.py`         | LLM    | Enumerate ~25-35 entities (people / orgs / books / concepts).         |
| 10 | `enrich_terminology.py`           | LLM    | Categorize + describe any entries with empty notes.                   |
| 11 | `merge_terminology_urls.py`       | code   | Attach show-notes URLs to terminology entries (truncation-safe; Wikipedia URLs reconstructed from entity name when sliced). |
| 12 | `podcast_build.py all`            | code   | Merge generated uncertainty spans, render both HTML files, regenerate library index, run all validators. |

End-to-end wall-clock on a typical 1h40m episode: **~3 minutes** on an M-series Mac with both Ollama models warm.

## Process

What the agent does when invoked:

1. **Resolve `$ARGUMENTS` to a URL or absolute path.** If empty, ask the user for the episode URL or resource file.
2. **Run prerequisite checks** (see Prerequisites above). Stop with a specific fix if anything fails.
3. **Run the pipeline:**
   ```bash
   python3 podcast-transformer/scripts/extract_one.py "$INPUT"
   ```
4. **Read the scorecard** from stderr (or `podcast-output/<slug>/working/_pipeline_metrics.jsonl` for the detailed split).
5. **Validate the bar** (see The bar above). If any check fails, report which step failed and stop. Don't paper over it.
6. **Report back briefly:**
   - Path to the briefing HTML (`podcast-output/<slug>/final/podcast-at-a-glance.html`)
   - Wall-clock total
   - Validator status (one line)
   - Counts: takeaways / claims / terminology entries

Keep the report short. The artifact is the deliverable, not the chat message.

## Recovery

- **Draft has <6 takeaways or <6 claims** → re-run `draft_notes.py --force` once. The model is stochastic and occasionally produces thin output.
- **Ollama call timed out** → check `ollama ps` for stuck loads; restart `ollama serve` if needed. Don't switch backends silently.
- **URL ingest reports unsupported domain** → ask the user for a local resource file or transcript and continue with the file-based workflow.
- **URL ingest found metadata but no transcript** → ask the user for the transcript text/file, or use a rendered-DOM fallback only if the user wants to debug that provider.
- **Direct HTTP misses rendered transcript content** → prefer a rendered-DOM fallback in this order: Chrome headless `--dump-dom`, local Playwright, Browserless `/smart-scrape` if `BROWSERLESS_TOKEN` is available, Browserless BrowserQL for selector or network-response capture, Firecrawl scrape if `FIRECRAWL_API_KEY` is available, Browser Use Cloud CDP if `BROWSER_USE_API_KEY` is available, then `browse-cli` for users who already have it configured.
- **`browse-cli` returned <1000 bytes** → the user probably isn't signed into the publisher site in their default browser. Tell them to open the URL in Chrome and confirm the transcript loads, then retry.
- **JSON parse error in a model output** → `draft_notes.extract_json` already attempts a repair for the common "missing `}` before `]`" case. If repair fails, the raw output is saved to `working/_*_draft_raw.txt` for inspection.
- **Validator flags AI slop** → the renderer's banned-text guard fired (e.g. "TOPIC LENSES", "AT A GLANCE", "Built for", left-handle accent bars). Fix the renderer in `podcast-transformer/assets/podcast-html/`, don't suppress the check.

## Anti-patterns

- **Don't echo the transcript back through a `Write` call.** The user already has the bytes; URL ingest or local files should create `source/user-provided-transcript.txt`. Echoing a 100KB transcript through tool input takes minutes and bloats the conversation.
- **Don't hand-author per-episode HTML.** The renderer is one file. Update assets in `podcast-transformer/assets/podcast-html/` and re-run.
- **Don't second-guess user-provided inputs.** A user transcript means skip transcription. User-supplied speaker names mean don't re-verify against voice characteristics. User `episode.notes.json` means trust its claims.
- **Don't switch backends silently.** If Ollama is down, surface the error with the fix. Don't fail over to a paid API the user didn't ask for.
- **Don't extend the pipeline in-loop.** New behavior belongs in a new script wired into `extract_one.py`, not in inline shell commands the user has to repeat next time.

## Configuration

Three env vars. Defaults work out of the box.

| Env var                  | Default                     | Notes                                              |
|--------------------------|-----------------------------|----------------------------------------------------|
| `PODCAST_DRAFT_MODEL`    | `gemma4:e4b-nvfp4`          | Bulk drafting + entity enumeration.                |
| `PODCAST_SHARPEN_MODEL`  | `qwen3.6:35B-a3b-nvfp4`     | Per-item rewrites + entity categorization.         |
| `PODCAST_OLLAMA_URL`     | `http://localhost:11434/api/chat` | Point at a remote Ollama if you don't want both models locally. |

Dump the active config any time:

```bash
python3 podcast-transformer/scripts/extract_one.py --show-config /dev/null
```

For the paid Anthropic API as a draft fallback, pass `--draft-backend api` with `ANTHROPIC_API_KEY` set.

## Where to find code + design docs

- `podcast-transformer/scripts/` — every step in the pipeline as a standalone script
- `podcast-transformer/scripts/pipeline_config.py` — config knobs in one file
- `podcast-transformer/assets/podcast-html/` — shared CSS, JS, and the two HTML templates
- `podcast-transformer/references/` — design principles, schema contracts, verification workflow
