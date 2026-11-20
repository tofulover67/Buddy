"""Data Graphics > Counters, without Qt or Resolve: how a number is written
(figures.py), the counter's clock (counters/model.py), its comp (counters/comp.py)
and the job that puts it on a timeline (place.counter, on a fake Resolve)."""

import re
import unittest

import _paths  # noqa: F401
from _child_reader import load_without_qt
from _fake_graphics import Controller, Project, studio

from core.fusion_place import PlaceError
from core.resolve_bridge import ResolveConnectionError
from pages.data_graphics import easing, figures, parts, place, text_look
from pages.data_graphics.counters import comp, model as M

FRAME = (1920, 1080)
JOBS = {"data_graphics.read": ("pages.data_graphics.place", "read"),
        "data_graphics.counter": ("pages.data_graphics.place", "counter"),
        "data_graphics.bars": ("pages.data_graphics.place", "bars")}


class FiguresTests(unittest.TestCase):
    def test_thousands_and_decimals_each_way(self):
        self.assertEqual(figures.plain(1234567.891, 2, "comma"), "1,234,567.89")
        self.assertEqual(figures.plain(1234567.891, 2, "dot"), "1.234.567,89")
        self.assertEqual(figures.plain(1234567.891, 1, "space"), "1 234 567,9")
        self.assertEqual(figures.plain(1234567.891, 0, "none"), "1234568")
        self.assertEqual(figures.plain(999, 0), "999")

    def test_below_zero_and_never_minus_zero(self):
        self.assertEqual(figures.plain(-1500, 0), "-1,500")
        self.assertEqual(figures.plain(-0.2, 0), "0")
        self.assertEqual(figures.plain(-0.2, 1), "-0.2")

    def test_a_clock_shows_the_second_its_in(self):
        self.assertEqual(figures.clock(0), "0:00")
        self.assertEqual(figures.clock(59.99), "0:59")
        self.assertEqual(figures.clock(60), "1:00")
        self.assertEqual(figures.clock(3725), "1:02:05")
        self.assertEqual(figures.clock(9.58, 2), "0:09.58")
        self.assertEqual(figures.clock(9.58, 1, "dot"), "0:09,5")

    def test_a_clock_running_down_shows_the_second_still_left(self):
        self.assertEqual(figures.clock(9.2, down=True), "0:10")
        self.assertEqual(figures.clock(0.01, down=True), "0:01")
        self.assertEqual(figures.clock(0, down=True), "0:00")

    def test_before_and_after(self):
        fmt = figures.clean_format({"prefix": "$", "suffix": " USD", "decimals": "2"}, M.DEFAULTS)
        self.assertEqual(figures.shown(1234.5, fmt), "$1,234.50 USD")
        self.assertEqual(figures.clean_format({"decimals": 99, "separator": "tabs", "form": "sundial",
                                               "prefix": "x" * 40}, M.DEFAULTS),
                         {"decimals": 3, "separator": "comma", "prefix": "x" * 12, "suffix": "", "form": "number"})

    def test_a_typed_number_is_read(self):
        for text, value in (("1,234", 1234.0), ("1,5", 1.5), ("1.234,5", 1234.5), ("$1 200", 1200.0), ("45%", 45.0),
                            ("1,234,567.8", 1234567.8), ("-3", -3.0), ("0.25", 0.25), ("1.234", 1.234),
                            ("−7", -7.0), ("abc", None), ("", None)):
            with self.subTest(text):
                self.assertEqual(figures.parse(text), value)

    def test_runs_keep_only_the_changes(self):
        self.assertEqual(figures.runs(["0", "0", "1", "1", "1", "2"]), [(0, "0"), (2, "1"), (5, "2")])


