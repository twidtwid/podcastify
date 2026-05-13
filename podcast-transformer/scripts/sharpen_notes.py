#!/usr/bin/env python3
"""Second-pass rewriter that sharpens claim topics and takeaways — one item
at a time, with full per-item context, so each rewrite stays on subject.

The previous batch version of this script (single LLM call to rewrite all
8 topics + 8 takeaways at once) was unreliable: the model would freely
reorder or regenerate items, producing punchier topics that no longer
matched their grounded claim bodies. It also dropped the guest's signature
phrases from takeaways.

This version makes 8 small per-claim calls and 8 per-takeaway calls. Each
call gets that one item's full context (body + evidence for a claim,
or the full takeaway text). The model is constrained to stay on the same
subject and preserve any verbatim phrasing the guest used.

Per-item calls are slower than one batch (~45-60s total instead of ~15s),
but produce correct results. Each call is gated by a validation check: if
the new topic doesn't share at least one significant noun with the
original topic or body, we keep the original.

Usage:
  python3 scripts/sharpen_notes.py <episode_dir> [--model M]

A pre-sharpen snapshot is preserved at `source/episode.notes.before-sharpen.json`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from pipeline_config import (
    SHARPEN_MODEL as DEFAULT_MODEL,
    SHARPEN_NUM_CTX as DEFAULT_NUM_CTX,
    OLLAMA_URL,
)

# Stopwords for the "shared-noun" validation
STOP = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "for",
    "with", "without", "on", "at", "by", "is", "are", "was", "were", "be",
    "been", "being", "as", "it", "its", "this", "that", "these", "those",
    "you", "your", "yours", "we", "our", "they", "their", "them",
    "what", "which", "who", "whom", "when", "where", "why", "how",
    "must", "should", "can", "could", "would", "may", "might", "will",
    "not", "no", "so", "than", "then", "from", "into", "out", "up",
    "down", "more", "less", "very", "even", "just", "only", "also",
    "any", "every", "all", "some", "one", "two", "three", "many", "much",
    "do", "does", "did", "have", "has", "had",
    "company", "companies", "business", "businesses",  # extremely common
}


def significant_words(text: str) -> set[str]:
    """Lowercased non-stopword words of len >= 4, mainly nouns."""
    out: set[str] = set()
    for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", text):
        wl = w.lower()
        if len(wl) >= 4 and wl not in STOP:
            out.add(wl)
    return out


def call_ollama(model: str, system: str, user: str, num_ctx: int) -> str:
    body = json.dumps({
        "model": model,
        "stream": False,
        "format": "json",
        # Disable thinking-mode output for reasoning-capable models like
        # qwen3.6 — when `think` is True, the model fills the `thinking`
        # field and leaves `content` empty under format=json.
        "think": False,
        "options": {
            "temperature": 0.4,
            "num_ctx": num_ctx,
            "num_predict": 800,
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
    with urllib.request.urlopen(req, timeout=240) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("message", {}).get("content", "")


# ── topic sharpener ───────────────────────────────────────────────────

TOPIC_SYSTEM = """You sharpen ONE claim topic at a time for a podcast briefing.

Input: a claim's existing topic, its grounded body, and its evidence.
Task: rewrite ONLY the topic to be more provocative and immediately useful, while staying on the SAME subject as the original.

# Hard rules

- Same subject. If the body talks about Anthropic's Long-Term Benefit Trust, the new topic must STILL describe that, just sharper. Don't switch to a different theme from the episode.
- Use the guest's own provocative phrasings when they appear in the body (e.g., "would you sell to Philip Morris for $1 more a share?").
- Second person beats third person when it lands ("Your charter..." not "Charters...").
- 4-12 words. Sentence case.
- No AI tells: no "navigate", "robust", "key insight", "harness", "leverage", "unlocking".

# Output

Exactly this JSON object — no markdown fence, no commentary:

