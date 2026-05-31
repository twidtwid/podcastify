from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "podcast-transformer" / "scripts" / "extract_entity_links.py"


def load_module():
    spec = importlib.util.spec_from_file_location("extract_entity_links", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SponsorFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_module()

    def test_detect_sponsors_from_adread(self) -> None:
        text = ("Before we get to it, this episode is brought to you by Mercury. "
                "As an AI founder I track run rate. Sponsored by Linear is a different show.")
        sponsors = self.m.detect_sponsors(text)
        self.assertIn("mercury", sponsors)
        self.assertIn("linear", sponsors)

    def test_no_false_positive_when_no_adread(self) -> None:
        self.assertEqual(self.m.detect_sponsors("We discussed OpenAI and Vercel at length."), set())

    def test_sponsor_link_dropped_by_label_and_domain(self) -> None:
        sponsors = {"mercury"}
        self.assertTrue(self.m._is_sponsor_link("Mercury", "https://mercury.com/", sponsors))
        self.assertTrue(self.m._is_sponsor_link("anything", "https://www.mercury.com/x", sponsors))

    def test_non_sponsor_link_kept(self) -> None:
        sponsors = {"mercury"}
        self.assertFalse(self.m._is_sponsor_link("OpenAI", "https://openai.com/", sponsors))
        self.assertFalse(self.m._is_sponsor_link("Vercel", "https://vercel.com/", sponsors))

    def test_extract_filters_sponsor_from_show_notes(self) -> None:
        notes = ("• Mercury: https://mercury.com/\n"
                 "• OpenAI Codex: https://openai.com/codex/\n")
        pairs = self.m.extract(notes, sponsors={"mercury"})
        labels = {p["label"] for p in pairs}
        self.assertNotIn("Mercury", labels)
        self.assertIn("OpenAI Codex", labels)


if __name__ == "__main__":
    unittest.main()
