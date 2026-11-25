"""Design's components (pages/design/): a main component and its instances - how they
are cleaned (model.clean: missing mains, loops, limits), what an instance draws
(model.resolve, the rule the canvas in web/design.js follows too), how one is written
into the Fusion comp (comp.py), detaching one, and deleting a main."""

import math
import re
import unittest

import _paths  # noqa: F401

from core import i18n
from pages.design import comp, model as M

FRAME = (1920, 1080)
ROOT = _paths.APP / "pages" / "design"
SHADOW = {"on": True, "dx": 16, "dy": 24, "blur": 24, "colour": "#ff0000", "opacity": 50}


def design(*layers, **more):
    return M.clean({"size": list(FRAME), "layers": list(layers), **more})


def rect(**more):
    return {"kind": "rect", "name": "Card", "x": 400, "y": 300, "w": 200, "h": 100, **more}


def main(*children, **more):
    """A main component of the children, its box round them: 300..500 across, 250..350 down
    for the one card."""
    return {"kind": "component", "id": "btn", "name": "Button", "children": list(children or [rect()]), **more}


def instance(**more):
    return {"kind": "instance", "name": "Button", "of": "btn", "x": 1200, "y": 600, "w": 200, "h": 100, **more}


def kinds(layers):
    return [layer["kind"] for layer in layers]


class CleaningTests(unittest.TestCase):
    def test_a_main_keeps_its_children_and_a_box(self):
        (made,) = design(main(rect(), {"kind": "ellipse", "x": 600, "y": 300, "w": 100, "h": 100}))["layers"]
        self.assertEqual((made["kind"], made["name"], kinds(made["children"])), ("component", "Button", ["rect", "ellipse"]))
        # No box sent: the bounds of its children.
        self.assertEqual(made["box"], {"x": 475.0, "y": 300.0, "w": 350.0, "h": 100.0})
        (kept,) = design(main(box={"x": 10, "y": "20", "w": 30, "h": 40}))["layers"]
        self.assertEqual(kept["box"], {"x": 10.0, "y": 20.0, "w": 30.0, "h": 40.0})
        for bad in ({"x": 1}, {"x": 0, "y": 0, "w": 0, "h": 5}, "box", {"x": "nan", "y": 0, "w": 9, "h": 9}):
            (made,) = design(main(box=bad))["layers"]
            self.assertEqual(made["box"], {"x": 400.0, "y": 300.0, "w": 200.0, "h": 100.0})
        self.assertNotIn("fill", made)                                  # no look of its own, as a group
        self.assertEqual(design(main(children=[]))["layers"], [])     # nothing in it: nothing

    def test_an_instance_is_a_box_that_names_its_main(self):
        whole = design(main(), instance(rot=-30, opacity=40, linked=0), instance())
        _main, bent, plain = whole["layers"]
        self.assertEqual({key: bent[key] for key in ("of", "x", "y", "w", "h", "rot", "opacity", "linked")},
                         {"of": "btn", "x": 1200.0, "y": 600.0, "w": 200.0, "h": 100.0, "rot": 330.0, "opacity": 40.0,
                          "linked": False})
        self.assertTrue(plain["linked"])                                # a uniform scale unless unlinked
        for key in ("fill", "stroke", "shadow", "blur", "children"):
            self.assertNotIn(key, plain)
        self.assertEqual(M.clean(whole), whole)                         # cleaning again changes nothing

    def test_an_instance_whose_main_isnt_there_is_dropped(self):
        made = design(rect(), instance(), instance(of="nothing"), {"kind": "group", "children": [instance()]})
        self.assertEqual(kinds(made["layers"]), ["rect"])
        # Named by a layer that isn't a main: dropped too.
        self.assertEqual(kinds(design(rect(id="btn"), instance())["layers"]), ["rect"])
        # Inside a group or another main, where the main is: kept.
        made = design(main(), {"kind": "group", "children": [instance(), rect()]})
        self.assertEqual(kinds(made["layers"][1]["children"]), ["instance", "rect"])

    def test_a_main_inside_a_main_becomes_a_group(self):
        inner = {"kind": "component", "id": "inner", "name": "Icon", "children": [rect()]}
        made = design(main({"kind": "group", "children": [inner]}), {**instance(), "of": "inner"})
        self.assertEqual(kinds(made["layers"]), ["component"])           # its instance went with it
        (group,) = made["layers"][0]["children"]
        self.assertEqual((group["kind"], group["children"][0]["kind"]), ("group", "group"))
        self.assertNotIn("box", group["children"][0])
        # Neither shapes a combined shape.
        made = design({"kind": "boolean", "children": [rect(), {"kind": "ellipse"}, main(), instance()]})
        self.assertEqual(kinds(made["layers"][0]["children"]), ["rect", "ellipse"])

    def test_ids_a_main_is_named_by_last(self):
        made = design(main(), instance())
        again = M.clean({**made, "layers": [*made["layers"], instance(id="btn")]})
        self.assertEqual([layer["id"] for layer in again["layers"]][:2], ["btn", made["layers"][1]["id"]])
        self.assertEqual(again["layers"][2]["of"], "btn")


