#!/usr/bin/env python3
"""Unit tests for pregen's notes layering and render writing (no network, no
generation).

Run directly so pipeline/ is on sys.path for `import pregen`:
    python3 pipeline/pregen_test.py
"""
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import pregen


def write_json(d: Path, name: str, obj) -> Path:
    p = d / name
    p.write_text(json.dumps(obj))
    return p


def magenta_render(size=(200, 200), blob=(70, 130)) -> bytes:
    """A flat magenta ground with an opaque grey blob, i.e. what pregen gets back
    from the image API."""
    im = Image.new("RGB", size, (230, 40, 220))
    lo, hi = blob
    for y in range(lo, hi):
        for x in range(lo, hi):
            im.putpixel((x, y), (90, 90, 95))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class LoadSpeciesNotes(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_missing_file_is_empty(self):
        self.assertEqual(pregen.load_species_notes(self.d / "nope.json"), {})

    def test_no_paths_is_empty(self):
        self.assertEqual(pregen.load_species_notes(), {})

    def test_comment_keys_and_non_strings_dropped(self):
        p = write_json(self.d, "n.json", {
            "Turdus merula": "keep me",
            "_comment": "drop me",
            "Parus major": {"not": "a string"},
            "Erithacus rubecula": 42,
        })
        self.assertEqual(pregen.load_species_notes(p), {"Turdus merula": "keep me"})

    def test_later_file_overrides_earlier_per_key(self):
        bundled = write_json(self.d, "bundled.json", {"a": "from bundled", "b": "only bundled"})
        operator = write_json(self.d, "operator.json", {"a": "from operator", "c": "only operator"})
        self.assertEqual(
            pregen.load_species_notes(bundled, operator),
            {"a": "from operator", "b": "only bundled", "c": "only operator"},
        )

    def test_malformed_json_is_skipped_not_fatal(self):
        bad = self.d / "bad.json"
        bad.write_text("{ this is not json")
        good = write_json(self.d, "good.json", {"a": "kept"})
        self.assertEqual(pregen.load_species_notes(bad, good), {"a": "kept"})

    def test_non_object_json_is_skipped(self):
        arr = write_json(self.d, "arr.json", ["not", "an", "object"])
        good = write_json(self.d, "good.json", {"a": "kept"})
        self.assertEqual(pregen.load_species_notes(arr, good), {"a": "kept"})


class NoteFor(unittest.TestCase):
    def test_scientific_name_key(self):
        self.assertEqual(pregen.note_for({"Turdus merula": "n"}, "Turdus merula"), "n")

    def test_slug_key(self):
        self.assertEqual(pregen.note_for({"turdus-merula": "n"}, "Turdus merula"), "n")

    def test_scientific_name_wins_over_slug(self):
        notes = {"Turdus merula": "sci", "turdus-merula": "slug"}
        self.assertEqual(pregen.note_for(notes, "Turdus merula"), "sci")

    def test_miss_is_none(self):
        self.assertIsNone(pregen.note_for({"Parus major": "n"}, "Turdus merula"))


class WriteRender(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_without_matte_is_byte_identical(self):
        data = magenta_render()
        out = self.d / "bird.png"
        pregen.write_render(out, data, matte=False)
        self.assertEqual(out.read_bytes(), data)

    def test_with_matte_writes_transparent_rgba(self):
        out = self.d / "bird.png"
        pregen.write_render(out, magenta_render(), matte=True)
        im = Image.open(out)
        self.assertEqual(im.mode, "RGBA")
        lo, hi = im.getchannel("A").getextrema()
        self.assertEqual(lo, 0, "expected transparent pixels")
        self.assertEqual(hi, 255, "expected opaque subject pixels")

    def test_with_matte_crops_to_the_subject(self):
        out = self.d / "bird.png"
        pregen.write_render(out, magenta_render(size=(200, 200), blob=(70, 130)), matte=True)
        # 60px blob plus a 2% margin, nowhere near the 200px source.
        with Image.open(out) as im:
            self.assertLess(max(im.size), 100)

    def test_leaves_no_temporary_file_behind(self):
        pregen.write_render(self.d / "bird.png", magenta_render(), matte=True)
        self.assertEqual([p.name for p in self.d.iterdir()], ["bird.png"])

    def test_temporary_file_is_not_picked_up_as_art(self):
        # build_masks globs *.png; the temp name must not match it.
        out = self.d / "bird.png"
        self.assertFalse(out.with_name(out.name + ".tmp").match("*.png"))


if __name__ == "__main__":
    unittest.main()
