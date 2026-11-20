"""Data Graphics > Graphs, without Qt or Resolve: the rows pasted or typed, the
layout and the bars' growth (graphs/model.py), the comp (graphs/comp.py) and the job
that puts it on a timeline (place.bars, on a fake Resolve)."""

import re
import unittest

import _paths  # noqa: F401
from _fake_graphics import studio

from core.fusion_place import PlaceError
from pages.data_graphics import easing, parts, place
from pages.data_graphics.graphs import comp, model as M

FRAME = (1920, 1080)


class RowsTests(unittest.TestCase):
    def test_pasted_from_a_spreadsheet(self):
        rows, skipped, over = M.parse_rows("Mon\t12\nTue\t1,234\n\nWed\t7.5\n")
        self.assertEqual(rows, [{"label": "Mon", "value": 12.0}, {"label": "Tue", "value": 1234.0},
                                {"label": "Wed", "value": 7.5}])
        self.assertEqual((skipped, over), ([], 0))

    def test_typed_with_commas_semicolons_or_spaces(self):
        rows, skipped, _over = M.parse_rows("Pears, 1,234\nKiwi; 3,5\nPlums 7\n45\nNew York, 12")
        self.assertEqual(rows, [{"label": "Pears", "value": 1234.0}, {"label": "Kiwi", "value": 3.5},
                                {"label": "Plums", "value": 7.0}, {"label": "", "value": 45.0},
                                {"label": "New York", "value": 12.0}])
        self.assertEqual(skipped, [])

    def test_money_and_percent_are_numbers(self):
        rows, _skipped, _over = M.parse_rows("Rent\t$1,200\nShare\t45%")
        self.assertEqual([r["value"] for r in rows], [1200.0, 45.0])

    def test_lines_without_a_number_are_said_not_guessed(self):
        rows, skipped, _over = M.parse_rows("Day\tSales\nMon\t12\nLoss\t-4")
        self.assertEqual(rows, [{"label": "Mon", "value": 12.0}])
        self.assertEqual(skipped, ["Day\tSales", "Loss\t-4"])

    def test_more_rows_than_fit_are_counted(self):
        rows, _skipped, over = M.parse_rows("\n".join(f"R{i}\t{i}" for i in range(15)))
        self.assertEqual((len(rows), over), (M.MAX_ROWS, 3))

    def test_extra_columns_take_the_last_number(self):
        rows, _skipped, _over = M.parse_rows("North\tQ1\t120\t150")
        self.assertEqual(rows, [{"label": "North Q1 120", "value": 150.0}])


