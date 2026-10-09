"""The Text+ font picker's faces (font_utils.py) and the picked font as kept in the
settings (options.py): Resolve's own (family, style) pairs, and an older Buddy's one-name
font still read. No Qt, no Resolve."""

import unittest

import _paths  # noqa: F401
from pages.text_animator import font_utils as fu
from pages.text_animator.options import Options

# The shape of Fusion's FontManager.GetFontList(): {family: {style: file}} - these names
# as Studio 21.1 lists them on Windows 11 (2026-10-05).
FONT_LIST = {
    "Arial": {"Regular": "arial.ttf", "Bold": "arialbd.ttf", "Black": "ariblk.ttf"},
    "Franklin Gothic Medium": {"Regular": "framd.ttf", "Italic": "framdit.ttf"},
    "Segoe UI": {"Regular": "segoeui.ttf", "Semilight": "segoeuisl.ttf"},
    "Sitka": {"Text": "Sitka.ttc", "Text Bold": "Sitka.ttc"},
}


class Settings(dict):
    def save(self):
        pass


class FacesTests(unittest.TestCase):
    def test_resolves_faces_are_labelled_family_then_style(self):
        faces = fu.resolve_faces(FONT_LIST)
        self.assertEqual(faces["Arial Regular"], ("Arial", "Regular"))
        self.assertEqual(faces["Arial Black"], ("Arial", "Black"))
        self.assertEqual(faces["Sitka Text Bold"], ("Sitka", "Text Bold"))
        self.assertEqual(faces["Franklin Gothic Medium Italic"], ("Franklin Gothic Medium", "Italic"))
        self.assertEqual(len(faces), 9)
        self.assertEqual(fu.resolve_faces(None), {})
        self.assertEqual(fu.resolve_faces({"Odd": None}), {})

    def test_an_older_buddys_name_finds_resolves_face(self):
        faces = fu.resolve_faces(FONT_LIST)
        self.assertEqual(fu.find_face(faces, "Segoe UI Semilight"), ("Segoe UI", "Semilight"))
        self.assertEqual(fu.find_face(faces, "Franklin Gothic Medium"), ("Franklin Gothic Medium", "Regular"))
        self.assertEqual(fu.find_face(faces, "Sitka Text"), ("Sitka", "Text"))
        self.assertEqual(fu.find_face(faces, "Arial"), ("Arial", "Regular"))
        self.assertEqual(fu.find_face(faces, "Segoe UI SemiLight"), ("Segoe UI", "Semilight"))
        self.assertIsNone(fu.find_face(faces, "Nope"))

    def test_the_fallback_guesses_the_style_from_the_name(self):
        faces = fu.guessed_faces(["Arial", "Arial Bold Italic", "Segoe UI Semilight"])
        self.assertEqual(faces["Arial Regular"], ("Arial", "Regular"))
        self.assertEqual(faces["Arial Bold Italic"], ("Arial", "Bold Italic"))
        # Not a weight word the guess knows: the whole name is the family.
        self.assertEqual(faces["Segoe UI Semilight Regular"], ("Segoe UI Semilight", "Regular"))


class PickedFontTests(unittest.TestCase):
    def test_a_face_is_kept_as_family_and_style(self):
        o = Options(Settings())
        self.assertEqual(o.font_face, ("Arial", "Regular"))
        self.assertTrue(o.set("font_face", ("Sitka", "Text Bold")))
        self.assertEqual((o.font_face, o.font_name), (("Sitka", "Text Bold"), "Sitka"))
        self.assertFalse(o.font_picked_by_name)
        self.assertEqual(o.view()["font_style"], "Text Bold")
        for bad in ("Sitka", ("Sitka",), ("Sitka", ""), (1, 2)):
            self.assertFalse(o.set("font_face", bad))

    def test_an_older_buddys_one_name_is_split(self):
        o = Options(Settings(selected_font_name="Arial Bold"))
        self.assertEqual(o.font_face, ("Arial", "Bold"))
        self.assertTrue(o.font_picked_by_name)
        o.set("font_face", ("Arial", "Black"))
        o.set("font_name", "Verdana Italic")        # one name again: the style goes with it
        self.assertEqual(o.font_face, ("Verdana", "Italic"))


if __name__ == "__main__":
    unittest.main()
