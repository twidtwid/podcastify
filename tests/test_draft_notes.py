from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "podcast-transformer" / "scripts"
DRAFT_NOTES = SCRIPTS_DIR / "draft_notes.py"


def load_draft_notes():
    # draft_notes imports its sibling `pipeline_config`, so the scripts dir
    # has to be on sys.path before we exec the module.
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("draft_notes", DRAFT_NOTES)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {DRAFT_NOTES}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DraftThinnessCheckTests(unittest.TestCase):
    """The retry-once gate in draft_notes was claims-only and let drafts with
    8 claims + 4 takeaways through, surfacing as `WARN: only 4 takeaways` from
    notes_lint with no recovery. Both lists must be counted.

    These tests don't exercise the full Ollama call loop; they assert against
    `extract_json` over hand-crafted raw outputs that mimic the model's JSON.
    """

    THICK_CLAIMS = [{"topic": f"c{i}", "body": "..."} for i in range(8)]
    THIN_TAKEAWAYS = [f"takeaway {i}" for i in range(4)]
    THICK_TAKEAWAYS = [f"takeaway {i}" for i in range(8)]

    def test_thick_claims_thin_takeaways_is_below_threshold(self) -> None:
        draft_notes = load_draft_notes()
        raw = json.dumps({
            "bottom_line": "...",
            "takeaways": self.THIN_TAKEAWAYS,
            "claims": self.THICK_CLAIMS,
        })
        parsed = draft_notes.extract_json(raw)
        # The fixed retry gate uses these counts directly.
        n_takeaways = len(parsed.get("takeaways", []) or [])
        n_claims = len(parsed.get("claims", []) or [])
        self.assertEqual(n_takeaways, 4)
        self.assertEqual(n_claims, 8)
        self.assertFalse(
            n_takeaways >= 6 and n_claims >= 6,
            "4 takeaways must trip the retry gate even when claims are thick",
        )

    def test_both_thick_passes_threshold(self) -> None:
        draft_notes = load_draft_notes()
        raw = json.dumps({
            "bottom_line": "...",
            "takeaways": self.THICK_TAKEAWAYS,
            "claims": self.THICK_CLAIMS,
        })
        parsed = draft_notes.extract_json(raw)
        n_takeaways = len(parsed.get("takeaways", []) or [])
        n_claims = len(parsed.get("claims", []) or [])
        self.assertTrue(n_takeaways >= 6 and n_claims >= 6)


class KeywordQueriesRepairTests(unittest.TestCase):
    """gemma4 occasionally emits `keyword_queries: { "a", "b", "c" }` —
    object delimiters around what was meant to be a list of bare strings.
    json.loads fails with `Expecting ':' delimiter` and the whole draft is
    lost. Recover by promoting each bare string to a `"str": "str"` pair so
    downstream code that iterates `.items()` still works."""

    def test_keyword_queries_bare_strings_in_object_braces_recovers(self) -> None:
        draft_notes = load_draft_notes()
        raw = (
            '{\n'
            '  "bottom_line": "...",\n'
            '  "takeaways": ["t1", "t2"],\n'
            '  "claims": [],\n'
            '  "links": [],\n'
            '  "keyword_queries": {\n'
            '    "OpenAI governance structure",\n'
            '    "Sam Altman shift from alignment",\n'
            '    "The Blip internal investigation"\n'
            '  }\n'
            '}'
        )
        parsed = draft_notes.extract_json(raw)
        self.assertIn("keyword_queries", parsed)
        self.assertIsInstance(parsed["keyword_queries"], dict)
        self.assertIn("OpenAI governance structure", parsed["keyword_queries"])
        self.assertEqual(
            parsed["keyword_queries"]["OpenAI governance structure"],
            "OpenAI governance structure",
        )

    def test_well_formed_keyword_queries_pass_through_unchanged(self) -> None:
        draft_notes = load_draft_notes()
        raw = json.dumps({
            "bottom_line": "...",
            "takeaways": [],
            "claims": [],
            "keyword_queries": {"shortLabel": "the substring"},
        })
        parsed = draft_notes.extract_json(raw)
        self.assertEqual(parsed["keyword_queries"], {"shortLabel": "the substring"})


if __name__ == "__main__":
    unittest.main()