class ModelTests(unittest.TestCase):
    def test_anything_cleans_to_a_whole_graph(self):
        self.assertEqual(set(M.clean(None)), set(M.DEFAULTS))
        self.assertEqual(M.clean(None)["rows"], M.SAMPLE_ROWS)
        spec = M.clean({"rows": [{"label": " A ", "value": "5"}, {"value": "x"}, "junk", {"label": "B", "value": -2}],
                        "seconds": 999, "colours": "rainbow", "bar_colour": "blue", "form": "clock", "title": " Hi "})
        self.assertEqual(spec["rows"], [{"label": "A", "value": 5.0}, {"label": "B", "value": 0.0}])
        self.assertEqual((spec["seconds"], spec["colours"], spec["bar_colour"]), (M.MAX_SECONDS, "many", M.PALETTE[0]))
        self.assertEqual(spec["title"], "Hi")
        self.assertNotIn("form", spec)
        self.assertEqual(M.clean(spec), spec)
        self.assertEqual(M.clean({"rows": []})["rows"], [])

    def test_the_tallest_bar_fills_the_room_and_the_rest_are_in_proportion(self):
        plan = M.layout(M.clean({"rows": [{"label": "A", "value": 10}, {"label": "B", "value": 5},
                                          {"label": "C", "value": 0}]}), FRAME)
        a, b, c = plan["bars"]
        self.assertAlmostEqual(b["h"], a["h"] / 2)
        self.assertEqual(c["h"], 0.0)
        self.assertGreater(a["base"] - a["h"], 0.12 * 1080)             # below the top margin
        self.assertEqual({bar["base"] for bar in plan["bars"]}, {plan["axis"]["y"]})
        self.assertLess(a["cx"], b["cx"])
        self.assertAlmostEqual(b["cx"], 960.0)
        self.assertEqual([bar["shown"] for bar in plan["bars"]], ["10", "5", "0"])

    def test_all_zero_draws_no_bars_and_doesnt_divide_by_zero(self):
        plan = M.layout(M.clean({"rows": [{"label": "A", "value": 0}]}), FRAME)
        self.assertEqual(plan["bars"][0]["h"], 0.0)
        self.assertEqual(M.layout(M.clean({"rows": []}), FRAME)["bars"], [])

    def test_a_title_takes_room_from_the_bars(self):
        rows = [{"label": "A", "value": 10}]
        plain = M.layout(M.clean({"rows": rows}), FRAME)
        titled = M.layout(M.clean({"rows": rows, "title": "Sales"}), FRAME)
        self.assertIsNone(plain["title"])
        self.assertEqual(titled["title"]["text"], "Sales")
        self.assertLess(titled["bars"][0]["h"], plain["bars"][0]["h"])

    def test_without_an_axis_nothing_about_the_graph_changes(self):
        plan = M.layout(M.clean({}), FRAME)
        self.assertEqual((plan["marks"], plan["tick"], plan["grid"]), ([], None, False))
        self.assertEqual((plan["subtitle"], plan["x_title"], plan["y_title"]), (None, None, None))
        self.assertEqual(plan["axis"]["x0"], 192.0)

    def test_the_scale_takes_round_steps_up_to_the_tallest_bar(self):
        plan = M.layout(M.clean({"scale": True}), FRAME)                     # 8..24, about four steps
        self.assertEqual([m["text"] for m in plan["marks"]], ["0", "10", "20", "30"])
        self.assertEqual(plan["marks"][0]["y"], plan["axis"]["y"])
        thu = plan["bars"][3]
        room = plan["axis"]["y"] - plan["marks"][-1]["y"]
        self.assertAlmostEqual(thu["h"], room * 24 / 30)                     # the bars are read off the scale
        self.assertGreater(plan["axis"]["x0"], 192.0)                        # room made for the numbers
        self.assertLess(plan["tick"]["x"], plan["axis"]["x0"])
        self.assertFalse(plan["grid"])
        big = M.layout(M.clean({"scale": True, "prefix": "$", "rows": [{"label": "A", "value": 1250}]}), FRAME)
        self.assertEqual([m["text"] for m in big["marks"]], ["$0", "$500", "$1,000", "$1,500"])
        small = M.layout(M.clean({"grid": True, "rows": [{"label": "A", "value": 0.37}, {"label": "B", "value": 0.9}]}), FRAME)
        self.assertEqual([m["text"] for m in small["marks"]], ["0.0", "0.5", "1.0"])    # a decimal where the step needs it
        self.assertTrue(small["grid"])
        self.assertIsNone(small["tick"])
        none = M.layout(M.clean({"scale": True, "rows": [{"label": "A", "value": 0}]}), FRAME)
        self.assertEqual(none["bars"][0]["h"], 0.0)

    def test_a_top_typed_is_the_top_and_split_into_the_steps_asked(self):
        plan = M.layout(M.clean({"scale": True, "top": "30", "steps": 5}), FRAME)
        self.assertEqual([m["text"] for m in plan["marks"]], ["0", "6", "12", "18", "24", "30"])
        alone = M.layout(M.clean({"top": 48}), FRAME)                        # no numbers shown: the bars still obey it
        self.assertAlmostEqual(alone["bars"][3]["h"], M.layout(M.clean({}), FRAME)["bars"][3]["h"] / 2)
        low = M.clean({"scale": True, "top": 5})                            # below the tallest bar: worked out instead
        self.assertEqual(M.layout(low, FRAME)["marks"][-1]["text"], "30")
        for typed, kept in (("", ""), ("abc", ""), (-4, ""), (0, ""), ("1,500", 1500.0)):
            self.assertEqual(M.clean({"top": typed})["top"], kept)
        self.assertEqual((M.clean({"steps": 99})["steps"], M.clean({"steps": "x"})["steps"]), (M.MAX_STEPS, 4))

    def test_titles_along_the_axes_and_a_subtitle_take_their_room(self):
        plain = M.layout(M.clean({"title": "Sales"}), FRAME)
        plan = M.layout(M.clean({"title": "Sales", "subtitle": "This week", "x_title": "Day", "y_title": "Units"}), FRAME)
        self.assertGreater(plan["subtitle"]["baseline"], plan["title"]["baseline"])
        self.assertLess(plan["subtitle"]["size"], plan["title"]["size"])
        self.assertGreater(plan["x_title"]["baseline"], plan["label"]["baseline"])
        self.assertLess(plan["x_title"]["baseline"], 1080)
        self.assertLess(plan["y_title"]["x"], plan["axis"]["x0"])
        self.assertLess(plan["bars"][3]["h"], plain["bars"][3]["h"])
        self.assertAlmostEqual(plan["title"]["x"], (plan["axis"]["x0"] + plan["axis"]["x1"]) / 2)

    def test_labels_and_values_can_be_left_out(self):
        plan = M.layout(M.clean({"labels": False, "values": False}), FRAME)
        self.assertIsNone(plan["label"])
        self.assertIsNone(plan["value"])

    def test_colours_a_colour_each_or_one(self):
        self.assertEqual(M.colours(M.clean({}))[:2], list(M.PALETTE[:2]))
        self.assertEqual(set(M.colours(M.clean({"colours": "one", "bar_colour": "#112233"}))), {"#112233"})
        rows = [{"label": "A", "value": 1, "colour": "#AABBCC"}, {"label": "B", "value": 2, "colour": "pink"},
                {"label": "C", "value": 3}]
        picked = M.clean({"rows": rows})
        self.assertEqual(picked["rows"][0]["colour"], "#aabbcc")                 # one bar's own, picked
        self.assertNotIn("colour", picked["rows"][1])
        self.assertEqual(M.colours(picked), ["#aabbcc", M.PALETTE[1], M.PALETTE[2]])
        self.assertEqual(M.colours(M.clean({"rows": rows, "colours": "one", "bar_colour": "#112233"})),
                         ["#aabbcc", "#112233", "#112233"])
        self.assertEqual(M.layout(picked, FRAME)["bars"][0]["colour"], "#aabbcc")
        many = M.colours(M.clean({"rows": [{"label": str(i), "value": i} for i in range(10)]}))
        self.assertEqual(many[8], M.PALETTE[0])                          # round again

    def test_one_after_another_every_bar_is_whole_by_the_builds_last_frame(self):
        spec = M.clean({"seconds": 2, "hold": 1, "stagger": True})
        grown = M.growth(spec, 24)
        self.assertEqual(len(grown), 5)
        self.assertEqual({len(bar) for bar in grown}, {M.frames(spec, 24)})
        build = M.build_frames(spec, 24)
        self.assertEqual([bar[build - 1] for bar in grown], [1.0] * 5)
        self.assertEqual([bar[0] for bar in grown], [0.0] * 5)
        # The first is under way before the last has started.
        self.assertGreater(grown[0][10], 0.0)
        self.assertEqual(grown[4][10], 0.0)
        self.assertEqual(set(grown[2][build:]), {1.0})

    def test_together_they_grow_as_one(self):
        grown = M.growth(M.clean({"stagger": False}), 24)
        self.assertEqual(grown[0], grown[4])

    def test_a_bar_of_its_own_timing_plays_it_and_the_rest_follow_the_graph(self):
        rows = [{"label": "A", "value": 1}, {"label": "B", "value": 1}]
        plain = M.clean({"rows": rows, "stagger": False, "seconds": 1, "hold": 0})
        self.assertEqual([(t["start"], t["length"], t["own"]) for t in M.timings(plain)], [(0.0, 1.0, False)] * 2)
        own = M.with_part(plain, "bar:1", "start", 0.5)                      # dragged in the dock: half a second in
        own = M.with_part(own, "bar:1", "easing", "linear")
        self.assertEqual(own["rows"][1]["timing"], {"start": 0.5, "length": 0.5, "easing": "linear",
                                                    "curve": [0.3333, 0.3333, 0.6667, 0.6667]})
        a, b = M.timings(own)
        self.assertEqual((a["own"], b["own"], b["start"], b["length"]), (False, True, 0.5, 0.5))   # kept within the build
        grown_a, grown_b = M.growth(own, 24)
        self.assertEqual(grown_a, M.growth(plain, 24)[0])
        self.assertEqual(grown_b[11], 0.0)                                   # not started at half way
        self.assertAlmostEqual(grown_b[17], (17 / 23 - 0.5) / 0.5, places=4)  # then steady
        self.assertEqual(grown_b[-1], 1.0)
        back = M.with_part(own, "bar:1", "reset", None)
        self.assertNotIn("timing", back["rows"][1])
        self.assertEqual(M.growth(back, 24), M.growth(plain, 24))
    def test_a_parts_own_timing_cleans_like_the_rest(self):
        own = parts.clean_timing({"easing": "custom", "curve": [0.1, 2, 0.9, -2], "start": -4, "length": 999})
        self.assertEqual((own["start"], own["length"], own["curve"]), (0.0, parts.MAX_LENGTH, [0.1, 1.5, 0.9, -0.5]))
        self.assertNotIn("kind", own)
        self.assertEqual(parts.clean_timing({"kind": "explode"}, parts.KINDS)["kind"], "none")
        self.assertEqual(parts.clean_timing({"kind": "rise"}, ("none", "fade"))["kind"], "none")
        self.assertIsNone(parts.clean_timing("fast"))
        spec = M.clean({"rows": [{"label": "A", "value": 1, "timing": None, "name_in": {"kind": "pop"},
                                  "value_in": {"kind": "rise", "start": 1}}],
                        "arrivals": {"title": {"kind": "fade"}, "grid": {"kind": "pop"}, "nonsense": {"kind": "fade"}}})
        row = spec["rows"][0]
        self.assertNotIn("timing", row)
        self.assertEqual((row["name_in"]["kind"], row["value_in"]["kind"], row["value_in"]["start"]), ("pop", "fade", 1.0))
        self.assertEqual({k: v["kind"] for k, v in spec["arrivals"].items()}, {"title": "fade", "grid": "none"})
        self.assertEqual(M.clean(spec), spec)

    def test_what_arrives_is_already_there_until_its_given_a_way_to(self):
        spec = M.clean({"title": "Sales", "scale": True})
        arriving = M.arrivals(spec)
        self.assertEqual(sorted(arriving), sorted(["title", "scale"] + [f"name:{i}" for i in range(5)]
                                                  + [f"value:{i}" for i in range(5)]))
        self.assertEqual({arriving[k]["kind"] for k in ("title", "scale", "name:0")}, {"none"})
        self.assertEqual({v["own"] for v in arriving.values()}, {False})
        # A bar's number fades in by itself as its bar starts.
        bars = M.timings(spec)
        self.assertEqual([(arriving[f"value:{i}"]["kind"], arriving[f"value:{i}"]["start"]) for i in range(5)],
                         [("fade", bar["start"]) for bar in bars])
        self.assertEqual(parts.states(arriving["title"], 24, 96), [])
        late = parts.states(arriving["value:4"], 24, 96)
        self.assertEqual(late[int(bars[4]["start"] * 24) - 1][0], 0.0)
        self.assertEqual(late[-1], (1.0, 0.0, 1.0))
        gone = M.arrivals(M.clean({"labels": False, "values": False}))
        self.assertEqual(gone, {})

    def test_the_docks_lanes_are_every_part_in_reading_order(self):
        spec = M.clean({"title": "Sales", "subtitle": "Week", "x_title": "Day", "y_title": "Units", "scale": True,
                        "grid": True, "rows": [{"label": "A", "value": 1}, {"label": "", "value": 2}]})
        lanes = M.lanes(spec)
        self.assertEqual([lane["id"] for lane in lanes],
                         ["title", "subtitle", "y_title", "x_title", "scale", "grid",
                          "bar:0", "name:0", "value:0", "bar:1", "value:1"])        # a row with no name has no name lane
        by = {lane["id"]: lane for lane in lanes}
        self.assertEqual((by["title"]["label"], by["title"]["plain"], by["title"]["kinds"]), ("Title", False, list(parts.KINDS)))
        self.assertEqual(by["grid"]["kinds"], ["none", "fade"])
        self.assertEqual((by["bar:0"]["label"], by["bar:0"]["plain"], by["bar:0"]["kind"], by["bar:0"]["kinds"]),
                         ("A", True, None, None))
        self.assertEqual((by["bar:1"]["label"], by["name:0"]["sub"], by["value:1"]["sub"]), ("2", "name", "number"))
        self.assertEqual(by["value:0"]["kinds"], ["fade"])

    def test_a_part_changed_in_the_dock_starts_from_what_it_was_playing(self):
        spec = M.clean({"title": "Sales"})
        popped = M.with_part(spec, "title", "kind", "pop")
        self.assertEqual(popped["arrivals"]["title"], {"kind": "pop", "start": 0.0, "length": parts.ARRIVES_IN,
                                                       "easing": easing.DEFAULT, "curve": easing.named(easing.DEFAULT)})
        later = M.with_part(popped, "title", "start", "1,5")
        self.assertEqual((later["arrivals"]["title"]["start"], later["arrivals"]["title"]["kind"]), (1.5, "pop"))
        shaped = M.with_part(later, "title", "curve", [0.2, 1.4, 0.6, 0.9])
        self.assertEqual(shaped["arrivals"]["title"]["easing"], "custom")
        number = M.with_part(spec, "value:2", "length", 1)                  # from the fade it had by itself
        self.assertEqual(number["rows"][2]["value_in"], {"kind": "fade", "start": M.timings(spec)[2]["start"], "length": 1.0,
                                                         "easing": "linear", "curve": easing.named("linear")})
        name = M.with_part(spec, "name:0", "kind", "rise")
        self.assertEqual(name["rows"][0]["name_in"]["kind"], "rise")
        self.assertEqual(M.with_part(shaped, "title", "reset", None)["arrivals"], {})
        for bad in (("subtitle", "kind", "fade"),            # the graph has no subtitle
                    ("bar:9", "start", 1), ("bar:x", "start", 1), ("bar:1", "kind", "fade"),
                    ("title", "colour", "#fff"), ("lasers", "start", 1)):
            self.assertIsNone(M.with_part(spec, *bad))
    def test_the_value_counts_up_with_its_bar(self):
        spec = M.clean({"rows": [{"label": "A", "value": 200}], "prefix": "$", "seconds": 1, "hold": 0})
        texts = M.value_texts(spec, M.growth(spec, 24))[0]
        self.assertEqual((texts[0], texts[-1]), ("$0", "$200"))


