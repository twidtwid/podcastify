from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "podcast-transformer" / "scripts"
PODCAST_BUILD = SCRIPTS_DIR / "podcast_build.py"
ARTIFACT_JS = REPO_ROOT / "podcast-transformer" / "assets" / "podcast-html" / "artifact.js"


def load_podcast_build():
    spec = importlib.util.spec_from_file_location("podcast_build", PODCAST_BUILD)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {PODCAST_BUILD}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SpeakerVisibilityTests(unittest.TestCase):
    def _write_minimal_sidecar(self, ep: Path) -> None:
        (ep / "source").mkdir(parents=True, exist_ok=True)
        (ep / "final").mkdir(parents=True, exist_ok=True)
        (ep / "source" / "episode.notes.json").write_text("{}\n", encoding="utf-8")
        sidecar = {
            "episode": {
                "title": "How to build a company",
                "podcast_title": "Lenny's Podcast",
                "hosts": ["Lenny Rachitsky"],
                "guests": ["Eric Ries"],
                "duration_seconds": 300,
                "chapters": [],
            },
            "verification": {"terminology": []},
        }
        (ep / "final" / "metadata.sidecar.json").write_text(
            json.dumps(sidecar),
            encoding="utf-8",
        )

    def test_package_marks_speaker_label_only_on_speaker_change(self) -> None:
        podcast_build = load_podcast_build()
        transcript = (
            "Lenny Rachitsky: Eric, welcome.\n"
            "Lenny Rachitsky: I want to start with the long arc.\n"
            "Eric Ries: Thanks for having me.\n"
            "Eric Ries: The first thing I learned was patience.\n"
            "Lenny Rachitsky: That connects to durable companies.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp)
            self._write_minimal_sidecar(ep)
            (ep / "source" / "user-provided-transcript.txt").write_text(
                transcript,
                encoding="utf-8",
            )

            package = podcast_build.build_package(ep)

        self.assertEqual(
            [(t["speaker"], t["show_speaker"]) for t in package["turns"]],
            [
                ("Lenny Rachitsky", True),
                ("Lenny Rachitsky", False),
                ("Eric Ries", True),
                ("Eric Ries", False),
                ("Lenny Rachitsky", True),
            ],
        )

    def test_package_prefers_structured_turns_over_text_regex_parsing(self) -> None:
        podcast_build = load_podcast_build()
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp)
            self._write_minimal_sidecar(ep)
            (ep / "source" / "user-provided-transcript.txt").write_text(
                "Reference Title: This line looks like a speaker but is not dialogue.\n"
                "Lenny Rachitsky: Text fallback should not be used.\n",
                encoding="utf-8",
            )
            (ep / "source" / "transcript.turns.json").write_text(
                json.dumps(
                    [
                        {"speaker": "Lenny Rachitsky", "text": "Eric, welcome."},
                        {"speaker": "Eric Ries", "text": "Thanks for having me."},
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            package = podcast_build.build_package(ep)

        self.assertEqual(
            [(t["speaker"], t["text"]) for t in package["turns"]],
            [
                ("Lenny Rachitsky", "Eric, welcome."),
                ("Eric Ries", "Thanks for having me."),
            ],
        )

    def test_transcript_renderer_suppresses_repeated_speaker_labels(self) -> None:
        # The rendered browser is client-side JS; guard that the shared
        # renderer honors the package field produced above.
        js = ARTIFACT_JS.read_text(encoding="utf-8")
        self.assertIn("turn.show_speaker === false", js)
        self.assertIn('class="speaker"', js)


if __name__ == "__main__":
    unittest.main()
