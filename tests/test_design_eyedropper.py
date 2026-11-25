"""Design's eyedropper (the "---- eyedropper --" section of pages/design/web/design.js,
DesignPage.on_screen_dropper): a colour taken from the design as it's drawn - by I, for
the picked layers' fill or stroke, or by the dropper button beside a colour field - and,
through Color Palette's screen dropper, from anywhere on the screen. The screen dropper
here is always a fake: no test reads the real screen or hooks the real mouse."""

import json
import os
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

import _paths  # noqa: F401

from core import i18n

ROOT = _paths.APP / "pages" / "design"
STRINGS = ("Click the design to take its colour. Escape stops.",
           "Click the design to take its colour for the stroke. Escape stops.",
           "Click the design to take its colour. Shift-click gives it to the stroke. Escape stops.",
           "Nothing here", "Take this colour from the canvas", "Anywhere on the screen",
           "Take the colour from anywhere on the screen, outside Buddy too. A right-click stops.",
           "Nothing is drawn there to take a colour from.", "What's picked has no colour like that to change.",
           "Pick a layer first, then press I to take a colour for it.",
           # Color Palette's own, said again here.
           "Screen dropper", "Could not start the screen colour dropper:\n{0}")

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, Qt, QTimer, Signal
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAVE_WEB = True
except ImportError:  # pragma: no cover
    HAVE_WEB = False


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web" / "design.js").read_text(encoding="utf-8")
        self.css = (ROOT / "web" / "design.css").read_text(encoding="utf-8")

    def test_every_colour_has_a_dropper_beside_it(self):
        # Text, fill, stroke and shadow colours are fields of the page; a gradient's
        # colours are drawn by the script - each is given a dropper button.
        self.assertEqual(re.findall(r'class="dg-colour"[^>]*data-prop="([\w.]+)"', self.html),
                         ["colour", "fill.colour", "stroke.colour", "shadow.colour"])
        self.assertIn('document.querySelectorAll("input.dg-colour[data-prop]").forEach(input => swatch(input, () => ({path: input.dataset.prop})));',
                      self.script)
        self.assertIn('swatch(el("input.dg-colour", {type: "color"', self.script)
        self.assertIn("() => ({stop: i})", self.script)
        self.assertIn('Buddy.icon("dropper")', self.script)
        # A colour hidden (the fill's, under a gradient) takes its dropper with it.
        self.assertIn(".ds-swatch:has(> input[hidden]) { display: none; }", self.css)

    def test_i_takes_a_colour_for_the_fill_and_shift_for_the_stroke(self):
        for line in ('e.key.toLowerCase() === "i" && !cmd && !e.altKey', "startDropper({ids: sel.slice(), stroke: e.shiftKey})",
                     "giveColour(aim, hex, e.shiftKey)", "const stroke = !!(shift || aim.stroke);",
                     'if (dropper && e.key === "Escape")', "if (e.button !== 0) return stopDropper();"):
            self.assertIn(line, self.script)

    def test_the_colour_is_the_designs_not_the_squares_under_it(self):
        # Taken from the design drawn alone (no checker), at the stage's own pixels.
        shot = self.script.split("function designShot()")[1].split("\n    }\n")[0]
        self.assertIn("doc.layers.forEach(l => drawLayer(ctx, l, 0));", shot)
        self.assertNotIn("checker", shot)
        self.assertIn("Math.floor(sx * ratio) - half", self.script)
        self.assertIn("if (!(a >= FAINT)) return null;", self.script)

    def test_the_cursor_is_a_dropper_while_it_is_on(self):
        rule = re.search(r"\.ds-stage canvas\.ds-dropping \{([^}]*)\}", self.css).group(1)
        self.assertIn("url(\"data:image/svg+xml,", rule)
        self.assertIn(") 4 18, crosshair;", rule)
        self.assertNotIn("#", rule)              # a # in a data URL ends it

    def test_what_it_says_is_translated(self):
        for text in STRINGS[:10]:
            self.assertIn(json.dumps(text)[1:-1], self.script.replace("\\'", "'"))
        for text in STRINGS:
            for language in ("日本語", "Español", "Deutsch", "Français", "한국어", "中文", "العربية", "Tiếng Việt"):
                with self.subTest(text=text, language=language):
                    self.assertNotEqual(i18n.translate(text, language), text)

    def test_the_screen_dropper_is_color_palettes_and_keeps_no_picture(self):
        page = (ROOT / "page.py").read_text(encoding="utf-8")
        self.assertIn("from pages.color_palette.screen_dropper import ScreenColorDropper", page)
        self.assertIn('Buddy.send("screen_dropper")', self.script)
        for name in ("screen_dropper.py", "mac_dropper_worker.py"):
            source = (_paths.APP / "pages" / "color_palette" / name).read_text(encoding="utf-8")
            self.assertNotRegex(source, r"\.save\(|toDataURL|QBuffer|base64")
        self.assertIn("ended = Signal()",
                      (_paths.APP / "pages" / "color_palette" / "screen_dropper.py").read_text(encoding="utf-8"))

    def test_qtwebengine_has_no_eyedropper_api_to_lean_on(self):
        # Checked on PySide6 6.11.2 (Chromium 140): window.EyeDropper is undefined, even
        # with --enable-blink-features=EyeDropperAPI. The view doesn't reach for it.
        self.assertNotIn("EyeDropper", self.script)