class LoopTests(unittest.TestCase):
    def test_a_main_cant_hold_an_instance_of_itself(self):
        made = design(main(rect(), instance(id="me")))
        self.assertEqual(kinds(made["layers"][0]["children"]), ["rect"])

    def test_nor_through_others(self):
        a = {"kind": "component", "id": "a", "name": "A", "children": [rect(), instance(id="a-has-b", of="b")]}
        b = {"kind": "component", "id": "b", "name": "B", "children": [rect(), instance(id="b-has-c", of="c")]}
        c = {"kind": "component", "id": "c", "name": "C", "children": [rect(), instance(id="c-has-a", of="a")]}
        made = design(a, b, c)
        left = [layer["id"] for layer in M.every(made["layers"]) if layer["kind"] == "instance"]
        self.assertEqual(left, ["a-has-b", "b-has-c"])                  # the one that closed the loop went
        # Nesting without a loop stays: a main of instances of another, twice.
        icon = {"kind": "component", "id": "icon", "name": "Icon", "children": [rect()]}
        made = design(icon, main(instance(id="i1", of="icon"), instance(id="i2", of="icon", x=1500)))
        self.assertEqual(kinds(made["layers"][1]["children"]), ["instance", "instance"])

    def test_a_loop_left_by_a_removed_main_unwinds(self):
        # B's only child is an instance of a main that isn't there: B is empty, so B's instances go too.
        b = {"kind": "component", "id": "b", "name": "B", "children": [instance(of="gone")]}
        self.assertEqual(design(b, instance(of="b"), rect())["layers"][0]["kind"], "rect")


class LimitTests(unittest.TestCase):
    def test_an_instance_is_one_layer_but_draws_its_mains(self):
        made = design(main(rect(), rect(), rect()), instance(), instance())
        self.assertEqual(M.count(made), 6)                              # main + 3, and 1 for each instance
        self.assertEqual(M.drawn(made), 12)                             # each instance draws main + 3
        nested = design({"kind": "component", "id": "icon", "name": "Icon", "children": [rect(), rect()]},
                        main(instance(of="icon"), instance(of="icon")), instance())
        self.assertEqual(M.drawn(nested), 3 + 7 + 7)

    def test_what_is_drawn_has_a_limit_too(self):
        big = main(*[rect() for _ in range(99)])                        # draws 100
        made = design(big, *[instance(id=f"i{n}") for n in range(12)])
        self.assertEqual(M.drawn(made), M.MAX_DRAWN)                    # 9 instances fit beside the main
        self.assertEqual([layer["id"] for layer in made["layers"][1:]], [f"i{n}" for n in range(9)])
        self.assertLess(M.count(made), M.MAX_LAYERS)


