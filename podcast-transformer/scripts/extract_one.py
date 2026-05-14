#!/usr/bin/env python3
"""End-to-end orchestrator for the /podcastextract pipeline.

Takes a dropped source/resource file and produces a fully-built episode
package: verified transcript, sidecar with chapters + terminology URLs,
authored notes.json, rendered HTML artifacts, validators all green.

Every step is instrumented with `pipeline_log.py` so the harness can score
total wall-clock plus the script-vs-LLM split.

Pipeline (subprocess calls, all instrumented via pipeline_log.py):

   0. url_ingest                for supported URLs, assemble source package
   1. parse_source              derive metadata, chapters, links, slug
   2. fetch_transcript          advanced browse-cli fallback (skip if prepared)
   3. capture_article_title     pull canonical title before trim
   4. trim_substack_article_body
   5. convert_transcript        scrape format → canonical Speaker: text
   6. sidecar_init              bootstrap metadata.sidecar.json
   7. sidecar_chapters          populate episode.chapters from parsed JSON
   8. extract_entity_links      harvest show-notes URLs from source file
   9. draft_notes               LLM call: notes.json (Ollama default)
  10. promote_notes             rename .draft.json → episode.notes.json,
                                  force short_title = canonical title
  11. sharpen_notes             LLM call: per-item topic + takeaway rewrite
  12. anchor_chapters           proportional within-turn chapter queries
  13. splice_chapter_queries    splice into notes.json
  14. populate_terminology      LLM call: enumerate people/orgs/books/concepts
  15. merge_terminology_urls    attach show-notes URLs to terminology
  16. enrich_terminology        LLM call: categorize + describe bare entries
  17. podcast_build all         render HTML + run validators

Usage:
  python3 scripts/extract_one.py <source_file> [--out-root DIR] [--slug SLUG]
                                  [--skip-fetch] [--keep-existing]

Defaults:
  --out-root  podcast-output
  --slug      derived from parse_source.py
  --skip-fetch  if source/transcript_raw.txt already exists, don't refetch
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "podcast-transformer" / "scripts"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


class StepError(RuntimeError):
    pass


def validate_slug(slug: str) -> str:
    """Return a safe episode slug or raise ValueError.

    Slugs become directory names that may be deleted on re-run, so they must
    not contain path separators, parent-directory components, or shell-ish
    punctuation.
    """
    if not SLUG_RE.fullmatch(slug or ""):
        raise ValueError(
            "Invalid slug. Use lowercase letters, numbers, and hyphens only "
            "(max 81 chars)."
        )
    return slug


def resolve_episode_dir(out_root: Path, slug: str) -> Path:
    root = out_root.resolve()
    episode_dir = (root / validate_slug(slug)).resolve()
    if not episode_dir.is_relative_to(root):
        raise ValueError(f"Episode dir escaped output root: {episode_dir}")
    return episode_dir


def is_http_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def prepare_source_input(args: argparse.Namespace) -> tuple[Path, Path | None]:
    source_value = str(args.source_file)
    if not is_http_url(source_value):
        return Path(source_value), None
    cmd = [
        sys.executable,
        str(SCRIPTS / "url_ingest.py"),
        source_value,
        "--out-root",
        str(args.out_root),
    ]
    if args.slug:
        cmd.extend(["--slug", args.slug])
    result = run(cmd)
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    episode_dir = Path(stdout.splitlines()[-1]) if stdout else resolve_episode_dir(args.out_root, args.slug)
    source_input = episode_dir / "source" / "_source_input.txt"
    if not source_input.exists():
        raise StepError(f"URL ingest did not create expected source file: {source_input}")
    return source_input, episode_dir


def has_prepared_transcript(episode_dir: Path) -> bool:
    transcript = episode_dir / "source" / "user-provided-transcript.txt"
    return transcript.exists() and transcript.stat().st_size > 0


def run(cmd: list[str], *, episode_dir: Optional[Path] = None,
        stdout_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Subprocess wrapper that streams output and raises on non-zero exit."""
    env = os.environ.copy()
    if episode_dir is not None:
        env["PT_EPISODE_DIR"] = str(episode_dir)
    print(f"  $ {' '.join(cmd)}", file=sys.stderr)
    if stdout_path is not None:
        with stdout_path.open("wb") as fh:
            result = subprocess.run(cmd, env=env, stdout=fh, stderr=subprocess.PIPE)
        if result.stderr:
            sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
    else:
        result = subprocess.run(cmd, env=env, capture_output=True)
        if result.stdout:
            sys.stderr.write(result.stdout.decode("utf-8", errors="replace"))
        if result.stderr:
            sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
    if result.returncode != 0:
        raise StepError(f"{cmd[0]} exited {result.returncode}")
    return result


