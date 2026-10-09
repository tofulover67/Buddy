"""Animation > Titles: real-font measuring (titles/measure.py, the bundled title fonts)
and the QPainter renderer (titles/render.py) - the picture lands where the composer
said, and nowhere else."""

import os
import unittest

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QGuiApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TitlesRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from pages.text_animator.titles import composer, measure, render
        cls.C, cls.M, cls.R = composer, measure, render

    @classmethod
    def tearDownClass(cls):
        cls.M.unload_bundled()

    def layout(self, text="3 secrets nobody tells you", look="hormozi", frame=(1920, 1080)):
        return self.C.compose(self.C.new_doc(text, look), frame, self.M.measure, self.M.metrics, ink=self.M.ink)

    def test_the_bundled_fonts_are_named_by_their_name_tables(self):
        faces = self.M.bundled_files()
        for face in (("Montserrat", "Black"), ("Montserrat", "ExtraBold"), ("Kaushan Script", "Regular")):
            self.assertIn(face, faces)

    def test_widths_scale_with_the_letter_size(self):
        font = ("Montserrat", "Black")
        w100 = self.M.measure("SECRETS", font, 100)
        self.assertGreater(w100, 300)
        self.assertAlmostEqual(self.M.measure("SECRETS", font, 250), 2.5 * w100, places=6)
        self.assertGreater(w100, self.M.measure("SECRETS", ("Montserrat", "ExtraBold"), 100))
        m = self.M.metrics(font)
        self.assertTrue(0.6 < m["cap"] < 0.8 and m["asc"] > m["cap"] and m["desc"] > 0, m)

    def test_ink_lands_in_the_block_and_nowhere_else(self):
        layout = self.layout()
        image, hits = self.R.render(layout, 480, transparent=True)
        self.assertEqual((image.width(), image.height()), (480, 270))
        self.assertEqual(hits, self.C.hit_boxes(layout))
        s = 480 / 1920
        b = layout["block"]
        pad = 0.1 * max(x["size"] for x in layout["boxes"]) * s     # the outline reaches past the letters
        inside = outside = 0
        for y in range(0, image.height(), 2):
            for x in range(0, image.width(), 2):
                if image.pixelColor(x, y).alpha() == 0:
                    continue
                if b["x"] * s - pad <= x <= (b["x"] + b["w"]) * s + pad and b["y"] * s - pad <= y <= (b["y"] + b["h"]) * s + pad:
                    inside += 1
                else:
                    outside += 1
        self.assertGreater(inside, 500)
        self.assertEqual(outside, 0)

    def test_the_key_is_drawn_in_its_colour(self):
        layout = self.layout()
        image, _hits = self.R.render(layout, 960, transparent=True)
        key = next(b for b in layout["boxes"] if b["role"] == "key")
        s = 960 / 1920
        yellow = QColor("#FFD93D")
        found = False
        for y in range(int(key["y"] * s), int((key["y"] + key["h"]) * s)):
            for x in range(int(key["x"] * s), int((key["x"] + key["w"]) * s)):
                c = image.pixelColor(x, y)
                if c.alpha() == 255 and abs(c.red() - yellow.red()) < 8 and abs(c.green() - yellow.green()) < 8 \
                        and abs(c.blue() - yellow.blue()) < 8:
                    found = True
                    break
            if found:
                break
        self.assertTrue(found)

    def test_every_look_draws(self):
        from pages.text_animator.titles import looks
        for look in looks.LOOKS:
            for frame in ((1920, 1080), (1080, 1920)):
                image, _hits = self.R.render(self.layout("Why you should never quit your job", look, frame), 270)
                self.assertFalse(image.isNull())

    def test_copies_hide_behind_the_word_until_they_fan_out(self):
        layout = self.layout("You need to STOP doing this", "echo")
        key = next(i for i, b in enumerate(layout["boxes"]) if b["role"] == "key")
        b = layout["boxes"][key]

        def ink_outside_the_word(spread):
            states = [None] * len(layout["boxes"])
            states[key] = {"spread": spread}
            image, _hits = self.R.render(layout, 960, transparent=True, states=states)
            s, n = 960 / 1920, 0
            for y in range(int((b["y"] - b["h"]) * s), int(b["y"] * s) - 2):      # the band above the word
                for x in range(int(b["x"] * s), int((b["x"] + b["w"]) * s)):
                    n += image.pixelColor(x, y).alpha() > 40
            return n

        self.assertEqual(ink_outside_the_word(0.0), 0)
        self.assertGreater(ink_outside_the_word(1.0), 200)

    def test_colours(self):
        self.assertEqual(self.R.colour("rgba(0,0,0,.5)").alpha(), 128)
        self.assertEqual(self.R.colour("#ff000080").alpha(), 128)
        self.assertEqual(self.R.colour("#39FF14").name(), "#39ff14")
        self.assertEqual(self.R.colour("nonsense", "#123456").name(), "#123456")


if __name__ == "__main__":
    unittest.main()
