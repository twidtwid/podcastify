"""Pipeline configuration — three knobs, sensible defaults.

The pipeline uses local Ollama models in two roles:

- `PODCAST_DRAFT_MODEL`   — bulk content generation. Used by `draft_notes.py`
                             and `populate_terminology.py`. Default: a fast
                             small model that handles long transcripts.
- `PODCAST_SHARPEN_MODEL` — editorial polish, per-item rewrites and
                             categorization. Used by `sharpen_notes.py` and
                             `enrich_terminology.py`. Default: a larger model
                             with `think=False` for short, sharp outputs.

And one endpoint:

- `PODCAST_OLLAMA_URL`    — the Ollama HTTP endpoint. Point at a remote
                             Ollama on your LAN if you don't want the heavy
                             models locally.

Override via env vars. Context-window numbers and the paid-API fallback
model name are plain Python constants below — edit if you swap models.

Precedence: CLI flag > env var > constant default.
"""
from __future__ import annotations

import os


def _env_str(key: str, default: str) -> str:
    return (os.environ.get(key) or "").strip() or default


# ── User-facing knobs (env-overridable) ───────────────────────────────
OLLAMA_URL: str = _env_str("PODCAST_OLLAMA_URL", "http://localhost:11434/api/chat")
DRAFT_MODEL: str = _env_str("PODCAST_DRAFT_MODEL", "gemma4:e4b-nvfp4")
SHARPEN_MODEL: str = _env_str("PODCAST_SHARPEN_MODEL", "qwen3.6:35B-a3b-nvfp4")


# ── Sub-stages share models with their parent role ─────────────────────
TERMINOLOGY_MODEL: str = DRAFT_MODEL       # bulk enumeration
ENRICH_MODEL: str = SHARPEN_MODEL          # per-item categorization


# ── Context windows — tuned to the default models. Edit if you swap. ──
DRAFT_NUM_CTX: int = 65536
TERMINOLOGY_NUM_CTX: int = 65536
SHARPEN_NUM_CTX: int = 8192
ENRICH_NUM_CTX: int = 4096   # ~700-char window per entry — small ctx is plenty


# ── Paid Anthropic API fallback (only used with --draft-backend api) ──
DRAFT_API_MODEL: str = "claude-haiku-4-5"


def print_config() -> None:
    print("podcast-transformer pipeline configuration")
    print(f"  PODCAST_OLLAMA_URL    = {OLLAMA_URL}")
    print(f"  PODCAST_DRAFT_MODEL   = {DRAFT_MODEL}        (used by draft + terminology)")
    print(f"  PODCAST_SHARPEN_MODEL = {SHARPEN_MODEL}  (used by sharpen + enrich)")


if __name__ == "__main__":
    print_config()
