"""Design's newer parts where they meet (pages/design/): an auto layout frame inside a
main component (its own box carried into every instance, in the canvas and the comp
alike), an instance never dropped into its own main from the layers list, and flipping
a frame and an instance."""

import json
import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401

from pages.design import comp, model as M

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


def rect(lid, x, y, w=100, h=60):
    return {"id": lid, "kind": "rect", "name": lid, "x": x, "y": y, "w": w, "h": h, "fill": SOLID}


def frame(lid, x, y, w, h, children):
    return {"id": lid, "kind": "frame", "name": lid, "x": x, "y": y, "w": w, "h": h, "radius": 12,
            "fill": {"type": "solid", "colour": "#ffffff"},
            "layout": {"direction": "row", "gap": 10, "padx": 20, "pady": 10, "align": "center", "w": "hug", "h": "hug"},
            "children": children}


def built():
    """A main holding a frame (a button: two boxes in a row), an instance of it twice
    the size elsewhere, and a frame of its own at the top."""
    button = frame("f", 300, 200, 250, 80, [rect("a", 245, 200), rect("b", 355, 200)])
    return M.clean({"size": [1920, 1080], "layers": [
        {"id": "c", "kind": "component", "name": "Button", "box": {"x": 300, "y": 200, "w": 250, "h": 80}, "children": [button]},
        {"id": "i", "kind": "instance", "name": "Big", "of": "c", "x": 1000, "y": 600, "w": 500, "h": 160, "rot": 0},
        frame("g", 400, 800, 250, 80, [rect("d", 345, 800), rect("e", 455, 800)]),
    ]})


class ResolveTests(unittest.TestCase):
    def test_a_frame_in_a_main_is_carried_box_and_all(self):
        design = built()
        made = M.resolve(design["layers"][1], M.mains(design))
        (carried,) = made["children"]
        self.assertEqual(carried["kind"], "frame")
        # The main's box (300, 200, 250 x 80) into the instance's (1000, 600, 500 x 160): twice.
        self.assertEqual((carried["x"], carried["y"], carried["w"], carried["h"]), (1000.0, 600.0, 500.0, 160.0))
        self.assertEqual((carried["radius"], carried["layout"]["gap"], carried["layout"]["padx"]), (24.0, 20.0, 40.0))
        self.assertEqual([(c["x"], c["w"]) for c in carried["children"]], [(890.0, 200.0), (1110.0, 200.0)])

    def test_the_comp_draws_the_frames_background_in_the_instance(self):
        text, _gid = comp.comp_text(built(), (1920, 1080), 24, 120, {})
        start = text.index("\t\tBig5_f1Mask = RectangleMask {")
        mask = text[start:text.index("\n\t\t}", start)]
        self.assertIn("Center = Input { Value = { 0.520833, 0.444444 }, },", mask)
        self.assertIn("Width = Input { Value = 0.260417, },", mask)


class TextShadowTests(unittest.TestCase):
    # Fusion turns and mirrors a Text+ shadow's Offset with the text; the canvas keeps a
    # shadow where it says on screen (measured on Studio 21.1.2), so the comp undoes it.
    def offset(self, **more):
        text = {"id": "t", "kind": "text", "name": "Shade", "x": 960, "y": 540, "text": "Shade", "size": 120,
                "shadow": {"on": True, "dx": 40, "dy": 0, "opacity": 100}, **more}
        written, _gid = comp.comp_text(M.clean({"size": [1920, 1080], "layers": [text]}), (1920, 1080), 24, 120, {})
        line = next(l for l in written.splitlines() if "Offset3" in l)
        return [round(float(v), 4) for v in line.split("{ ")[2].split(" }")[0].split(", ")]

    def test_the_shadow_stays_where_the_canvas_puts_it(self):
        share = 120 / 0.716
        right = round(40 / share, 4)
        self.assertEqual(self.offset(), [right, 0.0])
        self.assertEqual(self.offset(rot=90), [0.0, right])              # turned back a quarter
        self.assertEqual(self.offset(flipx=True), [-right, 0.0])          # mirrored back
        self.assertEqual(self.offset(flipy=True), [right, 0.0])


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
        self.host.tool_settings("design", {})["spec"] = built()
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
        return self.js(f"(function f(list) {{ for (const l of list) {{ if (l.id === {json.dumps(lid)}) return l; "
                       f"const in_ = l.children && f(l.children); if (in_) return in_; }} return null; }})(designView.design().layers)")

    def flip(self, ids):
        self.js(f"designView.pick({json.dumps(ids)})")
        self.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'H', shiftKey: true, bubbles: true}))")
        self.wait(300)

    def test_an_instance_is_never_dropped_into_its_own_main(self):
        self.assertIsNone(self.js("designView.moveLayers(designView.design().layers, ['i'], 'c', 0)"))
        self.assertIsNotNone(self.js("designView.moveLayers(designView.design().layers, ['i'], 'g', 0)"))

    def test_a_frame_flipped_moves_to_its_mirrored_place_and_keeps_its_order(self):
        before = self.layer("g")
        self.js("designView.pick(['g', 'c'])")
        self.flip(["g", "c"])
        after = self.layer("g")
        self.assertNotEqual(after["x"], before["x"])
        self.assertEqual([c["id"] for c in after["children"]], ["d", "e"])
        d, e = after["children"]
        self.assertLess(d["x"], e["x"])                                   # laid out first to last again

    def test_an_instance_flipped_only_moves(self):
        before = self.layer("i")
        self.flip(["i", "g"])
        after = self.layer("i")
        self.assertNotEqual(after["x"], before["x"])
        self.assertEqual((after["rot"], after.get("flipx")), (0, None))
        self.assertTrue(self.page.spec["layers"][1]["kind"] == "instance")

    def test_the_fields_follow_a_shape_while_it_is_dragged(self):
        # The design's frame "g" holds rect "d" (100 x 60): stretch it by its right side
        # and read Width before letting go.
        self.js("designView.pick(['d'])")
        self.wait(200)
        # Where a design px is on screen: the view fitted as paint() fits it, rulers shown.
        point = ("(function (x, y) { const c = document.getElementById('stage'), b = c.getBoundingClientRect();"
                 " const r = document.getElementById('rulers') && !document.getElementById('rulers').hidden ? 20 : 0;"
                 " const k = Math.min((b.width - r - 40) / 1920, (b.height - r - 40) / 1080);"
                 " return [b.left + r + (b.width - r - 1920 * k) / 2 + x * k, b.top + r + (b.height - r - 1080 * k) / 2 + y * k]; })")
        d = self.layer("d")
        x0, y0 = self.js(f"{point}({d['x'] + d['w'] / 2}, {d['y']})")
        x1, _y1 = self.js(f"{point}({d['x'] + d['w'] / 2 + 120}, {d['y']})")
        fire = ("document.getElementById('stage').dispatchEvent(new PointerEvent('{kind}', "
                "{{bubbles: true, button: 0, pointerId: 1, clientX: {x}, clientY: {y}}}))")
        self.js(fire.format(kind="pointerdown", x=x0, y=y0))
        self.js(fire.format(kind="pointermove", x=(x0 + x1) / 2, y=y0))
        self.js(fire.format(kind="pointermove", x=x1, y=y0))
        self.wait(150)
        held = float(self.js("document.querySelector('[data-prop=\"w\"]').value"))
        live = self.layer("d")["w"]
        self.js(fire.format(kind="pointerup", x=x1, y=y0))
        self.assertGreater(live, 200)
        self.assertAlmostEqual(held, live, delta=0.01)


if __name__ == "__main__":
    unittest.main()
