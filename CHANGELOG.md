# Changelog

## 1.0.3 - 2026-05-21

- Adds direct URL-ingest support for the Dwarkesh Podcast (dwarkesh.com), which runs on Substack under a custom domain and routes through the shared `substack` provider kind.
- Adds `resolve_speaker_aliases.py`: maps anonymous `SPEAKER_NN` diarization labels to real participant names when a Substack post ships without a `speaker_map`. A no-op for providers whose transcripts already carry named speakers.
- Hardens `populate_terminology.py`: caps the entity list at 35, salvages a truncated terminology array instead of hard-failing, and never drops publisher-linked entries when trimming.
- The briefing's right-hand sidebar can now be collapsed via a topbar toggle; the preference persists per reader.

## 1.0.2 - 2026-05-14

- Adds direct URL-ingest support for Conversations with Tyler.
- Preserves publisher canonical URLs from URL-ingest bundles when platform links such as YouTube or Apple Podcasts are also present.
- Allows normal WordPress pages with form-related JavaScript noscript warnings through the fetch guard.

## 1.0.1 - 2026-05-14

Patch release for the main library index.

- Extracts FoundMyFitness publish dates from `episode_date` blocks such as `Posted on March 24th 2026`.
- Also recognizes common meta and JSON-LD publish-date fields during URL ingest.
- Ensures FoundMyFitness dates flow into `_source_input.txt`, sidecar metadata, package JSON, and `podcast-output/index.html`.

## 1.0 - 2026-05-14

First stable release of the URL-first podcast artifact pipeline.

- Supports direct ingest for Lenny's Newsletter/Substack, The New Yorker Radio Hour, FoundMyFitness, 99% Invisible, and The Tim Ferriss Show.
- Produces a self-contained briefing, annotated transcript, renderer package JSON, verified transcript Markdown, and metadata sidecar for each episode.
- Uses structured transcript turns when provider ingest can produce them, with text parsing fallback for local transcripts.
- Renders transcript speaker labels only on speaker change.
- Validates rendered artifacts, notes, transcript shape, sidecar metadata, chapter anchors, and known renderer regressions.
- Tracks unclear transcript markers through `verification.uncertain_spans` with marker-text coverage checks.
- Retains local Ollama as the default drafting and enrichment backend, with API fallback available by flag.