class ResolveTests(unittest.TestCase):
    def resolved(self, *children, **inst):
        made = design(main(*children), instance(**inst))
        return M.resolve(made["layers"][1], M.mains(made))

    def test_a_copy_of_the_mains_layers_in_the_instances_place(self):
        group = self.resolved()
        self.assertEqual((group["kind"], group["name"], group["id"]), ("group", "Button", "l3"))
        (card,) = group["children"]
        self.assertEqual((card["x"], card["y"], card["w"], card["h"], card["rot"]), (1200, 600, 200, 100, 0))
        self.assertEqual(card["id"], "l3/l2")                            # its own: "<instance>/<layer in the main>"
        self.assertIsNone(M.resolve({**instance(), "of": "nothing"}, {}))

    def test_places_and_sizes_scale_from_the_mains_box(self):
        # The main's box: 210 x 100 round the card and a dot over its right end, its middle 405 across.
        dot = {"kind": "ellipse", "name": "Dot", "x": 500, "y": 300, "w": 20, "h": 20}
        card, spot = self.resolved(rect(radius=10, stroke={"width": 4}, shadow=SHADOW, blur=6), dot,
                                   w=420, h=200, x=1000, y=500)["children"]
        # Twice as large, about the instance's middle.
        self.assertAlmostEqual(card["w"], 400)
        self.assertAlmostEqual(card["h"], 200)
        self.assertAlmostEqual(card["x"], 1000 - 10)
        self.assertAlmostEqual(spot["x"], 1000 + 190)
        self.assertAlmostEqual(spot["w"], 40)
        # Lengths of the look scale with it, the shadow keeps its way.
        self.assertAlmostEqual(card["radius"], 20)
        self.assertAlmostEqual(card["stroke"]["width"], 8)
        self.assertAlmostEqual(card["blur"], 12)
        self.assertEqual([round(card["shadow"][key]) for key in ("dx", "dy", "blur")], [32, 48, 48])

    def test_a_turned_instance_turns_every_layer_about_its_middle(self):
        off = rect(x=500, w=100)                                        # right of the card
        _card, side = self.resolved(rect(), off, w=250, rot=90)["children"]
        # The box: 300..550 across, its middle 425. 75 px right of it, turned 90 clockwise: 75 px down.
        self.assertAlmostEqual(side["x"], 1200)
        self.assertAlmostEqual(side["y"], 600 + 75)
        self.assertEqual(side["rot"], 90)
        self.assertAlmostEqual(side["w"], 100)

    def test_a_uniform_scale_is_exact_for_a_turned_child(self):
        base = design(main(rect(rot=30)))["layers"][0]["box"]
        made = design(main(rect(rot=30)), instance(w=base["w"] * 1.5, h=base["h"] * 1.5, rot=20))
        (card,) = M.resolve(made["layers"][1], M.mains(made))["children"]
        # (to the 3 decimals a design keeps its places in)
        self.assertAlmostEqual(card["rot"], 50, 3)
        self.assertAlmostEqual(card["w"], 300, 3)
        self.assertAlmostEqual(card["h"], 150, 3)

    def test_a_stretch_is_exact_for_square_turns_and_approximate_otherwise(self):
        # Unlinked, twice as wide only.
        (card,) = self.resolved(w=400, h=100, linked=False)["children"]
        self.assertEqual((card["w"], card["h"], card["rot"]), (400, 100, 0))
        made = design(main(rect(rot=90)), instance(linked=False))       # box 100 x 200
        made["layers"][1].update(w=200, h=200)                           # twice as wide
        (card,) = M.resolve(made["layers"][1], M.mains(made))["children"]
        self.assertAlmostEqual(card["w"], 200)                           # its width runs down: unchanged
        self.assertAlmostEqual(card["h"], 200)                           # its height runs across: doubled
        self.assertAlmostEqual(card["rot"], 90)
        # Turned 45 and stretched 2 x 1: its width's way is where (2 cos 45, sin 45) points, and
        # its area is twice what it was (the slant a stretch would give it is left out).
        made = design(main(rect(rot=45)))
        box = made["layers"][0]["box"]
        made = design(main(rect(rot=45)), instance(w=box["w"] * 2, h=box["h"], linked=False))
        (card,) = M.resolve(made["layers"][1], M.mains(made))["children"]
        self.assertAlmostEqual(card["rot"], math.degrees(math.atan2(1, 2)))
        self.assertAlmostEqual(card["w"], 200 * math.hypot(2 * math.cos(math.pi / 4), math.sin(math.pi / 4)))
        self.assertAlmostEqual(card["w"] * card["h"], 2 * 200 * 100)

    def test_text_images_lines_paths_and_combined_shapes(self):
        text = {"kind": "text", "name": "Label", "x": 400, "y": 300, "size": 40, "text": "OK"}
        picture = {"kind": "image", "name": "Logo", "path": "D:\\Footage\\logo.png", "px": [400, 200], "x": 400, "y": 300, "w": 200}
        line = {"kind": "line", "x": 400, "y": 300, "w": 200, "rot": 90}
        path = {"kind": "path", "x": 400, "y": 300, "w": 200, "h": 100, "closed": True,
                "points": [{"x": 0, "y": 1}, {"x": 0.5, "y": 0}, {"x": 1, "y": 1}]}
        shape = {"kind": "boolean", "fill": {"type": "linear", "angle": 10}, "shadow": SHADOW,
                 "children": [rect(), {"kind": "ellipse", "x": 400, "y": 300}]}
        made = design(main(rect(), text, picture, line, path, shape, box={"x": 400, "y": 300, "w": 200, "h": 100}),
                      instance(w=400, h=200, rot=30))
        _card, label, logo, stroke, hill, combined = M.resolve(made["layers"][1], M.mains(made))["children"]
        self.assertEqual((label["size"], label["rot"], label["x"], label["text"]), (80, 30, 1200, "OK"))
        self.assertEqual((logo["w"], logo["h"]), (400, 200))             # never stretched: its own shape
        self.assertAlmostEqual(stroke["rot"], 120)
        self.assertAlmostEqual(stroke["w"], 400)
        self.assertEqual(hill["points"], made["layers"][0]["children"][4]["points"])      # places in its own box
        self.assertEqual((hill["w"], hill["h"]), (400, 200))
        self.assertEqual(combined["fill"]["angle"], 40)                 # its gradient turns with it
        inst_id = made["layers"][1]["id"]
        self.assertEqual([c["id"] for c in combined["children"]],
                         [f"{inst_id}/{c['id']}" for c in made["layers"][0]["children"][5]["children"]])
        self.assertEqual(combined["shadow"]["dx"], 32)

    def test_a_nested_instance_comes_out_in_its_new_place(self):
        icon = {"kind": "component", "id": "icon", "name": "Icon", "children": [rect(x=100, y=100, w=20, h=20)]}
        made = design(icon, main(rect(), {**instance(), "id": "inner", "of": "icon", "x": 400, "y": 300, "w": 20, "h": 20}),
                      instance(w=400, h=200))
        _card, inner = M.resolve(made["layers"][2], M.mains(made))["children"]
        self.assertEqual((inner["kind"], inner["of"], inner["x"], inner["w"]), ("instance", "icon", 1200, 40))
        (dot,) = M.resolve(inner, M.mains(made))["children"]
        self.assertEqual((dot["x"], dot["y"], dot["w"]), (1200, 600, 40))


