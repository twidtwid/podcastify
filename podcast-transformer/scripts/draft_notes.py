#!/usr/bin/env python3
"""Draft `source/episode.notes.json` from the verified transcript + chapter
outline. Writes `source/episode.notes.draft.json`; the orchestrator promotes
that draft after forcing canonical metadata.

The cognitively expensive step in our pipeline is writing 8 takeaways, ~8
position-establishing claims, and the bottom-line paragraph. With the
verified transcript and chapter outline in hand, a model pass produces a
solid first draft that the user edits down. Cuts per-episode authoring time
from ~30 min to ~10 min of editing.

Inputs:
- `<episode_dir>/source/user-provided-transcript.txt`
- `<episode_dir>/final/metadata.sidecar.json` (for title, hosts, guests, chapters)

Outputs:
- `<episode_dir>/source/episode.notes.draft.json`

Default requirements:
- Ollama running locally with the configured draft model.

Optional paid API fallback:
- `ANTHROPIC_API_KEY` in env.
- Network access; this calls api.anthropic.com.

The API fallback defaults to `claude-haiku-4-5`. Override with
`--model`.

The system prompt encodes our anti-slop rules from `references/design-principles.md`
and the schema from `references/notes-schema.md` so the draft already obeys the
rules `notes_lint.py` would catch:
- claim topics are position-establishing headlines (4–12 words, not category
  labels)
- bottom_line lands a real thesis, not metadata about the episode
- built_for names a specific reader, not a vague audience
- no AI slop fingerprints (left-handle accent bars, dashboard counts, etc.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from pipeline_config import (
    DRAFT_API_MODEL as DEFAULT_MODEL,
    DRAFT_MODEL as DEFAULT_OLLAMA_MODEL,
    DRAFT_NUM_CTX as DEFAULT_OLLAMA_NUM_CTX,
    OLLAMA_URL,
)

API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """You are drafting `episode.notes.json` for a podcast-transformer pipeline that turns a podcast episode into a researcher's reference page (briefing + annotated transcript HTML).

You'll receive the verified transcript and chapter outline. Produce a JSON object that follows the schema below. Be opinionated and specific — never a marketing summary, never an AI assistant explaining what was said. Write the way an excellent ghostwriter would: voiced, dense, grounded in the specific stories and numbers the guest used.

# Exemplar of the bar (study before writing)

This is a single CLAIM from a reference episode. Match this density, specificity, and voice. Notice how the topic is a position-establishing headline, the claim adds mechanism and weaponizes a thought experiment, and the evidence names the *specific case with specific numbers*.

```json
{
  "topic": "Standard VC paperwork already obligates you to sell to anyone",
  "claim": "The 'best practices' incorporation and shareholder-agreement templates that almost every venture-backed company uses encode shareholder primacy as fiduciary duty. In a hostile bid, the board cannot legally refuse the highest offer — even if the buyer is the most evil company they can name. The guest weaponizes this with the 'would you sell to Philip Morris for $1 more a share?' thought experiment; founders' visceral 'hell no' collides with the legal documents they already signed.",
  "evidence": "The Vectura Corporation case: a UK inhaler-therapeutics company that the British Thoracic Society begged not to sell to Philip Morris; the board accepted Philip Morris's bid (165p) over a private-equity bid (155p) citing fiduciary duty to take the highest offer."
}
```

A second exemplar (TAKEAWAY) — note it's a first-person-useful position, not a meta-summary:

> "Your standard VC paperwork already obligates you to sell to Philip Morris for a dollar a share extra. Founders are routinely shocked to learn this; their lawyers framed it as 'best practices' to make fundraising easier."

That's 35 words and carries a specific, repeatable position. Do that.

# Required counts and lengths (NON-NEGOTIABLE — the validator rejects shortfalls)

- **takeaways**: EXACTLY 8 items. Each 30–50 words. NOT under 25 words. NOT over 60 words.
- **claims**: EXACTLY 8 items. Each is a JSON object {topic, claim, evidence}.
  - **topic**: a sentence-case position-statement, **4–12 words**. Stop at 12.
  - **claim**: **80–120 words minimum**. Lead with the position; back it with the mechanism, the specific case the guest cited, and the consequence. A claim under 60 words is a quality failure — REWRITE IT before emitting.
  - **evidence**: **35–65 words minimum**. Evidence under 30 words is a quality failure — REWRITE IT. Name the specific moment, number, AND consequence (e.g., "Vectura Corporation: UK inhaler-therapeutics maker; British Thoracic Society begged them not to sell; the board accepted Philip Morris's 165p bid over a private-equity 155p offer; Philip Morris took a $900 million write-down within three years and dissolved the company"). Two or three concrete data points per evidence entry — not just one. "The episode discusses…" is NOT evidence.
