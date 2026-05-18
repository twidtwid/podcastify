from __future__ import annotations

import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OG_CARD = REPO_ROOT / "podcast-transformer" / "scripts" / "og_card.py"


def load_og_card():
    spec = importlib.util.spec_from_file_location("og_card", OG_CARD)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {OG_CARD}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OgCardHardeningTests(unittest.TestCase):
    def test_truncate_returns_a_fitting_prefix(self) -> None:
        from PIL import Image, ImageDraw

        og = load_og_card()
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        font = og._font(og.SERIF_REG, 36)
        text = "word " * 400
        result = og._truncate(draw, text, font, 500)
        self.assertTrue(result.endswith("…"))
        self.assertLessEqual(og._text_w(draw, result, font), 500)
        # Short text that already fits is returned unchanged.
        self.assertEqual(og._truncate(draw, "short", font, 100000), "short")

    def test_generate_og_card_tolerates_nonstring_and_missing_fields(self) -> None:
        og = load_og_card()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "og-card.png"
            # episode is not a dict; guests contains non-strings; title numeric.
            og.generate_og_card({"episode": ["bogus"]}, out)
            self.assertTrue(out.exists())
            out2 = Path(tmp) / "og2.png"
            og.generate_og_card(
                {"episode": {"short_title": 12345, "guests": [None, 7], "hosts": None}},
                out2,
            )
            self.assertTrue(out2.exists())

    def test_pathological_input_does_not_hang(self) -> None:
        og = load_og_card()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "og-card.png"
            package = {
                "episode": {
                    "short_title": "A" * 500_000,
                    "podcast_title": "B" * 500_000,
                    "guests": ["C" * 100_000] * 50,
                }
            }
            start = time.monotonic()
            og.generate_og_card(package, out)
            elapsed = time.monotonic() - start
            self.assertTrue(out.exists())
            # Clamps + binary-search truncate keep this well under a second;
            # generous ceiling guards against the O(n^2) regression.
            self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()
