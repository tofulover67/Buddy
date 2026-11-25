"""Design's corners, points and resets (pages/design/): a box's four corners rounded each
its own way ("radii"), polygons and stars rounded, the round-corner dots on the canvas,
Edit points on any shape, Reset (a layer as it was drawn, "born"), Ctrl+Z from a field
and renaming a layer from its row - the model, the outline, the comp and the page."""

import json
import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401

from pages.design import comp, geometry, model as M

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QTimer
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAVE_WEB = True
except ImportError:  # pragma: no cover
    HAVE_WEB = False

SOLID = {"type": "solid", "colour": "#3c82e0"}


def design(*layers):
    return M.clean({"size": [1920, 1080], "layers": list(layers)})


def box(**more):
    return {"id": "r", "kind": "rect", "name": "Box", "x": 600, "y": 400, "w": 400, "h": 300, "fill": SOLID, **more}


class ModelTests(unittest.TestCase):
    def test_four_different_corners_are_kept(self):
        (layer,) = design(box(radius=10, radii=[0, 40, 80, 20]))["layers"]
        self.assertEqual(layer["radii"], [0.0, 40.0, 80.0, 20.0])

    def test_four_alike_are_one_radius(self):
        (layer,) = design(box(radius=10, radii=[30, 30, 30, 30]))["layers"]
        self.assertNotIn("radii", layer)
        self.assertEqual(layer["radius"], 30.0)

    def test_polygons_and_stars_have_a_radius(self):
        layers = design({"kind": "polygon", "w": 200, "h": 200, "radius": 12},
                        {"kind": "star", "w": 200, "h": 200})["layers"]
        self.assertEqual([l["radius"] for l in layers], [12.0, 0.0])

    def test_an_instance_scales_its_mains_corners(self):
        d = design({"id": "c", "kind": "component", "box": {"x": 600, "y": 400, "w": 400, "h": 300},
                    "children": [box(radii=[0, 40, 80, 20])]},
                   {"id": "i", "kind": "instance", "of": "c", "x": 1200, "y": 600, "w": 200, "h": 150})
        (carried,) = M.resolve(d["layers"][1], M.mains(d))["children"]
        self.assertEqual(carried["radii"], [0.0, 20.0, 40.0, 10.0])


class BornTests(unittest.TestCase):
    def test_a_new_layer_is_born_as_it_is(self):
        (layer,) = design(box(radius=12))["layers"]
        self.assertEqual(layer["born"]["kind"], "rect")
        self.assertEqual((layer["born"]["w"], layer["born"]["radius"]), (400.0, 12.0))
        self.assertFalse(set(M.OWN) & set(layer["born"]))

    def test_what_it_was_born_stays_through_changes(self):
        (first,) = design(box())["layers"]
        changed = {**first, "kind": "path", "closed": True, "points": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}],
                   "w": 50, "fill": {"type": "solid", "colour": "#ff0000"}}
        (layer,) = design(changed)["layers"]
        self.assertEqual(layer["kind"], "path")
        self.assertEqual((layer["born"]["kind"], layer["born"]["w"], layer["born"]["fill"]["colour"]), ("rect", 400.0, "#3c82e0"))

    def test_groups_have_no_born_but_their_layers_do(self):
        (group,) = design({"kind": "group", "children": [box()]})["layers"]
        self.assertNotIn("born", group)
        self.assertIn("born", group["children"][0])

    def test_the_comp_is_the_same_with_or_without_it(self):
        with_born = design(box(radius=12))
        without = json.loads(json.dumps(with_born))
        del without["layers"][0]["born"]
        made = [comp.comp_text(d, (1920, 1080), 24, 120, {}) for d in (with_born, without)]
        self.assertEqual(*[text.replace(gid, "") for text, gid in made])


class GeometryTests(unittest.TestCase):
    def test_corners_too_large_for_a_side_shrink_together(self):
        # 100 + 300 along a 200 side: every corner made half as large.
        layer = {"w": 200, "h": 600, "radii": [100, 300, 0, 0]}
        self.assertEqual(geometry.radii_of(layer), [50.0, 150.0, 0.0, 0.0])
        self.assertEqual(geometry.radii_of({"w": 200, "h": 600, "radius": 500}), [100.0] * 4)

    def test_one_radius_draws_as_it_did(self):
        closed, points = geometry._local(box(radius=20, rot=0))
        r, k = 20, 20 * geometry.KAPPA
        self.assertEqual(points[:2], [(-200 + r, -150, -k, 0, 0, 0), (200 - r, -150, 0, 0, k, 0)])
        self.assertEqual(len(points), 8)

    def test_a_corner_not_rounded_is_one_point(self):
        _closed, points = geometry._local(box(radii=[0, 40, 0, 0], rot=0))
        self.assertEqual(points[0], (-200, -150, 0, 0, 0, 0))
        self.assertEqual(len(points), 5)

    def test_a_square_corner_rounded_is_a_quarter_circle(self):
        out = geometry.rounded([(0, 0), (100, 0), (100, 100), (0, 100)], 10)
        k = 10 * geometry.KAPPA
        a, b = out[0], out[1]
        for got, want in zip(a + b, (0, 10, 0, 0, 0, -k, 10, 0, -k, 0, 0, 0)):
            self.assertAlmostEqual(got, want, places=6)

    def test_a_rounding_never_cuts_past_half_a_side(self):
        out = geometry.rounded([(0, 0), (20, 0), (20, 20), (0, 20)], 500)
        self.assertAlmostEqual(out[0][1], 10.0, places=6)


