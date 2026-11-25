"""Design's flips (Shift+H, Shift+V): a layer's own picture mirrored (flipx / flipy in
model.clean), the rule a flip follows (geometry.flipped), the outline and the comp drawn
mirrored to match, and the page's buttons and keys."""

import re
import unittest

import _paths  # noqa: F401

from pages.design import comp, geometry, model as M

FRAME = (1920, 1080)
ROOT = _paths.APP / "pages" / "design"


def design(*layers, **more):
    return M.clean({"size": list(FRAME), "layers": list(layers), **more})


def one(layer):
    return design(layer)["layers"][0]


def rect(**more):
    return {"kind": "rect", "name": "Card", "x": 480, "y": 270, "w": 384, "h": 216, **more}


def path(**more):
    return {"kind": "path", "name": "Hill", "x": 700, "y": 400, "w": 300, "h": 200, "rot": 30, "closed": True,
            "points": [{"x": 0, "y": 1}, {"x": 0.3, "y": 0, "ix": -0.2, "iy": 0.1, "ox": 0.25, "oy": -0.05},
                       {"x": 1, "y": 0.8}], **more}


def star(**more):
    return {"kind": "star", "name": "Star", "x": 900, "y": 500, "w": 200, "h": 260, "rot": 15, "sides": 5, **more}


def mirrored(outline, axis, middle):
    """An outline mirrored point by point, handles too."""
    out = []
    for x, y, ix, iy, ox, oy in outline["points"]:
        if axis == "x":
            out.append((2 * middle - x, y, -ix, iy, -ox, oy))
        else:
            out.append((x, 2 * middle - y, ix, -iy, ox, -oy))
    return {"closed": outline["closed"], "points": out}


class Close(unittest.TestCase):
    def same_outline(self, a, b):
        self.assertEqual(a["closed"], b["closed"])
        self.assertEqual(len(a["points"]), len(b["points"]))
        for p, q in zip(a["points"], b["points"]):
            for u, v in zip(p, q):
                self.assertAlmostEqual(u, v, places=2)


class ModelTests(unittest.TestCase):
    def test_a_flip_is_kept_only_where_it_means_something(self):
        card, plain, line, text, image = design(
            rect(flipx=1), rect(flipx=False, flipy=0), {"kind": "line", "flipx": True, "flipy": True},
            {"kind": "text", "flipy": True}, {"kind": "image", "path": "D:\\Footage\\logo.png", "px": [400, 200], "flipx": True})["layers"]
        self.assertIs(card["flipx"], True)
        self.assertNotIn("flipy", card)
        self.assertFalse({"flipx", "flipy"} & set(plain))           # there only when true
        self.assertFalse({"flipx", "flipy"} & set(line))            # a line is the same mirrored
        self.assertEqual((text["flipy"], image["flipx"]), (True, True))
        group, shape = design({"kind": "group", "flipx": True, "children": [rect(flipy=True)]},
                              {"kind": "boolean", "flipx": True, "children": [rect(), {"kind": "ellipse", "flipx": True}]})["layers"]
        self.assertNotIn("flipx", group)                            # a container has no picture of its own
        self.assertNotIn("flipx", shape)
        self.assertTrue(group["children"][0]["flipy"])
        self.assertTrue(shape["children"][1]["flipx"])

    def test_both_flips_are_kept_as_a_half_turn(self):
        layer = one(rect(rot=30, flipx=True, flipy=True))
        self.assertFalse({"flipx", "flipy"} & set(layer))
        self.assertEqual(layer["rot"], 210.0)
        whole = design(rect(flipx=True), path(flipy=True), rect(rot=300, flipx=1, flipy=1))
        self.assertEqual(M.clean(whole), whole)                     # cleaning again changes nothing


