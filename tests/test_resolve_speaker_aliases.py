from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "podcast-transformer" / "scripts"


def load_module():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    path = SCRIPTS / "resolve_speaker_aliases.py"
    spec = importlib.util.spec_from_file_location("resolve_speaker_aliases", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GenericLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_module()

    def test_recognizes_diarization_placeholders(self) -> None:
        for label in ("SPEAKER", "SPEAKER_00", "SPEAKER_1", "SPEAKER 2",
                      "SPEAKER-12", "speaker_03"):
            self.assertTrue(self.mod.is_generic_label(label), label)

    def test_rejects_real_names(self) -> None:
        for name in ("Dwarkesh Patel", "Dylan Patel", "HOST", "Tim Ferriss",
                     "SPEAKERPHONE", "Speaker of the House"):
            self.assertFalse(self.mod.is_generic_label(name), name)

    def test_generic_labels_in_first_appearance_order(self) -> None:
        turns = [
            {"speaker": "SPEAKER_01"}, {"speaker": "SPEAKER_00"},
            {"speaker": "SPEAKER_01"}, {"speaker": "Dwarkesh Patel"},
            {"speaker": "SPEAKER_00"},
        ]
        self.assertEqual(self.mod.generic_labels_in_order(turns),
                         ["SPEAKER_01", "SPEAKER_00"])


class ValidateMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_module()

    def test_keeps_known_labels_mapped_to_known_participants(self) -> None:
        result = self.mod.validate_map(
            {"SPEAKER_00": "Dwarkesh Patel", "SPEAKER_01": "Dylan Patel"},
            ["SPEAKER_00", "SPEAKER_01"],
            ["Dwarkesh Patel", "Dylan Patel"],
        )
        self.assertEqual(result,
                         {"SPEAKER_00": "Dwarkesh Patel", "SPEAKER_01": "Dylan Patel"})

    def test_drops_hallucinated_names(self) -> None:
        # A name not in the participant list is never written into the transcript.
        result = self.mod.validate_map(
            {"SPEAKER_00": "Dwarkesh Patel", "SPEAKER_01": "Sam Altman"},
            ["SPEAKER_00", "SPEAKER_01"],
            ["Dwarkesh Patel", "Dylan Patel"],
        )
        self.assertEqual(result, {"SPEAKER_00": "Dwarkesh Patel"})

    def test_drops_unknown_labels_and_is_case_insensitive(self) -> None:
        result = self.mod.validate_map(
            {"SPEAKER_99": "Dylan Patel", "SPEAKER_00": "dylan patel"},
            ["SPEAKER_00"],
            ["Dwarkesh Patel", "Dylan Patel"],
        )
        self.assertEqual(result, {"SPEAKER_00": "Dylan Patel"})


class DeterministicFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_module()

    def test_two_speakers_first_label_is_host(self) -> None:
        result = self.mod.deterministic_two_speaker_map(
            ["SPEAKER_00", "SPEAKER_01"], "Dwarkesh Patel", ["Dylan Patel"])
        self.assertEqual(result,
                         {"SPEAKER_00": "Dwarkesh Patel", "SPEAKER_01": "Dylan Patel"})

    def test_no_fallback_when_not_exactly_two_each(self) -> None:
        self.assertEqual(
            self.mod.deterministic_two_speaker_map(
                ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"],
                "Host", ["Guest"]),
            {})
        self.assertEqual(
            self.mod.deterministic_two_speaker_map(
                ["SPEAKER_00", "SPEAKER_01"], "", ["Guest"]),
            {})


class ApplyAliasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_module()

    def test_relabels_and_merges_adjacent_same_speaker_turns(self) -> None:
        turns = [
            {"speaker": "SPEAKER_00", "text": "Welcome to the show.", "word_count": 4},
            {"speaker": "SPEAKER_01", "text": "Glad to be here.", "word_count": 4},
            {"speaker": "SPEAKER_01", "text": "Big fan.", "word_count": 2},
            {"speaker": "SPEAKER_00", "text": "Let's dive in.", "word_count": 3},
        ]
        out = self.mod.apply_aliases(
            turns, {"SPEAKER_00": "Dwarkesh Patel", "SPEAKER_01": "Dylan Patel"})
        self.assertEqual([t["speaker"] for t in out],
                         ["Dwarkesh Patel", "Dylan Patel", "Dwarkesh Patel"])
        self.assertEqual(out[1]["text"], "Glad to be here. Big fan.")
        self.assertEqual(out[1]["word_count"], 6)

    def test_unmapped_labels_are_left_untouched(self) -> None:
        turns = [
            {"speaker": "SPEAKER_00", "text": "Hi."},
            {"speaker": "SPEAKER_03", "text": "(crosstalk)"},
        ]
        out = self.mod.apply_aliases(turns, {"SPEAKER_00": "Dwarkesh Patel"})
        self.assertEqual([t["speaker"] for t in out],
                         ["Dwarkesh Patel", "SPEAKER_03"])

    def test_turns_to_transcript_renders_speaker_lines(self) -> None:
        turns = [
            {"speaker": "Dwarkesh Patel", "text": "Question?"},
            {"speaker": "Dylan Patel", "text": "Answer."},
        ]
        self.assertEqual(self.mod.turns_to_transcript(turns),
                         "Dwarkesh Patel: Question?\nDylan Patel: Answer.\n")


if __name__ == "__main__":
    unittest.main()