def step(name: str, kind: str, episode_dir: Path, fn, *, note: str = ""):
    """Bracket a step in pipeline_log.py calls and run `fn`. fn must take no
    args; it returns whatever it wants (we ignore)."""
    run([sys.executable, str(SCRIPTS / "pipeline_log.py"), "start",
         name, "--kind", kind, "--episode-dir", str(episode_dir),
         "--note", note])
    t0 = time.time()
    try:
        fn()
        status = "ok"
    except Exception as e:
        status = f"fail: {type(e).__name__}: {e}"
        run([sys.executable, str(SCRIPTS / "pipeline_log.py"), "end",
             name, "--episode-dir", str(episode_dir),
             "--status", status])
        raise
    finally:
        elapsed = time.time() - t0
    run([sys.executable, str(SCRIPTS / "pipeline_log.py"), "end",
         name, "--episode-dir", str(episode_dir), "--status", status,
         "--note", f"{elapsed:.1f}s"])


def fetch_transcript(canonical_url: str, raw_path: Path) -> None:
    """browse-cli the canonical URL. Lenny's Substack needs ?showTranscript=true."""
    url = canonical_url
    if "lennysnewsletter.com" in url and "showTranscript=true" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}showTranscript=true"
    browse_cli = shutil.which("browse")
    if not browse_cli:
        raise StepError(
            "browse-cli not found on PATH; install with: "
            "brew install pepijnsenders/tap/browse && browse init"
        )
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [browse_cli, "--wait", "5000", url]
    print(f"  $ {' '.join(cmd)}", file=sys.stderr)
    with raw_path.open("wb") as fh:
        result = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE)
    if result.stderr:
        sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
    if result.returncode != 0:
        raise StepError(f"browse exited {result.returncode}")
    if raw_path.stat().st_size < 1000:
        raise StepError(f"browse output too small ({raw_path.stat().st_size} bytes); transcript probably didn't load")


def extract_article_title(raw_path: Path, podcast_title: str) -> str:
    """Pick the article title out of the browse-cli scrape's preamble.

    Browse-cli emits the article header as plain markdown, e.g.:

        Lenny's Podcast: Product \\| Career \\| Growth

        How to build a company that withstands any era \\| Eric Ries, Lean Startup author

        0:00

    The episode title is the longest meaningful line before the first
    transcript timestamp, excluding the podcast title and image/audio UI.
    Returns empty string if it can't find one."""
    text = raw_path.read_text(encoding="utf-8")
    import re as _re
    ts_re = _re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
    podcast_norm = podcast_title.lower().strip() if podcast_title else ""
    candidates: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if ts_re.match(s):
            break
        # Skip markdown links/images and audio-player UI
        if s.startswith("[!") or s.startswith("![") or s.startswith("["):
            continue
        if "audio playback" in s.lower():
            continue
        if "subscribe" in s.lower() and len(s) < 50:
            continue
        if podcast_norm and s.lower() == podcast_norm:
            continue
        # Markdown escapes the `|` separator; unescape so we can split on it.
        s = s.replace(r"\|", "|").replace("\\|", "|")
        candidates.append(s)
    if not candidates:
        return ""
    title = max(candidates, key=len)
    # Strip the publisher's byline suffix — Substack's H1 commonly reads
    # "How to build a company that withstands any era | Eric Ries, Lean Startup author".
    # The reader's canonical title is the part BEFORE the separator.
    for sep in (" | ", " — ", " – ", " - "):
        if sep in title:
            title = title.split(sep, 1)[0].strip()
            break
    return title