class RuleTests(Close):
    def test_a_flip_mirrors_the_place_turns_the_other_way_and_toggles_the_flip(self):
        layer = one(rect(x=500, y=300, rot=30))
        across = geometry.flipped(layer, "x", 600)
        self.assertEqual((across["x"], across["y"], across["rot"], across["flipx"]), (700.0, 300.0, 330.0, True))
        self.assertNotIn("flipy", across)
        down = geometry.flipped(layer, "y", 200)
        self.assertEqual((down["x"], down["y"], down["rot"], down["flipy"]), (500.0, 100.0, 330.0, True))
        # Across then up and down: turned half round (30 + 180), flipped neither way.
        both = geometry.flipped(across, "y", 300)
        self.assertFalse({"flipx", "flipy"} & set(both))
        self.assertEqual((both["x"], both["y"], both["rot"]), (700.0, 300.0, 210.0))
        line = geometry.flipped(one({"kind": "line", "x": 100, "y": 100, "w": 50, "rot": 20}), "x", 0)
        self.assertEqual((line["x"], line["rot"]), (-100.0, 340.0))
        self.assertFalse({"flipx", "flipy"} & set(line))

    def test_flipping_twice_the_same_way_is_no_flip(self):
        for layer, axes in ((one(rect(rot=30)), "xy"), (one(path()), "xy"), (one({"kind": "text", "x": 10, "y": 20, "rot": 45}), "xy"),
                            (one(star(flipx=True)), "x"), (one(rect(rot=0, flipy=True)), "y")):
            for axis in axes:
                with self.subTest(kind=layer["kind"], axis=axis):
                    twice = geometry.flipped(geometry.flipped(layer, axis, 640), axis, 640)
                    self.assertEqual(M.clean({"layers": [twice]})["layers"][0], layer)
        # One flipped the other way first comes back as the same picture, written the other way round.
        for layer, axis in ((one(path(flipx=True)), "y"), (one(star(flipy=True)), "x")):
            twice = geometry.flipped(geometry.flipped(layer, axis, 0), axis, 0)
            self.assertEqual(round((twice["rot"] - layer["rot"]) % 360), 180)
            self.same_outline(geometry.outline(twice), geometry.outline(layer))

    def test_a_flipped_outline_is_the_outline_mirrored_points_and_handles(self):
        for layer in (one(path()), one(path(flipy=True)), one(star()), one(rect(rot=20, radius=40)),
                      one({"kind": "ellipse", "x": 300, "y": 300, "w": 200, "h": 100, "rot": 70}),
                      one({"kind": "polygon", "x": 300, "y": 300, "w": 200, "h": 100, "rot": 10, "sides": 3})):
            for axis, middle in (("x", 640), ("y", 360), ("x", 0)):
                with self.subTest(kind=layer["kind"], axis=axis, middle=middle):
                    made = M.clean({"layers": [geometry.flipped(layer, axis, middle)]})["layers"][0]
                    self.same_outline(geometry.outline(made), mirrored(geometry.outline(layer), axis, middle))

    def test_a_layers_own_flip_mirrors_its_outline_about_its_middle(self):
        layer = one(path(rot=0))
        self.same_outline(geometry.outline({**layer, "flipx": True}), mirrored(geometry.outline(layer), "x", layer["x"]))
        self.same_outline(geometry.outline({**layer, "flipy": True}), mirrored(geometry.outline(layer), "y", layer["y"]))

    def test_children_flip_about_the_same_line_and_a_booleans_gradient_turns(self):
        group = design({"kind": "group", "children": [rect(x=100, rot=10), path(x=300)]})["layers"][0]
        made = geometry.flipped(group, "x", 200)
        self.assertEqual([(c["x"], c["rot"], c.get("flipx")) for c in made["children"]], [(300.0, 350.0, True), (100.0, 330.0, True)])
        shape = design({"kind": "boolean", "fill": {"type": "linear", "angle": 30},
                        "children": [rect(), {"kind": "ellipse", "x": 600, "y": 300}]})["layers"][0]
        self.assertEqual(geometry.flipped(shape, "x", 0)["fill"]["angle"], 150.0)
        self.assertEqual(geometry.flipped(shape, "y", 0)["fill"]["angle"], 330.0)
        self.assertEqual(geometry.flipped(geometry.flipped(shape, "x", 0), "x", 0)["fill"], shape["fill"])

    def test_a_gradient_runs_mirrored(self):
        box = (100, 100, 200, 100)
        start, end = geometry.gradient_line(box, 0, 0, (-1.0, 1.0))
        self.assertEqual((start, end), ((200.0, 100.0), (0.0, 100.0)))
        # A flipped layer turned by -r draws its gradient as the mirror of the one turned by r.
        (s0, e0), (s1, e1) = geometry.gradient_line(box, 30, 20), geometry.gradient_line(box, -30, 20, (-1.0, 1.0))
        for a, b in ((s0, s1), (e0, e1)):
            self.assertAlmostEqual(2 * 100 - a[0], b[0])
            self.assertAlmostEqual(a[1], b[1])