- **keyword_queries**: a JSON **OBJECT** (dict), not an array. Open with `{`, close with `}`. Contains 18-25 `"shortLabel": "substring that uniquely identifies this concept in the transcript"` pairs.
- **links**: leave as `[]` — pipeline fills this from show notes.
- **chapter_queries**: leave as `[]` — pipeline fills this.

Word-count is enforced: aim ABOVE the floor on each, never below.

# Style

- Sentence case for headlines (claim topics, short_title). Not Title Case. "Why founders get ousted" — not "Why Founders Get Ousted".
- Use the guest's own words and concepts. Do NOT coin new terms; if the guest said "financial gravity," use "financial gravity," not "financial-gravity-defeating force".
- Quote specific names, numbers, and stories the guest used. Generic restatements are weak.
- Don't paraphrase the topic in the claim body — that's a slop tell. The claim body should explain the *mechanism* behind the topic, then name the case.

# Anti-slop

- Don't write "X argues that..." / "the guest explores..." / "In this episode, ..." — those are meta-summaries.
- Don't start sentences with "Furthermore," "Moreover," "Additionally," or "In conclusion,".
- Don't use phrases like "navigate complex challenges", "robust framework", "key insights", "core principles" — these are AI tells.
- Don't reach for symmetry or rule-of-three when the guest didn't use one.

# Schema (every field is required unless noted)

```json
{
  "short_title": "5-9 word headline that establishes the through-line",
  "description": "1-2 sentence orientation, 30-60 words. What the episode is about + why a reader would care.",
  "built_for": "1-2 sentence specific reader persona. Concrete, not 'anyone interested in X'.",
  "bottom_line": "3-5 sentence thesis paragraph. The single biggest argument the guest is making, with the why. This is the most-read paragraph on the briefing page.",
  "takeaways": [
    "8-10 declarative one-paragraph takeaways. Each is a position, not a topic. Lead with the assertion. Roughly 30-50 words each.",
    "..."
  ],
  "claims": [
    {
      "topic": "Position-establishing headline, 4-12 words. NOT a category label.",
      "claim": "60-150 word argument-with-reasoning. Why this position, what supports it.",
      "evidence": "30-80 word grounding in the transcript: specific stories, names, numbers the guest cited."
    },
    "8 claims total — distinctive, not overlapping with each other or with takeaways."
  ],
  "links": [
    {"label": "Display text", "url": "https://..."}
  ],
  "keyword_queries": {
    "shortLabel": "longer substring that uniquely identifies this concept in the transcript"
  },
  "chapter_queries": [
    "ignore this field — the pipeline generates it"
  ]
}
```

# Hard rules

1. **No AI slop.** Read these as your inner critic:
   - Don't write claim topics like "How the user thinks" or "About the topic." A topic is a HEADLINE that states a position. ("Agency is the new differentiator." YES. "On agency." NO.)
   - Don't write `built_for` as "Anyone curious about X" — name a specific role and what they're trying to figure out.
   - Don't pad takeaways with throat-clearing ("The guest argues that..."). Just state the position.
   - No metadata-as-content. Don't write takeaways about "the conversation" or "the episode" — write takeaways about the WORLD as the guest sees it.

2. **No false specifics.** If the guest cites a number or name, use it; never invent.

3. **`evidence` is grounded.** Quote specific moments — companies, people, anecdotes, numbers — that appeared in the transcript. Not generic restatements of the claim.

4. **Output ONLY valid JSON.** No prose intro, no markdown fences, no commentary. The first character of your response must be `{` and the last must be `}`.

5. **`chapter_queries` and `links`**: emit empty arrays / objects; the pipeline fills them.
"""


USER_TEMPLATE = """# Episode metadata

**Title:** {title}
**Podcast:** {podcast_title}
**Host:** {host}
**Guest:** {guest}
**Published:** {published_at}
**Duration:** {duration_min} minutes

# Chapter outline

{chapters}

# Verified transcript

{transcript}

---

