#!/usr/bin/env python3
"""Deterministic optimization harness for podcastify.

The full `/podcastextract` run is dominated by local LLM calls, so CI cannot
depend on wall-clocking a real Ollama run. This harness tracks the parts we can
measure repeatably:

1. Build a complex Lenny-sized episode package from public metadata and a
   synthetic transcript shaped like a 99-minute interview.
2. Verify URL ingest/parse metadata is rich enough to skip the
   resolve_speakers LLM preflight for that episode shape.
3. Time deterministic package build + HTML render + artifact validation.
4. Audit the generated HTML source-document contract and required primitives.

The synthetic transcript is not copied from the paid/public episode transcript;
it is generated text using public episode metadata and chapter topics.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import extract_one
import parse_source
import podcast_build
import url_ingest

LENNY_URL = "https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands"
LENNY_TITLE = "How to build a company that withstands any era"
LENNY_PODCAST = "Lenny's Podcast: Product | Career | Growth"
LENNY_GUEST = "Eric Ries"
LENNY_HOST = "Lenny Rachitsky"
LENNY_DURATION_SECONDS = 99 * 60 + 22
LENNY_PUBLISHED_AT = "2026-05-10"
RENDER_CEILING_SECONDS = 3.0

LENNY_CHAPTERS = [
    (0, "Introduction to Eric Ries"),
    (146, "Introducing Incorruptible"),
    (386, "Protecting what you have built"),
    (695, "Why founders get ousted"),
    (898, "Too early, too late"),
    (1249, "Ethos plus integrity"),
    (1601, "Novo Nordisk's governance fortress"),
    (1996, "The harder is easier principle"),
    (2563, "Cloudflare's mission emergence story"),
    (3407, "Shareholder primacy as natural law"),
    (3968, "Anthropic and AI governance"),
    (4913, "Three things founders can do"),
    (5851, "Book resources and farewell"),
]

TERMS = [
    ("Eric Ries", "person", "Author and entrepreneur explaining governance structures that keep companies aligned with their purpose."),
    ("Lenny Rachitsky", "person", "Host of Lenny's Podcast, framing the founder and product-operator implications."),
    ("The Lean Startup", "book", "Ries's earlier operating system for startup learning loops and product iteration."),
    ("Incorruptible", "book", "Ries's governance project about protecting companies from mission drift and extraction."),
    ("financial gravity", "concept", "The pull that drags successful companies toward short-term extraction once incentives change."),
    ("shareholder primacy", "concept", "The governance norm that treats shareholder return as the dominant board obligation."),
    ("public benefit corporation", "concept", "A corporate form that lets directors consider mission and stakeholders alongside shareholder return."),
    ("Cloudflare", "company", "Infrastructure company used as an example of mission emerging through repeated hard choices."),
    ("Anthropic", "company", "AI company discussed as an example of mission-protective governance in a fast-growing market."),
    ("Novo Nordisk", "company", "Company referenced for long-term industrial-foundation governance."),
    ("Costco", "company", "Retailer invoked as a durable example of culture and stakeholder alignment."),
    ("Vectura", "company", "Case study in board duties, acquisition pressure, and mission conflict."),
]

LINKS = [
    ("Episode transcript", LENNY_URL),
    ("Eric Ries", "https://www.incorruptible.co"),
    ("The Lean Startup", "https://theleanstartup.com"),
    ("Cloudflare", "https://www.cloudflare.com"),
    ("Anthropic", "https://www.anthropic.com"),
    ("Costco", "https://www.costco.com"),
    ("Novo Nordisk", "https://www.novonordisk.com"),
    ("Lenny's Newsletter", "https://www.lennysnewsletter.com"),
]


def timestamp(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def make_turns(count: int = 240) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    chapter_idx = 0
    for i in range(count):
        start = int(i * LENNY_DURATION_SECONDS / count)
        while chapter_idx + 1 < len(LENNY_CHAPTERS) and start >= LENNY_CHAPTERS[chapter_idx + 1][0]:
            chapter_idx += 1
        chapter_title = LENNY_CHAPTERS[chapter_idx][1]
        speaker = LENNY_HOST if i % 3 == 0 else LENNY_GUEST
        if speaker == LENNY_HOST:
            text = (
                f"In this section on {chapter_title}, help a founder understand the practical tradeoff. "
                "What decision would change this week if they wanted the company to survive incentives, "
                "capital pressure, and leadership transitions?"
            )
        else:
            text = (
                f"The useful move in {chapter_title} is to treat governance as product design for the company itself. "
                "A mission statement is weak unless the board duties, ownership structure, operating rituals, "
                "and culture bank all make the harder choice easier when financial gravity arrives."
            )
        turns.append({
            "speaker": speaker,
            "start": start,
            "end": min(start + 20, LENNY_DURATION_SECONDS),
            "text": text,
            "word_count": word_count(text),
        })
    return turns


def write_lenny_fixture(out_root: Path) -> Path:
    turns = make_turns()
    transcript = "\n".join(f"{t['speaker']}: {t['text']}" for t in turns) + "\n"
    bundle = url_ingest.SourceBundle(
        provider_id="lenny_substack",
        input_url=LENNY_URL,
        canonical_url=LENNY_URL,
        slug="lenny-eric-ries-complex",
        title=f"{LENNY_TITLE} | {LENNY_GUEST}, Lean Startup author",
        transcript_text=transcript,
        transcript_turns=turns,
        transcript_source_url=LENNY_URL,
        metadata={
            "date": LENNY_PUBLISHED_AT,
            "duration_seconds": str(LENNY_DURATION_SECONDS),
            "host": LENNY_HOST,
            "guest": LENNY_GUEST,
            "podcast_title": LENNY_PODCAST,
        },
        chapters=[{"timestamp": timestamp(sec), "title": title} for sec, title in LENNY_CHAPTERS],
        links=[{"text": label, "url": href} for label, href in LINKS],
    )
    episode_dir = url_ingest.write_bundle(bundle, out_root)
    with contextlib.redirect_stdout(io.StringIO()):
        parse_source.main([str(episode_dir / "source" / "_source_input.txt"), str(episode_dir)])

    sidecar = {
        "episode": {
            "title": LENNY_TITLE,
            "podcast_title": LENNY_PODCAST,
            "episode_url": LENNY_URL,
            "published_at": LENNY_PUBLISHED_AT,
            "duration_seconds": LENNY_DURATION_SECONDS,
            "hosts": [LENNY_HOST],
            "guests": [LENNY_GUEST],
            "chapters": [
                {"start": sec, "timestamp": timestamp(sec), "title": title, "summary": ""}
                for sec, title in LENNY_CHAPTERS
            ],
        },
        "verification": {
            "terminology": [
                {"term": term, "category": cat, "confidence": "primary", "notes": notes}
                for term, cat, notes in TERMS
            ],
            "sources": [{"title": label, "url": href, "used_for": ["fixture"]} for label, href in LINKS],
        },
    }
    final_dir = episode_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    (final_dir / "metadata.sidecar.json").write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    notes = {
        "short_title": LENNY_TITLE,
        "description": "A founder-facing episode about encoding mission into company structure before incentives turn hostile.",
        "built_for": "Founders and product leaders choosing governance and operating rules before scale hardens the defaults.",
        "bottom_line": (
            "Durability is designed before the crisis. A company that wants to withstand any era has to encode mission "
            "into governance, incentives, and culture while the founder still has leverage."
        ),
        "takeaways": [
            "Mission statements are weak protection unless governance and board duties make mission-preserving decisions legitimate.",
            "Founder control can expire suddenly, so durable companies need structures that outlast charisma and voting power.",
            "Financial gravity arrives after success; the work is to decide which compromises the company will refuse in advance.",
            "The harder choice can become easier when trust, culture, and operating rituals have been deposited over years.",
            "Public benefit corporations and mission-protective provisions are tools, not branding exercises.",
            "AI governance raises the same alignment problem at company scale: who has authority when incentives conflict?",
            "The practical founder move is to identify fiduciaries and encode purpose before capital markets force the issue.",
            "A company becomes resilient when culture, charter, and incentives tell the same story under pressure.",
        ],
        "claims": [
            {
                "topic": "Governance is product design for the company",
                "claim": "The episode treats governance as the operating system that decides what the company can keep choosing after scale changes the incentives. A founder who waits until the crisis has already lost leverage.",
                "evidence": "The fixture centers public benefit corporations, mission guardians, and board duties as structural choices rather than brand language.",
            },
            {
                "topic": "Financial gravity appears after success",
                "claim": "The pull toward extraction is strongest once a company has something valuable to harvest. That is why protective structure has to be installed while the mission still feels obvious.",
                "evidence": "The chapter outline moves from founder control to shareholder primacy, Vectura, and mission-driven governance.",
            },
            {
                "topic": "Culture banks need deposits before withdrawals",
                "claim": "Trust compounds when leaders repeatedly make costly choices that match the mission. In a hard moment, the organization can draw on that credibility instead of discovering it has none.",
                "evidence": "The synthetic transcript includes Cloudflare, Costco, and the culture-bank frame as examples of repeated choices.",
            },
        ],
        "links": [{"label": label, "url": href, "note": "Public source/link used by the performance fixture."} for label, href in LINKS],
        "keyword_queries": {
            "financial gravity": "financial gravity arrives",
            "public benefit corporation": "public benefit corporations",
            "culture bank": "culture bank",
        },
        "chapter_queries": [title for _, title in LENNY_CHAPTERS],
    }
    (episode_dir / "source" / "episode.notes.json").write_text(
        json.dumps(notes, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return episode_dir


def analyze_source_document(final_dir: Path) -> dict[str, Any]:
    violations: dict[str, list[str]] = {}
    for name in ("podcast-at-a-glance.html", "annotated-transcript.html"):
        text = (final_dir / name).read_text(encoding="utf-8")
        hits: list[str] = []
        if '<link rel="icon" href="data:,">' not in text:
            hits.append("missing blank data favicon")
        if 'type="application/json" id="episode-data"' not in text:
            hits.append("missing episode-data island")
        if re.search(r'\brole=["\']tab(?:list)?["\']|\baria-selected=', text, re.I):
            hits.append("folder tabs use ARIA tab roles")
        for rx in podcast_build.EXTERNAL_RESOURCE_RES:
            if rx.search(text):
                hits.append("external resource reference")
                break
        if hits:
            violations[name] = hits
    return {"clean": not violations, "violations": violations}


def analyze_primitives(final_dir: Path) -> dict[str, Any]:
    missing: dict[str, list[str]] = {}
    for name, markers in podcast_build.REQUIRED_HTML_PRIMITIVES.items():
        text = (final_dir / name).read_text(encoding="utf-8")
        absent = [marker for marker in markers if marker not in text]
        if absent:
            missing[name] = absent
    return {
        "clean": not missing,
        "missing": missing,
        "primitive_files": sorted(podcast_build.REQUIRED_HTML_PRIMITIVES),
    }


def run_once() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="podcastify-perf-") as tmp:
        episode_dir = write_lenny_fixture(Path(tmp))
        parsed = json.loads((episode_dir / "working" / "_parsed.json").read_text(encoding="utf-8"))
        args = SimpleNamespace(host=None, guest=[])
        host, guests = extract_one.resolved_participants(args, parsed)
        preflight = {
            "host": host,
            "guests": guests,
            "resolve_speakers_skipped": extract_one.can_skip_resolve_speakers(args, parsed),
        }

        t0 = time.perf_counter()
        package = podcast_build.build_package(episode_dir)
        t1 = time.perf_counter()
        podcast_build.render_artifacts(episode_dir, package)
        t2 = time.perf_counter()
        validate_rc = podcast_build.validate_artifacts(episode_dir)
        t3 = time.perf_counter()

        final_dir = episode_dir / "final"
        sizes = {
            name: (final_dir / name).stat().st_size
            for name in ("episode.package.json", "podcast-at-a-glance.html", "annotated-transcript.html")
        }
        return {
            "fixture": {
                "url": LENNY_URL,
                "title": LENNY_TITLE,
                "published_at": LENNY_PUBLISHED_AT,
                "duration_seconds": LENNY_DURATION_SECONDS,
                "turns": len(package["turns"]),
                "chapters": len(package["chapters"]),
                "terms": len(package["terms"]),
            },
            "preflight": preflight,
            "timings": {
                "build_package_seconds": round(t1 - t0, 4),
                "render_seconds": round(t2 - t1, 4),
                "validate_seconds": round(t3 - t2, 4),
                "deterministic_total_seconds": round(t3 - t0, 4),
                "ceiling_seconds": RENDER_CEILING_SECONDS,
            },
            "sizes": sizes,
            "validate_rc": validate_rc,
            "source_document": analyze_source_document(final_dir),
            "primitives": analyze_primitives(final_dir),
        }


def regressions(report: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if not report["preflight"]["resolve_speakers_skipped"]:
        out.append("preflight: Lenny fixture should skip resolve_speakers when host+guest metadata is present")
    if report["timings"]["deterministic_total_seconds"] > RENDER_CEILING_SECONDS:
        out.append(
            f"deterministic render path {report['timings']['deterministic_total_seconds']}s "
            f"exceeds {RENDER_CEILING_SECONDS}s ceiling"
        )
    if report["validate_rc"] != 0:
        out.append("podcast_build.validate_artifacts failed")
    if not report["source_document"]["clean"]:
        out.append(f"source-document contract violations: {report['source_document']['violations']}")
    if not report["primitives"]["clean"]:
        out.append(f"primitive markers missing: {report['primitives']['missing']}")
    return out


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# podcastify optimization harness",
        "",
        f"- fixture: {report['fixture']['title']} ({report['fixture']['published_at']}, {report['fixture']['duration_seconds']}s)",
        f"- corpus: {report['fixture']['turns']} turns, {report['fixture']['chapters']} chapters, {report['fixture']['terms']} terms",
        f"- preflight: resolve_speakers skipped = {report['preflight']['resolve_speakers_skipped']} "
        f"({report['preflight']['host']} / {', '.join(report['preflight']['guests'])})",
        f"- deterministic path: {report['timings']['deterministic_total_seconds']}s "
        f"(build {report['timings']['build_package_seconds']}s, render {report['timings']['render_seconds']}s, "
        f"validate {report['timings']['validate_seconds']}s)",
        f"- source-document contract: {'clean' if report['source_document']['clean'] else report['source_document']['violations']}",
        f"- primitives: {'clean' if report['primitives']['clean'] else report['primitives']['missing']}",
    ]
    regs = regressions(report)
    if regs:
        lines += ["", "## REGRESSIONS", *[f"- {r}" for r in regs]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    report = run_once()
    print(json.dumps(report, indent=2) if args.json else to_markdown(report))
    regs = regressions(report)
    if args.check and regs:
        print("perf harness regressions:\n- " + "\n- ".join(regs), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
