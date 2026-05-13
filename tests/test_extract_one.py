from __future__ import annotations

import importlib.util
import shutil
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


if __name__ == "__main__":
    unittest.main()
