"""The pets' skins (pages/pets/skins.py) - all free - and the Pets page
wearing and offering them, with its Donate button."""

import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.pets import behaviour, skins

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from pages.pets import page as page_mod
    from test_pets import Host
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class SkinTests(unittest.TestCase):
    def test_every_species_has_classic_its_colour_and_the_accessories(self):
        for species in behaviour.SPECIES:
            with self.subTest(species):
                ids = [sid for sid, _name in skins.skins_for(species)]
                self.assertEqual(ids[0], "classic")
                self.assertEqual(ids[1], skins.COLOURS[species][0])
                self.assertEqual(ids[2], skins.CUSTOM)
                self.assertEqual(ids[3:], [sid for sid, _name in skins.ACCESSORIES])

    def test_every_colour_is_drawn(self):
        sprites = (_paths.APP / "pages" / "pets" / "web" / "sprites.js").read_text(encoding="utf-8")
        for species, (colour, _name) in skins.COLOURS.items():
            with self.subTest(species):
                self.assertRegex(sprites, rf"{species}: \{{{colour}:")
        for sid, _name in skins.ACCESSORIES:
            self.assertIn(f'kind === "{sid}"', sprites)

    def test_what_may_be_worn(self):
        self.assertTrue(skins.is_skin("blob", "classic"))
        self.assertTrue(skins.is_skin("blob", "mint"))
        self.assertFalse(skins.is_skin("blob", "gold"))                 # the robot's, not the blob's
        self.assertTrue(skins.is_skin("snappy", "crown"))
        self.assertFalse(skins.is_skin("snappy", "halo"))

    def test_saved_choices_are_made_safe(self):
        self.assertEqual(skins.clean_skins(None), {})
        self.assertEqual(skins.clean_skins({"blob": "mint", "robot": "classic", "snappy": "mint", "cat": "crown",
                                            "poof": 3, "blaze": "crown"}),
                         {"blob": "mint", "blaze": "crown"})
        self.assertEqual(skins.clean_colours({"blob": "#abcdef", "robot": "red", "cat": "#000000"}), {"blob": "#ABCDEF"})


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageSkinTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def make(self, saved=None):
        self.host = Host(os.path.join(self._tmp.name, "achievements.json"), saved)
        pets = page_mod.Pets(self.host)
        self.addCleanup(pets._close_window)
        return pets

    @staticmethod
    def field(pets, key):
        return next(f for f in pets.settings_pages()[0]["fields"] if f.get("key") == key)

    def test_a_skin_is_worn_and_remembered_per_pet(self):
        pets = self.make()
        row = self.field(pets, "skin")
        self.assertEqual((row["kind"], row["species"]), ("skins", "blob"))
        self.assertEqual([(k["id"], k["chosen"]) for k in row["items"]][:2], [("classic", True), ("mint", False)])
        self.assertTrue(pets.set_value("skin", "tophat"))
        self.assertEqual(pets.skin_of("blob"), "tophat")
        self.assertEqual(self.host.tools["pets"]["skins"], {"blob": "tophat"})
        self.assertFalse(pets.set_value("skin", "gold"))                 # not the blob's
        self.assertTrue(pets.set_value("skin", "classic"))
        self.assertEqual(self.host.tools["pets"]["skins"], {})
        pets.set_value("skin", "mint")
        pets.show(True)
        self.assertEqual(pets.window.values["skins"]["blob"], "mint")

    def test_a_colour_of_your_own_is_worn_as_picked(self):
        pets = self.make()
        self.assertFalse(pets.set_value("colour", "orange"))              # not a colour
        self.assertTrue(pets.set_value("colour", "#ff8800"))
        self.assertEqual(pets.colour_of("blob"), "#FF8800")
        self.assertEqual(pets.skin_of("blob"), "custom")                  # picking a colour wears it
        self.assertEqual(self.host.tools["pets"]["colours"], {"blob": "#FF8800"})
        self.assertFalse(pets.set_value("colour", "#ff8800"))             # the same again: nothing
        row = self.field(pets, "skin")
        self.assertEqual(row["colour"], "#FF8800")
        self.assertTrue(next(k for k in row["items"] if k["id"] == "custom")["chosen"])
        pets.show(True)
        self.assertEqual(pets.window.values["colours"]["blob"], "#FF8800")

    def test_the_page_offers_a_donation_when_there_is_a_page_for_it(self):
        pets = self.make()

        def actions():
            return [b["action"] for f in pets.settings_pages()[0]["fields"] if f["kind"] == "buttons" for b in f["items"]]

        self.assertNotIn("donate", actions())                              # no page set: no button
        with mock.patch.object(page_mod, "DONATE_URL", "https://buddy.example.com/donate"), \
                mock.patch.object(page_mod.QDesktopServices, "openUrl") as opened:
            self.assertIn("donate", actions())
            pets.on_settings_action("donate", None)
            self.assertEqual(opened.call_args[0][0].toString(), "https://buddy.example.com/donate")

    def test_an_old_licence_file_is_tidied_away(self):
        from core import tool_dirs
        path = tool_dirs.file_path("pets", "licence.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}")
        self.make()
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
