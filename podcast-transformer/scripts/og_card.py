#!/usr/bin/env python3
"""Generate a 1200x630 social-preview card (og-card.png) for an episode.

Editorial card matching the artifact design system (paper/ink/accent, Charter
-> Georgia serif). Importable as generate_og_card(package, out_path) and runnable
as `python og_card.py <episode_dir>`.

Pillow is required. macOS ships the Georgia/Helvetica fonts this uses; on other
hosts the loader falls back through a candidate list, then to Pillow's default.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

# Design-system palette (mirrors assets/podcast-html/artifact.css :root).
PAPER = (250, 246, 239)
INK = (26, 24, 21)
INK_SOFT = (74, 68, 60)
MUTED = (138, 131, 120)
ACCENT = (138, 58, 26)
RULE = (217, 209, 194)

W, H = 1200, 630
MARGIN_X = 96
CONTENT_W = W - 2 * MARGIN_X

# Font candidates, most-preferred first. Charter (the CSS choice) is not a
# system TTF; Georgia is its documented fallback and ships on macOS.
SERIF_BOLD = [
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "/Library/Fonts/Georgia Bold.ttf",
]
SERIF_REG = [
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/Library/Fonts/Georgia.ttf",
]
SANS_REG = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> float:
    return draw.textlength(text, font=font)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if _text_w(draw, trial, font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit_title(
    draw: ImageDraw.ImageDraw, text: str, max_w: int, max_lines: int = 4
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    for size in range(80, 46, -4):
        font = _font(SERIF_BOLD, size)
        lines = _wrap(draw, text, font, max_w)
        if len(lines) <= max_lines:
            return font, lines
    font = _font(SERIF_BOLD, 48)
    lines = _wrap(draw, text, font, max_w)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;: ") + "…"
    return font, lines


def _draw_tracked(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    tracking: float,
) -> None:
    x, y = pos
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += _text_w(draw, ch, font) + tracking


def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    # Defensive hard cap: far more than can ever fit one line, but bounds the
    # string handed to Pillow's text measurement regardless of caller (Pillow
    # raises on >1M-char strings; never measure a pathological input).
    if len(text) > 2000:
        text = text[:2000]
    if _text_w(draw, text, font) <= max_w:
        return text
    # Binary search the longest prefix that fits, O(log n) measurements
    # instead of one measurement per dropped character (O(n^2) on long input).
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_w(draw, text[:mid] + "…", font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


def _fmt_duration(seconds: Any) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    h, m = divmod(total // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def generate_og_card(package: dict[str, Any], out_path: Path, public_base: str = "") -> Path:
    ep = package.get("episode", {})
    if not isinstance(ep, dict):
        ep = {}
    # Coerce to str and clamp before any text measurement: these fields come
    # from scraped/untrusted metadata. Without a length bound a pathological
    # title/guest list drives the wrapping/measuring loops into a long CPU
    # spin (the caller's best-effort try/except catches exceptions, not hangs).
    short_title = (str(ep.get("short_title") or ep.get("title") or "Untitled episode")).strip()[:300]
    podcast_title = (str(ep.get("podcast_title") or "")).strip()[:200]
    guests = [str(g).strip()[:120] for g in (ep.get("guests") or []) if str(g).strip()][:10]
    hosts = [str(h).strip()[:120] for h in (ep.get("hosts") or []) if str(h).strip()][:10]
    duration = _fmt_duration(ep.get("duration_seconds"))

    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    # Top accent band.
    draw.rectangle([(0, 0), (W, 12)], fill=ACCENT)

    x0, x1 = MARGIN_X, W - MARGIN_X

    # Kicker: podcast title, uppercased + tracked.
    kicker_font = _font(SANS_REG, 24)
    kicker = _truncate(draw, podcast_title.upper(), kicker_font, CONTENT_W)
    _draw_tracked(draw, (x0, 104), kicker, kicker_font, ACCENT, 3.0)

    # Title block.
    title_font, title_lines = _fit_title(draw, short_title, CONTENT_W)
    # ImageFont.load_default() (the no-font fallback on a host missing every
    # candidate) has no .size on older Pillow — degrade instead of crashing.
    title_size = getattr(title_font, "size", 0) or 64
    line_h = int(title_size * 1.14)
    y = 168
    for line in title_lines:
        draw.text((x0, y), line, font=title_font, fill=INK)
        y += line_h

    # Rule under title.
    rule_y = y + 34
    draw.line([(x0, rule_y), (x1, rule_y)], fill=RULE, width=2)

    # Guests.
    if guests:
        guest_font = _font(SERIF_REG, 36)
        guest_line = _truncate(draw, "with " + ", ".join(guests), guest_font, CONTENT_W)
        draw.text((x0, rule_y + 30), guest_line, font=guest_font, fill=INK_SOFT)

    # Footer: host - duration (left), domain (right).
    foot_font = _font(SANS_REG, 23)
    draw.line([(x0, 560), (x1, 560)], fill=RULE, width=2)
    left_bits = [b for b in [", ".join(hosts), duration] if b]
    if left_bits:
        draw.text((x0, 580), "  ·  ".join(left_bits), font=foot_font, fill=MUTED)
    domain = urlparse(public_base).netloc or public_base.replace("https://", "").split("/")[0]
    if domain:
        dw = _text_w(draw, domain, foot_font)
        draw.text((x1 - dw, 580), domain, font=foot_font, fill=MUTED)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: og_card.py <episode_dir> [public_base_url]", file=sys.stderr)
        return 2
    episode_dir = Path(args[0]).resolve()
    public_base = args[1] if len(args) > 1 else ""
    package_path = episode_dir / "final" / "episode.package.json"
    if not package_path.exists():
        print(f"missing {package_path}", file=sys.stderr)
        return 1
    package = json.loads(package_path.read_text(encoding="utf-8"))
    out = generate_og_card(package, episode_dir / "final" / "og-card.png", public_base)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
