"""Design's auto layout frames (pages/design/): what model.clean keeps of a frame and its
layout, the comp a frame becomes (its background as a rectangle under its children), and
the layout rule itself - which only the view runs (web/design.js, layOut), since text is
measured there - checked by opening the page offscreen with designs whose places are
worked out by hand below."""

import json
import os
import re
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401

from pages.design import comp, model as M

FRAME = (1920, 1080)
ROOT = _paths.APP / "pages" / "design"


def design(*layers, **more):
    return M.clean({"size": list(FRAME), "layers": list(layers), **more})


def rect(name="Box", w=100, h=100, **more):
    return {"kind": "rect", "name": name, "x": 0, "y": 0, "w": w, "h": h, **more}


def frame(*children, x=0.5, y=0.5, w=1, h=1, **more):
    """A frame whose top left corner is where x - w/2, y - h/2 say (0, 0 by default)."""
    return {"kind": "frame", "name": "Card", "x": x, "y": y, "w": w, "h": h, "children": list(children), **more}


class ModelTests(unittest.TestCase):
    def test_a_frame_has_a_rectangles_look_and_a_layout(self):
        (card,) = design(frame(rect(), x=300, y=200, w=400, h=100, rot=45, radius=12))["layers"]
        self.assertEqual((card["kind"], card["x"], card["y"], card["w"], card["h"]), ("frame", 300.0, 200.0, 400.0, 100.0))
        self.assertEqual(card["rot"], 0.0)                              # a frame is never turned
        self.assertEqual((card["radius"], card["fill"]["type"], card["stroke"]["width"], card["shadow"]["on"]),
                         (12.0, "none", 0.0, False))                  # no fill to start with
        self.assertEqual(card["layout"], M.LAYOUT)
        self.assertNotIn("blur", card)
        self.assertEqual(M.TITLES["frame"], "Auto layout")
        self.assertEqual(design({"kind": "frame"})["layers"][0]["name"], "Auto layout")

    def test_layout_settings_are_cleaned(self):
        (card,) = design(frame(layout={"direction": "column", "gap": -12, "padx": -5, "pady": "24", "align": "end",
                                       "w": "fixed", "h": "stretch"}))["layers"]
        self.assertEqual(card["layout"], {"direction": "column", "gap": -12.0, "padx": 0.0, "pady": 24.0, "align": "end",
                                          "w": "fixed", "h": "hug"})
        (card,) = design(frame(layout={"direction": "diagonal", "align": "middle", "gap": "wide"}))["layers"]
        self.assertEqual((card["layout"]["direction"], card["layout"]["align"], card["layout"]["gap"]), ("row", "center", 16.0))
        (card,) = design(frame(layout="junk"))["layers"]
        self.assertEqual(card["layout"], M.LAYOUT)
        (card,) = design(frame(layout={"direction": "none"}))["layers"]
        self.assertEqual(card["layout"]["direction"], "none")
        self.assertEqual(M.DIRECTIONS, ("row", "column", "none"))

    def test_children_are_kept_where_they_were_sent(self):
        # The view lays a frame out; Python stores what it is sent and never moves anything.
        sent = frame(rect(x=999, y=-40), {"kind": "text", "text": "Go", "x": 5, "y": 7}, x=10, y=20, w=30, h=40,
                     layout={"direction": "row", "gap": 8, "padx": 100, "pady": 100})
        (card,) = design(sent)["layers"]
        self.assertEqual([(c["x"], c["y"]) for c in card["children"]], [(999.0, -40.0), (5.0, 7.0)])
        self.assertEqual((card["x"], card["y"], card["w"], card["h"]), (10.0, 20.0, 30.0, 40.0))
        made = design(sent)
        self.assertEqual(M.clean(made), made)                           # cleaning again changes nothing

    def test_frames_nest_and_go_in_groups_but_never_in_a_boolean(self):
        made = design(frame(rect(), frame(rect(), {"kind": "text"}, name="Button"), {"kind": "group", "children": [rect()]}),
                      {"kind": "group", "children": [frame(rect())]},
                      {"kind": "boolean", "children": [rect(), frame(rect()), {"kind": "ellipse"}]})
        card, group, shape = made["layers"]
        self.assertEqual([c["kind"] for c in card["children"]], ["rect", "frame", "group"])
        self.assertEqual([c["kind"] for c in card["children"][1]["children"]], ["rect", "text"])
        self.assertEqual(group["children"][0]["kind"], "frame")
        self.assertEqual([c["kind"] for c in shape["children"]], ["rect", "ellipse"])
        self.assertEqual(M.count(made), 13)                             # not the frame in the boolean, nor its rect
        ids = [layer["id"] for layer in M.every(made["layers"])]
        self.assertEqual(len(set(ids)), len(ids))

    def test_an_empty_frame_is_kept_and_counts_toward_the_limit(self):
        (card,) = design(frame())["layers"]
        self.assertEqual(card["children"], [])
        made = design(frame(*[rect() for _ in range(M.MAX_LAYERS + 5)]))
        self.assertEqual(M.count(made), M.MAX_LAYERS)
        self.assertEqual(len(made["layers"][0]["children"]), M.MAX_LAYERS - 1)
        self.assertEqual(M.with_new(made, rect()), (None, None))


