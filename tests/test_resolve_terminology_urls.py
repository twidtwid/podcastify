from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "podcast-transformer" / "scripts"
RESOLVE = SCRIPTS_DIR / "resolve_terminology_urls.py"


def load_resolver():
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("resolve_terminology_urls", RESOLVE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {RESOLVE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LooksLikeMatchTests(unittest.TestCase):
    def test_identical_terms_match(self) -> None:
        resolver = load_resolver()
        self.assertTrue(resolver._looks_like_match("Eric Ries", "Eric Ries"))

    def test_company_disambiguation_suffix_matches(self) -> None:
        resolver = load_resolver()
        self.assertTrue(resolver._looks_like_match("Anthropic", "Anthropic (company)"))

    def test_first_name_does_not_match_full_other_name(self) -> None:
        # Guard against accepting "Sam" → "Samuel L. Jackson".
        resolver = load_resolver()
        self.assertFalse(resolver._looks_like_match("Sam", "Samuel L. Jackson"))

    def test_accent_insensitive_on_identical_name(self) -> None:
        # Wikipedia returns ASCII titles for diacritic queries; the diacritic
        # form of the same name must still compare equal.
        resolver = load_resolver()
        self.assertTrue(resolver._looks_like_match("Éric Ries", "Eric Ries"))


class ResolveSidecarFlowTests(unittest.TestCase):
    """End-to-end against a synthetic sidecar with a mocked Wikipedia API."""

    def _make_sidecar(self, ep_dir: Path) -> Path:
        sidecar = {
            "verification": {
                "terminology": [
                    {"term": "Eric Ries", "category": "person", "url": ""},
                    {"term": "Anthropic", "category": "company", "url": ""},
                    {"term": "Spotify", "category": "company", "url": "https://open.spotify.com/"},
                    {"term": "AGI elicitation", "category": "concept", "url": ""},
                    {"term": "Disambiguation Person", "category": "person", "url": ""},
                ]
            }
        }
        (ep_dir / "final").mkdir(parents=True, exist_ok=True)
        path = ep_dir / "final" / "metadata.sidecar.json"
        path.write_text(json.dumps(sidecar), encoding="utf-8")
        return path

    def test_resolver_fills_missing_urls_and_skips_already_set(self) -> None:
        resolver = load_resolver()
        with tempfile.TemporaryDirectory() as tmp:
            ep_dir = Path(tmp)
            sidecar_path = self._make_sidecar(ep_dir)

            def fake_lookup(term: str) -> str:
                return {
                    "Eric Ries": "https://en.wikipedia.org/wiki/Eric_Ries",
                    "Anthropic": "https://en.wikipedia.org/wiki/Anthropic",
                    "Disambiguation Person": "",
                }.get(term, "")

            with mock.patch.object(resolver, "lookup_wikipedia", side_effect=fake_lookup):
                self.assertEqual(resolver.main([str(ep_dir)]), 0)

            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            terminology = sidecar["verification"]["terminology"]
            by_term = {t["term"]: t for t in terminology}
            self.assertEqual(
                by_term["Eric Ries"]["url"], "https://en.wikipedia.org/wiki/Eric_Ries"
            )
            self.assertEqual(
                by_term["Anthropic"]["url"], "https://en.wikipedia.org/wiki/Anthropic"
            )
            # Already-set URL must be preserved untouched.
            self.assertEqual(by_term["Spotify"]["url"], "https://open.spotify.com/")
            # Concept categories are skipped — only person/company/book/org/podcast.
            self.assertEqual(by_term["AGI elicitation"]["url"], "")
            # A disambiguation / no-match leaves the URL empty.
            self.assertEqual(by_term["Disambiguation Person"]["url"], "")


if __name__ == "__main__":
    unittest.main()