Output the JSON now."""


def load_sidecar(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fmt_chapters(sidecar: dict) -> str:
    chapters = sidecar.get("episode", {}).get("chapters") or []
    lines = []
    for c in chapters:
        ts = c.get("timestamp") or ""
        title = c.get("title") or ""
        lines.append(f"- {ts}  {title}")
    return "\n".join(lines) if lines else "(no chapters provided)"


def call_claude_api(api_key: str, model: str, system: str, user: str, max_tokens: int) -> str:
    """Legacy paid-API path (still works; only used when --use-api is passed)."""
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    parts = payload.get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def call_ollama(model: str, system: str, user: str, num_ctx: int) -> str:
    """Call a local Ollama model. Uses `format: "json"` to guarantee valid
    JSON output (Ollama's grammar-constrained decoding) so we never see the
    "missing close-brace" failure mode that hit the Claude CLI path."""
    body = json.dumps({
        "model": model,
        "stream": False,
        "format": "json",
        "options": {
            # 0.3 keeps language varied without inventing words; 0.2 caused
            # the model to collapse takeaways into a smaller, denser set.
            "temperature": 0.3,
            "num_ctx": num_ctx,
            # Allow longer output so claim bodies hit the 80-120 word target.
            "num_predict": 8000,
        },
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"content-type": "application/json"},
    )
    # Generous timeout — a long-transcript draft on local hardware can take 1-3 min.
    with urllib.request.urlopen(req, timeout=900) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("message", {}).get("content", "")


_BARE_STRING_OBJECT_RE = re.compile(
    r'\{\s*(?:"(?:[^"\\]|\\.)*"\s*,\s*)+"(?:[^"\\]|\\.)*"\s*,?\s*\}'
)


def _repair_bare_string_object_bodies(s: str) -> str:
    """Rewrite `{ "a", "b", "c" }` (bare strings, no key:value pairs) as
    `{ "a": "a", "b": "b", "c": "c" }`. gemma4 occasionally emits this
    shape when it conflates set/array literals with the JSON-object syntax
    the prompt asks for (most often for `keyword_queries`). Downstream code
    iterates `.items()`, so promoting each bare string to its own
    `key: key` pair keeps the contract intact without losing model intent.
    """
    def replace_body(match: re.Match[str]) -> str:
        body = match.group(0)
        strings = re.findall(r'"(?:[^"\\]|\\.)*"', body)
        return "{" + ", ".join(f"{s}: {s}" for s in strings) + "}"
    return _BARE_STRING_OBJECT_RE.sub(replace_body, s)


def extract_json(raw: str) -> dict:
    """Strip any prefatory text and parse the JSON object. The system prompt asks
    for raw JSON only, but defensively tolerate a leading markdown fence and
    one or two missing close-braces (a recurring failure mode of long-output
    model runs: the final array element drops its closing `}` before `]`)."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json\n"):
            s = s[5:]
        s = s.rstrip("`").strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"No JSON object found in model output. First 200 chars:\n{raw[:200]}")
    candidate = s[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        # Repair 1: insert `}` before a stray `]` when the parser complains
        # about a missing comma right at a `]` token (last claim missed its
        # close-brace).
        if e.msg.startswith("Expecting") and e.pos < len(candidate) and candidate[e.pos] == "]":
            repaired = candidate[:e.pos] + "}" + candidate[e.pos:]
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        # Repair 2: dict closed with `]` instead of `}` (common when the model
        # confuses the array/object types for `keyword_queries`). Swap the
        # offending `]` for `}` and retry.
        if e.msg.startswith("Expecting") and e.pos < len(candidate) and candidate[e.pos] == "]":
            repaired = candidate[:e.pos] + "}" + candidate[e.pos + 1:]
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        # Repair 3: object body containing only bare strings (no key:value
        # pairs). gemma4 emits e.g.
        #     "keyword_queries": { "OpenAI governance", "Sam Altman shift", ... }
        # which trips `Expecting ':' delimiter`. Promote each bare string
        # to its own `"X": "X"` pair.
        if e.msg.startswith("Expecting ':' delimiter"):
            repaired = _repair_bare_string_object_bodies(candidate)
            if repaired != candidate:
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("episode_dir", type=Path, help="podcast-output/<slug>")
    ap.add_argument("--model", default=None,
                    help="model alias: ollama default 'gemma4:e4b-nvfp4'; API default 'claude-haiku-4-5'")
    ap.add_argument("--max-tokens", type=int, default=8000,
                    help="max tokens (API-mode only; ignored in Ollama mode)")
    ap.add_argument("--num-ctx", type=int, default=DEFAULT_OLLAMA_NUM_CTX,
                    help="Ollama context window in tokens (default 65536)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing draft file")
    backend = ap.add_mutually_exclusive_group()
    backend.add_argument("--use-ollama", action="store_true",
                         help="use local Ollama (default backend; explicit flag for clarity)")
    backend.add_argument("--use-api", action="store_true",
                         help="use the paid Anthropic API instead of local Ollama")
    args = ap.parse_args(argv)

    ep = args.episode_dir.expanduser().resolve()
    transcript_path = ep / "source" / "user-provided-transcript.txt"
    sidecar_path = ep / "final" / "metadata.sidecar.json"
    out_path = ep / "source" / "episode.notes.draft.json"

    for p in (transcript_path, sidecar_path):
        if not p.exists():
            print(f"ERROR: {p} not found", file=sys.stderr)
            return 2
    if out_path.exists() and not args.force:
        print(f"ERROR: {out_path} already exists; pass --force to overwrite", file=sys.stderr)
        return 2

    sc = load_sidecar(sidecar_path)
    ep_meta = sc.get("episode", {})
    user_prompt = USER_TEMPLATE.format(
        title=ep_meta.get("title", "(no title)"),
        podcast_title=ep_meta.get("podcast_title", ""),
        host=", ".join(ep_meta.get("hosts") or []) or "(unknown)",
        guest=", ".join(ep_meta.get("guests") or []) or "(unknown)",
        published_at=ep_meta.get("published_at", ""),
        duration_min=round((ep_meta.get("duration_seconds") or 0) / 60),
        chapters=fmt_chapters(sc),
        transcript=transcript_path.read_text(encoding="utf-8"),
    )

    if args.use_api:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("ERROR: --use-api requires ANTHROPIC_API_KEY in env.", file=sys.stderr)
            return 2
        model = args.model or DEFAULT_MODEL
        print(f"Calling Anthropic API: {model} (paid, {len(user_prompt):,} chars)", file=sys.stderr)
        raw = call_claude_api(api_key, model, SYSTEM_PROMPT, user_prompt, args.max_tokens)
    else:
        # Default: local Ollama. No cloud, no API costs. Auto-retries once
        # when the draft comes back with fewer than 6 takeaways or claims
        # (a stochastic regression we've seen with Gemma 4 e4b).
        model = args.model or DEFAULT_OLLAMA_MODEL
        print(f"Calling Ollama: {model} (local, ctx={args.num_ctx}, {len(user_prompt):,} chars)",
              file=sys.stderr)
        attempts = 0
        raw = ""
        while attempts < 2:
            attempts += 1
            try:
                raw = call_ollama(model, SYSTEM_PROMPT, user_prompt, args.num_ctx)
            except Exception as e:
                print(f"ERROR calling Ollama: {e}", file=sys.stderr)
                print("  is the daemon running? `ollama serve`", file=sys.stderr)
                print(f"  is the model pulled? `ollama pull {model}`", file=sys.stderr)
                return 2
            # Count both claims and takeaways before declaring the draft
            # "thick enough." The earlier check only counted `"topic"` keys
            # (claims) and considered presence of `"takeaways"` sufficient,
            # so a draft with 8 claims + 4 takeaways slipped through without
            # the retry — and downstream notes_lint then warned "only 4
            # takeaways (recommend 5+)" with no automatic recovery. Parse the
            # JSON to count both lists; fall back to the cheap heuristic if
            # the model output isn't parseable yet (retry will rerun anyway).
            t_count = raw.count('"topic"')
            try:
                parsed_check = extract_json(raw)
                n_takeaways = len(parsed_check.get("takeaways", []) or [])
                n_claims = len(parsed_check.get("claims", []) or [])
            except Exception:
                n_takeaways = 0
                n_claims = t_count
            if n_takeaways >= 6 and n_claims >= 6:
                break
            if attempts < 2:
                print(
                    f"  draft looked thin (takeaways={n_takeaways}, claims={n_claims}); "
                    "retrying once...",
                    file=sys.stderr,
                )
    try:
        draft = extract_json(raw)
    except Exception as e:
        debug_path = ep / "working" / "_notes_draft_raw.txt"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(raw, encoding="utf-8")
        print(f"ERROR: {e}", file=sys.stderr)
        print(f"  Raw model output saved to {debug_path}", file=sys.stderr)
        return 2

    out_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote draft to {out_path}")
    print("Review, edit, and rename to episode.notes.json when satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
