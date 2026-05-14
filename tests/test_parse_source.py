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


if __name__ == "__main__":
    unittest.main()
