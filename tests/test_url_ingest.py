from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
URL_INGEST = REPO_ROOT / "podcast-transformer" / "scripts" / "url_ingest.py"
PROVIDERS = REPO_ROOT / "podcast-transformer" / "providers"


def load_url_ingest():
    spec = importlib.util.spec_from_file_location("url_ingest", URL_INGEST)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {URL_INGEST}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class UrlIngestManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()

    def test_manifest_contains_expected_seed_providers(self) -> None:
        manifest = self.url_ingest.load_manifest(PROVIDERS)
        ids = {provider["id"] for provider in manifest["providers"]}
        self.assertEqual(
            ids,
            {
                "lenny_substack",
                "new_yorker",
                "foundmyfitness",
                "tim_blog",
                "99pi",
                "conversations_with_tyler",
                "dwarkesh",
                "youtube",
            },
        )

    def test_provider_manifest_is_one_file_per_provider(self) -> None:
        files = sorted(path.name for path in PROVIDERS.glob("*.json"))
        self.assertEqual(
            files,
            [
                "99pi.json",
                "conversations_with_tyler.json",
                "dwarkesh.json",
                "foundmyfitness.json",
                "lenny_substack.json",
                "new_yorker.json",
                "tim_blog.json",
                "youtube.json",
            ],
        )

    def test_match_provider_uses_hostname_case_insensitively(self) -> None:
        manifest = self.url_ingest.load_manifest(PROVIDERS)
        provider = self.url_ingest.match_provider(
            "https://WWW.LENNYSNEWSLETTER.COM/p/how-to-build-a-company-that-withstands",
            manifest,
        )
        self.assertEqual(provider["id"], "lenny_substack")
        self.assertEqual(provider["kind"], "substack")

    def test_match_provider_rejects_unknown_domain_with_supported_domains(self) -> None:
        manifest = self.url_ingest.load_manifest(PROVIDERS)
        with self.assertRaisesRegex(
            self.url_ingest.UrlIngestError,
            "Unsupported podcast URL domain.*99percentinvisible.org.*tim.blog",
        ):
            self.url_ingest.match_provider("https://example.com/episode", manifest)


class UrlIngestScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_root = Path(self.tmp.name) / "out"

    def test_slugify_keeps_episode_dirs_safe_and_readable(self) -> None:
        self.assertEqual(
            self.url_ingest.slugify("How to Build a Company: Eric Ries!"),
            "how-to-build-a-company-eric-ries",
        )

    def test_fetch_once_writes_cached_response_under_working_fetches(self) -> None:
        episode_dir = self.out_root / "episode"

        def fake_fetcher(url: str) -> tuple[int, str, bytes]:
            self.assertEqual(url, "https://example.com/episode")
            return 200, "text/html; charset=utf-8", b"<html><h1>Episode</h1></html>"

        path = self.url_ingest.fetch_once(
            "https://example.com/episode",
            episode_dir,
            "page.html",
            fetcher=fake_fetcher,
        )

        self.assertEqual(path, episode_dir / "working" / "fetches" / "page.html")
        self.assertEqual(path.read_text(encoding="utf-8"), "<html><h1>Episode</h1></html>")

    def test_fetch_once_allows_normal_pages_with_form_noscript_javascript_warning(self) -> None:
        episode_dir = self.out_root / "episode"

        def fake_fetcher(url: str) -> tuple[int, str, bytes]:
            return (
                200,
                "text/html; charset=utf-8",
                b"<html><body><article><h1>Episode</h1><noscript>Please enable JavaScript in your browser to complete this form.</noscript></article></body></html>",
            )

        path = self.url_ingest.fetch_once(
            "https://example.com/episode",
            episode_dir,
            "page.html",
            fetcher=fake_fetcher,
        )

        self.assertIn("<article>", path.read_text(encoding="utf-8"))

    def test_write_bundle_creates_output_contract(self) -> None:
        bundle = self.url_ingest.SourceBundle(
            provider_id="example",
            input_url="https://example.com/episode",
            canonical_url="https://example.com/episode",
            slug="example-episode",
            title="Example Episode",
            transcript_text="HOST: Hello\nGUEST: Hi\n",
            transcript_source_url="https://example.com/transcript",
            metadata={"date": "2026-05-13"},
            chapters=[{"time": "00:00", "title": "Intro"}],
            links=[{"url": "https://youtube.com/watch?v=abc", "text": "YouTube"}],
            warnings=["YouTube metadata not fetched"],
        )
        episode_dir = self.url_ingest.write_bundle(bundle, self.out_root)

        self.assertEqual(episode_dir, self.out_root / "example-episode")
        self.assertTrue((episode_dir / "source" / "_source_input.txt").exists())
        self.assertEqual(
            (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8"),
            "HOST: Hello\nGUEST: Hi\n",
        )
        provenance = json.loads(
            (episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(provenance["provider_id"], "example")
        self.assertEqual(provenance["transcript_path"], "source/user-provided-transcript.txt")


class UrlIngestHtmlUtilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()

    def test_extract_title_prefers_og_title_then_h1_then_title_tag(self) -> None:
        html_text = """
        <html><head>
          <title>Fallback Title</title>
          <meta property="og:title" content="Open Graph Episode">
        </head><body><h1>Heading Episode</h1></body></html>
        """
        self.assertEqual(self.url_ingest.extract_title(html_text), "Open Graph Episode")

    def test_extract_links_resolves_relative_urls_and_keeps_text(self) -> None:
        html_text = '<a href="/transcript">Download a Transcript</a><a href="https://youtu.be/abc">Watch</a>'
        links = self.url_ingest.extract_links(html_text, "https://example.com/episode")
        self.assertEqual(
            links,
            [
                {"url": "https://example.com/transcript", "text": "Download a Transcript"},
                {"url": "https://youtu.be/abc", "text": "Watch"},
            ],
        )

    def test_html_to_text_preserves_block_breaks_and_unescapes_entities(self) -> None:
        text = self.url_ingest.html_to_text("<h2>Transcript</h2><p>HOST:&nbsp;Hello</p><p>GUEST: Hi</p>")
        self.assertIn("Transcript\nHOST: Hello\nGUEST: Hi", text)

    def test_dialog_start_handles_alternating_two_speaker_transcript(self) -> None:
        text = "\n".join(
            [
                "Navigation",
                "Subscribe",
                "Host: Welcome to the show.",
                "Guest: Thanks for having me.",
                "Host: Let's start with the big idea.",
                "Guest: The big idea is resilience.",
            ]
        )
        transcript = self.url_ingest.text_or_html_to_transcript(text)
        self.assertEqual(
            transcript,
            "\n".join(
                [
                    "Host: Welcome to the show.",
                    "Guest: Thanks for having me.",
                    "Host: Let's start with the big idea.",
                    "Guest: The big idea is resilience.",
                ]
            )
            + "\n",
        )

    def test_dialog_start_handles_diarization_speaker_labels(self) -> None:
        text = "\n".join(
            [
                "Navigation",
                "SPEAKER_01: Welcome to the show.",
                "SPEAKER_02: Thanks for having me.",
                "SPEAKER_01: Let's begin.",
                "SPEAKER_02: Sounds good.",
            ]
        )
        transcript = self.url_ingest.text_or_html_to_transcript(text)
        self.assertTrue(transcript.startswith("SPEAKER_01: Welcome"))
        self.assertNotIn("Navigation", transcript)

    def test_dialog_start_rejects_one_off_reference_titles(self) -> None:
        lines = [
            "Transcription",
            "Just Think: The Challenges Of The Disengaged Mind",
            "TARGET ARTICLE: Posttraumatic Growth and Adversity",
            "Rhonda Patrick: Hi, everyone. I'm here with Dr. Arthur Brooks.",
            "Arthur Brooks: It's great to be here.",
            "Rhonda Patrick: Let's talk about happiness.",
            "Arthur Brooks: Happiness is not a feeling.",
        ]
        transcript = self.url_ingest.extract_transcript_section("\n".join(lines))
        self.assertTrue(transcript.startswith("Rhonda Patrick: Hi, everyone."))
        self.assertNotIn("Just Think", transcript)
        self.assertNotIn("TARGET ARTICLE", transcript)

    def test_inline_timestamp_markers_are_stripped_from_transcript_lines(self) -> None:
        text = "\n".join(
            [
                "HOST: This is the opening thought [00:11:00] continuing mid-sentence.",
                "GUEST: Another response.",
                "HOST: Back to the host.",
            ]
        )
        transcript = self.url_ingest.text_or_html_to_transcript(text)
        self.assertNotIn("[00:11:00]", transcript)
        self.assertIn("opening thought continuing mid-sentence", transcript)

    def test_dialog_end_drops_tim_blog_footer_legal_block(self) -> None:
        # tim.blog appends a multi-paragraph legal/comments block after the
        # final speaker turn. Lines like `LEGAL CONDITIONS:` and `Comment
        # Rules:` match the `Speaker: text` shape and get picked up as
        # spurious speakers in the transcript lint speaker list. The footer
        # speakers each appear exactly once; real dialog speakers recur.
        text = "\n".join(
            [
                "Tim Ferriss: Welcome back to the show.",
                "Elad Gil: Thanks for having me.",
                "Tim Ferriss: Let's begin.",
                "Elad Gil: Sounds great.",
                "Tim Ferriss: And until next time, thanks for tuning in.",
                "LEGAL CONDITIONS: Tim Ferriss owns the copyright...",
                "WHAT IS NOT ALLOWED: No one is authorized...",
                "Comment Rules: Remember what Fonzie was like? Cool.",
            ]
        )
        transcript = self.url_ingest.text_or_html_to_transcript(text)
        self.assertNotIn("LEGAL CONDITIONS", transcript)
        self.assertNotIn("WHAT IS NOT ALLOWED", transcript)
        self.assertNotIn("Comment Rules", transcript)
        self.assertIn("thanks for tuning in", transcript)

    def test_dialog_end_drops_99pi_related_episodes_listing(self) -> None:
        # 99pi appends a related-episodes sidebar after the final speaker
        # turn. Each related-episode title is rendered as
        # `<Title>: <Subtitle> Episode <N>` which matches the
        # `Speaker: text` shape. On the live page each related-episode
        # title repeats once (heading + "Play Pause Add to Queue" row),
        # so each chrome "speaker" appears exactly twice — real dialog
        # speakers recur many more times across the transcript.
        text = "\n".join(
            [
                "ROMAN MARS: Welcome to 99% Invisible.",
                "CHRIS BERUBE: Today's story is about right to repair.",
                "ROMAN MARS: Let's hear it.",
                "CHRIS BERUBE: Here's the thing.",
                "ROMAN MARS: Tell me more.",
                "CHRIS BERUBE: Right.",
                "ROMAN MARS: That's all for today.",
                "Florence Nightingale: Data Viz Pioneer Episode 433 -",
                "Florence Nightingale: Data Viz Pioneer Play Pause",
                "A Better World: Radical Cartographic Kurt Kohlstedt -",
                "A Better World: Radical Cartographic Play Pause",
                "Mini-Stories: Volume 2 Episode 242 -",
                "Mini-Stories: Volume 2 Play Pause",
                "Hills Hoist: The Iconic Rotary Clothesline that Shaped Australia",
            ]
        )
        transcript = self.url_ingest.text_or_html_to_transcript(text)
        self.assertNotIn("Florence Nightingale", transcript)
        self.assertNotIn("A Better World", transcript)
        self.assertNotIn("Mini-Stories", transcript)
        self.assertNotIn("Hills Hoist", transcript)
        self.assertIn("That's all for today", transcript)

    def test_extract_chapters_finds_common_timestamp_lines(self) -> None:
        text = "00:00 Intro\n12:34 Building durable teams\n1:02:03 Closing thoughts"
        self.assertEqual(
            self.url_ingest.extract_chapters(text),
            [
                {"time": "00:00", "title": "Intro"},
                {"time": "12:34", "title": "Building durable teams"},
                {"time": "1:02:03", "title": "Closing thoughts"},
            ],
        )


class UrlIngestStripTitleBoilerplateTests(unittest.TestCase):
    """Cover `title_strip_prefix` + suffix peeling.

    Suffix-stripping was previously the only knob; tim.blog per-episode
    transcript pages prepend `The Tim Ferriss Show Transcripts:` to og:title,
    which leaked into `episode.short_title` as the entire headline. These
    cases lock both ends of the trim, the looped-until-stable behaviour, and
    the case-insensitive match.
    """

    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()

    def test_suffix_is_stripped_with_dangling_separators(self) -> None:
        provider = {"title_strip_suffix": [" - The Blog of Author Tim Ferriss"]}
        result = self.url_ingest.strip_title_boilerplate(
            "Sami Inkinen of Virta Health - The Blog of Author Tim Ferriss",
            provider,
        )
        self.assertEqual(result, "Sami Inkinen of Virta Health")

    def test_prefix_is_stripped_with_dangling_separators(self) -> None:
        provider = {"title_strip_prefix": ["The Tim Ferriss Show Transcripts:"]}
        result = self.url_ingest.strip_title_boilerplate(
            "The Tim Ferriss Show Transcripts: Sami Inkinen of Virta Health",
            provider,
        )
        self.assertEqual(result, "Sami Inkinen of Virta Health")

    def test_prefix_and_suffix_apply_in_same_call(self) -> None:
        provider = {
            "title_strip_prefix": ["The Tim Ferriss Show Transcripts:"],
            "title_strip_suffix": [" — The Blog of Author Tim Ferriss"],
        }
        result = self.url_ingest.strip_title_boilerplate(
            "The Tim Ferriss Show Transcripts: Sami Inkinen of Virta Health"
            " — The Blog of Author Tim Ferriss",
            provider,
        )
        self.assertEqual(result, "Sami Inkinen of Virta Health")

    def test_matching_is_case_insensitive(self) -> None:
        provider = {"title_strip_prefix": ["THE TIM FERRISS SHOW:"]}
        result = self.url_ingest.strip_title_boilerplate(
            "the tim ferriss show: Episode title", provider)
        self.assertEqual(result, "Episode title")

    def test_loops_until_stable_when_a_prefix_exposes_another(self) -> None:
        provider = {"title_strip_prefix": [
            "The Tim Ferriss Show Transcripts:",
            "The Tim Ferriss Show:",
        ]}
        result = self.url_ingest.strip_title_boilerplate(
            "The Tim Ferriss Show Transcripts: The Tim Ferriss Show: Real headline",
            provider,
        )
        self.assertEqual(result, "Real headline")

    def test_provider_with_neither_key_returns_title_unchanged(self) -> None:
        result = self.url_ingest.strip_title_boilerplate(
            "Just a clean title", {})
        self.assertEqual(result, "Just a clean title")

    def test_string_value_is_accepted_in_addition_to_list(self) -> None:
        # title_strip_prefix accepts a single string, not just a list — the
        # function normalizes both, matching the prior suffix behavior.
        provider = {"title_strip_prefix": "Episode:"}
        result = self.url_ingest.strip_title_boilerplate(
            "Episode: Real headline", provider)
        self.assertEqual(result, "Real headline")


class UrlIngestSelectTranscriptLinkTests(unittest.TestCase):
    """Cover the negative-score guard in `select_transcript_link`.

    Fresh tim.blog episodes have an interview page but no per-episode
    transcript page yet. The publisher menu still links "All Transcripts"
    (an index of every transcript ever) — that link scores `-50` after the
    index-page penalty and used to win by default. The guard turns "best is
    negative" into a `None` return so the caller fails loud instead of
    silently ingesting an index page as the interview.
    """

    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()

    def test_index_only_candidates_return_none(self) -> None:
        # Every candidate matches "transcript" but each lives under an
        # index/category/all-transcripts marker, scoring negative.
        links = [
            {"url": "https://tim.blog/category/the-tim-ferriss-show-transcripts/",
             "text": "The Tim Ferriss Show Transcripts"},
            {"url": "https://tim.blog/2026/05/all-transcripts-from-the-tim-ferriss-show/",
             "text": "All Transcripts"},
            {"url": "https://tim.blog/transcripts/",
             "text": "Browse transcripts"},
        ]
        self.assertIsNone(
            self.url_ingest.select_transcript_link(links, contains="transcript")
        )

    def test_per_episode_permalink_still_wins(self) -> None:
        # A dated permalink scores +50; the index page scores -100. Positive
        # wins, no None returned.
        links = [
            {"url": "https://tim.blog/category/the-tim-ferriss-show-transcripts/",
             "text": "The Tim Ferriss Show Transcripts"},
            {"url": "https://tim.blog/2026/05/21/sami-inkinen-transcript/",
             "text": "This episode"},
        ]
        chosen = self.url_ingest.select_transcript_link(links, contains="transcript")
        self.assertIsNotNone(chosen)
        self.assertIn("sami-inkinen-transcript", chosen["url"])

    def test_no_candidates_return_none(self) -> None:
        self.assertIsNone(
            self.url_ingest.select_transcript_link(
                [{"url": "https://tim.blog/", "text": "Home"}],
                contains="transcript",
            )
        )


class UrlIngestYoutubeHelperTests(unittest.TestCase):
    """Cover the pure helpers behind `ingest_youtube_captions`.

    The full ingest path shells out to `yt-dlp` and depends on YouTube's
    response shape, but these three normalizers are pure functions on
    strings / dicts and worth locking down independently.
    """

    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()

    def test_clean_vtt_drops_headers_timestamps_and_cue_tags(self) -> None:
        vtt = "\n".join([
            "WEBVTT",
            "Kind: captions",
            "Language: en",
            "",
            "00:00:00.000 --> 00:00:02.000 align:start position:0%",
            "Hello",
            "Hello <00:00:01.234><c>world</c>",
            "",
            "00:00:02.000 --> 00:00:04.000 align:start position:0%",
            "Hello <00:00:01.234><c>world</c>",
            "How are you",
        ])
        cleaned = self.url_ingest._clean_youtube_vtt(vtt)
        # Headers, timestamps, inline cue tags all gone; chronological
        # first-occurrence dedupe keeps the line order; whitespace collapsed.
        self.assertEqual(cleaned, "Hello Hello world How are you")

    def test_clean_vtt_preserves_non_adjacent_repeated_utterances(self) -> None:
        # Earlier implementation deduped via a global `seen: set`, so any
        # short utterance the speaker actually repeated later in the
        # transcript ("Right.", "Yeah.") was silently dropped on its second
        # appearance. Prior-line dedupe still collapses the consecutive
        # progressive-build duplicates but preserves real repeats.
        vtt = "\n".join([
            "WEBVTT",
            "",
            "00:00:00.000 --> 00:00:01.000",
            "Right.",
            "",
            "00:00:01.000 --> 00:00:02.000",
            "I see what you mean.",
            "",
            "00:00:02.000 --> 00:00:03.000",
            "Right.",
        ])
        cleaned = self.url_ingest._clean_youtube_vtt(vtt)
        # Both "Right." occurrences survive — only consecutive duplicates
        # (build-up artefacts) get collapsed.
        self.assertEqual(cleaned, "Right. I see what you mean. Right.")

    def test_clean_vtt_empty_input_returns_empty_string(self) -> None:
        self.assertEqual(self.url_ingest._clean_youtube_vtt(""), "")
        self.assertEqual(
            self.url_ingest._clean_youtube_vtt("WEBVTT\nKind: captions\n"),
            "",
        )

    def test_chunk_into_speaker_turns_alternates_strictly(self) -> None:
        transcript = " ".join(f"Sentence number {n}." for n in range(1, 13))
        turns = self.url_ingest._chunk_into_speaker_turns(
            transcript, target_turns=6)
        # Strict Host/Guest alternation from the first turn onward.
        for i, turn in enumerate(turns):
            expected_speaker = "Host" if i % 2 == 0 else "Guest"
            self.assertTrue(turn.startswith(f"{expected_speaker}: "), turn)

    def test_chunk_into_speaker_turns_preserves_every_word(self) -> None:
        transcript = ("This is sentence one. Here comes sentence two. "
                      "And finally sentence three.")
        turns = self.url_ingest._chunk_into_speaker_turns(transcript, target_turns=3)
        # Strip speaker prefixes, rejoin: every word from the source survives.
        bodies = " ".join(t.split(": ", 1)[1] for t in turns)
        for word in transcript.split():
            self.assertIn(word, bodies)

    def test_chunk_falls_back_to_word_chunking_when_punctuation_is_missing(self) -> None:
        # ASR-only YouTube auto-captions arrive without sentence-ending
        # punctuation, so the regex split produces ONE "sentence" — without
        # the fallback that's exactly 1 turn and the caller's `len(turns)<3`
        # guard would raise on every such video. The word-based fallback
        # delivers the requested turn count instead.
        transcript = " ".join([f"word{i}" for i in range(120)])
        turns = self.url_ingest._chunk_into_speaker_turns(
            transcript, target_turns=6)
        self.assertGreaterEqual(len(turns), 3)
        # Strict alternation survives the fallback path.
        for i, turn in enumerate(turns):
            expected = "Host" if i % 2 == 0 else "Guest"
            self.assertTrue(turn.startswith(f"{expected}: "), turn)
        # Every input word is still represented in the output.
        bodies = " ".join(t.split(": ", 1)[1] for t in turns)
        for word in transcript.split():
            self.assertIn(word, bodies)

    def test_chunk_into_speaker_turns_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(self.url_ingest._chunk_into_speaker_turns(""), [])
        # Whitespace-only / non-sentence input also yields nothing.
        self.assertEqual(self.url_ingest._chunk_into_speaker_turns("   \n  "), [])

    def test_format_youtube_chapters_produces_pipeline_shape(self) -> None:
        info = {"chapters": [
            {"start_time": 0, "title": "Intro"},
            {"start_time": 754, "title": "The compute bottleneck"},
        ]}
        self.assertEqual(
            self.url_ingest._format_youtube_chapters(info),
            [
                {"timestamp": "00:00", "title": "Intro"},
                {"timestamp": "12:34", "title": "The compute bottleneck"},
            ],
        )

    def test_format_youtube_chapters_uses_hhmmss_past_one_hour(self) -> None:
        # A long-form 2h episode's "1:15:00" chapter would otherwise have
        # rendered as "75:00" under the old MM:SS-only scheme.
        info = {"chapters": [
            {"start_time": 3599, "title": "Just under an hour"},
            {"start_time": 3600, "title": "Exactly an hour"},
            {"start_time": 4500, "title": "Mid-second-hour"},
        ]}
        self.assertEqual(
            [c["timestamp"] for c in self.url_ingest._format_youtube_chapters(info)],
            ["59:59", "01:00:00", "01:15:00"],
        )

    def test_format_youtube_chapters_handles_missing_or_blank_title(self) -> None:
        info = {"chapters": [
            {"start_time": 60},                  # no title key
            {"start_time": 120, "title": "   "},  # blank title
            {"start_time": 180, "title": "Real title"},
        ]}
        result = self.url_ingest._format_youtube_chapters(info)
        self.assertEqual(
            [c["title"] for c in result],
            ["Untitled chapter", "Untitled chapter", "Real title"],
        )

    def test_format_youtube_chapters_skips_unparseable_start_time(self) -> None:
        info = {"chapters": [
            {"start_time": "not-a-number", "title": "Bad row"},
            {"start_time": 30, "title": "Good row"},
        ]}
        self.assertEqual(
            self.url_ingest._format_youtube_chapters(info),
            [{"timestamp": "00:30", "title": "Good row"}],
        )

    def test_format_youtube_chapters_missing_or_empty_key_returns_empty(self) -> None:
        self.assertEqual(self.url_ingest._format_youtube_chapters({}), [])
        self.assertEqual(
            self.url_ingest._format_youtube_chapters({"chapters": None}),
            [],
        )
        self.assertEqual(
            self.url_ingest._format_youtube_chapters({"chapters": []}),
            [],
        )


class UrlIngestDirectTranscriptProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_root = Path(self.tmp.name) / "out"
        self.fixtures = REPO_ROOT / "tests" / "fixtures" / "url_ingest"

    def fixture_fetcher(self, mapping: dict[str, Path]):
        def fetcher(url: str) -> tuple[int, str, bytes]:
            if url not in mapping:
                self.fail(f"unexpected fetch URL: {url}")
            return 200, "text/html; charset=utf-8", mapping[url].read_bytes()
        return fetcher

    def test_new_yorker_direct_transcript_link_writes_bundle(self) -> None:
        url = "https://www.newyorker.com/podcast/the-new-yorker-radio-hour/sam-altmans-trust-issues-at-openai"
        transcript_url = url + "/transcript"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher(
                {
                    url: self.fixtures / "new_yorker" / "page.html",
                    transcript_url: self.fixtures / "new_yorker" / "transcript.txt",
                }
            ),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("HOST: This is the New Yorker Radio Hour.", transcript)
        source = (episode_dir / "source" / "_source_input.txt").read_text(encoding="utf-8")
        self.assertIn("Sam Altman's Trust Issues at OpenAI", source)
        self.assertIn("Apple Podcasts", source)

    def test_tim_blog_direct_transcript_link_converts_html_transcript(self) -> None:
        url = "https://tim.blog/2026/04/29/elad-gil/"
        transcript_url = "https://tim.blog/2026/04/29/elad-gil-transcript/"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher(
                {
                    url: self.fixtures / "tim_blog" / "page.html",
                    transcript_url: self.fixtures / "tim_blog" / "transcript.html",
                }
            ),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("Tim Ferriss: Welcome back.", transcript)
        provenance = json.loads((episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider_id"], "tim_blog")
        self.assertEqual(provenance["chapters"][1], {"time": "10:15", "title": "Startup markets"})

    def test_99pi_direct_transcript_link_writes_bundle(self) -> None:
        url = "https://99percentinvisible.org/episode/666-enshittification/"
        transcript_url = "https://99percentinvisible.org/episode/666-enshittification/transcript"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher(
                {
                    url: self.fixtures / "99pi" / "page.html",
                    transcript_url: self.fixtures / "99pi" / "transcript.html",
                }
            ),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("ROMAN MARS: This is 99% Invisible.", transcript)
        provenance = json.loads((episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider_id"], "99pi")


class UrlIngestArticleTranscriptProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_root = Path(self.tmp.name) / "out"
        self.fixtures = REPO_ROOT / "tests" / "fixtures" / "url_ingest"

    def fixture_fetcher(self, mapping: dict[str, Path]):
        def fetcher(url: str) -> tuple[int, str, bytes]:
            if url not in mapping:
                self.fail(f"unexpected fetch URL: {url}")
            return 200, "text/html; charset=utf-8", mapping[url].read_bytes()
        return fetcher

    def test_foundmyfitness_inline_transcript_writes_bundle(self) -> None:
        url = "https://www.foundmyfitness.com/episodes/arthur-brooks"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher({url: self.fixtures / "foundmyfitness" / "page.html"}),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("Rhonda Patrick: Arthur, welcome.", transcript)
        provenance = json.loads((episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider_id"], "foundmyfitness")
        self.assertEqual(provenance["metadata"]["date"], "2026-03-24")
        self.assertEqual(provenance["chapters"][0], {"time": "00:00", "title": "Introduction"})
        source_input = (episode_dir / "source" / "_source_input.txt").read_text(encoding="utf-8")
        self.assertIn("Date: 2026-03-24", source_input)

    def test_conversations_with_tyler_inline_transcript_writes_bundle(self) -> None:
        url = "https://conversationswithtyler.com/episodes/craig-newmark/"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher(
                {url: self.fixtures / "conversations_with_tyler" / "page.html"}
            ),
        )

        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertTrue(transcript.startswith("TYLER COWEN: Craig, hello. Welcome."))
        self.assertIn("COWEN: Today I'm here with Craig Newmark.", transcript)
        self.assertIn("NEWMARK: Customer service is a big deal.", transcript)
        self.assertNotIn("Thanks to an anonymous listener", transcript)

        source_input = (episode_dir / "source" / "_source_input.txt").read_text(encoding="utf-8")
        self.assertIn("Podcast: Conversations with Tyler", source_input)
        self.assertIn("Host: Tyler Cowen", source_input)
        self.assertIn("Date: 2026-04-29", source_input)
        self.assertIn("https://www.youtube.com/watch?v=pZMuKkH92fo", source_input)
        self.assertNotIn("https://www.youtube.com/playlist", source_input)

        provenance = json.loads((episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider_id"], "conversations_with_tyler")
        self.assertEqual(provenance["metadata"]["date"], "2026-04-29")


class UrlIngestSubstackProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url_ingest = load_url_ingest()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_root = Path(self.tmp.name) / "out"
        self.fixtures = REPO_ROOT / "tests" / "fixtures" / "url_ingest"

    def fixture_fetcher(self, mapping: dict[str, Path]):
        def fetcher(url: str) -> tuple[int, str, bytes]:
            if url not in mapping:
                self.fail(f"unexpected fetch URL: {url}")
            return 200, "application/json" if "transcription.json" in url else "text/html", mapping[url].read_bytes()
        return fetcher

    def test_lenny_substack_transcription_json_writes_bundle(self) -> None:
        url = "https://www.lennysnewsletter.com/p/how-to-build-a-company-that-withstands"
        transcript_url = "https://substackcdn.com/video_upload/post/12345/abcdef/transcription.json?Expires=9999999999&Signature=test"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher(
                {
                    url: self.fixtures / "lenny_substack" / "page.html",
                    transcript_url: self.fixtures / "lenny_substack" / "transcription.json",
                }
            ),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("Lenny Rachitsky: Eric, welcome to the podcast.", transcript)
        self.assertIn("Eric Ries: Thanks for having me.", transcript)
        source = (episode_dir / "source" / "_source_input.txt").read_text(encoding="utf-8")
        self.assertIn("https://www.youtube.com/watch?v=PoJ1vTdHpks", source)
        provenance = json.loads((episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider_id"], "lenny_substack")
        self.assertEqual(provenance["chapters"][1], {"time": "06:45", "title": "Long-term company building"})
        turns = json.loads((episode_dir / "source" / "transcript.turns.json").read_text(encoding="utf-8"))
        self.assertEqual(turns[0]["speaker"], "Lenny Rachitsky")
        self.assertEqual(turns[1]["speaker"], "Eric Ries")
        self.assertGreater(turns[1]["word_count"], 0)

    def test_dwarkesh_substack_transcription_json_writes_bundle(self) -> None:
        # Dwarkesh runs on Substack under the custom domain dwarkesh.com, so it
        # routes through the shared `substack` ingest kind. Its diarization
        # ships `speaker_map: null` — the turns keep raw `SPEAKER_NN` labels for
        # resolve_speakers to map downstream, rather than resolving to names
        # here the way the Lenny fixture (which has a speaker_map) does.
        url = "https://www.dwarkesh.com/p/dylan-patel"
        transcript_url = "https://substackcdn.com/video_upload/post/190839917/abcdef/transcription.json?Expires=9999999999&Signature=test"
        episode_dir = self.url_ingest.ingest_url(
            url,
            self.out_root,
            fetcher=self.fixture_fetcher(
                {
                    url: self.fixtures / "dwarkesh" / "page.html",
                    transcript_url: self.fixtures / "dwarkesh" / "transcription.json",
                }
            ),
        )
        transcript = (episode_dir / "source" / "user-provided-transcript.txt").read_text(encoding="utf-8")
        self.assertIn("SPEAKER_00: Dylan is the CEO of SemiAnalysis.", transcript)
        self.assertIn("SPEAKER_01: So when you talk about the CapEx", transcript)
        source = (episode_dir / "source" / "_source_input.txt").read_text(encoding="utf-8")
        self.assertIn("Dylan Patel — Deep dive on the 3 big bottlenecks", source)
        self.assertIn("https://www.youtube.com/watch?v=mDG_Hx3BSUE", source)
        self.assertIn("Podcast: Dwarkesh Podcast", source)
        self.assertIn("Host: Dwarkesh Patel", source)
        provenance = json.loads((episode_dir / "working" / "_url_ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider_id"], "dwarkesh")
        self.assertEqual(provenance["chapters"][0], {"time": "00:00", "title": "Why an H100 is worth more today than 3 years ago"})
        turns = json.loads((episode_dir / "source" / "transcript.turns.json").read_text(encoding="utf-8"))
        self.assertEqual(turns[0]["speaker"], "SPEAKER_00")
        self.assertEqual(turns[1]["speaker"], "SPEAKER_01")
        self.assertGreater(turns[0]["word_count"], 0)

    def test_substack_duration_seconds_from_segment_list(self) -> None:
        # Real transcription.json is a flat list of {start, end, text, ...}
        # segments — the last segment's `end` is the wall-clock duration.
        segments = [
            {"start": 0.0, "end": 12.5, "text": "Hello"},
            {"start": 12.6, "end": 30.0, "text": "World"},
            {"start": 30.0, "end": 5112.374, "text": "Goodbye"},
        ]
        self.assertEqual(self.url_ingest._substack_duration_seconds(segments), 5112)

    def test_substack_duration_seconds_from_dict_envelope(self) -> None:
        envelope = {"segments": [{"start": 0.0, "end": 90.7, "text": "x"}]}
        self.assertEqual(self.url_ingest._substack_duration_seconds(envelope), 90)

    def test_substack_duration_seconds_handles_missing_payload(self) -> None:
        self.assertEqual(self.url_ingest._substack_duration_seconds([]), 0)
        self.assertEqual(self.url_ingest._substack_duration_seconds(None), 0)

    def test_substack_json_to_turns_merges_consecutive_same_speaker_segments(self) -> None:
        payload = [
            {"speaker": "SPEAKER_0", "start": 0, "end": 2, "text": "First sentence."},
            {"speaker": "SPEAKER_0", "start": 2, "end": 4, "text": "Second sentence."},
            {"speaker": "SPEAKER_1", "start": 4, "end": 6, "text": "Reply."},
        ]
        turns = self.url_ingest.substack_json_to_turns(
            payload,
            {"SPEAKER_0": "Eric Ries", "SPEAKER_1": "Lenny Rachitsky"},
        )
        self.assertEqual(
            [(t["speaker"], t["text"]) for t in turns],
            [
                ("Eric Ries", "First sentence. Second sentence."),
                ("Lenny Rachitsky", "Reply."),
            ],
        )
        self.assertEqual(turns[0]["start"], 0)
        self.assertEqual(turns[0]["end"], 4)

    def test_useful_links_now_keeps_entity_links(self) -> None:
        # Pre-fix behavior dropped everything except Apple/Spotify/YouTube and
        # left extract_entity_links with nothing to attach to terminology
        # entries — so the briefing's inspector showed zero outlinks. Now we
        # keep any HTTP(S) link that isn't obvious chrome.
        links = [
            {"text": "Apple", "url": "https://pod.link/show.apple?key=abc"},
            {"text": "Quibi", "url": "https://en.wikipedia.org/wiki/Quibi"},
            {"text": "Sign in", "url": "https://www.example.com/signin"},
            {"text": "Share on Twitter", "url": "https://twitter.com/intent/tweet?url=…"},
            {"text": "Eric Ries", "url": "https://en.wikipedia.org/wiki/Eric_Ries"},
        ]
        kept = self.url_ingest.useful_links(links)
        urls = [l["url"] for l in kept]
        self.assertIn("https://en.wikipedia.org/wiki/Quibi", urls)
        self.assertIn("https://en.wikipedia.org/wiki/Eric_Ries", urls)
        self.assertIn("https://pod.link/show.apple?key=abc", urls)
        # Sign-in chrome and share intents must still be filtered.
        self.assertNotIn("https://www.example.com/signin", urls)
        self.assertFalse(any("twitter.com/intent" in u for u in urls))

    def test_useful_links_drops_publisher_chrome(self) -> None:
        # Regression: Substack legal footer + Cloudflare email-obfuscation +
        # noscript fallback were leaking through as entity bullets, then
        # getting promoted to fake terminology entries ("Privacy",
        # "[email protected]", "turn on JavaScript", "Collection notice").
        links = [
            {"text": "Eric Ries", "url": "https://en.wikipedia.org/wiki/Eric_Ries"},
            {"text": "Privacy", "url": "https://substack.com/privacy"},
            {"text": "Terms", "url": "https://substack.com/tos"},
            {"text": "Collection notice", "url": "https://substack.com/ccpa#personal-data-collected"},
            {"text": "[email protected]", "url": "https://www.lennysnewsletter.com/cdn-cgi/l/email-protection#abc"},
            {"text": "turn on JavaScript", "url": "https://enable-javascript.com/"},
            {"text": "DMCA", "url": "https://substack.com/dmca"},
        ]
        kept_urls = [l["url"] for l in self.url_ingest.useful_links(links)]
        self.assertEqual(kept_urls, ["https://en.wikipedia.org/wiki/Eric_Ries"])

    def test_substack_speaker_map_handles_escaped_quote_embed(self) -> None:
        # In production Substack post pages, the speaker_map embed is a
        # JSON-encoded string inside an outer JSON, so the bytes are
        # `\"speaker_map\":{\"SPEAKER_0\":\"Eric Ries\"...}` rather than a plain
        # `"speaker_map": {...}` literal. The escaped form must be recognized
        # so the bundle gets human-named speakers instead of raw SPEAKER_NN.
        escaped = (
            r'something,\"speaker_map\":{\"SPEAKER_0\":\"Eric Ries\",'
            r'\"SPEAKER_1\":\"Lenny Rachitsky\"},more'
        )
        self.assertEqual(
            self.url_ingest.extract_substack_speaker_map(escaped),
            {"SPEAKER_0": "Eric Ries", "SPEAKER_1": "Lenny Rachitsky"},
        )

    def test_substack_speaker_map_still_handles_unescaped_embed(self) -> None:
        plain = 'foo, "speaker_map": {"SPEAKER_0": "A", "SPEAKER_1": "B"}, bar'
        self.assertEqual(
            self.url_ingest.extract_substack_speaker_map(plain),
            {"SPEAKER_0": "A", "SPEAKER_1": "B"},
        )

    def test_substack_prefers_escaped_signed_cdn_url(self) -> None:
        html_text = (
            r'{\"transcription\":{\"cdn_url\":\"'
            r'https://substackcdn.com/video_upload/post/1/abc/2/transcription.json?Expires=999&Signature=sig'
            r'\",\"transcript_url\":\"s3://substack-video/video_upload/post/1/abc/2/transcription.json\"}}'
        )
        self.assertEqual(
            self.url_ingest.find_substack_transcription_url(html_text, "https://example.com"),
            "https://substackcdn.com/video_upload/post/1/abc/2/transcription.json?Expires=999&Signature=sig",
        )


if __name__ == "__main__":
    unittest.main()