class CompTests(unittest.TestCase):
    def comp(self, *layers, frame_size=FRAME):
        self.text, _gid = comp.comp_text(design(*layers), frame_size, 24, 120)
        return self.text

    def tool(self, name):
        start = self.text.index(f"\t\t{name} = ")
        return self.text[start:self.text.index("\n\t\t}", start)]

    def names(self):
        return re.findall(r"^\t\t(\w+) = ", self.text, re.M)

    def test_a_frame_is_its_background_under_its_children(self):
        self.comp(frame(rect(name="Dot", x=500, y=300, w=40, h=40), {"kind": "text", "name": "Label", "x": 600, "y": 300},
                        name="Button", x=480, y=270, w=384, h=216, radius=54,
                        fill={"type": "solid", "colour": "#3c82e0"}, stroke={"width": 4}))
        self.assertEqual(self.names(), ["Canvas", "Button1Mask", "Button1Fill", "Button1StrokeMask", "Button1Stroke",
                                        "Button1On1", "Button1On", "Dot2Mask", "Dot2Fill", "Dot2On", "Label3Text", "Label3On",
                                        "MediaOut1"])
        mask = self.tool("Button1Mask")
        self.assertIn("Button1Mask = RectangleMask {", mask)            # written as a rectangle is
        for line in ("Center = Input { Value = { 0.25, 0.75 }, },", "Width = Input { Value = 0.2, },",
                     "Height = Input { Value = 0.2, },", "CornerRadius = Input { Value = 0.5, },"):
            self.assertIn(line, mask)
        self.assertNotIn("Angle", mask)
        self.assertIn("TopLeftBlue = Input { Value = 0.878431, },", self.tool("Button1Fill"))
        # The children go on over it, in order.
        self.assertIn('Background = Input { SourceOp = "Button1On", Source = "Output", },', self.tool("Dot2On"))
        self.assertIn('Background = Input { SourceOp = "Dot2On", Source = "Output", },', self.tool("Label3On"))
        self.assertIn('Input = Input { SourceOp = "Label3On", Source = "Output", },', self.tool("MediaOut1"))
        self.assertIn("Center = Input { Value = { 0.3125, 0.722222 }, },", self.tool("Label3Text"))

    def test_a_frame_with_no_fill_is_only_its_children(self):
        self.comp(frame(rect(name="Dot")), rect(name="After"))
        self.assertEqual(self.names(), ["Canvas", "Dot2Mask", "Dot2Fill", "Dot2On", "After3Mask", "After3Fill", "After3On",
                                        "MediaOut1"])
        self.assertIn('Background = Input { SourceOp = "Canvas", Source = "Output", },', self.tool("Dot2On"))

    def test_a_frames_shadow_is_its_backgrounds(self):
        self.comp(frame(rect(name="Dot"), name="Card", w=200, h=100, fill={"type": "solid"},
                        shadow={"on": True, "dx": 0, "dy": 12, "blur": 24, "opacity": 50}))
        self.assertIn("Card1ShadowMask = RectangleMask {", self.text)
        self.assertIn('Foreground = Input { SourceOp = "Card1Shadow", Source = "Output", },', self.tool("Card1On1"))
        self.assertIn('Background = Input { SourceOp = "Card1On", Source = "Output", },', self.tool("Dot2On"))

    def test_a_see_through_frame_is_put_together_then_faded_on(self):
        self.comp(frame(rect(name="Dot"), name="Card", w=200, h=100, opacity=40, fill={"type": "solid"}), rect(name="After"))
        self.assertIn("Card1Canvas = Background {", self.text)
        self.assertIn('Background = Input { SourceOp = "Card1Canvas", Source = "Output", },', self.tool("Card1On"))
        self.assertNotIn("Blend", self.tool("Card1On"))                 # the background itself isn't faded twice
        self.assertIn('Background = Input { SourceOp = "Card1On", Source = "Output", },', self.tool("Dot2On"))
        fade = self.tool("Card1Fade")
        self.assertIn('Foreground = Input { SourceOp = "Dot2On", Source = "Output", },', fade)
        self.assertIn("Blend = Input { Value = 0.4, },", fade)
        self.assertIn('Background = Input { SourceOp = "Card1Fade", Source = "Output", },', self.tool("After3On"))

    def test_hidden_frames_and_nested_frames(self):
        self.comp(frame(rect(name="Gone"), hidden=True), frame(frame(rect(name="Dot"), name="Button", fill={"type": "solid"}),
                                                               name="Card", fill={"type": "solid"}))
        self.assertEqual(self.names(), ["Canvas", "Card3Mask", "Card3Fill", "Card3On", "Button4Mask", "Button4Fill", "Button4On",
                                        "Dot5Mask", "Dot5Fill", "Dot5On", "MediaOut1"])
        self.assertNotIn("GroupOperator", self.text)


