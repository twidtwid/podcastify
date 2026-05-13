#!/usr/bin/env python3
"""Create and validate podcast-transformer metadata sidecars."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "podcast-transformer/v1"
VALID_STATUSES = {"draft", "in_review", "needs_review", "verified"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel_or_abs(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def input_for_path(path_value: str, input_type: str, base: Path) -> dict[str, Any]:
    path = Path(path_value)
    record: dict[str, Any] = {
        "type": input_type,
        "path": rel_or_abs(path, base),
        "collected_at": utc_now(),
    }
    if path.exists() and path.is_file():
        record["sha256"] = sha256_file(path)
        record["bytes"] = path.stat().st_size
    else:
        record["notes"] = "Path was recorded but not found when sidecar was initialized"
    return record


def input_for_url(url: str, input_type: str, title: str | None = None, used_for: list[str] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": input_type,
        "url": url,
        "collected_at": utc_now(),
    }
    if title:
        record["title"] = title
    if used_for:
        record["used_for"] = used_for
    return record


def default_sidecar(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output).resolve()
    base = output.parent
    inputs: list[dict[str, Any]] = []

    for media in args.media or []:
        inputs.append(input_for_path(media, "media", base))
    for transcript in args.existing_transcript or []:
        inputs.append(input_for_path(transcript, "existing_transcript", base))
    for url in args.episode_url or []:
        inputs.append(input_for_url(url, "episode_page", used_for=["episode metadata"]))
    for url in args.companion_url or []:
        inputs.append(input_for_url(url, "companion_page", used_for=["show notes", "links", "terminology"]))
    for url in args.rss_url or []:
        inputs.append(input_for_url(url, "rss", used_for=["episode metadata"]))

    episode = {
        "title": args.title or "",
        "podcast_title": args.podcast_title or "",
        "episode_number": args.episode_number or "",
        "episode_url": (args.episode_url or [""])[0],
        "rss_url": (args.rss_url or [""])[0],
        "description": args.description or "",
        "published_at": args.published_at or "",
        "duration_seconds": args.duration_seconds,
        "hosts": args.host or [],
        "guests": args.guest or [],
        "language": args.language or "",
        "chapters": [],
    }

    now = utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": now,
        "updated_at": now,
        "episode": episode,
        "inputs": inputs,
        "transcription": {
            "engine": "",
            "model": "",
            "language": args.language or "",
            "started_at": "",
            "completed_at": "",
            "raw_transcript_path": "",
            "verified_transcript_path": "",
            "segment_transcript_path": "",
            "diarization": {"enabled": False, "method": "", "notes": ""},
            "context_terms": [],
        },
        "verification": {
            "status": "draft",
            "verified_at": "",
            "verified_by": "",
            "methods": [],
            "sources": [],
            "terminology": [],
            "people": [],
            "organizations": [],
            "uncertain_spans": [],
            "corrections": [],
        },
        "outputs": {
            "verified_transcript_md": "",
            "verified_transcript_json": "",
            "metadata_sidecar": rel_or_abs(output, base),
            "annotated_transcript_html": "",
            "summary_html": "",
            "verification_notes": "",
        },
    }


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Sidecar root must be a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = utc_now()
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def require_dict(data: dict[str, Any], key: str, errors: list[str]) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        errors.append(f"{key} must be an object")
        return {}
    return value


def require_list(data: dict[str, Any], key: str, errors: list[str]) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        errors.append(f"{key} must be an array")
        return []
    return value


def check_path(path_value: str, base: Path, label: str, warnings: list[str]) -> None:
    if not path_value:
        return
    path = Path(path_value)
    if path.is_absolute():
        if not path.exists():
            warnings.append(f"{label} points to a missing local path: {path_value}")
        return
    # Paths in the sidecar may be stored relative to the sidecar's directory
    # (final/) or to the episode root (parent of final/). Try both before
    # warning so cwd-relative inputs from `init` and final/-relative outputs
    # both resolve cleanly.
    candidates = [base / path, base.parent / path, Path.cwd() / path]
    if not any(c.exists() for c in candidates):
        warnings.append(f"{label} points to a missing local path: {path_value}")


def validate_sidecar(data: dict[str, Any], sidecar_path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    base = sidecar_path.parent

    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")

    episode = require_dict(data, "episode", errors)
    inputs = require_list(data, "inputs", errors)
    transcription = require_dict(data, "transcription", errors)
    verification = require_dict(data, "verification", errors)
    outputs = require_dict(data, "outputs", errors)

    status = verification.get("status")
    if status not in VALID_STATUSES:
        errors.append(f"verification.status must be one of {sorted(VALID_STATUSES)}")

    if not inputs:
        warnings.append("inputs is empty; record at least the media file, episode page, or user note")

    if not episode.get("title"):
        warnings.append("episode.title is empty")
    if not episode.get("podcast_title"):
        warnings.append("episode.podcast_title is empty")

    for index, item in enumerate(inputs):
        if not isinstance(item, dict):
            errors.append(f"inputs[{index}] must be an object")
            continue
        if not item.get("type"):
            errors.append(f"inputs[{index}].type is required")
        if not item.get("path") and not item.get("url"):
            errors.append(f"inputs[{index}] must contain path or url")
        if item.get("path"):
            check_path(str(item["path"]), base, f"inputs[{index}].path", warnings)

    for key in ("raw_transcript_path", "verified_transcript_path", "segment_transcript_path"):
        check_path(str(transcription.get(key) or ""), base, f"transcription.{key}", warnings)

    for key, value in outputs.items():
        if key.endswith("_html") or key.endswith("_md") or key.endswith("_json") or key == "verification_notes":
            check_path(str(value or ""), base, f"outputs.{key}", warnings)

    sources = verification.get("sources", [])
    if sources and not isinstance(sources, list):
        errors.append("verification.sources must be an array")
    elif isinstance(sources, list):
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"verification.sources[{index}] must be an object")
                continue
            if not source.get("url") and not source.get("path"):
                errors.append(f"verification.sources[{index}] must contain url or path")
            if not source.get("used_for"):
                warnings.append(f"verification.sources[{index}].used_for is empty")

    uncertain = verification.get("uncertain_spans", [])
    if uncertain and not isinstance(uncertain, list):
        errors.append("verification.uncertain_spans must be an array")

    if status == "verified":
        if not verification.get("verified_at"):
            errors.append("verified sidecars must set verification.verified_at")
        if not transcription.get("verified_transcript_path") and not outputs.get("verified_transcript_md"):
            errors.append("verified sidecars must point to a verified transcript")
        if uncertain:
            warnings.append("sidecar is verified while uncertain_spans is non-empty; confirm uncertainties are acceptable")

    return errors, warnings


def cmd_init(args: argparse.Namespace) -> int:
    output = Path(args.output)
    if output.exists() and not args.force:
        print(f"Refusing to overwrite existing sidecar: {output}", file=sys.stderr)
        return 2
    write_json(output, default_sidecar(args))
    print(f"Wrote {output}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.sidecar)
    try:
        data = load_json(path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    errors, warnings = validate_sidecar(data, path)
    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARN: {message}")
    if errors or (warnings and args.strict):
        return 1
    print("Sidecar is valid")
    return 0


def cmd_add_source(args: argparse.Namespace) -> int:
    path = Path(args.sidecar)
    data = load_json(path)
    verification = data.setdefault("verification", {})
    sources = verification.setdefault("sources", [])
    if not isinstance(sources, list):
        raise ValueError("verification.sources must be an array")
    source: dict[str, Any] = {
        "title": args.title or "",
        "accessed_at": args.accessed_at or utc_now(),
        "used_for": args.used_for or [],
    }
    if args.url:
        source["url"] = args.url
    if args.path:
        source["path"] = args.path
    if not args.url and not args.path:
        raise ValueError("add-source requires --url or --path")
    sources.append(source)
    write_json(path, data)
    print(f"Added source to {path}")
    return 0


def cmd_stamp_verified(args: argparse.Namespace) -> int:
    path = Path(args.sidecar)
    data = load_json(path)
    verification = data.setdefault("verification", {})
    transcription = data.setdefault("transcription", {})
    outputs = data.setdefault("outputs", {})
    verification["status"] = "verified"
    verification["verified_at"] = args.verified_at or utc_now()
    verification["verified_by"] = args.verified_by or "Codex"
    if args.transcript:
        transcription["verified_transcript_path"] = args.transcript
        outputs["verified_transcript_md"] = args.transcript
    write_json(path, data)
    print(f"Stamped {path} as verified")
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    """Fill in verification.terminology entries from references/terminology_library.json.

    Two modes:
    - Explicit: `--term Anthropic --term Costco` adds those entries from the library.
    - Implicit: with no --term flags, takes every bare-string term name already
      present in the sidecar's terminology block and enriches the ones that
      are missing notes/url.
    """
    repo_root = Path(__file__).resolve().parents[2]
    library_path = repo_root / "podcast-transformer" / "references" / "terminology_library.json"
    if not library_path.exists():
        print(f"ERROR: terminology library not found at {library_path}", file=sys.stderr)
        print("  Run scripts/build_terminology_library.py first.", file=sys.stderr)
        return 2
    library: dict[str, dict] = json.loads(library_path.read_text(encoding="utf-8"))

    sidecar_path = Path(args.sidecar)
    sc = load_json(sidecar_path)
    terminology = sc.setdefault("verification", {}).setdefault("terminology", [])
    by_name = {e.get("term"): e for e in terminology if e.get("term")}

    requested = args.term or [e.get("term") for e in terminology if e.get("term")]
    added, enriched, missing = [], [], []

    for term in requested:
        if not term:
            continue
        lib_entry = library.get(term)
        if not lib_entry:
            missing.append(term)
            continue
        existing = by_name.get(term)
        if existing is None:
            terminology.append({
                "term": term,
                "category": lib_entry["category"],
                "confidence": lib_entry.get("confidence", "primary"),
                "notes": lib_entry["notes"],
                "url": lib_entry["url"],
            })
            added.append(term)
        else:
            changed = False
            if not existing.get("notes") and lib_entry["notes"]:
                existing["notes"] = lib_entry["notes"]; changed = True
            if not existing.get("url") and lib_entry["url"]:
                existing["url"] = lib_entry["url"]; changed = True
            if not existing.get("category") and lib_entry["category"]:
                existing["category"] = lib_entry["category"]; changed = True
            if changed:
                enriched.append(term)

    sc["updated_at"] = utc_now()
    write_json(sidecar_path, sc)
    print(f"Added {len(added)} new entries: {added}")
    print(f"Enriched {len(enriched)} existing entries: {enriched}")
    if missing:
        print(f"Not in library ({len(missing)}): {missing}", file=sys.stderr)
        print("  → contribute these to the library by running build_terminology_library.py after they're filled in.", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create a new metadata sidecar")
    init.add_argument("--output", required=True)
    init.add_argument("--title")
    init.add_argument("--podcast-title")
    init.add_argument("--episode-number", help="episode number/identifier rendered in the briefing kicker (e.g. 863, S03E12)")
    init.add_argument("--description")
    init.add_argument("--published-at")
    init.add_argument("--duration-seconds", type=int, help="estimate when no audio is available; otherwise leave unset and update later from ffprobe")
    init.add_argument("--language")
    init.add_argument("--host", action="append")
    init.add_argument("--guest", action="append", help="repeatable; the FIRST guest is the headline guest shown in 'About the guest'")
    init.add_argument("--media", action="append")
    init.add_argument("--existing-transcript", action="append")
    init.add_argument("--episode-url", action="append")
    init.add_argument("--companion-url", action="append")
    init.add_argument("--rss-url", action="append")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    validate = subparsers.add_parser("validate", help="validate a sidecar")
    validate.add_argument("sidecar")
    validate.add_argument("--strict", action="store_true", help="treat warnings as failures")
    validate.set_defaults(func=cmd_validate)

    add_source = subparsers.add_parser("add-source", help="append a verification source")
    add_source.add_argument("sidecar")
    add_source.add_argument("--url")
    add_source.add_argument("--path")
    add_source.add_argument("--title")
    add_source.add_argument("--accessed-at")
    add_source.add_argument("--used-for", action="append")
    add_source.set_defaults(func=cmd_add_source)

    stamp = subparsers.add_parser("stamp-verified", help="mark a sidecar verified")
    stamp.add_argument("sidecar")
    stamp.add_argument("--transcript")
    stamp.add_argument("--verified-by")
    stamp.add_argument("--verified-at")
    stamp.set_defaults(func=cmd_stamp_verified)

    enrich = subparsers.add_parser("enrich", help="fill in terminology entries from references/terminology_library.json")
    enrich.add_argument("sidecar")
    enrich.add_argument("--term", action="append", help="explicit term to add from the library; repeatable. If omitted, enriches every term already present in the sidecar.")
    enrich.set_defaults(func=cmd_enrich)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
