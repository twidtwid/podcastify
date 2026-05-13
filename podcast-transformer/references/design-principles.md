# Design Principles

The rules that shape the briefing and transcript artifacts. Each rule exists because the first draft violated it and the result was unusable. Keep these rules durable across episodes — an LLM run that ignores them will produce content the user will throw away.

## Content vs. metadata

The page **replaces** the podcast for the reader. Metadata about the recording is not content about the episode.

**Do not surface:**
- Counts of chapters, takeaways, claims, terms.
- Speaker word share, talk-time percentages, transcript word count as a hero stat.
- Per-chapter timestamps as a primary visual ("the conversation arc"). Timestamps belong in the transcript's chapter rail because they help navigate; on the briefing they're noise.
- The fact that a thing exists ("Found 6 claims") instead of the thing itself.

**Do surface:**
- Names of people, companies, concepts, books, methods.
- The actual claims, with the mechanism that makes them true.
- The frameworks the speaker uses to make decisions.
- External links to the canonical source for each named entity.

## Headlines, not labels

Section titles and claim topics must be **headlines that argue something**, not category labels.

- **Good claim topic:** *"Why the AI market may stay concentrated."*
- **Bad claim topic:** *"AI market structure."* Bad: *"Longevity."* Bad: *"Markets."*

A one-word category tells the reader nothing about the position. If you can't write a 4–10 word position-establishing headline, you don't yet understand what the speaker argued.

## Bodies must add information beyond their headline

The claim body's job is to do the work the headline promised. If the headline says *"Why the AI market may stay concentrated,"* the body must explain **why** — the mechanism, evidence, or consequence. A body that paraphrases the headline in more words is intellectually lazy and visibly so.

Same for takeaways: each takeaway should be a sentence a reader could repeat in conversation, not a summary of "what the episode covered."

## No left-handle bars

A 3–4px accent-colored vertical line on the left edge of a card or quote is the visual fingerprint of AI-generated layouts. People notice it and discount the whole page. Don't use it.

Convey emphasis through:
- Typography (size, weight, italic, family)
- Whitespace and rules (horizontal dividers between sections)
- Color (text color shifts, not bar accents)
- Position in the layout

Acceptable exceptions: a horizontal rule between sections (top border) is fine. A pull-quote can use larger italic type without a bar.

## Cards stack as columns, not as grids of identical tiles

Multiple cards in a row of identical size and shape (metric tiles, takeaway grids, lens chips) reads as a SaaS dashboard. The briefing is a document, not a dashboard. Stack content vertically inside a column; use the third column for the inspector, not for more decorative tiles.

## Folder-tab nav, durable across pages

The topbar's folder tabs are the durable navigation. Rules:

- The same set of tabs appears on every page (briefing + transcript + external publisher resources).
- The **active** tab has paper-color fill, ink-color text, and **no line** below — the folder opens into the page.
- **Inactive** tabs sit behind with paper-tint fill, muted text, and the topbar's bottom border visible under them (the closed folder edge).
- External tabs include a small `↗` arrow indicator and open in a new window.
- Internal tabs (Briefing ↔ Transcript) stay in the same window.

The metaphor is a physical manila folder stack. Inverting active/inactive (line under active, no line under inactive) breaks the metaphor.

## External links open in new windows

Any `<a>` whose `href` is `http(s)://...` opens with `target="_blank" rel="noopener noreferrer"`. The briefing is a reference page; users come back to it. Internal anchors and relative URLs (Briefing ↔ Transcript) stay in the same window.

## In-text search, not whole-element highlighting

When the user searches the transcript, mark the actual occurrences with `<mark>` and scroll to the first one. Highlighting an entire 400-word turn because the term appears somewhere in it is the wrong UX — the visible part of the turn often doesn't contain the term.

## Reading column is narrow; chrome can be wider

Prose maxes at ~46rem (~70ch). The topbar and the studio-level container can extend to 90rem. The reading column stays anchored left-of-center on wide screens; the right column is whitespace or the entity inspector.

## Strict monotonic chapter anchoring

Each chapter must anchor to a distinct turn, in order. Two chapters cannot share an anchor turn — the second would render with no content under it. The build script enforces this; chapter queries should be specific enough that they don't all match the same long turn.

## Whitespace and typography do the work

Default to:
- Single serif body face (Charter, Iowan Old Style, Palatino, Georgia)
- Single sans face for chrome (system-ui)
- Generous line-height (1.5–1.7)
- Real horizontal rules between sections, not box borders
- Cream background (`#faf6ef`), ink text (`#1a1815`), one accent color

If you find yourself adding a third font, a fourth accent color, or a fifth card style, stop. The page reads better with less.

## Programmatic guards

`scripts/podcast_build.py validate` and `scripts/notes_lint.py` check for known slop patterns. If you add a new visual rule, add a check so the next regression fails the build.

Banned strings currently flagged in rendered HTML:
- `Copy turn`, `audio navigation`, `overflow: auto`, `max-height: 360px` (old patterns we explicitly removed)
- `TOPIC LENSES`, `Conversation arc`, `AT A GLANCE` followed by a stat-tile structure, `Who's talking`, `Topic Lenses` (slop patterns we removed)

Banned content patterns flagged in `notes.json`:
- Claim topic that is one word or fewer than 3 words
- Claim body that is byte-identical to (or a substring of) the topic
- Takeaway that's shorter than 8 words
- `built_for` text that includes phrases like "use the inspector" (references internal UI that the reader cannot see)
