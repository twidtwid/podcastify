# Transcript Verification

The goal is a transcript that is accurate enough to cite, search, and annotate, not merely a plausible STT cleanup. **Verification has a time budget. Mark uncertain spans liberally rather than chase them.**

## Evidence Priority

Use this hierarchy when correcting names, terms, and links. Stop climbing once the term is supported — do not re-confirm:

1. Episode metadata, RSS feed, title, description, chapters, and official show notes (the URLs already in `inputs` — usually 1–3 fetches).
2. Companion website explicitly associated with the episode (one more fetch).
3. Guest's own site or a reputable disambiguation source (one more fetch, only when (1) and (2) are silent on the term).
4. Web search — only for material terms still uncertain after (1)–(3). **Cap: 3 web searches per episode.** Beyond that, mark the term uncertain and move on.

Do **not** open official pages for every book, paper, company, or product mentioned. Names with `confidence: medium` backed by the companion page are good enough.

## Correction Workflow (single pass)

1. Build the candidate term list in **one** pass from the already-collected sources: hosts, guests, organizations and books/papers explicitly named in chapter titles or show notes.
2. Grep the raw transcript for phonetic variants and inconsistent spellings of those candidates.
3. Apply corrections backed by sources already collected. Record decisions in `verification.terminology` / `verification.people` / `verification.organizations` / `verification.corrections`.
4. For anything ambiguous, mark `[name uncertain]` / `[term uncertain]` and add to `verification.uncertain_spans`. **Do not** open new browser tabs mid-pass to resolve.
5. Preserve transcript meaning. Do not rewrite spoken language into polished prose except for clear transcription errors.
6. Keep timestamps aligned. If reflowing paragraphs, retain timestamp anchors at meaningful intervals.
7. Treat ads, intro music, cross-talk, and background speech explicitly. Mark skipped non-content sections as `[ad break]`, `[music]`, or `[cross-talk]` when useful.

## Ambiguity Handling

Use uncertainty markers rather than guessing:

- `[unclear 00:12:04]` when the words cannot be heard.
- `[name uncertain: Jane? 00:24:17]` when a plausible name is not source-backed.
- `[term uncertain]` when a technical term has multiple plausible spellings.

Add each material uncertainty to `verification.uncertain_spans` with timestamp, speaker, reason, and the best next step.

## Speaker Labels

Use real speaker names only when evidence supports them. Evidence can include episode metadata, host introductions, distinct recurring voices, or a companion transcript.

If speaker identity is unknown, use stable neutral labels (`Speaker 1`, `Speaker 2`) and explain the limitation in the sidecar. Do not assign a guest name to a voice solely because the episode description names that guest.

## Verification Notes

Create `verification-notes.md` when the episode has meaningful corrections or unresolved ambiguity. Keep it short:

- Sources consulted.
- Major terms and names corrected.
- Unresolved spans and why they remain unresolved.
- Any assumptions used for chapters, speakers, or timestamps.

## Final Read-Through

Before calling the transcript verified, scan for:

- Repeated STT artifacts such as duplicated phrases, hallucinated outros, or impossible names.
- Unexpanded acronyms that are central to the episode.
- Names that differ between title, description, and transcript.
- Terminology that appears in multiple spellings.
- Timestamps that jump backwards or disappear for long sections.
- Speaker labels that change for the same person.