try:
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAVE_WEB = True
except ImportError:  # pragma: no cover - PySide6 without its web engine
    HAVE_WEB = False

FACE = {"Arial|Bold": {"css": {"family": "Arial", "weight": 700, "italic": False}, "em": 0.716, "baseline": 0.375}}


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class LayoutRuleTests(unittest.TestCase):
    """The page is opened on its own, offscreen, and handed designs as Python would; what
    it lays out is read back. Each expected place is worked out by hand in the test."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        from test_audio_assistant import qt_app
        cls.app = qt_app()
        if not isinstance(cls.app, QApplication):     # another file's test made a bare one
            raise unittest.SkipTest("a QCoreApplication was made first; a web view needs a QApplication")

    def setUp(self):
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from shiboken6 import delete
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.view = QWebEngineView()
        self.addCleanup(delete, self.view)
        self.view.resize(1400, 860)
        loop, loaded = QEventLoop(), []
        self.view.loadFinished.connect(lambda ok: (loaded.append(ok), loop.quit()))
        self.view.setUrl(QUrl.fromLocalFile(str(ROOT / "web" / "index.html")))
        self.view.show()
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        self.assertEqual(loaded, [True])
        self.js("window.__sent = []; Buddy.send = (name, data) => window.__sent.push([name, JSON.parse(JSON.stringify(data))]);"
                " return true;")
        self.receive("fonts", {"fonts": {"Arial": ["Bold", "Regular"]}})

    def js(self, body):
        from PySide6.QtCore import QEventLoop, QTimer
        loop, result = QEventLoop(), []
        self.view.page().runJavaScript("JSON.stringify((() => {" + body + "})())", 0,
                                       lambda value: (result.append(value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        self.assertTrue(result, "JavaScript timed out")
        return json.loads(result[0])

    def receive(self, name, payload):
        self.js(f"Buddy.receive({json.dumps(name)}, {json.dumps(payload)}); return true;")

    def show(self, *layers, rev=0):
        """The design as Python would send it; the design the view then holds."""
        spec = design(*layers)
        self.receive("state", {"connected": False, "timeline": "", "problem": "", "frame": list(FRAME), "fps": 24.0,
                               "spec": spec, "rev": rev, "faces": FACE, "images": {}, "designs": [], "design_id": "",
                               "saved": False, "max_layers": M.MAX_LAYERS, "max_stops": M.MAX_STOPS, "replace": True,
                               "adding": False, "can_undo": False, "can_redo": False})
        return self.js("return designView.design();")

    def places(self, layer):
        return [(round(c["x"], 3), round(c["y"], 3)) for c in layer["children"]]

    def box(self, layer):
        return tuple(round(layer[k], 3) for k in ("x", "y", "w", "h"))

    # Three rectangles, 100 wide: 100, 60 and 140 high. The frame's top left corner is
    # (100, 200); 30 px of padding across, 10 down, 20 between each.
    THREE = (rect("A", 100, 100, x=900, y=900), rect("B", 100, 60, x=-50, y=0), rect("C", 100, 140, x=3, y=3))

    def row(self, **layout):
        return frame(*self.THREE, x=150, y=250, w=100, h=100,
                     layout={"direction": "row", "gap": 20, "padx": 30, "pady": 10, **layout})

    def test_a_row_hugs_its_children_with_gaps_and_padding(self):
        (card,) = self.show(self.row(align="center"))["layers"]
        # 100 + 20 + 100 + 20 + 100 across, 30 each side: 400; the tallest, 140, and 10 each side: 160.
        self.assertEqual(self.box(card), (300, 280, 400, 160))
        # Lefts at 130, 250, 370; all in the middle of 210..350 down.
        self.assertEqual(self.places(card), [(180, 280), (300, 280), (420, 280)])

    def test_each_alignment_across_a_row(self):
        (card,) = self.show(self.row(align="start"))["layers"]
        self.assertEqual(self.places(card), [(180, 260), (300, 240), (420, 280)])      # tops at 210
        (card,) = self.show(self.row(align="end"))["layers"]
        self.assertEqual(self.places(card), [(180, 300), (300, 320), (420, 280)])      # bottoms at 350

    def test_a_column_and_its_alignments(self):
        # 50 high each: 100, 60 and 140 wide.
        kids = (rect("A", 100, 50), rect("B", 60, 50), rect("C", 140, 50))
        for align, xs in (("start", (180, 160, 200)), ("center", (200, 200, 200)), ("end", (220, 240, 200))):
            (card,) = self.show(frame(*kids, x=150, y=250, w=100, h=100,
                                      layout={"direction": "column", "gap": 20, "padx": 30, "pady": 10, "align": align}))["layers"]
            # 140 + 60 across: 200; 50 * 3 + 20 * 2 + 20 down: 210.
            self.assertEqual(self.box(card), (200, 305, 200, 210), align)
            self.assertEqual(self.places(card), list(zip(xs, (235, 305, 375))), align)

    def test_a_fixed_frame_keeps_its_size(self):
        (card,) = self.show(frame(*self.THREE, x=400, y=350, w=600, h=300,
                                  layout={"direction": "row", "gap": 20, "padx": 30, "pady": 10, "w": "fixed", "h": "fixed"}))["layers"]
        self.assertEqual(self.box(card), (400, 350, 600, 300))
        self.assertEqual(self.places(card), [(180, 350), (300, 350), (420, 350)])
        # Fixed one way, hugging the other.
        (card,) = self.show(frame(*self.THREE, x=400, y=350, w=600, h=300,
                                  layout={"direction": "row", "gap": 20, "padx": 30, "pady": 10, "w": "fixed"}))["layers"]
        self.assertEqual(self.box(card), (400, 280, 600, 160))

    def test_hidden_children_take_no_room_and_off_places_nothing(self):
        a, b, c = self.THREE
        (card,) = self.show(frame(a, {**b, "hidden": True}, c, x=150, y=250, w=100, h=100,
                                  layout={"direction": "row", "gap": 20, "padx": 30, "pady": 10}))["layers"]
        self.assertEqual(self.box(card), (240, 280, 280, 160))
        self.assertEqual([self.places(card)[0], self.places(card)[2]], [(180, 280), (300, 280)])
        (card,) = self.show(frame(*self.THREE, x=150, y=250, w=100, h=100, layout={"direction": "none"}))["layers"]
        self.assertEqual(self.box(card), (150, 250, 100, 100))
        self.assertEqual(self.places(card), [(900, 900), (-50, 0), (3, 3)])

    def test_a_frame_inside_a_frame(self):
        # A button (two rectangles, 40x40 and 80x20, 10 apart, 20 across and 10 down of
        # padding) under a 200x50 bar in a card whose top left is (0, 0).
        button = frame(rect("Icon", 40, 40), rect("Label", 80, 20), name="Button",
                       layout={"direction": "row", "gap": 10, "padx": 20, "pady": 10})
        card = frame(rect("Bar", 200, 50), button, layout={"direction": "column", "gap": 12, "padx": 16, "pady": 16, "align": "start"})
        (card,) = self.show(card)["layers"]
        # The button: 20 + 40 + 10 + 80 + 20 = 170 by 60. The card: 200 + 32 by 50 + 12 + 60 + 32.
        self.assertEqual(self.box(card), (116, 77, 232, 154))
        bar, button = card["children"]
        self.assertEqual((bar["x"], bar["y"]), (116, 41))
        self.assertEqual(self.box(button), (101, 108, 170, 60))         # its top left at (16, 78)
        self.assertEqual(self.places(button), [(56, 108), (126, 108)])

    def test_a_text_child_is_laid_out_by_the_line_it_draws(self):
        label = {"kind": "text", "text": "Play", "font": "Arial", "style": "Bold", "size": 40, "align": "left", "x": 0, "y": 0}
        (button,) = self.show(frame(label, layout={"direction": "row", "padx": 24, "pady": 12}))["layers"]
        wide = self.js('const c = document.createElement("canvas").getContext("2d"); c.font = \'700 40px "Arial", sans-serif\';'
                       ' return c.measureText("Play").width;')
        for got, want in zip(self.box(button), ((wide + 48) / 2, 32, wide + 48, 64)):
            self.assertAlmostEqual(got, want, places=2)
        # Its line's left side at 24; its box (40 high, its middle (0.375 - 0.25) x 40 below
        # where the text is placed) from 12 down.
        self.assertEqual(self.places(button), [(24, 27)])
        # A longer label: the button grows with it.
        (longer,) = self.show(frame({**label, "text": "Play it again"}, layout={"direction": "row", "padx": 24, "pady": 12}))["layers"]
        self.assertGreater(longer["w"], button["w"] + 100)

    def test_what_is_laid_out_is_what_python_keeps(self):
        (card,) = self.show(self.row())["layers"]
        kept = M.clean({"size": list(FRAME), "layers": [card]})["layers"][0]
        self.assertEqual((self.box(kept), self.places(kept)), (self.box(card), self.places(card)))
        self.assertEqual(self.show(self.row(), rev=1)["layers"][0], card)       # laid out again: the same

    def test_shift_a_wraps_the_picked_layers_in_a_frame(self):
        # Spread across with gaps of 150 and 250: in a row, 200 apart, in their order across.
        self.show(rect("C", 100, 140, x=800, y=480), rect("A", 100, 100, x=200, y=500), rect("B", 100, 60, x=450, y=520))
        self.js('document.dispatchEvent(new KeyboardEvent("keydown", {key: "a", ctrlKey: true, bubbles: true}));'
                'document.dispatchEvent(new KeyboardEvent("keydown", {key: "A", shiftKey: true, bubbles: true})); return true;')
        sent = self.js("return window.__sent.filter(s => s[0] === 'design').pop()[1].design;")
        (card,) = sent["layers"]
        self.assertEqual((card["kind"], card["layout"]["direction"], card["layout"]["gap"], card["fill"]["type"]),
                         ("frame", "row", 200, "none"))
        self.assertEqual([c["name"] for c in card["children"]], ["A", "B", "C"])
        # Its top left where theirs was (150, 410); 700 by 140, each in the middle down.
        self.assertEqual(self.box(card), (500, 480, 700, 140))
        self.assertEqual(self.places(card), [(200, 480), (500, 480), (800, 480)])
        self.assertEqual(M.clean(sent)["layers"][0]["children"][1]["x"], 500.0)


class WiringTests(unittest.TestCase):
    def test_the_view_and_the_model_name_the_same_things(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "design.js").read_text(encoding="utf-8")
        for prop, values in (("layout.direction", M.DIRECTIONS), ("layout.align", M.LAYOUT_ALIGNS),
                             ("layout.w", M.SIZINGS), ("layout.h", M.SIZINGS)):
            part = html.split(f'data-prop="{prop}"')[1].split("</div>")[0]
            self.assertEqual(tuple(re.findall(r'data-value="(\w+)"', part)), values)
        for prop in ("layout.gap", "layout.pad", "layout.padx", "layout.pady"):
            self.assertIn(f'data-prop="{prop}"', html)
        self.assertIn('id="layout-pop"', html)
        self.assertIn("(Shift+A)", html)
        self.assertIn('e.shiftKey && key === "a"', script)
        self.assertIn("// ---- auto-layout --", script)


if __name__ == "__main__":
    unittest.main()