class CompTests(unittest.TestCase):
    def comp(self, *layers):
        self.text, _gid = comp.comp_text(design(*layers), FRAME, 24, 120)
        return self.text

    def tool(self, name):
        start = self.text.index(f"\t\t{name} = ")
        return self.text[start:self.text.index("\n\t\t}", start)]

    def test_a_flipped_rectangle_is_only_turned_the_other_way(self):
        layer = geometry.flipped(one(rect(rot=20, radius=54)), "x", 480)
        self.comp(layer)
        mask = self.tool("Card1Mask")
        self.assertIn("Card1Mask = RectangleMask {", mask)
        for line in ("Center = Input { Value = { 0.25, 0.75 }, },", "Width = Input { Value = 0.2, },",
                     "CornerRadius = Input { Value = 0.5, },", "Angle = Input { Value = -340, },"):
            self.assertIn(line, mask)

    def test_a_flipped_gradient_runs_the_other_way(self):
        fill = {"type": "linear", "angle": 0, "stops": [{"at": 0, "colour": "#ff0000"}, {"at": 100, "colour": "#0000ff"}]}
        self.comp(rect(fill=fill))
        plain = self.tool("Card1Fill")
        self.comp(rect(fill=fill, flipx=True))
        flipped = self.tool("Card1Fill")
        self.assertIn("Start = Input { Value = { 0.15, 0.75 }, },", plain)
        self.assertIn("Start = Input { Value = { 0.35, 0.75 }, },", flipped)
        self.assertIn("End = Input { Value = { 0.15, 0.75 }, },", flipped)

    def test_a_flipped_star_and_path_are_their_outlines_mirrored(self):
        for layer, name in ((one(star(flipy=True)), "Star1Mask"), (one(path(flipx=True)), "Hill1Mask")):
            with self.subTest(name):
                self.comp(layer)
                mask = self.tool(name)
                self.assertIn("PolylineMask {", mask)
                written = re.search(r"Polyline \{.*\} \}", mask).group(0)
                shape = comp._Comp(design(layer), FRAME, 120, None)
                self.assertEqual(written, str(comp._polyline(shape, geometry.outline(layer))).strip())
                self.comp({**layer, "flipx": False, "flipy": False})
                self.assertNotIn(written, self.tool(name))

    def test_a_flipped_picture_goes_through_a_transform_that_flips_it(self):
        image = {"kind": "image", "name": "Logo", "path": "D:\\Footage\\logo.png", "px": [400, 200], "x": 960, "y": 540, "w": 400}
        self.comp(image)
        self.assertNotIn("Logo1Flip", self.text)
        self.comp({**image, "flipx": True})
        flip = self.tool("Logo1Flip")
        self.assertIn("Logo1Flip = Transform {", flip)
        self.assertIn('Input = Input { SourceOp = "Logo1Image", Source = "Output", },', flip)
        self.assertIn("FlipHoriz = Input { Value = 1, },", flip)
        self.assertNotIn("FlipVert", flip)
        self.assertIn('Foreground = Input { SourceOp = "Logo1Flip", Source = "Output", },', self.tool("Logo1Fill"))
        self.comp({**image, "flipy": True, "rot": 30})
        self.assertIn("FlipVert = Input { Value = 1, },", self.tool("Logo1Flip"))
        self.assertIn("Angle = Input { Value = -30, },", self.tool("Logo1Fill"))
        self.assertNotIn("GroupOperator", self.text)

    def test_flipped_text_is_turned_half_round(self):
        text = {"kind": "text", "name": "Title", "x": 960, "y": 540}
        self.comp(text)
        self.assertNotIn("AngleY", self.tool("Title1Text"))
        self.comp({**text, "flipx": True, "rot": 30})
        made = self.tool("Title1Text")
        self.assertIn("AngleY = Input { Value = 180, },", made)
        self.assertIn("AngleZ = Input { Value = -30, },", made)
        self.assertNotIn("AngleX", made)
        self.comp({**text, "flipy": True})
        self.assertIn("AngleX = Input { Value = 180, },", self.tool("Title1Text"))


class PageTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web" / "design.js").read_text(encoding="utf-8")

    def test_the_arrange_group_has_both_flips_naming_their_keys(self):
        arrange = self.html.split('id="arrange-group"')[1].split("</section>")[0]
        self.assertEqual(re.findall(r'data-flip="(\w)"', arrange), ["x", "y"])
        self.assertIn("(Shift+H)", arrange)
        self.assertIn("(Shift+V)", arrange)

    def test_the_keys_and_the_canvas_follow_the_rule(self):
        for line in ('e.shiftKey && (key === "h" || key === "v")', 'flip(key === "h" ? "x" : "y")',
                     "ctx.scale(...flipOf(l));", "ctx.scale(...flipOf(c));", "measurer.scale(...flipOf(l));",
                     "l.rot = ((-l.rot % 360) + 360) % 360;", "l.rot = (l.rot + 180) % 360;",
                     "const a = axis === \"x\" ? 180 - l.fill.angle : -l.fill.angle;"):
            self.assertIn(line, self.script)
        # The flip keys come before the tool keys (V without Shift is the Select tool).
        self.assertLess(self.script.index('(key === "h" || key === "v") && sel.length'), self.script.index("const tools = {v:"))


if __name__ == "__main__":
    unittest.main()
