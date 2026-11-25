"""Panels' comp (pages/panels/comp.py): the text of the Fusion comp a panel becomes - every
frame of clock.pose() baked to keys, thinned - and the job that puts it on a (fake)
timeline as a new clip (place.add), named in the Resolve reader's process."""

import json
import os
import re
import tempfile
import unittest

import _paths  # noqa: F401
from _child_reader import ChildReader, load_without_qt
from _fake_graphics import Controller, Project, studio

from core.fusion_place import PlaceError
from core.resolve_bridge import ResolveConnectionError
from pages.panels import clock, comp, model, place, solve, styles

FRAME = (1920, 1080)
FPS = 24.0
JOBS = {"panels.add": ("pages.panels.place", "add")}
# The template - three pages, a dashboard of ~40 boxes, build-on, a click, a slide - stays under this many tools
# (779 when a moving box began to carry what is in it, from 665 when each box moved alone).
MOST_TOOLS = 850


def page(pid, name, *children):
    return {"id": pid, "name": name, "root": {"id": f"b{pid[1:]}00", "role": "section", "pad": 14, "gap": 10,
                                              "children": list(children)}}


def text(bid, words, **more):
    return {"id": bid, "role": "text", "name": words, "text": words, **more}


def button(bid, words, **more):
    return {"id": bid, "role": "button", "name": words, "text": words, **more}


def panel(*pages, **more):
    """A still panel: build off, the cursor resting outside the window, no clicks."""
    raw = {"kind": "app", "style": "plain", "title": "Test", "width": 600.0, "zoom": 1.0, "x": 50.0, "y": 50.0,
           "pages": list(pages) or [page("p1", "Home", text("b1", "Hello"))], "start": "p1", "seconds": 3.0,
           "path": [[0.0, 95.0, 95.0]], "clicks": [], "cursor": "windows", "build": {"on": False}}
    return model.clean({**raw, **more})


def middle(spec, bid, frame=FRAME):
    """Where a box's middle is on the frame, % across and down."""
    solved = solve.solve_all(spec)
    for pid, solved_page in solved.items():
        for b in solved_page["boxes"]:
            if b["id"] == bid:
                return list(solve.to_frame(spec, solved[spec["start"]]["h"], b["x"] + b["w"] / 2, b["y"] + b["h"] / 2,
                                           frame))
    raise KeyError(bid)


def placed(spec, bid):
    return next(b for p in solve.solve_all(spec).values() for b in p["boxes"] if b["id"] == bid)


class Comp:
    """A comp's text, read back the way Fusion would: its tools, their inputs, their keys."""

    def __init__(self, spec, frame=FRAME, frames=None, metrics=None):
        self.spec, self.frame = spec, frame
        self.frames = frames or model.frames(spec, FPS)
        self.text = comp.comp_text(spec, frame, FPS, self.frames, metrics)
        self.tools = dict((n, k) for n, k in re.findall(r"^\t\t(\w+) = (\w+) \{", self.text, re.M))

    def body(self, name):
        start = self.text.index(f"\t\t{name} = ")
        return self.text[start:self.text.index("\n\t\t}", start)]

    def kind(self, kind):
        return [n for n, k in self.tools.items() if k == kind]

    def source(self, tool, name):
        m = re.search(rf"\b{name} = Input {{ SourceOp = \"(\w+)\"", self.body(tool))
        return m.group(1) if m else None

    def value(self, tool, name):
        m = re.search(rf"\b{name} = Input {{ Value = ([^,]+|\{{[^}}]*\}}), \}}", self.body(tool))
        if not m:
            return None
        raw = m.group(1).strip()
        return tuple(float(v) for v in raw.strip("{} ").split(",")) if raw.startswith("{") else float(raw)

    def keys(self, spline):
        return [(int(f), float(v)) for f, v in re.findall(r"\[(\d+)\] = \{ ([-\d.e]+)", self.body(spline))]

    def curve(self, tool, name):
        """[(frame, value)] keyed on an input, or None when it's plain."""
        src = self.source(tool, name)
        return self.keys(src) if src else None

    def path_keys(self, tool, name):
        """(x keys, y keys) of a Point input wired to an XYPath ([(0, v)] for a plain side)."""
        path = self.source(tool, name)
        out = []
        for side in ("X", "Y"):
            src = self.source(path, side)
            out.append(self.keys(src) if src else [(0, self.value(path, side))])
        return out


def at(keys, frame):
    """A keyed value on a frame: straight between keys."""
    if frame <= keys[0][0]:
        return keys[0][1]
    for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
        if f0 <= frame <= f1:
            return v0 + (v1 - v0) * (frame - f0) / (f1 - f0)
    return keys[-1][1]


def poses(spec, frames, frame=FRAME):
    clk = clock.clock(spec, FPS, frame)
    return clk, [clock.pose(spec, clk, f) for f in range(frames)]


def look(pose, pid, bid, key):
    entry = next((e for e in pose["pages"] if e["page"] == pid), None)
    return (entry["boxes"].get(bid) or clock.REST)[key] if entry else None