class CompTests(unittest.TestCase):
    def comp(self, *layers):
        text, _gid = comp.comp_text(design(*layers), (1920, 1080), 24, 120, {})
        return text

    def test_corners_alike_stay_a_rectangle_mask(self):
        self.assertIn("RectangleMask", self.comp(box(radius=20)))

    def test_corners_that_differ_are_a_polyline(self):
        text = self.comp(box(radii=[0, 40, 80, 20]))
        self.assertNotIn("RectangleMask", text)
        self.assertIn("PolylineMask", text)

    def test_a_rounded_star_has_twice_the_points(self):
        sharp = self.comp({"kind": "star", "name": "S", "w": 300, "h": 300, "fill": SOLID})
        round_ = self.comp({"kind": "star", "name": "S", "w": 300, "h": 300, "fill": SOLID, "radius": 8})
        self.assertEqual(sharp.count("X = ") * 2, round_.count("X = "))


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.home = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": self.home.name, "BUDDY_RESOLVE_READER": "off"})
        patcher.start()
        self.addCleanup(patcher.stop)
        from test_design import Host
        from pages.design.page import DesignPage
        self.host = Host()
        self.host.tool_settings("design", {})["spec"] = design(
            box(),
            {"id": "s", "kind": "star", "name": "Star", "x": 1300, "y": 400, "w": 300, "h": 260, "rot": 20,
             "radius": 14, "fill": SOLID},
            {"id": "p", "kind": "polygon", "name": "Hex", "x": 1300, "y": 850, "w": 200, "h": 200, "fill": SOLID},
            box(id="q", x=600, y=850, w=300, h=200, rot=30, flipx=True, radii=[0, 50, 10, 30]))
        self.page = DesignPage(self.host)
        self.page.resize(1500, 900)
        self.page.show()
        self.until("!!(window.designView && designView.design() && document.querySelectorAll('.ds-layer').length)")

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait(self, ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def js(self, expression):
        out, loop = [], QEventLoop()
        self.page.view.page().runJavaScript(f"JSON.stringify({expression})", 0, lambda value: (out.append(value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        return json.loads(out[0]) if out and out[0] else None

    def until(self, expression, ms=15000):
        for _ in range(ms // 100):
            if self.js(expression):
                return
            self.wait(100)
        self.fail(f"never true: {expression}")

    def layer(self, lid):
        return self.js(f"designView.design().layers.find(l => l.id === {json.dumps(lid)})")

    # Where a design px is on screen: the view fitted as paint() fits it, rulers shown.
    POINT = ("(function (x, y) { const c = document.getElementById('stage'), b = c.getBoundingClientRect();"
             " const r = document.getElementById('rulers') && !document.getElementById('rulers').hidden ? 20 : 0;"
             " const k = Math.min((b.width - r - 40) / 1920, (b.height - r - 40) / 1080);"
             " return [b.left + r + (b.width - r - 1920 * k) / 2 + x * k, b.top + r + (b.height - r - 1080 * k) / 2 + y * k]; })")

    def drag(self, start, end, alt=False):
        (x0, y0), (x1, y1) = (self.js(f"{self.POINT}({x}, {y})") for x, y in (start, end))
        fire = ("document.getElementById('stage').dispatchEvent(new PointerEvent('{kind}', "
                "{{bubbles: true, button: 0, pointerId: 1, altKey: {alt}, clientX: {x}, clientY: {y}}}))")
        alt = "true" if alt else "false"
        self.js(fire.format(kind="pointerdown", x=x0, y=y0, alt=alt))
        self.js(fire.format(kind="pointermove", x=(x0 + x1) / 2, y=(y0 + y1) / 2, alt=alt))
        self.js(fire.format(kind="pointermove", x=x1, y=y1, alt=alt))
        self.js(fire.format(kind="pointerup", x=x1, y=y1, alt=alt))
        self.wait(300)

    def type_into(self, selector, value):
        self.js(f"(function () {{ const n = document.querySelector({json.dumps(selector)}); n.focus(); n.value = {json.dumps(value)};"
                f" n.dispatchEvent(new Event('change', {{bubbles: true}})); n.blur(); }})()")
        self.wait(300)

    def test_the_canvas_draws_the_outline_the_comp_writes(self):
        for lid in ("r", "s", "p", "q"):
            l = self.layer(lid)
            drawn = self.js(f"designView.shapePoints(designView.design().layers.find(l => l.id === '{lid}'))")
            _closed, points = geometry._local(l)
            self.assertEqual(len(drawn), len(points), lid)
            for got, want in zip(drawn, points):
                for key, value in zip(("x", "y", "ix", "iy", "ox", "oy"), want):
                    self.assertAlmostEqual(got[key], value, places=3, msg=(lid, key))

    def test_a_rectangle_shows_its_corners_and_each_one(self):
        self.js("designView.pick(['r'])")
        self.wait(200)
        shown = self.js("[!document.querySelector('[data-for=\"radius\"]').hidden, !document.getElementById('edit-corners').hidden]")
        self.assertEqual(shown, [True, True])
        self.type_into('[data-prop="radii.1"]', "40")
        self.assertEqual(self.layer("r")["radii"], [0, 40, 0, 0])
        self.assertEqual(self.js("[document.querySelector('[data-prop=\"radius\"]').value, "
                                 "document.querySelector('[data-prop=\"radius\"]').placeholder]"), ["", "Mixed"])
        self.wait(400)
        self.assertEqual(self.page.spec["layers"][0]["radii"], [0.0, 40.0, 0.0, 0.0])
        self.type_into('[data-prop="radius"]', "24")
        l = self.layer("r")
        self.assertEqual((l["radius"], l.get("radii")), (24, None))

    def test_a_polygon_has_corners_but_not_each_one(self):
        self.js("designView.pick(['p'])")
        self.wait(200)
        shown = self.js("[!document.querySelector('[data-for=\"radius\"]').hidden, !document.getElementById('edit-corners').hidden, "
                        "!document.getElementById('edit-points').hidden]")
        self.assertEqual(shown, [True, False, True])
        self.type_into('[data-prop="radius"]', "20")
        self.assertEqual(self.layer("p")["radius"], 20)

    def test_dragging_a_corner_dot_rounds_all_four(self):
        self.js("designView.pick(['r'])")
        self.wait(200)
        dot = next(d for d in self.js("designView.cornerDots()") if d["corner"] == 0)      # top left
        self.drag((dot["x"], dot["y"]), (dot["x"] + 30, dot["y"] + 30))
        l = self.layer("r")
        self.assertNotIn("radii", l)
        self.assertAlmostEqual(l["radius"], 30, delta=2)              # from 0, by how far it went in

    def test_alt_dragging_a_corner_dot_rounds_that_one(self):
        self.js("designView.pick(['r'])")
        self.wait(200)
        dot = next(d for d in self.js("designView.cornerDots()") if d["corner"] == 2)      # bottom right
        self.drag((dot["x"], dot["y"]), (dot["x"] - 30, dot["y"] - 30), alt=True)
        radii = self.layer("r")["radii"]
        self.assertEqual(radii[:2] + radii[3:], [0, 0, 0])
        self.assertAlmostEqual(radii[2], 30, delta=2)

    def test_a_flipped_boxs_dot_is_its_own_pictures_corner(self):
        self.js("designView.pick(['q'])")
        self.wait(200)
        dots = self.js("designView.cornerDots()")
        # Flipped across, the picture's top right (50) is drawn at the box's top left.
        self.assertEqual([d["corner"] for d in dots], [1, 0, 3, 2])

    def test_edit_points_makes_a_rectangle_a_path_of_the_same_outline(self):
        before = self.layer("q")
        want = geometry.outline(before)["points"]
        self.js("designView.pick(['q'])")
        self.wait(200)
        self.js("document.getElementById('edit-points').click()")
        self.wait(400)
        after = self.layer("q")
        self.assertEqual(after["kind"], "path")
        self.assertTrue(after["closed"])
        got = geometry.outline(after)["points"]
        self.assertEqual(len(got), len(want))
        for g, w in zip(got, want):
            for a, b in zip(g, w):
                self.assertAlmostEqual(a, b, places=2)
        self.until("designView && designView.design().layers.length === 4")
        self.assertEqual(self.page.spec["layers"][3]["kind"], "path")

    def test_reset_puts_a_shape_back_as_it_was_drawn_where_it_is_now(self):
        self.js("designView.pick(['q'])")
        self.wait(200)
        self.js("document.getElementById('edit-points').click()")            # now a path...
        self.wait(300)
        self.js("(function () { const l = designView.design().layers.find(l => l.id === 'q');"
                " l.x = 900; l.w = 90; l.fill.colour = '#ff0000'; })()")
        self.js("(function () { const n = document.querySelector('[data-prop=\"opacity\"]'); n.value = '40';"
                " n.dispatchEvent(new Event('input', {bubbles: true})); })()")
        self.until("designView.design().layers.find(l => l.id === 'q').opacity === 40")
        self.assertFalse(self.js("document.getElementById('reset-layer').hidden"))
        self.js("document.getElementById('reset-layer').click()")
        self.wait(400)
        l = self.layer("q")
        self.assertEqual((l["kind"], l["w"], l["radii"], l["fill"]["colour"], l["opacity"], l["flipx"]),
                         ("rect", 300, [0, 50, 10, 30], "#3c82e0", 100, True))
        self.assertEqual((l["x"], l["name"]), (900, "Box"))                    # where it is, called what it is
        self.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'z', ctrlKey: true, bubbles: true}))")
        self.wait(400)
        self.assertEqual(self.layer("q")["kind"], "path")                      # one step to undo

    def test_ctrl_z_in_a_field_nothing_was_typed_in_is_the_designs_undo(self):
        self.js("designView.pick(['r'])")
        self.wait(200)
        self.type_into('[data-prop="radius"]', "24")
        self.assertEqual(self.layer("r")["radius"], 24)
        self.js("document.querySelector('[data-prop=\"x\"]').focus()")
        self.js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'z', ctrlKey: true, bubbles: true}))")
        self.wait(400)
        self.assertEqual(self.layer("r")["radius"], 0)
        self.assertNotEqual(self.js("document.activeElement.dataset.prop || ''"), "x")

    def test_ctrl_z_while_typing_is_left_to_the_field(self):
        self.js("designView.pick(['r'])")
        self.wait(200)
        self.js("(function () { const n = document.querySelector('[data-prop=\"x\"]'); n.focus(); n.value = '61'; })()")
        self.js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'z', ctrlKey: true, bubbles: true}))")
        self.wait(300)
        self.assertEqual(self.js("document.activeElement.dataset.prop || ''"), "x")
        self.assertEqual(self.page.undo_steps, [])

    def name_of_row(self, lid):
        return f"[...document.querySelectorAll('.ds-layer[data-id]')].find(r => r.dataset.id === '{lid}')"

    def test_two_clicks_on_a_layers_row_rename_it(self):
        # Each click as its own (detail 1), the list redrawn between them as it is.
        for _ in range(2):
            self.js(f"{self.name_of_row('s')}.querySelector('.ds-layer-name').dispatchEvent("
                    "new MouseEvent('click', {bubbles: true, detail: 1}))")
            self.wait(60)
        self.assertTrue(self.js(f"!!{self.name_of_row('s')}.querySelector('input.field')"))
        self.js("(function () { const f = document.querySelector('#layers input.field'); f.value = 'Shiny';"
                " f.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); })()")
        self.wait(400)
        self.assertEqual(self.layer("s")["name"], "Shiny")
        self.assertEqual(self.page.spec["layers"][1]["name"], "Shiny")

    def test_clicks_far_apart_only_pick(self):
        click = (f"{self.name_of_row('p')}.querySelector('.ds-layer-name').dispatchEvent("
                 "new MouseEvent('click', {bubbles: true, detail: 1}))")
        self.js(click)
        self.wait(700)
        self.js(click)
        self.wait(100)
        self.assertFalse(self.js("!!document.querySelector('#layers input.field')"))

    def test_f2_renames_the_picked_layer_and_escape_keeps_its_name(self):
        self.js("designView.pick(['r'])")
        self.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'F2', bubbles: true}))")
        self.wait(100)
        self.assertTrue(self.js(f"!!{self.name_of_row('r')}.querySelector('input.field')"))
        self.js("(function () { const f = document.querySelector('#layers input.field'); f.value = 'Nope';"
                " f.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); })()")
        self.wait(300)
        self.assertEqual(self.layer("r")["name"], "Box")

    def test_a_rounded_star_is_drawn_with_curves(self):
        drawn = self.js("designView.shapePoints(designView.design().layers.find(l => l.id === 's'))")
        self.assertEqual(len(drawn), 20)
        self.assertTrue(all(p["ix"] or p["iy"] or p["ox"] or p["oy"] for p in drawn))


if __name__ == "__main__":
    unittest.main()
