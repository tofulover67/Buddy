"""Interactions (pages/interactions/): the rule the shapes answer by (model.py), the
comp that runs it in Fusion (comp.py), the job that puts it on a (fake) timeline
(place.add), that the tool is wired in and translated, and the page itself."""

import ast
import os
import re
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from unittest import mock

import _paths  # noqa: F401
from _child_reader import ChildReader, load_without_qt
from _fake_graphics import Controller, Project, studio

from core import i18n, more_tools, tool_dirs
from core.fusion_place import PlaceError
from core.resolve_bridge import ResolveConnectionError
from core.tools_kb import get_tool
from pages.interactions import comp, model as M, place

FRAME = (1920, 1080)
ROOT = _paths.APP / "pages" / "interactions"
JOBS = {"data_graphics.read": ("pages.data_graphics.place", "read"),
        "interactions.add": ("pages.interactions.place", "add")}

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAVE_WEB = True
except ImportError:
    HAVE_WEB = False


def one_button(**more):
    """A button in the middle and a cursor that crosses it left to right in two
    seconds, at 10 frames a second: on it from frame 9 to frame 11."""
    return M.clean({"seconds": 2, "elements": [{"kind": "button", "x": 50, "y": 50, "w": 10, "h": 20, "label": "Go"}],
                    "path": [[0, 0, 50], [2, 100, 50]], "clicks": [], "hover_seconds": 0.3, "press_seconds": 0.2,
                    **more})


def state_of(spec, fps=24.0):
    """The state page.py sends for an interaction (its _state), made from the same modules."""
    from pages.data_graphics.graphic_page import css_face
    total = M.frames(spec, fps)
    preview = {"frame": list(FRAME), "fps": fps, "total": total,
               "cursor": [[round(x, 3), round(y, 3)] for x, y in M.cursor(spec, fps, total)],
               "down": [int(d) for d in M.down(spec, fps, total)],
               "hover_frames": M.look_back(spec["hover_seconds"], fps), "press_frames": M.look_back(spec["press_seconds"], fps)}
    return {"connected": False, "timeline": "", "problem": "", "frame": list(FRAME), "fps": fps, "spec": spec,
            "preview": preview, "face": css_face(spec["font"], spec["style"]), "images": {},
            "kinds": list(M.KINDS), "full": False, "max_seconds": M.MAX_SECONDS, "min_seconds": M.MIN_SECONDS,
            "answer_keys": {k: list(v) for k, v in M.ANSWER_KEYS.items()},
            "glow": {"reach": comp.GLOW_REACH, "soft": comp.GLOW_SOFT, "lift": comp.GLOW_LIFT},
            "cursors": comp.CURSOR_LOOKS, "cursor_dip": comp.CURSOR_DIP, "replace": True, "adding": False,
            "can_undo": False, "can_redo": False, "is_template": spec == M.clean({})}


