from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SHARPEN_NOTES = REPO_ROOT / "podcast-transformer" / "scripts" / "sharpen_notes.py"


def load_sharpen_notes():
    scripts_dir = str(SHARPEN_NOTES.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("sharpen_notes", SHARPEN_NOTES)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {SHARPEN_NOTES}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SharpenTopicValidationTests(unittest.TestCase):
    def test_rejects_topics_that_exceed_notes_lint_limit(self) -> None:
        sharpen = load_sharpen_notes()
        claim = {
            "topic": "Mission protects purpose",
            "claim": "Mission must protect purpose when investors apply pressure to the charter.",
            "evidence": "The guest describes mission pressure.",
        }
        too_long = "Mission protects purpose when investors apply relentless pressure to the fragile charter forever"
        with mock.patch.object(sharpen, "call_ollama", return_value=f'{{"topic": "{too_long}"}}'):
            self.assertIsNone(sharpen.sharpen_topic("model", claim, 4096))


if __name__ == "__main__":
    unittest.main()
