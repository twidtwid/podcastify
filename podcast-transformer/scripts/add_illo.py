#!/usr/bin/env python3
"""Embed an illo illustration into a podcast briefing (podcast-at-a-glance.html).

Idempotent: re-running replaces the prior illo figure (markers are stamped in),
so it is safe to run after every pipeline re-render. Self-contained output —
the image is compressed to JPEG and inlined as a data URI, so the briefing
stays portable (publishes to report-portal / Funnel with no external asset).

Place the figure right after the "thesis" card in the briefing's main column.

USAGE
  # generate the illustration with the illo skill (free Codex backend):
  add_illo.py <episode-dir> --prompt-file scene.txt --caption "…"
  add_illo.py <episode-dir> --prompt "a riso scene of …" --caption "…"

  # or inject an image you already rendered:
  add_illo.py <episode-dir> --image /tmp/scene.png --caption "…"

NOTES
  - Pick a character that fits the EPISODE. Blot (illo's default ink-drop
    mascot) is the safe neutral pick for business / tech / general episodes.
    Topic packs (mole=going-deep, boss=leadership, …) when they fit. Cadence
    is the fitness/health coach — only for fitness/wellness episodes.
  - The illo skill triggers only on explicit illo intent; this script calls its
    engine directly (illo.py generate), which is fine.
"""
import argparse, base64, html, pathlib, subprocess, sys, tempfile

ILLO = pathlib.Path.home() / ".claude/skills/illo/scripts/illo.py"
START = "<!--ILLO-HERO-START-->"
END = "<!--ILLO-HERO-END-->"


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{r.stderr or r.stdout}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description="Embed an illo illustration into a podcast briefing.")
    ap.add_argument("episode_dir", help="podcast-output/<slug>  (or its final/ dir)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", help="use an existing rendered image (png/jpg)")
    src.add_argument("--prompt", help="generate via illo from this prompt string")
    src.add_argument("--prompt-file", help="generate via illo from this prompt file")
    ap.add_argument("--caption", default="", help="figcaption text (HTML-escaped)")
    ap.add_argument("--credit", default="illo", help="small muted credit tag (default: illo)")
    ap.add_argument("--target", default="podcast-at-a-glance.html", help="briefing filename inside final/")
    ap.add_argument("--aspect", default="16:9", help="illo aspect when generating (default 16:9)")
    ap.add_argument("--ref", help="illo --ref model sheet (locks a custom character)")
    ap.add_argument("--max-width", type=int, default=1400)
    ap.add_argument("--quality", type=int, default=82)
    args = ap.parse_args()

    ep = pathlib.Path(args.episode_dir).expanduser()
    final = ep if ep.name == "final" else ep / "final"
    target = final / args.target
    if not target.is_file():
        sys.exit(f"briefing not found: {target}")

    # 1. obtain the source image
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="illo_"))
    if args.image:
        raw = pathlib.Path(args.image).expanduser()
        if not raw.is_file():
            sys.exit(f"image not found: {raw}")
    else:
        raw = tmp / "illo.png"
        cmd = [sys.executable, str(ILLO), "generate", "--backend", "codex",
               "--aspect", args.aspect, "--out", str(raw)]
        if args.ref:
            cmd += ["--ref", args.ref]
        if args.prompt_file:
            cmd += ["--prompt-file", args.prompt_file]
        else:
            cmd += ["--prompt", args.prompt]
        print("rendering illo (codex backend)…", file=sys.stderr)
        sh(cmd)
        if not raw.is_file():
            sys.exit("illo did not produce an image")

    # 2. compress to a web-sized JPEG (sips, macOS)
    jpg = tmp / "hero.jpg"
    sh(["sips", "-Z", str(args.max_width), "-s", "format", "jpeg",
        "-s", "formatOptions", str(args.quality), str(raw), "--out", str(jpg)])
    b64 = base64.b64encode(jpg.read_bytes()).decode()

    cap = html.escape(args.caption, quote=True)
    credit = html.escape(args.credit, quote=True)
    figure = (
        f'{START}\n'
        f'            <figure class="card illo-hero" style="margin:0;padding:0;overflow:hidden">\n'
        f'              <img src="data:image/jpeg;base64,{b64}" alt="{cap}" '
        f'style="display:block;width:100%;height:auto">\n'
        + (f'              <figcaption style="font-size:.8rem;color:var(--muted);'
           f'padding:.5rem .85rem;border-top:1px solid var(--rule)">{cap} '
           f'<span style="opacity:.7">{credit}</span></figcaption>\n' if cap else "")
        + f'            </figure>\n'
        f'            {END}'
    )

    htmltext = target.read_text()

    # 3a. idempotent: replace a prior stamped figure
    if START in htmltext and END in htmltext:
        pre = htmltext[: htmltext.index(START)]
        post = htmltext[htmltext.index(END) + len(END):]
        htmltext = pre + figure + post
    else:
        # 3b. first insert — anchor right after the thesis card.
        anchor = "</blockquote>\n            </section>"
        if anchor not in htmltext:
            sys.exit("could not find the thesis-card anchor to insert after; "
                     "the briefing template may have changed.")
        htmltext = htmltext.replace(anchor, anchor + "\n\n            " + figure, 1)

    target.write_text(htmltext)
    print(f"embedded illo into {target} ({target.stat().st_size/1024:.0f}KB)")


if __name__ == "__main__":
    main()