if HAVE_WEB:
    class FakeDropper(QObject):
        """ScreenColorDropper's signals and callbacks, with nothing on the screen."""
        color_sampled = Signal(str)
        failed = Signal(str)
        ended = Signal()
        made = []
        refuse = None

        def __init__(self, hide_parent_callback=None, show_parent_callback=None, parent=None):
            super().__init__(parent)
            if FakeDropper.refuse:
                raise RuntimeError(FakeDropper.refuse)
            self.show_parent = show_parent_callback
            FakeDropper.made.append(self)
            hide_parent_callback()

        def finish(self, hex_color=None, problem=None):
            self.show_parent()
            if problem:
                self.failed.emit(problem)
            if hex_color:
                self.color_sampled.emit(hex_color)
            self.ended.emit()


class Mem(dict):
    def save(self):
        self.saved = dict(self)


class Host:
    controller, connected = None, False

    def __init__(self, spec=None):
        self.buckets = {}
        if spec:
            self.tool_settings("design", {"spec": spec, "replace": True})

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
        self.app = QApplication.instance() or QApplication([])
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": self.home.name, "BUDDY_RESOLVE_READER": "off"})
        patcher.start()
        self.addCleanup(patcher.stop)
        FakeDropper.made, FakeDropper.refuse = [], None
        fake = types.ModuleType("pages.color_palette.screen_dropper")
        fake.ScreenColorDropper = FakeDropper
        modules = mock.patch.dict(sys.modules, {"pages.color_palette.screen_dropper": fake})
        modules.start()
        self.addCleanup(modules.stop)


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class ScreenDropperTests(_PageCase):
    def setUp(self):
        super().setUp()
        from pages.design.page import DesignPage
        self.page = DesignPage(Host())
        self.addCleanup(self.page.deleteLater)
        self.sent = []
        # Only what the dropper says: the page's own view may load while a test waits.
        self.page.emit = lambda name, data=None: (self.sent.append((name, data))
                                                  if name in ("dropped", "dropper_done", "alert") else None)

    def names(self):
        return [name for name, _data in self.sent]

    def test_a_colour_comes_back_then_that_its_over_and_the_window_is_back(self):
        self.page.on_screen_dropper({})
        self.assertEqual(self.page.window().windowOpacity(), 0.0)
        self.page.on_screen_dropper({})                  # one at a time
        self.assertEqual(len(FakeDropper.made), 1)
        FakeDropper.made[0].finish("#a1b2c3")
        wait(50)
        self.assertEqual(self.sent, [("dropped", {"hex": "#A1B2C3"}), ("dropper_done", {})])
        self.assertEqual(self.page.window().windowOpacity(), 1.0)
        self.page.on_screen_dropper({})                  # and it can be used again
        self.assertEqual(len(FakeDropper.made), 2)

    def test_cancelled_it_only_says_its_over(self):
        self.page.on_screen_dropper({})
        FakeDropper.made[0].finish(None)
        wait(50)
        self.assertEqual(self.sent, [("dropper_done", {})])

    def test_anything_but_a_colour_is_passed_over(self):
        self.page.on_screen_dropper({})
        FakeDropper.made[0].finish("url(javascript:1)")
        wait(50)
        self.assertEqual(self.names(), ["dropper_done"])

    def test_a_dropper_that_cant_start_or_fails_is_said(self):
        FakeDropper.refuse = "It needs the 'pynput' package"
        self.page.on_screen_dropper({})
        self.assertEqual(self.names(), ["dropper_done", "alert"])
        self.assertIn("pynput", self.sent[1][1]["text"])
        self.assertEqual(self.page.window().windowOpacity(), 1.0)
        FakeDropper.refuse = None
        self.sent.clear()
        self.page.on_screen_dropper({})
        FakeDropper.made[0].finish(None, problem="The screen dropper couldn't start.")
        wait(50)
        self.assertEqual(self.names(), ["alert", "dropper_done"])

    def test_nothing_starts_while_the_design_is_being_added(self):
        self.page.adding = True
        self.page.on_screen_dropper({})
        self.assertEqual((self.names(), FakeDropper.made), (["dropper_done"], []))