def extract_substack_section(raw_path: Path) -> None:
    """The browse-cli output of a Substack `?showTranscript=true` page
    contains the article body THEN the transcript section. We want just the
    transcript portion (everything after the first `0:00` / `0:0X` /
    `MM:SS` line followed by an ALL-CAPS speaker line) so the converter
    doesn't trip on article prose.

    If the raw file already starts with the timestamp pattern, leave it alone.
    """
    text = raw_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Find the first "<ts> … <NAME> … text" sequence (NAME = ALL CAPS or
    # Title Case, 2-4 words). Browse-cli inserts blank lines between fields.
    import re as _re
    ts_re = _re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
    allcaps_re = _re.compile(r"^[A-Z][A-Z .'-]{2,}[A-Z]$")
    titlecase_re = _re.compile(
        r"^[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){1,3}$"
    )

    def _is_speaker(s: str) -> bool:
        s = s.strip()
        if not s or len(s) > 60:
            return False
        return bool(allcaps_re.match(s) or titlecase_re.match(s))

    start = None
    for i, ln in enumerate(lines[:-4]):
        if ts_re.match(ln.strip()):
            # Look up to 4 non-blank lines forward for a speaker name
            j = i + 1
            checked = 0
            while j < len(lines) and checked < 4:
                cand = lines[j].strip()
                if cand:
                    if _is_speaker(cand):
                        start = i
                        break
                    # Non-blank, non-speaker → this timestamp is UI, not transcript
                    break
                j += 1
            if start is not None:
                break
    if start is None:
        return  # No transcript markers found; downstream will fail loudly
    if start == 0:
        return
    new_text = "\n".join(lines[start:]) + "\n"
    raw_path.write_text(new_text, encoding="utf-8")
    print(f"  trimmed {start} leading lines (article body)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source_file")
    p.add_argument("--out-root", type=Path, default=REPO_ROOT / "podcast-output")
    p.add_argument("--slug", default=None)
    p.add_argument("--skip-fetch", action="store_true",
                   help="if source/transcript_raw.txt already exists, don't refetch")
    p.add_argument("--keep-existing", action="store_true",
                   help="don't wipe existing episode dir (default: wipe so the run is reproducible)")
    p.add_argument("--title", default=None,
                   help="explicit episode title — needed for the inline-transcript path "
                        "where there's no browse-cli scrape to capture it from")
    p.add_argument("--published-at", default=None,
                   help="explicit publish date (YYYY-MM-DD) — surfaces in the library index kicker")
    p.add_argument("--duration-seconds", type=int, default=None,
                   help="explicit episode duration in seconds — surfaces in the kicker")
    p.add_argument("--host", default=None,
                   help="explicit host name (drives the 'about the host' sidebar card)")
    p.add_argument("--guest", action="append", default=[],
                   help="explicit guest name; repeatable (first guest is the headline guest)")
    p.add_argument("--draft-model", default=None,
                   help="model alias for draft_notes. Default: $PODCAST_DRAFT_MODEL (gemma4:e4b-nvfp4).")
    p.add_argument("--draft-backend", choices=["ollama", "api"], default="ollama",
                   help="draft_notes backend: ollama (local, default) or api (paid Anthropic API)")
    p.add_argument("--draft-use-api", action="store_true",
                   help="(deprecated alias for --draft-backend api)")
    p.add_argument("--show-config", action="store_true",
                   help="dump the active pipeline configuration and exit")
    args = p.parse_args(argv)
    if args.draft_use_api:
        args.draft_backend = "api"
    if args.show_config:
        from pipeline_config import print_config
        print_config()
        return 0

    source_file, prepared_episode_dir = prepare_source_input(args)
    if prepared_episode_dir is None and not source_file.is_file():
        print(f"ERROR: source file not found: {source_file}", file=sys.stderr)
        return 2

    # 1. Slug derivation (fast, runs outside the instrumented pipeline)
    slug = args.slug
    if prepared_episode_dir is not None and not slug:
        slug = prepared_episode_dir.name
    if not slug:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "parse_source.py"),
             str(source_file), "/tmp/_slug_probe", "--print-slug"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"ERROR: slug derivation failed:\n{result.stderr}", file=sys.stderr)
            return 2
        slug = result.stdout.strip() or "episode"

    try:
        episode_dir = resolve_episode_dir(args.out_root, slug)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if episode_dir.exists() and not args.keep_existing and episode_dir != prepared_episode_dir:
        shutil.rmtree(episode_dir)
    episode_dir.mkdir(parents=True, exist_ok=True)
    (episode_dir / "source").mkdir(exist_ok=True)
    (episode_dir / "working").mkdir(exist_ok=True)
    (episode_dir / "final").mkdir(exist_ok=True)

    # Reset metrics so each run is clean
    run([sys.executable, str(SCRIPTS / "pipeline_log.py"), "reset", str(episode_dir)])

    print(f"Episode dir: {episode_dir}", file=sys.stderr)
    print(f"Slug:        {slug}", file=sys.stderr)
    print(f"Source:      {source_file}", file=sys.stderr)
    print("-" * 60, file=sys.stderr)

    # ── 1. parse_source ───────────────────────────────────────────────
    step("parse_source", "script", episode_dir, lambda: run(
        [sys.executable, str(SCRIPTS / "parse_source.py"),
         str(source_file), str(episode_dir)],
    ))

    parsed = json.loads((episode_dir / "working" / "_parsed.json").read_text(encoding="utf-8"))
    canonical_url = parsed["canonical_url"]

    # ── 2. fetch transcript (skip if inline transcript present) ───────
    transcript_md = episode_dir / "source" / "user-provided-transcript.txt"
    raw_path = episode_dir / "source" / "transcript_raw.txt"

    # Holder for the canonical article title — captured from the un-trimmed
    # browse-cli scrape, used downstream by sidecar_init.
    captured_title = {"value": ""}

    if parsed.get("inline_transcript") or has_prepared_transcript(episode_dir):
        # ingest_combined-style inline transcript already extracted
        pass
    else:
        if args.skip_fetch and raw_path.exists():
            print("(skip-fetch: using existing transcript_raw.txt)", file=sys.stderr)
        else:
            step("fetch_transcript", "script", episode_dir,
                 lambda: fetch_transcript(canonical_url, raw_path),
                 note=canonical_url)
        # Capture the article title BEFORE the preamble is trimmed away.
        def _capture_title() -> None:
            captured_title["value"] = extract_article_title(
                raw_path, parsed.get("podcast_title", "")
            )
            print(f"  article title: {captured_title['value']!r}", file=sys.stderr)

        step("capture_article_title", "script", episode_dir, _capture_title)
        # Trim leading article body, then convert
        step("trim_substack_article_body", "script", episode_dir,
             lambda: extract_substack_section(raw_path))
        step("convert_transcript", "script", episode_dir, lambda: run(
            [sys.executable, str(SCRIPTS / "convert_transcript.py"),
             str(episode_dir)],
        ))

    # ── 3. sidecar init ───────────────────────────────────────────────
    # Title precedence: CLI flag > captured-from-scrape > URL-slug guess.
    title = args.title or captured_title["value"] or parsed.get("episode_title_guess", "")

    # ── 3a. resolve_speakers ─────────────────────────────────────────
    # Tiny Ollama call that reads the episode title + opening turns of the
    # transcript and returns {host, guests}. Replaces the prose-mining regex
    # heuristics that used to live in parse_source — those overfit one
    # publisher's voice and broke everywhere else. The model is asked to
    # return empty values when uncertain, so a wrong identity never poisons
    # the briefing.
    step(
        "resolve_speakers", "script", episode_dir,
        lambda: run([
            sys.executable, str(SCRIPTS / "resolve_speakers.py"),
            str(episode_dir),
            "--title", title or "",
            "--podcast-title", parsed.get("podcast_title", "") or "",
            "--episode-url", canonical_url or "",
        ]),
        note="LLM call: identify host + guests from title + opening transcript",
    )
    speakers_path = episode_dir / "working" / "_speakers.json"
    speakers = {"host": "", "guests": []}
    if speakers_path.is_file():
        try:
            speakers = json.loads(speakers_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    def _init_sidecar() -> None:
        cmd = [
            sys.executable, str(SCRIPTS / "sidecar.py"), "init",
            "--output", str(episode_dir / "final" / "metadata.sidecar.json"),
            "--existing-transcript", str(transcript_md),
            "--force",
        ]
        if title:
            cmd += ["--title", title]
        if parsed.get("podcast_title"):
            cmd += ["--podcast-title", parsed["podcast_title"]]
        if canonical_url:
            cmd += ["--episode-url", canonical_url]
        # Host precedence: CLI flag > bundle declaration (parsed) > LLM resolver.
        host = args.host or parsed.get("host_guess") or speakers.get("host", "")
        if host:
            cmd += ["--host", host]
        # Guest precedence: same order as host. The bundle rarely declares a
        # guest; the resolver is normally the source of truth.
        guests = (
            args.guest
            or ([parsed["guest_guess"]] if parsed.get("guest_guess") else [])
            or [g for g in speakers.get("guests", []) if g]
        )
        for g in guests:
            cmd += ["--guest", g]
        # Fall back to whatever url_ingest captured (Substack transcription.json
        # ends → duration, page's `<time datetime>` → published_at). Without
        # this, sidecar.episode.duration_seconds is `null` and the library
        # index card renders "0M"; published_at is "" and the hero loses its
        # date stamp. Both signals are already present in `_source_input.txt`
        # — we just need to forward them through.
        published_at = args.published_at or parsed.get("published_at_guess", "")
        if published_at:
            cmd += ["--published-at", published_at]
        duration_seconds = args.duration_seconds or parsed.get("duration_seconds_guess") or 0
        if not duration_seconds and transcript_md.exists():
            # Last-resort estimate so the library index doesn't render "0M".
            # 150 words/minute is the conventional podcast pace; the value is
            # explicitly approximate (rounded to nearest minute) and gets
            # overwritten if the user later supplies a precise duration via
            # --duration-seconds.
            words = len(transcript_md.read_text(encoding="utf-8").split())
            if words:
                duration_seconds = max(60, round(words / 150) * 60)
        if duration_seconds:
            cmd += ["--duration-seconds", str(duration_seconds)]
        run(cmd)

    step("sidecar_init", "script", episode_dir, _init_sidecar)

    # ── 4. populate chapters ──────────────────────────────────────────
    step("sidecar_chapters", "script", episode_dir, lambda: run(
        [sys.executable, str(SCRIPTS / "sidecar_chapters.py"), str(episode_dir)],
    ))

    # ── 5. extract show-notes link list ───────────────────────────────
    step("extract_entity_links", "script", episode_dir, lambda: run(
        [sys.executable, str(SCRIPTS / "extract_entity_links.py"),
         str(episode_dir),
         "--source", str(episode_dir / "source" / "_source_input.txt")],
    ))

    # ── 6. draft notes (local Ollama by default; paid API on request) ───
    def _draft_notes() -> None:
        cmd = [sys.executable, str(SCRIPTS / "draft_notes.py"),
               str(episode_dir), "--force"]
        if args.draft_model:
            cmd += ["--model", args.draft_model]
        if args.draft_backend == "api":
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise StepError("--draft-backend api requires ANTHROPIC_API_KEY in env")
            cmd += ["--use-api"]
        else:
            cmd += ["--use-ollama"]
        run(cmd)

    auth_note = {
        "ollama": "local Ollama (no cloud)",
        "api": "paid Anthropic API",
    }[args.draft_backend]
    step("draft_notes", "script", episode_dir, _draft_notes,
         note=f"draft via {auth_note}")

    # ── 7. promote draft → final, with a length-aware short_title ──────
    # The model drafts a punchy `short_title`. Normally we override it with
    # the publisher's canonical so the briefing matches what the publisher
    # actually shipped. BUT Tim Ferriss-style SEO titles run ~200 chars
    # ("Elad Gil, Consigliere to Empire Builders — How to Spot Billion-Dollar
    # Companies Before Everyone Else, The Misty AI Frontier, How Coke Beat
    # Pepsi, When Consensus Pays, and Much More (#863)") — far too long for
    # the briefing's top bar / library card chip. When the canonical is
    # genuinely long, prefer the publisher's own pre-em-dash chunk, then
    # fall back to the LLM draft. Keep the FULL title in `episode.title`
    # (the hero block can wrap it); only override `short_title` here.
    SHORT_TITLE_CHAR_LIMIT = 80

    def _shorten_for_chrome(canonical: str, draft_short: str) -> str:
        canonical = canonical.strip()
        if not canonical:
            return draft_short or canonical
        if len(canonical) <= SHORT_TITLE_CHAR_LIMIT:
            return canonical
        # Tim Ferriss / Sam Harris convention: "<Guest Headline> — <topic list>".
        # The first em-dash chunk is the real headline, the rest is SEO bait.
        for sep in (" — ", " – ", ": "):
            head = canonical.split(sep, 1)[0].strip()
            if head and len(head) <= SHORT_TITLE_CHAR_LIMIT:
                return head
        # Fall back to whatever the LLM drafted, then to a hard truncation.
        if draft_short and len(draft_short) <= SHORT_TITLE_CHAR_LIMIT:
            return draft_short
        return canonical[: SHORT_TITLE_CHAR_LIMIT - 1].rstrip() + "…"

    def _promote() -> None:
        draft = episode_dir / "source" / "episode.notes.draft.json"
        final = episode_dir / "source" / "episode.notes.json"
        if not draft.exists():
            raise StepError(f"draft missing: {draft}")
        notes = json.loads(draft.read_text(encoding="utf-8"))
        sidecar = json.loads((episode_dir / "final" / "metadata.sidecar.json").read_text(encoding="utf-8"))
        canonical_title = (sidecar.get("episode", {}).get("title") or "").strip()
        draft_short = (notes.get("short_title") or "").strip()
        notes["short_title"] = _shorten_for_chrome(canonical_title, draft_short)
        final.write_text(json.dumps(notes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    step("promote_notes", "script", episode_dir, _promote)

    # ── 7b. sharpen topics + takeaways via the sharpen-stage model ─────────
    # Hybrid local-model strategy: a fast small model for the bulky transcript
    # drafting (PODCAST_DRAFT_MODEL, default gemma4:e4b-nvfp4) and a larger
    # editorial model for per-item polish (PODCAST_SHARPEN_MODEL, default
    # qwen3.6:35B-a3b-nvfp4 with think=False — sharp short outputs in seconds).
    # Both defaults live in pipeline_config.py; the sharpen script reads them
    # directly when no --model override is given.
    step("sharpen_notes", "script", episode_dir, lambda: run(
        [sys.executable, str(SCRIPTS / "sharpen_notes.py"), str(episode_dir)],
    ), note="LLM call: per-item punchy-rewrite second pass")

    # ── 8. anchor chapters (proportional — eliminates duplicate queries) ─
    # Skipped when there are no chapters or no per-turn timestamps
    # (the inline-transcript path doesn't produce _turn_timestamps.json).
    def _anchor() -> None:
        chapters_json = episode_dir / "working" / "_chapters.json"
        chapters_json.write_text(
            json.dumps(parsed["chapters"], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        turns_json = episode_dir / "working" / "_turn_timestamps.json"
        out_path = episode_dir / "working" / "_chapter_queries.json"
        if not parsed["chapters"] or not turns_json.exists():
            print(f"  no chapters / no turn timestamps — writing empty chapter_queries",
                  file=sys.stderr)
            out_path.write_text("[]\n", encoding="utf-8")
            return
        sidecar = json.loads((episode_dir / "final" / "metadata.sidecar.json").read_text(encoding="utf-8"))
        duration = int(sidecar.get("episode", {}).get("duration_seconds") or 0)
        run(
            [sys.executable, str(SCRIPTS / "anchor_chapters_proportional.py"),
             "--transcript", str(transcript_md),
             "--turns", str(turns_json),
             "--chapters", str(chapters_json),
             "--duration-seconds", str(duration)],
            stdout_path=out_path,
        )

    step("anchor_chapters", "script", episode_dir, _anchor)

    # ── 9. splice chapter_queries into notes.json ─────────────────────
    def _splice() -> None:
        notes_path = episode_dir / "source" / "episode.notes.json"
        queries_path = episode_dir / "working" / "_chapter_queries.json"
        notes = json.loads(notes_path.read_text(encoding="utf-8"))
        queries = json.loads(queries_path.read_text(encoding="utf-8"))
        notes["chapter_queries"] = queries
        notes_path.write_text(
            json.dumps(notes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    step("splice_chapter_queries", "script", episode_dir, _splice)

    # ── 9b. generate chapters via LLM when the publisher didn't expose any ──
    # Substack/New Yorker/FoundMyFitness/99pi/Tim Ferriss pages don't embed
    # YouTube-style `(00:00) Title` chapter timelines in their HTML, so
    # sidecar.episode.chapters stays empty and the transcript browser's left
    # rail collapses to just the search box. generate_chapters.py reads the
    # full transcript and asks the local Gemma model for 8-12 chapter
    # boundaries with verbatim query phrases for exact turn-anchoring.
    # No-op when the sidecar already has chapters from another source.
    step(
        "generate_chapters", "script", episode_dir,
        lambda: run([sys.executable, str(SCRIPTS / "generate_chapters.py"),
                     str(episode_dir)]),
        note="LLM call: 8-12 chapter boundaries + verbatim anchor queries",
    )

    # ── 10a. populate terminology via LLM (entity enumeration) ────────
    step("populate_terminology", "script", episode_dir, lambda: run(
        [sys.executable, str(SCRIPTS / "populate_terminology.py"),
         str(episode_dir)],
    ), note="LLM call: enumerate people/orgs/books/concepts")

    # ── 10b. merge URLs from show-notes link list into terminology ────
    # NOTE: `--add-missing` is deliberately NOT passed. populate_terminology's
    # LLM call already enumerated the real entities from the transcript; the
    # merge step only ATTACHES show-notes URLs to those. Without this guard,
    # publisher chrome links — Substack's `/privacy`, `/tos`, `/ccpa`, the
    # Cloudflare email-protection rewrite, "turn on JavaScript" noscript
    # fallbacks — got promoted to first-class "concept" terminology entries,
    # which then got hallucinated descriptions by enrich_terminology.
    step("merge_terminology_urls", "script", episode_dir, lambda: run(
        [sys.executable, str(SCRIPTS / "merge_terminology_urls.py"),
         str(episode_dir)],
    ))

    # ── 10c. enrich entries that landed bare from the show-notes merge ──
    step("enrich_terminology", "script", episode_dir, lambda: run(
        [sys.executable, str(SCRIPTS / "enrich_terminology.py"),
         str(episode_dir)],
    ), note="LLM call: categorize + describe bare entries")

    # ── 10d. resolve canonical Wikipedia URLs for entries still missing one ──
    # Show notes from url_ingest bundles rarely include per-entity links, so
    # without this step the briefing's inspector is all plain text. Bounded
    # network step — failures (404, disambiguation, name mismatch) just leave
    # the URL empty and the renderer falls back to plain text.
    step("resolve_terminology_urls", "script", episode_dir, lambda: run(
        [sys.executable, str(SCRIPTS / "resolve_terminology_urls.py"),
         str(episode_dir)],
    ), note="HTTP: Wikipedia REST summary lookups for people/companies/books")

    # ── 11. podcast_build all ─────────────────────────────────────────
    step("podcast_build_all", "script", episode_dir, lambda: run(
        [sys.executable, str(SCRIPTS / "podcast_build.py"), "all",
         str(episode_dir)],
    ))

    print("-" * 60, file=sys.stderr)
    print(f"DONE. Episode: {episode_dir}", file=sys.stderr)
    # Print summary inline so a human can eyeball it
    run([sys.executable, str(SCRIPTS / "pipeline_log.py"), "summary",
         str(episode_dir)])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except StepError as e:
        print(f"\nPIPELINE FAILED: {e}", file=sys.stderr)
        sys.exit(2)
