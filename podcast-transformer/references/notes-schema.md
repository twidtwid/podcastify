# `episode.notes.json` Schema

The renderer's data contract. `source/episode.notes.json` carries the human-curated facts that turn a raw transcript into a usable briefing. Every field below is consumed by `scripts/podcast_build.py` or by the HTML renderer. Validator: `scripts/notes_lint.py`.

This file is curated, not derived. Fields you can compute from the transcript or the sidecar live there; fields a person (or a careful LLM pass) must write live here.

## Top-level shape

```json
{
  "short_title": "Elad Gil on AI, Markets, and Company Building",
  "description": "Conversation with Elad Gil about AI markets, venture investing, ...",
  "built_for": "An investor friend catching up on AI and investing.",
  "bottom_line": "This episode is about deciding what is actually durable in a fast-moving AI cycle: scarce talent, compute bottlenecks, ...",
  "takeaways": [ "...", "..." ],
  "claims": [ { "topic": "...", "claim": "...", "evidence": "..." } ],
  "links": [ { "label": "...", "url": "...", "kind": "...", "note": "..." } ],
  "keyword_queries": { "label": "substring in transcript", ... },
  "chapter_queries": [ "substring in transcript per chapter" ]
}
```

## Field rules

### `short_title` *(required)*

A reading-friendly title under ~60 chars. Renders as the page H1. Commas are visually emphasized in the briefing's hero: each comma-separated phrase gets a different color, so phrase the title so the commas land on meaningful divisions.

### `description` *(required)*

One or two sentences. Renders as the small grey paragraph under the title in the briefing hero. Should describe what the conversation *is*, not the position it argues — that's `bottom_line`.

### `built_for` *(optional)*

One sentence naming the audience and the job this page is meant to serve. Example: *"An investor friend catching up on AI and investing."*

**Rendering status:** the field is **carried through to `episode.package.json`** so future surfaces (alternate page modes, search snippets, prompt templates) can use it, but the current briefing page does not render it. An earlier "BUILT FOR" card was removed because it tended to become navigation-help text rather than audience-and-purpose framing. Keep authoring `built_for` — `notes_lint.py` checks it for invisible-UI references — but don't expect it on the page today.

### `bottom_line` *(required)*

The episode's central thesis in 1–3 sentences. Renders as the italic blockquote in **The thesis** card on the briefing. Should state the position the speaker actually argues. If the episode has multiple arguments, pick the one a reader will remember a week later.

### `takeaways` *(required, 5–8 items)*

Each item is one sentence. Renders as a stacked numbered list in **What to take away**.

Rules:
- First-person-useful. A reader should be able to repeat a takeaway in conversation.
- Not a section summary. "They discussed AI markets" is not a takeaway.
- Each one stands alone — no "as mentioned above" links between them.

### `claims` *(required, 3–6 items)*

Each claim has three fields. Renders as cards in **Notable claims**.

```json
{
  "topic": "Why the AI market may stay concentrated",
  "claim": "Compute, top researchers, training data, and customer distribution are each scarce — and each scarcity compounds the others. ...",
  "evidence": "Compute bottlenecks, AI pay packages, and historic revenue ramps chapters; HBM and CoWoS named as physical limits."
}
```

- **`topic`** — a position-establishing sub-headline, 4–10 words. State what is argued, not what category it belongs to. **Good:** *"Why the AI market may stay concentrated."* **Bad:** *"AI market structure"* (category label). **Bad:** *"Longevity"* (one word).
- **`claim`** — the mechanism, evidence, or consequence that supports the topic. **Do not paraphrase the topic in the claim.** If the topic asks *why*, the claim must explain *why*. A claim that just restates the topic with extra words is a content failure.
- **`evidence`** — name the specific moment, chapter, framing, data point, or example that backs the claim. **Good:** *"Exit timing chapter; xAI/Cursor and Scale/Meta cited."* **Bad:** *"Episode transcript"* — that names the source, not the evidence within it.

### `links` *(optional)*

External resources referenced in the episode. Render in **Read next** on the briefing. Each link:

```json
{
  "label": "Tim.blog show notes",
  "url": "https://tim.blog/2026/04/29/elad-gil/",
  "kind": "source",
  "note": "Canonical companion page with links and resources."
}
```

- **`label`** — human-readable name.
- **`url`** — full URL.
- **`kind`** — optional taxonomy: `source`, `person`, `book`, `podcast`, `paper`, `product`, `company`. Not currently rendered, but reserved.
- **`note`** — optional one-line description; renders under the label.

Special labels the renderer looks for in the topbar's folder tabs (regex match, case-insensitive):
- `show notes` → "Show notes" external folder tab
- `official transcript` → "Official transcript" external folder tab

### `keyword_queries` *(optional)*

A label → transcript substring map. Used historically for an in-page keyword index; the current renderer does not display these, but `podcast_build.py` keeps them in the package JSON for downstream tools.

### Chapter source priority

When a chapter timeline is available, prefer in this order:

1. **YouTube auto-chapters / video-description timestamps.** These are per-second accurate (HH:MM:SS) and tied to actual audio positions. If the episode has a YouTube version with a chapter timeline, that's the gold source. Parse with `scripts/ingest_combined.py` if the user dropped them in a combined input file.
2. **Publisher show-notes timestamps.** Often coarser (MM:SS or rounded to nearest 30s) but trustworthy when YouTube isn't available.
3. **Sidecar chapters with estimated `start` values.** Use the no-audio workflow below — chapter_queries do the real anchoring, timestamps become labels.

### Combined input convention

`scripts/ingest_combined.py` accepts a single text file that bundles URL, optional chapter timeline, and transcript in one paste:

```
https://example.com/episodes/foo

Timeline:
- [00:02:04](https://www.youtube.com/watch?v=...&t=124s) - The three macronutrients of happiness
- [00:03:57](https://www.youtube.com/watch?v=...&t=237s) - Why chasing pleasure alone won't make you happy
...

Roman Mars: This is 99% Invisible. I'm Roman Mars...
Chris Berube: Hey, Roman.
...
```

Required:
- The first `https?://` URL anywhere becomes the canonical episode URL.
- Chapter lines match `- [HH:MM:SS](optional-url) - Title` (or `- [MM:SS]...`).
- The transcript section starts at the first `Speaker:` line and runs to end-of-file.

Run: `python3 scripts/ingest_combined.py <combined-file> <episode-dir>`. It writes the transcript to `source/user-provided-transcript.txt` and stashes the URL + chapters in `working/_parsed.json` for sidecar init.

### `chapter_queries` *(strongly recommended when chapters exist)*

A list parallel to `episode.chapters` in the sidecar. Each entry is a short substring expected to appear in the transcript turn where that chapter begins.

- One query per chapter. Order matches the chapter order in the sidecar.
- The query is **substring-matched, case-insensitive** against each turn's `speaker + text`.
- If a query is missing or doesn't match, the renderer falls back to a word-proportional time estimate.
- The build script enforces **strict monotonic anchoring**: each chapter must land on a turn AFTER the previous chapter's turn. Two chapters cannot share the same anchor turn. If your queries produce duplicate anchors, the second chapter's anchor is bumped forward by one turn — choose more specific queries to avoid this.

Choose queries that are **unique to a single turn**. A short distinctive phrase from the speaker who introduces the topic works well.

## Entity links: `verification.terminology[].url`

Not in `notes.json` — lives in the sidecar's `verification.terminology` array. See [sidecar-schema.md](sidecar-schema.md). When a term has a `url`, the briefing's right-column inspector renders the term name as a clickable link with an external-link arrow.

For each episode, harvest canonical URLs for entities from the publisher's companion page (e.g. show notes) into `terms[].url`. Per term:
- People: their canonical site or Wikipedia entry
- Companies: their homepage
- Concepts / tech: Wikipedia or an authoritative explainer
- Books: publisher page or Amazon

If a canonical URL is not obvious, omit the field. Don't fabricate Wikipedia URLs that don't exist.

## When there's no audio file

Sometimes the user hands you a transcript without an audio file (a publisher-released transcript, a podcast you don't have the .mp3 for, etc.). The pipeline still works — adjust how you fill these fields:

- **`episode.duration_seconds`** *(sidecar)* — set to a rough estimate (`words ÷ 175 × 60` for typical conversational pace). This becomes the byline duration ("38m") on both pages. If you can't estimate, set `null` and the byline omits duration.
- **`episode.chapters[].start`** *(sidecar)* — distribute proportional timestamps across the duration estimate. Make them **monotonically increasing** and roughly evenly spaced. These show up as labels in the chapter rail — they don't drive any audio navigation, so approximate is fine.
- **`chapter_queries`** — this is the load-bearing field when there's no audio. Each query must match a distinctive substring of the transcript turn where the chapter begins. The build script anchors chapters to actual turns via these queries, regardless of the timestamps. **Specific is better than short.** Quote a vivid phrase the speaker uses, not a generic word.
- Sidecar verification status — note in `verification.uncertain_spans` or `verification-notes.md` that timestamps are estimated, not measured.

What you do **not** do without audio:
- Run STT or diarization tools (you don't have audio to transcribe).
- Set `transcription.engine` to anything other than `manual`.
- Treat the chapter timestamps as accurate to the second — they're labels, not anchors.

The 99% Invisible #666 (Enshittification) internal test episode was built this way and serves as the reference shape, but generated outputs are intentionally not committed to public history.

## Required minimum for a usable episode

To produce a non-degraded briefing, the renderer needs:

- `short_title`, `description`, `bottom_line`
- 5+ `takeaways`
- 3+ `claims` (with non-trivial topic/claim/evidence triples)
- At least one entry in the sidecar's `verification.terminology` (so the inspector has something to show)
- `chapter_queries` aligned with `episode.chapters` if you want clean transcript navigation

Without these, the page renders but reads thin. Lint with `python3 scripts/notes_lint.py source/episode.notes.json`.
