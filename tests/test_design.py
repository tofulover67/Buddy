"""Design (pages/design/): what a design is (model.py), its shapes as outlines
(geometry.py), the comp it becomes in Fusion (comp.py - what's asserted there was laid
over real Resolve renders, docs/RESOLVE_API_GUIDE.md), designs kept by name (store.py),
the job that puts one on a (fake) timeline (place.add), that the tool is wired in and
translated, and the page itself."""

import ast
import math
import os
import re
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
from pages.design import comp, geometry, model as M, place, store

FRAME = (1920, 1080)
ROOT = _paths.APP / "pages" / "design"
JOBS = {"data_graphics.read": ("pages.data_graphics.place", "read"),
        "design.add": ("pages.design.place", "add")}
SHADOW = {"on": True, "dx": 16, "dy": 24, "blur": 24, "colour": "#ff0000", "opacity": 50}

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAVE_WEB = True
except ImportError:
    HAVE_WEB = False


def design(*layers, **more):
    return M.clean({"size": list(FRAME), "layers": list(layers), **more})


def rect(**more):
    return {"kind": "rect", "name": "Card", "x": 480, "y": 270, "w": 384, "h": 216, **more}


class ModelTests(unittest.TestCase):
    def test_anything_cleans_to_a_whole_design(self):
        self.assertEqual(M.clean(None), M.clean({}))
        self.assertEqual(M.clean({}), {"name": "", "size": [1920, 1080], "seconds": 5.0, "layers": []})
        made = M.clean({"name": "  My   badge ", "size": [8, "wide"], "seconds": 9999, "layers": "none"})
        self.assertEqual((made["name"], made["size"], made["seconds"]), ("My badge", [16, 1080], 600.0))
        whole = design(rect(), {"kind": "ellipse"}, {"kind": "text"}, {"kind": "nonsense"}, "junk")
        self.assertEqual(M.clean(whole), whole)                         # cleaning again changes nothing
        self.assertEqual([layer["kind"] for layer in whole["layers"]], ["rect", "ellipse", "text"])

    def test_a_layer_has_everything_its_kind_needs(self):
        card, line, star, text = design(rect(opacity=250, rot=-90, radius="12"), {"kind": "line"},
                                        {"kind": "star", "sides": 99, "inner": 1}, {"kind": "text", "text": " a\n b "})["layers"]
        self.assertEqual((card["opacity"], card["rot"], card["radius"]), (100.0, 270.0, 12.0))
        self.assertEqual((card["fill"]["type"], card["stroke"]["width"], card["shadow"]["on"], card["blur"]),
                         ("solid", 0.0, False, 0.0))
        # A line is its stroke: no fill, and a width to start with.
        self.assertEqual((line["fill"]["type"], line["stroke"]["width"]), ("none", 4.0))
        self.assertEqual((star["sides"], star["inner"]), (60, 5.0))
        self.assertEqual((text["text"], text["font"], text["style"], text["align"]), ("a b", "Arial", "Bold", "center"))

    def test_a_shape_can_keep_its_width_and_height_in_step(self):
        card, linked, line, text = design(rect(), rect(linked=1), {"kind": "line", "linked": True}, {"kind": "text", "linked": True})["layers"]
        self.assertEqual((card["linked"], linked["linked"]), (False, True))
        self.assertNotIn("linked", line)                                # a line has one length
        self.assertNotIn("linked", text)

    def test_fills_strokes_and_shadows_are_cleaned(self):
        (layer,) = design(rect(fill={"type": "linear", "angle": "45", "stops": [{"at": 150, "colour": "RED"},
                                                                               {"at": 20, "colour": "#00FF00"}]},
                               stroke={"width": -3, "join": "pointy", "cap": "round"}, shadow={"on": 1, "opacity": 500}))["layers"]
        self.assertEqual(layer["fill"]["stops"], [{"at": 20.0, "colour": "#00ff00"}, {"at": 100.0, "colour": "#ffffff"}])
        self.assertEqual(layer["fill"]["angle"], 45.0)
        self.assertEqual(layer["stroke"], {"width": 0.0, "colour": "#ffffff", "join": "miter", "cap": "round"})
        self.assertEqual((layer["shadow"]["on"], layer["shadow"]["opacity"]), (True, 100.0))
        (one,) = design(rect(fill={"type": "linear", "stops": [{"at": 0, "colour": "#000000"}]}))["layers"]
        self.assertEqual(len(one["fill"]["stops"]), 2)                  # a gradient needs two colours

    def test_ids_last_and_are_never_shared(self):
        made = design({**rect(), "id": "keep-me"}, {**rect(), "id": "keep-me"}, {**rect(), "id": "no good!"}, rect())
        ids = [layer["id"] for layer in made["layers"]]
        self.assertEqual(ids[0], "keep-me")
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual([layer["id"] for layer in M.clean(made)["layers"]], ids)

    def test_groups_and_combined_shapes_hold_their_layers(self):
        made = design({"kind": "group", "opacity": 50, "children": [rect(), {"kind": "text"}, {"kind": "group", "children": []}]},
                      {"kind": "boolean", "op": "subtract", "children": [rect(), {"kind": "ellipse"}, {"kind": "line"},
                                                                         {"kind": "text"}]},
                      {"kind": "boolean", "op": "melt", "children": [{"kind": "line"}]},
                      {"kind": "group"})
        group, shape = made["layers"]
        self.assertEqual(([c["kind"] for c in group["children"]], group["opacity"]), (["rect", "text"], 50.0))
        self.assertNotIn("fill", group)                                 # a group has no look of its own
        # Only shapes that enclose something shape a boolean.
        self.assertEqual(([c["kind"] for c in shape["children"]], shape["op"], shape["fill"]["type"]),
                         (["rect", "ellipse"], "subtract", "solid"))
        self.assertEqual(M.count(made), 6)
        self.assertEqual([layer["kind"] for layer in M.every(made["layers"])],
                         ["group", "rect", "text", "boolean", "rect", "ellipse"])

    def test_a_path_needs_two_points_and_three_to_close(self):
        points = [{"x": 0, "y": 1}, {"x": 0.5, "y": 0, "ix": -0.3, "ox": 0.3}, {"x": 1, "y": 1}]
        closed, opened, none = (design({"kind": "path", "closed": True, "points": p})["layers"]
                                for p in (points, points[:2], points[:1]))
        self.assertEqual((closed[0]["closed"], len(closed[0]["points"])), (True, 3))
        self.assertEqual(closed[0]["points"][1], {"x": 0.5, "y": 0.0, "ix": -0.3, "iy": 0.0, "ox": 0.3, "oy": 0.0})
        self.assertFalse(opened[0]["closed"])
        self.assertEqual(none, [])

    def test_an_image_keeps_its_own_shape(self):
        (layer,) = design({"kind": "image", "path": "D:\\Footage\\logo.png", "px": [400, 200], "w": 300, "h": 999})["layers"]
        self.assertEqual((layer["w"], layer["h"]), (300.0, 150.0))
        for bad in ({"path": "D:\\Footage\\logo.gif", "px": [4, 4]}, {"path": "D:\\Footage\\logo.png"},
                    {"path": "D:\\Footage\\logo.png", "px": [0, 4]}):
            self.assertEqual(design({"kind": "image", **bad})["layers"], [])
        self.assertEqual(M.boxed([4000, 1000], FRAME), (960.0, 240.0))
        self.assertEqual(M.boxed([200, 100], FRAME), (200.0, 100.0))

    def test_a_design_holds_only_so_many_layers(self):
        made = design(*[rect() for _ in range(M.MAX_LAYERS + 20)])
        self.assertEqual(M.count(made), M.MAX_LAYERS)
        self.assertEqual(M.with_new(made, rect()), (None, None))
        more, lid = M.with_new(design(rect()), {"kind": "ellipse", "id": "taken"})
        self.assertEqual((len(more["layers"]), more["layers"][-1]["id"]), (2, lid))
        self.assertEqual(M.with_new(design(), {"kind": "image"}), (None, None))

    def test_what_a_design_is_set_in_and_how_long_it_is(self):
        made = design({"kind": "text", "font": "Inter", "style": "Black"}, {"kind": "text", "font": "Inter", "style": "Black"},
                      {"kind": "group", "children": [{"kind": "text"}]}, seconds=2)
        self.assertEqual(M.faces(made), [("Inter", "Black"), ("Arial", "Bold")])
        self.assertEqual(M.frames(made, 24.0), 48)
        self.assertEqual(M.design_id(made, FRAME, 24.0), M.design_id(M.clean(made), FRAME, 24))
        self.assertNotEqual(M.design_id(made, FRAME, 24.0), M.design_id(made, (3840, 2160), 24.0))


