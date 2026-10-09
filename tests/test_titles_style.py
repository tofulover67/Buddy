"""Animation > Titles: the person's changes on a look's styles (titles/looks.py) - a
colour fills the letters (a gradient gives way; a hollow word's strokes take it), an
outline width multiplies the look's, a face replaces the role's - the role's, then
the line's, then the word's own, the most particular winning; the composer measuring
in a role's new face; and all of it in the Text+ comp Place writes."""

import os
import re
import unittest

import _paths  # noqa: F401
from pages.text_animator.titles import composer as C
from pages.text_animator.titles import looks as L

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


def fake(text, font, size):
    return len(text) * size * 0.55


class RestyleTests(unittest.TestCase):
    def test_nothing_to_change_is_the_same_style(self):
        style = L.role_style(L.get("hormozi"), "key")
        self.assertIs(L.restyled(style), style)
        self.assertIs(L.restyled(style, colour="red", font="Anton", stroke=1), style)    # nothing valid

    def test_a_colour_fills_and_a_gradient_gives_way(self):
        accent = L.role_style(L.get("script"), "accent")
        out = L.restyled(accent, colour="#00ff00")
        self.assertEqual(out["fill"], "#00ff00")
        self.assertNotIn("gradient", out)
        self.assertIn("gradient", accent)                    # the look itself untouched

    def test_a_hollow_words_strokes_take_the_colour(self):
        normal = L.role_style(L.get("outline_fill"), "normal")
        out = L.restyled(normal, colour="#ff0000")
        self.assertEqual(out["fill"], "none")
        self.assertEqual({k["colour"] for k in out["strokes"]}, {"#ff0000"})

    def test_an_outline_width_and_a_face(self):
        key = L.role_style(L.get("hormozi"), "key")
        out = L.restyled(key, stroke=2.0, font=["Anton", "Regular"])
        self.assertAlmostEqual(out["strokes"][0]["w"], key["strokes"][0]["w"] * 2)
        self.assertEqual(out["font"], ("Anton", "Regular"))
        self.assertEqual(L.restyled(key, stroke=0)["strokes"][0]["w"], 0.0)

    def test_the_most_particular_change_wins(self):
        look = L.get("hormozi")
        doc = C.new_doc("a b c", "hormozi")
        doc["roleColour"] = {"normal": "#111111"}
        doc["lineColour"] = {"b#1": "#222222"}
        doc["over"]["colour"] = {"c#1": "#333333"}
        doc["roleStyle"] = {"normal": {"font": ["Anton", "Regular"]}}
        self.assertEqual(L.word_style(look, "normal", "a#1", "a#1", doc)["fill"], "#111111")      # the role's
        self.assertEqual(L.word_style(look, "normal", "b#1", "b#1", doc)["fill"], "#222222")      # its line's
        self.assertEqual(L.word_style(look, "normal", "c#1", "b#1", doc)["fill"], "#333333")      # its own
        self.assertEqual(L.word_style(look, "normal", "a#1", "a#1", doc)["font"], ("Anton", "Regular"))
        self.assertEqual(L.word_style(look, "key", "a#1", "a#1", doc)["fill"], "#FFD93D")         # another role: the look's

    def test_box_styles_follow_the_layout(self):
        doc = C.new_doc("3 secrets nobody tells you", "hormozi")
        doc["over"]["colour"] = {"tells#1": "#abcdef"}
        layout = C.compose(doc, (1920, 1080), fake)
        styles = dict(zip((b["id"] for b in layout["boxes"]), L.box_styles(layout, doc)))
        self.assertEqual(styles["tells#1"]["fill"], "#abcdef")
        self.assertEqual(L.box_styles(layout), [L.role_style(L.get("hormozi"), b["role"]) for b in layout["boxes"]])

    def test_the_composer_measures_in_a_roles_new_face(self):
        seen = set()

        def measure(text, font, size):
            seen.add(tuple(font))
            return fake(text, font, size)
        doc = C.new_doc("3 secrets nobody tells you", "hormozi")
        doc["roleStyle"] = {"normal": {"font": ["Anton", "Regular"]}}
        layout = C.compose(doc, (1920, 1080), measure)
        self.assertIn(("Anton", "Regular"), seen)
        self.assertTrue(all(tuple(b["font"]) == ("Anton", "Regular") for b in layout["boxes"] if b["role"] == "normal"))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class CompTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from pages.text_animator.titles import measure, textplus_comp
        cls.M, cls.T = measure, textplus_comp

    @classmethod
    def tearDownClass(cls):
        cls.M.unload_bundled()

    def test_the_persons_colour_and_outline_go_into_the_comp(self):
        doc = C.new_doc("3 secrets nobody tells you", "hormozi")
        doc["over"]["colour"] = {"nobody#1": "#0000ff"}
        doc["over"]["stroke"] = {"nobody#1": 2.0}
        layout = C.compose(doc, (1920, 1080), self.M.measure, self.M.metrics, ink=self.M.ink)
        plain = self.T.comp_text(layout, "t", 72)
        text = self.T.comp_text(layout, "t", 72, styles=L.box_styles(layout, doc))
        i = next(n for n, b in enumerate(layout["boxes"], 1) if b["id"] == "nobody#1")

        def inputs(comp):
            body = comp[comp.index(f"\t\tWord{i} = "):]
            body = body[:body.index("ViewInfo")]
            return {m.group(1): m.group(2) for m in re.finditer(r"(\w+) = Input \{ Value = ([^,]*),", body)}
        got, was = inputs(text), inputs(plain)
        self.assertEqual((float(got["Red1"]), float(got["Green1"]), float(got["Blue1"])), (0.0, 0.0, 1.0))
        self.assertAlmostEqual(float(got["Thickness2"]), 2 * float(was["Thickness2"]), places=4)


if __name__ == "__main__":
    unittest.main()
