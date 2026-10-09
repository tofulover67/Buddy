"""Animation > Titles, the kinetic arrangement (titles/kinetic.py): blocks by the words'
marks, each sized to the edge it joins and turned with it, a camera that settles on each
block upright, the clock that moves it on, and the Text+ comp keyed from both. A fake
measure (every letter 0.55 of the size) - the geometry, not the fonts."""

import math
import os
import re
import unittest

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False
from pages.text_animator.titles import composer as C
from pages.text_animator.titles import kinetic as K
from pages.text_animator.titles import looks as L
from pages.text_animator.titles import textplus_comp as TP
from pages.text_animator.titles import timing as T

LANDSCAPE, PORTRAIT = (1920, 1080), (1080, 1920)
HOOK = "Stop scrolling this one trick changes everything"


def fake(text, font, size):
    return len(text) * size * 0.55


def kinetic_doc(text=HOOK, look="hormozi", **changes):
    doc = C.new_doc(text, look)
    doc["arrange"] = "kinetic"
    doc.update(changes)
    return doc


def compose(frame=LANDSCAPE, **changes):
    doc = kinetic_doc(**changes)
    return doc, C.compose(doc, frame, fake)


def corners(b):
    """A box's corners, turned about its centre."""
    cx, cy, r = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2, math.radians(b.get("rot", 0.0))
    c, s = math.cos(r), math.sin(r)
    return [(cx + u * b["w"] / 2 * c - v * b["h"] / 2 * s, cy + u * b["w"] / 2 * s + v * b["h"] / 2 * c)
            for u, v in ((-1, -1), (1, -1), (-1, 1), (1, 1))]


class MarkTests(unittest.TestCase):
    def test_suggestions_turn_on_long_words_and_take_turns(self):
        marks = K.suggest(C.tokenize(HOOK))
        self.assertNotIn(0, marks)
        self.assertEqual(marks[1], "cw")                    # "scrolling": long
        turns = [m for _i, m in sorted(marks.items()) if m != "line"]
        self.assertTrue(all(a != b for a, b in zip(turns, turns[1:])), turns)
        self.assertIn(marks[max(marks)], ("cw", "ccw"))     # the payoff turns

    def test_the_persons_marks_win_and_none_takes_one_away(self):
        tokens = C.tokenize(HOOK)
        marks = K.marks_of(tokens, {"turns": {"one#1": "ccw", "scrolling#1": "none", "stop#1": "cw"}})
        self.assertEqual(marks[3], ("ccw", "manual"))
        self.assertEqual(marks[1], (None, "manual"))
        self.assertEqual(marks[0], (None, "auto"))          # the first word: never marked


