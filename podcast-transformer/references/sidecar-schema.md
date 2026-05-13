# Metadata Sidecar Schema

Use `metadata.sidecar.json` as the audit trail for a transformed episode. The file should be valid JSON and stable enough for future automation.

Companion docs: [notes-schema.md](notes-schema.md) covers the curated `source/episode.notes.json` contract that drives the renderer alongside this sidecar.

## Top-Level Shape

```json
{
  "schema_version": "podcast-transformer/v1",
  "created_at": "2026-05-12T15:30:00Z",
  "updated_at": "2026-05-12T16:10:00Z",
  "episode": {},
  "inputs": [],
  "transcription": {},
  "verification": {},
  "outputs": {}
}
```

## Episode

Recommended fields:

- `title`: episode title.
- `podcast_title`: show title.
- `episode_url`: canonical episode page.
- `rss_url`: RSS/feed URL when known.
- `description`: original episode description or concise extracted description.
- `published_at`: ISO 8601 date/time or date string.
- `duration_seconds`: numeric duration when known.
- `hosts`: array of host names.
- `guests`: array of guest names.
- `language`: BCP 47-ish language code such as `en`.
- `chapters`: array of `{ "start": 0, "title": "Intro" }`.

## Inputs

Each input should record where the evidence came from.

```json
{
  "type": "media",
  "path": "source/episode.mp3",
  "sha256": "hex...",
  "bytes": 12345678,
  "collected_at": "2026-05-12T15:30:00Z",
  "notes": "Original file supplied by user"
}
```

For URLs, use `url` instead of `path`:

```json
{
  "type": "companion_page",
  "url": "https://example.com/show-notes",
  "title": "Episode show notes",
  "collected_at": "2026-05-12T15:31:00Z",
  "used_for": ["links", "guest names", "terminology"]
}
```

Common input types: `media`, `episode_page`, `companion_page`, `rss`, `existing_transcript`, `manual_note`, `web_search_result`, `official_source`.

## Transcription

Recommended fields:

- `engine`: provider/tool name such as `whisper.cpp`, `openai`, `local-whisper`, or `manual`.
- `model`: model identifier.
- `language`: detected or requested language.
- `started_at` and `completed_at`: timestamps.
- `raw_transcript_path`: path to raw model output.
- `verified_transcript_path`: path to corrected transcript.
- `segment_transcript_path`: path to canonical JSON segments when available.
- `diarization`: object with `enabled`, `method`, and `notes`.
- `context_terms`: terms supplied to the transcription engine or used during correction.

## Verification

Recommended fields:

- `status`: `draft`, `in_review`, `needs_review`, or `verified`.
- `verified_at`: timestamp set only when the package is ready.
- `verified_by`: `Codex`, user name, or reviewer label.
- `methods`: array such as `metadata`, `companion_page`, `official_sources`, `web_search`, `contextual_audio_review`.
- `sources`: array of source records with `url` or `path`, `title`, `accessed_at`, and `used_for`.
- `terminology`: array of corrected/verified terms.
- `people`: array of people records with `name`, `role`, `source`, and `confidence`.
- `organizations`: array of organization records.
- `uncertain_spans`: array of unclear transcript spans.
- `corrections`: array of major corrections from raw to verified transcript.

Example terminology record:

```json
{
  "term": "CRISPR-Cas9",
  "category": "concept",
  "raw_heard_as": "crisper cast nine",
  "source": "https://example.edu/research",
  "url": "https://en.wikipedia.org/wiki/CRISPR-Cas9",
  "confidence": "high",
  "notes": "Guest's lab page uses this spelling"
}
```

Terminology field reference:

- **`term`** *(required)* — canonical spelling.
- **`category`** *(recommended)* — one of: `person`, `company`, `organization`, `concept`, `book`, `health`, or any other string. The renderer groups by category in the briefing's entity inspector; known categories use a curated order, others fall back to alphabetical.
- **`url`** *(optional)* — canonical link for this entity (homepage, Wikipedia entry, publisher page). When present, the briefing renders the term name as a clickable external link. Harvest from the publisher's companion page where possible; don't fabricate URLs.
- **`source`** — URL or path supporting the spelling/identification.
- **`confidence`** — `high`, `medium`, `low`, `primary`.
- **`notes`** — one-line description; renders in the briefing's inspector under the term name.
- **`raw_heard_as`** — optional, what the STT engine produced before correction.

Example uncertain span:

```json
{
  "timestamp": "00:18:42",
  "speaker": "Guest",
  "text": "[unclear 00:18:42]",
  "reason": "Background noise covers a name",
  "resolution_needed": "Ask user or inspect higher-quality audio"
}
```

## Outputs

Recommended fields:

- `verified_transcript_md`: `final/transcript.verified.md`.
- `verified_transcript_json`: `final/transcript.verified.json` when available.
- `metadata_sidecar`: `final/metadata.sidecar.json`.
- `annotated_transcript_html`: `final/annotated-transcript.html`.
- `summary_html`: `final/podcast-at-a-glance.html`.
- `verification_notes`: `final/verification-notes.md`.

## Status Semantics

Use `draft` while ingesting or transcribing. Use `in_review` after a corrected transcript exists but validation or human review is pending. Use `needs_review` when unresolved uncertain spans materially affect meaning. Use `verified` only when terminology, names, sources, and deliverable paths have been checked and known uncertainties are explicitly documented.