class ModelTests(unittest.TestCase):
    def test_anything_cleans_to_a_whole_counter(self):
        self.assertEqual(M.clean(None), M.clean({}))
        self.assertEqual(set(M.clean({})), set(M.DEFAULTS))
        spec = M.clean({"from": "ten", "to": "2,5", "seconds": 0, "hold": -4, "easing": "bouncy", "size": 900,
                        "x": -3, "align": "upside down", "colour": "red", "font": "", "outline": 1})
        self.assertEqual((spec["from"], spec["to"], spec["seconds"], spec["hold"]), (0.0, 2.5, M.MIN_SECONDS, 0.0))
        self.assertEqual((spec["easing"], spec["size"], spec["x"], spec["align"]), (easing.DEFAULT, 80.0, 0.0, "center"))
        self.assertEqual((spec["colour"], spec["font"], spec["outline"]), ("#ffffff", "Arial", True))
        self.assertEqual(M.clean(spec), spec)

    def test_every_preset_is_a_valid_counter(self):
        for name, fields in M.PRESETS.items():
            with self.subTest(name):
                spec = M.clean({**M.DEFAULTS, **fields})
                for key, value in fields.items():
                    self.assertEqual(spec[key], value)

    def test_a_preset_changes_how_its_written_and_never_the_numbers(self):
        typed = M.clean({"from": 3, "to": 4200, "seconds": 7, "hold": 2.5, "decimals": 1, "separator": "dot",
                         "easing": "custom", "curve": [0.2, 1.4, 0.6, 0.9], "colour": "#ff0000"})
        for name in M.PRESETS:
            with self.subTest(name):
                spec = M.with_preset(typed, name)
                changed = {k for k in spec if spec[k] != typed[k]}
                self.assertLessEqual(changed, {"prefix", "suffix", "form"})
        self.assertEqual((M.with_preset(typed, "Money")["prefix"], M.with_preset(typed, "Percent")["suffix"]), ("$", "%"))
        # High to low is as good as low to high: whichever way was typed is kept, by every preset.
        down = M.clean({**typed, "from": 4200, "to": 3})
        for name in M.PRESETS:
            with self.subTest(name, way="down"):
                spec = M.with_preset(down, name)
                self.assertEqual((spec["from"], spec["to"]), (4200.0, 3.0))
        self.assertEqual(M.with_preset(down, "Timer")["form"], "clock")
        self.assertEqual(M.texts(M.with_preset(M.clean({"from": 10, "to": 0}), "Timer"), 24)[0], "0:10")
        self.assertIsNone(M.with_preset(typed, "Fireworks"))

    def test_it_starts_on_from_and_lands_on_to_exactly(self):
        spec = M.clean({"from": 0, "to": 1250000, "prefix": "$", "seconds": 2, "hold": 1})
        texts = M.texts(spec, 24)
        self.assertEqual(len(texts), M.frames(spec, 24))
        self.assertEqual(len(texts), 72)
        self.assertEqual(texts[0], "$0")
        self.assertEqual(texts[47], "$1,250,000")          # the run's last frame
        self.assertEqual(set(texts[47:]), {"$1,250,000"})   # then held
        self.assertNotEqual(texts[46], "$1,250,000")

    def test_it_lands_even_with_no_hold(self):
        spec = M.clean({"to": 100, "seconds": 1, "hold": 0, "easing": "ease_in"})
        self.assertEqual(M.texts(spec, 30)[-1], "100")

    def test_the_easing_is_the_clock(self):
        spec = M.clean({"from": 0, "to": 100, "easing": "linear"})
        self.assertEqual(M.value_at(spec, 0.5), 50.0)
        spec = M.clean({"from": 0, "to": 100, "easing": "ease_out"})
        self.assertEqual(M.value_at(spec, 0.5), 87.5)
        for name, curve in easing.CURVES.items():
            with self.subTest(name):
                self.assertEqual((easing.ease(curve, 0), easing.ease(curve, 1)), (0.0, 1.0))
        self.assertAlmostEqual(easing.ease(easing.CURVES["ease_in"], 0.5), 0.125)
        self.assertAlmostEqual(easing.ease(easing.CURVES["linear"], 0.3), 0.3)
        self.assertGreater(max(easing.ease(easing.CURVES["overshoot"], p / 20) for p in range(21)), 1.0)

    def test_a_named_curve_is_its_handles_and_a_dragged_one_is_kept(self):
        self.assertEqual(M.clean({"easing": "linear", "curve": [0, 0, 0, 0]})["curve"], [0.3333, 0.3333, 0.6667, 0.6667])
        spec = M.clean({"easing": "custom", "curve": [0.2, 1.4, 0.6, 0.9]})
        self.assertEqual((spec["easing"], spec["curve"]), ("custom", [0.2, 1.4, 0.6, 0.9]))
        wild = M.clean({"easing": "custom", "curve": [-1, 9, 2, -9]})
        self.assertEqual(wild["curve"], [0.0, easing.Y_MAX, 1.0, easing.Y_MIN])
        for bad in ({"easing": "custom"}, {"easing": "custom", "curve": [1, 2]}, {"easing": "bouncy"}):
            self.assertEqual(M.clean(bad)["easing"], easing.DEFAULT)

    def test_a_curve_that_overshoots_counts_past_the_end_and_comes_back(self):
        spec = M.clean({"to": 100, "easing": "overshoot", "seconds": 1, "hold": 0})
        values = [int(t) for t in M.texts(spec, 24)]
        self.assertGreater(max(values), 100)
        self.assertEqual(values[-1], 100)

    def test_a_countdown_shows_each_second_until_its_gone(self):
        spec = M.clean({"from": 10, "to": 0, "seconds": 10, "hold": 0, "easing": "linear", "form": "clock"})
        texts = M.texts(spec, 24)
        self.assertEqual((texts[0], texts[1], texts[-1]), ("0:10", "0:10", "0:00"))
        self.assertEqual(texts[-2], "0:01")
        seen = [t for _f, t in figures.runs(texts)]
        self.assertEqual(seen, [f"0:{n:02d}" for n in range(10, -1, -1)])

    def test_an_arrival_ends_exactly_in_place(self):
        for kind in ("fade", "rise", "pop"):
            with self.subTest(kind):
                spec = M.clean({"in_kind": kind, "in_seconds": 0.5, "in_easing": "overshoot"})
                states = parts.states(M.arrival(spec), 24, 96)
                self.assertEqual(len(states), 13)                       # frames 0..12: half a second
                self.assertEqual(states[-1], (1.0, 0.0, 1.0))
                self.assertEqual(states[0][0], 0.0)
                self.assertTrue(all(0.0 <= a <= 1.0 for a, _dy, _s in states))      # opacity never overshoots
        self.assertEqual(parts.states(M.arrival(M.clean({"in_kind": "rise"})), 24, 96)[0], (0.0, parts.RISE, 1.0))
        self.assertEqual(parts.states(M.arrival(M.clean({"in_kind": "pop"})), 24, 96)[0], (0.0, 0.0, parts.POP))
        self.assertEqual(len(parts.states(M.arrival(M.clean({"in_kind": "fade", "in_seconds": 10})), 24, 30)), 30)

    def test_an_arrival_that_starts_later_isnt_there_until_then(self):
        late = parts.states(M.arrival(M.clean({"in_kind": "fade", "in_start": 1, "in_seconds": 0.5, "in_easing": "linear"})), 24, 96)
        self.assertEqual(len(late), 37)                                 # until 1.5 s
        self.assertEqual({a for a, _dy, _s in late[:25]}, {0.0})
        self.assertAlmostEqual(late[30][0], 0.5)
        self.assertEqual(late[-1], (1.0, 0.0, 1.0))

    def test_the_count_can_start_later_and_the_clip_grows_to_fit(self):
        spec = M.clean({"to": 100, "seconds": 1, "hold": 0, "count_start": 0.5, "easing": "linear"})
        texts = M.texts(spec, 24)
        self.assertEqual(len(texts), 12 + 24)
        self.assertEqual(set(texts[:13]), {"0"})                        # waiting, then off on frame 12
        self.assertNotEqual(texts[13], "0")
        self.assertEqual(texts[-1], "100")
        slow = M.clean({"seconds": 1, "hold": 0, "in_kind": "fade", "in_start": 2, "in_seconds": 1})
        self.assertEqual(M.frames(slow, 24), 73)                        # the arrival ends after the count: the clip covers it

    def test_the_docks_lanes_are_the_count_and_its_arriving(self):
        spec = M.clean({"seconds": 2, "count_start": 0.5, "in_kind": "pop", "in_start": 0.25, "in_seconds": 0.75})
        count, arrive = M.lanes(spec)
        self.assertEqual((count["id"], count["start"], count["length"], count["kind"], count["kinds"]),
                         ("count", 0.5, 2.0, None, None))
        self.assertEqual((arrive["id"], arrive["start"], arrive["length"], arrive["kind"], arrive["kinds"]),
                         ("arrive", 0.25, 0.75, "pop", list(parts.KINDS)))
        moved = M.with_part(spec, "count", "start", 1.25)
        self.assertEqual((moved["count_start"], moved["seconds"]), (1.25, 2.0))
        self.assertEqual(M.with_part(spec, "count", "length", 5)["seconds"], 5.0)
        shaped = M.with_part(spec, "arrive", "curve", [0.1, 1.3, 0.5, 1.0])
        self.assertEqual((shaped["in_easing"], shaped["in_curve"], shaped["easing"]), ("custom", [0.1, 1.3, 0.5, 1.0], easing.DEFAULT))
        self.assertEqual(M.with_part(spec, "count", "curve", [0.2, 1.4, 0.6, 0.9])["easing"], "custom")
        self.assertEqual(M.with_part(spec, "arrive", "kind", "none")["in_kind"], "none")
        for bad in (("title", "start", 1), ("count", "kind", "fade"), ("count", "reset", None)):
            self.assertIsNone(M.with_part(spec, *bad))
    def test_an_arrival_cleans_like_the_rest(self):
        spec = M.clean({"in_kind": "explode", "in_seconds": 999, "in_start": -3, "count_start": "soon",
                        "in_easing": "custom", "in_curve": [0.2, 1.4, 0.6, 0.9]})
        self.assertEqual((spec["in_kind"], spec["in_seconds"], spec["in_start"], spec["count_start"]),
                         ("none", parts.MAX_LENGTH, 0.0, 0.0))
        self.assertEqual((spec["in_easing"], spec["in_curve"]), ("custom", [0.2, 1.4, 0.6, 0.9]))
        self.assertEqual(M.clean({"in_easing": "linear"})["in_curve"], easing.named("linear"))
        self.assertEqual(M.clean({})["in_seconds"], parts.ARRIVES_IN)
    def test_a_clip_shorter_or_longer_than_asked_still_plays_from_the_start(self):
        spec = M.clean({"to": 10, "seconds": 1, "hold": 1})
        self.assertEqual(len(M.texts(spec, 24, 30)), 30)
        self.assertEqual(M.texts(spec, 24, 100)[-1], "10")

    def test_where_it_sits(self):
        at = M.place_of(M.clean({"size": 20, "x": 25, "y": 50}), FRAME)
        self.assertEqual((at["size"], at["x"]), (216.0, 480.0))
        self.assertAlmostEqual(at["baseline"], 540 + text_look.CAP * 216 / 2)

    def test_the_same_counter_has_the_same_id(self):
        a, b = M.clean({"to": 5}), M.clean({"to": 6})
        self.assertEqual(M.counter_id(a, FRAME, 24), M.counter_id(M.clean({"to": 5}), FRAME, 24))
        self.assertNotEqual(M.counter_id(a, FRAME, 24), M.counter_id(b, FRAME, 24))
        self.assertNotEqual(M.counter_id(a, FRAME, 24), M.counter_id(a, FRAME, 30))


