"""SVG Importer's Lottie side (pages/svg_importer/engine.py): what a Lottie
file's animation becomes in the Fusion tools Buddy writes - a path that
morphs, a position that eases along a straight or a curved run, and how a
stroke's corners and ends are drawn. The files are made up here; no test
needs Resolve."""

import json
import math
import os
import re
import tempfile
import unittest

import _paths  # noqa: F401  (puts app/ on sys.path)

from pages.svg_importer import engine

EASE_OUT = {"x": 0.333, "y": 0}
EASE_IN = {"x": 0.667, "y": 1}


def outline(points, closed=True):
    """A Lottie path value with straight sides."""
    return {"v": [list(p) for p in points], "i": [[0, 0] for _ in points], "o": [[0, 0] for _ in points],
            "c": closed}


def static(value):
    return {"a": 0, "k": value}


def stroke(join=1, cap=1):
    return {"ty": "st", "c": static([0, 0, 0, 1]), "o": static(100), "w": static(2), "lj": join, "lc": cap}


def fill():
    return {"ty": "fl", "c": static([0.2, 0.4, 1, 1]), "o": static(100)}


def group_tr():
    return {"ty": "tr", "p": static([0, 0]), "a": static([0, 0]), "s": static([100, 100]), "r": static(0),
            "o": static(100)}


def layer(path, paint, position=None, ind=1):
    return {"ty": 4, "ind": ind, "nm": "Layer", "ip": 0, "op": 60, "st": 0,
            "ks": {"p": position or static([50, 50, 0]), "a": static([50, 50, 0]), "s": static([100, 100, 100]),
                   "r": static(0), "o": static(100)},
            "shapes": [{"ty": "gr", "nm": "Group", "it": [{"ty": "sh", "nm": "Path", "ks": path}] + paint
                        + [group_tr()]}]}


def build(layers, fr=30, comp_fps=None):
    """(the tools as one text, the import's log lines) for a made-up file."""
    data = {"v": "5.12.2", "fr": fr, "ip": 0, "op": 60, "w": 100, "h": 100, "assets": [], "layers": layers}
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        log = []
        tools, _label, _count = engine.build_lottie_tools(path, 100, 100, log=log.append, comp_fps=comp_fps)
        return "\n".join(tools), [str(line) for line in log]
    finally:
        os.remove(path)


def spline_keys(text, name_part):
    """[(frame, the key's own text)] of the first BezierSpline whose name holds name_part."""
    found = re.search(r"(\w*%s\w*) = BezierSpline \{.*?KeyFrames = \{\n(.*?)\n        \},\n    \}" % name_part,
                      text, re.S)
    if found is None:
        raise AssertionError(f"no spline named like {name_part!r}")
    keys = []
    for line in found.group(2).splitlines():
        frame = re.match(r"\s*\[([-0-9.e]+)\] = \{ (.*) \},$", line)
        keys.append((float(frame.group(1)), frame.group(2)))
    return keys


def key_value(key_text):
    return float(key_text.split(",")[0])


# A mask is named for its path, then the run's own ending; the spline keying it adds "Polyline".
MORPH = r"Mask\w*Polyline"
SQUARE = [(40, 40), (60, 40), (60, 60), (40, 60)]
WIDE = [(30, 40), (70, 40), (70, 60), (30, 60)]


def morphing(first=SQUARE, second=WIDE):
    return {"a": 1, "k": [{"t": 0, "s": [outline(first)], "o": EASE_OUT, "i": EASE_IN},
                          {"t": 30, "s": [outline(second)]}]}


