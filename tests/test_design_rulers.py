"""Design's rulers and guides (pages/design/): the guides a design keeps, cleaned
(model.py) and never put in the comp (comp.py); how the canvas is shown - rulers, guides
shown or locked - kept in the page's settings (page.py); the view wired for them
(web/); and the page itself, offscreen, driven with the mouse and keys."""

import json
import os
import re
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from _fake_graphics import studio

from core import i18n
from core.tools_kb import get_tool
from pages.design import comp, model as M, place, store

FRAME = (1920, 1080)
ROOT = _paths.APP / "pages" / "design"
# What the rulers and guides put on the page, and the toast the view writes.
SAID = ("Rulers", "Show or hide the rulers (Shift+R). Drag from a ruler to make a guide", "Guides",
        "Guides: show, lock or clear them", "Show guides", "Lock guides", "Clear guides", "Hide rulers",
        "Where the guide is, in px. Leave it empty to remove the guide", "Where the guide is",
        "There's no room for more guides that way.")

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, Qt
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
    def test_guides_are_numbers_once_each_in_order(self):
        made = design(rect(), guides={"x": [960, "300", 300.0004, 300, True, None, "wide", float("nan"), [1], -2e9],
                                      "y": "junk", "z": [5]})
        self.assertEqual(made["guides"], {"x": [-M.FAR, 300.0, 960.0], "y": []})
        self.assertEqual(M.clean(made), made)                           # cleaning again changes nothing
        self.assertEqual(design(rect(), guides={"x": [1.23456]})["guides"]["x"], [1.235])

    def test_a_design_without_guides_has_no_guides(self):
        self.assertNotIn("guides", M.clean({}))
        for junk in (None, "lines", [], {"x": [], "y": []}, {"x": ["a"]}, {"x": 5}):
            self.assertNotIn("guides", design(rect(), guides=junk), junk)

    def test_there_are_only_so_many_each_way(self):
        made = design(guides={"x": list(range(100, 0, -1)), "y": [1, 2]})
        self.assertEqual(len(made["guides"]["x"]), M.MAX_GUIDES)
        self.assertEqual(made["guides"]["x"], sorted(made["guides"]["x"]))
        self.assertEqual(made["guides"]["y"], [1.0, 2.0])

    def test_the_comp_never_has_them(self):
        plain = design(rect(), {"kind": "text", "text": "Hi"})
        lined = design(rect(), {"kind": "text", "text": "Hi"}, guides={"x": [100, 960], "y": [540]})
        self.assertIn("guides", lined)
        self.assertEqual(comp.comp_text(lined, FRAME, 24.0, 120), comp.comp_text(plain, FRAME, 24.0, 120))
        self.assertEqual(M.design_id(lined, FRAME, 24.0), M.design_id(plain, FRAME, 24.0))
        text, _gid = comp.comp_text(lined, FRAME, 24.0, 120)
        self.assertNotIn("uide", text)

    def test_a_design_with_guides_is_added_as_one_without(self):
        controller, timeline = studio()
        place.add(controller, design(rect(), guides={"x": [100], "y": [200]}))
        controller2, timeline2 = studio()
        place.add(controller2, design(rect()))
        self.assertEqual(timeline.tracks[1][0].comp.text, timeline2.tracks[1][0].comp.text)

    def test_a_saved_design_keeps_its_guides(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"BUDDY_HOME": home}):
            made = design(rect(), guides={"y": [540, 100]})
            self.assertEqual(store.load(store.save(made))["guides"], {"x": [], "y": [100.0, 540.0]})


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web" / "design.js").read_text(encoding="utf-8")
        self.css = (ROOT / "web" / "design.css").read_text(encoding="utf-8")

    def test_the_rulers_are_drawn_over_the_stage_and_let_the_mouse_through(self):
        # A canvas of their own over the stage's: the stage's sums (at()) stay its own.
        self.assertRegex(self.html, r'<canvas id="stage"[^>]*></canvas><canvas id="rulers"[^>]*aria-hidden="true"')
        self.assertIn('id="ruler-corner"', self.html)
        self.assertIn('id="rulers-toggle" aria-pressed="true"', self.html)
        self.assertRegex(self.css, r"\.ds-stage #rulers \{ pointer-events: none; \}")
        self.assertIn('const box = $("stage").getBoundingClientRect();', self.script)
        self.assertIn("return {x: (sx - view.ox) / view.k, y: (sy - view.oy) / view.k, sx, sy};", self.script)
        # Fitting leaves them their room, and the overlay is the stage's size in device px.
        self.assertIn("view.ox = room + (cw - room - W * view.k) / 2;", self.script)
        self.assertEqual(self.script.count("canvas.width = Math.round(cw * ratio);"), 2)

    def test_the_rulers_code_is_a_section_of_its_own(self):
        self.assertEqual(self.script.count("// ---- rulers --"), 1)
        for line in ('xs.push(...snapGuides("x"));', 'ys.push(...snapGuides("y"));', "resizeHeld(sideToGuide(p, e), e);",
                     "drawHeld(toGuide(p, e), e);", "if (rulerDown(p)) return paint();", 'if (d.type === "guide") return guideDrop(d, e);',
                     "if (guideDouble(at(e))) return;", "rulerState(s);", "bindRulers();"):
            self.assertIn(line, self.script)

    def test_shift_r_is_the_rulers_not_the_rectangle(self):
        self.assertLess(self.script.index("if (rulerKey(e, cmd)) return did();"), self.script.index("const tools = {v:"))
        self.assertIn('e.shiftKey && e.code === "KeyR"', self.script)
        self.assertIn('e.code === "Semicolon"', self.script)

    def test_what_they_say_is_translated(self):
        for text in SAID:
            with self.subTest(text):
                self.assertTrue(f'"{text}"' in self.html or f'"{text}"' in self.script or f">{text}<" in self.html, text)
                self.assertNotEqual(i18n.translate(text, "日本語"), text)

    def test_ask_buddy_is_told(self):
        told = " ".join(get_tool("design").capabilities)
        for word in ("Rulers", "Shift+R", "guide", "Lock guides"):
            self.assertIn(word, told)


