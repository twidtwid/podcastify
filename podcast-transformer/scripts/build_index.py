#!/usr/bin/env python3
"""Build a library index page listing every episode under podcast-output/.

Scans `podcast-output/<slug>/final/episode.package.json` for each episode,
emits `podcast-output/index.html` with one entry per episode (kicker, title,
bottom-line, links to briefing and transcript).

Auto-invoked from `podcast_build.py` after a build; safe to run standalone:

    python3 scripts/build_index.py <library_root>
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = SKILL_ROOT / "assets" / "podcast-html"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def discover_episodes(library_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for package_path in sorted(library_root.glob("*/final/episode.package.json")):
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"WARN: skipping {package_path}: {exc}", file=sys.stderr)
            continue
        slug = package_path.parents[1].name
        episode = package.get("episode", {})
        entries.append({
            "slug": slug,
            "title": episode.get("short_title") or episode.get("title") or slug,
            "full_title": episode.get("title") or "",
            "podcast_title": episode.get("podcast_title") or "",
            "episode_number": episode.get("episode_number") or "",
            "published_at": episode.get("published_at") or "",
            "duration_seconds": int(episode.get("duration_seconds") or 0),
            "hosts": episode.get("hosts") or [],
            "guests": episode.get("guests") or [],
            "bottom_line": package.get("bottom_line") or episode.get("description") or "",
            "generated_at": package.get("generated_at") or "",
        })
    # Newest first by published_at if available, otherwise by generated_at.
    entries.sort(key=lambda e: (e["published_at"] or e["generated_at"] or ""), reverse=True)
    return entries


def fmt_date(value: str) -> str:
    if not value:
        return ""
    # Try YYYY-MM-DD first
    try:
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            d = datetime.strptime(value, "%Y-%m-%d")
        else:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.strftime("%B %-d, %Y")
    except Exception:
        return value


def fmt_duration(seconds: int) -> str:
    if not seconds:
        return ""
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"


def render_entries(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return '<p class="empty">No episodes published yet. Run <code>podcast_build.py all &lt;episode-dir&gt;</code> to add one.</p>'
    parts = []
    for e in entries:
        ep_num = f"#{html.escape(e['episode_number'])}" if e['episode_number'] else ""
        meta_bits = [b for b in [
            html.escape(e["podcast_title"]),
            ep_num,
            fmt_date(e["published_at"]),
            fmt_duration(e["duration_seconds"]),
        ] if b]
        meta_line = " · ".join(meta_bits)
        byline_parts = [b for b in [
            ", ".join(html.escape(h) for h in e["hosts"]),
            ", ".join(html.escape(g) for g in e["guests"][:2]) + ("…" if len(e["guests"]) > 2 else ""),
        ] if b]
        byline = " with ".join(p for p in byline_parts if p)
        parts.append(f"""
        <article class="lib-entry">
          <p class="kicker">{meta_line}</p>
          <h2 class="lib-title"><a href="{html.escape(e['slug'])}/final/podcast-at-a-glance.html">{html.escape(e['title'])}</a></h2>
          {f'<p class="meta">{html.escape(byline)}</p>' if byline else ''}
          {f'<p class="lib-lede">{html.escape(e["bottom_line"])}</p>' if e['bottom_line'] else ''}
          <p class="lib-links">
            <a href="{html.escape(e['slug'])}/final/podcast-at-a-glance.html">Briefing →</a>
            <a href="{html.escape(e['slug'])}/final/annotated-transcript.html">Transcript →</a>
          </p>
        </article>""")
    return "\n".join(parts)


INDEX_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Podcast library</title>
<style>{{CSS}}

/* Library-index specific styles */
main.library { max-width: var(--measure); margin: 0 auto; padding: 3rem 1.5rem 6rem; }
.library-header { margin-bottom: 2.5rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--rule); }
.library-header h1 { font-family: var(--font-display); font-size: 2.2rem; font-weight: 700; margin: 0 0 0.4em; letter-spacing: -0.02em; }
.library-header .meta { margin: 0; }
.lib-entry { padding: 2rem 0; border-bottom: 1px solid var(--rule-soft); }
.lib-entry:last-of-type { border-bottom: none; }
.lib-entry .kicker { margin-bottom: 0.4em; }
.lib-title { font-family: var(--font-display); font-size: 1.6rem; font-weight: 700; line-height: 1.2; letter-spacing: -0.015em; text-transform: none; color: var(--ink); margin: 0 0 0.4em; }
.lib-title a { color: var(--ink); border-bottom: none; }
.lib-title a:hover { color: var(--accent); }
.lib-lede { font-family: var(--font-serif); font-size: 1.05rem; line-height: 1.5; color: var(--ink-soft); font-style: italic; margin: 0.6rem 0 0; max-width: var(--measure-prose); }
.lib-links { font-family: var(--font-sans); font-size: 0.9rem; margin: 1rem 0 0; display: flex; gap: 1.5rem; }
.lib-links a { color: var(--accent); border-bottom: none; font-weight: 600; }
.lib-links a:hover { color: var(--ink); }
.empty { font-family: var(--font-sans); color: var(--muted); font-style: italic; }
.empty code { background: var(--paper-tint); padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.9em; }
footer.lib-colophon { margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--rule); font-family: var(--font-sans); font-size: 0.8rem; color: var(--muted); display: flex; justify-content: space-between; flex-wrap: wrap; gap: 1rem; }

@media (max-width: 720px) {
  main.library { padding: 2rem 1rem 4rem; }
  .library-header h1 { font-size: 1.7rem; }
  .lib-title { font-size: 1.35rem; }
  .lib-entry { padding: 1.5rem 0; }
  .lib-links { gap: 1.25rem; }
  .lib-links a { padding: 0.35rem 0; }
}
</style>
</head>
<body class="glance-app">
<main class="library">
  <header class="library-header">
    <h1>Podcast library</h1>
    <p class="meta">{{COUNT}} episode{{PLURAL}} · generated {{GENERATED_AT}}</p>
  </header>
  {{ENTRIES}}
  <footer class="lib-colophon">
    <span>podcast-transformer · library index</span>
    <span>Updated {{GENERATED_AT}}</span>
  </footer>
</main>
</body>
</html>
"""


def build_index(library_root: Path) -> Path:
    entries = discover_episodes(library_root)
    css = (ASSET_DIR / "artifact.css").read_text(encoding="utf-8")
    substitutions = {
        "CSS": css,
        "COUNT": str(len(entries)),
        "PLURAL": "" if len(entries) == 1 else "s",
        "GENERATED_AT": utc_now(),
        "ENTRIES": render_entries(entries),
    }
    # Single pass over the original template so rendered entry content (derived
    # from scraped episode metadata) can never re-expand a later token.
    html_doc = re.sub(
        r"\{\{(CSS|COUNT|PLURAL|GENERATED_AT|ENTRIES)\}\}",
        lambda m: substitutions[m.group(1)],
        INDEX_TEMPLATE,
    )
    out = library_root / "index.html"
    out.write_text(html_doc, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library_root", type=Path, help="The podcast-output/ directory")
    args = parser.parse_args(argv)
    root = args.library_root.resolve()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2
    out = build_index(root)
    n = len(discover_episodes(root))
    print(f"Wrote {out} ({n} episode{'' if n == 1 else 's'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