class ThinTests(unittest.TestCase):
    def test_a_held_value_is_no_keys_and_a_step_is_four(self):
        self.assertIsNone(comp._thin([0.5] * 100, 0.001))
        self.assertEqual(comp._thin([0.0] * 50 + [1.0] * 50, 0.001), [(0, 0.0), (49, 0.0), (50, 1.0), (99, 1.0)])

    def test_a_curve_keeps_its_turns_and_stays_within_its_tolerance(self):
        values = [0.0] * 20 + [(1 - (1 - i / 30) ** 3) for i in range(31)] + [1.0] * 30 + [1 - i / 30 for i in range(31)]
        keys = comp._thin(values, 0.002)
        self.assertLess(len(keys), len(values) / 2)
        frames = [f for f, _v in keys]
        for turn in (0, 20, 50, 81, len(values) - 1):          # where it starts, turns, holds and ends
            self.assertIn(turn, frames)
        for f, v in enumerate(values):
            self.assertAlmostEqual(at(keys, f), v, delta=0.002 + 1e-9)


class CompTests(unittest.TestCase):
    def test_its_whole_balanced_lua_with_every_tool_named_once_and_tagged(self):
        spec = model.clean({})
        made = Comp(spec)
        text = made.text
        self.assertTrue(text.startswith("Composition {"))
        bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)
        self.assertEqual(bare.count("{"), bare.count("}"))
        names = re.findall(r"^\t\t(\w+) = \w+ \{", text, re.M)
        self.assertEqual(len(names), len(set(names)))
        for used in set(re.findall(r'SourceOp = "(\w+)"', text)):
            self.assertIn(used, made.tools)
        gid = model.panel_id(spec, FRAME, FPS)
        self.assertIn(f'CustomData = {{ TEMPLATE_ID = "Text+", {comp.TAG} = "{gid}" }}', text)
        self.assertEqual(comp.TAG, "BuddyPanel")
        self.assertEqual(made.source("MediaOut1", "Input"), "CursorOn")
        self.assertNotIn("GroupOperator", text)
        self.assertNotIn("sMerge", text)
        self.assertNotIn("Expression", text)                                # baked: nothing in Resolve is live

    def test_the_template_stays_small(self):
        made = Comp(model.clean({}))
        self.assertLess(len(made.tools), MOST_TOOLS)
        self.assertNotIn("Transform", made.tools.values())                  # moves are the Merges' own Size and Center

    def test_one_text_plus_for_every_line_of_text(self):
        spec = panel(page("p1", "Home", text("b1", "Hello"), text("b2", "A longer line that wraps onto two lines here " * 3),
                          button("b3", "Go")))
        lines = [p for p in styles.drawn(spec, "p1") if p["kind"] == "text"]
        made = Comp(spec)
        self.assertEqual(len(made.kind("TextPlus")), len(lines))
        said = sorted(re.findall(r'StyledText = Input \{ Value = "([^"]*)"', made.text))
        self.assertEqual(said, sorted(p["text"] for p in lines))
        bold = [n for n in made.kind("TextPlus") if made.body(n).find('Style = Input { Value = "Bold"') > 0]
        self.assertEqual(len(bold), sum(1 for p in lines if p["bold"]))

    def test_text_is_sized_and_set_on_its_baseline_by_the_faces_metrics(self):
        spec = panel(page("p1", "Home", text("b1", "Hello")))
        metrics = {"Arial|Regular": {"em": 0.8, "baseline": 0.3}}
        made = Comp(spec, metrics=metrics)
        p = next(p for p in styles.drawn(spec, "p1") if p["kind"] == "text" and p["box"] == "b1")
        name = "Home_Hello_Label"
        k = solve.scale(spec, FRAME)
        size = p["size"] * k
        self.assertAlmostEqual(made.value(name, "Size"), size / (0.8 * FRAME[0]), places=5)
        u = comp._Units(spec, FRAME, solve.solve_all(spec)["p1"]["h"])
        fx, fy = u.px(p["x"], p["y"])
        cx, cy = made.value(name, "Center")
        self.assertAlmostEqual(cx, fx / FRAME[0], places=5)
        self.assertAlmostEqual(cy, 1 - (fy - 0.3 * size) / FRAME[1], places=5)
        self.assertEqual(made.value(name, "HorizontalLeftCenterRight"), -1)

    def test_a_still_box_has_no_keys(self):
        made = Comp(panel(page("p1", "Home", text("b1", "Hello"), button("b2", "Go"))))
        self.assertNotIn("BezierSpline", made.tools.values())
        self.assertNotIn("XYPath", made.tools.values())
        self.assertIn("Home_Hello_LabelOn", made.tools)                     # straight into the page's chain
        self.assertNotIn("Home_HelloCanvas", made.tools)

    def test_a_hovered_box_grows_to_the_poses_scale_and_glows(self):
        spec = panel(page("p1", "Home", text("b1", "Hello"), button("b2", "Go")))
        spot = middle(spec, "b2")
        spec = model.clean({**spec, "path": [[0.0, 95.0, 95.0], [1.0, *spot], [3.0, *spot]]})
        made = Comp(spec)
        _clk, seen = poses(spec, made.frames)
        scales = [look(p, "p1", "b2", "scale") for p in seen]
        size = made.curve("Home_GoOn", "Size")
        self.assertIsNotNone(size)
        self.assertAlmostEqual(max(v for _f, v in size), max(scales), places=4)
        self.assertAlmostEqual(max(scales), 1 + spec["answers"]["hover"]["grow"] / 100, places=4)
        for f in range(made.frames):
            self.assertAlmostEqual(at(size, f), scales[f], delta=2 * comp.SCALE_TOL)
        # It grows about its own middle: the Merge's Center makes up for sizing about the frame's.
        b = placed(spec, "b2")
        u = comp._Units(spec, FRAME, solve.solve_all(spec)["p1"]["h"])
        mx, my = u.point(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
        xs, ys = made.path_keys("Home_GoOn", "Center")
        s = max(scales)
        f = scales.index(s)
        self.assertAlmostEqual(at(xs, f), (1 - s) * mx + s * 0.5, places=4)
        self.assertAlmostEqual(at(ys, f), (1 - s) * my + s * 0.5 - u.high(look(seen[f], "p1", "b2", "dy")), places=4)
        glow = made.curve("Home_GoGlowOn", "Blend")
        self.assertAlmostEqual(max(v for _f, v in glow), spec["answers"]["hover"]["glow"] / 100, places=3)
        self.assertEqual(made.source("Home_GoGlowOn", "Background"), "Home_GoCanvas")
        self.assertIsNone(made.curve("Home_HelloOn", "Size") if "Home_HelloOn" in made.tools else None)

    def two_pages(self, how, first=(), second=()):
        """A panel whose button is clicked at 1 s, going to the second page with `how`:
        (spec, comp, the change)."""
        go = button("b2", "Go", go={"page": "p2", "with": how})
        spec = panel(page("p1", "Home", text("b1", "Hello"), *first, go),
                     page("p2", "Next", text("b11", "There"), *second))
        spot = middle(spec, "b2")
        # The cursor clicks the button and then leaves the window, so nothing on the new page is hovered.
        spec = model.clean({**spec, "path": [[0.0, *spot], [1.2, *spot], [1.25, 98.0, 98.0], [3.0, 98.0, 98.0]],
                            "clicks": [[1.0, 1.2]], "go_seconds": 0.5})
        made = Comp(spec)
        change = next(c for c in clock.clock(spec, FPS, FRAME)["pages"] if c["why"] == "click")
        return spec, made, change

    def test_a_fade_keys_both_pages_blends_across_exactly_its_frames(self):
        _spec, made, change = self.two_pages("fade")
        start, end = change["at"], change["at"] + change["frames"]
        leaving, arriving = made.curve("HomeOn", "Blend"), made.curve("NextOn", "Blend")
        for keys, before, after in ((leaving, 1.0, 0.0), (arriving, 0.0, 1.0)):
            self.assertEqual(at(keys, start), before)
            self.assertEqual(at(keys, end), after)
            self.assertEqual({f for f, v in keys if 0 < v < 1} <= set(range(start + 1, end)), True)
            self.assertIn(start, [f for f, _v in keys])
            self.assertIn(end, [f for f, _v in keys])
        for name in ("HomeOn", "NextOn"):
            self.assertEqual(made.source(name, "EffectMask"), "WindowInsideBody")   # kept to the window's inside
            self.assertIsNone(made.source(name, "Center"))

    def test_a_slide_moves_the_pages_by_their_merges_center(self):
        spec, made, change = self.two_pages("slide")
        start, end = change["at"], change["at"] + change["frames"]
        u = comp._Units(spec, FRAME, solve.solve_all(spec)["p1"]["h"])
        slide = u.wide(clock.SLIDE * spec["width"])
        (xs, _ys) = made.path_keys("NextOn", "Center")
        self.assertAlmostEqual(at(xs, start), 0.5 + slide, places=4)
        self.assertAlmostEqual(at(xs, end), 0.5, places=4)
        (xs, _ys) = made.path_keys("HomeOn", "Center")
        self.assertAlmostEqual(at(xs, start), 0.5, places=4)
        self.assertLess(at(xs, end - 1), 0.5 - slide * 0.8)
        self.assertIsNotNone(made.curve("HomeOn", "Blend"))

    def test_a_match_glides_the_box_of_the_same_name(self):
        card = {"role": "section", "variant": "card", "name": "Card", "pad": 10, "children": [text("b5", "Total")]}
        spec, made, change = self.two_pages("match", first=[{**card, "id": "b4"}],
                                            second=[text("b12", "Pushed down " * 4), {**card, "id": "b14",
                                                    "children": [text("b15", "Total")]}])
        start, end = change["at"], change["at"] + change["frames"]
        # Both pages stay whole while it runs (their boxes fade); the page left goes at its end.
        self.assertEqual(made.curve("HomeOn", "Blend"), [(0, 1.0), (end - 1, 1.0), (end, 0.0), (made.frames - 1, 0.0)])
        old, new = placed(spec, "b4"), placed(spec, "b14")
        u = comp._Units(spec, FRAME, solve.solve_all(spec)["p1"]["h"])
        _xs, ys = made.path_keys("Next_CardOn", "Center")
        dy = (old["y"] + old["h"] / 2) - (new["y"] + new["h"] / 2)
        self.assertNotAlmostEqual(dy, 0.0)
        self.assertAlmostEqual(at(ys, start), 0.5 - u.high(dy), places=4)
        self.assertAlmostEqual(at(ys, end), 0.5, places=4)
        # The card it came from doesn't show: its own prims (its "own"), and what's in it that has a pair.
        own = [n for n in made.tools if n.startswith("Home_Card_Rect") and n.endswith("On") and made.tools[n] == "Merge"]
        self.assertTrue(own)
        for name in own:
            self.assertEqual(at(made.curve(name, "Blend"), start + 1), 0.0)
        self.assertEqual(at(made.curve("Home_TotalOn", "Blend"), start + 1), 0.0)
        # The rest fade - once, in what they're in: the page's own root.
        faded = made.curve("Home_ColumnOn", "Blend")
        self.assertEqual((at(faded, start), at(faded, end - 1) < 0.5), (1.0, True))
        self.assertNotIn("Home_HelloOn", made.tools)                        # carried by the root, nothing of its own

    def test_build_on_keys_each_box_in_and_up(self):
        spec = panel(page("p1", "Home", text("b1", "Hello"), text("b2", "World")), build={"on": True})
        made = Comp(spec)
        builds = clock.clock(spec, FPS, FRAME)["build"]["p1"]
        u = comp._Units(spec, FRAME, solve.solve_all(spec)["p1"]["h"])
        root = "Home_ColumnOn"                                              # the page's root arrives first, and carries them
        self.assertEqual(made.source("Home_HelloOn", "Background"), "Home_ColumnCanvas")
        for name, bid in (("Home_HelloOn", "b1"), ("Home_WorldOn", "b2")):
            start, n = builds[bid]
            for f in range(start, start + n + 1):
                # Put together, each arrives as its own build says: faded in once, risen 8 px.
                there = clock._eased((f - start) / n, clock.ARRIVES)
                alpha = at(made.curve(root, "Blend"), f) * at(made.curve(name, "Blend"), f)
                self.assertAlmostEqual(alpha, there, delta=0.004)
                rise = sum(0.5 - at(made.path_keys(tool, "Center")[1], f) for tool in (root, name))
                self.assertAlmostEqual(rise, u.high((1 - there) * clock.RISE), delta=2 * comp.PX_TOL / FRAME[1])
        self.assertGreater(builds["b2"][0], builds["b1"][0])

    def test_a_chart_draws_on_to_its_whole_length(self):
        chart = {"id": "b3", "role": "chart", "name": "Sales", "chart": {"shape": "line", "rows": [
            {"label": "Mon", "value": 3}, {"label": "Tue", "value": 5}, {"label": "Wed", "value": 4}]}}
        made = Comp(panel(page("p1", "Home", chart), build={"on": True}))
        lengths = [n for n in made.kind("BezierSpline") if n.endswith("WriteLength")]
        self.assertEqual(len(lengths), 1)
        keys = made.keys(lengths[0])
        self.assertEqual((keys[0][1], keys[-1][1]), (0.0, 1.0))
        (mask,) = [n for n in made.kind("PolylineMask") if made.source(n, "WriteLength") == lengths[0]]
        self.assertIsNotNone(made.value(mask, "BorderWidth"))

    def test_a_pie_sweeps_and_bars_grow(self):
        pie = {"id": "b3", "role": "chart", "name": "Share", "chart": {"shape": "pie", "rows": [
            {"label": "A", "value": 1}, {"label": "B", "value": 3}]}}
        bars = {"id": "b4", "role": "chart", "name": "Bars", "chart": {"shape": "bars", "rows": [
            {"label": "A", "value": 1}, {"label": "B", "value": 3}]}}
        made = Comp(panel(page("p1", "Home", pie, bars), build={"on": True}))
        sweeps = [n for n in made.kind("EllipseMask") if made.source(n, "WriteLength")]
        self.assertEqual(len(sweeps), 2)
        last = made.curve(sweeps[-1], "WriteLength")
        first = made.curve(sweeps[0], "WriteLength")
        self.assertAlmostEqual(first[-1][1] + last[-1][1], 1.0, places=3)  # whole, at the end
        self.assertEqual(made.value(sweeps[0], "Angle"), 90)
        grown = [n for n in made.kind("RectangleMask") if made.source(n, "Height")]
        self.assertEqual(len(grown), 2)
        self.assertEqual(made.curve(grown[0], "Height")[0][1], 0.0)

    def test_the_cursor_is_a_keyed_path_and_dips_or_isnt_there(self):
        spec = panel(path=[[0.0, 10.0, 10.0], [2.0, 60.0, 40.0], [3.0, 60.0, 40.0]], clicks=[[2.2, 2.4]])
        made = Comp(spec)
        self.assertEqual(made.source("CursorMask", "Center"), "CursorPath")
        self.assertEqual(made.source("CursorEdgeMask", "Center"), "CursorPath")
        xs, ys = made.path_keys("CursorMask", "Center")
        self.assertAlmostEqual(xs[0][1], 0.10, places=3)
        self.assertAlmostEqual(at(ys, 48), 1 - 0.40, places=3)
        self.assertLess(len(xs), 12)                                        # thinned, not a key a frame
        dip = made.curve("CursorMask", "Size")
        self.assertAlmostEqual(min(v for _f, v in dip), 1 - comp.CURSOR_DIP)
        self.assertEqual(at(dip, int(2.3 * FPS)), 1 - comp.CURSOR_DIP)
        gone = Comp(panel(cursor="none"))
        self.assertFalse([n for n in gone.tools if n.startswith("Cursor")])
        self.assertEqual(gone.source("MediaOut1", "Input"), "HomeOn")

    def test_a_prims_px_land_where_the_units_say(self):
        divider = {"id": "b2", "role": "divider", "name": "Rule"}
        for frame, zoom in (((1920, 1080), 1.0), ((3840, 2160), 1.6), ((1080, 1920), 0.7)):
            spec = panel(page("p1", "Home", text("b1", "Hello"), divider), zoom=zoom, x=30.0, y=40.0)
            made = Comp(spec, frame=frame)
            p = next(p for p in styles.drawn(spec, "p1") if p["box"] == "b2")
            height = solve.solve_all(spec)["p1"]["h"]
            k = zoom * frame[0] / 1920
            fx = 0.30 * frame[0] + (p["x"] + p["w"] / 2 - spec["width"] / 2) * k
            fy = 0.40 * frame[1] + (p["y"] + p["h"] / 2 - height / 2) * k
            cx, cy = made.value("Home_Rule_RectMask", "Center")
            self.assertAlmostEqual(cx, fx / frame[0], places=5)
            self.assertAlmostEqual(cy, 1 - fy / frame[1], places=5)
            self.assertAlmostEqual(made.value("Home_Rule_RectMask", "Width"), p["w"] * k / frame[0], places=5)
            self.assertAlmostEqual(made.value("Home_Rule_RectMask", "Height"), p["h"] * k / frame[1], places=5)
            # The window's middle is at x, y.
            fx, fy = solve.to_frame(spec, height, spec["width"] / 2, height / 2, frame)
            self.assertEqual((round(fx, 6), round(fy, 6)), (30.0, 40.0))

    def test_neighbouring_fills_of_one_colour_share_a_background(self):
        row = {"id": "b9", "role": "section", "dir": "row", "name": "Row",
               "children": [{"id": f"b{i}", "role": "icon", "variant": "avatar", "name": "Dot"} for i in (1, 2, 3)]}
        spec = panel(page("p1", "Home", row))
        self.assertEqual(len({p["fill"] for p in styles.drawn(spec, "p1") if p["box"] in ("b1", "b2", "b3")}), 1)
        made = Comp(spec)
        shared = [n for n in made.tools if n.startswith("Home_Dot_Shapes")]
        self.assertIn("Home_Dot_ShapesFill", shared)
        self.assertEqual(made.source("Home_Dot_ShapesMask2", "EffectMask"), "Home_Dot_ShapesMask1")
        self.assertEqual(made.source("Home_Dot_ShapesFill", "EffectMask"), "Home_Dot_ShapesMask3")

    def test_a_hovered_card_carries_its_text(self):
        card = {"id": "b4", "role": "section", "variant": "card", "name": "Card", "pad": 10, "children": [text("b5", "Total")]}
        spec = panel(page("p1", "Home", card))
        b = placed(spec, "b4")
        spot = list(solve.to_frame(spec, solve.solve_all(spec)["p1"]["h"], b["x"] + 4, b["y"] + 4, FRAME))
        spec = model.clean({**spec, "path": [[0.0, 95.0, 95.0], [1.0, *spot], [3.0, *spot]]})
        made = Comp(spec)
        self.assertIsNotNone(made.curve("Home_CardOn", "Size"))
        # The text has no pose of its own; it is drawn on the card's canvas, so the card's Merge sizes and lifts it.
        tool, path = "Home_Total_LabelOn", []
        while tool and made.tools.get(tool) == "Merge":
            path.append(tool)
            tool = made.source(tool, "Background")
        self.assertEqual(tool, "Home_CardCanvas")
        self.assertNotIn("Home_TotalOn", made.tools)
        _clk, seen = poses(spec, made.frames)
        solved = solve.solve_all(spec)["p1"]
        f = made.frames - 1
        moved = clock.carried(solved, seen[f]["pages"][0]["boxes"], "b5")
        self.assertAlmostEqual(moved["scale"], at(made.curve("Home_CardOn", "Size"), f), places=4)
        self.assertNotEqual(round(moved["dy"], 3), 0.0)

    def test_a_linked_box_that_doesnt_answer_still_changes_the_page(self):
        go = button("b2", "Go", reacts=False, go={"page": "p2", "with": "fade"})
        spec = panel(page("p1", "Home", text("b1", "Hello"), go), page("p2", "Next", text("b11", "There")))
        spot = middle(spec, "b2")
        spec = model.clean({**spec, "path": [[0.0, *spot], [3.0, *spot]], "clicks": [[1.0, 1.2]]})
        made = Comp(spec)
        self.assertIn("NextOn", made.tools)                                 # the page it goes to shows
        self.assertIsNone(made.curve("Home_GoOn", "Size") if "Home_GoOn" in made.tools else None)   # and it never answers
        self.assertFalse([n for n in made.tools if n.startswith("Home_GoGlow")])

    def test_an_area_wipes_on_with_its_line(self):
        chart = {"id": "b3", "role": "chart", "name": "Sales", "chart": {"shape": "area", "rows": [
            {"label": "Mon", "value": 3}, {"label": "Tue", "value": 5}, {"label": "Wed", "value": 4}]}}
        spec = panel(page("p1", "Home", chart), build={"on": True})
        area = next(p for p in styles.drawn(spec, "p1") if p["kind"] == "poly" and p.get("fill"))
        self.assertEqual(area["draw"], "across")
        made = Comp(spec)
        (mask,) = [n for n in made.kind("PolylineMask") if made.source(n, "EffectMask")]
        wipe = made.source(mask, "EffectMask")
        self.assertIn('PaintMode = Input { Value = FuID { "Multiply" }', made.body(mask))
        width = made.curve(wipe, "Width")
        xs = [x for x, _y in area["points"]]
        u = comp._Units(spec, FRAME, solve.solve_all(spec)["p1"]["h"])
        shares = clock.clock(spec, FPS, FRAME)["draw"]["b3"]
        self.assertEqual(width[0][1], 0.0)
        self.assertAlmostEqual(width[-1][1], u.wide(max(xs) - min(xs)), places=5)
        for f in (5, 10, 20):
            self.assertAlmostEqual(at(width, f), u.wide((max(xs) - min(xs)) * shares[f]), delta=2 * comp.PX_TOL / FRAME[0])
        still = Comp(panel(page("p1", "Home", chart)))                      # with no build it is simply there
        self.assertFalse([n for n in still.kind("PolylineMask") if still.source(n, "EffectMask")])

    def test_going_back_puts_the_arriving_page_on_top_as_pose_does(self):
        spec = panel(page("p1", "Home", text("b1", "Hello")), page("p2", "Next", text("b11", "There")),
                     page_keys=[[1.0, "p2", "fade"], [2.0, "p1", "fade"]], go_seconds=0.5)
        made = Comp(spec)
        changes = clock.clock(spec, FPS, FRAME)["pages"]
        back = next(c for c in changes if c["page"] == "p1" and c["why"] == "key")
        start, end = back["at"], back["at"] + back["frames"]
        _clk, seen = poses(spec, made.frames)
        self.assertEqual([p["page"] for p in seen[start + 2]["pages"]], ["p2", "p1"])      # pose: Home over Next
        # Home is put on a second time, after Next, while it arrives over it - and not by its own Merge then.
        self.assertEqual(made.source("HomeOnTop", "Background"), "NextOn")
        top, low = made.curve("HomeOnTop", "Blend"), made.curve("HomeOn", "Blend")
        for f in range(made.frames):
            want = next((p["alpha"] for p in seen[f]["pages"] if p["page"] == "p1"), 0.0)
            self.assertAlmostEqual(at(top, f) + at(low, f), want, delta=0.003)
            if start < f < end:
                self.assertEqual(at(low, f), 0.0)
            elif f < start:
                self.assertEqual(at(top, f), 0.0)
        self.assertNotIn("NextOnTop", made.tools)
        self.assertNotIn("HomeOnTop", Comp(self.two_pages("fade")[0]).tools)  # going forward needs nothing more

    def test_pages_that_never_show_are_left_out(self):
        made = Comp(panel(page("p1", "Home", text("b1", "Hello")), page("p2", "Hidden", text("b2", "Never"))))
        self.assertNotIn("HiddenOn", made.tools)
        self.assertNotIn("Never", made.text)


LOGO = "D:\\Footage\\logo.png"


def texts(made, spline):
    """[(frame, text)] of a keyed text input's spline."""
    return [(int(f), json.loads(f'"{t}"')) for f, t in
            re.findall(r'\[(\d+)\] = \{ \d+, Flags = \{[^}]*\}, Value = Text \{ Value = "((?:[^"\\]|\\.)*)" \}', made.body(spline))]


def text_at(keys, frame):
    return [t for f, t in keys if f <= frame][-1]


class RoundTwoCompTests(unittest.TestCase):
    def photo(self, px=(400, 200), **more):
        box = {"id": "b2", "role": "image", "name": "Photo", "w": 200, "h": 200, "image": {"path": LOGO, "px": list(px)}}
        return panel(page("p1", "Home", box), **more)

    def test_a_picture_is_a_loader_sized_to_its_crop_and_cut_by_its_boxs_mask(self):
        spec = self.photo()
        made = Comp(spec)
        prim = next(p for p in styles.drawn(spec, "p1") if p["kind"] == "image")
        self.assertEqual(prim["crop"], [100.0, 0.0, 200.0, 200.0])
        loader = made.body("Home_Photo_PictureImage")
        self.assertEqual(made.tools["Home_Photo_PictureImage"], "Loader")
        self.assertIn('Filename = "D:\\\\Footage\\\\logo.png", FormatID = "PNGFormat",', loader)
        self.assertIn('["Clip1.PNGFormat.PostMultiply"] = Input { Value = 1, },', loader)
        self.assertNotIn("AddTool", made.text)
        fill = "Home_Photo_PictureFill"
        self.assertEqual(made.tools[fill], "Merge")
        self.assertEqual((made.source(fill, "Background"), made.source(fill, "Foreground"), made.source(fill, "EffectMask")),
                         ("Home_Photo_PictureBack", "Home_Photo_PictureImage", "Home_Photo_PictureMask"))
        k = solve.scale(spec, FRAME)
        self.assertAlmostEqual(made.value(fill, "Size"), prim["w"] * k / 200, places=5)    # the crop's width is the box's
        u = comp._Units(spec, FRAME, solve.solve_all(spec)["p1"]["h"])
        cx, cy = made.value(fill, "Center")
        mx, my = u.point(prim["x"] + prim["w"] / 2, prim["y"] + prim["h"] / 2)
        self.assertAlmostEqual(cx, mx, places=5)
        self.assertAlmostEqual(cy, my, places=5)
        mask = "Home_Photo_PictureMask"
        self.assertAlmostEqual(made.value(mask, "Width"), u.wide(prim["w"]), places=5)
        self.assertAlmostEqual(made.value(mask, "Height"), u.high(prim["h"]), places=5)
        self.assertAlmostEqual(made.value(mask, "CornerRadius"), prim["radius"] / (min(prim["w"], prim["h"]) / 2), places=5)
        self.assertEqual(made.source("Home_Photo_PictureFillOn", "Foreground"), fill)
        # A tall picture is sized by its width too: the crop always has the box's shape.
        tall = Comp(self.photo((100, 400)))
        self.assertAlmostEqual(tall.value(fill, "Size"), prim["w"] * k / 100, places=5)
        jpeg = Comp(panel(page("p1", "Home", {"id": "b2", "role": "image", "name": "Photo",
                                              "image": {"path": "D:\\Footage\\still.jpg", "px": [10, 10]}})))
        self.assertIn('FormatID = "JpegFormat"', jpeg.body("Home_Photo_PictureImage"))
        self.assertNotIn("PostMultiply", jpeg.body("Home_Photo_PictureImage"))

    def test_a_picture_that_arrives_moves_as_every_box_does(self):
        spec = self.photo(build={"on": True})
        made = Comp(spec)
        self.assertEqual(made.source("Home_PhotoOn", "Foreground"), "Home_Photo_PictureFill")
        self.assertIsNotNone(made.curve("Home_PhotoOn", "Blend"))
        self.assertIsNotNone(made.path_keys("Home_PhotoOn", "Center"))

    def test_a_line_that_types_keys_its_letters_in_place(self):
        hi = text("b1", "Hello you", type={"speed": 12})
        spec = panel(page("p1", "Home", hi))
        made = Comp(spec)
        label = "Home_Hello_You_Label"
        self.assertEqual(made.source(label, "StyledText"), f"{label}Typed")
        keys = texts(made, f"{label}Typed")
        typed = clock.clock(spec, FPS, FRAME)["typed"]["b1"]
        for f in range(made.frames):
            self.assertEqual(text_at(keys, f), "Hello you"[:typed[f]], f)
        self.assertEqual(keys[0], (0, "H"))
        self.assertEqual(len(keys), 9)                                      # a key each letter, not each frame
        # Set from its start, and nothing about where it is changes as it types.
        prim = next(p for p in styles.drawn(spec, "p1") if p["box"] == "b1")
        self.assertEqual((prim["align"], made.value(label, "HorizontalLeftCenterRight")), ("left", -1))
        self.assertIsNone(made.source(label, "Center"))
        u = comp._Units(spec, FRAME, solve.solve_all(spec)["p1"]["h"])
        self.assertAlmostEqual(made.value(label, "Center")[0], u.px(prim["x"], prim["y"])[0] / FRAME[0], places=5)

    def test_the_caret_steps_with_the_letters_and_blinks(self):
        spec = panel(page("p1", "Home", text("b1", "Hi", type={"speed": 4})), seconds=4.0)
        made = Comp(spec)
        clk = clock.clock(spec, FPS, FRAME)
        marks = clk["caret"]["b1"]
        u = comp._Units(spec, FRAME, solve.solve_all(spec)["p1"]["h"])
        xs, ys = made.path_keys("Home_Hi_CaretMask", "Center")
        for f in range(made.frames):
            if marks[f] is None:
                continue
            x, y, w, h = marks[f]
            cx, cy = u.point(x + w / 2, y + h / 2)
            self.assertAlmostEqual(at(xs, f), cx, delta=comp.PX_TOL / FRAME[0] + 1e-9)
            self.assertAlmostEqual(at(ys, f), cy, delta=comp.PX_TOL / FRAME[1] + 1e-9)
        self.assertAlmostEqual(made.value("Home_Hi_CaretMask", "Width"), u.wide(marks[0][2]), places=5)
        blend = made.curve("Home_Hi_CaretFillOn", "Blend")
        for f in range(made.frames):
            self.assertEqual(round(at(blend, f), 6), 0.0 if marks[f] is None else 1.0, f)
        label = next(p for p in styles.drawn(spec, "p1") if p["box"] == "b1")
        self.assertIn(f'{made.source("Home_Hi_CaretFill", "EffectMask")}', made.tools)
        self.assertEqual(comp._with_carets([label], clk["caret"])[1], {"box": "b1", "kind": "caret", "fill": label["fill"]})
        quiet = Comp(panel(page("p1", "Home", text("b1", "Hi", type={"caret": False}))))
        self.assertFalse([n for n in quiet.tools if "Caret" in n])

    def test_a_field_shows_its_placeholder_until_its_first_letter(self):
        field = {"id": "b2", "role": "field", "name": "Email", "text": "Email",
                 "type": {"text": "tess@example.com", "caret": False}}
        spec = panel(page("p1", "Home", field))
        spot = middle(spec, "b2")
        spec = model.clean({**spec, "path": [[0.0, *spot], [3.0, *spot]], "clicks": [[1.0, 1.2]]})
        made = Comp(spec)
        typed = clock.clock(spec, FPS, FRAME)["typed"]["b2"]
        start = typed.index(1)
        self.assertEqual(start, 24)
        hint = made.curve("Home_Email_LabelOn", "Blend")
        self.assertEqual((at(hint, start - 1), at(hint, start)), (1.0, 0.0))
        keys = texts(made, "Home_Email_Label2Typed")
        self.assertEqual((text_at(keys, start - 1), text_at(keys, start), text_at(keys, made.frames - 1)),
                         ("", "t", "tess@example.com"))
        never = Comp(model.clean({**spec, "clicks": []}))                   # never clicked: only its placeholder
        self.assertNotIn("Home_Email_Label2", never.tools)
        self.assertIsNone(never.curve("Home_Email_LabelOn", "Blend"))

    def test_a_box_that_pops_in_is_sized_by_its_merge(self):
        spec = panel(page("p1", "Home", button("b2", "Go", arrive={"kind": "pop"}), button("b3", "Stay")), build={"on": True})
        made = Comp(spec)
        start, n = clock.clock(spec, FPS, FRAME)["build"]["p1"]["b2"]
        size = made.curve("Home_GoOn", "Size")
        self.assertAlmostEqual(at(size, start), clock.POP, places=3)
        self.assertGreater(max(v for _f, v in size), 1.0)                   # past its size a little
        self.assertAlmostEqual(at(size, start + n), 1.0, places=4)
        self.assertIsNone(made.curve("Home_StayOn", "Size") if "Home_StayOn" in made.tools else None)   # a rise only moves
        still = Comp(model.clean({**spec, "build": {"on": False}}))         # its own arrival, though the page doesn't build
        self.assertIsNotNone(still.curve("Home_GoOn", "Size"))
        self.assertNotIn("Home_StayOn", still.tools)

    def test_a_missing_picture_is_said_and_nothing_is_added(self):
        controller, timeline = studio()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "logo.png")
            spec = panel(page("p1", "Home", {"id": "b2", "role": "image", "name": "Photo",
                                             "image": {"path": path, "px": [10, 10]}}))
            with self.assertRaises(PlaceError) as caught:
                place.add(controller, spec)
            self.assertEqual(str(caught.exception),
                             "The image \"logo.png\" isn't where it was. Put it back, or take it off its image box.")
            self.assertEqual(timeline.tracks[1], [])
            with open(path, "wb") as fh:
                fh.write(b"made up")
            done = place.add(controller, spec)                              # there again: on it goes
            self.assertIn("Home_Photo_PictureImage = Loader {", timeline.tracks[done["track"]][0].comp.text)