class GeometryTests(unittest.TestCase):
    def test_a_rectangle_is_four_corners_or_eight_points_round_them(self):
        (plain,) = design(rect())["layers"]
        out = geometry.outline(plain)
        self.assertTrue(out["closed"])
        self.assertEqual([p[:2] for p in out["points"]], [(288.0, 162.0), (672.0, 162.0), (672.0, 378.0), (288.0, 378.0)])
        (round_,) = design(rect(radius=500))["layers"]
        self.assertEqual(geometry.radius_of(round_), 108.0)             # never more than half its shorter side
        self.assertEqual(len(geometry.outline(round_)["points"]), 8)

    def test_a_turn_is_clockwise_about_the_middle(self):
        (layer,) = design(rect(rot=90))["layers"]
        first = geometry.outline(layer)["points"][0]
        self.assertAlmostEqual(first[0], 480 + 108)
        self.assertAlmostEqual(first[1], 270 - 192)
        self.assertEqual([tuple(round(v) for v in p) for p in geometry.corners(layer)],
                         [(588, 78), (588, 462), (372, 462), (372, 78)])
        self.assertEqual(tuple(round(v) for v in geometry.bounds([layer])), (372, 78, 588, 462))

    def test_polygons_stars_lines_and_paths(self):
        hexagon, star, line, path = design(
            {"kind": "polygon", "x": 100, "y": 100, "w": 200, "h": 200, "sides": 6},
            {"kind": "star", "x": 100, "y": 100, "w": 200, "h": 200, "sides": 5, "inner": 50},
            {"kind": "line", "x": 100, "y": 100, "w": 200, "h": 9},
            {"kind": "path", "x": 100, "y": 100, "w": 200, "h": 100,
             "points": [{"x": 0, "y": 1}, {"x": 0.5, "y": 0, "ix": -0.25, "ox": 0.25}, {"x": 1, "y": 1}]})["layers"]
        points = geometry.outline(hexagon)["points"]
        self.assertEqual(len(points), 6)
        self.assertAlmostEqual(points[0][0], 100)
        self.assertAlmostEqual(points[0][1], 0)                         # the first corner is at the top
        points = geometry.outline(star)["points"]
        self.assertEqual(len(points), 10)
        self.assertAlmostEqual(math.hypot(points[1][0] - 100, points[1][1] - 100), 50)
        self.assertEqual(geometry.outline(line), {"closed": False, "points": [(0.0, 100.0, 0.0, 0.0, 0.0, 0.0),
                                                                              (200.0, 100.0, 0.0, 0.0, 0.0, 0.0)]})
        out = geometry.outline(path)
        self.assertFalse(out["closed"])
        self.assertEqual(out["points"][1], (100.0, 50.0, -50.0, 0.0, 50.0, 0.0))

    def test_a_gradient_runs_from_side_to_side_and_turns_with_its_layer(self):
        box = (480, 270, 384, 216)
        self.assertEqual(geometry.gradient_line(box, 0, 0), ((288.0, 270.0), (672.0, 270.0)))
        (x0, y0), (x1, y1) = geometry.gradient_line(box, 0, 90)
        self.assertEqual((round(x0), round(y0), round(x1), round(y1)), (480, 162, 480, 378))
        (x0, y0), (x1, y1) = geometry.gradient_line(box, 90, 0)
        self.assertEqual((round(x0), round(y0), round(x1), round(y1)), (480, 78, 480, 462))

    def test_a_design_is_fitted_into_another_frame(self):
        self.assertEqual(geometry.fit(FRAME, FRAME), (1.0, 0.0, 0.0))
        self.assertEqual(geometry.fit(FRAME, (3840, 2160)), (2.0, 0.0, 0.0))
        self.assertEqual(geometry.fit((1080, 1080), FRAME), (1.0, 420.0, 0.0))
        self.assertEqual(geometry.fit(FRAME, (1080, 1920)), (0.5625, 0.0, 656.25))


