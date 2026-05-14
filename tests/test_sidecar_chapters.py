from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_CHAPTERS = REPO_ROOT / "podcast-transformer" / "scripts" / "sidecar_chapters.py"


def load_sidecar_chapters():
    spec = importlib.util.spec_from_file_location("sidecar_chapters", SIDECAR_CHAPTERS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {SIDECAR_CHAPTERS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SidecarChaptersTests(unittest.TestCase):
    def test_no_parsed_chapters_is_informational(self) -> None:
        sidecar_chapters = load_sidecar_chapters()
        with tempfile.TemporaryDirectory() as tmp:
            episode_dir = Path(tmp)
            (episode_dir / "working").mkdir()
            (episode_dir / "final").mkdir()
            (episode_dir / "working" / "_parsed.json").write_text(
                json.dumps({"chapters": []}),
                encoding="utf-8",
            )
            (episode_dir / "final" / "metadata.sidecar.json").write_text(
                json.dumps({"episode": {"chapters": []}}),
                encoding="utf-8",
            )
            stderr = StringIO()
            with redirect_stderr(stderr):
                rc = sidecar_chapters.main([str(episode_dir)])
        self.assertEqual(rc, 0)
        self.assertIn("INFO: parsed JSON had no chapters", stderr.getvalue())
        self.assertNotIn("WARN:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
