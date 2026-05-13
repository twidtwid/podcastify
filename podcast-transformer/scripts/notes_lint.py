#!/usr/bin/env python3
"""Lint episode.notes.json against the curated-content schema.

Schema reference: ../references/notes-schema.md
Design rules reference: ../references/design-principles.md

Catches the failure modes we keep regressing into:
- Claim topic that's a one-word category label, not a position-establishing headline.
- Claim body that paraphrases (or is byte-identical to) its topic.
- Takeaways that summarize instead of stating a useful position.
- `built_for` that references invisible UI ("the inspector", "the right column").
- Missing required fields or empty critical lists.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_KEYS = ("short_title", "description", "bottom_line", "takeaways", "claims")
MIN_TAKEAWAYS = 5
MAX_TAKEAWAYS = 10
MIN_CLAIMS = 3
MAX_CLAIMS = 8
MIN_TAKEAWAY_WORDS = 8
MIN_TOPIC_WORDS_HARD = 2  # 1-word topic is a category label, error
MIN_TOPIC_WORDS_SOFT = 4  # 2-3 word topics get a warning unless they contain a verb
MAX_TOPIC_WORDS = 12
COMMON_VERBS = {
    "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had",
    "can", "could", "may", "might", "must", "should", "will", "would",
    "buy", "sell", "stay", "kill", "burn", "bury", "outlast", "outlasts",
    "compound", "compounds", "ride", "rode", "scale", "scales",
    "merge", "merges", "exit", "exits", "win", "wins", "fail", "fails",
    "matter", "matters", "shift", "shifts", "move", "moves",
    "concentrate", "concentrates",
}
MIN_CLAIM_WORDS = 15
INVISIBLE_UI_PHRASES = (
    "the inspector",
    "the right column",
    "the left column",
    "the side rail",
    "the sidebar",
    "this dashboard",
)
LAZY_CLAIM_PREFIXES = (
    "this episode is about",
    "the discussion moves",
    "they discuss",
    "they examine",
    "they explore",
)


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def is_paraphrase(topic: str, claim: str) -> bool:
    """Return True if the claim body just restates the topic."""
    t = normalize(topic)
    c = normalize(claim)
    if not t or not c:
        return False
    if t == c:
        return True
    if t in c and len(c) < len(t) * 2:
        return True
    t_words = set(t.split())
    c_words = set(c.split()[: len(t.split()) + 2])
    if t_words and len(t_words & c_words) / len(t_words) > 0.8:
        return True
    return False


def lint_notes(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return ["notes.json root must be an object"], warnings

    for key in REQUIRED_KEYS:
        if key not in data:
            errors.append(f"missing required field: {key}")

    short_title = (data.get("short_title") or "").strip()
    if short_title and len(short_title) > 80:
        warnings.append(f"short_title is long ({len(short_title)} chars); consider trimming for the hero")

    description = (data.get("description") or "").strip()
    if description and word_count(description) < 8:
        warnings.append("description is very short; consider adding context for the hero subtitle")

    bottom_line = (data.get("bottom_line") or "").strip()
    if bottom_line and word_count(bottom_line) < 12:
        warnings.append("bottom_line is short; the thesis card needs enough substance to read as a real argument")

    built_for = (data.get("built_for") or "").strip().lower()
    for phrase in INVISIBLE_UI_PHRASES:
        if phrase in built_for:
            errors.append(
                f"built_for references invisible UI ({phrase!r}); state the audience and the job, not the page layout"
            )

    takeaways = data.get("takeaways") or []
    if not isinstance(takeaways, list):
        errors.append("takeaways must be a list")
        takeaways = []
    if len(takeaways) < MIN_TAKEAWAYS:
        warnings.append(f"only {len(takeaways)} takeaways (recommend {MIN_TAKEAWAYS}+)")
    if len(takeaways) > MAX_TAKEAWAYS:
        warnings.append(f"{len(takeaways)} takeaways may be too many for the briefing (recommend up to {MAX_TAKEAWAYS})")
    for i, t in enumerate(takeaways):
        if not isinstance(t, str) or not t.strip():
            errors.append(f"takeaways[{i}] must be a non-empty string")
            continue
        if word_count(t) < MIN_TAKEAWAY_WORDS:
            warnings.append(f"takeaways[{i}] is short ({word_count(t)} words); a takeaway should stand alone as a sentence a reader could repeat")
        t_low = t.lower().strip()
        if any(t_low.startswith(prefix) for prefix in LAZY_CLAIM_PREFIXES):
            warnings.append(f"takeaways[{i}] reads like an episode-summary phrasing; takeaways should be reader-useful claims")

    claims = data.get("claims") or []
    if not isinstance(claims, list):
        errors.append("claims must be a list")
        claims = []
    if len(claims) < MIN_CLAIMS:
        warnings.append(f"only {len(claims)} claims (recommend {MIN_CLAIMS}+)")
    if len(claims) > MAX_CLAIMS:
        warnings.append(f"{len(claims)} claims is a lot; consider trimming to the most distinctive {MAX_CLAIMS}")
    for i, c in enumerate(claims):
        if not isinstance(c, dict):
            errors.append(f"claims[{i}] must be an object")
            continue
        topic = (c.get("topic") or "").strip()
        body = (c.get("claim") or "").strip()
        evidence = (c.get("evidence") or "").strip()
        if not topic:
            errors.append(f"claims[{i}].topic is required")
        else:
            n_topic = word_count(topic)
            topic_words = {w.lower().strip(".,;:!?") for w in topic.split()}
            has_verb = bool(topic_words & COMMON_VERBS)
            if n_topic <= MIN_TOPIC_WORDS_HARD:
                errors.append(
                    f"claims[{i}].topic is too short ({n_topic} words) and reads as a category label; rewrite as a position-establishing headline (see design-principles.md)"
                )
            elif n_topic < MIN_TOPIC_WORDS_SOFT and not has_verb:
                warnings.append(
                    f"claims[{i}].topic is short ({n_topic} words) with no verb; consider rewriting as a position-establishing headline"
                )
            elif n_topic > MAX_TOPIC_WORDS:
                warnings.append(f"claims[{i}].topic is long ({n_topic} words); aim for 4-{MAX_TOPIC_WORDS}")
        if not body:
            errors.append(f"claims[{i}].claim is required")
        elif word_count(body) < MIN_CLAIM_WORDS:
            warnings.append(
                f"claims[{i}].claim is short ({word_count(body)} words); the body should add mechanism, evidence, or consequence beyond the topic"
            )
        if topic and body and is_paraphrase(topic, body):
            errors.append(
                f"claims[{i}].claim paraphrases the topic; the body must add the why/how/so-what, not restate the headline (see design-principles.md)"
            )
        if not evidence:
            warnings.append(f"claims[{i}].evidence is empty; name the specific moment, chapter, or example from the episode")
        elif evidence.lower() in {"episode transcript", "transcript", "show notes", "apple chapter metadata", "podcast"}:
            warnings.append(
                f"claims[{i}].evidence is generic ({evidence!r}); evidence should name the moment or quote, not the source type"
            )

    links = data.get("links") or []
    if isinstance(links, list):
        for i, l in enumerate(links):
            if not isinstance(l, dict):
                errors.append(f"links[{i}] must be an object")
                continue
            if not l.get("url"):
                errors.append(f"links[{i}].url is required")
            elif not re.match(r"^https?://", l["url"]):
                warnings.append(f"links[{i}].url does not look like a full URL: {l['url']!r}")

    chapter_queries = data.get("chapter_queries") or []
    if chapter_queries and not isinstance(chapter_queries, list):
        errors.append("chapter_queries must be a list")
    elif isinstance(chapter_queries, list):
        seen: dict[str, int] = {}
        for i, q in enumerate(chapter_queries):
            if not isinstance(q, str):
                errors.append(f"chapter_queries[{i}] must be a string")
                continue
            if q and q.lower() in seen:
                warnings.append(
                    f"chapter_queries[{i}] duplicates queries[{seen[q.lower()]}]; chapters should anchor to distinct turns"
                )
            elif q:
                seen[q.lower()] = i

    return errors, warnings


def print_report(path: Path, errors: list[str], warnings: list[str], strict: bool) -> int:
    print(f"Notes lint: {path}")
    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARN: {message}")
    if not errors and not warnings:
        print("No notes-content issues found")
    if errors or (warnings and strict):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notes")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    path = Path(args.notes)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    errors, warnings = lint_notes(data)
    return print_report(path, errors, warnings, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
