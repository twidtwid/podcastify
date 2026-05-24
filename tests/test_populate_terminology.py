from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "podcast-transformer" / "scripts"


def load_populate_terminology():
    # pipeline_config is imported by populate_terminology with a bare
    # `from pipeline_config import ...`, so the scripts dir must be importable.
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    path = SCRIPTS / "populate_terminology.py"
    spec = importlib.util.spec_from_file_location("populate_terminology", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry(term: str, **extra) -> dict:
    base = {"term": term, "category": "concept", "confidence": "primary",
            "notes": f"note for {term}"}
    base.update(extra)
    return base


class ParseTerminologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_populate_terminology()

    def test_well_formed_object_returns_terminology_list(self) -> None:
        raw = json.dumps({"terminology": [_entry("ASML"), _entry("TSMC")]})
        terms = self.mod._parse_terminology(raw)
        self.assertEqual([t["term"] for t in terms], ["ASML", "TSMC"])

    def test_markdown_fenced_output_is_unwrapped(self) -> None:
        raw = "```json\n" + json.dumps({"terminology": [_entry("EUV")]}) + "\n```"
        terms = self.mod._parse_terminology(raw)
        self.assertEqual([t["term"] for t in terms], ["EUV"])

    def test_truncated_array_salvages_complete_objects(self) -> None:
        # Simulates a num_predict cutoff: the JSON is cut off mid-object after
        # three complete entries. The salvage path keeps the three and drops
        # the dangling fragment instead of hard-failing the whole step.
        good = ",\n".join(json.dumps(_entry(t)) for t in ("ASML", "TSMC", "Nvidia"))
        raw = '{"terminology": [\n' + good + ',\n    {\n      "term": "synchrotr'
        terms = self.mod._parse_terminology(raw)
        self.assertEqual([t["term"] for t in terms], ["ASML", "TSMC", "Nvidia"])

    def test_non_dict_array_members_are_dropped(self) -> None:
        raw = '{"terminology": [{"term": "ASML"}, "stray-string", 42]}'
        terms = self.mod._parse_terminology(raw)
        self.assertEqual([t["term"] for t in terms], ["ASML"])

    def test_missing_terminology_array_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mod._parse_terminology('{"something_else": []}')


class CapTermsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_populate_terminology()

    def test_list_under_limit_is_unchanged(self) -> None:
        terms = [_entry(f"t{i}") for i in range(10)]
        self.assertEqual(self.mod._cap_terms(terms, limit=35), terms)

    def test_over_limit_is_trimmed_in_original_order(self) -> None:
        terms = [_entry(f"t{i}") for i in range(50)]
        capped = self.mod._cap_terms(terms, limit=35)
        self.assertEqual(len(capped), 35)
        self.assertEqual([t["term"] for t in capped],
                         [f"t{i}" for i in range(35)])

    def test_url_bearing_entries_are_never_dropped(self) -> None:
        # 5 publisher-linked entries scattered past the cutoff must survive
        # even though they sit beyond position `limit`.
        terms = [_entry(f"t{i}") for i in range(50)]
        linked_positions = [3, 20, 40, 45, 49]
        for pos in linked_positions:
            terms[pos]["url"] = f"https://example.com/{pos}"
        capped = self.mod._cap_terms(terms, limit=35)
        self.assertEqual(len(capped), 35)
        kept = {t["term"] for t in capped}
        for pos in linked_positions:
            self.assertIn(f"t{pos}", kept)
        # Original order is preserved across kept entries.
        positions = [int(t["term"][1:]) for t in capped]
        self.assertEqual(positions, sorted(positions))

    def test_default_limit_is_max_terms(self) -> None:
        terms = [_entry(f"t{i}") for i in range(self.mod.MAX_TERMS + 12)]
        self.assertEqual(len(self.mod._cap_terms(terms)), self.mod.MAX_TERMS)


if __name__ == "__main__":
    unittest.main()
