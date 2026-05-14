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

    def test_url_ingest_bundle_canonical_url_beats_platform_links(self) -> None:
        parse_source = load_parse_source()
        with tempfile.TemporaryDirectory() as tmp:
            episode_dir = Path(tmp) / "episode"
            source_dir = episode_dir / "source"
            source_dir.mkdir(parents=True)
            source_input = source_dir / "_source_input.txt"
            source_input.write_text(
                "Canonical URL: https://conversationswithtyler.com/episodes/craig-newmark/\n"
                "Title: Craig Newmark on Institutional Maintenance\n"
                "Podcast: Conversations with Tyler\n"
                "Host: Tyler Cowen\n"
                "\n"
                "Links:\n"
                "- YouTube episode: https://www.youtube.com/watch?v=pZMuKkH92fo\n"
                "- Apple: https://podcasts.apple.com/us/podcast/conversations-with-tyler/id983795625\n",
                encoding="utf-8",
            )

            self.assertEqual(parse_source.main([str(source_input), str(episode_dir)]), 0)

            parsed = (episode_dir / "working" / "_parsed.json").read_text(encoding="utf-8")
            self.assertIn(
                '"canonical_url": "https://conversationswithtyler.com/episodes/craig-newmark/"',
                parsed,
            )


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


class ProseGuestMiningIsGone(unittest.TestCase):
    """parse_source no longer ships prose-mining heuristics for the guest's
    name (or the host's, or the new-book title). Those overfit one
    publisher's voice and produced wrong guesses elsewhere. The current
    pipeline asks the local Ollama model in resolve_speakers.py instead.

    These tests are a tombstone — fail loudly if anyone reintroduces them.
    """

    def test_derive_guest_function_no_longer_exists(self) -> None:
        parse_source = load_parse_source()
        self.assertFalse(hasattr(parse_source, "derive_guest"))

    def test_derive_host_function_no_longer_exists(self) -> None:
        parse_source = load_parse_source()
        self.assertFalse(hasattr(parse_source, "derive_host"))

    def test_derive_book_function_no_longer_exists(self) -> None:
        parse_source = load_parse_source()
        self.assertFalse(hasattr(parse_source, "derive_book"))

    def test_prose_regexes_no_longer_exist(self) -> None:
        parse_source = load_parse_source()
        for name in ("GUEST_RE", "HOST_PATTERN", "BOOK_PATTERN", "_TITLE_GUEST_RE"):
            self.assertFalse(
                hasattr(parse_source, name),
                f"parse_source.{name} should be gone (replaced by resolve_speakers.py)",
            )


class NormalizeDateTests(unittest.TestCase):
    def test_iso_timestamp_truncated_to_calendar_date(self) -> None:
        parse_source = load_parse_source()
        self.assertEqual(
            parse_source._normalize_date("2026-04-13T06:00:00-04:00"),
            "2026-04-13",
        )

    def test_already_calendar_date_unchanged(self) -> None:
        parse_source = load_parse_source()
        self.assertEqual(parse_source._normalize_date("2026-05-10"), "2026-05-10")

    def test_empty_stays_empty(self) -> None:
        parse_source = load_parse_source()
        self.assertEqual(parse_source._normalize_date(""), "")

    def test_garbage_passes_through_unchanged(self) -> None:
        # Conservative: don't drop a value we don't recognize — let the next
        # consumer decide (sidecar.validate will complain if it's malformed).
        parse_source = load_parse_source()
        self.assertEqual(parse_source._normalize_date("April 13"), "April 13")


class BundleMetadataTests(unittest.TestCase):
    """url_ingest bundles must round-trip Title/Podcast/Host/Guest/Date back
    out of _source_input.txt so the sidecar populates `episode.title` and
    `episode.podcast_title`. Strict sidecar validation rejects empty values.
    """

    def test_parse_bundle_metadata_reads_title_and_date(self) -> None:
        parse_source = load_parse_source()
        text = (
            "Canonical URL: https://example.com/p/foo\n"
            "Title: Sam Altman's Trust Issues at OpenAI\n"
            "Date: 2026-04-13T06:00:00-04:00\n"
        )
        meta = parse_source.parse_bundle_metadata(text)
        self.assertEqual(meta.get("title"), "Sam Altman's Trust Issues at OpenAI")
        self.assertEqual(meta.get("date"), "2026-04-13T06:00:00-04:00")
        self.assertEqual(meta.get("canonical url"), "https://example.com/p/foo")

    def test_parse_bundle_metadata_ignores_non_metadata_colon_lines(self) -> None:
        parse_source = load_parse_source()
        text = (
            "Title: Real Title\n"
            "Doctorow's three-stage platform decay: ...prose with a colon.\n"
            "Tim Ferriss: Hello.\n"  # SPEAKER_RE matches; not metadata
        )
        meta = parse_source.parse_bundle_metadata(text)
        self.assertEqual(meta, {"title": "Real Title"})


class DerivePodcastTitleTests(unittest.TestCase):
    """Cover the publishers the V1 skill ships providers for, so the sidecar's
    `episode.podcast_title` is populated and strict validation passes."""

    def test_lennys_substack(self) -> None:
        parse_source = load_parse_source()
        self.assertEqual(
            parse_source.derive_podcast_title(
                ["https://www.lennysnewsletter.com/p/eric-ries"]
            ),
            "Lenny's Podcast: Product | Career | Growth",
        )

    def test_new_yorker_radio_hour_via_generic_podcast_path(self) -> None:
        parse_source = load_parse_source()
        self.assertEqual(
            parse_source.derive_podcast_title(
                ["https://www.newyorker.com/podcast/the-new-yorker-radio-hour/sam-altmans-trust-issues-at-openai"]
            ),
            "The New Yorker Radio Hour",
        )

    def test_tim_blog(self) -> None:
        parse_source = load_parse_source()
        self.assertEqual(
            parse_source.derive_podcast_title(["https://tim.blog/2026/04/29/elad-gil/"]),
            "The Tim Ferriss Show",
        )

    def test_foundmyfitness(self) -> None:
        parse_source = load_parse_source()
        self.assertEqual(
            parse_source.derive_podcast_title(
                ["https://www.foundmyfitness.com/episodes/arthur-brooks"]
            ),
            "FoundMyFitness",
        )

    def test_ninety_nine_percent_invisible(self) -> None:
        parse_source = load_parse_source()
        self.assertEqual(
            parse_source.derive_podcast_title(
                ["https://99percentinvisible.org/episode/666-enshittification/"]
            ),
            "99% Invisible",
        )

    def test_apple_podcasts_url_unchanged(self) -> None:
        # Existing behavior must not regress.
        parse_source = load_parse_source()
        self.assertEqual(
            parse_source.derive_podcast_title(
                ["https://podcasts.apple.com/us/podcast/the-tim-ferriss-show/id863897795"]
            ),
            "The Tim Ferriss Show",
        )


if __name__ == "__main__":
    unittest.main()