class CompTests(unittest.TestCase):
    def setUp(self):
        self.spec = M.clean({"to": 100, "suffix": "%", "seconds": 2, "hold": 1, "align": "left", "x": 20, "y": 30,
                             "font": "Inter", "style": "Black", "colour": "#ff8800"})
        self.text, self.gid = comp.comp_text(self.spec, FRAME, 24, 72)

    def test_one_text_plus_keyed_into_media_out(self):
        self.assertEqual(self.text.count("= TextPlus {"), 1)
        self.assertIn('StyledText = Input { SourceOp = "CounterText", Source = "Value", },', self.text)
        self.assertIn('Input = Input { SourceOp = "Counter", Source = "Output", },', self.text)
        self.assertIn(f'CustomData = {{ TEMPLATE_ID = "Text+", {comp.TAG} = "{self.gid}" }}', self.text)
        self.assertIn("RenderRange = { 0, 71 }", self.text)

    def test_its_keys_are_the_models_texts(self):
        keyed = [(int(f), t) for f, t in re.findall(r'\[(\d+)\] = \{ \d+, Flags = \{ Linear = true, LockedY = true \}, '
                                                    r'Value = Text \{ Value = "([^"]*)" \} \}', self.text)]
        self.assertEqual(keyed, figures.runs(M.texts(self.spec, 24, 72)))
        self.assertEqual(keyed[0], (0, "0%"))
        self.assertEqual(keyed[-1][1], "100%")
        self.assertLessEqual(keyed[-1][0], 47)            # there by the run's last frame, then held

    def test_the_face_the_colour_and_the_side_it_grows_from(self):
        for line in ('Font = Input { Value = "Inter", },', 'Style = Input { Value = "Black", },',
                     "HorizontalLeftCenterRight = Input { Value = -1, },", "Red1 = Input { Value = 1, },",
                     "Green1 = Input { Value = 0.533333, },", "Blue1 = Input { Value = 0, },"):
            self.assertIn(line, self.text)
        self.assertNotIn("Enabled2", self.text)
        self.assertNotIn("Enabled3", self.text)

    def test_size_and_centre_from_the_faces_metrics(self):
        text, _gid = comp.comp_text(self.spec, FRAME, 24, 72, {"em": 0.8, "baseline": 0.4})
        size_px = 0.18 * 1080
        self.assertIn(f"Size = Input {{ Value = {size_px / (0.8 * 1920):.6g}, }},", text)
        baseline = 0.30 * 1080 + text_look.CAP * size_px / 2
        self.assertIn(f"Center = Input {{ Value = {{ 0.2, {1 - (baseline - 0.4 * size_px) / 1080:.6g} }}, }},", text)

    def test_metrics_that_make_no_sense_are_not_used(self):
        self.assertEqual(text_look.clean_metrics({"em": "wide", "baseline": 40}), text_look.clean_metrics(None))
        self.assertEqual(text_look.clean_metrics({"em": 0.8, "baseline": 0.4}), {"em": 0.8, "baseline": 0.4})

    def test_outline_and_shadow_when_asked(self):
        spec = M.clean({**self.spec, "outline": True, "outline_colour": "#0000ff", "shadow": True})
        text, _gid = comp.comp_text(spec, FRAME, 24, 72)
        for line in ("Enabled2 = Input { Value = 1, },", "OutsideOnly2 = Input { Value = 1, },",
                     "Blue2 = Input { Value = 1, },", "Enabled3 = Input { Value = 1, },"):
            self.assertIn(line, text)

    def test_already_there_adds_no_tools(self):
        self.assertNotIn("Transform", self.text)
        self.assertNotIn("Merge", self.text)
        self.assertEqual(parts.states(M.arrival(self.spec), 24, 72), [])

    def test_a_fade_is_the_blend_of_a_merge_onto_a_clear_canvas(self):
        spec = M.clean({**self.spec, "in_kind": "fade", "in_seconds": 0.5, "in_easing": "linear"})
        text, _gid = comp.comp_text(spec, FRAME, 24, 72)
        self.assertNotIn("= Transform {", text)
        self.assertIn('Blend = Input { SourceOp = "ArriveBlend", Source = "Value", },', text)
        self.assertIn('Foreground = Input { SourceOp = "Counter", Source = "Output", },', text)
        self.assertIn('Input = Input { SourceOp = "CounterOn", Source = "Output", },', text)
        block = text[text.index("\t\tArriveBlend = BezierSpline"):text.index("\t\tCanvas = Background")]
        keys = [(int(f), float(v)) for f, v in re.findall(r"\[(\d+)\] = \{ ([\d.e-]+),", block)]
        self.assertEqual((keys[0], keys[-1]), ((0, 0.0), (12, 1.0)))    # there at half a second, then held

    def test_a_rise_and_a_pop_are_a_transform_about_the_digits_middle(self):
        rise = M.clean({**self.spec, "in_kind": "rise"})
        text, _gid = comp.comp_text(rise, FRAME, 24, 72)
        at = M.place_of(rise, FRAME)
        pivot_y = 1 - (at["baseline"] - text_look.CAP * at["size"] / 2) / 1080
        self.assertIn(f"Pivot = Input {{ Value = {{ 0.2, {pivot_y:.6g} }}, }},", text)
        self.assertIn('Center = Input { SourceOp = "ArrivePath", Source = "Value", },', text)
        self.assertNotIn('Size = Input { SourceOp = "ArriveSize"', text)
        ys = text[text.index("\t\tArrivePathY = BezierSpline"):text.index("\t\tArrivePath = XYPath")]
        first = float(re.findall(r"\[\d+\] = \{ ([\d.e-]+),", ys)[0])
        self.assertAlmostEqual(first, 0.5 - parts.RISE * at["size"] / 1080, places=5)      # below its place
        pop, _gid = comp.comp_text(M.clean({**self.spec, "in_kind": "pop"}), FRAME, 24, 72)
        self.assertIn('Size = Input { SourceOp = "ArriveSize", Source = "Value", },', pop)
        self.assertNotIn("ArrivePath", pop)

    def test_text_with_quotes_stays_one_string(self):
        spec = M.clean({"prefix": '"', "suffix": "\\", "to": 1, "seconds": 0.2, "hold": 0})
        text, _gid = comp.comp_text(spec, FRAME, 24, 5)
        self.assertIn('Value = Text { Value = "\\"1\\\\" }', text)