class MorphingPathTests(unittest.TestCase):
    def test_a_morphing_path_keys_the_masks_outline(self):
        text, log = build([layer(morphing(), [fill()])])
        self.assertIn('Polyline = Input { SourceOp = "', text)
        keys = spline_keys(text, MORPH)
        self.assertEqual([frame for frame, _ in keys], [0.0, 30.0])
        self.assertEqual([key_value(k) for _, k in keys], [0.0, 1.0])
        # Each key holds its own outline: the square, then the wide box (x 30..70 of 100 is -0.2..0.2).
        self.assertIn("Value = Polyline", keys[0][1])
        self.assertIn("X = -0.1,", keys[0][1])
        self.assertIn("X = -0.2,", keys[1][1])
        self.assertFalse([line for line in log if "morph" in line], log)

    def test_the_morph_carries_the_keyframes_easing(self):
        text, _log = build([layer(morphing(), [fill()])])
        first, last = spline_keys(text, MORPH)
        # o (0.333, 0) and i (0.667, 1) over 30 frames, on the 0 -> 1 curve.
        self.assertIn("RH = { 9.99, 0.0 }", first[1])
        self.assertIn("LH = { 20.01, 1.0 }", last[1])
        self.assertNotIn("Linear = true", first[1].split("Value =")[0])

    def test_fill_and_stroke_masks_each_get_the_morph(self):
        text, _log = build([layer(morphing(), [stroke(), fill()])])
        self.assertEqual(len(re.findall(r"\w+Polyline = BezierSpline", text)), 2)

    def test_keys_land_on_the_comps_frames(self):
        text, _log = build([layer(morphing(), [fill()])], fr=30, comp_fps=24)
        self.assertEqual([frame for frame, _ in spline_keys(text, MORPH)], [0.0, 24.0])

    def test_a_still_layers_place_moves_every_outline(self):
        moved = layer(morphing(), [fill()], position=static([60, 50, 0]))
        text, _log = build([moved])
        first, last = spline_keys(text, MORPH)
        # Ten pixels right of where the unmoved layer's outlines sit.
        self.assertIn("X = 0.0,", first[1])
        self.assertIn("X = -0.1,", last[1])

    def test_outlines_with_different_point_counts_stay_still_and_say_so(self):
        text, log = build([layer(morphing(second=WIDE + [(50, 70)]), [fill()])])
        self.assertNotIn("Polyline = BezierSpline", text)
        self.assertTrue([line for line in log if "path morphing" in line], log)

    def test_an_older_files_end_outline_is_read(self):
        path = {"a": 1, "k": [{"t": 0, "s": [outline(SQUARE)], "e": [outline(WIDE)]}, {"t": 30}]}
        text, _log = build([layer(path, [fill()])])
        self.assertIn("X = -0.2,", spline_keys(text, MORPH)[1][1])


class StrokeStyleTests(unittest.TestCase):
    def mask_styles(self, text):
        return (re.findall(r"JoinStyle = Input \{ Value = (\d+) \}", text),
                re.findall(r"CapStyle = Input \{ Value = (\d+) \}", text))

    def test_round_corners_and_ends(self):
        text, _log = build([layer(static(outline(SQUARE, closed=False)), [stroke(join=2, cap=2)])])
        self.assertEqual(self.mask_styles(text), (["1"], ["1"]))

    def test_bevel_and_square(self):
        text, _log = build([layer(static(outline(SQUARE, closed=False)), [stroke(join=3, cap=3)])])
        self.assertEqual(self.mask_styles(text), (["0"], ["2"]))

    def test_miter_and_butt_are_as_before(self):
        text, _log = build([layer(static(outline(SQUARE, closed=False)), [stroke(join=1, cap=1)])])
        self.assertEqual(self.mask_styles(text), (["2"], ["0"]))

    def test_a_fills_mask_is_left_alone(self):
        text, _log = build([layer(static(outline(SQUARE)), [fill()])])
        self.assertEqual(self.mask_styles(text), (["2"], ["0"]))


def group(name, path_points, paint, position=(0, 0)):
    tr = group_tr()
    tr["p"] = static(list(position))
    return {"ty": "gr", "nm": name, "it": [{"ty": "sh", "nm": name + "Path", "ks": static(outline(path_points))}]
            + paint + [tr]}


def shape_layer(shapes, ind=1, **extra):
    made = layer(static(outline(SQUARE)), [fill()], ind=ind)
    made["shapes"] = shapes
    made.update(extra)
    return made


