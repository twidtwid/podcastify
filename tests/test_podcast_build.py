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

    def test_malformed_structured_turns_fall_back_to_text_transcript(self) -> None:
        podcast_build = load_podcast_build()
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp)
            self._write_minimal_sidecar(ep)
            (ep / "source" / "user-provided-transcript.txt").write_text(
                "Lenny Rachitsky: Text fallback should be used.\n"
                "Eric Ries: The structured turns file is corrupt.\n",
                encoding="utf-8",
            )
            (ep / "source" / "transcript.turns.json").write_text(
                "{not valid json",
                encoding="utf-8",
            )

            package = podcast_build.build_package(ep)

        self.assertEqual(
            [(t["speaker"], t["text"]) for t in package["turns"]],
            [
                ("Lenny Rachitsky", "Text fallback should be used."),
                ("Eric Ries", "The structured turns file is corrupt."),
            ],
        )

    def test_sync_uncertain_spans_merges_with_existing_manual_spans(self) -> None:
        podcast_build = load_podcast_build()
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp)
            self._write_minimal_sidecar(ep)
            sidecar_path = ep / "final" / "metadata.sidecar.json"
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            sidecar["verification"]["uncertain_spans"] = [
                {
                    "timestamp": "00:01:00",
                    "speaker": "Lenny Rachitsky",
                    "text": "[unclear company name]",
                    "reason": "Manual review note.",
                    "resolution_needed": "Check audio.",
                }
            ]
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
            package = {
                "turns": [
                    {
                        "speaker": "Lenny Rachitsky",
                        "text": "This already has [unclear company name].",
                    },
                    {
                        "speaker": "Eric Ries",
                        "text": "This adds [inaudible 00:03:14] from the transcript.",
                    },
                ]
            }

            podcast_build.sync_uncertain_spans(ep, package)

            updated = json.loads(sidecar_path.read_text(encoding="utf-8"))
        spans = updated["verification"]["uncertain_spans"]
        self.assertEqual([span["text"] for span in spans], ["[unclear company name]", "[inaudible 00:03:14]"])

    def test_transcript_renderer_suppresses_repeated_speaker_labels(self) -> None:
        # The rendered browser is client-side JS; guard that the shared
        # renderer honors the package field produced above.
        js = ARTIFACT_JS.read_text(encoding="utf-8")
        self.assertIn("turn.show_speaker === false", js)
        self.assertIn('class="speaker"', js)


class TemplateHardeningTests(unittest.TestCase):
    def test_render_template_escapes_title(self) -> None:
        # short_title is scraped/untrusted and lands in <title>; a crafted
        # value must not break out into executable markup.
        podcast_build = load_podcast_build()
        evil = "</title><script>alert(document.cookie)</script>"
        out = podcast_build.render_template(
            "transcript-browser.html.tmpl", {"episode": {}}, evil
        )
        self.assertNotIn("<script>alert(document.cookie)</script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_render_template_single_pass_no_token_reexpansion(self) -> None:
        # A title containing a literal later token must NOT be expanded by a
        # subsequent substitution pass (no content injection / duplication).
        podcast_build = load_podcast_build()
        package = {"episode": {"marker": "UNIQUE_DATA_MARKER"}}
        out = podcast_build.render_template(
            "transcript-browser.html.tmpl", package, "{{DATA_JSON}}"
        )
        # The real {{DATA_JSON}} slot expands exactly once; the literal
        # "{{DATA_JSON}}" carried in the title is not re-expanded.
        self.assertEqual(out.count("UNIQUE_DATA_MARKER"), 1)
        self.assertIn("{{DATA_JSON}}", out)

    def test_write_json_is_atomic_and_leaves_no_tmp(self) -> None:
        podcast_build = load_podcast_build()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "out.json"
            podcast_build.write_json(target, {"a": 1})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1})
            self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_resolve_transcript_path_warns_on_derived_output_fallback(self) -> None:
        import io
        import contextlib

        podcast_build = load_podcast_build()
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp)
            (ep / "final").mkdir(parents=True, exist_ok=True)
            (ep / "final" / "transcript.verified.md").write_text("x", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                resolved = podcast_build.resolve_transcript_path(ep, {})
            self.assertEqual(resolved, ep / "final" / "transcript.verified.md")
            self.assertIn("WARN", stderr.getvalue())
            self.assertIn("prior output", stderr.getvalue())


class JsonExportTests(unittest.TestCase):
    def _write_minimal_episode(self, ep: Path) -> None:
        SpeakerVisibilityTests()._write_minimal_sidecar(ep)
        (ep / "source" / "user-provided-transcript.txt").write_text(
            "Lenny Rachitsky: Eric, welcome.\n"
            "Eric Ries: Thanks for having me.\n",
            encoding="utf-8",
        )

    def test_export_json_writes_package_and_skips_html_render(self) -> None:
        import contextlib
        import io

        podcast_build = load_podcast_build()
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp)
            self._write_minimal_episode(ep)
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                rc = podcast_build.main(["export-json", str(ep)])

            package_path = ep / "final" / "episode.package.json"
            self.assertEqual(rc, 0)
            self.assertEqual(Path(stdout.getvalue().strip()).resolve(), package_path.resolve())
            self.assertTrue(package_path.is_file())
            self.assertFalse((ep / "final" / "podcast-at-a-glance.html").exists())
            self.assertFalse((ep / "final" / "annotated-transcript.html").exists())
            package = json.loads(package_path.read_text(encoding="utf-8"))
            self.assertEqual(package["schema_version"], "podcast-transformer/package-v1")

    def test_rendered_artifacts_follow_html_source_document_contract(self) -> None:
        podcast_build = load_podcast_build()
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp)
            self._write_minimal_episode(ep)
            package = podcast_build.build_package(ep)

            podcast_build.render_artifacts(ep, package)

            self.assertEqual(podcast_build.validate_artifacts(ep), 0)
            for name in ("podcast-at-a-glance.html", "annotated-transcript.html"):
                html = (ep / "final" / name).read_text(encoding="utf-8")
                self.assertIn('<link rel="icon" href="data:,">', html)
                self.assertIn('type="application/json" id="episode-data"', html)
                self.assertIn('aria-current="page"', html)
                self.assertNotIn('role="tablist"', html)
                self.assertNotIn('role="tab"', html)
                self.assertNotIn("aria-selected", html)


if __name__ == "__main__":
    unittest.main()