class JobTests(unittest.TestCase):
    def test_the_job_loads_without_qt_and_is_named_in_the_readers_process(self):
        from core import resolve_reader_child
        self.assertEqual(load_without_qt(JOBS), "")
        for name, where in JOBS.items():
            self.assertEqual(resolve_reader_child.JOBS[name], where)

    def test_it_is_a_new_clip_at_the_playhead(self):
        controller, timeline = studio()
        footage = timeline.put(1, 86400, 90000)
        timeline.playhead = 86400 + 48
        spec = panel(seconds=2.0)
        done = place.add(controller, spec, {})
        self.assertEqual((done["track"], done["start"], done["end"], done["replaced"]), (2, 86448, 86496, False))
        self.assertEqual((done["timeline"], done["fps"], done["frame"], done["seconds"]), ("Edit", 24.0, [1920, 1080], 2.0))
        self.assertEqual(done["id"], model.panel_id(spec, FRAME, 24.0))
        (item,) = timeline.tracks[2]
        self.assertEqual(item.comp.GetData(comp.TAG), done["id"])
        self.assertIn("HomeOn = Merge {", item.comp.text)
        self.assertEqual(timeline.tracks[1], [footage])                     # nothing of theirs touched
        self.assertEqual(timeline.playhead, 86448)

    def test_adding_again_replaces_buddys_own_unless_told_not_to(self):
        controller, timeline = studio()
        place.add(controller, panel())
        again = place.add(controller, panel(accent="#e0503c"))
        self.assertTrue(again["replaced"])
        self.assertEqual(len(timeline.tracks[1]), 1)
        kept = place.add(controller, panel(), replace=False)
        self.assertEqual((kept["track"], kept["replaced"]), (2, False))
        theirs = timeline.put(3, 86400, 86500, data={"BuddyDesign": "bd-1"})    # another tool's graphic is someone else's
        other = place.add(controller, panel())
        self.assertNotEqual(other["track"], 3)
        self.assertIn(theirs, timeline.tracks[3])

    def test_no_project_no_timeline_or_a_refused_comp_is_said_and_adds_nothing(self):
        with self.assertRaises(ResolveConnectionError):
            place.add(Controller(None), panel())
        with self.assertRaises(ResolveConnectionError) as caught:
            place.add(Controller(Project(None)), panel())
        self.assertIn("Open a timeline", str(caught.exception))
        controller, timeline = studio()
        timeline.takes_comps = False
        with self.assertRaises(PlaceError) as caught:
            place.add(controller, panel())
        self.assertIn("panel", str(caught.exception))
        self.assertEqual(timeline.tracks[1], [])

    def test_it_runs_through_the_readers_process(self):
        controller, timeline = studio()
        reader = ChildReader(controller, JOBS)
        got = []
        reader.ask("panels.add", {"spec": panel(), "metrics": {"Arial|Bold": {"em": 0.72, "baseline": 0.37}}},
                   lambda result, error: got.append((result, error)))
        reader.run()
        ((result, error),) = got
        self.assertIsNone(error)
        self.assertEqual(result["track"], 1)
        self.assertEqual(timeline.tracks[1][0].comp.GetData(comp.TAG), result["id"])


if __name__ == "__main__":
    unittest.main()
