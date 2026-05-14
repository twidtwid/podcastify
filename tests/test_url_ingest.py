from __future__ import annotations

import importlib.util
import json
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


if __name__ == "__main__":
    unittest.main()