class CompTests(unittest.TestCase):
    def setUp(self):
        self.spec = M.clean({"title": "Sales", "seconds": 2, "hold": 1})
        self.frames = M.frames(self.spec, 24)
        self.text, self.gid = comp.comp_text(self.spec, FRAME, 24, self.frames)

    def test_a_bar_is_a_masked_background_per_row(self):
        self.assertEqual(self.text.count("= RectangleMask {"), 6)       # five bars and the floor line
        self.assertEqual(len(re.findall(r"\tBar\d = Background \{", self.text)), 5)
        self.assertIn('EffectMask = Input { SourceOp = "Bar3Mask", Source = "Mask", },', self.text)
        self.assertIn('Height = Input { SourceOp = "Bar3Height", Source = "Value", },', self.text)
        self.assertIn('Center = Input { SourceOp = "Bar3Path", Source = "Value", },', self.text)
        self.assertIn(f'{comp.TAG} = "{self.gid}"', self.text)

    def test_labels_values_and_title_are_text_plus(self):
        self.assertEqual(self.text.count("= TextPlus {"), 5 + 5 + 1)
        self.assertIn('StyledText = Input { Value = "Thu", },', self.text)
        self.assertIn('StyledText = Input { Value = "Sales", },', self.text)
        self.assertIn('StyledText = Input { SourceOp = "Value4Text", Source = "Value", },', self.text)
        self.assertIn('Value = Text { Value = "24" }', self.text)

    def test_a_bar_grows_from_the_floor(self):
        plan = M.layout(self.spec, FRAME)
        block = self.text[self.text.index("\t\tBar4Height = BezierSpline"):self.text.index("\t\tBar4PathX")]
        keys = [(int(f), float(v)) for f, v in re.findall(r"\[(\d+)\] = \{ ([\d.e-]+),", block)]
        self.assertEqual(keys[0], (0, 0.0))
        self.assertEqual(keys[-1][0], self.frames - 1)
        self.assertAlmostEqual(keys[-1][1], plan["bars"][3]["h"] / 1080, places=5)
        ys = self.text[self.text.index("\t\tBar4PathY = BezierSpline"):self.text.index("\t\tBar4Path = XYPath")]
        last = float(re.findall(r"\[\d+\] = \{ ([\d.e-]+),", ys)[-1])
        bar = plan["bars"][3]
        self.assertAlmostEqual(last, 1 - (bar["base"] - bar["h"] / 2) / 1080, places=5)

    def test_everything_is_merged_in_order_into_media_out(self):
        merges = re.findall(r"\t\t(\w+) = Merge \{\n\t\t\tInputs = \{\n\t\t\t\tBackground = Input \{ SourceOp = \"(\w+)\"",
                            self.text)
        self.assertEqual(merges[0], ("FloorOn", "Canvas"))
        for (_name, background), (before, _b) in zip(merges[1:], merges):
            self.assertEqual(background, before)
        self.assertIn(f'Input = Input {{ SourceOp = "{merges[-1][0]}", Source = "Output", }},', self.text)

    def test_what_isnt_asked_for_isnt_drawn(self):
        spec = M.clean({"labels": False, "values": False, "rounded": True})
        text, _gid = comp.comp_text(spec, FRAME, 24, 48)
        self.assertNotIn("TextPlus", text)
        self.assertEqual(text.count(f"CornerRadius = Input {{ Value = {M.CORNER}, }},"), 5)
        self.assertNotIn("CornerRadius", self.text)

    def test_every_part_can_arrive(self):
        for word in ("TitleArrive", "Label1Arrive", "GridBlend", "MarksArrive"):
            self.assertNotIn(word, self.text)
        spec = M.clean({**self.spec, "scale": True, "grid": True})
        for part, kind in (("title", "pop"), ("name:0", "rise"), ("scale", "fade"), ("grid", "fade")):
            spec = M.with_part(spec, part, "kind", kind)
        text, _gid = comp.comp_text(spec, FRAME, 24, self.frames)
        self.assertIn('Size = Input { SourceOp = "TitleArriveSize", Source = "Value", },', text)
        self.assertIn('Foreground = Input { SourceOp = "TitleArrive", Source = "Output", },', text)
        self.assertIn('Blend = Input { SourceOp = "TitleBlend", Source = "Value", },', text)
        self.assertIn('Center = Input { SourceOp = "Label1ArrivePath", Source = "Value", },', text)
        self.assertNotIn("Label2Arrive", text)                               # only the name that was asked to
        self.assertIn('Blend = Input { SourceOp = "MarksBlend", Source = "Value", },', text)       # the numbers, as one
        self.assertEqual(text.count('Blend = Input { SourceOp = "GridBlend", Source = "Value", },'), 3)   # every line
        untitled, _gid = comp.comp_text(M.clean({"arrivals": {"title": {"kind": "pop"}}}), FRAME, 24, self.frames)
        self.assertNotIn("TitleArrive", untitled)

    def test_a_number_fades_in_when_its_part_says(self):
        def blend(text, k):
            block = text[text.index(f"\t\tValue{k}Blend = BezierSpline"):text.index(f"\t\tValue{k}On = Merge")]
            return [(int(f), float(v)) for f, v in re.findall(r"\[(\d+)\] = \{ ([\d.e-]+),", block)]
        keys = blend(self.text, 5)                                           # Fri: by itself, as its bar starts
        start = int(M.timings(self.spec)[4]["start"] * 24)
        self.assertEqual(keys[0], (0, 0.0))
        self.assertTrue(all(v == 0.0 for f, v in keys if f < start))
        self.assertEqual(keys[-1], (self.frames - 1, 1.0))
        late, _gid = comp.comp_text(M.with_part(self.spec, "value:4", "start", 2.5), FRAME, 24, self.frames)
        self.assertTrue(all(v == 0.0 for f, v in blend(late, 5) if f < 60))
    def test_the_axis_is_lines_numbers_and_a_turned_title(self):
        for word in ("Line1", "Mark0", "Subtitle", "AlongTitle", "SideTitle", "AngleZ"):
            self.assertNotIn(word, self.text)
        spec = M.clean({"scale": True, "grid": True, "subtitle": "This week", "x_title": "Day", "y_title": "Units"})
        text, _gid = comp.comp_text(spec, FRAME, 24, 48)
        plan = M.layout(spec, FRAME)
        self.assertEqual(text.count("= RectangleMask {"), 5 + 1 + 3)        # bars, the floor, a line per mark above it
        for said in ("0", "10", "20", "30", "This week", "Day", "Units"):
            self.assertIn(f'StyledText = Input {{ Value = "{said}", }},', text)
        mark = text[text.index("\t\tMark2 = TextPlus"):text.index("\t\tMark2On = Merge")]
        self.assertIn("HorizontalLeftCenterRight = Input { Value = 1, },", mark)      # the numbers end at the axis
        side = text[text.index("\t\tSideTitle = TextPlus"):text.index("\t\tSideTitleOn = Merge")]
        self.assertIn("AngleZ = Input { Value = 90, },", side)
        t = plan["y_title"]
        self.assertIn(f"Center = Input {{ Value = {{ {(t['x'] - 0.375 * t['size']) / 1920:.6g}, {1 - t['y'] / 1080:.6g} }}, }},", side)
        line = text[text.index("\t\tLine3Mask = RectangleMask"):text.index("\t\tLine3 = Background")]
        self.assertIn(f"{1 - plan['marks'][3]['y'] / 1080:.6g} }}, }},", line)

    def test_a_row_without_a_name_has_no_label(self):
        spec = M.clean({"rows": [{"label": "", "value": 3}, {"label": "B", "value": 4}]})
        text, _gid = comp.comp_text(spec, FRAME, 24, 48)
        self.assertNotIn("Label1 = TextPlus", text)
        self.assertIn("Label2 = TextPlus", text)