# The design on the canvas: a red rectangle, a blue one at half opacity, an empty one to
# take colours, a text and a gradient.
SPEC = {"size": [1920, 1080], "layers": [
    {"id": "red", "kind": "rect", "x": 400, "y": 300, "w": 300, "h": 200, "fill": {"type": "solid", "colour": "#e0503c"}},
    {"id": "half", "kind": "rect", "x": 1000, "y": 300, "w": 300, "h": 200, "opacity": 50,
     "fill": {"type": "solid", "colour": "#3c82e0"}},
    {"id": "box", "kind": "rect", "x": 1500, "y": 300, "w": 300, "h": 200, "fill": {"type": "none", "colour": "#ffffff"}},
    {"id": "words", "kind": "text", "x": 960, "y": 800, "text": "Tess", "font": "Arial", "style": "Bold", "size": 90,
     "colour": "#ffffff"},
    {"id": "fade", "kind": "rect", "x": 400, "y": 800, "w": 300, "h": 200,
     "fill": {"type": "linear", "colour": "#ffffff", "angle": 0, "stops": [{"at": 0, "colour": "#000000"}, {"at": 100, "colour": "#ffffff"}]}},
]}

DRIVE = """
window.__ed = (() => {
    const cv = document.getElementById("stage");
    const where = (x, y) => {
        const box = cv.getBoundingClientRect(), k = Math.min((box.width - 40) / 1920, (box.height - 40) / 1080);
        return {clientX: box.left + (box.width - 1920 * k) / 2 + x * k, clientY: box.top + (box.height - 1080 * k) / 2 + y * k};
    };
    const ev = (type, x, y, more) => cv.dispatchEvent(new PointerEvent(type, Object.assign({bubbles: true, button: 0, pointerId: 1},
        where(x, y), more || {})));
    return {
        tap(x, y, more) { ev("pointermove", x, y, more); ev("pointerdown", x, y, more); ev("pointerup", x, y, more); },
        key(k, more) { document.dispatchEvent(new KeyboardEvent("keydown", Object.assign({key: k, bubbles: true}, more || {}))); },
        on() { return cv.classList.contains("ds-dropping"); },
    };
})();
"""


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class CanvasTests(_PageCase):
    """The real page, offscreen, driven with synthetic pointer and key events."""

    def setUp(self):
        super().setUp()
        from pages.design.page import DesignPage
        self.page = DesignPage(Host(SPEC))
        self.addCleanup(self.page.deleteLater)
        self.page.resize(1500, 900)
        self.page.show()
        for _ in range(100):
            wait(100)
            if self.js("typeof designView === 'object' && !!designView.design() && document.querySelectorAll('.ds-drop').length === 4"):
                break
        else:
            self.fail("the page didn't load")
        self.assertTrue(self.js(f"(() => {{ {DRIVE}; return !!window.__ed; }})()"))

    def js(self, expression):
        out, loop = [], QEventLoop()
        self.page.view.page().runJavaScript(f"JSON.stringify({expression})", 0, lambda value: (out.append(value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        return json.loads(out[0]) if out and out[0] else None

    def run_js(self, code):
        self.js(f"(() => {{ {code}; return true; }})()")
        wait(150)                                          # the view sends the design; Python has it

    def layer(self, lid):
        return next(l for l in self.page.spec["layers"] if l["id"] == lid)

    def test_i_takes_the_colour_under_the_click_for_the_picked_layers(self):
        self.run_js('designView.pick(["box"]); __ed.key("i")')
        self.assertTrue(self.js("__ed.on()"))
        self.assertTrue(self.js("!document.getElementById('ds-drop-screen').hidden"))
        self.run_js("__ed.tap(400, 300)")
        self.assertFalse(self.js("__ed.on()"))
        fill = self.layer("box")["fill"]
        self.assertEqual((fill["type"], fill["colour"]), ("solid", "#e0503c"))
        # Shift-click: the stroke - a see-through colour taken at full strength, a stroke
        # that wasn't there given a width.
        self.run_js('designView.pick(["box"]); __ed.key("i"); __ed.tap(950, 260, {shiftKey: true})')
        stroke = self.layer("box")["stroke"]
        self.assertEqual(stroke["width"], 4)
        got, want = bytes.fromhex(stroke["colour"][1:]), bytes.fromhex("3c82e0")
        self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(got, want)), stroke["colour"])
        # A text's colour is its fill.
        self.run_js('designView.pick(["words"]); __ed.key("i"); __ed.tap(400, 300)')
        self.assertEqual(self.layer("words")["colour"], "#e0503c")

    def test_where_nothing_is_drawn_nothing_is_taken_and_escape_stops(self):
        before = json.dumps(self.page.spec)
        self.run_js('designView.pick(["box"]); __ed.key("i"); __ed.tap(1700, 950)')
        self.assertEqual(json.dumps(self.page.spec), before)
        self.assertTrue(self.js("__ed.on()"))
        self.run_js('__ed.key("Escape")')
        self.assertFalse(self.js("__ed.on()"))
        self.run_js('designView.pick([]); __ed.key("i")')          # nothing picked: nothing to take it for
        self.assertFalse(self.js("__ed.on()"))
        self.run_js('designView.pick(["box"]); __ed.key("i"); __ed.tap(400, 300, {button: 2})')
        self.assertFalse(self.js("__ed.on()"))
        self.assertEqual(json.dumps(self.page.spec), before)

    def test_a_fields_dropper_gives_the_colour_to_that_field(self):
        self.run_js('designView.pick(["red"]); document.querySelector("#stroke-group .ds-drop").click()')
        self.assertTrue(self.js("__ed.on() && document.querySelector('#stroke-group .ds-drop').getAttribute('aria-pressed') === 'true'"))
        self.run_js("__ed.tap(1000, 300)")
        self.assertNotEqual(self.layer("red")["stroke"]["colour"], "#ffffff")
        self.assertEqual(self.layer("red")["fill"]["colour"], "#e0503c")
        # A gradient's second colour, from its panel.
        self.run_js('designView.pick(["fade"]); document.getElementById("edit-gradient").click(); '
                    'document.querySelectorAll("#stops .ds-drop")[1].click(); __ed.tap(400, 300)')
        self.assertEqual([s["colour"] for s in self.layer("fade")["fill"]["stops"]], ["#000000", "#e0503c"])

    def test_anywhere_on_the_screen_goes_through_python_and_back(self):
        self.run_js('designView.pick(["red"]); document.querySelector("#fill-group .ds-drop").click(); '
                    'document.getElementById("ds-drop-screen").click()')
        wait(200)
        self.assertEqual(len(FakeDropper.made), 1)
        self.assertFalse(self.js("__ed.on()"))
        # While it's out, a click that lands on the page is the dropper's, not the canvas's:
        # the blue rectangle isn't picked by it.
        picked = "[...document.querySelectorAll('.ds-layer')].map(r => r.getAttribute('aria-selected'))"
        before = self.js(picked)
        self.run_js("__ed.tap(1000, 300)")
        self.assertEqual(self.js(picked), before)
        FakeDropper.made[0].finish("#123456")
        wait(300)
        self.assertEqual(self.layer("red")["fill"]["colour"], "#123456")


if __name__ == "__main__":
    unittest.main()