class ModelTests(unittest.TestCase):
    def test_anything_cleans_to_a_whole_interaction(self):
        self.assertEqual(M.clean(None), M.clean({}))
        self.assertEqual(set(M.clean({})), set(M.DEFAULTS))
        outlined = M.clean({"hover_outline": 300, "outline_colour": "blue", "outline_width": 0})
        self.assertEqual([outlined["answers"]["hover"]["outline"], outlined["outline_colour"], outlined["outline_width"]],
                         [100.0, "#ffffff", 0.1])
        spec = M.clean({"seconds": 999, "hover_grow": "lots", "hover_glow": 400, "cursor": "hand", "cursor_size": 0,
                        "font": "", "label_colour": "red", "elements": [{"kind": "blob", "w": 900, "label": "  a   b  "},
                                                                         "nonsense"],
                        "path": [[0, 10, 10], ["x", 1, 1], [0.5, 500, -5], [0.2, 1, 1], [1e9, 0, 0]],
                        "clicks": [[0.1, 0.3], [0.2, 0.4], [5, 4], [0.5, 9999]]})
        hover = spec["answers"]["hover"]
        self.assertEqual((spec["seconds"], hover["grow"], hover["glow"]), (M.MAX_SECONDS, 8.0, 100.0))
        answers = M.clean({"answers": {"hover": {"grow": 99, "lift": "up", "glow": -5, "outline": 50, "spin": 1},
                                       "press": "nonsense"}})["answers"]
        self.assertEqual(answers, {"hover": {"grow": 50.0, "lift": 0.0, "glow": 0.0, "outline": 50.0},
                                   "press": M.DEFAULTS["answers"]["press"]})
        self.assertEqual([e["states"] for e in spec["elements"]], [None, None])           # they follow the interaction's
        self.assertEqual((spec["cursor"], spec["cursor_size"], spec["font"], spec["label_colour"]),
                         (M.DEFAULTS["cursor"], 1.0, "Arial", "#ffffff"))
        first, second = spec["elements"]
        self.assertEqual((first["id"], first["kind"], first["w"], first["label"]), ("e1", "button", 100.0, "a b"))
        self.assertEqual((second["id"], second["kind"]), ("e2", "button"))
        self.assertEqual(spec["path"], [[0.0, 10.0, 10.0], [0.5, 100.0, 0.0]])     # in order, on the frame
        self.assertEqual(spec["clicks"], [[0.1, 0.3], [0.5, 30.0]])                # no overlaps, none backwards
        self.assertEqual(M.clean(spec), spec)

    def test_a_new_one_is_the_template_and_a_recorded_one_keeps_no_template_clicks(self):
        self.assertEqual(M.clean({})["path"], M.TEMPLATE_PATH)
        self.assertEqual(M.clean({})["clicks"], M.TEMPLATE_CLICKS)
        mine = M.clean({"path": [[0, 1, 1], [1, 2, 2]]})
        self.assertEqual((mine["path"], mine["clicks"]), ([[0.0, 1.0, 1.0], [1.0, 2.0, 2.0]], []))
        self.assertEqual(M.clean({"path": []})["path"], [[0.0, 50.0, 50.0]])       # a cursor is always somewhere
        self.assertEqual(M.clean({"elements": []})["elements"], [])

    def test_a_circle_is_as_tall_as_it_is_wide(self):
        circle = M.clean({"elements": [{"kind": "circle", "w": 12, "h": 40, "radius": 3}]})["elements"][0]
        self.assertEqual((circle["w"], circle["h"], circle["radius"]), (12.0, 12.0, 100.0))

    def test_an_image_is_a_box_with_a_picture_and_nothing_without_one(self):
        logo = {"kind": "image", "path": "D:\\Footage\\logo.PNG", "px": [400, 200], "w": 20, "h": 30, "radius": 10}
        (el,) = M.clean({"elements": [logo]})["elements"]
        self.assertEqual((el["id"], el["kind"], el["path"], el["px"]), ("e1", "image", "D:\\Footage\\logo.PNG", [400, 200]))
        self.assertEqual((el["w"], el["h"], el["radius"], el["colour"], el["reacts"]), (20.0, 30.0, 10.0, "#ffffff", True))
        self.assertNotIn("path", M.clean({})["elements"][1])                    # only an image has one
        for bad in ({"path": ""}, {"path": "D:\\Footage\\clip.mov"}, {"px": [0, 10]}, {"px": "big"}, {"px": None},
                    {"px": [10]}, {"px": [10, 99999]}):
            self.assertEqual(M.clean({"elements": [{**logo, **bad}, {"kind": "card"}]})["elements"][0]["kind"], "card")
        self.assertTrue(M.is_image("a.JPeG"))
        self.assertFalse(M.is_image("a.svg"))
        # Its box is hit by its sides, like a card.
        self.assertTrue(M.inside({**el, "x": 50, "y": 50}, 59, 64, FRAME))
        self.assertFalse(M.inside({**el, "x": 50, "y": 50}, 61, 50, FRAME))

    def test_a_picture_gets_a_box_of_its_own_shape_and_is_never_stretched(self):
        self.assertEqual(M.boxed([400, 200], FRAME), (24.0, 21.333))            # 460.8 px wide, 230.4 px high
        self.assertEqual(M.boxed([200, 400], FRAME), (22.5, 80.0))              # a tall one: no taller than most of the frame
        wide = {"w": 20, "h": 30, "px": [400, 200]}                             # a box 384 x 324 px
        self.assertEqual(M.fit(wide, FRAME), (384.0, 192.0))
        tall = {"w": 20, "h": 10, "px": [400, 200]}                             # a box 384 x 108 px
        self.assertEqual(M.fit(tall, FRAME), (216.0, 108.0))

    def test_an_image_is_added_only_with_its_picture(self):
        spec = M.clean({})
        self.assertEqual(M.with_new(spec, "image"), (None, None))
        made, eid = M.with_new(spec, "image", {"path": "D:\\Footage\\logo.png", "px": [400, 200], "w": 24, "h": 21.333})
        self.assertEqual((eid, made["elements"][-1]["kind"], made["elements"][-1]["h"]), ("e5", "image", 21.333))

    def test_shapes_are_added_changed_raised_and_removed(self):
        spec = M.clean({})
        more, eid = M.with_new(spec, "circle")
        self.assertEqual((eid, more["elements"][-1]["kind"], len(more["elements"])), ("e5", "circle", 5))
        self.assertEqual(M.with_new(spec, "star"), (None, None))
        full = M.clean({"elements": [{"kind": "card"}] * 40})
        self.assertEqual(len(full["elements"]), M.MAX_ELEMENTS)
        self.assertEqual(M.with_new(full, "card"), (None, None))

        moved = M.with_element(spec, "e2", "box", {"x": 10, "y": 200, "w": 30, "nonsense": 1})
        self.assertEqual([moved["elements"][1][k] for k in ("x", "y", "w", "h")], [10.0, 100.0, 30.0, 9.0])
        self.assertEqual(M.with_element(spec, "e2", "label", "  Hello   there ")["elements"][1]["label"], "Hello there")
        self.assertFalse(M.with_element(spec, "e2", "reacts", False)["elements"][1]["reacts"])
        self.assertEqual(M.with_element(spec, "e2", "colour", "#ABCDEF")["elements"][1]["colour"], "#abcdef")
        for bad in (("e9", "label", "x"), ("e2", "kind", "card"), ("e2", "id", "e1")):
            self.assertIsNone(M.with_element(spec, *bad))

        raised, eid = M.to_front(spec, "e2")
        self.assertEqual((eid, [e["label"] for e in raised["elements"]]), ("e4", ["", "Save", "Share", "Play"]))
        self.assertEqual([e["id"] for e in raised["elements"]], ["e1", "e2", "e3", "e4"])    # an id is a place in the pile
        self.assertEqual([e["label"] for e in M.without(spec, "e3")["elements"]], ["", "Play", "Share"])
        self.assertIsNone(M.without(spec, "e9"))
        self.assertEqual(M.to_front(spec, "e9"), (None, None))

    def test_the_cursor_is_keyed_on_frames_and_goes_straight_between_them(self):
        spec = one_button()
        self.assertEqual(M.frames(spec, 10), 20)
        self.assertEqual(M.cursor_keys(spec, 10, 20), [(0, 0.0, 50.0), (19, 100.0, 50.0)])
        where = M.cursor(spec, 10, 20)
        self.assertEqual(len(where), 20)
        self.assertEqual((where[0], where[19]), ((0.0, 50.0), (100.0, 50.0)))
        self.assertAlmostEqual(where[10][0], 100 * 10 / 19)
        # A path that starts late or ends early holds where it starts and where it ends.
        late = M.clean({"seconds": 2, "path": [[0.5, 10, 10], [1.0, 90, 10]]})
        keys = M.cursor_keys(late, 10, 20)
        self.assertEqual(keys, [(0, 10.0, 10.0), (5, 10.0, 10.0), (10, 90.0, 10.0), (19, 90.0, 10.0)])
        self.assertEqual(M.cursor(late, 10, 20)[3], (10.0, 10.0))
        self.assertEqual(M.cursor(late, 10, 20)[15], (90.0, 10.0))

    def test_a_click_is_down_for_a_frame_at_least(self):
        spec = M.clean({"seconds": 2, "path": [[0, 0, 0]], "clicks": [[0.5, 0.8], [1.21, 1.22]]})
        held = M.down(spec, 10, 20)
        self.assertEqual([f for f, d in enumerate(held) if d], [5, 6, 7, 12])

    def test_inside_a_box_and_inside_a_circle(self):
        box = {"kind": "button", "x": 50, "y": 50, "w": 10, "h": 20}
        self.assertTrue(M.inside(box, 54.9, 59.9, FRAME))
        self.assertFalse(M.inside(box, 55.1, 50, FRAME))
        self.assertFalse(M.inside(box, 50, 60.1, FRAME))
        circle = {"kind": "circle", "x": 50, "y": 50, "w": 10}       # 192 px across: 96 px is 5% across, 8.9% down
        self.assertTrue(M.inside(circle, 54.9, 50, FRAME))
        self.assertTrue(M.inside(circle, 50, 58.8, FRAME))
        self.assertFalse(M.inside(circle, 50, 59, FRAME))
        self.assertFalse(M.inside(circle, 54, 57, FRAME))             # inside its box, outside its round

    def test_a_hover_comes_up_over_its_frames_and_goes_the_same_way(self):
        spec = one_button()
        rows = M.amounts(spec, FRAME, 10, 20)["e1"]
        on = [f for f, (x, _y) in enumerate(M.cursor(spec, 10, 20)) if 45 < x < 55]
        self.assertEqual(on, [9, 10])
        hover = [h for h, _p in rows]
        self.assertEqual(hover[:9], [0.0] * 9)
        third, two_thirds = M.eased(1 / 3), M.eased(2 / 3)
        for got, want in zip(hover[9:14], (third, two_thirds, two_thirds, third, 0.0)):
            self.assertAlmostEqual(got, want, places=5)
        self.assertEqual(set(hover[13:]), {0.0})
        self.assertEqual({p for _h, p in rows}, {0.0})                 # never clicked

    def test_a_press_needs_the_button_down_on_the_shape(self):
        on_it = one_button(clicks=[[0.9, 1.1]])          # down on frames 9 and 10: both on the shape
        press = [p for _h, p in M.amounts(on_it, FRAME, 10, 20)["e1"]]
        self.assertEqual(press[8:13], [0.0, 0.5, 1.0, 0.5, 0.0])
        missed = one_button(clicks=[[0.2, 0.4]])         # down while it's still on its way
        self.assertEqual({p for _h, p in M.amounts(missed, FRAME, 10, 20)["e1"]}, {0.0})

    def test_a_cursor_that_starts_on_a_shape_has_it_fully_hovered_from_the_first_frame(self):
        spec = one_button(path=[[0, 50, 50]])
        self.assertEqual(M.amounts(spec, FRAME, 10, 20)["e1"][0], (1.0, 0.0))

    def test_a_shape_that_doesnt_answer_has_no_amounts(self):
        self.assertEqual(sorted(M.amounts(M.clean({}), FRAME, 24, 120)), ["e2", "e3", "e4"])

    def test_how_big_a_shape_is(self):
        spec = M.clean({"hover_grow": 10, "press_shrink": 4})          # saved before states: the same sizes
        self.assertAlmostEqual(M.scale(spec, 1, 0), 1.10)
        self.assertAlmostEqual(M.scale(spec, 1, 1), 1.06)
        self.assertAlmostEqual(M.scale(spec, 0, 0), 1.0)

    def test_a_new_interaction_answers_as_it_always_did(self):
        # Hover 8 % bigger and a 70 % glow, a click 6 % smaller, no lift, no outline.
        self.assertEqual(M.clean({})["answers"], {"hover": {"grow": 8.0, "lift": 0.0, "glow": 70.0, "outline": 0.0},
                                                  "press": {"grow": -6.0, "lift": 0.0, "glow": 0.0, "outline": 0.0}})
        self.assertEqual(M.look(M.clean({})["answers"], 1, 1), {"scale": 1 + 0.08 - 0.06, "lift": 0.0, "glow": 0.7, "outline": 0.0})

    def test_an_interaction_saved_before_states_draws_exactly_as_it_did(self):
        old = {"seconds": 2, "elements": [{"kind": "button", "x": 50, "y": 50, "w": 10, "h": 20}],
               "path": [[0, 0, 50], [2, 100, 50]], "clicks": [[0.9, 1.1]], "hover_seconds": 0.3, "press_seconds": 0.2,
               "hover_grow": 12, "hover_glow": 40, "hover_outline": 30, "press_shrink": 10}
        spec = M.clean(old)
        for key in ("hover_grow", "hover_glow", "hover_outline", "press_shrink"):
            self.assertNotIn(key, spec)                                   # gone once it's moved over
        self.assertEqual(spec["answers"], {"hover": {"grow": 12.0, "lift": 0.0, "glow": 40.0, "outline": 30.0},
                                           "press": {"grow": -10.0, "lift": 0.0, "glow": 0.0, "outline": 0.0}})
        self.assertEqual(M.clean(spec), spec)
        rows, looks = M.amounts(spec, FRAME, 10, 20)["e1"], M.looks(spec, FRAME, 10, 20)["e1"]
        self.assertTrue(any(h for h, _p in rows) and any(p for _h, p in rows))
        for (hover, press), got in zip(rows, looks):
            # The sums it was drawn with before: size, glow and outline from the four old numbers.
            self.assertEqual(got["scale"], 1 + 12 / 100 * hover - 10 / 100 * press)
            self.assertEqual(got["glow"], 40 / 100 * hover)
            self.assertEqual(got["outline"], 30 / 100 * hover)
            self.assertEqual(got["lift"], 0.0)
        # Old numbers out of their old ranges are kept to them, as they were then.
        self.assertEqual(M.clean({"hover_grow": 80, "press_shrink": 50})["answers"]["press"]["grow"], -30.0)
        self.assertEqual(M.clean({"hover_grow": 80})["answers"]["hover"]["grow"], 50.0)
        # One that has answers is read by them; the old keys beside them say nothing.
        self.assertEqual(M.clean({**spec, "hover_grow": 1})["answers"], spec["answers"])

    def test_how_a_shape_looks_adds_up_its_hover_and_its_press(self):
        answers = {"hover": {"grow": 10, "lift": 2, "glow": 60, "outline": 50},
                   "press": {"grow": -20, "lift": -1, "glow": 70, "outline": 80}}
        got = M.look(answers, 0.5, 1.0)
        self.assertAlmostEqual(got["scale"], 1 + 0.05 - 0.2)
        self.assertAlmostEqual(got["lift"], 1 - 1)
        self.assertEqual((got["glow"], got["outline"]), (1.0, 1.0))      # kept to 0..1
        self.assertEqual(M.look(answers, 0, 0), {"scale": 1.0, "lift": 0.0, "glow": 0.0, "outline": 0.0})

    def test_a_shape_with_states_of_its_own_answers_by_them(self):
        spec = one_button(elements=[{"kind": "button", "x": 50, "y": 50, "w": 10, "h": 20},
                                    {"kind": "button", "x": 50, "y": 50, "w": 10, "h": 20}], clicks=[[0.9, 1.1]])
        mine = M.with_answers(spec, "e2", "hover", "lift", 5)
        self.assertIsNone(mine["elements"][0]["states"])
        # Its own starts as the interaction's, with the one change.
        self.assertEqual(mine["elements"][1]["states"],
                         {**spec["answers"], "hover": {**spec["answers"]["hover"], "lift": 5.0}})
        mine = M.with_answers(mine, "e2", "press", "grow", 20)
        self.assertEqual(mine["answers"], spec["answers"])                # everyone else's is as it was
        looks = M.looks(mine, FRAME, 10, 20)
        f = max(range(20), key=lambda i: M.amounts(mine, FRAME, 10, 20)["e1"][i][1])     # the most pressed frame
        self.assertEqual(looks["e1"][f], M.look(spec["answers"], *M.amounts(mine, FRAME, 10, 20)["e1"][f]))
        self.assertGreater(looks["e2"][f]["scale"], 1.0)                  # it grows when pressed, the other dips
        self.assertLess(looks["e1"][f]["scale"], 1.0)
        self.assertGreater(looks["e2"][f]["lift"], 0.0)
        self.assertEqual(looks["e1"][f]["lift"], 0.0)
        self.assertNotEqual(M.scale(mine, 1, 1, mine["elements"][1]), M.scale(mine, 1, 1, mine["elements"][0]))
        # Everyone's changed: the one with its own doesn't follow.
        theirs = M.with_answers(mine, "", "hover", "glow", 10)
        self.assertEqual(theirs["answers"]["hover"]["glow"], 10.0)
        self.assertEqual(theirs["elements"][1]["states"]["hover"]["glow"], 70.0)
        # Reset: it follows the interaction's again.
        back = M.with_answers(theirs, "e2", "hover", "reset", None)
        self.assertIsNone(back["elements"][1]["states"])
        self.assertEqual(M.looks(back, FRAME, 10, 20)["e2"], M.looks(back, FRAME, 10, 20)["e1"])
        # Its states go where it goes, and a copy keeps them.
        raised, eid = M.to_front(M.with_answers(spec, "e1", "hover", "grow", 30), "e1")
        self.assertEqual((eid, raised["elements"][1]["states"]["hover"]["grow"]), ("e2", 30.0))
        self.assertEqual(M.with_element(raised, "e2", "label", "Go")["elements"][1]["states"]["hover"]["grow"], 30.0)
        self.assertIsNone(M.with_new(raised, "card")[0]["elements"][-1]["states"])     # a new one follows everyone's
        for bad in (("e9", "hover", "grow", 1), ("e1", "rest", "grow", 1), ("", "hover", "reset", None),
                    ("e1", "hover", "spin", 1), ("", "press", "seconds", 1)):
            self.assertIsNone(M.with_answers(spec, *bad))
        self.assertEqual(M.with_answers(spec, "", "press", "lift", 99)["answers"]["press"]["lift"], 20.0)

    def test_a_recording_is_thinned_to_the_keys_that_matter(self):
        # A straight move sampled thirty times, then a corner, then still.
        samples = ([[i / 30, 10 + 2 * i, 20] for i in range(31)] + [[1 + i / 30, 70, 20 + i] for i in range(1, 31)]
                   + [[2 + i / 30, 70, 50] for i in range(1, 31)])
        spec = M.with_recording(M.clean({}), samples, [[0.5, 0.6]], 3.0)
        self.assertEqual(spec["path"], [[0.0, 10.0, 20.0], [1.0, 70.0, 20.0], [2.0, 70.0, 50.0], [3.0, 70.0, 50.0]])
        self.assertEqual((spec["seconds"], spec["clicks"]), (3.0, [[0.5, 0.6]]))
        # What's left plays the same: no sample is further than the tolerance from it.
        curve = [[t / 50, 50 + 30 * (t / 50) ** 2, 10 + t] for t in range(51)]
        kept = M.thinned(curve)
        self.assertLess(len(kept), 12)
        for t, x, y in curve:
            a = max(k for k in kept if k[0] <= t)
            b = min(k for k in kept if k[0] >= t)
            p = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0
            off = ((x - (a[1] + (b[1] - a[1]) * p)) ** 2 + (y - (a[2] + (b[2] - a[2]) * p)) ** 2) ** 0.5
            self.assertLessEqual(off, M.TOLERANCE + 1e-9)
        self.assertEqual(M.with_recording(M.clean({}), samples, [], 0.1)["seconds"], M.MIN_SECONDS)

    def test_the_same_interaction_has_the_same_id(self):
        a = M.clean({})
        self.assertEqual(M.interaction_id(a, FRAME, 24), M.interaction_id(M.clean({}), FRAME, 24))
        self.assertNotEqual(M.interaction_id(a, FRAME, 24), M.interaction_id(M.with_answers(a, "", "hover", "grow", 9),
                                                                              FRAME, 24))
        self.assertNotEqual(M.interaction_id(a, FRAME, 24), M.interaction_id(M.with_answers(a, "e2", "hover", "grow", 8),
                                                                              FRAME, 24))
        self.assertNotEqual(M.interaction_id(a, FRAME, 24), M.interaction_id(a, FRAME, 30))


