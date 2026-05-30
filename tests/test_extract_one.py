from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT_ONE = REPO_ROOT / "podcast-transformer" / "scripts" / "extract_one.py"


def load_extract_one():
    spec = importlib.util.spec_from_file_location("extract_one", EXTRACT_ONE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {EXTRACT_ONE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ExtractOneSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extract_one = load_extract_one()

    def test_slug_rejects_path_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid slug"):
            self.extract_one.validate_slug("../../outside")

    def test_slug_allows_normal_episode_slugs(self) -> None:
        self.assertEqual(
            self.extract_one.validate_slug("lenny-ries-incorruptible"),
            "lenny-ries-incorruptible",
        )

    def test_resolve_episode_dir_stays_under_output_root(self) -> None:
        out_root = Path("/tmp/podcastify-test-output")
        episode_dir = self.extract_one.resolve_episode_dir(out_root, "abc-123")
        self.assertEqual(episode_dir, out_root.resolve() / "abc-123")

    def test_fetch_transcript_reports_generic_browse_install_command(self) -> None:
        with mock.patch.object(shutil, "which", return_value=None):
            with self.assertRaisesRegex(
                self.extract_one.StepError,
                "browse-cli not found on PATH; install with: brew install pepijnsenders/tap/browse && browse init",
            ):
                self.extract_one.fetch_transcript(
                    "https://example.com/episode",
                    Path("/tmp/podcastify-test-output/source/transcript_raw.txt"),
                )

    def test_is_http_url_detects_only_http_and_https(self) -> None:
        self.assertTrue(self.extract_one.is_http_url("https://example.com/episode"))
        self.assertTrue(self.extract_one.is_http_url("http://example.com/episode"))
        self.assertFalse(self.extract_one.is_http_url("/tmp/source.txt"))
        self.assertFalse(self.extract_one.is_http_url("file:///tmp/source.txt"))

    def test_prepare_source_input_leaves_local_files_unchanged(self) -> None:
        args = argparse.Namespace(
            source_file="/tmp/source.txt",
            out_root=Path("/tmp/out"),
            slug=None,
        )
        self.assertEqual(
            self.extract_one.prepare_source_input(args),
            (Path("/tmp/source.txt"), None),
        )

    def test_prepare_source_input_runs_url_ingest_and_returns_generated_source(self) -> None:
        args = argparse.Namespace(
            source_file="https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands",
            out_root=Path("/tmp/out"),
            slug="lenny-test",
        )
        completed = mock.Mock()
        completed.stdout = b"/tmp/out/lenny-test\n"
        expected_dir = Path("/tmp/out/lenny-test").resolve()
        expected = expected_dir / "source" / "_source_input.txt"
        with mock.patch.object(self.extract_one, "run", return_value=completed) as run_mock:
            with mock.patch.object(Path, "exists", return_value=True):
                source, prepared_dir = self.extract_one.prepare_source_input(args)
        self.assertEqual(source, expected)
        self.assertEqual(prepared_dir, expected_dir)
        run_mock.assert_called_once()
        self.assertIn("url_ingest.py", run_mock.call_args.args[0][1])

    def test_prepare_source_input_resolves_url_ingest_stdout_path(self) -> None:
        args = argparse.Namespace(
            source_file="https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands",
            out_root=Path("/tmp/out"),
            slug="lenny-test",
        )
        completed = mock.Mock()
        completed.stdout = b"/tmp/out/lenny-test\n"
        expected_dir = Path("/tmp/out/lenny-test").resolve()
        expected_source = expected_dir / "source" / "_source_input.txt"
        with mock.patch.object(self.extract_one, "run", return_value=completed):
            with mock.patch.object(Path, "exists", return_value=True):
                source, prepared_dir = self.extract_one.prepare_source_input(args)
        self.assertEqual(source, expected_source)
        self.assertEqual(prepared_dir, expected_dir)

    def test_should_skip_fetch_when_prepared_transcript_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "source" / "user-provided-transcript.txt"
            transcript.parent.mkdir(parents=True)
            transcript.write_text("HOST: Hello\nGUEST: Hi\n", encoding="utf-8")
            self.assertTrue(self.extract_one.has_prepared_transcript(Path(tmp)))

    def test_resolve_speakers_can_be_skipped_when_metadata_has_participants(self) -> None:
        args = argparse.Namespace(host=None, guest=[])
        parsed = {"host_guess": "Lenny Rachitsky", "guest_guess": "Eric Ries"}
        self.assertTrue(self.extract_one.can_skip_resolve_speakers(args, parsed))
        self.assertEqual(
            self.extract_one.resolved_participants(args, parsed),
            ("Lenny Rachitsky", ["Eric Ries"]),
        )

    def test_resolve_speakers_runs_when_guest_is_unknown(self) -> None:
        args = argparse.Namespace(host=None, guest=[])
        parsed = {"host_guess": "Lenny Rachitsky", "guest_guess": ""}
        self.assertFalse(self.extract_one.can_skip_resolve_speakers(args, parsed))

    def test_cli_participants_beat_metadata_and_model_output(self) -> None:
        args = argparse.Namespace(host="CLI Host", guest=["CLI Guest"])
        parsed = {"host_guess": "Metadata Host", "guest_guess": "Metadata Guest"}
        speakers = {"host": "Model Host", "guests": ["Model Guest"]}
        self.assertEqual(
            self.extract_one.resolved_participants(args, parsed, speakers),
            ("CLI Host", ["CLI Guest"]),
        )


if __name__ == "__main__":
    unittest.main()