class Mem(dict):
    def save(self):
        self.saved = dict(self)


class Host:
    controller, connected = None, False

    def __init__(self):
        self.buckets = {}

    def tool_settings(self, tool_id, defaults=None):
        return self.buckets.setdefault(tool_id, Mem(defaults or {}))

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")

    def ensure_connected(self):
        from core.resolve_bridge import ResolveConnectionError
        raise ResolveConnectionError("Resolve isn't running.")

    def set_busy(self, on, message=""):
        pass


class _PageCase(unittest.TestCase):
    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": self.home.name, "BUDDY_RESOLVE_READER": "off"})
        patcher.start()
        self.addCleanup(patcher.stop)


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class PageTests(_PageCase):
    def setUp(self):
        super().setUp()
        from pages.design.page import DesignPage
        self.host = Host()
        self.page = DesignPage(self.host)
        self.addCleanup(self.page.deleteLater)
        self.sent = []
        self.page.emit = lambda name, data=None: self.sent.append((name, data))

    def last(self, name):
        return next(data for n, data in reversed(self.sent) if n == name)

    def test_how_the_canvas_is_shown_is_kept_in_the_settings(self):
        self.page.web_ready()
        state = self.last("state")
        self.assertEqual((state["rulers"], state["guides_shown"], state["guides_locked"], state["max_guides"]),
                         (True, True, False, M.MAX_GUIDES))
        self.page.on_option({"key": "rulers", "value": False})
        self.page.on_option({"key": "guides_locked", "value": 1})
        state = self.last("state")
        self.assertEqual((state["rulers"], state["guides_locked"]), (False, True))
        self.assertEqual((self.host.buckets["design"].saved["rulers"], self.host.buckets["design"].saved["guides_locked"]),
                         (False, True))
        self.assertEqual(self.page.spec, M.clean({}))                   # not the design's, and no step to undo
        self.assertFalse(self.page.undo_steps)
        self.page.on_option({"key": "replace", "value": False})         # the page's own options still go through
        self.assertFalse(self.last("state")["replace"])
        self.page.on_option({"key": "zoom", "value": True})             # nothing else is set this way
        self.page.on_option(None)
        self.assertNotIn("zoom", self.host.buckets["design"].saved)

    def test_guides_are_part_of_the_design_and_its_history(self):
        self.page.on_design({"design": {"size": list(FRAME), "layers": [rect()], "guides": {"x": [100]}}, "rev": 1})
        self.page.on_design({"design": {"size": list(FRAME), "layers": [rect()], "guides": {"x": [100, 50.5], "y": [9]}}, "rev": 2})
        self.assertEqual(self.last("state")["spec"]["guides"], {"x": [50.5, 100.0], "y": [9.0]})
        self.page.on_undo({"rev": 3})
        self.assertEqual(self.last("state")["spec"]["guides"], {"x": [100.0], "y": []})
        self.page.on_design({"design": {"size": list(FRAME), "layers": [rect()]}, "rev": 4})
        self.assertNotIn("guides", self.page.spec)                      # cleared


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