class LayoutTests(unittest.TestCase):
    def test_blocks_follow_the_marks(self):
        doc, layout = compose(turns={"scrolling#1": "cw", "this#1": "line", "trick#1": "none", "changes#1": "none",
                                     "one#1": "none", "everything#1": "ccw"})
        self.assertEqual(layout["lines"], [["stop#1"], ["scrolling#1"], ["this#1", "one#1", "trick#1", "changes#1"],
                                           ["everything#1"]])
        self.assertEqual([b["how"] for b in layout["kinetic"]["blocks"]], ["first", "cw", "line", "ccw"])
        self.assertEqual([t["mark"] for t in layout["tokens"]][:2], [None, "cw"])
        self.assertEqual(layout["alternates"], [])
        self.assertEqual(layout["bars"], [])

    def test_a_turned_block_is_as_long_as_the_side_it_joins(self):
        _doc, layout = compose()
        blocks = layout["kinetic"]["blocks"]
        for prev, block in zip(blocks, blocks[1:]):
            own = block["own"]
            length = own[2] - own[0]                      # along its reading, in its own upright frame
            if block["how"] == "line":
                self.assertAlmostEqual(length, prev["all"][2] - prev["all"][0], delta=0.5, msg=block["first"])
            else:
                self.assertAlmostEqual(length, prev["all"][3] - prev["all"][1], delta=0.5, msg=block["first"])

    def test_turned_words_are_turned_and_none_upside_down(self):
        _doc, layout = compose()
        hows = {b["first"]: b["how"] for b in layout["kinetic"]["blocks"]}
        self.assertEqual(hows["scrolling#1"], "cw")
        rot = {b["id"]: b["rot"] for b in layout["boxes"]}
        self.assertEqual(rot["stop#1"], 0.0)
        self.assertEqual(rot["scrolling#1"], 90.0)
        for b in K.at_rest(layout)["boxes"]:
            self.assertLessEqual(abs((b["rot"] + 180) % 360 - 180), 90.0 + 1e-9, b["id"])

    def test_blocks_never_overlap(self):
        _doc, layout = compose()
        for which in K.CAMERAS:
            boxes = K.at_rest({**layout, "kinetic": {**layout["kinetic"], "camera": which}})["boxes"]
            by_block = {}
            for b in boxes:
                by_block.setdefault(b["line"], []).extend(corners(b))
            spans = {k: (min(p[0] for p in ps), min(p[1] for p in ps), max(p[0] for p in ps), max(p[1] for p in ps))
                     for k, ps in by_block.items()}
            for a in spans:
                for b in spans:
                    if a < b:
                        A, B = spans[a], spans[b]
                        overlap = min(A[2], B[2]) - max(A[0], B[0]), min(A[3], B[3]) - max(A[1], B[1])
                        self.assertFalse(overlap[0] > 1 and overlap[1] > 1, (which, a, b))

    def test_keep_all_in_view_has_every_word_in_the_frame(self):
        for frame in (LANDSCAPE, PORTRAIT):
            _doc, layout = compose(frame, camera="all")
            for b in K.at_rest(layout)["boxes"]:
                for x, y in corners(b):
                    self.assertTrue(-0.5 <= x <= frame[0] + 0.5 and -0.5 <= y <= frame[1] + 0.5, (frame, b["id"]))

    def test_the_last_block_settles_upright_and_centred(self):
        for which, fill in K.FILL.items():
            _doc, layout = compose(camera=which)
            last = layout["lines"][-1]
            boxes = [b for b in K.at_rest(layout)["boxes"] if b["id"] in last]
            for b in boxes:
                self.assertAlmostEqual((b["rot"] + 180) % 360 - 180, 0.0, places=6)
            x0, x1 = min(b["x"] for b in boxes), max(b["x"] + b["w"] for b in boxes)
            self.assertAlmostEqual((x0 + x1) / 2, LANDSCAPE[0] / 2, delta=2)
            if which == "each":
                self.assertAlmostEqual(x1 - x0, LANDSCAPE[0] * fill, delta=LANDSCAPE[0] * 0.02)

    def test_a_nudge_moves_along_its_block(self):
        """A turned block's word nudged "right" (its own reading direction) moves down the
        world, as that block reads down."""
        _doc, base = compose()
        _doc, moved = compose(over={**C.new_doc("")["over"], "offset": {"scrolling#1": [0.5, 0.0]}})
        a = next(b for b in base["boxes"] if b["id"] == "scrolling#1")
        b = next(b for b in moved["boxes"] if b["id"] == "scrolling#1")
        self.assertAlmostEqual(b["x"], a["x"], delta=0.01)
        self.assertAlmostEqual(b["y"] - a["y"], 0.5 * a["size"], delta=0.01)

    def test_the_camera_reframes_on_a_nudged_word(self):
        """Zoomed to each: the last block's word nudged still settles in the frame's
        centre - the camera follows it - and the blocks before it move the other way."""
        _doc, base = compose()
        last = base["lines"][-1][0]
        _doc, moved = compose(over={**C.new_doc("")["over"], "offset": {last: [0.4, -0.3]}})
        a = {b["id"]: b for b in K.at_rest(base)["boxes"]}
        b = {b["id"]: b for b in K.at_rest(moved)["boxes"]}
        for boxes in (a, b):
            box = boxes[last]
            self.assertAlmostEqual(box["x"] + box["w"] / 2, LANDSCAPE[0] / 2, delta=0.5)
            self.assertAlmostEqual(box["y"] + box["h"] / 2, LANDSCAPE[1] / 2, delta=0.5)
        size = a[last]["size"]
        self.assertAlmostEqual(b["stop#1"]["x"] - a["stop#1"]["x"], -0.4 * size, delta=0.5)
        self.assertAlmostEqual(b["stop#1"]["y"] - a["stop#1"]["y"], 0.3 * size, delta=0.5)

    def test_keeping_all_in_view_takes_in_a_word_nudged_out(self):
        _doc, layout = compose(camera="all", over={**C.new_doc("")["over"], "offset": {"stop#1": [0.0, 3.0]}})
        for b in K.at_rest(layout)["boxes"]:
            for x, y in corners(b):
                self.assertTrue(-0.5 <= x <= LANDSCAPE[0] + 0.5 and -0.5 <= y <= LANDSCAPE[1] + 0.5, b["id"])

    def test_a_turned_word_is_framed_turned(self):
        """Its own turn (the person's) counts: zoomed to each, the turned last word's
        corners fill the frame as far as the fill, no further."""
        _doc, base = compose()
        last = base["lines"][-1][0]
        _doc, layout = compose(over={**C.new_doc("")["over"], "rot": {last: 30.0}})
        box = next(b for b in K.at_rest(layout)["boxes"] if b["id"] == last)
        pts = corners(box)
        w = max(x for x, _y in pts) - min(x for x, _y in pts)
        h = max(y for _x, y in pts) - min(y for _x, y in pts)
        fill = K.FILL["each"]
        self.assertAlmostEqual(max(w / LANDSCAPE[0], h / LANDSCAPE[1]), fill, delta=0.005)

    def test_every_look_lays_out_kinetic(self):
        for look in L.LOOKS:
            for frame in (LANDSCAPE, PORTRAIT):
                doc = kinetic_doc(look=look)
                layout = C.compose(doc, frame, fake)
                self.assertEqual(len(layout["boxes"]), 7, look)
                clock = T.timing(doc, layout, 8.0)
                self.assertIn("camera", clock)