class CompTests(unittest.TestCase):
    def setUp(self):
        self.spec = M.clean({})
        self.text, self.gid = comp.comp_text(self.spec, FRAME, 24, 120)

    def tool(self, name, text=None):
        text = text or self.text
        start = text.index(f"\t\t{name} = ")
        return text[start:text.index("\n\t\t},", start)]

    def test_one_controller_holds_the_cursor_and_the_amounts(self):
        cursor = self.tool("Cursor")
        self.assertIn("Cursor = Custom {", cursor)
        for line in ('PointIn1 = Input { SourceOp = "CursorPath", Source = "Value", },',
                     'NumberIn1 = Input { SourceOp = "CursorClick", Source = "Value", },',
                     "NumberIn2 = Input { Value = 5, },", "NumberIn3 = Input { Value = 2, },",
                     # Everyone's states, X hovered and Y pressed: grow, lift, glow; the outline's two.
                     "PointIn2 = Input { Value = { 0.08, -0.06 }, },", "PointIn3 = Input { Value = { 0, 0 }, },",
                     "PointIn4 = Input { Value = { 0.7, 0 }, },",
                     "NumberIn4 = Input { Value = 0, },", "NumberIn5 = Input { Value = 0, },"):
            self.assertIn(line, cursor)
        self.assertNotIn("NumberIn6", cursor)
        self.assertNotIn("NumberIn8", cursor)
        self.assertIn(f'CustomData = {{ TEMPLATE_ID = "Text+", {comp.TAG} = "{self.gid}" }}', self.text)
        self.assertIn("RenderRange = { 0, 119 }", self.text)

    def test_the_cursor_is_keyed_where_the_model_says(self):
        def keys(name):
            return [(int(f), float(v)) for f, v in re.findall(r"\[(\d+)\] = \{ ([-\d.e]+),", self.tool(name))]
        want = M.cursor_keys(self.spec, 24, 120)
        self.assertEqual(keys("CursorPathX"), [(f, x / 100) for f, x, _y in want])
        for (f, got), (wf, _x, y) in zip(keys("CursorPathY"), want):
            self.assertEqual(f, wf)
            self.assertAlmostEqual(got, 1 - y / 100)                    # Fusion counts up from the bottom
        held = M.down(self.spec, 24, 120)
        click = dict(keys("CursorClick"))
        first = held.index(True)
        self.assertEqual((click[first - 1], click[first]), (0.0, 1.0))
        self.assertEqual([f for f, d in enumerate(held) if d], [58, 59, 60, 61])       # 2.4 s to 2.6 s

    def test_only_the_cursor_is_keyed(self):
        splines = re.findall(r"\t\t(\w+) = BezierSpline", self.text)
        self.assertEqual(splines, ["CursorPathX", "CursorPathY", "CursorClick"])

    def test_a_shape_that_answers_looks_back_over_the_cursors_last_frames(self):
        state = self.tool("Button2State")
        hover = re.search(r'NumberIn1 = Input \{ Expression = "(.*?)", \},', state).group(1)
        self.assertTrue(hover.startswith(":local n = Cursor.NumberIn2; if n < 1 then n = 1 end;"))
        self.assertIn('Cursor:GetValue(\\"PointIn1\\", time - k)', hover)
        self.assertIn("p.X > Button2Mask.Center.X - Button2Mask.Width / 2", hover)       # its own mask is its edge
        self.assertTrue(hover.endswith("return s / c"))
        self.assertNotIn("[time", self.text)        # Tool.Input[time - k] reads the present frame, not the past
        self.assertIn('NumberIn2 = Input { Expression = "NumberIn1 * NumberIn1 * (3 - 2 * NumberIn1)", },', state)
        press = re.search(r'NumberIn3 = Input \{ Expression = "(.*?)", \},', state).group(1)
        self.assertIn(":local n = Cursor.NumberIn3;", press)
        self.assertIn("and d then s = s + 1", press)

    def test_its_size_lift_and_glow_read_its_state(self):
        state = self.tool("Button2State")
        # A shape that follows everyone's reads its states off the Cursor, and adds them up as model.look() does.
        for line in ('PointIn1 = Input { Expression = "Cursor.PointIn2", },',
                     'PointIn2 = Input { Expression = "Cursor.PointIn3", },',
                     'PointIn3 = Input { Expression = "Cursor.PointIn4", },',
                     'PointIn4 = Input { Expression = "Point(Cursor.NumberIn4, Cursor.NumberIn5)", },',
                     'NumberIn4 = Input { Expression = "1 + PointIn1.X * NumberIn2 + PointIn1.Y * NumberIn3", },',
                     'NumberIn5 = Input { Expression = "PointIn2.X * NumberIn2 + PointIn2.Y * NumberIn3", },',
                     'NumberIn6 = Input { Expression = ":local v = PointIn3.X * NumberIn2 + PointIn3.Y * NumberIn3; '
                     'if v < 0 then v = 0 end; if v > 1 then v = 1 end; return v", },',
                     'NumberIn7 = Input { Expression = ":local v = PointIn4.X * NumberIn2 + PointIn4.Y * NumberIn3; '
                     'if v < 0 then v = 0 end; if v > 1 then v = 1 end; return v", },'):
            self.assertIn(line, state)
        shape = self.tool("Button2")
        self.assertIn('Size = Input { Expression = "Button2State.NumberIn4", },', shape)
        self.assertIn('Center = Input { Expression = "Point(0.5, 0.5 + Button2State.NumberIn5)", },', shape)
        self.assertIn('Pivot = Input { Expression = "Button2Mask.Center", },', shape)
        glow = self.tool("Button2GlowMask")
        self.assertIn('Level = Input { Expression = "Button2State.NumberIn6", },', glow)
        self.assertIn('Center = Input { Expression = "Button2Mask.Center", },', glow)

    def test_a_lift_moves_the_shape_its_glow_outline_and_label_together(self):
        spec = M.with_answers(M.clean({"hover_outline": 50}), "", "hover", "lift", 4)
        text, _gid = comp.comp_text(spec, FRAME, 24, 120)
        self.assertIn("PointIn3 = Input { Value = { 0.04, 0 }, },", self.tool("Cursor", text))   # a share of the height
        # The one Transform that lifts takes the glow, the fill, the outline and the label merged together.
        self.assertIn('Input = Input { SourceOp = "Button2LabelOn", Source = "Output", },', self.tool("Button2", text))
        for on, under in (("Button2LabelOn", "Button2EdgeOn"), ("Button2EdgeOn", "Button2FillOn"),
                          ("Button2FillOn", "Button2GlowOn")):
            self.assertIn(f'Background = Input {{ SourceOp = "{under}", Source = "Output", }},', self.tool(on, text))
        self.assertIn('Center = Input { Expression = "Point(0.5, 0.5 + Button2State.NumberIn5)", },', self.tool("Button2", text))
        # The masks the cursor is read against stay where the shape is at rest.
        self.assertNotIn("NumberIn5", self.tool("Button2Mask", text))

    def test_a_shape_with_states_of_its_own_has_its_numbers_written_in(self):
        spec = M.with_answers(M.with_answers(self.spec, "e3", "press", "grow", 12), "e3", "hover", "outline", 40)
        text, _gid = comp.comp_text(spec, FRAME, 24, 120)
        own = self.tool("Button3State", text)
        for line in ("PointIn1 = Input { Value = { 0.08, 0.12 }, },", "PointIn2 = Input { Value = { 0, 0 }, },",
                     "PointIn3 = Input { Value = { 0.7, 0 }, },", "PointIn4 = Input { Value = { 0.4, 0 }, },"):
            self.assertIn(line, own)
        self.assertNotIn("Cursor.PointIn", own)
        self.assertIn('Size = Input { Expression = "Button3State.NumberIn4", },', self.tool("Button3", text))
        # The others still follow the Cursor's, which is still everyone's.
        self.assertIn('PointIn1 = Input { Expression = "Cursor.PointIn2", },', self.tool("Button2State", text))
        self.assertIn("PointIn2 = Input { Value = { 0.08, -0.06 }, },", self.tool("Cursor", text))
        # Only the one whose states have an outline gets the outline's tools.
        self.assertIn("\t\tButton3EdgeMask = RectangleMask {", text)
        self.assertNotIn("\t\tButton2EdgeMask", text)
        self.assertNotEqual(self.tool("Button3State", text).replace("Button3", "X"), self.tool("Button2State", text).replace("Button2", "X"))

    def test_an_outline_is_traced_only_when_its_turned_up(self):
        self.assertNotIn("EdgeMask", self.text.replace("CursorEdgeMask", ""))     # none to start with
        self.assertIn("NumberIn4 = Input { Value = 0, },", self.tool("Cursor"))
        spec = M.clean({"hover_outline": 80, "outline_colour": "#ff0000", "outline_width": 1})     # as saved before states
        text, _gid = comp.comp_text(spec, FRAME, 24, 120)
        self.assertIn("NumberIn4 = Input { Value = 0.8, },", self.tool("Cursor", text))
        pressed, _gid = comp.comp_text(M.with_answers(self.spec, "", "press", "outline", 60), FRAME, 24, 120)
        self.assertIn("NumberIn5 = Input { Value = 0.6, },", self.tool("Cursor", pressed))
        self.assertIn("\t\tButton2EdgeMask = RectangleMask {", pressed)          # pressed only is an outline too
        edge = self.tool("Button2EdgeMask", text)
        for line in ("Solid = Input { Value = 0, },", "BorderWidth = Input { Value = 0.005625, },",     # 1% of 1080 px
                     'Level = Input { Expression = "Button2State.NumberIn7", },',
                     'Center = Input { Expression = "Button2Mask.Center", },',
                     'CornerRadius = Input { Expression = "Button2Mask.CornerRadius", },'):
            self.assertIn(line, edge)
        self.assertIn("TopLeftRed = Input { Value = 1, },", self.tool("Button2Edge", text))
        self.assertIn("TopLeftGreen = Input { Value = 0, },", self.tool("Button2Edge", text))
        # Over the fill, under the label, inside the shape's own Transform.
        self.assertIn('Background = Input { SourceOp = "Button2FillOn", Source = "Output", },', self.tool("Button2EdgeOn", text))
        self.assertIn('Background = Input { SourceOp = "Button2EdgeOn", Source = "Output", },', self.tool("Button2LabelOn", text))
        self.assertNotIn("\t\tCard1EdgeMask", text)                               # one that stays still has none

    def test_where_a_shape_is_and_what_it_says(self):
        mask = self.tool("Button2Mask")
        self.assertIn("Button2Mask = RectangleMask {", mask)
        for line in ("Center = Input { Value = { 0.32, 0.4 }, },", "Width = Input { Value = 0.14, },",
                     "Height = Input { Value = 0.09, },", "CornerRadius = Input { Value = 0.5, },"):
            self.assertIn(line, mask)
        label = self.tool("Button2Label")
        self.assertIn('StyledText = Input { Value = "Play", },', label)
        self.assertIn('Center = Input { Expression = "Point(Button2Mask.Center.X, Button2Mask.Center.Y + ', label)
        self.assertIn("TopLeftBlue = Input { Value = 0.878431, },", self.tool("Button2Fill"))

    def test_a_shape_that_stays_still_has_no_state_no_glow_and_no_transform(self):
        for name in ("Card1State", "Card1GlowMask", "Card1 = Transform", "Card1Label"):
            self.assertNotIn(f"\t\t{name}", self.text)
        self.assertIn("\t\tCard1Mask = RectangleMask {", self.text)
        self.assertIn('Foreground = Input { SourceOp = "Card1Fill", Source = "Output", },', self.tool("Card1On"))

    def test_the_shapes_are_piled_in_order_under_the_cursor(self):
        self.assertIn('Background = Input { SourceOp = "Canvas", Source = "Output", },', self.tool("Card1On"))
        self.assertIn('Background = Input { SourceOp = "Card1On", Source = "Output", },', self.tool("Button2On"))
        self.assertIn('Background = Input { SourceOp = "Button4On", Source = "Output", },', self.tool("CursorEdgeOn"))
        self.assertIn('Input = Input { SourceOp = "CursorOn", Source = "Output", },', self.text)

    def test_an_image_is_a_loader_merged_at_the_size_that_fits_its_box(self):
        logo = {"kind": "image", "path": "D:\\Footage\\logo.png", "px": [400, 200], "x": 50, "y": 50, "w": 20, "h": 30,
                "radius": 25, "label": "New"}
        text, _gid = comp.comp_text(M.clean({"elements": [logo]}), FRAME, 24, 48)
        loader = self.tool("Image1Image", text)
        self.assertIn("Image1Image = Loader {", loader)
        self.assertIn('Filename = "D:\\\\Footage\\\\logo.png", FormatID = "PNGFormat",', loader)
        self.assertIn('["Clip1.PNGFormat.PostMultiply"] = Input { Value = 1, },', loader)
        fill = self.tool("Image1Fill", text)
        self.assertIn("Image1Fill = Merge {", fill)
        for line in ('Background = Input { SourceOp = "Image1Back", Source = "Output", },',
                     'Foreground = Input { SourceOp = "Image1Image", Source = "Output", },',
                     'Center = Input { Expression = "Image1Mask.Center", },',
                     'Size = Input { Expression = "Image1Mask.Width * 4.8", },',          # 384 px over its own 400... of 1920
                     'EffectMask = Input { SourceOp = "Image1Mask", Source = "Mask", },'):
            self.assertIn(line, fill)
        self.assertIn("CornerRadius = Input { Value = 0.25, },", self.tool("Image1Mask", text))
        # The rest is any shape's: a state, a glow, a label, its own Transform.
        for name in ("Image1State = Custom", "Image1GlowMask = RectangleMask", "Image1Label = TextPlus", "Image1 = Transform"):
            self.assertIn(f"\t\t{name} {{", text)
        self.assertIn('Background = Input { SourceOp = "Image1GlowOn", Source = "Output", },', self.tool("Image1FillOn", text))
        # A box wider than the picture: its height holds the picture in.
        text, _gid = comp.comp_text(M.clean({"elements": [{**logo, "h": 10}]}), FRAME, 24, 48)
        self.assertIn('Size = Input { Expression = "Image1Mask.Height * 5.4", },', self.tool("Image1Fill", text))
        jpeg, _gid = comp.comp_text(M.clean({"elements": [{**logo, "path": "D:\\Footage\\photo.JPG"}]}), FRAME, 24, 48)
        self.assertIn('FormatID = "JpegFormat",', jpeg)
        self.assertNotIn("PostMultiply", jpeg)

    def test_an_image_that_stays_still_is_just_its_picture(self):
        logo = {"kind": "image", "path": "D:\\Footage\\logo.png", "px": [400, 200], "reacts": False}
        text, _gid = comp.comp_text(M.clean({"elements": [logo]}), FRAME, 24, 48)
        self.assertIn('Foreground = Input { SourceOp = "Image1Fill", Source = "Output", },', self.tool("Image1On", text))
        self.assertNotIn("Image1State", text)

    def test_a_circle_is_an_ellipse_hit_by_its_round(self):
        spec = M.clean({"elements": [{"kind": "circle", "x": 50, "y": 50, "w": 10}]})
        text, _gid = comp.comp_text(spec, FRAME, 24, 48)
        mask = self.tool("Circle1Mask", text)
        self.assertIn("Circle1Mask = EllipseMask {", mask)
        self.assertIn("Width = Input { Value = 0.1, },", mask)
        self.assertIn("Height = Input { Value = 0.1, },", mask)          # an ellipse's Height is a share of the width too
        self.assertIn("< Circle1Mask.Width * Circle1Mask.Width / 4", self.tool("Circle1State", text))
        self.assertNotIn("CornerRadius", self.tool("Circle1GlowMask", text))

    def test_the_cursor_drawn_as_a_windows_or_a_mac_pointer_a_dot_or_not_at_all(self):
        self.assertEqual(M.DEFAULTS["cursor"], "mac" if sys.platform == "darwin" else "windows")
        self.assertEqual(set(comp.CURSOR_LOOKS), set(M.CURSORS) - {"none"})
        windows, _gid = comp.comp_text(M.clean({"cursor": "windows"}), FRAME, 24, 120)
        self.assertIn("CursorMask = PolylineMask {", windows)
        self.assertIn('Center = Input { SourceOp = "CursorPath", Source = "Value", },', self.tool("CursorMask", windows))
        self.assertIn("TopLeftRed = Input { Value = 1, },", self.tool("CursorFill", windows))     # white, edged in black
        self.assertIn("TopLeftRed = Input { Value = 0, },", self.tool("CursorEdge", windows))
        mac, _gid = comp.comp_text(M.clean({"cursor": "mac"}), FRAME, 24, 120)
        self.assertIn("TopLeftRed = Input { Value = 0, },", self.tool("CursorFill", mac))         # black, edged in white
        self.assertIn("TopLeftRed = Input { Value = 1, },", self.tool("CursorEdge", mac))
        self.assertNotEqual(self.tool("CursorMask", mac), self.tool("CursorMask", windows))
        dot, _gid = comp.comp_text(M.clean({"cursor": "dot"}), FRAME, 24, 120)
        self.assertIn("CursorMask = EllipseMask {", dot)
        hidden, _gid = comp.comp_text(M.clean({"cursor": "none"}), FRAME, 24, 120)
        self.assertNotIn("CursorMask", hidden)
        self.assertIn("\t\tCursor = Custom {", hidden)                   # the shapes still answer it
        self.assertIn('Input = Input { SourceOp = "Button4On", Source = "Output", },', hidden)

    def test_a_label_with_quotes_stays_one_string(self):
        spec = M.clean({"elements": [{"kind": "button", "label": 'Say "hi"\\'}]})
        text, _gid = comp.comp_text(spec, FRAME, 24, 48)
        self.assertIn('StyledText = Input { Value = "Say \\"hi\\"\\\\", },', text)

    def test_no_shapes_is_still_a_comp(self):
        text, _gid = comp.comp_text(M.clean({"elements": []}), FRAME, 24, 48)
        self.assertIn('Background = Input { SourceOp = "Canvas", Source = "Output", },', text)