# Drives the canvas as a person would: guides from the rulers, a shape drawn and moved
# onto them, a guide moved and dragged away, one typed, the keys. At a device pixel
# ratio of 1.5 (the stage's sizing went wrong there once).
DRIVE = r"""(() => {
Object.defineProperty(window, "devicePixelRatio", {value: 1.5, configurable: true});
designView.pick([]);
const cv = document.getElementById("stage"), out = {};
const box = () => cv.getBoundingClientRect();
const fit = () => {
    const b = box(), R = 20;
    const k = Math.min((b.width - R - 40) / 1920, (b.height - R - 40) / 1080);
    return {b, k, ox: R + (b.width - R - 1920 * k) / 2, oy: R + (b.height - R - 1080 * k) / 2};
};
const client = (x, y) => { const f = fit(); return [f.b.left + f.ox + x * f.k, f.b.top + f.oy + y * f.k]; };
const raw = (type, cx, cy, more) => cv.dispatchEvent(new PointerEvent(type, Object.assign({bubbles: true, button: 0, pointerId: 1, clientX: cx, clientY: cy}, more || {})));
const ev = (type, x, y, more) => { const [cx, cy] = client(x, y); raw(type, cx, cy, more); };
const dragTo = (x0, y0, x1, y1) => { ev("pointerdown", x0, y0); ev("pointermove", (x0 + x1) / 2, (y0 + y1) / 2); ev("pointermove", x1, y1); ev("pointerup", x1, y1); };
const guides = () => JSON.parse(JSON.stringify(designView.design().guides || null));
const [cx] = client(960, 0), [, cy] = client(0, 540);
raw("pointerdown", cx, box().top + 10); ev("pointermove", 960, 150); ev("pointermove", 960, 300.4); ev("pointerup", 960, 300.4);
raw("pointerdown", box().left + 10, cy); ev("pointermove", 300, 540); ev("pointermove", 500.3, 540); ev("pointerup", 500.3, 540);
raw("pointerdown", box().left + 10, cy); ev("pointermove", 800, 540); ev("pointermove", 1000, 540); ev("pointerup", 1000, 540);
out.made = guides();
raw("pointerdown", box().left + 10, cy); raw("pointerup", box().left + 10, cy);
out.clicked = guides();
document.querySelector('[data-tool="rect"]').click();
dragTo(503, 303, 800, 600);
let l = designView.design().layers.slice(-1)[0];
out.drawn = [l.x, l.y, l.w, l.h];
dragTo(650, 450, 1147, 450);
l = designView.design().layers.slice(-1)[0];
out.moved = l.x - l.w / 2;
designView.pick([]);
ev("pointerdown", 500, 800); ev("pointermove", 510, 800); ev("pointermove", 520.4, 800); ev("pointerup", 520.4, 800);
out.guideMoved = guides();
ev("pointerdown", 520, 800); ev("pointermove", 300, 800);
{ const [, y] = client(0, 800); raw("pointermove", box().left + 5, y); raw("pointerup", box().left + 5, y); }
out.guideGone = guides();
ev("pointermove", 1000.4, 950);
out.cursor = cv.style.cursor;
{ const [x, y] = client(700, 300); cv.dispatchEvent(new MouseEvent("dblclick", {bubbles: true, clientX: x, clientY: y})); }
const field = document.querySelector(".ds-guide-field");
out.field = field && field.value;
if (field) { field.value = "500/2"; field.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true})); }
out.typed = guides();
document.getElementById("ruler-corner").click();
out.menu = [...document.querySelectorAll(".menu-pop button")].map(b => b.textContent);
Buddy.closeMenu();
document.dispatchEvent(new KeyboardEvent("keydown", {key: "R", code: "KeyR", shiftKey: true, bubbles: true}));
out.tool = document.querySelector('[data-tool="select"]').getAttribute("aria-pressed");
out.corner = document.getElementById("ruler-corner").hidden;
const rc = document.getElementById("rulers");
out.sizes = [cv.width, cv.height, rc.width, rc.height, rc.style.width, cv.style.width];
return JSON.stringify(out);
})()"""


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class LivePageTests(_PageCase):
    def js(self, code):
        out, loop = [], QEventLoop()
        self.page.view.page().runJavaScript(code, 0, lambda value: (out.append(value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        return out[0] if out else None

    def test_guides_are_made_moved_snapped_to_and_typed_with_the_mouse(self):
        from pages.design.page import DesignPage
        host = Host()
        host.tool_settings("design", {})["spec"] = design(rect(x=960, y=900, w=200, h=100))
        self.page = DesignPage(host)
        self.addCleanup(self.page.deleteLater)
        self.page.resize(1500, 900)
        self.page.show()
        for _ in range(150):
            _wait(100)
            if self.js("!!(window.designView && designView.design() && document.getElementById('rulers').width > 300)"):
                break
        else:
            self.fail("the page never drew")
        out = json.loads(self.js(DRIVE))
        self.assertEqual(out["made"], {"x": [500, 1000], "y": [300]})
        self.assertEqual(out["clicked"], out["made"])                   # a click on a ruler makes none
        self.assertEqual([round(v, 3) for v in out["drawn"]], [650, 450, 300, 300])     # drawn from the guides' corner
        self.assertAlmostEqual(out["moved"], 1000, places=3)            # its left side snapped onto a guide
        self.assertEqual(out["guideMoved"], {"x": [520, 1000], "y": [300]})            # on whole px
        self.assertEqual(out["guideGone"], {"x": [1000], "y": [300]})
        self.assertEqual(out["cursor"], "col-resize")
        self.assertEqual((out["field"], out["typed"]), ("300", {"x": [1000], "y": [250]}))
        self.assertEqual(out["menu"], ["Show guides", "Lock guides", "Clear guides", "Hide rulers"])
        self.assertEqual((out["tool"], out["corner"]), ("true", True))  # Shift+R hid the rulers, didn't pick the rectangle
        w, h, rw, rh, shown, stage_shown = out["sizes"]
        self.assertEqual((rw, rh, shown), (w, h, stage_shown))
        self.assertAlmostEqual(w / float(shown[:-2]), 1.5, delta=0.01)
        for _ in range(50):
            _wait(100)
            if self.page.spec.get("guides") == {"x": [1000.0], "y": [250.0]} and host.buckets["design"].get("rulers") is False:
                break
        self.assertEqual(self.page.spec["guides"], {"x": [1000.0], "y": [250.0]})
        self.assertIs(host.buckets["design"]["rulers"], False)
        self.page.on_undo({"rev": 999})
        self.assertEqual(self.page.spec["guides"], {"x": [1000.0], "y": [300.0]})


if __name__ == "__main__":
    unittest.main()