class CameraTests(unittest.TestCase):
    def test_holds_then_eases_onto_each_block(self):
        doc, layout = compose()
        clock = T.timing(doc, layout, 8.0)
        keys, blocks = clock["camera"], layout["kinetic"]["blocks"]
        self.assertEqual(len(keys), 1 + 2 * (len(blocks) - 1))
        starts = {w["id"]: w["start"] for w in clock["words"]}
        for k, block in enumerate(blocks[1:], 1):
            hold, there = keys[2 * k - 1], keys[2 * k]
            self.assertAlmostEqual(hold["t"], starts[block["first"]])
            self.assertAlmostEqual(there["t"] - hold["t"], K.MOVE)
            want = K.camera_on(block, "each", LANDSCAPE)
            for n in ("cx", "cy", "angle", "z"):
                self.assertAlmostEqual(there[n], want[n])
                self.assertAlmostEqual(K.camera_at(keys, there["t"] + 0.01 if k == len(blocks) - 1 else there["t"])[n],
                                       want[n], places=6)
        mid = K.camera_at(keys, keys[1]["t"] + K.MOVE / 2)
        self.assertAlmostEqual(mid["angle"], (keys[1]["angle"] + keys[2]["angle"]) / 2, places=6)
        self.assertAlmostEqual(mid["z"], math.sqrt(keys[1]["z"] * keys[2]["z"]), places=6)

    def test_turns_go_the_short_way_round(self):
        keys = [{"t": 0, "cx": 0, "cy": 0, "angle": 270.0, "z": 1}, {"t": 1, "cx": 0, "cy": 0, "angle": 0.0, "z": 1}]
        self.assertAlmostEqual(K.camera_at(keys, 0.5)["angle"], 315.0)

    def test_the_clock_waits_for_the_camera_and_follows_a_retimed_block(self):
        doc, layout = compose()
        clock = T.timing(doc, layout, 8.0)
        self.assertGreaterEqual(clock["intro"], clock["camera"][-1]["t"])
        blocks = layout["kinetic"]["blocks"]
        first = {w["id"]: w["start"] for w in clock["words"]}
        for k, block in enumerate(blocks):
            self.assertAlmostEqual(first[block["first"]], k * K.STEP + (T.KEY_BEAT if any(
                b["role"] == "key" for b in layout["boxes"] if b["id"] == block["first"]) else 0))
        # The camera moves on as the block's earliest word comes in.
        doc["times"] = {w: 4.0 for w in blocks[2]["words"]}
        moved = T.timing(doc, layout, 8.0)["camera"]
        self.assertAlmostEqual(moved[3]["t"], 4.0)

    def test_screen_boxes_turn_and_zoom_about_the_camera(self):
        box = {"x": 100.0, "y": 50.0, "w": 40.0, "h": 20.0, "size": 30.0, "baseline": 66.0, "rot": 90.0, "track": 2.0}
        cam = {"cx": 120.0, "cy": 60.0, "angle": 90.0, "z": 2.0}
        s = K.screen_box(box, cam, (1000, 500))
        self.assertAlmostEqual(s["x"] + s["w"] / 2, 500.0)
        self.assertAlmostEqual(s["y"] + s["h"] / 2, 250.0)
        self.assertAlmostEqual(s["size"], 60.0)
        self.assertAlmostEqual(s["rot"], 0.0)
        self.assertAlmostEqual(s["baseline"] - (s["y"] + s["h"] / 2), 12.0)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class CompTests(unittest.TestCase):
    """canvas_math sizes Text+ from Qt's own reading of the fonts: a Qt app first."""

    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
    def test_every_word_keyed_on_its_text_plus(self):
        doc, layout = compose()
        clock = T.timing(doc, layout, 8.0)
        text = TP.comp_text(layout, "t1", 8 * 24, styles=L.box_styles(layout, doc), clock=clock, fps=24)
        for i in range(1, len(layout["boxes"]) + 1):
            for name in (f"Word{i}Size", f"Word{i}Angle", f"Word{i}PathX", f"Word{i}PathY"):
                self.assertIn(f"{name} = BezierSpline", text)
            block = re.search(rf"\tWord{i} = TextPlus \{{(.*?)\n\t\t\}},", text, re.S).group(1)
            self.assertIn(f'Size = Input {{ SourceOp = "Word{i}Size"', block)
            self.assertIn(f'Center = Input {{ SourceOp = "Word{i}Path"', block)
            self.assertIn(f'AngleZ = Input {{ SourceOp = "Word{i}Angle"', block)
        self.assertNotIn("Move1 = Transform", text)

    def test_the_pose_at_rest_is_where_a_still_word_goes(self):
        _doc, layout = compose()
        for b in K.at_rest(layout)["boxes"]:
            if abs(b["rot"]) > 1e-9:
                continue
            size, x, y, angle = TP._pose(b, T.REST, LANDSCAPE)
            self.assertAlmostEqual(size, TP.text_plus_size(b, LANDSCAPE[0]))
            cx, cy = TP.text_plus_center(b, LANDSCAPE)
            self.assertAlmostEqual(x, cx)
            self.assertAlmostEqual(y, cy)
            self.assertEqual(angle, -0.0)

    def test_a_still_is_where_the_camera_settles(self):
        doc, layout = compose()
        text = TP.comp_text(layout, "t1", 48, styles=L.box_styles(layout, doc))
        rest = K.at_rest(layout)["boxes"][-1]
        cx, cy = TP.text_plus_center(rest, LANDSCAPE)
        self.assertIn(f"Center = Input {{ Value = {{ {cx:.6g}, {cy:.6g} }}, }},", text)


if __name__ == "__main__":
    unittest.main()