class JobTests(unittest.TestCase):
    def test_the_jobs_load_without_qt(self):
        self.assertEqual(load_without_qt(JOBS), "")

    def test_the_job_is_named_in_the_readers_process(self):
        from core import resolve_reader_child
        for name, where in JOBS.items():
            self.assertEqual(resolve_reader_child.JOBS[name], where)

    def test_it_is_a_new_clip_at_the_playhead(self):
        controller, timeline = studio()
        footage = timeline.put(1, 86400, 90000)
        timeline.playhead = 86400 + 48
        done = place.add(controller, {}, {"em": 0.72, "baseline": 0.38})
        self.assertEqual((done["track"], done["start"], done["end"], done["replaced"]), (2, 86448, 86568, False))
        self.assertEqual((done["timeline"], done["fps"], done["frame"], done["seconds"]), ("Edit", 24.0, [1920, 1080], 5.0))
        self.assertEqual(done["id"], M.interaction_id(M.clean({}), FRAME, 24.0))
        (item,) = timeline.tracks[2]
        self.assertEqual(item.comp.GetData(comp.TAG), done["id"])
        self.assertIn("Cursor = Custom {", item.comp.text)
        self.assertEqual(timeline.tracks[1], [footage])                 # nothing of theirs touched
        self.assertEqual(timeline.playhead, 86448)

    def test_adding_again_replaces_buddys_own_unless_told_not_to(self):
        controller, timeline = studio()
        place.add(controller, {})
        again = place.add(controller, {"hover_grow": 20})
        self.assertTrue(again["replaced"])
        self.assertEqual(len(timeline.tracks[1]), 1)
        kept = place.add(controller, {}, replace=False)
        self.assertEqual((kept["track"], kept["replaced"]), (2, False))

    def test_an_image_that_has_gone_is_said_and_nothing_is_added(self):
        controller, timeline = studio()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "logo.png")
            spec = {"elements": [{"kind": "image", "path": path, "px": [400, 200]}]}
            with self.assertRaises(PlaceError) as caught:
                place.add(controller, spec)
            self.assertIn('The image "logo.png" isn\'t where it was.', str(caught.exception))
            self.assertEqual(timeline.tracks[1], [])
            with open(path, "wb") as fh:
                fh.write(b"made up")
            done = place.add(controller, spec)                          # there again: on it goes
            self.assertIn("Image1Image = Loader {", timeline.tracks[done["track"]][0].comp.text)

    def test_no_project_or_no_timeline_is_said(self):
        with self.assertRaises(ResolveConnectionError):
            place.add(Controller(None), {})
        with self.assertRaises(ResolveConnectionError) as caught:
            place.add(Controller(Project(None)), {})
        self.assertIn("Open a timeline", str(caught.exception))

    def test_a_failure_adds_nothing(self):
        controller, timeline = studio()
        timeline.takes_comps = False
        with self.assertRaises(PlaceError):
            place.add(controller, {})
        self.assertEqual(timeline.tracks[1], [])


