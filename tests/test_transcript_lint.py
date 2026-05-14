from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT_LINT = REPO_ROOT / "podcast-transformer" / "scripts" / "transcript_lint.py"


def load_transcript_lint():
    spec = importlib.util.spec_from_file_location("transcript_lint", TRANSCRIPT_LINT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {TRANSCRIPT_LINT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SpeakerLabelRecognitionTests(unittest.TestCase):
    """Regression coverage for diarization-style speaker labels.

    A Whisper / Substack transcription.json bundle whose page HTML did not
    expose a speaker_map produces lines like `SPEAKER_0: text` and
    `SPEAKER_1: text`. Before this fix, _SPEAKER_TOKEN's ALL-CAPS branch
    rejected the underscore, SPEAKER_RE found no matches, and the linter
    emitted a false-negative `no speaker labels found` warning even though
    every line was clearly speaker-labeled.
    """

    def test_speaker_underscore_digit_is_recognized(self) -> None:
        lint = load_transcript_lint()
        text = (
            "SPEAKER_0: Hello there.\n"
            "SPEAKER_1: Welcome.\n"
            "SPEAKER_0: Let's begin.\n"
        )
        matches = [m.group(1) for m in lint.SPEAKER_RE.finditer(text)]
        self.assertEqual(matches, ["SPEAKER_0", "SPEAKER_1", "SPEAKER_0"])

    def test_speaker_underscore_two_digits_is_recognized(self) -> None:
        # The whisperx / pyannote default label format is SPEAKER_NN.
        lint = load_transcript_lint()
        text = "SPEAKER_01: A.\nSPEAKER_02: B.\n"
        matches = [m.group(1) for m in lint.SPEAKER_RE.finditer(text)]
        self.assertEqual(matches, ["SPEAKER_01", "SPEAKER_02"])

    def test_human_name_speakers_still_recognized(self) -> None:
        # Guard against widening the regex too far and changing existing behavior.
        lint = load_transcript_lint()
        text = (
            "Eric Ries: ... all kinds of famous companies.\n"
            "Lenny Rachitsky: I want to hear the story.\n"
            "Eric Ries: Dario was a first-time founder.\n"
        )
        matches = [m.group(1) for m in lint.SPEAKER_RE.finditer(text)]
        self.assertEqual(matches, ["Eric Ries", "Lenny Rachitsky", "Eric Ries"])

    def test_lowercase_sentence_with_colon_still_rejected(self) -> None:
        # Confirms the underscore widening didn't open the door to non-speaker
        # colon prefixes like "Doctorow's three-stage platform decay: ..."
        lint = load_transcript_lint()
        text = "Doctorow's three-stage platform decay: a thesis with words after.\n"
        self.assertEqual(list(lint.SPEAKER_RE.finditer(text)), [])

    def test_unclear_markers_are_clean_when_covered_by_sidecar(self) -> None:
        lint = load_transcript_lint()
        text = "Eric Ries: This phrase has [inaudible 00:03:14] in it.\n"
        sidecar = {"verification": {"uncertain_spans": [{"text": "[inaudible 00:03:14]"}]}}
        with tempfile.TemporaryDirectory() as tmp:
            sidecar_path = Path(tmp) / "metadata.sidecar.json"
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
            errors, warnings, stats = lint.lint_text(text, sidecar_path=sidecar_path)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, ["no timestamps found"])
        self.assertEqual(stats["unclear_markers"], 1)

    def test_unrelated_uncertain_span_does_not_cover_unclear_marker(self) -> None:
        lint = load_transcript_lint()
        text = "Eric Ries: This phrase has [inaudible 00:03:14] in it.\n"
        sidecar = {"verification": {"uncertain_spans": [{"text": "[unclear company name]"}]}}
        with tempfile.TemporaryDirectory() as tmp:
            sidecar_path = Path(tmp) / "metadata.sidecar.json"
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
            errors, warnings, stats = lint.lint_text(text, sidecar_path=sidecar_path)
        self.assertEqual(errors, [])
        self.assertIn(
            "1 unclear/inaudible markers found; confirm sidecar uncertain_spans covers material cases",
            warnings,
        )
        self.assertEqual(stats["sidecar_uncertain_spans"], 1)
        self.assertEqual(stats["covered_unclear_markers"], 0)

    def test_malformed_explicit_sidecar_is_reported(self) -> None:
        lint = load_transcript_lint()
        text = "Eric Ries: This phrase has [inaudible 00:03:14] in it.\n"
        with tempfile.TemporaryDirectory() as tmp:
            sidecar_path = Path(tmp) / "metadata.sidecar.json"
            sidecar_path.write_text("{not valid json", encoding="utf-8")
            errors, warnings, stats = lint.lint_text(text, sidecar_path=sidecar_path)
        self.assertEqual(errors, [f"could not read sidecar {sidecar_path}: Expecting property name enclosed in double quotes"])
        self.assertIn(
            "1 unclear/inaudible markers found; confirm sidecar uncertain_spans covers material cases",
            warnings,
        )
        self.assertEqual(stats["sidecar_uncertain_spans"], 0)

    def test_uncovered_unclear_markers_still_warn(self) -> None:
        lint = load_transcript_lint()
        text = "Guest: This phrase has [inaudible 00:03:14] in it.\n"
        errors, warnings, stats = lint.lint_text(text)
        self.assertEqual(errors, [])
        self.assertIn(
            "1 unclear/inaudible markers found; confirm sidecar uncertain_spans covers material cases",
            warnings,
        )
        self.assertEqual(stats["unclear_markers"], 1)

    def test_no_inline_timestamps_is_clean_when_sidecar_has_chapters(self) -> None:
        lint = load_transcript_lint()
        text = "David Remnick: Welcome.\nRonan Farrow: Thanks.\n"
        sidecar = {
            "episode": {"chapters": [{"start": 0, "title": "Opening"}]},
            "verification": {"uncertain_spans": []},
        }
        with tempfile.TemporaryDirectory() as tmp:
            sidecar_path = Path(tmp) / "metadata.sidecar.json"
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
            errors, warnings, stats = lint.lint_text(text, sidecar_path=sidecar_path)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(stats["timestamps"], 0)
        self.assertEqual(stats["sidecar_chapters"], 1)


if __name__ == "__main__":
    unittest.main()
