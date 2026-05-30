#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = SKILL_ROOT / "assets" / "podcast-html"

# Public base URL where the rendered library is served. Used to build
# absolute og:url / og:image so Slack/Discord/X can unfurl a rich card.
# Override with PODCAST_PUBLIC_BASE_URL; the localhost default works with
# `python -m http.server` but a real social unfurl needs a publicly
# reachable URL (e.g. a tunnel or static host).
DEFAULT_PUBLIC_BASE = "http://localhost:8000"


def public_base() -> str:
    return os.environ.get("PODCAST_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE).rstrip("/")


def _meta(prop: str, content: str, *, name: bool = False) -> str:
    attr = "name" if name else "property"
    return f'<meta {attr}="{html.escape(prop, quote=True)}" content="{html.escape(content, quote=True)}">'


def build_head_meta(package: dict[str, Any], slug: str, filename: str) -> str:
    """Open Graph + Twitter card tags for a rich social unfurl."""
    ep = package.get("episode", {})
    title = (ep.get("short_title") or ep.get("title") or "Podcast briefing").strip()
    desc = re.sub(r"\s+", " ", (ep.get("description") or "").strip())
    if len(desc) > 300:
        desc = desc[:299].rstrip() + "…"
    guests = ", ".join(ep.get("guests") or [])
    if guests and guests.lower() not in desc.lower():
        desc = f"{guests} — {desc}" if desc else f"Conversation with {guests}."
    site = (ep.get("podcast_title") or "Podcast library").strip()
    base = public_base()
    page_url = f"{base}/{slug}/final/{filename}"
    image_url = f"{base}/{slug}/final/og-card.png"
    tags = [
        _meta("og:type", "article"),
        _meta("og:site_name", site),
        _meta("og:title", title),
        _meta("og:description", desc),
        _meta("og:url", page_url),
        _meta("og:image", image_url),
        _meta("og:image:width", "1200"),
        _meta("og:image:height", "630"),
        _meta("twitter:card", "summary_large_image", name=True),
        _meta("twitter:title", title, name=True),
        _meta("twitter:description", desc, name=True),
        _meta("twitter:image", image_url, name=True),
        _meta("description", desc, name=True),
    ]
    return "\n".join(tags)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same dir then atomically rename, so a crash
    # or kill mid-write can never leave a truncated JSON file that the next
    # run's read_json would choke on (the pipeline runs parallel/killable).
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def esc_json_for_html(value: dict[str, Any]) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def seconds_to_timestamp(seconds: int | float | None) -> str:
    seconds = int(seconds or 0)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def resolve_sidecar_path(episode_dir: Path) -> Path:
    sidecar = episode_dir / "final" / "metadata.sidecar.json"
    if not sidecar.exists():
        raise FileNotFoundError(f"Missing sidecar: {sidecar}")
    return sidecar


def resolve_transcript_path(episode_dir: Path, sidecar: dict[str, Any]) -> Path:
    raw = sidecar.get("transcription", {}).get("raw_transcript_path") or ""
    candidates: list[Path] = []
    if raw:
        raw_path = Path(raw)
        if raw_path.is_absolute():
            candidates.append(raw_path)
        candidates.append((episode_dir / "final" / raw_path).resolve())
        candidates.append((episode_dir / raw_path).resolve())
    candidates.extend([
        episode_dir / "source" / "user-provided-transcript.txt",
        episode_dir / "final" / "transcript.verified.md",
    ])
    derived_output = (episode_dir / "final" / "transcript.verified.md").resolve()
    for candidate in candidates:
        if candidate.exists():
            if candidate.resolve() == derived_output:
                print(
                    f"WARN: transcript input fell back to the build's own prior output "
                    f"{candidate} — raw transcript not found; the rebuilt package will be "
                    f"a lossy second generation (structured turns lost). Restore the raw "
                    f"transcript or re-run from source.",
                    file=sys.stderr,
                )
            return candidate
    raise FileNotFoundError("Could not find transcript input from sidecar or standard episode paths")


def parse_speaker_turns(text: str) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    speaker = ""
    chunks: list[str] = []
    speaker_re = re.compile(r"^(?:\*\*)?(Tim Ferriss|Elad Gil|[^:\n]{2,60})(?:\*\*)?:\s*(.*)$")
    timestamp_re = re.compile(r"^\[?\d{2}:\d{2}:\d{2}\]?\s*$")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or timestamp_re.match(line) or line.startswith("#"):
            continue
        match = speaker_re.match(line)
        if match:
            if speaker and chunks:
                turns.append({"speaker": speaker, "text": normalize_space(" ".join(chunks))})
            speaker = match.group(1).strip("* ")
            chunks = [match.group(2).strip()]
        elif speaker:
            chunks.append(line)
    if speaker and chunks:
        turns.append({"speaker": speaker, "text": normalize_space(" ".join(chunks))})
    for i, turn in enumerate(turns):
        turn["id"] = f"t{i}"
        turn["word_count"] = word_count(turn["text"])
    return turns


def resolve_structured_turns_path(episode_dir: Path, transcript_path: Path) -> Path | None:
    candidates = [
        transcript_path.with_suffix(".turns.json"),
        transcript_path.parent / "transcript.turns.json",
        episode_dir / "source" / "transcript.turns.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def read_structured_turns(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARN: ignoring invalid structured turns {path}: {exc}", file=sys.stderr)
        return []
    if isinstance(raw, dict):
        raw = raw.get("turns") or []
    if not isinstance(raw, list):
        return []
    turns: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        speaker = normalize_space(str(item.get("speaker") or ""))
        text = normalize_space(str(item.get("text") or ""))
        if not speaker or not text:
            continue
        turn = dict(item)
        turn["speaker"] = speaker
        turn["text"] = text
        turns.append(turn)
    for i, turn in enumerate(turns):
        turn["id"] = f"t{i}"
        turn["word_count"] = int(turn.get("word_count") or word_count(turn["text"]))
    return turns


def annotate_speaker_visibility(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark speaker labels for display only when the speaker changes."""
    previous = None
    for turn in turns:
        speaker = turn.get("speaker") or ""
        turn["show_speaker"] = speaker != previous
        previous = speaker
    return turns


def find_turn(turns: list[dict[str, Any]], query: str, start: int = 0, allow_wrap: bool = True) -> int | None:
    if not query:
        return None
    needle = query.lower()
    haystacks = [f"{turn['speaker']} {turn['text']}".lower() for turn in turns]
    for i in range(max(0, start), len(haystacks)):
        if needle in haystacks[i]:
            return i
    if allow_wrap:
        for i, haystack in enumerate(haystacks):
            if needle in haystack:
                return i
    words = [word for word in re.findall(r"[a-zA-Z0-9']+", needle) if len(word) > 3]
    if not words:
        return None
    best: tuple[int, int] | None = None
    fuzzy_range = range(0, len(haystacks)) if allow_wrap else range(max(0, start), len(haystacks))
    for i in fuzzy_range:
        haystack = haystacks[i]
        score = sum(1 for word in words if word in haystack)
        if score and (best is None or score > best[0]):
            best = (score, i)
    return best[1] if best and best[0] >= max(2, min(4, len(words))) else None


def estimate_turn_for_time(turns: list[dict[str, Any]], start: int, duration: int) -> int:
    if not turns:
        return 0
    if duration <= 0:
        return 0
    total_words = sum(turn["word_count"] for turn in turns) or 1
    target_words = total_words * min(max(start / duration, 0), 1)
    running = 0
    for i, turn in enumerate(turns):
        running += turn["word_count"]
        if running >= target_words:
            return i
    return len(turns) - 1


def build_chapters(
    sidecar: dict[str, Any],
    notes: dict[str, Any],
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_chapters = sidecar.get("episode", {}).get("chapters", [])
    queries = notes.get("chapter_queries") or []
    duration = int(sidecar.get("episode", {}).get("duration_seconds") or 0)
    chapters: list[dict[str, Any]] = []
    cursor = 0
    for i, chapter in enumerate(raw_chapters):
        start = int(chapter.get("start") or 0)
        query = queries[i] if i < len(queries) else ""
        turn_index = find_turn(turns, query, cursor, allow_wrap=False)
        if turn_index is None:
            turn_index = estimate_turn_for_time(turns, start, duration)
        # Enforce strict monotonic anchoring: each chapter must land on a turn
        # AFTER the previous chapter's turn, so no two chapters share an anchor.
        turn_index = min(max(turn_index, cursor), len(turns) - 1)
        cursor = turn_index + 1
        chapters.append({
            "id": f"c{i}",
            "anchor_id": f"chapter-{i}",
            "turn_id": turns[turn_index]["id"] if turns else "t0",
            "turn_index": turn_index,
            "start": start,
            "timestamp": chapter.get("timestamp") or seconds_to_timestamp(start),
            "title": chapter.get("title") or f"Chapter {i + 1}",
            "summary": chapter.get("summary") or "",
            "tags": chapter.get("tags") or [],
            "query": query,
        })
    return chapters


def build_keywords(
    notes: dict[str, Any],
    chapters: list[dict[str, Any]],
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    keywords: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label, query in (notes.get("keyword_queries") or {}).items():
        turn_index = find_turn(turns, query)
        if turn_index is not None:
            keywords.append({"label": label, "target_id": turns[turn_index]["id"], "query": query})
            seen.add(label.lower())
    for chapter in chapters:
        for tag in chapter.get("tags", []):
            if tag.lower() not in seen:
                keywords.append({"label": tag, "target_id": chapter["anchor_id"], "query": tag})
                seen.add(tag.lower())
    return keywords


def source_links(sidecar: dict[str, Any], notes: dict[str, Any]) -> list[dict[str, str]]:
    links = notes.get("links") or []
    if links:
        return links
    sources = sidecar.get("verification", {}).get("sources", [])
    return [
        {
            "label": source.get("title") or source.get("url") or "Source",
            "url": source.get("url") or "",
            "kind": "source",
            "note": ", ".join(source.get("used_for") or []),
        }
        for source in sources if source.get("url")
    ]


def build_package(episode_dir: Path) -> dict[str, Any]:
    sidecar_path = resolve_sidecar_path(episode_dir)
    sidecar = read_json(sidecar_path)
    notes_path = episode_dir / "source" / "episode.notes.json"
    notes = read_json(notes_path) if notes_path.exists() else {}
    transcript_path = resolve_transcript_path(episode_dir, sidecar)
    structured_turns_path = resolve_structured_turns_path(episode_dir, transcript_path)
    turns = read_structured_turns(structured_turns_path) if structured_turns_path else []
    if not turns:
        turns = parse_speaker_turns(transcript_path.read_text(encoding="utf-8", errors="replace"))
    turns = annotate_speaker_visibility(turns)
    chapters = build_chapters(sidecar, notes, turns)
    keywords = build_keywords(notes, chapters, turns)
    terms = sidecar.get("verification", {}).get("terminology", [])
    speakers: dict[str, int] = {}
    for turn in turns:
        speakers[turn["speaker"]] = speakers.get(turn["speaker"], 0) + turn["word_count"]
    episode = sidecar.get("episode", {})
    package = {
        "schema_version": "podcast-transformer/package-v1",
        "generated_at": utc_now(),
        "episode_dir": str(episode_dir.resolve()),
        "episode": {
            "title": episode.get("title") or "",
            "short_title": notes.get("short_title") or episode.get("title") or "",
            "podcast_title": episode.get("podcast_title") or "",
            "episode_number": episode.get("episode_number") or "",
            "episode_url": episode.get("episode_url") or "",
            "description": notes.get("description") or episode.get("description") or "",
            "published_at": episode.get("published_at") or "",
            "duration_seconds": episode.get("duration_seconds") or 0,
            "hosts": episode.get("hosts") or [],
            "guests": episode.get("guests") or [],
            "language": episode.get("language") or "en",
        },
        "bottom_line": notes.get("bottom_line") or episode.get("description") or "",
        "built_for": notes.get("built_for") or "",
        "takeaways": notes.get("takeaways") or [],
        "claims": notes.get("claims") or [],
        "links": source_links(sidecar, notes),
        "terms": terms,
        "chapters": chapters,
        "keywords": keywords,
        "turns": turns,
        "stats": {
            "word_count": sum(turn["word_count"] for turn in turns),
            "turn_count": len(turns),
            "chapter_count": len(chapters),
            "term_count": len(terms),
            "speaker_word_counts": speakers,
        },
        "provenance": {
            "sidecar_path": str(sidecar_path.relative_to(episode_dir)),
            "transcript_path": str(transcript_path.relative_to(episode_dir)) if transcript_path.is_relative_to(episode_dir) else str(transcript_path),
            "notes_path": str(notes_path.relative_to(episode_dir)) if notes_path.exists() else "",
        },
        "outputs": {
            "verified_transcript_md": "transcript.verified.md",
            "metadata_sidecar": "metadata.sidecar.json",
            "annotated_transcript_html": "annotated-transcript.html",
            "summary_html": "podcast-at-a-glance.html",
            "package_json": "episode.package.json",
        },
    }
    return package


def write_verified_transcript(package: dict[str, Any], final_dir: Path) -> None:
    chapter_by_turn: dict[str, list[dict[str, Any]]] = {}
    for chapter in package["chapters"]:
        chapter_by_turn.setdefault(chapter["turn_id"], []).append(chapter)
    lines = [
        f"# {package['episode']['short_title'] or package['episode']['title']}",
        "",
        f"Podcast - {package['episode']['podcast_title']} #{package['episode']['episode_number']}",
        "Generated by podcast-transformer.",
        "",
    ]
    for turn in package["turns"]:
        for chapter in chapter_by_turn.get(turn["id"], []):
            lines.extend([
                f"## [{chapter['timestamp']}] {chapter['title']}",
                "",
                chapter["summary"],
                "",
            ])
        wrapped = textwrap.wrap(turn["text"], width=104, break_long_words=False, break_on_hyphens=False)
        if wrapped:
            lines.append(f"{turn['speaker']}: {wrapped[0]}")
            lines.extend(wrapped[1:])
        else:
            lines.append(f"{turn['speaker']}:")
        lines.append("")
    (final_dir / "transcript.verified.md").write_text("\n".join(lines), encoding="utf-8")


UNCLEAR_RE = re.compile(
    r"\[[^\]]*\b(?:inaudible|unintelligible|unclear)\b[^\]]*\]|\b(?:inaudible|unintelligible)\b|\[\s*\?\s*\]|\?\?\?",
    re.IGNORECASE,
)


def sync_uncertain_spans(episode_dir: Path, package: dict[str, Any]) -> None:
    sidecar_path = episode_dir / "final" / "metadata.sidecar.json"
    if not sidecar_path.exists():
        return
    sidecar = read_json(sidecar_path)
    verification = sidecar.setdefault("verification", {})
    existing = verification.get("uncertain_spans")
    if not isinstance(existing, list):
        existing = []
    existing_keys = {
        (
            normalize_space(str(span.get("speaker") or "")),
            normalize_space(str(span.get("text") or "")),
        )
        for span in existing
        if isinstance(span, dict)
    }
    spans: list[dict[str, str]] = []
    for turn in package.get("turns", []):
        text = str(turn.get("text") or "")
        for match in UNCLEAR_RE.finditer(text):
            speaker = str(turn.get("speaker") or "")
            span_text = match.group(0)
            key = (normalize_space(speaker), normalize_space(span_text))
            if key in existing_keys:
                continue
            existing_keys.add(key)
            spans.append({
                "timestamp": "",
                "speaker": speaker,
                "text": span_text,
                "reason": "Transcript contains unclear or inaudible marker.",
                "resolution_needed": "Review the publisher transcript or source audio if the span is material.",
            })
    if spans:
        verification["uncertain_spans"] = existing + spans
        write_json(sidecar_path, sidecar)


def render_template(
    template_name: str, package: dict[str, Any], title: str, head_meta: str = ""
) -> str:
    template = (ASSET_DIR / template_name).read_text(encoding="utf-8")
    css = (ASSET_DIR / "artifact.css").read_text(encoding="utf-8")
    app_js = (ASSET_DIR / "artifact.js").read_text(encoding="utf-8")
    # title lands in HTML text/attribute context and is derived from scraped
    # episode metadata, so it MUST be escaped. head_meta is already-escaped
    # markup from build_head_meta(); css/app_js/data are trusted assets.
    substitutions = {
        "TITLE": html.escape(title),
        "HEAD_META": head_meta,
        "CSS": css,
        "APP_JS": app_js,
        "DATA_JSON": esc_json_for_html(package),
    }
    # Single pass over the ORIGINAL template: substituted content is never
    # re-scanned, so a field containing a literal "{{DATA_JSON}}" (etc.)
    # cannot trigger a second-pass expansion / content injection.
    return re.sub(
        r"\{\{(TITLE|HEAD_META|CSS|APP_JS|DATA_JSON)\}\}",
        lambda m: substitutions[m.group(1)],
        template,
    )


def slim_for_glance(package: dict[str, Any]) -> dict[str, Any]:
    """Strip transcript-only fields from the glance payload (drops ~100KB on a typical episode)."""
    slim = dict(package)
    slim.pop("turns", None)
    slim.pop("keywords", None)
    return slim


def render_artifacts(episode_dir: Path, package: dict[str, Any], only: str = "all") -> None:
    final_dir = episode_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    write_json(final_dir / "episode.package.json", package)
    write_verified_transcript(package, final_dir)
    slug = episode_dir.name
    try:
        from og_card import generate_og_card
        generate_og_card(package, final_dir / "og-card.png", public_base())
    except Exception as exc:  # card is best-effort; never fail the build on it
        print(f"WARN: og-card render skipped ({exc})", file=sys.stderr)
    if only in {"all", "transcript"}:
        markup = render_template(
            "transcript-browser.html.tmpl",
            package,
            f"{package['episode']['short_title']} - Transcript",
            build_head_meta(package, slug, "annotated-transcript.html"),
        )
        (final_dir / "annotated-transcript.html").write_text(markup, encoding="utf-8")
    if only in {"all", "glance"}:
        markup = render_template(
            "glance-dashboard.html.tmpl",
            slim_for_glance(package),
            f"{package['episode']['short_title']} - Briefing",
            build_head_meta(package, slug, "podcast-at-a-glance.html"),
        )
        (final_dir / "podcast-at-a-glance.html").write_text(markup, encoding="utf-8")


# Patterns that indicate AI-slop regression in the rendered HTML.
# Each is a string that should never appear in a clean briefing or transcript.
# See references/design-principles.md for the rationale behind each removal.
BANNED_RENDER_PATTERNS = (
    # Old transcript-browser patterns that were deliberately removed.
    "Copy turn",
    "audio navigation",
    "overflow: auto",
    "max-height: 360px",
    # Briefing slop patterns we explicitly removed.
    "TOPIC LENSES",
    "Topic Lenses",
    "Conversation arc",
    "CONVERSATION ARC",
    "Who's talking",
    "WHO'S TALKING",
    "AT A GLANCE",
    "Speaker share",
    "Built for",  # the BUILT FOR card was removed; field stays in notes.json but should not render
)

EXTERNAL_RESOURCE_RES = (
    re.compile(r"<script[^>]+src=['\"]https?:", re.I),
    re.compile(r"<link[^>]+href=['\"]https?:", re.I),
    re.compile(r"<img[^>]+src=['\"]https?:", re.I),
    re.compile(r"<source[^>]+src=['\"]https?:", re.I),
    re.compile(r"@import\s+['\"]?https?:", re.I),
    re.compile(r"url\(\s*['\"]?https?:", re.I),
    re.compile(r"fetch\(\s*['\"]https?:", re.I),
)

REQUIRED_HTML_PRIMITIVES = {
    "podcast-at-a-glance.html": (
        "PodcastArtifacts.renderGlanceDashboard",
        "folder-tabs",
        "inspector-toggle",
        "takeaway-tile",
        "claim-card",
        "entity-list",
    ),
    "annotated-transcript.html": (
        "PodcastArtifacts.renderTranscriptBrowser",
        "folder-tabs",
        'type="search"',
        "chapter-nav-item",
        "data-copy-anchor",
        'document.createElement("mark")',
    ),
}


def validate_artifacts(episode_dir: Path) -> int:
    final_dir = episode_dir / "final"
    failures: list[str] = []  # hard errors that break the build
    warnings: list[str] = []  # degraded output; build succeeds but flags issue
    package_path = final_dir / "episode.package.json"
    if not package_path.exists():
        failures.append(f"missing {package_path}")
    else:
        package = read_json(package_path)
        chapters = package.get("chapters", [])
        anchors = [c["anchor_id"] for c in chapters]
        if len(anchors) != len(set(anchors)):
            failures.append("chapter anchor ids are not unique")
        turn_ids = {turn["id"] for turn in package.get("turns", [])}
        missing = [c["turn_id"] for c in chapters if c["turn_id"] not in turn_ids]
        if missing:
            failures.append(f"chapter turn targets missing: {missing[:5]}")
        # Each chapter should anchor to a DISTINCT turn. Duplicates render as
        # stacked headers with no content under all but the last — a content
        # issue, not a build failure. Surface as a warning so the user can fix
        # chapter_queries in notes.json.
        turn_assignments = [c["turn_id"] for c in chapters]
        seen: dict[str, int] = {}
        for i, tid in enumerate(turn_assignments):
            if tid in seen:
                warnings.append(
                    f"chapter[{i}] ({chapters[i].get('title','?')!r}) shares anchor turn {tid} "
                    f"with chapter[{seen[tid]}] ({chapters[seen[tid]].get('title','?')!r}); "
                    "make chapter_queries more distinctive."
                )
            else:
                seen[tid] = i
    for name in ["annotated-transcript.html", "podcast-at-a-glance.html", "transcript.verified.md", "metadata.sidecar.json"]:
        if not (final_dir / name).exists():
            failures.append(f"missing final/{name}")
    for html_name in ("annotated-transcript.html", "podcast-at-a-glance.html"):
        html_path = final_dir / html_name
        if not html_path.exists():
            continue
        text = html_path.read_text(encoding="utf-8")
        # Skip the embedded <script type="application/json"> payload so that
        # banned strings appearing in the data don't trip the visual check.
        visible = _strip_embedded_payload(text)
        if not re.search(r'<link\b[^>]*\brel=["\']icon["\'][^>]*\bhref=["\']data:,["\']', text, re.I):
            failures.append(f"{html_name} missing blank data favicon")
        if not re.search(r'<script\s+type=["\']application/json["\']\s+id=["\']episode-data["\']>', text, re.I):
            failures.append(f"{html_name} missing embedded episode-data JSON island")
        for rx in EXTERNAL_RESOURCE_RES:
            match = rx.search(text)
            if match:
                failures.append(f"{html_name} contains external resource reference: {match.group(0)[:80]}")
                break
        if re.search(r'\brole=["\']tab(?:list)?["\']|\baria-selected=', text, re.I):
            failures.append(f"{html_name} uses ARIA tab roles for folder navigation")
        if 'aria-current="page"' not in text:
            failures.append(f"{html_name} folder tabs missing aria-current on active page")
        for primitive in REQUIRED_HTML_PRIMITIVES.get(html_name, ()):
            if primitive not in text:
                failures.append(f"{html_name} missing required primitive marker: {primitive}")
        for banned in BANNED_RENDER_PATTERNS:
            if banned in visible:
                failures.append(f"banned slop pattern found in {html_name}: {banned!r}")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    for warning in warnings:
        print(f"WARN: {warning}", file=sys.stderr)
    if failures:
        return 1
    print("Podcast package artifacts are valid")
    return 0


def _strip_embedded_payload(html: str) -> str:
    """Remove the <script type="application/json" id="episode-data">...</script> block so banned-text checks ignore data."""
    return re.sub(
        r'<script\s+type="application/json"\s+id="episode-data">.*?</script>',
        "",
        html,
        flags=re.DOTALL,
    )


def run_existing_validators(episode_dir: Path) -> int:
    sidecar_script = SKILL_ROOT / "scripts" / "sidecar.py"
    transcript_lint = SKILL_ROOT / "scripts" / "transcript_lint.py"
    notes_lint = SKILL_ROOT / "scripts" / "notes_lint.py"
    final_dir = episode_dir / "final"
    notes_path = episode_dir / "source" / "episode.notes.json"
    commands = [
        [sys.executable, str(sidecar_script), "validate", str(final_dir / "metadata.sidecar.json"), "--strict"],
        [
            sys.executable, str(transcript_lint), str(final_dir / "transcript.verified.md"),
            "--sidecar", str(final_dir / "metadata.sidecar.json"),
        ],
    ]
    if notes_path.exists():
        commands.append([sys.executable, str(notes_lint), str(notes_path)])
    result = 0
    for command in commands:
        completed = subprocess.run(command, check=False)
        result = result or completed.returncode
    return result


def regenerate_library_index(episode_dir: Path) -> None:
    """Rebuild the library index at podcast-output/index.html so the parent directory has a real landing page."""
    library_root = episode_dir.parent
    index_script = SKILL_ROOT / "scripts" / "build_index.py"
    if not index_script.exists() or not library_root.is_dir():
        return
    subprocess.run([sys.executable, str(index_script), str(library_root)], check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build podcast transcript packages and HTML artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    # `export-json` writes only `episode.package.json` and prints its path
    # — the JSON-only handoff for an external renderer. HTML artifacts come
    # from `render` / `all`. A previous revision shipped both `build` and
    # `export-json` as aliases for the same code path; one canonical name
    # is plenty.
    for name in ["export-json", "render", "all", "validate"]:
        p = sub.add_parser(name)
        p.add_argument("episode_dir", type=Path)
    sub.choices["render"].add_argument("--only", choices=["all", "transcript", "glance"], default="all")
    args = parser.parse_args(argv)
    episode_dir = args.episode_dir.resolve()
    if args.command == "export-json":
        package = build_package(episode_dir)
        write_json(episode_dir / "final" / "episode.package.json", package)
        print(episode_dir / "final" / "episode.package.json")
        return 0
    if args.command == "render":
        package_path = episode_dir / "final" / "episode.package.json"
        package = read_json(package_path) if package_path.exists() else build_package(episode_dir)
        render_artifacts(episode_dir, package, args.only)
        regenerate_library_index(episode_dir)
        return 0
    if args.command == "all":
        package = build_package(episode_dir)
        sync_uncertain_spans(episode_dir, package)
        render_artifacts(episode_dir, package, "all")
        regenerate_library_index(episode_dir)
        return validate_artifacts(episode_dir) or run_existing_validators(episode_dir)
    if args.command == "validate":
        return validate_artifacts(episode_dir) or run_existing_validators(episode_dir)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