class JobTests(unittest.TestCase):
    def test_the_jobs_load_without_qt(self):
        self.assertEqual(load_without_qt(JOBS), "")

    def test_the_jobs_are_named_in_the_readers_process(self):
        from core import resolve_reader_child
        for name, where in JOBS.items():
            self.assertEqual(resolve_reader_child.JOBS[name], where)

    def test_read_says_the_timeline_and_the_fonts(self):
        controller, _timeline = studio(fps=30, size=(1080, 1920), name="Short")
        self.assertEqual(place.read(controller), {"timeline": "Short", "frame": [1080, 1920], "fps": 30.0,
                                                  "fonts": {"Arial": ["Bold", "Regular"], "Inter": ["Black"]}})
        self.assertNotIn("fonts", place.read(controller, fonts=False))

    def test_read_with_no_timeline_still_lists_the_fonts(self):
        got = place.read(Controller(Project(None)))
        self.assertEqual((got["timeline"], sorted(got["fonts"])), ("", ["Arial", "Inter"]))
        self.assertEqual(place.read(Controller(None))["timeline"], "")

    def test_a_counter_is_a_new_clip_at_the_playhead(self):
        controller, timeline = studio()
        footage = timeline.put(1, 86400, 90000)
        timeline.playhead = 86400 + 48
        spec = M.clean({"to": 500, "seconds": 2, "hold": 1})
        done = place.counter(controller, spec, {"em": 0.72, "baseline": 0.38})
        self.assertEqual((done["track"], done["start"], done["end"], done["replaced"]), (2, 86448, 86520, False))
        self.assertEqual((done["timeline"], done["fps"], done["frame"], done["seconds"]), ("Edit", 24.0, [1920, 1080], 3.0))
        self.assertEqual(done["id"], M.counter_id(spec, FRAME, 24.0))
        (item,) = timeline.tracks[2]
        self.assertEqual(item.comp.GetData(comp.TAG), done["id"])
        self.assertIn('Value = Text { Value = "500" }', item.comp.text)
        self.assertEqual(timeline.tracks[1], [footage])                 # nothing of theirs touched
        self.assertEqual(timeline.playhead, 86448)

    def test_adding_again_replaces_buddys_own_unless_told_not_to(self):
        controller, timeline = studio()
        place.counter(controller, {"to": 1})
        again = place.counter(controller, {"to": 2})
        self.assertTrue(again["replaced"])
        self.assertEqual(len(timeline.tracks[1]), 1)
        kept = place.counter(controller, {"to": 3}, replace=False)
        self.assertEqual((kept["track"], kept["replaced"]), (2, False))

    def test_what_arrives_is_cleaned_first(self):
        controller, timeline = studio()
        place.counter(controller, {"to": "lots", "seconds": 9999})
        (item,) = timeline.tracks[1]
        self.assertEqual(item.end - item.start, M.frames(M.clean({"seconds": 9999}), 24.0))

    def test_no_project_or_no_timeline_is_said(self):
        with self.assertRaises(ResolveConnectionError):
            place.counter(Controller(None), {})
        with self.assertRaises(ResolveConnectionError) as caught:
            place.counter(Controller(Project(None)), {})
        self.assertIn("Open a timeline", str(caught.exception))

    def test_a_failure_adds_nothing(self):
        controller, timeline = studio()
        timeline.takes_comps = False
        with self.assertRaises(PlaceError):
            place.counter(controller, {})
        self.assertEqual(timeline.tracks[1], [])


if __name__ == "__main__":
    unittest.main()
