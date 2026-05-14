from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "podcast-transformer" / "scripts"
GENERATE_CHAPTERS = SCRIPTS_DIR / "generate_chapters.py"


def load_generate_chapters():
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("generate_chapters", GENERATE_CHAPTERS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {GENERATE_CHAPTERS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NormalizeChaptersTests(unittest.TestCase):
    """`_normalize_chapters` had no duration guard. gemma4 occasionally emits
    `start_min` values an order of magnitude past the episode duration
    (145, 300, 430 ... for a 61-minute episode), which then become 8700,
    18000, 25800 seconds in the sidecar. anchor_chapters collapses every
    out-of-range chapter onto the LAST transcript turn, producing 10+
    duplicate-anchor warnings and a chapter rail that points to the same
    place from every entry.
    """

    def test_normalize_drops_chapters_past_episode_duration(self) -> None:
        gc = load_generate_chapters()
        payload = {
            "chapters": [
                {"start_min": 0, "title": "Cold open", "query": "we begin"},
                {"start_min": 10, "title": "Middle", "query": "as I was saying"},
                {"start_min": 145, "title": "Way past end", "query": "out of range"},
                {"start_min": 300, "title": "Even further", "query": "still out"},
            ]
        }
        chapters = gc._normalize_chapters(payload, duration_minutes=61)
        titles = [c["title"] for c in chapters]
        self.assertIn("Cold open", titles)
        self.assertIn("Middle", titles)
        self.assertNotIn("Way past end", titles)
        self.assertNotIn("Even further", titles)

    def test_normalize_keeps_chapters_within_duration(self) -> None:
        gc = load_generate_chapters()
        payload = {
            "chapters": [
                {"start_min": 0, "title": "A", "query": "q1"},
                {"start_min": 5, "title": "B", "query": "q2"},
                {"start_min": 12, "title": "C", "query": "q3"},
            ]
        }
        chapters = gc._normalize_chapters(payload, duration_minutes=20)
        self.assertEqual(len(chapters), 3)
        self.assertEqual([c["start"] for c in chapters], [0, 300, 720])

    def test_normalize_works_when_duration_unknown(self) -> None:
        # Some publishers don't expose duration. Without a duration bound,
        # we have no signal to filter — keep everything (the prior
        # behavior).
        gc = load_generate_chapters()
        payload = {
            "chapters": [
                {"start_min": 0, "title": "A", "query": "q"},
                {"start_min": 999, "title": "B", "query": "q"},
            ]
        }
        chapters = gc._normalize_chapters(payload, duration_minutes=0)
        self.assertEqual(len(chapters), 2)


if __name__ == "__main__":
    unittest.main()
