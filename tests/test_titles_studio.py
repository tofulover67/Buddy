"""Animation > Titles: the tab's Python side (titles/studio.py) against a fake page - the
title kept and cleaned (every per-word change the page can send), a change answered
with the layout as numbers (the letters' outlines, the styles with the person's
changes on, the clock), the Suggestions and the Looks rail on a message of their own
(the rail waiting for a pause in the typing), the settings written a moment after the
last change, the person's own looks, and Place handed to the worker."""

import os
import time
import unittest

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class Settings(dict):
    saves = 0

    def save(self):
        self.saves += 1


class Page:
    def __init__(self):
        self.settings = Settings()
        self.events = []

    def emit(self, name, payload):
        self.events.append((name, payload))

    def last(self, name):
        return next(p for n, p in reversed(self.events) if n == name)

    def count(self, name):
        return sum(1 for n, _p in self.events if n == name)


def pump(seconds):
    """Lets Qt's timers run (the side message, the save, the Looks rail's wait)."""
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from pages.text_animator.titles import measure, studio
        cls.M, cls.S = measure, studio

    @classmethod
    def tearDownClass(cls):
        cls.M.unload_bundled()

    def setUp(self):
        self.page = Page()
        self.studio = self.S.Studio(self.page)

    def tearDown(self):
        self.studio.close()

    def test_a_kept_title_is_cleaned(self):
        title = self.S.clean({"text": "x" * 500, "look": "nope", "lines": 9, "pos": "zz", "anim": "spin",
                              "stagger": 4, "bw": 0.01, "seconds": 99, "exit": {"on": True, "bogus": 1}})
        self.assertEqual(len(title["text"]), 200)
        self.assertEqual((title["look"], title["lines"], title["pos"], title["anim"]), ("hormozi", "auto", "look", "look"))
        self.assertEqual((title["stagger"], title["bw"], title["seconds"]), (0.5, 0.3, 10.0))
        self.assertEqual(title["exit"], {"on": True, "style": "reverse", "order": "same", "dir": None})
        self.assertFalse(title["pulse"])
        self.assertTrue(self.S.clean({"pulse": True})["pulse"])
        self.assertFalse(self.S.clean({"pulse": "yes"})["pulse"])

    def test_nothing_kept_still_has_every_map(self):
        title = self.S.clean(None)
        for name in ("roles", "size", "offset", "colour", "brk", "anim", "rot", "stroke", "len", "dir", "curves"):
            self.assertEqual(title["over"][name], {}, name)
        self.assertEqual((title["times"], title["roleColour"], title["roleStyle"], title["curves"]), ({}, {}, {}, None))

    def test_the_per_word_changes_are_made_safe(self):
        title = self.S.clean({
            "over": {"size": {"a#1": 99, "b#1": "big"}, "offset": {"a#1": [0.5, -0.25], "b#1": [1]},
                     "colour": {"a#1": "#ff0000", "b#1": "red"}, "roles": {"a#1": "key", "b#1": "boss"},
                     "rot": {"a#1": 400}, "stroke": {"a#1": -1}, "len": {"a#1": 0}, "dir": {"a#1": "l", "b#1": "x"},
                     "brk": {"a#1": True, "b#1": 1}, "anim": {"a#1": "slam", "b#1": "spin"},
                     "curves": {"a#1": {"ch": {"scale": [{"t": 0, "v": 2}, {"t": 1, "v": 1}]}}, "b#1": {"ch": {}}}},
            "roleColour": {"key": "#00ff00", "boss": "#00ff00", "normal": "green"},
            "roleStyle": {"key": {"font": ["Anton", "Regular"]}, "normal": {"font": "Anton"}},
            "custom": True, "gpos": [0.3, 9], "times": {"a#1": -2, "b#1": 4},
            "exitTimes": {"a#1": 2.5, "b#1": "soon", 7: 1},
        })
        self.assertEqual(title["exitTimes"], {"a#1": 2.5})
        o = title["over"]
        self.assertEqual(o["size"], {"a#1": 4.0})
        self.assertEqual(o["offset"], {"a#1": [0.5, -0.25]})
        self.assertEqual((o["colour"], o["roles"], o["rot"], o["stroke"]), ({"a#1": "#ff0000"}, {"a#1": "key"}, {"a#1": 180.0}, {"a#1": 0.0}))
        self.assertAlmostEqual(o["len"]["a#1"], 1 / 60)
        self.assertEqual(o["exitLen"], {})
        self.assertEqual((o["dir"], o["brk"], o["anim"]), ({"a#1": "l"}, {"a#1": True}, {"a#1": "slam"}))
        self.assertEqual(list(o["curves"]), ["a#1"])
        self.assertEqual((title["roleColour"], title["roleStyle"]), ({"key": "#00ff00"}, {"key": {"font": ["Anton", "Regular"]}}))
        self.assertEqual((title["custom"], title["gpos"]), (True, [0.3, 1.25]))
        self.assertEqual(title["times"], {"a#1": 0.0, "b#1": 4.0})
        self.assertFalse(self.S.clean({"custom": True})["custom"])          # nowhere to put it: not custom

    def test_the_view_is_numbers_the_page_draws(self):
        self.studio.view(gallery=True)
        v = self.page.last("ti")
        self.assertNotIn("preview", v)
        self.assertEqual(v["frame"], [1920, 1080])
        self.assertEqual(len(v["boxes"]), 5)
        for box in v["boxes"]:
            self.assertTrue(box["d"].startswith("M"))                   # the letters' outline, as SVG path data
            self.assertEqual(len(box["ink"]), 4)
        self.assertEqual(len(v["styles"]), 5)
        self.assertEqual(len(v["clock"]["words"]), 5)
        self.assertEqual(set(v["curves"]), {"pop", "slam", "slide", "blur", "type", "fade"})
        self.assertEqual({t["id"]: t["role"] for t in v["tokens"]}["3#1"], "key")
        self.assertTrue(v["lines"] and v["alternates"] and v["faces"] and v["looks"])
        self.assertFalse(v["pulses"])                    # hormozi has no echoes to pulse
        self.assertEqual(v["ui"], {"dock": True, "back": "dark", "units": "s", "askReset": True, "expand": None,
                                   "guides": {"safe": True, "thirds": False}})

    def test_the_side_message_has_the_suggestions_and_the_looks(self):
        from pages.text_animator.titles import looks
        self.studio.view(gallery=True)
        pump(0.1)
        side = self.page.last("ti_side")
        self.assertEqual(set(side["gallery"]), set(looks.LOOKS))
        self.assertTrue(all(g["boxes"] and g["boxes"][0]["d"] for g in side["gallery"].values()))
        self.assertTrue(side["alternates"])
        self.assertEqual(side["alternates"][0]["count"], self.page.last("ti")["alternates"][0]["lines"])

    def test_typing_waits_for_a_pause_before_the_looks_are_laid_out_again(self):
        self.studio.view(gallery=True)
        pump(0.1)
        self.page.events.clear()
        for text in ("Stop", "Stop wasting", "Stop wasting money"):
            self.studio.on_change({"change": {"text": text}})
            pump(0.02)
        self.assertTrue(all("gallery" not in p for n, p in self.page.events if n == "ti_side"))
        pump(self.S.GALLERY_DELAY_MS / 1000 + 0.2)
        side = self.page.last("ti_side")
        self.assertIn("gallery", side)
        self.assertEqual(self.page.count("ti_side"), 4)    # one per letter's worth (alternates), then the rail
        self.studio.on_change({"change": {"stagger": 0.2}})
        pump(0.05)
        self.assertNotIn("gallery", self.page.last("ti_side"))

    def test_a_change_is_kept_answered_and_saved_after_a_pause(self):
        self.studio.on_change({"change": {"text": "Stop wasting money on gear", "look": "script"}, "seq": 7})
        self.assertEqual(self.page.settings["titles"]["text"], "Stop wasting money on gear")
        v = self.page.last("ti")
        self.assertEqual((v["title"]["look"], v["seq"]), ("script", 7))
        self.studio.on_change({"change": {"stagger": 0.2}, "seq": 8})
        self.assertEqual(self.page.settings.saves, 0)
        pump(self.S.SAVE_DELAY_MS / 1000 + 0.2)
        self.assertEqual(self.page.settings.saves, 1)      # once, for both

    def test_the_persons_changes_are_in_the_layout_styles_and_clock(self):
        over = self.studio.title["over"]
        over.update({"colour": {"nobody#1": "#ff0000"}, "size": {"3#1": 1.5}, "rot": {"tells#1": 10},
                     "len": {"you#1": 0.5}, "curves": {"secrets#1": {"len": 0.4, "ch": {"rot": [{"t": 0, "v": -30}, {"t": 1, "v": 0}]}}}})
        self.studio.on_change({"change": {"over": over, "times": {"tells#1": 0.9}}})
        v = self.page.last("ti")
        boxes = {b["id"]: b for b in v["boxes"]}
        styles = {b["id"]: s for b, s in zip(v["boxes"], v["styles"])}
        words = {w["id"]: w for w in v["clock"]["words"]}
        self.assertEqual(styles["nobody#1"]["fill"], "#ff0000")
        self.assertEqual(boxes["tells#1"]["rot"], 10.0)
        self.assertEqual(words["you#1"]["len"], 0.5)
        self.assertEqual((words["secrets#1"]["kind"], words["secrets#1"]["len"], words["secrets#1"]["drawn"]), ("custom", 0.4, "word"))
        self.assertEqual(words["tells#1"]["start"], 0.9)
        self.studio.on_change({"change": {"over": {**over, "size": {}}}})
        self.assertLess(self.page.last("ti")["boxes"][0]["size"], boxes["3#1"]["size"])

    def test_a_new_entrance_clears_the_titles_own_curve(self):
        curve = {"ch": {"scale": [{"t": 0, "v": 0.2}, {"t": 1, "v": 1}]}}
        self.studio.on_change({"change": {"curves": curve}})
        self.assertIsNotNone(self.page.settings["titles"]["curves"])
        self.assertEqual(self.page.last("ti")["clock"]["words"][1]["kind"], "custom")
        self.studio.on_change({"change": {"anim": "slide"}})
        self.assertIsNone(self.page.settings["titles"]["curves"])

    def test_a_suggestion_sticks_until_the_text_or_the_breaks_change(self):
        self.studio.view()
        alt = self.page.last("ti")["alternates"][0]
        self.studio.on_change({"change": {"forced": alt["key"]}})
        self.assertEqual(self.page.last("ti")["key"], alt["key"])
        self.studio.on_change({"change": {"text": "Something else entirely"}})
        self.assertIsNone(self.page.settings["titles"]["forced"])
        self.studio.on_change({"change": {"forced": self.page.last("ti")["alternates"][0]["key"]}})
        over = self.studio.title["over"]
        self.studio.on_change({"change": {"over": {**over, "brk": {"else#1": True}}}})
        self.assertIsNone(self.page.settings["titles"]["forced"])

    def test_the_timeline_shape_is_the_layouts(self):
        self.studio.set_timeline((1080, 1920), 30)
        pump(0.1)
        v = self.page.last("ti")
        self.assertEqual((v["frame"], v["fps"]), ([1080, 1920], 30.0))
        self.assertIn("gallery", self.page.last("ti_side"))

    def test_reset_all_keeps_the_words_and_the_look(self):
        self.studio.view()
        d = self.page.last("ti")["defaults"]
        self.assertNotIn("text", d)
        self.assertNotIn("look", d)
        self.assertEqual((d["seconds"], d["times"], d["exitTimes"], d["curves"], d["custom"]), (3.0, {}, {}, None, False))
        self.assertEqual(set(d["over"]), set(self.S._OVER))
        self.assertTrue(all(m == {} for m in d["over"].values()))

    def test_the_tabs_layout_is_kept(self):
        self.studio.on_ui({"dock": False, "back": "checker", "units": "f", "askReset": False, "expand": "cv", "guides": {"thirds": True}, "junk": 1})
        self.assertEqual(self.page.settings["titles_ui"], {"dock": False, "back": "checker", "units": "f", "askReset": False, "expand": "cv",
                                                          "guides": {"safe": True, "thirds": True}})
        self.studio.on_ui({"back": "neon", "units": "ms"})   # none it knows: all as they were
        ui = self.page.settings["titles_ui"]
        self.assertEqual((ui["back"], ui["units"]), ("checker", "f"))

    def test_looks_of_your_own(self):
        over = self.studio.title["over"]
        self.studio.on_change({"change": {"look": "echo", "over": {**over, "colour": {"3#1": "#123456"}}, "seconds": 4.0}})
        self.studio.on_save_look({})
        mine = self.page.settings["titles_mine"]
        self.assertEqual([m["name"] for m in mine], ["My look 1"])
        self.assertNotIn("text", mine[0]["doc"])
        self.assertEqual(self.page.last("ti")["mine"], ["My look 1"])
        pump(0.1)
        self.assertEqual(len(self.page.last("ti_side")["mine"]), 1)
        # Back to a plain look, new words - then the saved one again: its look, changes and length.
        self.studio.on_change({"change": {"look": "hormozi", "over": {**over, "colour": {}}, "seconds": 2.0, "text": "New words here"}})
        self.studio.on_use_look({"index": 0})
        title = self.page.settings["titles"]
        self.assertEqual((title["look"], title["seconds"], title["text"]), ("echo", 4.0, "New words here"))
        self.assertEqual(title["over"]["colour"], {"3#1": "#123456"})
        self.studio.on_use_look({"index": 5})                 # no such look: nothing happens
        self.studio.on_forget_look({"index": 0})
        self.assertEqual(self.page.settings["titles_mine"], [])

    def test_place_is_a_worker_job(self):
        self.assertTrue(callable(self.studio.place_job()))

    @unittest.skipUnless(os.environ.get("BUDDY_TI_DUMP"), "set BUDDY_TI_DUMP=path to write a 'ti' message")
    def test_dump_a_message_for_the_browser(self):
        import json
        self.studio.view(gallery=True)
        with open(os.environ["BUDDY_TI_DUMP"], "w", encoding="utf-8") as fh:
            fh.write("window.TI_PAYLOAD = " + json.dumps(self.page.last("ti")) + ";")


if __name__ == "__main__":
    unittest.main()
