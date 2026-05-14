from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PARSE_SOURCE = REPO_ROOT / "podcast-transformer" / "scripts" / "parse_source.py"


def load_parse_source():
    spec = importlib.util.spec_from_file_location("parse_source", PARSE_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {PARSE_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ParseSourceSameFileTests(unittest.TestCase):
    def test_input_can_already_be_episode_source_input(self) -> None:
        parse_source = load_parse_source()
        with tempfile.TemporaryDirectory() as tmp:
            episode_dir = Path(tmp) / "episode"
            source_dir = episode_dir / "source"
            source_dir.mkdir(parents=True)
            source_input = source_dir / "_source_input.txt"
            source_input.write_text(
                "Canonical URL: https://tim.blog/2026/04/29/elad-gil/\n"
                "Tim Ferriss: Hello\n"
                "Elad Gil: Hi\n"
                "Tim Ferriss: Welcome\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_source.main([str(source_input), str(episode_dir)]), 0)
            self.assertTrue((episode_dir / "working" / "_parsed.json").exists())


class ExtractInlineTranscriptTests(unittest.TestCase):
    """Regression coverage for the metadata-stomp bug.

    url_ingest writes a bundle whose _source_input.txt is metadata only
    (Canonical URL / Title / Transcript URL / Date / link list) and stages
    the real transcript in source/user-provided-transcript.txt. Before this
    fix, SPEAKER_RE matched the `Foo: bar` metadata lines, parse_source
    declared a false-positive inline transcript, and overwrote the staged
    transcript with metadata fragments.
    """

    SUBSTACK_BUNDLE = (
        "Canonical URL: https://www.lennysnewsletter.com/p/some-episode\n"
        "Title: An episode title with a colon: it still contains a colon\n"
        "Transcript URL: https://substackcdn.com/transcription.json?Expires=1\n"
        "Date: 2026-05-10T12:03:32.926Z\n"
        "\n"
        "Links:\n"
        "- YouTube: https://youtu.be/abc\n"
        "- Spotify: https://open.spotify.com/episode/xyz\n"
        "- Apple Podcasts: https://podcasts.apple.com/us/podcast/foo/id1\n"
    )

    def test_metadata_only_bundle_is_not_an_inline_transcript(self) -> None:
        parse_source = load_parse_source()
        self.assertEqual(
            parse_source.extract_inline_transcript(self.SUBSTACK_BUNDLE),
            "",
        )

    def test_real_transcript_after_metadata_is_still_detected(self) -> None:
        parse_source = load_parse_source()
        text = self.SUBSTACK_BUNDLE + (
            "\n"
            "Tim Ferriss: Hello and welcome.\n"
            "Elad Gil: Glad to be here.\n"
            "Tim Ferriss: Let's start.\n"
        )
        extracted = parse_source.extract_inline_transcript(text)
        self.assertIn("Tim Ferriss: Hello and welcome.", extracted)
        # Metadata lines must NOT be folded into the extracted transcript.
        self.assertNotIn("Canonical URL", extracted)
        self.assertNotIn("Transcript URL", extracted)
        self.assertNotIn("Title:", extracted)

    def test_existing_staged_transcript_is_not_overwritten(self) -> None:
        parse_source = load_parse_source()
        with tempfile.TemporaryDirectory() as tmp:
            episode_dir = Path(tmp) / "episode"
            source_dir = episode_dir / "source"
            source_dir.mkdir(parents=True)
            staged = source_dir / "user-provided-transcript.txt"
            real_transcript = (
                "SPEAKER_01: I think it is very hard to be the right amount of AGI-pilled.\n"
                + ("Lenny Rachitsky: That makes sense.\n" * 50)
            )
            staged.write_text(real_transcript, encoding="utf-8")
            source_input = source_dir / "_source_input.txt"
            source_input.write_text(self.SUBSTACK_BUNDLE, encoding="utf-8")

            self.assertEqual(parse_source.main([str(source_input), str(episode_dir)]), 0)
            self.assertEqual(staged.read_text(encoding="utf-8"), real_transcript)


if __name__ == "__main__":
    unittest.main()