```json
{"topic": "your sharpened topic here"}
```
"""


def sharpen_topic(model: str, claim: dict, num_ctx: int) -> str | None:
    """Returns the sharpened topic, or None if validation fails."""
    user = (
        f"# Existing topic\n{claim.get('topic', '')}\n\n"
        f"# Grounded body (DO NOT CHANGE — for context only)\n{claim.get('claim', '')}\n\n"
        f"# Grounded evidence (DO NOT CHANGE — for context only)\n{claim.get('evidence', '')}\n\n"
        f"Output the JSON now."
    )
    raw = call_ollama(model, TOPIC_SYSTEM, user, num_ctx)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    new_topic = (payload.get("topic") or "").strip()
    if not new_topic:
        return None
    # Sanity-check length
    wc = len(new_topic.split())
    if wc < 4 or wc > 14:
        return None
    # Subject-preservation check: the new topic must share at least one
    # significant word with either the original topic or the body.
    original_words = significant_words(claim.get("topic", ""))
    body_words = significant_words((claim.get("claim", "") + " " + claim.get("evidence", ""))[:400])
    new_words = significant_words(new_topic)
    if not (new_words & original_words) and not (new_words & body_words):
        return None
    return new_topic


# ── takeaway sharpener ────────────────────────────────────────────────

TAKEAWAY_SYSTEM = """You sharpen ONE takeaway at a time for a podcast briefing.

Input: a takeaway as currently written.
Task: rewrite the WHOLE takeaway to be punchier and more provocative, while preserving the same subject and ANY verbatim phrases the guest used.

# Hard rules

- Same subject. If the original is about mission-hopeful vs mission-driven, the new one must be too.
- Preserve verbatim quotes the guest already nailed (e.g., "mission hopeful", "harder is easier", "golden goose", "to pursue any lawful act", "who would you rather die than betray").
- Lead with the assertion. Cut "Do not...", "Implement...", "Make sure to..." openings. Just state the position.
- 25-50 words. Two sentences max.
- No AI tells: no "navigate", "robust", "key insight", "harness", "leverage", "unlocking", "in the realm of".

# Output

Exactly this JSON object — no markdown fence, no commentary:

```json
{"takeaway": "your sharpened takeaway here"}
```
"""


def sharpen_takeaway(model: str, original: str, num_ctx: int) -> str | None:
    user = (
        f"# Existing takeaway\n{original}\n\n"
        f"Output the JSON now."
    )
    raw = call_ollama(model, TAKEAWAY_SYSTEM, user, num_ctx)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    new_t = (payload.get("takeaway") or "").strip()
    if not new_t:
        return None
    wc = len(new_t.split())
    if wc < 15 or wc > 65:
        return None
    # Subject-preservation: share at least one significant word with original
    orig_words = significant_words(original)
    new_words = significant_words(new_t)
    if not (new_words & orig_words):
        return None
    return new_t


# ── orchestration ─────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("episode_dir", type=Path)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    ap.add_argument("--skip-takeaways", action="store_true")
    ap.add_argument("--skip-claims", action="store_true")
    args = ap.parse_args(argv)

    ep = args.episode_dir.resolve()
    notes_path = ep / "source" / "episode.notes.json"
    if not notes_path.exists():
        print(f"ERROR: notes.json missing at {notes_path}", file=sys.stderr)
        return 2

    notes = json.loads(notes_path.read_text(encoding="utf-8"))
    backup = ep / "source" / "episode.notes.before-sharpen.json"
    backup.write_text(json.dumps(notes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    claim_changes = 0
    if not args.skip_claims:
        for i, claim in enumerate(notes.get("claims") or []):
            print(f"  sharpening claim {i+1}/{len(notes['claims'])}...", file=sys.stderr)
            new_topic = sharpen_topic(args.model, claim, args.num_ctx)
            if new_topic and new_topic.strip() != (claim.get("topic") or "").strip():
                print(f"    {claim.get('topic','')!r}\n    -> {new_topic!r}", file=sys.stderr)
                claim["topic"] = new_topic
                claim_changes += 1
            else:
                print(f"    (kept original; validation declined the rewrite)", file=sys.stderr)

    takeaway_changes = 0
    if not args.skip_takeaways:
        takeaways = notes.get("takeaways") or []
        for i, t in enumerate(takeaways):
            print(f"  sharpening takeaway {i+1}/{len(takeaways)}...", file=sys.stderr)
            new_t = sharpen_takeaway(args.model, t, args.num_ctx)
            if new_t and new_t.strip() != t.strip():
                print(f"    BEFORE: {t[:90]}…\n    AFTER:  {new_t[:90]}…", file=sys.stderr)
                takeaways[i] = new_t
                takeaway_changes += 1
            else:
                print(f"    (kept original)", file=sys.stderr)

    notes_path.write_text(json.dumps(notes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"sharpened {claim_changes} claims + {takeaway_changes} takeaways -> {notes_path}")
    print(f"backup at {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