class _Said(HTMLParser):
    """What the page says: its text, titles and labels that aren't marked translate="no"."""

    def __init__(self):
        super().__init__()
        self.texts, self._off = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in ("meta", "link", "input", "br"):
            self._off.append(attrs.get("translate") == "no" or tag in ("script", "title", "style")
                             or (self._off[-1] if self._off else False))
        for key in ("title", "aria-label"):
            if attrs.get(key) and attrs.get("translate") != "no":
                self.texts.append(attrs[key])

    def handle_endtag(self, tag):
        if tag not in ("meta", "link", "input", "br") and self._off:
            self._off.pop()

    def handle_data(self, data):
        if data.strip() and not (self._off and self._off[-1]):
            self.texts.append(i18n.normalize(data))


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web" / "interactions.js").read_text(encoding="utf-8")

    def test_its_in_the_sidebar_under_editing_tools(self):
        source = (_paths.APP / "registry.py").read_text(encoding="utf-8")
        # It's a tab of Design Studio: the rail has that, and not Interactions by itself.
        self.assertIn('"pages.design_studio.page", "DesignStudioPage"', source)
        self.assertNotIn('"InteractionsPage"', source)
        self.assertIn('data-tab="interactions" aria-selected="true"', self.html)
        self.assertIn("interactions", tool_dirs.BUCKETS)

    def test_what_the_page_says_is_translated(self):
        parser = _Said()
        parser.feed(self.html)
        said = ["Interactions", more_tools.BLURBS["interactions"], "Interaction added on V2.",
                "Interaction replaced on V1.", "Couldn't add the interaction", "Adding the interaction…",
                "Adding an interaction", f"An interaction holds up to {M.MAX_ELEMENTS} shapes.", "Play", "Pause",
                "Choose an image", "Images (*.png *.jpg *.jpeg)", "An image's colour is the colour of its glow",
                "Buddy couldn't read that image. PNG and JPEG images can be added.",
                'The image "logo.png" isn\'t where it was. Put it back, or remove it from the stage.',
                "Back to the template.", "Undo", "Only this shape", "Lifts (%)", "Outlined (%)", "States…",
                "Show the shapes as they look", *parser.texts,
                *re.findall(r'(?:textContent =|toast\(|[?:]) "([A-Z][^"]{8,})"', self.script)]
        self.assertGreater(len(said), 60)
        for text in ("Stop", "Record the cursor", "Nothing was recorded.",
                     "Drag a shape to move it, the corner of the picked one to resize it."):
            self.assertTrue(text in said or f'"{text}"' in self.script, text)
            said.append(text)
        for text in said:
            with self.subTest(text):
                self.assertNotEqual(i18n.translate(text, "日本語"), text)

    def test_every_field_in_the_page_is_a_field_of_the_interaction(self):
        keys = set(re.findall(r'data-key="(\w+)"', self.html))
        self.assertLessEqual(keys, set(M.DEFAULTS) - {"elements", "path", "clicks"})
        self.assertEqual(keys, {"outline_colour", "outline_width", "hover_seconds", "press_seconds", "seconds",
                                "cursor", "cursor_size", "font", "style", "label_size", "label_colour"})
        # The States pop: each of a state's numbers, for Hover and for Pressed, within the model's spans.
        states = self.html.split('id="states-pop"')[1].split("</section>")[0]
        self.assertEqual(re.findall(r'data-state="(\w+)"', states), list(M.STATES))
        for part in states.split("data-state=")[1:]:
            sliders = re.findall(r'data-answer="(\w+)" min="([-\d.]+)" max="([-\d.]+)"', part)
            self.assertEqual({k: (float(lo), float(hi)) for k, lo, hi in sliders}, M.ANSWER_KEYS)
        self.assertEqual(re.findall(r'data-value="(\w+)"', self.html.split('id="look"')[1].split("</div>")[0]),
                         ["rest", "hover", "press"])
        self.assertEqual(re.findall(r'data-kind="(\w+)"', self.html), list(M.KINDS))
        self.assertEqual(set(comp.FORMATS), set(M.IMAGE_TYPES))
        from pages.interactions.page import InteractionsPage
        self.assertEqual(InteractionsPage.IMAGE_FILTER, "Images (" + " ".join(f"*{e}" for e in M.IMAGE_TYPES) + ")")
        self.assertEqual(re.findall(r'<button type="button" data-value="(\w+)"', self.html.split('data-key="cursor"')[1])[:4],
                         list(M.CURSORS))

    def test_the_detailed_options_are_in_panels_of_their_own(self):
        page, states, timing, outline, labels = re.split(r'id="(?:states|timing|outline|labels)-pop"', self.html)
        self.assertIn('data-key="cursor"', page)
        self.assertIn('id="look"', page)                          # the chips stay on the page, the sliders in States…
        self.assertNotIn("data-answer", page)
        self.assertIn('data-answer="grow"', states)
        self.assertIn('id="own-states"', states)
        for key in ("hover_seconds", "press_seconds", "seconds"):
            self.assertIn(f'data-key="{key}"', timing)
        for key in ("outline_colour", "outline_width"):
            self.assertIn(f'data-key="{key}"', outline)
        for key in ("font", "style", "label_size", "label_colour"):
            self.assertIn(f'data-key="{key}"', labels)
        self.assertEqual(self.html.count('class="dg-pop"'), self.html.count("dg-pop-close"))

    def test_the_view_answers_by_the_models_rule(self):
        # The same sums as model.amounts(): a share of the last frames, the hover eased.
        for said in ("raw * raw * (3 - 2 * raw)", "p.hover_frames", "p.press_frames", "at.down && inside(it, at.x, at.y)",
                     "(now === undefined ? frame : now) - k", "(it && it.states) || state.spec.answers",
                     # model.look(), sum for sum.
                     "scale: 1 + h.grow / 100 * hover + p.grow / 100 * press", "lift: h.lift * hover + p.lift * press",
                     "glow: Math.min(1, Math.max(0, h.glow / 100 * hover + p.glow / 100 * press))",
                     "outline: Math.min(1, Math.max(0, h.outline / 100 * hover + p.outline / 100 * press))"):
            self.assertIn(said, self.script)

    def test_ask_buddy_is_told_what_it_makes(self):
        tool = get_tool("interactions")
        self.assertTrue(tool.needs_resolve)
        told = " ".join([tool.summary, *tool.capabilities, tool.notes])
        for word in ("Button, Card, Circle or Image", "PNG or JPEG", "a Loader", "Try it", "Record the cursor", "new Fusion clip", "a tool named Cursor",
                     "Point In 1", "still answer", "Timing…", "Label text…", "Windows' pointer", "a Mac's pointer", "Outline…",
                     "States…", "Rest, Hover and Pressed", "Only this shape", "Lifts", "Point In 2", "Number In 5",
                     "<Name>State", "saved before states"):
            self.assertIn(word, told)


