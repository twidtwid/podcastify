from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
URL_INGEST = REPO_ROOT / "podcast-transformer" / "scripts" / "url_ingest.py"
PROVIDERS = REPO_ROOT / "podcast-transformer" / "providers.json"


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
            {"lenny_substack", "new_yorker", "foundmyfitness", "tim_blog", "99pi"},
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


if __name__ == "__main__":
    unittest.main()
