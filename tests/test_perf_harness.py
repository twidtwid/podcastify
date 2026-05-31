from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "podcast-transformer" / "scripts"
PERF_HARNESS = SCRIPTS_DIR / "perf_harness.py"


def load_perf_harness():
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("perf_harness", PERF_HARNESS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {PERF_HARNESS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PodcastPerfHarnessTests(unittest.TestCase):
    def test_complex_lenny_fixture_hits_fast_path_and_contracts(self) -> None:
        harness = load_perf_harness()
        report = harness.run_once()

        # Byline metadata no longer skips the transcript model — that skip was the
        # bug that mislabeled network sub-show hosts. The byline still supplies the
        # fallback host/guest when the model hasn't run.
        self.assertFalse(report["preflight"]["resolve_speakers_skipped"])
        self.assertEqual(report["preflight"]["host"], "Lenny Rachitsky")
        self.assertEqual(report["preflight"]["guests"], ["Eric Ries"])
        self.assertGreaterEqual(report["fixture"]["turns"], 200)
        self.assertEqual(report["validate_rc"], 0)
        self.assertTrue(report["source_document"]["clean"], report["source_document"]["violations"])
        self.assertTrue(report["primitives"]["clean"], report["primitives"]["missing"])
        self.assertFalse(harness.regressions(report))


if __name__ == "__main__":
    unittest.main()