class CompTests(unittest.TestCase):
    def comp(self, *layers, frame=FRAME, metrics=None, **more):
        self.text, self.gid = comp.comp_text(design(*layers, **more), frame, 24, 120, metrics)
        return self.text

    def tool(self, name):
        start = self.text.index(f"\t\t{name} = ")
        return self.text[start:self.text.index("\n\t\t}", start)]

    def test_its_a_whole_comp_tagged_as_buddys(self):
        text = self.comp(rect())
        self.assertTrue(text.startswith("Composition {"))
        self.assertIn(f'CustomData = {{ TEMPLATE_ID = "Text+", BuddyDesign = "{self.gid}" }}', text)
        self.assertIn("RenderRange = { 0, 119 }", text)
        self.assertIn("TopLeftAlpha = Input { Value = 0, },", self.tool("Canvas"))
        self.assertIn('Input = Input { SourceOp = "Card1On", Source = "Output", },', self.tool("MediaOut1"))
        self.assertNotIn("GroupOperator", text)                         # a group's output reads empty in an imported comp
        self.assertNotIn("sMerge", text)

    def test_a_rectangle_stays_a_rectangle(self):
        self.comp(rect(radius=54, rot=20, fill={"type": "solid", "colour": "#3c82e0"}))
        mask = self.tool("Card1Mask")
        self.assertIn("Card1Mask = RectangleMask {", mask)
        for line in ("Center = Input { Value = { 0.25, 0.75 }, },", "Width = Input { Value = 0.2, },",
                     "Height = Input { Value = 0.2, },", "CornerRadius = Input { Value = 0.5, },",
                     "Angle = Input { Value = -20, },", "MaskWidth = Input { Value = 1920, },"):
            self.assertIn(line, mask)
        fill = self.tool("Card1Fill")
        self.assertIn('EffectMask = Input { SourceOp = "Card1Mask", Source = "Mask", },', fill)
        self.assertIn("TopLeftBlue = Input { Value = 0.878431, },", fill)
        self.assertIn('Foreground = Input { SourceOp = "Card1Fill", Source = "Output", },', self.tool("Card1On"))

    def test_an_ellipses_sides_are_both_shares_of_the_width(self):
        self.comp({"kind": "ellipse", "x": 960, "y": 540, "w": 384, "h": 192})
        mask = self.tool("Ellipse1Mask")
        self.assertIn("Ellipse1Mask = EllipseMask {", mask)
        self.assertIn("Width = Input { Value = 0.2, },", mask)
        self.assertIn("Height = Input { Value = 0.1, },", mask)
        self.assertNotIn("Angle", mask)

    def test_other_shapes_are_outlines_with_every_handle_written(self):
        self.comp({"kind": "path", "name": "Hill", "x": 960, "y": 540, "w": 384, "h": 216, "closed": True,
                   "points": [{"x": 0, "y": 1}, {"x": 0.5, "y": 0, "ix": -0.25, "ox": 0.25}, {"x": 1, "y": 1}]})
        mask = self.tool("Hill1Mask")
        self.assertIn("Hill1Mask = PolylineMask {", mask)
        self.assertIn('DrawMode = "ModifyOnly",', mask)
        self.assertIn("Polyline { Closed = true, Points = { { X = -0.1, Y = -0.1, LX = 0, LY = 0, RX = 0, RY = 0, Linear = true }, "
                      "{ X = 0, Y = 0.1, LX = -0.05, LY = 0, RX = 0.05, RY = 0 }, "
                      "{ X = 0.1, Y = -0.1, LX = 0, LY = 0, RX = 0, RY = 0, Linear = true } } }", mask)

    def test_a_stroke_is_the_outline_drawn_as_a_line(self):
        self.comp(rect(stroke={"width": 19.2, "colour": "#ffd21e"}),
                  {"kind": "polygon", "name": "Tri", "sides": 3, "stroke": {"width": 9.6, "join": "round", "cap": "square"},
                   "fill": {"type": "none"}})
        mask = self.tool("Card1StrokeMask")
        self.assertIn("Solid = Input { Value = 0, },", mask)
        self.assertIn("BorderWidth = Input { Value = 0.01, },", mask)
        self.assertIn('EffectMask = Input { SourceOp = "Card1StrokeMask", Source = "Mask", },', self.tool("Card1Stroke"))
        # Fill then stroke, each merged on in turn.
        self.assertIn('Foreground = Input { SourceOp = "Card1Fill", Source = "Output", },', self.tool("Card1On1"))
        self.assertIn('Background = Input { SourceOp = "Card1On1", Source = "Output", },', self.tool("Card1On"))
        mask = self.tool("Tri2StrokeMask")
        for line in ("JoinStyle = Input { Value = 1, },", "CapStyle = Input { Value = 2, },", "MiterLimit = Input { Value = 10, },",
                     "BorderWidth = Input { Value = 0.005, },"):
            self.assertIn(line, mask)
        self.assertNotIn("Tri2Fill", self.text)                         # no fill: no tools for one

    def test_a_gradient_fill(self):
        self.comp(rect(fill={"type": "linear", "angle": 90, "stops": [{"at": 0, "colour": "#ff0000"},
                                                                      {"at": 100, "colour": "#0000ff"}]}))
        fill = self.tool("Card1Fill")
        for line in ('Type = Input { Value = FuID { "Gradient" }, },', 'GradientType = Input { Value = FuID { "Linear" }, },',
                     "Start = Input { Value = { 0.25, 0.85 }, },", "End = Input { Value = { 0.25, 0.65 }, },",
                     "Gradient = Input { Value = Gradient { Colors = { [0] = { 1, 0, 0, 1 }, [1] = { 0, 0, 1, 1 } } }, },"):
            self.assertIn(line, fill)

    def test_opacity_is_the_blend_of_the_merge_that_puts_it_on(self):
        self.comp(rect(opacity=40))
        self.assertIn("Blend = Input { Value = 0.4, },", self.tool("Card1On"))
        self.assertNotIn("Card1Canvas", self.text)
        # With more than one piece it is put together first, so it fades as one.
        self.comp(rect(opacity=40, stroke={"width": 8}))
        self.assertIn("Card1Canvas = Background {", self.text)
        self.assertIn('Foreground = Input { SourceOp = "Card1Stroke", Source = "Output", },', self.tool("Card1Part2"))
        on = self.tool("Card1On")
        self.assertIn('Foreground = Input { SourceOp = "Card1Part2", Source = "Output", },', on)
        self.assertIn("Blend = Input { Value = 0.4, },", on)

    def test_a_shadow_is_the_outline_moved_and_softened(self):
        self.comp(rect(shadow=SHADOW, stroke={"width": 19.2}))
        mask = self.tool("Card1ShadowMask")
        # Grown by the half of the stroke outside the shape, moved, soft: 2.15 x sigma of the width.
        for line in ("Center = Input { Value = { 0.258333, 0.727778 }, },", "Width = Input { Value = 0.21, },",
                     f"SoftEdge = Input {{ Value = {12 * 2.15 / 1920:.6g}, }},"):
            self.assertIn(line, mask)
        shade = self.tool("Card1Shadow")
        self.assertIn("TopLeftRed = Input { Value = 0.5", shade)        # its colour, as strong as its opacity
        self.assertIn("TopLeftAlpha = Input { Value = 0.5", shade)
        self.assertIn('Foreground = Input { SourceOp = "Card1Shadow", Source = "Output", },', self.tool("Card1On1"))
        # A line's shadow is the line's own.
        self.comp({"kind": "line", "x": 960, "y": 540, "w": 960, "stroke": {"width": 19.2}, "shadow": SHADOW})
        mask = self.tool("Line1ShadowMask")
        self.assertIn("BorderWidth = Input { Value = 0.01, },", mask)
        self.assertIn("Center = Input { Value = { 0.508333, 0.477778 }, },", mask)

    def test_a_blurred_layer_is_blurred_by_a_share_of_the_width(self):
        self.comp(rect(blur=40))
        blur = self.tool("Card1Blur")
        self.assertIn(f"XBlurSize = Input {{ Value = {20 / 1920 * 1670:.6g}, }},", blur)
        self.assertIn('Foreground = Input { SourceOp = "Card1Blur", Source = "Output", },', self.tool("Card1On"))
        self.comp(rect(blur=40), frame=(3840, 2160))                    # twice the px, the same number
        self.assertIn(f"XBlurSize = Input {{ Value = {20 / 1920 * 1670:.6g}, }},", self.tool("Card1Blur"))

    def test_a_combined_shape_is_its_masks_in_a_chain(self):
        children = [{"kind": "ellipse", "x": 300, "y": 300, "w": 300, "h": 300}, rect(x=400, y=300), {"kind": "star", "x": 300, "y": 300}]
        for op, mode in (("union", "Merge"), ("subtract", "Subtract"), ("intersect", "Multiply"), ("exclude", "Invert")):
            self.comp({"kind": "boolean", "name": "Badge", "op": op, "children": children})
            self.assertNotIn("PaintMode", self.tool("Badge1Mask1"))
            for n, before in ((2, 1), (3, 2)):
                mask = self.tool(f"Badge1Mask{n}")
                self.assertIn(f'PaintMode = Input {{ Value = FuID {{ "{mode}" }}, }},', mask)
                self.assertIn(f'EffectMask = Input {{ SourceOp = "Badge1Mask{before}", Source = "Mask", }},', mask)
            self.assertIn('EffectMask = Input { SourceOp = "Badge1Mask3", Source = "Mask", },', self.tool("Badge1Fill"))
        self.assertIn("Badge1Mask1 = EllipseMask {", self.text)
        self.assertIn("Badge1Mask3 = PolylineMask {", self.text)
        # Its shadow is the same chain moved, then blurred as one.
        self.comp({"kind": "boolean", "name": "Badge", "op": "subtract", "children": children, "shadow": SHADOW})
        self.assertIn('EffectMask = Input { SourceOp = "Badge1ShadowMask3", Source = "Mask", },', self.tool("Badge1ShadowFill"))
        self.assertIn('Input = Input { SourceOp = "Badge1ShadowFill", Source = "Output", },', self.tool("Badge1Shadow"))

    def test_text_is_a_text_plus(self):
        self.comp({"kind": "text", "name": "Title", "x": 480, "y": 270, "text": 'Say "hi"', "font": "Inter", "style": "Black",
                   "size": 192, "align": "left", "rot": 30, "colour": "#ff0000", "shadow": SHADOW},
                  metrics={"Inter|Black": {"em": 0.8, "baseline": 0.4}})
        text = self.tool("Title1Text")
        for line in ("Title1Text = TextPlus {", 'StyledText = Input { Value = "Say \\"hi\\"", },', 'Font = Input { Value = "Inter", },',
                     'Style = Input { Value = "Black", },', "Size = Input { Value = 0.125, },",
                     "Center = Input { Value = { 0.25, 0.75 }, },", "HorizontalLeftCenterRight = Input { Value = -1, },",
                     "AngleZ = Input { Value = -30, },", "Red1 = Input { Value = 1, },", "Enabled3 = Input { Value = 1, },",
                     "Offset3 = Input { Value = { 0.107735, -0.0532692 }, },", "SoftnessX3 = Input { Value = 7.5, },",
                     "Alpha3 = Input { Value = 0.5, },"):
            self.assertIn(line, text)
        # A face nobody measured is taken for Arial's.
        self.comp({"kind": "text", "size": 192})
        self.assertIn(f"Size = Input {{ Value = {192 / 0.716 / 1920:.6g}, }},", self.tool("Text1Text"))

    def test_an_image_is_a_loader_merged_at_its_place_and_size(self):
        self.comp({"kind": "image", "name": "Logo", "path": "D:\\Footage\\logo.png", "px": [400, 200], "x": 480, "y": 270,
                   "w": 600, "rot": 25, "radius": 30, "shadow": SHADOW})
        loader = self.tool("Logo1Image")
        self.assertIn("Logo1Image = Loader {", loader)
        self.assertIn('Filename = "D:\\\\Footage\\\\logo.png", FormatID = "PNGFormat"', loader)
        fill = self.tool("Logo1Fill")
        for line in ("Center = Input { Value = { 0.25, 0.75 }, },", "Size = Input { Value = 1.5, },", "Angle = Input { Value = -25, },",
                     'EffectMask = Input { SourceOp = "Logo1Mask", Source = "Mask", },',
                     'Background = Input { SourceOp = "Logo1Back", Source = "Output", },'):
            self.assertIn(line, fill)
        self.assertIn("Logo1ShadowMask = RectangleMask {", self.text)
        self.comp({"kind": "image", "name": "Logo", "path": "D:\\Footage\\logo.jpg", "px": [400, 200]})
        self.assertNotIn("Logo1Mask", self.text)                        # square corners need no mask
        self.assertIn('FormatID = "JpegFormat"', self.text)

    def test_a_see_through_group_is_put_together_first(self):
        children = [rect(name="A"), {"kind": "ellipse", "name": "B"}]
        self.comp({"kind": "group", "name": "Pair", "children": children}, rect(name="C"))
        self.assertNotIn("Pair1", self.text)                            # a solid group is just its layers, in order
        self.assertIn('Background = Input { SourceOp = "B3On", Source = "Output", },', self.tool("C4On"))
        self.comp({"kind": "group", "name": "Pair", "opacity": 50, "children": children}, rect(name="C"))
        self.assertIn('Background = Input { SourceOp = "Pair1Canvas", Source = "Output", },', self.tool("A2On"))
        on = self.tool("Pair1On")
        self.assertIn('Foreground = Input { SourceOp = "B3On", Source = "Output", },', on)
        self.assertIn("Blend = Input { Value = 0.5, },", on)
        self.assertIn('Background = Input { SourceOp = "Pair1On", Source = "Output", },', self.tool("C4On"))

    def test_hidden_layers_and_empty_looks_are_left_out(self):
        self.comp(rect(hidden=True), rect(name="Ghost", fill={"type": "none"}), rect(name="Gone", opacity=0), rect(name="Here"))
        self.assertEqual(re.findall(r"^\t\t(\w+) = ", self.text, re.M), ["Canvas", "Here4Mask", "Here4Fill", "Here4On", "MediaOut1"])

    def test_tools_are_named_after_their_layers(self):
        self.comp(rect(name="My badge!"), rect(name="2nd"), rect(name="バッジ"), rect(name="My badge!"))
        names = set(re.findall(r"^\t\t(\w+)On = ", self.text, re.M))
        self.assertEqual(names, {"Mybadge1", "Rectangle2nd2", "Rectangle3", "Mybadge4"})

    def test_another_frame_takes_the_design_fitted(self):
        self.comp(rect(stroke={"width": 19.2}), frame=(3840, 2160))
        self.assertIn("Width = Input { Value = 0.2, },", self.tool("Card1Mask"))        # shares of the frame: the same
        self.assertIn("MaskWidth = Input { Value = 3840, },", self.tool("Card1Mask"))
        self.assertIn("BorderWidth = Input { Value = 0.01, },", self.tool("Card1StrokeMask"))
        self.comp(rect(), frame=(1080, 1920))                           # upright: smaller, in the middle
        mask = self.tool("Card1Mask")
        self.assertIn("Width = Input { Value = 0.2, },", mask)
        self.assertIn(f"Height = Input {{ Value = {216 * 0.5625 / 1920:.6g}, }},", mask)
        self.assertIn(f"Center = Input {{ Value = {{ 0.25, {1 - (270 * 0.5625 + 656.25) / 1920:.6g} }}, }},", mask)

    def test_every_outline_can_be_a_polyline(self):
        with mock.patch.object(comp, "PLAIN", False):
            self.comp(rect(), {"kind": "ellipse"})
        self.assertNotIn("RectangleMask", self.text)
        self.assertEqual(self.text.count("= PolylineMask {"), 2)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_designs_are_kept_opened_and_removed(self):
        self.assertEqual(store.listed(), [])
        first = store.save(design(rect(), name="Badge"))
        second = store.save(design(name="Lower third"))
        self.assertRegex(first, r"^d-[0-9a-f]{10}$")
        self.assertEqual({d["name"]: d["layers"] for d in store.listed()}, {"Badge": 1, "Lower third": 0})
        self.assertEqual(store.load(first), design(rect(), name="Badge"))
        self.assertEqual(store.save(design(rect(), rect(), name="Badge 2"), first), first)      # over the one it came from
        self.assertEqual(store.load(first)["name"], "Badge 2")
        self.assertTrue(store.remove(second))
        self.assertFalse(store.remove(second))
        self.assertEqual([d["id"] for d in store.listed()], [first])
        self.assertTrue(os.path.isdir(os.path.join(self.home.name, ".buddy", "tools", "design", "designs")))

    def test_only_its_own_files_are_read(self):
        for bad in ("", None, "..\\..\\settings", "d-zzzzzzzzzz", "d-0123456789/../x"):
            self.assertIsNone(store.load(bad))
            self.assertFalse(store.remove(bad))
        kept = store.save(design(rect()))
        with open(os.path.join(store.folder(), f"{kept}.json"), "w", encoding="utf-8") as fh:
            fh.write("not json")
        os.remove(os.path.join(store.folder(), f"{kept}.json.bak")) if os.path.exists(
            os.path.join(store.folder(), f"{kept}.json.bak")) else None
        self.assertIsNone(store.load(kept))
        self.assertEqual(store.listed(), [])


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
        spec = design(rect(), seconds=2)
        done = place.add(controller, spec, {})
        self.assertEqual((done["track"], done["start"], done["end"], done["replaced"]), (2, 86448, 86496, False))
        self.assertEqual((done["timeline"], done["fps"], done["frame"], done["seconds"]), ("Edit", 24.0, [1920, 1080], 2.0))
        self.assertEqual(done["id"], M.design_id(spec, FRAME, 24.0))
        (item,) = timeline.tracks[2]
        self.assertEqual(item.comp.GetData(comp.TAG), done["id"])
        self.assertIn("Card1Mask = RectangleMask {", item.comp.text)
        self.assertEqual(timeline.tracks[1], [footage])                 # nothing of theirs touched
        self.assertEqual(timeline.playhead, 86448)

    def test_adding_again_replaces_buddys_own_unless_told_not_to(self):
        controller, timeline = studio()
        place.add(controller, design(rect()))
        again = place.add(controller, design(rect(radius=20)))
        self.assertTrue(again["replaced"])
        self.assertEqual(len(timeline.tracks[1]), 1)
        kept = place.add(controller, design(rect()), replace=False)
        self.assertEqual((kept["track"], kept["replaced"]), (2, False))

    def test_nothing_to_add_is_said(self):
        controller, timeline = studio()
        for empty in (design(), design(rect(hidden=True))):
            with self.assertRaises(PlaceError) as caught:
                place.add(controller, empty)
            self.assertIn("There's nothing to add yet.", str(caught.exception))
        self.assertEqual(timeline.tracks[1], [])

    def test_an_image_that_has_gone_is_said_and_nothing_is_added(self):
        controller, timeline = studio()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "logo.png")
            spec = design({"kind": "group", "children": [{"kind": "image", "path": path, "px": [400, 200]}]})
            with self.assertRaises(PlaceError) as caught:
                place.add(controller, spec)
            self.assertIn('The image "logo.png" isn\'t where it was.', str(caught.exception))
            self.assertEqual(timeline.tracks[1], [])
            with open(path, "wb") as fh:
                fh.write(b"made up")
            done = place.add(controller, spec)                          # there again: on it goes
            self.assertIn("Image2Image = Loader {", timeline.tracks[done["track"]][0].comp.text)

    def test_no_project_or_no_timeline_is_said(self):
        with self.assertRaises(ResolveConnectionError):
            place.add(Controller(None), design(rect()))
        with self.assertRaises(ResolveConnectionError) as caught:
            place.add(Controller(Project(None)), design(rect()))
        self.assertIn("Open a timeline", str(caught.exception))

    def test_a_failure_adds_nothing(self):
        controller, timeline = studio()
        timeline.takes_comps = False
        with self.assertRaises(PlaceError):
            place.add(controller, design(rect()))
        self.assertEqual(timeline.tracks[1], [])


