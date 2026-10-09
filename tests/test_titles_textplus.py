"""Animation > Titles: a composed title written as a Fusion comp (titles/textplus_comp.py)
- one Text+ per word, merged into MediaOut1 - with the mapping onto Text+'s inputs
measured on Studio 21.1 (Size and Center from canvas_math, outline width, shadow
offset, gradient, rotation)."""

import math
import os
import re
import unittest

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

W, H = 1920, 1080


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TextPlusCompTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from pages.text_animator import canvas_math
        from pages.text_animator.titles import composer, looks, measure, textplus_comp
        cls.cm, cls.C, cls.L, cls.M, cls.T = canvas_math, composer, looks, measure, textplus_comp

    @classmethod
    def tearDownClass(cls):
        cls.M.unload_bundled()

    def layout(self, text="3 secrets nobody tells you", look="hormozi", frame=(W, H)):
        return self.C.compose(self.C.new_doc(text, look), frame, self.M.measure, self.M.metrics, ink=self.M.ink)

    def tool(self, text, name):
        """The Inputs block of one tool in the comp text, as {input: raw value}."""
        body = text[text.index(f"\t\t{name} = "):]
        body = body[:body.index("ViewInfo")]
        return {m.group(1): m.group(2).strip().rstrip(",").strip()
                for m in re.finditer(r"(\w+) = Input \{ (?:Value = )?(.*?), \}", body)}

    def test_one_text_plus_per_word_merged_in_order(self):
        layout = self.layout()
        text = self.T.comp_text(layout, "t-1", 96)
        n = len(layout["boxes"])
        self.assertEqual(len(re.findall(r"= TextPlus \{", text)), n)
        self.assertEqual(len(re.findall(r"= Merge \{", text)), n - 1)
        self.assertIn(f'Input = Input {{ SourceOp = "Merge{n - 1}"', text)
        self.assertIn('BuddyTitle = "t-1"', text)
        self.assertIn("RenderRange = { 0, 95 }", text)
        self.assertEqual(text.count("{"), text.count("}"))
        for i, box in enumerate(layout["boxes"], 1):
            self.assertEqual(self.tool(text, f"Word{i}")["StyledText"], f'"{box["text"]}"')

    def test_a_single_word_goes_straight_to_media_out(self):
        text = self.T.comp_text(self.layout("STOP"), "t-2", 48)
        self.assertNotIn("Merge", text)
        self.assertIn('Input = Input { SourceOp = "Word1"', text)

    def test_size_and_center_put_the_word_where_the_box_is(self):
        layout = self.layout()
        for box in layout["boxes"]:
            size = self.T.text_plus_size(box, W)
            self.assertAlmostEqual(size * self.cm._px_per_size(*box["font"]) * W, box["size"], places=6)
            cx, cy = self.T.text_plus_center(box, (W, H))
            self.assertAlmostEqual(cx * W, box["x"] + box["w"] / 2, places=6)
            base = (1 - cy) * H + self.cm.baseline(*box["font"]) * box["size"]
            self.assertAlmostEqual(base, box["baseline"], places=6)

    def test_the_outline_reaches_as_far_as_the_preview_draws_it(self):
        layout = self.layout()
        text = self.T.comp_text(layout, "t-3", 96)
        box = layout["boxes"][0]
        inputs = self.tool(text, "Word1")
        size = self.T.text_plus_size(box, W)
        reach = self.T.OUTLINE_REACH * float(inputs["Thickness2"]) * size * W
        self.assertAlmostEqual(reach, 0.08 * box["size"], places=3)     # the look's stroke: 0.08 of the letter
        self.assertEqual((inputs["Enabled2"], inputs["OutsideOnly2"]), ("1", "1"))
        self.assertEqual((inputs["Red2"], inputs["Green2"], inputs["Blue2"]), ("0", "0", "0"))

    def test_role_colours(self):
        layout = self.layout()
        text = self.T.comp_text(layout, "t-4", 96)
        for i, box in enumerate(layout["boxes"], 1):
            red = float(self.tool(text, f"Word{i}")["Red1"])
            green = float(self.tool(text, f"Word{i}")["Green1"])
            want = {"key": (1.0, 0xD9 / 255), "accent": (0x39 / 255, 1.0), "normal": (1.0, 1.0)}[box["role"]]
            self.assertAlmostEqual(red, want[0], places=4)
            self.assertAlmostEqual(green, want[1], places=4)

    def test_the_script_accent_gradient_turned_and_shadowed_words(self):
        layout = self.layout(look="script")
        text = self.T.comp_text(layout, "t-5", 96)
        accent = next(i for i, b in enumerate(layout["boxes"], 1) if b["role"] == "accent")
        inputs = self.tool(text, f"Word{accent}")
        self.assertEqual(inputs["Type1"], "2")
        self.assertEqual(inputs["ShadingMappingLevel1"], "3")              # per word
        self.assertEqual(float(inputs["ShadingMappingAngle1"]), 270.0)     # the preview's 90, mirrored
        self.assertIn("Gradient { Colors = { [0] = {", text)
        self.assertEqual(float(inputs["AngleZ"]), 7.0)                     # the look's -7, counter-clockwise
        shadowed = self.tool(text, "Word1")
        x, y = (float(v) for v in shadowed["Offset3"].strip("{} ").split(","))
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertLess(y, 0.0)                                           # straight down, in Fusion's y-up
        box = layout["boxes"][0]
        size = self.T.text_plus_size(box, W)
        self.assertAlmostEqual(-y * size * W, 0.05 * box["size"], places=3)

    def test_an_animated_title_keys_each_word_from_the_clock(self):
        from pages.text_animator.titles import timing
        doc = self.C.new_doc("3 secrets nobody tells you", "hormozi")
        doc["anim"] = "slam"
        doc["exit"] = {"on": True, "style": "slide", "order": "same"}
        layout = self.C.compose(doc, (W, H), self.M.measure, self.M.metrics, ink=self.M.ink)
        clock = timing.timing(doc, layout, 2.5)
        text = self.T.comp_text(layout, "t-6", 60, clock=clock, fps=24)
        n = len(layout["boxes"])
        self.assertEqual(text.count("{"), text.count("}"))
        self.assertIn("Canvas = Background {", text)
        self.assertEqual(len(re.findall(r"Merge\d+ = Merge \{", text)), n)       # every word onto the canvas
        self.assertEqual(len(re.findall(r"Move\d+ = Transform \{", text)), n)
        self.assertEqual(len(re.findall(r"Move\d+Path = XYPath \{", text)), n)   # the slide out
        self.assertEqual(len(re.findall(r"Blur\d+ = Blur \{", text)), n)        # the slam's blur
        self.assertIn('Blend = Input { SourceOp = "Merge1Blend", Source = "Value", }', text)
        # The opacity keys are the clock's, frame for frame.
        want = timing.keys(clock["words"][0], 24, 60)["alpha"]
        block = text[text.index("Merge1Blend = BezierSpline"):]
        block = block[:block.index("		},")]
        got = [(int(f), float(v)) for f, v in re.findall(r"\[(\d+)\] = \{ ([-\d.e]+)", block)]
        self.assertEqual([f for f, _v in got], [f for f, _v in want])
        for (_f, v), (_g, w) in zip(got, want):
            self.assertAlmostEqual(v, w, places=5)
        # The Transform scales about the word's own centre.
        box = layout["boxes"][0]
        pivot = re.search(r"Move1 = Transform \{.*?Pivot = Input \{ Value = \{ ([\d.]+), ([\d.]+) \}", text, re.S)
        self.assertAlmostEqual(float(pivot.group(1)), (box["x"] + box["w"] / 2) / W, places=5)
        self.assertAlmostEqual(float(pivot.group(2)), 1 - (box["y"] + box["h"] / 2) / H, places=5)

    def test_a_still_title_has_no_animation_tools(self):
        text = self.T.comp_text(self.layout(), "t-7", 60)
        for tool in ("Canvas", "Transform", "XYPath", "BezierSpline", "Blur {"):
            self.assertNotIn(tool, text)

    def test_the_new_looks_mechanisms(self):
        # Hollow words and centre strokes: fill off, an outline reaching half its width each way.
        layout = self.layout("Stop wasting money on gear", look="outline_fill")
        text = self.T.comp_text(layout, "t-8", 60)
        plain = next(i for i, b in enumerate(layout["boxes"], 1) if b["role"] != "key")
        inputs = self.tool(text, f"Word{plain}")
        box = layout["boxes"][plain - 1]
        self.assertEqual((inputs["Enabled1"], inputs["OutsideOnly2"]), ("0", "0"))
        size = self.T.text_plus_size(box, W)
        reach = self.T.OUTLINE_REACH * float(inputs["Thickness2"]) * size * W
        self.assertAlmostEqual(reach, 0.045 / 2 * box["size"], places=3)
        # Letter spacing: CharacterSpacing from the measured (s - 1) x Size x width per gap.
        layout = self.layout("Stop wasting money on gear", look="title_bar")
        text = self.T.comp_text(layout, "t-9", 60)
        i, box = next((i, b) for i, b in enumerate(layout["boxes"], 1) if b["track"])
        spacing = float(self.tool(text, f"Word{i}")["CharacterSpacing"])
        self.assertAlmostEqual((spacing - 1) * self.T.text_plus_size(box, W) * W, box["track"], places=3)
        # A bar: a masked Background on the canvas, under the words.
        self.assertIn("Canvas = Background {", text)
        self.assertIn("Bar1Mask = RectangleMask {", text)
        self.assertLess(text.index("Bar1On = Merge {"), text.index("Merge1 = Merge {"))
        # A glow: element 3 with no offset.
        glow = self.tool(self.T.comp_text(self.layout(look="neon_extrude"), "t-10", 60), "Word1")
        self.assertEqual((glow["Enabled3"], glow["Offset3"]), ("1", "{ 0, 0 }"))

    def test_copies_ride_on_the_words_own_canvas(self):
        layout = self.layout("This changes everything", look="echo")
        text = self.T.comp_text(layout, "t-11", 60)
        key = next(i for i, b in enumerate(layout["boxes"], 1) if b["role"] == "key")
        self.assertIn(f"Word{key}Copy0 = TextPlus {{", text)
        copies = re.findall(rf"Word{key}Copy0_\d+[ab] = Merge \{{", text)
        self.assertEqual(len(copies), 4)                     # 2 copies a side
        self.assertIn(f'Foreground = Input {{ SourceOp = "Word{key}", Source = "Output", }}', text)
        self.assertEqual(text.count("{"), text.count("}"))
        copy = self.tool(text, f"Word{key}Copy0")
        self.assertEqual(copy["Enabled1"], "0")              # outline-only copies
        self.assertEqual(copy["StyledText"], self.tool(text, f"Word{key}")["StyledText"])

    def test_copies_fan_out_keyed_frame_by_frame(self):
        from pages.text_animator.titles import timing
        doc = self.C.new_doc("You need to STOP doing this", "echo")
        doc["exit"] = {"on": True, "style": "reverse", "order": "same"}
        layout = self.C.compose(doc, (W, H), self.M.measure, self.M.metrics, ink=self.M.ink)
        clock = timing.timing(doc, layout, 3.0)
        text = self.T.comp_text(layout, "t-12", 90, clock=clock, fps=30)
        self.assertEqual(text.count("{"), text.count("}"))
        key = next(i for i, b in enumerate(layout["boxes"], 1) if b["role"] == "key")
        self.assertEqual(len(re.findall(rf"Word{key}Copy0_\d[ab]Path = XYPath \{{", text)), 4)
        merge = self.tool(text, f"Word{key}Copy0_2a")
        self.assertIn(f'SourceOp = "Word{key}Copy0_2aPath"', merge["Center"])
        self.assertIn(f'SourceOp = "Word{key}Copy0_2aBlend"', merge["Blend"])
        # Its offset is the preview's on every key: none behind the word, all of it out.
        group = self.L.get("echo")["roles"]["key"]["copies"][0]
        dy = group["step"][1] * layout["boxes"][key - 1]["size"]
        block = text[text.index(f"Word{key}Copy0_2aPathY = BezierSpline"):]
        block = block[:block.index("\t\t},")]
        spread = dict(timing.keys(clock["words"][key - 1], 30, 90)["spread"])
        for f, v in re.findall(r"\[(\d+)\] = \{ ([-\d.e]+)", block):
            out, _shown = timing.copy_at(group, 2, spread[int(f)])
            self.assertAlmostEqual(float(v), 0.5 - 2 * dy * out / H, places=5)
        # With the pulse, the same copy is keyed through the hold as well.
        doc["pulse"] = True
        clock = timing.timing(doc, layout, 3.0)
        word = clock["words"][key - 1]
        text = self.T.comp_text(layout, "t-14", 90, clock=clock, fps=30)
        block = text[text.index(f"Word{key}Copy0_2aBlend = BezierSpline"):]
        block = block[:block.index("\t\t},")]
        a = group["alpha"] * group["fade"]
        for f, v in re.findall(r"\[(\d+)\] = \{ ([-\d.e]+)", block):
            st = timing.state(word, int(f) / 30)
            self.assertAlmostEqual(float(v), a * timing.copy_at(group, 2, st["spread"], st["pulse"])[1], places=5)
        held = [int(f) for f, _v in re.findall(r"\[(\d+)\] = \{ ([-\d.e]+)", block)]
        self.assertTrue(any(word["pulse"]["start"] * 30 < f < (word["pulse"]["start"] + word["pulse"]["len"]) * 30
                            for f in held))
        # Still: the copies sit at their places, no keys.
        still = self.T.comp_text(layout, "t-13", 90)
        self.assertNotIn("XYPath", still)

    def test_a_text_value_is_written_as_it_is_and_always_quoted(self):
        self.assertIn('"café"', self.T._input("StyledText", "café"))
        self.assertNotIn("\\u", self.T._input("StyledText", "café"))
        brace = self.T._input("StyledText", "{not a table}")
        self.assertIn('Value = "{not a table}"', brace)

    def test_only_a_gradient_is_written_raw(self):
        raw = self.T._input("Gradient", self.T._gradient({"stops": [(0, "#ff0000"), (1, "#0000ff")]}))
        self.assertIn("Value = Gradient { Colors =", raw)
        self.assertNotIn('"', raw)

    def test_colours_parse(self):
        self.assertEqual(self.T._rgba("#ff0000"), (1.0, 0.0, 0.0, 1.0))
        self.assertAlmostEqual(self.T._rgba("rgba(0,0,0,.55)")[3], 0.55)
        self.assertAlmostEqual(self.T._rgba("#00000080")[3], 128 / 255)
        self.assertEqual(self.T._rgba("bad", "#ffffff"), (1.0, 1.0, 1.0, 1.0))
        self.assertTrue(math.isclose(self.T._rgba("#39FF14")[1], 1.0))


if __name__ == "__main__":
    unittest.main()