class Mem(dict):
    def save(self):
        self.saved = dict(self)


class Host:
    def __init__(self, controller=None):
        self.buckets = {}
        self.controller, self.connected = controller, controller is not None
        self.busy = []

    def tool_settings(self, tool_id, defaults=None):
        return self.buckets.setdefault(tool_id, Mem(defaults or {}))

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")

    def ensure_connected(self):
        if self.controller is None:
            raise ResolveConnectionError("Resolve isn't running.")
        return self.controller

    def set_busy(self, on, message=""):
        self.busy.append((on, message))


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class PageTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": self.home.name, "BUDDY_RESOLVE_READER": "off"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.open()

    def open(self, controller=None):
        from pages.interactions.page import InteractionsPage
        self.host = Host(controller)
        self.page = InteractionsPage(self.host)
        self.addCleanup(self.page.deleteLater)
        self.sent = []
        self.page.emit = lambda name, data=None: self.sent.append((name, data))
        if controller is not None:
            self.reader = self.page._calls.reader = ChildReader(controller, JOBS)

    def last(self, name):
        return next(data for n, data in reversed(self.sent) if n == name)

    def test_its_a_real_page_under_editing_tools(self):
        self.assertFalse(self.page.is_placeholder)
        self.assertEqual((self.page.tool_id, self.page.category), ("interactions", "Editing Tools"))

    def test_the_state_carries_the_interaction_and_what_the_view_plays(self):
        self.page.web_ready()
        state = self.last("state")
        self.assertEqual(state["spec"], M.clean({}))
        self.assertTrue(state["is_template"])
        self.assertFalse(state["connected"])
        preview = state["preview"]
        self.assertEqual((preview["frame"], preview["fps"], preview["total"]), ([1920, 1080], 24.0, 120))
        self.assertEqual((len(preview["cursor"]), len(preview["down"])), (120, 120))
        self.assertEqual([tuple(p) for p in preview["cursor"]],
                         [(round(x, 3), round(y, 3)) for x, y in M.cursor(state["spec"], 24.0, 120)])
        self.assertEqual(preview["down"], [int(d) for d in M.down(state["spec"], 24.0, 120)])
        self.assertEqual((preview["hover_frames"], preview["press_frames"]), (5, 2))
        self.assertEqual(state["face"], {"family": "Arial", "weight": 700, "italic": False})
        self.assertEqual((state["kinds"], state["full"]), (list(M.KINDS), False))
        self.assertIn("Arial", self.last("fonts")["fonts"])

    def test_a_field_changed_is_cleaned_kept_and_sent_back(self):
        self.page.on_answers({"id": "", "state": "hover", "key": "grow", "value": "999"})
        self.page.on_set({"key": "cursor", "value": "dot"})
        self.page.on_set({"key": "elements", "value": []})               # the shapes have actions of their own
        self.page.on_set({"key": "answers", "value": {}})                # and so have the states
        self.page.on_set({"key": "hover_grow", "value": 1})              # a key there isn't any more
        self.page.on_set({"key": "nonsense", "value": 1})
        spec = self.last("state")["spec"]
        self.assertEqual((spec["answers"]["hover"]["grow"], spec["cursor"], len(spec["elements"])), (50.0, "dot", 4))
        self.assertEqual(self.host.buckets["interactions"].saved["spec"], spec)
        again = type(self.page)(self.host)
        self.addCleanup(again.deleteLater)
        self.assertEqual(again.spec, spec)

    def test_states_are_everyones_or_a_shapes_own_and_each_change_is_a_step(self):
        from pages.data_graphics import graphic_page
        clock = [100.0]
        with mock.patch.object(graphic_page.time, "monotonic", side_effect=lambda: clock[0]):
            for value in (10, 12, 14):                                   # a slider dragged: one step
                self.page.on_answers({"id": "", "state": "press", "key": "lift", "value": value})
                clock[0] += 0.05
            self.assertEqual(len(self.page.undo_steps), 1)
            self.assertEqual(self.last("state")["spec"]["answers"]["press"]["lift"], 14.0)
            clock[0] += 5
            self.page.on_answers({"id": "e3", "state": "hover", "key": "glow", "value": 20})
            spec = self.last("state")["spec"]
            self.assertEqual(spec["elements"][2]["states"]["hover"]["glow"], 20.0)
            self.assertEqual(spec["elements"][2]["states"]["press"]["lift"], 14.0)    # its own started as everyone's
            self.assertIsNone(spec["elements"][1]["states"])
            for bad in (None, {}, {"id": "e9", "state": "hover", "key": "glow", "value": 1},
                        {"id": "", "state": "rest", "key": "glow", "value": 1}, {"id": "e3", "state": "hover", "key": "x"}):
                self.page.on_answers(bad)
            self.assertEqual(self.page.spec, spec)
            clock[0] += 5
            self.page.on_answers({"id": "e3", "state": "hover", "key": "reset", "value": None})
            self.assertIsNone(self.page.spec["elements"][2]["states"])
            self.page.on_undo()
            self.assertEqual(self.page.spec, spec)
        self.assertEqual(self.last("state")["answer_keys"], {k: list(v) for k, v in M.ANSWER_KEYS.items()})

    def test_an_interaction_saved_before_states_opens_looking_the_same(self):
        from pages.interactions.page import InteractionsPage
        old = {key: value for key, value in M.clean({}).items() if key != "answers"}
        old.update({"hover_grow": 15, "hover_glow": 30, "hover_outline": 20, "press_shrink": 9})
        host = Host()
        host.buckets["interactions"] = Mem({"spec": old})
        page = InteractionsPage(host)
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.spec["answers"], {"hover": {"grow": 15.0, "lift": 0.0, "glow": 30.0, "outline": 20.0},
                                                "press": {"grow": -9.0, "lift": 0.0, "glow": 0.0, "outline": 0.0}})
        self.assertNotIn("hover_grow", page.spec)

    def test_the_views_made_up_state_is_the_pages(self):
        self.page.web_ready()
        self.assertEqual(set(state_of(self.page.spec)), set(self.last("state")))

    def test_shapes_are_added_changed_raised_and_removed(self):
        self.page.on_new_element({"kind": "circle"})
        self.assertEqual(self.last("picked"), {"id": "e5"})
        self.page.on_element({"id": "e5", "key": "box", "value": {"x": 20, "y": 30, "w": 12}})
        self.page.on_element({"id": "e5", "key": "label", "value": "Go"})
        self.page.on_element({"id": "e9", "key": "label", "value": "nobody"})
        self.page.on_element(None)
        circle = self.last("state")["spec"]["elements"][4]
        self.assertEqual((circle["x"], circle["y"], circle["w"], circle["label"]), (20.0, 30.0, 12.0, "Go"))
        self.page.on_front_element({"id": "e2"})
        self.assertEqual(self.last("picked"), {"id": "e5"})
        self.assertEqual(self.last("state")["spec"]["elements"][4]["label"], "Play")
        self.page.on_remove_element({"id": "e5"})
        self.page.on_remove_element({"id": "e9"})
        self.assertEqual([e["label"] for e in self.last("state")["spec"]["elements"]], ["", "Save", "Share", "Go"])
        self.page.on_new_element({"kind": "star"})
        self.assertEqual(len(self.page.spec["elements"]), 4)

    def picture(self, name="logo.png", size=(400, 200)):
        """A made-up picture in the test's own folder: its path."""
        from PySide6.QtGui import QImage
        path = os.path.join(self.home.name, name)
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(0xffff8a1e)
        self.assertTrue(image.save(path))
        return path

    def test_an_image_is_picked_from_a_file_and_boxed_to_its_shape(self):
        from pages.interactions import page as page_module
        path = self.picture()
        with mock.patch.object(page_module.QFileDialog, "getOpenFileName", return_value=(path, "")) as asked:
            self.page.on_new_element({"kind": "image"})
        self.assertEqual(asked.call_count, 1)
        el = self.page.spec["elements"][-1]
        self.assertEqual((el["kind"], el["path"], el["px"], el["w"], el["h"]),
                         ("image", os.path.normpath(path), [400, 200], 24.0, 21.333))
        self.assertEqual(self.last("picked"), {"id": "e5"})
        state = self.last("state")
        self.assertEqual(list(state["images"]), ["e5"])
        self.assertTrue(state["images"]["e5"].startswith("file:///"))
        self.assertTrue(state["images"]["e5"].endswith("/logo.png"))
        self.assertEqual(self.host.buckets["interactions"]["image_dir"], os.path.dirname(path))
        # Nothing picked: nothing added.
        with mock.patch.object(page_module.QFileDialog, "getOpenFileName", return_value=("", "")):
            self.page.on_new_element({"kind": "image"})
        self.assertEqual(len(self.page.spec["elements"]), 5)
        self.page.on_undo()
        self.assertEqual(self.page.spec, M.clean({}))

    def test_images_dropped_on_the_page_become_shapes_and_other_files_are_said(self):
        self.assertTrue(self.page.file_drops)
        self.assertTrue(self.page.accepts_drops())
        notes = os.path.join(self.home.name, "notes.txt")
        broken = os.path.join(self.home.name, "broken.png")
        for made_up in (notes, broken):
            with open(made_up, "w", encoding="utf-8") as fh:
                fh.write("not a picture")
        self.page.on_files_dropped([self.picture("a.png"), notes, self.picture("b.jpg", (300, 300)), broken])
        kinds = [(e["kind"], os.path.basename(e.get("path", ""))) for e in self.page.spec["elements"][4:]]
        self.assertEqual(kinds, [("image", "a.png"), ("image", "b.jpg")])
        self.assertEqual(self.last("toast"), {"text": "Buddy couldn't read that image. PNG and JPEG images can be added."})
        self.page.adding = True
        self.assertFalse(self.page.accepts_drops())

    def test_its_said_when_theres_no_room_for_another_shape(self):
        for _ in range(M.MAX_ELEMENTS):
            self.page.on_new_element({"kind": "button"})
        self.assertEqual(len(self.page.spec["elements"]), M.MAX_ELEMENTS)
        self.assertTrue(self.page._state()["full"])
        self.assertEqual(self.last("toast"), {"text": f"An interaction holds up to {M.MAX_ELEMENTS} shapes."})

    def test_a_recording_becomes_the_cursor_and_the_clips_length(self):
        samples = [[i / 20, 10 + i, 40] for i in range(61)]              # three seconds, a straight line
        self.page.on_recorded({"samples": samples, "clicks": [[1.0, 1.2]], "seconds": 3.0})
        state = self.last("state")
        self.assertEqual(state["spec"]["path"], [[0.0, 10.0, 40.0], [3.0, 70.0, 40.0]])
        self.assertEqual((state["spec"]["clicks"], state["spec"]["seconds"]), ([[1.0, 1.2]], 3.0))
        self.assertEqual(state["preview"]["total"], 72)
        self.assertFalse(state["is_template"])
        for bad in (None, {}, {"samples": []}, {"samples": "lots"}):
            self.page.on_recorded(bad)
        self.assertEqual(self.page.spec, state["spec"])
        self.page.on_undo()                                              # one step: the template's cursor is back
        self.assertEqual(self.page.spec, M.clean({}))

    def test_a_drag_is_one_step_and_reset_is_one_step(self):
        from pages.data_graphics import graphic_page
        clock = [100.0]
        with mock.patch.object(graphic_page.time, "monotonic", side_effect=lambda: clock[0]):
            for x in (33, 34, 35, 36):                                   # a shape dragged: many boxes, one step
                self.page.on_element({"id": "e2", "key": "box", "value": {"x": x}})
                clock[0] += 0.05
            self.assertEqual(len(self.page.undo_steps), 1)
            clock[0] += 5
            self.page.on_element({"id": "e3", "key": "colour", "value": "#112233"})
            self.assertEqual(len(self.page.undo_steps), 2)
            mine = self.page.spec
            self.page.on_reset()
            self.assertEqual(self.page.spec, M.clean({}))
            self.page.on_undo()
            self.assertEqual(self.page.spec, mine)
            self.page.on_undo()
            self.page.on_undo()
            self.assertEqual(self.page.spec, M.clean({}))
            self.page.on_redo()
            self.assertEqual(self.page.spec["elements"][1]["x"], 36.0)

    def test_the_timeline_is_read_as_the_page_opens(self):
        controller, _timeline = studio(fps=30, size=(1080, 1920), name="Short")
        self.open(controller)
        self.page._read(connect=True)
        self.reader.run()
        state = self.last("state")
        self.assertEqual((state["connected"], state["timeline"], state["frame"], state["fps"]),
                         (True, "Short", [1080, 1920], 30.0))
        self.assertEqual(state["preview"]["total"], 150)
        self.assertEqual(self.last("fonts")["fonts"]["Inter"], ["Black"])

    def test_add_puts_it_on_the_timeline_through_the_reader(self):
        controller, timeline = studio()
        self.open(controller)
        self.page.on_answers({"id": "", "state": "hover", "key": "grow", "value": 20})
        self.page.on_add()
        self.assertTrue(self.page.adding)
        self.assertEqual(self.page.working_on(), "Adding an interaction")
        self.page.on_answers({"id": "", "state": "hover", "key": "grow", "value": 1})     # nothing changes while it's being added
        self.page.on_answers({"id": "e2", "state": "hover", "key": "grow", "value": 1})
        self.page.on_new_element({"kind": "card"})
        self.reader.run()
        self.assertFalse(self.page.adding)
        name, args = self.reader.asked[-1]
        self.assertEqual((name, args["spec"]["answers"]["hover"]["grow"], args["replace"]), ("interactions.add", 20.0, True))
        self.assertIsNone(args["spec"]["elements"][1]["states"])
        (item,) = timeline.tracks[1]
        self.assertEqual(item.comp.GetData(comp.TAG), M.interaction_id(self.page.spec, FRAME, 24.0))
        self.assertEqual(self.last("toast"), {"text": "Interaction added on V1."})
        self.page.on_add()
        self.reader.run()
        self.assertEqual(self.last("toast"), {"text": "Interaction replaced on V1."})

    def test_not_connected_is_said_and_nothing_is_added(self):
        self.page.on_add()
        self.assertFalse(self.page.adding)
        self.assertEqual(self.last("alert")["title"], "Not connected")

    def test_a_failure_is_said(self):
        controller, timeline = studio()
        timeline.takes_comps = False
        self.open(controller)
        self.page.on_add()
        self.reader.run()
        self.assertEqual(self.last("alert")["title"], "Couldn't add the interaction")
        self.assertEqual(timeline.tracks[1], [])


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class ViewTests(unittest.TestCase):
    """The page opened on its own, offscreen, handed the state Python sends (state_of):
    what it draws and what it sends back."""

    @classmethod
    def setUpClass(cls):
        from test_audio_assistant import qt_app
        cls.app = qt_app()
        if not isinstance(cls.app, QApplication):     # another file's test made a bare one
            raise unittest.SkipTest("a QCoreApplication was made first; a web view needs a QApplication")

    def setUp(self):
        import json
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from shiboken6 import delete
        self.json = json
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.view = QWebEngineView()
        self.addCleanup(delete, self.view)
        self.view.resize(1400, 900)
        loop, loaded = QEventLoop(), []
        self.view.loadFinished.connect(lambda ok: (loaded.append(ok), loop.quit()))
        self.view.setUrl(QUrl.fromLocalFile(str(ROOT / "web" / "index.html")))
        self.view.show()
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        self.assertEqual(loaded, [True])
        self.js("window.__sent = []; Buddy.send = (name, data) => window.__sent.push([name, JSON.parse(JSON.stringify("
                "data === undefined ? null : data))]); return true;")

    def js(self, body):
        from PySide6.QtCore import QEventLoop, QTimer
        loop, result = QEventLoop(), []
        self.view.page().runJavaScript("JSON.stringify((() => {" + body + "})())", 0,
                                       lambda value: (result.append(value), loop.quit()))
        QTimer.singleShot(8000, loop.quit)
        loop.exec()
        self.assertTrue(result, "JavaScript timed out")
        return self.json.loads(result[0]) if result[0] is not None else None

    def wait(self, ms):
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def receive(self, name, payload):
        self.js(f"Buddy.receive({self.json.dumps(name)}, {self.json.dumps(payload)}); return true;")

    def show(self, spec=None):
        self.spec = spec or M.clean({})
        self.receive("state", state_of(self.spec))

    def sent(self, name=None):
        return [s for s in self.js("return window.__sent;") if name is None or s[0] == name]

    def forget(self):
        self.js("window.__sent.length = 0; return true;")

    def click(self, selector):
        self.js(f"document.querySelector({self.json.dumps(selector)}).click(); return true;")

    def slide(self, state, key, value):
        self.js(f"const n = document.querySelector('[data-state={state}] [data-answer={key}]'); n.value = '{value}';"
                " n.dispatchEvent(new Event('input')); return true;")

    def look(self, eid, f=None):
        return self.js(f"return interactionsView.lookOf({self.json.dumps(eid)}{'' if f is None else f', {f}'});")

    def test_nothing_is_sent_on_load_and_the_chips_show_a_state_still(self):
        self.show()
        self.wait(150)
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.js("return interactionsView.info();")["view"], "frame")
        rest = M.look(self.spec["answers"], 0, 0)
        self.assertEqual(self.look("e2"), rest)                         # the template's first frame: nothing hovered
        self.click("#look [data-value=hover]")
        info = self.js("return interactionsView.info();")
        self.assertEqual((info["view"], info["look"], info["playing"]), ("state", "hover", False))
        for eid in ("e2", "e3", "e4"):                                   # every one that answers, nothing picked
            self.assertEqual(self.look(eid), M.look(self.spec["answers"], 1, 0))
        self.assertEqual(self.look("e1"), rest)                          # the panel stays still
        self.click("#look [data-value=press]")
        self.assertEqual(self.look("e3"), M.look(self.spec["answers"], 1, 1))
        pressed = self.js("return [...document.querySelectorAll('#look button')].map(b => b.getAttribute('aria-pressed'));")
        self.assertEqual(pressed, ["false", "false", "true"])
        # With a shape picked, only it shows the state.
        self.receive("picked", {"id": "e3"})
        self.assertEqual(self.look("e2"), rest)
        self.assertEqual(self.look("e3"), M.look(self.spec["answers"], 1, 1))
        self.click("#look [data-value=rest]")
        self.assertEqual(self.look("e3"), rest)
        self.assertEqual(self.sent(), [])                                # showing a state is the view's own

    def test_the_states_pop_draws_the_three_states_and_sends_what_is_changed(self):
        self.show()
        self.click("#edit-states")
        self.assertEqual(self.js("return document.getElementById('states-pop').hidden;"), False)
        rest, hover, press = self.js("return interactionsView.pictures();")
        self.assertGreater(rest, 100)                                    # a sample button, nothing picked
        self.assertGreater(hover, rest)                                  # grown, and glowing
        self.assertEqual(self.js("return document.getElementById('own-states').disabled;"), True)
        values = self.js("return [...document.querySelectorAll('[data-state] [data-answer]')].map(n => Number(n.value));")
        self.assertEqual(values, [self.spec["answers"][s][k] for s in M.STATES for k in M.ANSWER_KEYS])
        # Nothing picked: everyone's.
        self.slide("press", "lift", 3)
        self.wait(120)
        self.assertEqual(self.sent(), [["answers", {"id": "", "state": "press", "key": "lift", "value": 3}]])
        self.forget()
        # Picked, Only this shape gives it its own (starting as everyone's) ...
        self.receive("picked", {"id": "e3"})
        self.assertEqual(self.js("return document.getElementById('own-states').checked;"), False)
        self.js("const c = document.getElementById('own-states'); c.checked = true; c.dispatchEvent(new Event('change'));"
                " return true;")
        self.assertEqual(self.sent(), [["answers", {"id": "e3", "state": "hover", "key": "grow", "value": 8}]])
        self.forget()
        # ... and with its own, a slider changes its alone.
        mine = M.with_answers(self.spec, "e3", "hover", "grow", 8)
        self.show(mine)
        self.assertEqual(self.js("return document.getElementById('own-states').checked;"), True)
        self.slide("hover", "outline", 40)
        self.wait(120)
        self.assertEqual(self.sent(), [["answers", {"id": "e3", "state": "hover", "key": "outline", "value": 40}]])
        self.forget()
        self.js("const c = document.getElementById('own-states'); c.checked = false; c.dispatchEvent(new Event('change'));"
                " return true;")
        self.assertEqual(self.sent(), [["answers", {"id": "e3", "state": "hover", "key": "reset", "value": None}]])
        # The pictures are of the picked shape, by its own states.
        big = M.with_answers(mine, "e3", "hover", "grow", 50)
        self.show(big)
        before = self.js("return interactionsView.pictures();")
        self.show(M.with_answers(big, "e3", "hover", "grow", -50))
        after = self.js("return interactionsView.pictures();")
        self.assertEqual(before[0], after[0])
        self.assertGreater(before[1], after[1])

    def test_a_change_of_a_value_never_starts_playback(self):
        self.show()
        self.js("const s = document.getElementById('scrub'); s.value = '60'; s.dispatchEvent(new Event('input')); return true;")
        self.assertEqual(self.js("return interactionsView.info();")["frame"], 60)
        self.slide("hover", "grow", 20)
        self.wait(120)
        self.show(M.with_answers(self.spec, "", "hover", "grow", 20))
        self.wait(200)
        info = self.js("return interactionsView.info();")
        self.assertEqual((info["playing"], info["frame"], info["view"]), (False, 60, "frame"))
        self.click("#look [data-value=hover]")
        self.show(M.with_answers(self.spec, "", "hover", "glow", 20))
        self.wait(200)
        info = self.js("return interactionsView.info();")
        self.assertEqual((info["playing"], info["frame"], info["view"]), (False, 60, "state"))

    def test_the_preview_draws_each_shape_by_its_own_answers(self):
        spec = M.with_answers(M.with_answers(M.clean({}), "e3", "press", "grow", 25), "e3", "hover", "lift", 3)
        spec = M.with_answers(spec, "", "hover", "outline", 50)
        self.show(spec)
        fps, total = 24.0, M.frames(spec, 24.0)
        want = M.looks(spec, FRAME, fps, total)
        frames = sorted({0, 20, 30, 40, 50, 55, 58, 59, 60, 61, 62, 64, 70, 80, 100, total - 1})
        for eid in ("e2", "e3", "e4"):
            got = self.js(f"return {self.json.dumps(frames)}.map(f => interactionsView.lookOf({self.json.dumps(eid)}, f));")
            for f, mine in zip(frames, got):
                for key in ("scale", "lift", "glow", "outline"):
                    self.assertAlmostEqual(mine[key], want[eid][f][key], delta=1e-9, msg=f"{eid} frame {f} {key}")
        # The middle button, clicked at 2.4 s, grows where the others would dip.
        self.assertGreater(want["e3"][60]["scale"], 1.0)
        self.assertGreater(want["e3"][60]["lift"], 0.0)

    def test_a_lift_draws_the_shape_higher(self):
        spec = M.clean({"elements": [{"kind": "card", "x": 50, "y": 50, "w": 20, "h": 10, "radius": 0, "colour": "#ff0000"}],
                        "cursor": "none"})
        spec = M.with_answers(M.with_answers(spec, "", "hover", "glow", 0), "", "hover", "lift", 10)
        self.show(spec)

        def red(y):
            return self.js(f"const c = interactionsView.picture(), d = c.getContext('2d');"
                           f" return d.getImageData(Math.round(c.width / 2), Math.round(c.height * {y}), 1, 1).data[0];")
        self.assertGreater(red(0.5), 200)                               # at rest, on its place
        self.assertLess(red(0.42), 50)
        self.click("#look [data-value=hover]")
        self.assertGreater(red(0.42), 200)                              # hovered, 10 % of the height higher
        self.assertLess(red(0.54), 50)


if __name__ == "__main__":
    unittest.main()