class JobTests(unittest.TestCase):
    def test_a_graph_is_a_new_clip_at_the_playhead(self):
        controller, timeline = studio()
        footage = timeline.put(1, 86400, 90000)
        spec = M.clean({"seconds": 2, "hold": 2})
        done = place.bars(controller, spec)
        self.assertEqual((done["track"], done["start"], done["end"]), (2, 86400, 86496))
        self.assertEqual(done["id"], M.graph_id(spec, FRAME, 24.0))
        self.assertEqual(timeline.tracks[2][0].comp.GetData(comp.TAG), done["id"])
        self.assertEqual(timeline.tracks[1], [footage])

    def test_a_counter_already_there_is_not_replaced_by_a_graph(self):
        controller, timeline = studio()
        place.counter(controller, {})
        done = place.bars(controller, {})
        self.assertEqual((done["track"], done["replaced"]), (2, False))
        self.assertEqual(len(timeline.tracks[1]), 1)

    def test_no_rows_is_said_and_nothing_added(self):
        controller, timeline = studio()
        with self.assertRaises(PlaceError) as caught:
            place.bars(controller, {"rows": []})
        self.assertIn("no numbers", str(caught.exception))
        self.assertEqual(timeline.tracks[1], [])


if __name__ == "__main__":
    unittest.main()