class CompTests(unittest.TestCase):
    def comp(self, *layers):
        self.text, _gid = comp.comp_text(design(*layers), FRAME, 24, 120)
        return self.text

    def tool(self, name):
        start = self.text.index(f"\t\t{name} = ")
        return self.text[start:self.text.index("\n\t\t}", start)]

    def names(self):
        return re.findall(r"^\t\t(\w+) = ", self.text, re.M)

    def test_an_instance_is_its_mains_layers_in_its_box(self):
        label = {"kind": "text", "name": "Label", "x": 400, "y": 300, "size": 40}
        self.comp(main(rect(), label), instance(x=960, y=540, w=400, h=200))
        # Flat tools, named after the instance, then each layer and its place in the main.
        self.assertEqual(self.names(), ["Canvas", "Card2Mask", "Card2Fill", "Card2On", "Label3Text", "Label3On",
                                        "Button4_Card1Mask", "Button4_Card1Fill", "Button4_Card1On",
                                        "Button4_Label2Text", "Button4_Label2On", "MediaOut1"])
        mask = self.tool("Button4_Card1Mask")
        for line in ("Center = Input { Value = { 0.5, 0.5 }, },", "Width = Input { Value = 0.208333, },",
                     "Height = Input { Value = 0.185185, },"):
            self.assertIn(line, mask)
        self.assertIn("Center = Input { Value = { 0.5, 0.5 }, },", self.tool("Button4_Label2Text"))
        self.assertIn('Background = Input { SourceOp = "Label3On", Source = "Output", },', self.tool("Button4_Card1On"))
        self.assertNotIn("GroupOperator", self.text)

    def test_a_see_through_or_hidden_instance(self):
        self.comp(main(rect(), hidden=True), instance(opacity=50), instance(name="Gone", hidden=True))
        self.assertNotIn("Card2", self.text)                            # the main hidden: only its instance
        self.assertNotIn("Gone", self.text)
        on = self.tool("Button3On")
        self.assertIn("Blend = Input { Value = 0.5, },", on)
        self.assertIn('Foreground = Input { SourceOp = "Button3_Card1On", Source = "Output", },', on)
        self.assertIn('Background = Input { SourceOp = "Button3Canvas", Source = "Output", },', self.tool("Button3_Card1On"))

    def test_a_turned_instance_turns_its_masks(self):
        self.comp(main(rect(), hidden=True), instance(rot=30, shadow=SHADOW))
        self.assertIn("Angle = Input { Value = -30, },", self.tool("Button3_Card1Mask"))

    def test_nested_instances_are_named_down_the_line(self):
        icon = {"kind": "component", "id": "icon", "name": "Icon", "hidden": True, "children": [rect(name="Dot")]}
        self.comp(icon, main({**instance(), "name": "Icon", "of": "icon", "x": 400, "y": 300}, hidden=True), instance())
        self.assertIn("Button5_Icon1_Dot1Mask", self.names())
        # A name of letters and digits never meets one with an underscore: no clash is possible.
        self.comp(rect(name="Button4_Card1"), main(hidden=True), instance())
        self.assertEqual(len(self.names()), len(set(self.names())))

    def test_detached_it_draws_the_same(self):
        # Detach (in the view) puts resolve()'s group in the instance's place, new ids and all:
        # the comp's inputs are the same, only its tools' names change.
        # (A box of 250 x 200 made half as large again, turned a quarter: values that round alike.)
        made = design(main(rect(stroke={"width": 6}, shadow=SHADOW), {"kind": "star", "x": 450, "y": 320}),
                      instance(rot=90, w=375, h=300))
        group = M.resolve(made["layers"][1], M.mains(made))
        detached = M.clean({**made, "layers": [made["layers"][0], group]})
        self.assertEqual(detached["layers"][1]["kind"], "group")
        values = lambda text: re.findall(r"Value = ([^\n]*), },", text)    # noqa: E731
        with_instance, _gid = comp.comp_text(made, FRAME, 24, 120)
        plain, _gid = comp.comp_text(detached, FRAME, 24, 120)
        self.assertEqual(values(with_instance), values(plain))


