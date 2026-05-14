from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "podcast-transformer" / "scripts"
RESOLVE = SCRIPTS_DIR / "resolve_speakers.py"


def load_resolver():
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("resolve_speakers", RESOLVE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {RESOLVE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _stage_episode(tmp: str, transcript: str = "Lenny Rachitsky: Welcome.\nEric Ries: Thanks for having me.\n") -> Path:
    ep = Path(tmp)
    (ep / "source").mkdir(parents=True, exist_ok=True)
    (ep / "working").mkdir(parents=True, exist_ok=True)
    (ep / "source" / "user-provided-transcript.txt").write_text(transcript, encoding="utf-8")
    return ep


class ResolveSpeakersFlowTests(unittest.TestCase):
    """End-to-end behavior with a mocked Ollama response."""

    def test_writes_resolved_host_and_guests_to_working(self) -> None:
        resolver = load_resolver()
        with tempfile.TemporaryDirectory() as tmp:
            ep = _stage_episode(tmp)
            model_json = json.dumps({"host": "Lenny Rachitsky", "guests": ["Eric Ries"]})
            with mock.patch.object(resolver, "call_ollama_json", return_value=model_json):
                rc = resolver.main([
                    str(ep),
                    "--title", "How to build a company that withstands any era",
                    "--podcast-title", "Lenny's Podcast: Product | Career | Growth",
                    "--episode-url", "https://example.com/p/foo",
                ])
            self.assertEqual(rc, 0)
            written = json.loads((ep / "working" / "_speakers.json").read_text(encoding="utf-8"))
            self.assertEqual(written, {"host": "Lenny Rachitsky", "guests": ["Eric Ries"]})

    def test_invalid_json_from_model_yields_empty_result(self) -> None:
        # The downstream pipeline must always find a parseable _speakers.json,
        # even when the model emits prose or a truncated payload.
        resolver = load_resolver()
        with tempfile.TemporaryDirectory() as tmp:
            ep = _stage_episode(tmp)
            with mock.patch.object(resolver, "call_ollama_json", return_value="not json at all"):
                rc = resolver.main([str(ep), "--title", "x"])
            self.assertEqual(rc, 0)
            written = json.loads((ep / "working" / "_speakers.json").read_text(encoding="utf-8"))
            self.assertEqual(written, {"host": "", "guests": []})
            # Raw output is preserved for debugging.
            self.assertTrue((ep / "working" / "_speakers_raw.txt").is_file())

    def test_ollama_call_failure_yields_empty_result(self) -> None:
        # A network/HTTP failure must not crash the pipeline.
        import urllib.error
        resolver = load_resolver()
        with tempfile.TemporaryDirectory() as tmp:
            ep = _stage_episode(tmp)
            with mock.patch.object(
                resolver,
                "call_ollama_json",
                side_effect=urllib.error.URLError("connection refused"),
            ):
                rc = resolver.main([str(ep), "--title", "x"])
            self.assertEqual(rc, 0)
            written = json.loads((ep / "working" / "_speakers.json").read_text(encoding="utf-8"))
            self.assertEqual(written, {"host": "", "guests": []})

    def test_missing_transcript_yields_empty_without_calling_model(self) -> None:
        # If the transcript doesn't exist yet (caller misordered the pipeline),
        # write an empty result rather than calling the model on nothing.
        resolver = load_resolver()
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp)
            (ep / "working").mkdir(parents=True, exist_ok=True)
            with mock.patch.object(resolver, "call_ollama_json") as patched_call:
                rc = resolver.main([str(ep), "--title", "x"])
            self.assertEqual(rc, 0)
            patched_call.assert_not_called()
            written = json.loads((ep / "working" / "_speakers.json").read_text(encoding="utf-8"))
            self.assertEqual(written, {"host": "", "guests": []})

    def test_guest_list_strings_are_stripped_and_filtered(self) -> None:
        resolver = load_resolver()
        with tempfile.TemporaryDirectory() as tmp:
            ep = _stage_episode(tmp)
            # Defensive coverage: model returns whitespace / empty entries.
            payload = json.dumps({"host": "  Lenny Rachitsky  ", "guests": [" Eric Ries ", "", "  ", "Cat Wu"]})
            with mock.patch.object(resolver, "call_ollama_json", return_value=payload):
                resolver.main([str(ep), "--title", "x"])
            written = json.loads((ep / "working" / "_speakers.json").read_text(encoding="utf-8"))
            self.assertEqual(written["host"], "Lenny Rachitsky")
            self.assertEqual(written["guests"], ["Eric Ries", "Cat Wu"])


if __name__ == "__main__":
    unittest.main()