class _Said(HTMLParser):
    """What the page says: its text, titles, labels and placeholders that aren't marked translate="no"."""

    def __init__(self):
        super().__init__()
        self.texts, self._off = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in ("meta", "link", "input", "br", "path", "rect", "ellipse"):
            self._off.append(attrs.get("translate") == "no" or tag in ("script", "title", "style")
                             or (self._off[-1] if self._off else False))
        for key in ("title", "aria-label", "placeholder"):
            if attrs.get(key) and attrs.get("translate") != "no":
                self.texts.append(attrs[key])

    def handle_endtag(self, tag):
        if tag not in ("meta", "link", "input", "br", "path", "rect", "ellipse") and self._off:
            self._off.pop()

    def handle_data(self, data):
        if data.strip() and not (self._off and self._off[-1]):
            self.texts.append(i18n.normalize(data))


def said():
    """Everything Design says: the page's own text, what its script writes, and what
    its Python sends."""
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "design.js").read_text(encoding="utf-8")
    parser = _Said()
    parser.feed(html)
    written = re.findall(r'(?:textContent =|toast\(|[?:]|title:|text:|ok:|cancel:|"aria-label":|Buddy\.t\() [`"]([A-Z{][^`"]{2,})[`"]', script)
    return ["Design", more_tools.BLURBS["design"], "Design added on V2.", "Design replaced on V1.", "Couldn't add the design",
            "Adding the design…", "Adding a design", f"A design holds up to {M.MAX_LAYERS} layers.", "Choose an image",
            "Images (*.png *.jpg *.jpeg)", "Buddy couldn't read that image. PNG and JPEG images can be added.",
            'The image "logo.png" isn\'t where it was. Put it back, or remove it from the design.',
            "There's nothing to add yet. Draw something on the canvas first.", "Design saved.", "That design couldn't be opened.",
            "Couldn't save the design", "Couldn't delete the design", "3 layers", "Open the other one", "Start a new one",
            "The timeline is 3840 x 2160: the design is fitted into it, as large as goes.",
            *M.TITLES.values(), *parser.texts,
            # (A font's name is a name, the same in every language.)
            *[text.replace("${state.max_layers}", str(M.MAX_LAYERS)) for text in written if text not in ("Arial", "Bold", "Regular")]]


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web" / "design.js").read_text(encoding="utf-8")

    def test_its_in_the_sidebar_under_editing_tools(self):
        source = (_paths.APP / "registry.py").read_text(encoding="utf-8")
        # It's a tab of Design Studio: the rail has that, and not Design by itself.
        self.assertIn('"pages.design_studio.page", "DesignStudioPage"', source)
        self.assertNotIn('"DesignPage"', source)
        self.assertIn('data-tab="design" aria-selected="true"', self.html)
        self.assertIn("design", tool_dirs.BUCKETS)

    def test_what_the_page_says_is_translated(self):
        texts = said()
        self.assertGreater(len(texts), 150)
        for text in ("Not saved", "Combined", "Unlock", "Hide", "Open", "Keep working", "Delete this saved design?",
                     "Click where the text goes.", "Untitled", "{n} layers"):
            self.assertTrue(text in texts or f'"{text}"' in self.script, text)
            texts.append(text)
        for text in texts:
            with self.subTest(text):
                self.assertNotEqual(i18n.translate(text, "日本語"), text)

    def test_the_view_and_the_model_name_the_same_things(self):
        self.assertEqual(re.findall(r'data-tool="(\w+)"', self.html),
                         ["select", "rect", "ellipse", "line", "polygon", "star", "pen", "text"])
        self.assertEqual(re.findall(r'data-op="(\w+)"', self.html), list(M.OPS))
        for group, values in (("fill.type", M.FILLS), ("stroke.join", M.JOINS), ("stroke.cap", M.CAPS), ("align", M.ALIGNS)):
            part = self.html.split(f'data-prop="{group}"')[1].split("</div>")[0]
            self.assertEqual(re.findall(r'data-value="(\w+)"', part), list(values))
        js = lambda name: re.search(rf"const {name} = (\[[^\]]+\])", self.script).group(1)    # noqa: E731
        self.assertEqual(tuple(ast.literal_eval(js("SHAPES"))), M.SHAPES)
        self.assertEqual(tuple(ast.literal_eval(js("SOLIDS"))), M.SOLIDS)
        for kind, title in M.TITLES.items():
            self.assertIn(f'{kind}: "{title}"', self.script)
        self.assertEqual(set(comp.JOIN), set(M.JOINS))
        self.assertEqual(set(comp.CAP), set(M.CAPS))
        self.assertEqual(set(comp.PAINT), set(M.OPS))
        from pages.design.page import DesignPage
        self.assertEqual(DesignPage.IMAGE_FILTER, "Images (" + " ".join(f"*{e}" for e in M.IMAGE_TYPES) + ")")
        from pages.interactions.comp import FORMATS
        self.assertEqual(set(FORMATS), set(M.IMAGE_TYPES))

    def test_the_canvas_draws_by_the_models_rules(self):
        # The same sums as geometry.py and comp.py: a star's dips, a gradient's reach, a
        # blur of half the number, a boolean's children combined in order.
        for line in ("const KAPPA = 0.5522847498;", "-Math.PI / 2 + 2 * Math.PI * i / steps", "l.inner / 100",
                     "(Math.abs(w * dx) + Math.abs(h * dy)) / 2", "l.shadow.blur / 2 * scale", "blur / 2 * scale",
                     'subtract: "destination-out"', 'intersect: "destination-in"', 'exclude: "xor"', "ctx.miterLimit = 10;"):
            self.assertIn(line, self.script)
        self.assertEqual(geometry.KAPPA, 0.5522847498)
        self.assertEqual(comp.MITER_LIMIT, 10)

    def test_the_canvas_is_shown_at_the_stages_size_on_a_scaled_screen(self):
        # A canvas with no size of its own in CSS is as large as the pixels it holds: at
        # 150% it was drawn half as large again and the mouse landed somewhere else.
        css = (ROOT / "web" / "design.css").read_text(encoding="utf-8")
        rule = re.search(r"\.ds-stage canvas \{([^}]*)\}", css).group(1)
        self.assertIn("width: 100%", rule)
        self.assertIn("height: 100%", rule)
        for line in ("canvas.width = Math.round(cw * ratio);", 'canvas.style.width = cw + "px";',
                     'canvas.style.height = ch + "px";', "const box = $(\"stage\").getBoundingClientRect();"):
            self.assertIn(line, self.script)

    def test_a_handle_shows_the_way_it_moves(self):
        # Two-way arrows, turned with the box: across (0 degrees), then each 45 on from it.
        self.assertIn('const ARROWS = ["ew-resize", "nwse-resize", "ns-resize", "nesw-resize"];', self.script)
        self.assertIn("Math.atan2(h.sy, h.sx) * 180 / Math.PI + f.rot", self.script)
        self.assertIn("h ? handleCursor(h, f) :", self.script)

    def test_sizes_are_set_precisely(self):
        # Width and Height linked (typed or dragged), sums in fields, Alt from the middle.
        self.assertIn('id="link-size" data-for="linked"', self.html)
        self.assertEqual(re.findall(r'data-spread="(\w)"', self.html), ["x", "y"])
        for line in ('if (path === "w") l.h = Math.max(1, l.w * was.h / was.w);', 'if (path === "h") l.w = Math.max(1, l.h * was.w / was.h);',
                     "!e.shiftKey && !one.linked", "value = calc(raw);", "const from = e.altKey ? [0, 0] : [-h.sx * f.w / 2, -h.sy * f.h / 2];",
                     "twin = copyOf(l, 0);", "if (NUMBERS.test(path)) stepping(node, path);"):
            self.assertIn(line, self.script)
        self.assertNotIn("eval(", self.script)                          # the page's policy allows none: sums are worked out by hand
        self.assertNotIn("new Function", self.script)

    def test_the_detailed_options_are_in_panels_of_their_own(self):
        page, gradient, stroke, effects, _designs = re.split(r'id="(?:gradient|stroke|effects|designs)-pop"', self.html)
        for prop in ("x", "y", "w", "h", "rot", "fill.type", "fill.colour", "stroke.width", "stroke.colour", "opacity", "text"):
            self.assertIn(f'data-prop="{prop}"', page)
        self.assertIn('data-prop="fill.angle"', gradient)
        for prop in ("stroke.join", "stroke.cap"):
            self.assertIn(f'data-prop="{prop}"', stroke)
        for prop in ("shadow.on", "shadow.dx", "shadow.dy", "shadow.blur", "shadow.colour", "shadow.opacity", "blur"):
            self.assertIn(f'data-prop="{prop}"', effects)
        self.assertEqual(self.html.count('class="dg-pop"'), self.html.count("dg-pop-close"))

    def test_ask_buddy_is_told_what_it_makes(self):
        tool = get_tool("design")
        self.assertTrue(tool.needs_resolve)
        told = " ".join([tool.summary, *tool.capabilities, tool.notes])
        for word in ("Rectangle (R)", "Pen (P)", "Unite, Subtract", "Gradient…", "Corners and ends…", "Shadow and blur…",
                     "Designs…", "PNG or JPEG", "a Loader", "new Fusion clip", "Badge3Mask", "a Text+", "Paint Mode",
                     "Not there yet"):
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

    def open(self, controller=None, host=None):
        from pages.design.page import DesignPage
        self.host = host or Host(controller)
        self.page = DesignPage(self.host)
        self.addCleanup(self.page.deleteLater)
        self.sent = []
        self.page.emit = lambda name, data=None: self.sent.append((name, data))
        if controller is not None:
            self.reader = self.page._calls.reader = ChildReader(controller, JOBS)

    def last(self, name):
        return next(data for n, data in reversed(self.sent) if n == name)

    def draw(self, *layers, rev=1, merge="", **more):
        self.page.on_design({"design": {"size": list(FRAME), "layers": list(layers), **more}, "rev": rev, "merge": merge})

    def test_its_a_real_page_under_editing_tools(self):
        self.assertFalse(self.page.is_placeholder)
        self.assertEqual((self.page.tool_id, self.page.category), ("design", "Editing Tools"))

    def test_a_new_design_is_empty_and_says_what_the_view_needs(self):
        self.page.web_ready()
        state = self.last("state")
        self.assertEqual(state["spec"], M.clean({}))
        self.assertEqual((state["connected"], state["rev"], state["saved"], state["designs"], state["design_id"]),
                         (False, 0, False, [], ""))
        self.assertEqual((state["max_layers"], state["max_stops"], state["can_undo"]), (M.MAX_LAYERS, M.MAX_STOPS, False))
        self.assertIn("Arial", self.last("fonts")["fonts"])

    def test_the_design_the_view_sends_is_cleaned_kept_and_sent_back(self):
        self.draw(rect(opacity=900), {"kind": "nonsense"}, rev=3)
        state = self.last("state")
        self.assertEqual(([layer["kind"] for layer in state["spec"]["layers"]], state["spec"]["layers"][0]["opacity"]),
                         (["rect"], 100.0))
        self.assertEqual((state["rev"], state["can_undo"]), (3, True))
        self.assertEqual(self.host.buckets["design"].saved["spec"], state["spec"])
        self.page.on_design({"design": "junk", "rev": 9})
        self.page.on_design(None)
        self.assertEqual(self.page.rev, 3)
        again = type(self.page)(self.host)
        self.addCleanup(again.deleteLater)
        self.assertEqual(again.spec, state["spec"])

    def test_undo_and_redo_walk_the_changes_and_a_drag_is_one_step(self):
        self.draw(rect(), rev=1)
        self.draw(rect(x=500), rev=2, merge="prop:x")
        self.draw(rect(x=510), rev=3, merge="prop:x")
        self.draw(rect(x=510, radius=9), rev=4, merge="prop:radius")
        self.assertEqual(len(self.page.undo_steps), 3)
        self.page.on_undo({"rev": 5})
        self.page.on_undo({"rev": 6})
        state = self.last("state")
        self.assertEqual((state["spec"]["layers"][0]["x"], state["rev"], state["can_redo"]), (480.0, 6, True))
        self.page.on_redo({"rev": 7})
        self.assertEqual(self.last("state")["spec"]["layers"][0]["x"], 510.0)
        self.page.on_set({"key": "seconds", "value": 9})                # nothing is set a field at a time
        self.page.on_reset()
        self.assertEqual(self.page.spec["seconds"], 5.0)
        self.assertTrue(self.page.spec["layers"])

    def test_text_brings_how_its_face_is_set(self):
        self.draw({"kind": "text", "font": "Inter", "style": "Black"})
        state = self.last("state")
        face = state["faces"]["Inter|Black"]
        self.assertEqual(face["css"], {"family": "Inter", "weight": 900, "italic": False})
        self.assertGreater(face["em"], 0.2)
        self.assertEqual(set(self.page.metrics()), {"Inter|Black"})
        self.page.web_ready()
        self.assertIn("Black", self.last("fonts")["fonts"]["Inter"])    # the face in use is always listed

    def picture(self, name="logo.png", size=(400, 200)):
        """A made-up picture in the test's own folder: its path."""
        from PySide6.QtGui import QImage
        path = os.path.join(self.home.name, name)
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(0xffff8a1e)
        self.assertTrue(image.save(path))
        return path

    def test_an_image_is_picked_from_a_file_and_never_stretched(self):
        from pages.design import page as page_module
        path = self.picture()
        with mock.patch.object(page_module.QFileDialog, "getOpenFileName", return_value=(path, "")):
            self.page.on_new_image({"rev": 2})
        state = self.last("state")
        (layer,) = state["spec"]["layers"]
        self.assertEqual((layer["kind"], layer["px"], layer["w"], layer["h"], layer["x"], layer["y"]),
                         ("image", [400, 200], 400.0, 200.0, 960.0, 540.0))
        self.assertEqual(self.last("picked"), {"ids": [layer["id"]]})
        self.assertTrue(state["images"][layer["path"]].startswith("file:///"))
        self.assertEqual(self.host.buckets["design"]["image_dir"], os.path.dirname(path))
        with mock.patch.object(page_module.QFileDialog, "getOpenFileName", return_value=("", "")):
            self.page.on_new_image({})
        self.assertEqual(len(self.page.spec["layers"]), 1)

    def test_dropped_files_become_layers_and_others_are_refused(self):
        notes = os.path.join(self.home.name, "notes.txt")
        with open(notes, "w", encoding="utf-8") as fh:
            fh.write("made up")
        self.assertTrue(self.page.accepts_drops())
        self.page.on_files_dropped([self.picture("a.png"), notes, self.picture("b.jpg", (100, 300))])
        self.assertEqual([layer["px"] for layer in self.page.spec["layers"]], [[400, 200], [100, 300]])
        self.assertEqual(self.last("toast")["text"], "Buddy couldn't read that image. PNG and JPEG images can be added.")

    def test_designs_are_saved_opened_started_and_deleted(self):
        self.draw(rect(), rev=1)
        self.page.on_save({"name": "  Badge ", "rev": 2})
        state = self.last("state")
        first = state["design_id"]
        self.assertEqual((state["spec"]["name"], state["saved"], [d["name"] for d in state["designs"]]), ("Badge", True, ["Badge"]))
        self.assertEqual(self.last("toast")["text"], "Design saved.")
        self.draw(rect(), rect(), rev=3, name="Badge")
        self.assertFalse(self.last("state")["saved"])
        self.page.on_save({"name": "Badge two", "copy": True, "rev": 4})
        second = self.last("state")["design_id"]
        self.assertNotEqual(second, first)
        self.assertEqual(len(store.load(first)["layers"]), 1)           # the one it came from is as it was
        self.page.on_open({"id": first, "rev": 5})
        state = self.last("state")
        self.assertEqual((state["spec"]["name"], state["design_id"], state["saved"], state["can_undo"]), ("Badge", first, True, False))
        self.assertEqual(self.last("picked"), {"ids": []})
        self.page.on_open({"id": "d-0000000000"})
        self.assertEqual(self.last("toast")["text"], "That design couldn't be opened.")
        self.page.on_delete_design({"id": first})
        state = self.last("state")
        self.assertEqual((state["design_id"], [d["id"] for d in state["designs"]], len(state["spec"]["layers"])), ("", [second], 1))
        self.page.on_new_design({"rev": 6})
        self.assertEqual(self.last("state")["spec"], M.clean({}))
        # The one that was open is open again next time.
        self.page.on_open({"id": second})
        again = type(self.page)(self.host)
        self.addCleanup(again.deleteLater)
        self.assertEqual((again.design_id, again.spec["name"]), (second, "Badge two"))

    def test_the_timeline_is_read_as_the_page_opens_and_sizes_an_empty_canvas(self):
        controller, _timeline = studio(size=(3840, 2160), fps=25)
        self.open(controller)
        self.page.on_shown()
        self.reader.run()
        state = self.last("state")
        self.assertEqual((state["connected"], state["timeline"], state["frame"], state["fps"]), (True, "Edit", [3840, 2160], 25.0))
        self.assertEqual(state["spec"]["size"], [3840, 2160])
        self.assertEqual(self.last("fonts")["fonts"]["Inter"], ["Black"])
        # One that has been drawn on keeps its size: it is fitted when it's added.
        host = Host(controller)
        host.tool_settings("design", {})["spec"] = design(rect())
        self.open(controller, host)
        self.page.on_shown()
        self.reader.run()
        self.assertEqual(self.last("state")["spec"]["size"], [1920, 1080])

    def test_add_puts_the_design_on_the_timeline(self):
        controller, timeline = studio()
        self.open(controller)
        self.page.on_add()
        self.assertEqual(self.last("toast")["text"], "There's nothing to add yet. Draw something on the canvas first.")
        self.draw(rect(), {"kind": "text"})
        self.page.on_add()
        self.assertTrue(self.page.adding)
        self.assertEqual(self.host.busy[-1], (True, "Adding the design…"))
        self.draw(rect(x=900), rev=5)                                   # nothing changes while it's being added
        self.reader.run()
        name, args = self.reader.asked[-1]
        self.assertEqual((name, args["spec"], args["replace"], set(args["metrics"])),
                         ("design.add", self.page.spec, True, {"Arial|Bold"}))
        self.assertFalse(self.page.adding)
        (item,) = timeline.tracks[1]
        self.assertIn("Text2Text = TextPlus {", item.comp.text)
        self.assertEqual(self.last("toast")["text"], "Design added on V1.")
        self.assertEqual(self.page.spec["layers"][0]["x"], 480.0)
        self.page.on_add()
        self.reader.run()
        self.assertEqual(self.last("toast")["text"], "Design replaced on V1.")

    def test_a_failed_add_is_said(self):
        controller, timeline = studio()
        timeline.takes_comps = False
        self.open(controller)
        self.draw(rect())
        self.page.on_add()
        self.reader.run()
        self.assertEqual(self.last("alert")["title"], "Couldn't add the design")
        self.assertFalse(self.page.adding)
        self.open()
        self.draw(rect())
        self.page.on_add()
        self.assertEqual(self.last("alert")["title"], "Not connected")


if __name__ == "__main__":
    unittest.main()
