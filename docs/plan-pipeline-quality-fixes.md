# Pipeline quality fixes — speaker, sponsor, bio, leak

Driver: the Codex Goals (How I AI) extract shipped with the host mislabeled (Lenny
instead of Claire Vo), "the guest" framing on a solo episode, the ad-read sponsor
(Mercury) listed as a substantive company, a blank host bio, and an absolute
`/Users/...` path baked into every render. These are systemic pipeline bugs, not
one-offs. Fix each at its source; the acceptance gate is a clean end-to-end re-run
with NO hand-editing.

## Bugs → fixes

1. **Host precedence inversion** — `extract_one.py:resolved_participants`
   Precedence is `CLI > parse byline (host_guess) > resolve_speakers`. The Substack
   "Host:" byline names the *publication owner* (Lenny), beating the transcript LLM
   that correctly reads "I'm Claire Vo". 
   **Fix:** `CLI > resolve_speakers (transcript) > parse byline`. The transcript is
   ground truth for who actually speaks. Always run resolve_speakers unless `--host`
   given (drop the byline-based `can_skip`). Keep byline as fallback when LLM empty.

2. **"guest" framing hardwired** — `draft_notes.py`, `sharpen_notes.py` prompts
   Prompts say "the guest" ~15×, so solo episodes still read "the guest ran…".
   **Fix:** make prompts speaker-aware. Pass a `speaker_label` derived from metadata
   (host name when solo / "the host and <guest>" for interviews) and instruct the
   model to attribute to the named speaker, never a generic "guest", and to detect
   solo vs interview from the turns.

3. **Ad-read sponsor pollution** — `populate_terminology.py` (+ draft prompt)
   No ad-read exclusion, so "brought to you by Mercury" becomes a company entity.
   **Fix:** instruct the terminology + notes prompts to identify sponsor/ad-read
   segments ("this episode is brought to you by", "sponsored by", "promo code") and
   EXCLUDE those brands from entities/claims/takeaways unless discussed on the merits.

4. **Blank host bio** — `populate_terminology.py`
   The "About the host" card renders a bio only when a terminology *person* entry
   matches the host name. None is guaranteed.
   **Fix:** terminology prompt MUST emit a `person` entry (with a real bio note) for
   every host and headline guest in episode metadata.

5. **Absolute path leak** — `podcast_build.py` (`"episode_dir": str(episode_dir.resolve())`)
   Bakes `/Users/<user>/...` into package + both HTML pages → leaks on publish.
   **Fix:** store the basename (slug) only, not the absolute path.

6. **Briefing lacks a graphic/chart** — `assets/podcast-html/artifact.js:renderGlance`
   (enhancement) Add a small inline-SVG theme/topic chart to the briefing so it meets
   the HTML-native bar. Lower priority than 1-5; may land as a follow-up commit.

## Verification gates
- Unit: `pytest tests/ -q` stays green; add/extend tests for #1 (precedence) and #3/#4
  (sponsor-exclude, host-bio) where fixtures allow.
- Validators: `podcast_build.py validate` + sidecar/transcript/notes lints pass.
- **End-to-end:** re-run `extract_one.py` on the Codex URL from scratch; assert the
  output has host=Claire Vo, no "the guest", Mercury absent from entities, a non-empty
  host bio, and no `/Users/` string — all WITHOUT manual edits.

## Commit
One repo (`twidtwid/podcastify`). Commit per-fix or as a coherent batch; push when Todd oks.