class DeletingAMainTests(unittest.TestCase):
    def test_without_its_main_an_instance_goes(self):
        # The view detaches a main's instances before the main goes (after asking); what is
        # sent without it - from elsewhere - loses them instead of keeping empty placeholders.
        made = design(main(), instance(), {"kind": "group", "children": [instance(), rect()]})
        gone = M.clean({**made, "layers": made["layers"][1:]})
        self.assertEqual(kinds(gone["layers"]), ["group"])
        self.assertEqual(kinds(gone["layers"][0]["children"]), ["rect"])
        self.assertEqual(M.instances_of(made, "btn"), [made["layers"][1], made["layers"][2]["children"][0]])

    def test_the_view_asks_then_detaches_first(self):
        script = (ROOT / "web" / "design.js").read_text(encoding="utf-8")
        self.assertIn('if (sure !== true && guardMains(mainsIn(all), all, () => remove(true), "delete")) return;', script)
        self.assertIn("left.forEach(detachOne);", script)
        self.assertIn('title: how === "ungroup" ? "Ungroup this component?" : "Delete this component?"', script)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web" / "design.js").read_text(encoding="utf-8")

    def test_the_canvas_resolves_by_the_models_rule(self):
        for line in ("const sx = inst.w / box.w, sy = inst.h / box.h, k = Math.sqrt(sx * sy);",
                     "const across = Math.hypot(ax, ay), down = sx * sy / across;",
                     "out.rot = round(Math.atan2(ay, ax) * 180 / Math.PI + inst.rot);",
                     "out.h = out.w * l.px[1] / l.px[0];", "if (l.kind === \"text\") out.size = l.size * down;",
                     "if (l.kind === \"boolean\") out.fill.angle = round(l.fill.angle + inst.rot);",
                     "out.id = inst.id + \"/\" + l.id;", "if (l.kind === \"instance\") return drawInstance(to, l, depth);",
                     "if (l.kind === \"group\" || l.kind === \"component\") {"):
            self.assertIn(line, self.script)

    def test_copies_of_a_main_are_instances_and_its_box_is_kept(self):
        for line in ("const made = JSON.parse(JSON.stringify(twinOf(l)));", "settleMains();", "clip = picked().map(keptForPaste);",
                     'if (cmd && e.altKey && (e.code === "KeyK" || e.code === "KeyB")) {'):
            self.assertIn(line, self.script)

    def test_the_buttons_and_the_pop_up(self):
        for part in ('id="make-component" title="Create component (Ctrl+Alt+K)"', 'id="detach" title="Detach instance (Ctrl+Alt+B)"',
                     'id="go-main"', 'id="edit-components"', 'class="dg-pop" id="components-pop"', 'id="components"'):
            self.assertIn(part, self.html)
        self.assertIn('panel("components", "components-pop", "edit-components");', self.script)

    def test_what_it_says_is_translated(self):
        for text in ("Component", "Instance", "Components", "Components…", "Create component", "Create component (Ctrl+Alt+K)",
                     "Go to main component", "Detach instance", "Detach instance (Ctrl+Alt+B)", "Place", "Show",
                     "An instance of it in the middle of the view", "Delete this component?", "Ungroup this component?",
                     "Its instance becomes a plain group, where it is now, looking as it does.",
                     "Its {n} instances become plain groups, where they are now, looking as they do.",
                     "A design draws up to {n} layers, each instance counted as all of its main's layers.",
                     "Pick some layers and click Create component (Ctrl+Alt+K) to make one."):
            with self.subTest(text):
                self.assertTrue(f'"{text}"' in self.script or text in self.html, text)
                self.assertNotEqual(i18n.translate(text, "日本語"), text)
                if "{n}" in text:
                    self.assertIn("{n}", i18n.translate(text, "Deutsch"))

    def test_the_view_is_told_the_limit(self):
        source = (ROOT / "page.py").read_text(encoding="utf-8")
        self.assertIn('"max_drawn": M.MAX_DRAWN', source)


if __name__ == "__main__":
    unittest.main()
