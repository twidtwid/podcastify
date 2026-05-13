#!/usr/bin/env python3
"""One-shot HTTP catcher to pull DOM text from a Chrome MCP page into a file.

Replaces the Blob+download pattern (see references/transcript-verification.md).
Blob downloads fail silently across five known modes: Spotlight indexing race,
variable per-user download dirs, Chrome auto-download rate-limit blocking,
filename collisions, and no success signal. Posting directly to a localhost
catcher fixes all five — synchronous confirmation, caller-specified path, no
download dialog.

Usage:
  scripts/dom_text_catcher.py --output PATH [--port 0] [--timeout 120]

Prints one JSON line to stdout with {port, js_snippet, output} so the caller
(typically a Claude session driving Chrome MCP) knows exactly which fetch to
inject. After receiving ONE POST, writes the body to --output, prints
`ok <bytes> -> <path>` to stderr, exits 0. On timeout exits 2.

Modern Chrome (>= 94) treats http://localhost as a secure context, so a page
loaded over HTTPS may fetch this catcher without mixed-content blocking. The
request uses Content-Type: text/plain to stay a CORS "simple request" and skip
the preflight OPTIONS round trip. The catcher still handles OPTIONS for
defense in depth.
"""

from __future__ import annotations

import argparse
import http.server
import json
import socket
import sys
import threading
from pathlib import Path


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_handler(output: Path, done: threading.Event, state: dict):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # Private Network Access (Chrome >= 117): public HTTPS pages fetching
            # http://localhost need this on the preflight (and the response) or
            # the request hangs until the renderer times out.
            self.send_header("Access-Control-Allow-Private-Network", "true")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(length) if length else b""
            output.write_bytes(data)
            state["bytes"] = len(data)
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
            done.set()

        def log_message(self, *args, **kwargs) -> None:  # noqa: ARG002
            return  # quiet

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", required=True, type=Path, help="Destination file path.")
    ap.add_argument("--port", type=int, default=0, help="Catcher port (0 = OS picks free port).")
    ap.add_argument("--timeout", type=float, default=120.0, help="Seconds to wait for POST.")
    ap.add_argument(
        "--selector",
        default='[class*="transcription-full-body-container"]',
        help="CSS selector embedded in the printed JS snippet (default: Substack transcript).",
    )
    args = ap.parse_args(argv)

    port = args.port or find_free_port()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    done = threading.Event()
    state: dict = {}

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), make_handler(output, done, state))
    except OSError as e:
        print(f"ERROR: cannot bind 127.0.0.1:{port} ({e})", file=sys.stderr)
        return 2

    selector_js = json.dumps(args.selector)
    js_snippet = (
        f"fetch('http://localhost:{port}',"
        f"{{method:'POST',headers:{{'Content-Type':'text/plain'}},"
        f"body:document.querySelector({selector_js}).innerText}})"
        f".then(r=>r.text())"
    )
    print(json.dumps({"port": port, "js_snippet": js_snippet, "output": str(output)}))
    sys.stdout.flush()

    threading.Thread(target=server.serve_forever, daemon=True).start()

    if not done.wait(timeout=args.timeout):
        print(f"ERROR: no POST received within {args.timeout}s on port {port}", file=sys.stderr)
        server.shutdown()
        return 2

    server.shutdown()
    print(f"ok {state.get('bytes', 0)} bytes -> {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