class PaintTests(unittest.TestCase):
    def test_the_first_item_in_a_list_is_painted_last(self):
        text, _log = build([shape_layer([group("Top", SQUARE, [fill()]), group("Under", WIDE, [fill()])])])
        # Whatever is merged in later lands on top: the first group's fill has to come after the second's.
        self.assertLess(text.index("UnderPath"), text.index("TopPath"))

    def test_a_fill_after_a_group_paints_the_paths_inside_it(self):
        white = fill()
        white["c"] = static([1, 1, 1, 1])
        text, _log = build([shape_layer([group("Box", SQUARE, [stroke()]), white])])
        under = re.search(r"BoxPath_Under\w* = Background \{.*?\n    \}", text, re.S)
        self.assertIsNotNone(under, "the group's path got no fill from the layer's own list")
        self.assertIn("TopLeftRed = Input { Value = 1 }", under.group(0))
        # ...and it sits under the group's own stroke.
        self.assertLess(text.index("BoxPath_Under"), text.index("BoxPath_Stroke"))

    def test_a_layers_fading_keys_the_masks_level(self):
        fading = layer(static(outline(SQUARE)), [fill()])
        fading["ks"]["o"] = {"a": 1, "k": [{"t": 0, "s": [100]}, {"t": 30, "s": [0]}]}
        text, _log = build([fading])
        self.assertRegex(text, r'Level = Input \{ SourceOp = "\w*Alpha\w*", Source = "Value" \}')
        self.assertEqual([key_value(k) for _, k in spline_keys(text, "Alpha")], [1.0, 0.0])
        # The Background stays opaque: its alpha is no place for opacity (see _shape_paint_level).
        self.assertNotIn("TopLeftAlpha = Input { SourceOp", text)

    def test_a_groups_own_fill_is_not_doubled_by_one_above_it(self):
        text, _log = build([shape_layer([group("Box", SQUARE, [fill()]), fill()])])
        self.assertNotIn("_Under", text)

    def test_a_matte_is_placed_by_its_shape_groups_transform(self):
        # The matte's box is drawn round its group's origin and put in place by the group.
        box = [(-10, -10), (10, -10), (10, 10), (-10, 10)]
        matte = shape_layer([group("Matte", box, [fill()], position=(20, 70))], ind=1, td=1)
        clipped = shape_layer([group("Art", SQUARE, [fill()])], ind=2, tt=1)
        text, _log = build([matte, clipped])
        clip = re.search(r"MatteMask\w* = PolylineMask \{.*?Points = \{(.*?)\} \} \}", text, re.S)
        self.assertIsNotNone(clip, "no clip mask was written")
        xs = [float(x) for x in re.findall(r"(?<![LR])X = ([-0-9.e]+)", clip.group(1))]
        ys = [float(y) for y in re.findall(r"(?<![LR])Y = ([-0-9.e]+)", clip.group(1))]
        # x 10..30 and y 60..80 of a 100 pixel comp, from the frame's middle, y up.
        self.assertEqual((round(min(xs), 6), round(max(xs), 6)), (-0.4, -0.2))
        self.assertEqual((round(min(ys), 6), round(max(ys), 6)), (-0.3, -0.1))


def moving(keys):
    return {"a": 1, "k": keys}


class PositionTests(unittest.TestCase):
    def test_a_straight_move_is_one_eased_segment_on_each_axis(self):
        position = moving([{"t": 0, "s": [50, 50, 0], "o": EASE_OUT, "i": EASE_IN, "to": [0, 0, 0], "ti": [0, 0, 0]},
                           {"t": 30, "s": [70, 30, 0]}])
        text, log = build([layer(static(outline(SQUARE)), [fill()], position=position)])
        self.assertIn("= XYPath {", text)
        self.assertRegex(text, r'Center = Input \{ SourceOp = "\w+Path\w*", Source = "Value" \}')
        xs, ys = spline_keys(text, "LayerX"), spline_keys(text, "LayerY")
        self.assertEqual([f for f, _ in xs], [0.0, 30.0])
        self.assertEqual([round(key_value(k), 6) for _, k in xs], [0.5, 0.7])
        self.assertEqual([round(key_value(k), 6) for _, k in ys], [0.5, 0.7])       # y runs up in Fusion
        self.assertIn("RH = { 9.99, 0.5 }", xs[0][1])
        self.assertFalse([line for line in log if "linear timing" in line], log)

    def test_tangents_along_the_line_still_count_as_straight(self):
        position = moving([{"t": 0, "s": [50, 50, 0], "o": EASE_OUT, "i": EASE_IN, "to": [0, -4, 0], "ti": [0, 4, 0]},
                           {"t": 30, "s": [50, 20, 0]}])
        text, _log = build([layer(static(outline(SQUARE)), [fill()], position=position)])
        self.assertEqual(len(spline_keys(text, "LayerY")), 2)

    def test_a_curved_move_is_keyed_on_every_frame_along_the_curve(self):
        # A quarter turn: out along x, arriving along y. "ti" on the first
        # keyframe is the handle at the SECOND point.
        k = 0.5523 * 20
        position = moving([{"t": 0, "s": [50, 50, 0], "to": [k, 0, 0], "ti": [0, -k, 0]},
                           {"t": 30, "s": [70, 70, 0]}])
        text, _log = build([layer(static(outline(SQUARE)), [fill()], position=position)])
        xs, ys = spline_keys(text, "LayerX"), spline_keys(text, "LayerY")
        self.assertEqual([f for f, _ in xs], [float(f) for f in range(31)])
        # Halfway in time is halfway round the arc: 45 degrees about (50, 70), radius 20.
        x, y = key_value(xs[15][1]), key_value(ys[15][1])
        want_x = (50 + 20 * math.sin(math.radians(45)) - 50) / 100 + 0.5
        want_y = -(70 - 20 * math.cos(math.radians(45)) - 50) / 100 + 0.5
        self.assertAlmostEqual(x, want_x, places=3)
        self.assertAlmostEqual(y, want_y, places=3)

    def test_a_moving_layers_far_off_points_are_drawn_inside_the_frame(self):
        # A line near the layer's far corner. Left in the layer's own
        # coordinates it would be drawn off the picture and cut away before
        # the Transform brought it back; drawn round its own middle it isn't.
        far = [(90, 95), (98, 95)]
        position = moving([{"t": 0, "s": [50, 50, 0], "to": [0, 0, 0], "ti": [0, 0, 0]}, {"t": 30, "s": [40, 50, 0]}])
        text, _log = build([layer(static(outline(far, closed=False)), [stroke()], position=position)])
        mask = re.search(r"StrokeMask\w* = PolylineMask \{.*?Points = \{(.*?)\} \} \}", text, re.S).group(1)
        points = [(float(x), float(y)) for x, y in re.findall(r"(?<![LR])X = ([-0-9.e]+), Y = ([-0-9.e]+)", mask)]
        self.assertEqual([(round(x, 6), round(y, 6)) for x, y in points], [(-0.04, 0.0), (0.04, 0.0)])
        # ...and the Transform puts it back: at rest the line's middle (94, 95) is 0.44 right and 0.45 down.
        xs, ys = spline_keys(text, "LayerX"), spline_keys(text, "LayerY")
        self.assertEqual((round(key_value(xs[0][1]), 6), round(key_value(ys[0][1]), 6)), (0.94, 0.05))

    def test_easing_reads_y_for_x(self):
        self.assertAlmostEqual(engine._lottie_ease_progress(0.5, None, None), 0.5)
        self.assertAlmostEqual(engine._lottie_ease_progress(0.5, EASE_OUT, EASE_IN), 0.5, places=6)
        self.assertLess(engine._lottie_ease_progress(0.25, EASE_OUT, EASE_IN), 0.2)
        self.assertGreater(engine._lottie_ease_progress(0.75, EASE_OUT, EASE_IN), 0.8)


if __name__ == "__main__":
    unittest.main()
