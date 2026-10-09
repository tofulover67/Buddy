"""Presets > Highlight: the word timing from Transcribe's transcripts, the box's
keys (highlight.py), the Text+ word measuring (highlight_resolve.measure, on
a fake Text+) and what the page says and keeps - no Resolve, no Qt."""

import json
import os
import re
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.text_animator import highlight as hl
from pages.text_animator import highlight_resolve as hr
from pages.text_animator import motion_resolve as mr
from pages.text_animator import units as un
from test_motion_units import Timeline, controller, on_timeline, text_clip, text_tool

TEXT = "A big thank you\nas well"
SPOKEN = [{"start": 1.0, "end": 1.1, "text": "A"}, {"start": 1.2, "end": 1.4, "text": "big"},
          {"start": 1.5, "end": 1.8, "text": "thank"}, {"start": 1.9, "end": 2.0, "text": "you"},
          {"start": 2.3, "end": 2.4, "text": "as"}, {"start": 2.5, "end": 2.9, "text": "well."}]


class OptionTests(unittest.TestCase):
    def test_defaults_and_bad_values(self):
        self.assertEqual(hl.clean_options(None), hl.DEFAULTS)
        got = hl.clean_options({"look": "box", "pop": "huge", "box_color": "#ABCDEF", "word_color": "red"})
        self.assertEqual(got["look"], "box")
        self.assertEqual(got["pop"], hl.DEFAULTS["pop"])
        self.assertEqual(got["box_color"], "#abcdef")
        self.assertEqual(got["word_color"], hl.DEFAULTS["word_color"])

    def test_the_gain_turns_the_fill_into_the_colour_and_leaves_black_black(self):
        gain = hl.color_gain((1.0, 0.5, 0.0), (1.0, 1.0, 1.0))
        self.assertEqual(gain, (1.0, 0.5, 0.0))
        fill = (0.5, 0.8, 0.0)
        gain = hl.color_gain((1.0, 0.4, 0.2), fill)
        self.assertAlmostEqual(fill[0] * gain[0], 1.0)
        self.assertAlmostEqual(fill[1] * gain[1], 0.4)
        self.assertEqual(gain[2], 1.0)                      # no blue to raise: left alone
        self.assertEqual(tuple(0.0 * g for g in gain), (0.0, 0.0, 0.0))


class WordTests(unittest.TestCase):
    def test_words_are_the_text_plus_characters(self):
        words = hl.words_of(TEXT)
        self.assertEqual([w[2] for w in words], ["A", "big", "thank", "you", "as", "well"])
        for first, last, word in words:
            self.assertEqual(TEXT[first:last], word)
        # The Follower's word numbers (units.py) agree, character by character.
        marks = un.unit_of(TEXT, un.WORDS)
        for n, (first, last, _w) in enumerate(words):
            self.assertEqual(set(marks[first:last]), {n})

    def test_each_character_takes_its_words_delay(self):
        delays = hl.delays(TEXT, [0, 5, 9, 14, 20, 26])
        self.assertEqual(len(delays), len(TEXT))
        self.assertEqual(delays[0], 0)
        self.assertEqual(delays[TEXT.index("big")], 5)
        self.assertEqual(delays[TEXT.index("\n")], 20)      # the line break waits for the next word

    def test_the_transcripts_words_line_up_with_the_captions(self):
        times, how = hl.word_times(TEXT, SPOKEN, 0.9, 3.0)
        self.assertEqual(how, "transcript")
        self.assertEqual(times, [1.0, 1.2, 1.5, 1.9, 2.3, 2.5])

    def test_a_word_fixed_by_hand_keeps_its_place(self):
        times, how = hl.word_times("A big THANKS, you\nas well", SPOKEN, 0.9, 3.0)
        self.assertEqual(how, "transcript")
        self.assertEqual(times[2], 1.5)
        times, _ = hl.word_times("A big thank-you\nas well", SPOKEN, 0.9, 3.0)
        self.assertEqual(times[2], 1.5)                     # one word for two said: the nearest spelled
        self.assertEqual(times[3:], [2.3, 2.5])

    def test_a_word_with_no_match_goes_between_its_neighbours(self):
        times, how = hl.word_times("A big thank you so\nmuch as well", SPOKEN, 0.9, 3.0)
        self.assertEqual(how, "transcript")
        self.assertTrue(1.9 < times[4] < times[5] < 2.3)
        self.assertEqual(times, sorted(times))

    def test_words_outside_the_clip_are_not_its_words(self):
        times, how = hl.word_times("as well", SPOKEN, 2.2, 3.0)
        self.assertEqual((times, how), ([2.3, 2.5], "transcript"))

    def test_a_caption_the_transcript_doesnt_have_is_timed_evenly(self):
        times, how = hl.word_times("Un grand merci", SPOKEN, 1.0, 2.0)
        self.assertEqual(how, "even")
        self.assertEqual(times[0], 1.0)
        self.assertEqual(times, sorted(times))
        self.assertLess(times[-1], 2.0)
        times, how = hl.word_times("A big thank you", None, 1.0, 2.0)
        self.assertEqual(how, "even")

    def test_the_transcript_that_matches_is_chosen(self):
        with tempfile.TemporaryDirectory() as folder:
            def write(name, words, when):
                path = os.path.join(folder, name)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"segments": [{"words": [{"start": w["start"], "end": w["end"], "word": " " + w["text"]}
                                                       for w in words]}]}, f)
                os.utime(path, (when, when))
            write("right.json", SPOKEN, 1000)
            write("newer but other.json", [{"start": 1.0, "end": 1.2, "text": "hello"}], 2000)
            with open(os.path.join(folder, "broken.json"), "w") as f:
                f.write("{not json")
            found = hl.load_transcripts(folder)
            self.assertEqual([p.name for p, _w in found], ["newer but other.json", "right.json"])
            path, words = hl.best_transcript(found, [(TEXT, 0.9, 3.0)])
            self.assertEqual(path.name, "right.json")
            self.assertEqual(words[0]["text"], "A")
            self.assertEqual(hl.best_transcript(found, [("nothing alike", 0, 9)]), (None, None))


class KeyTests(unittest.TestCase):
    BOXES = [(0.3, 0.2, 0.05, 0.05), (0.5, 0.2, 0.1, 0.05), (0.7, 0.2, 0.08, 0.05)]

    def value(self, keys, frame):
        """The value a channel holds at a key's frame or, between keys, the last one before."""
        before = [f for f in keys if f <= frame]
        return keys[max(before)]["value"]

    def test_the_box_is_off_until_the_first_word_then_moves_word_to_word(self):
        keys = hl.plan([10, 30, 50], self.BOXES, 80, 24, hl.clean_options({}))
        for channel in keys.values():
            frames = list(channel)
            self.assertEqual(frames, sorted(frames))
            self.assertLessEqual(max(frames), 79)
        self.assertEqual(self.value(keys["level"], 9), 0.0)
        self.assertEqual(self.value(keys["level"], 11), 1.0)
        self.assertEqual(self.value(keys["x"], 29), 0.3)      # stays on a word until the next starts
        self.assertEqual(self.value(keys["x"], 32), 0.5)      # there SLIDE (2 frames at 24) later
        self.assertEqual(self.value(keys["x"], 79), 0.7)
        # Pops a size bigger on arrival, then back.
        self.assertAlmostEqual(keys["w"][32]["value"], 0.1 * hl.POPS["small"])
        self.assertAlmostEqual(self.value(keys["w"], 40), 0.1)

    def test_a_jump_takes_one_frame_and_no_pop_stays_at_size(self):
        keys = hl.plan([10, 30], self.BOXES[:2], 60, 24, hl.clean_options({"move": "jump", "pop": "off"}))
        self.assertEqual(self.value(keys["x"], 30), 0.3)
        self.assertEqual(self.value(keys["x"], 31), 0.5)
        self.assertEqual({round(k["value"], 6) for f, k in keys["w"].items() if f >= 11}, {0.05, 0.1})
        self.assertIsNone(hl.pop_keys(24, hl.clean_options({"pop": "off"})))

    def test_words_said_close_together_still_take_turns(self):
        keys = hl.plan([10, 11, 12, 40], self.BOXES + [(0.9, 0.2, 0.1, 0.05)], 60, 24, hl.clean_options({}))
        frames = list(keys["x"])
        self.assertEqual(frames, sorted(set(frames)))
        self.assertEqual(self.value(keys["x"], 59), 0.9)

    def test_no_handle_runs_past_its_neighbours(self):
        keys = hl.plan([10, 30, 50], self.BOXES, 80, 24, hl.clean_options({"pop": "big"}))
        for channel in keys.values():
            frames = sorted(channel)
            for a, b in zip(frames, frames[1:]):
                self.assertLess(channel[a]["rh"][0], b)
                self.assertGreater(channel[b]["lh"][0], a)
                self.assertEqual(channel[a]["rh"][1], channel[a]["value"])     # flat: never past either value

    def test_boxes_on_a_line_are_as_tall_as_the_line(self):
        ink = [(0.3, 0.6, 0.1, 0.05), (0.5, 0.59, 0.1, 0.08), (0.4, 0.45, 0.2, 0.05)]
        boxes = hl.word_boxes(ink, 16 / 9, (0.1, 0.1))
        self.assertAlmostEqual(boxes[0][3], boxes[1][3])
        self.assertAlmostEqual(boxes[0][1], boxes[1][1])
        self.assertLess(boxes[2][3], boxes[0][3])           # its own line, its own height
        self.assertAlmostEqual(boxes[0][3], (0.63 - 0.55) * 1.2)     # both words' ink: 0.55..0.63
        self.assertGreater(boxes[0][2], 0.1)


class FakeTextPlus:
    """A Text+ whose DoD is the ink of the characters Write On shows, each
    character 10 px wide on one line, 30 px tall - and a shadow 20 px lower
    while element 3 is on."""

    class Output:
        def __init__(self, tp):
            self.tp = tp

        def GetDoD(self, _when):
            tp = self.tp
            n = len(tp.text)
            # As Text+: character i shows when Start x n <= i < End x n.
            shown = [i for i in range(n) if tp.inputs["Start"] * n <= i < tp.inputs["End"] * n and tp.text[i] != " "]
            if not shown:
                return {1: 0, 2: 0, 3: 0, 4: 0}
            drop = 20 if tp.inputs.get("Enabled3") else 0
            return {1: 100 + 10 * shown[0] - 2, 2: 500 - drop - 2, 3: 100 + 10 * (shown[-1] + 1) + 2, 4: 530 + 2}

    def __init__(self, text):
        self.text = text
        self.inputs = {"Start": 0.0, "End": 1.0, "Enabled1": 1, "Enabled3": 1}
        self.Output = self.Output(self)

    def GetInput(self, name):
        return self.inputs.get(name)

    def SetInput(self, name, value):
        self.inputs[name] = value


class FakeComp:
    def GetPrefs(self, key):
        return {"Comp.FrameFormat.Width": 1000, "Comp.FrameFormat.Height": 1000}[key]

    def GetAttrs(self):
        return {"COMPN_CurrentTime": 0}


class MeasureTests(unittest.TestCase):
    def test_each_word_is_read_alone_with_only_its_fill_and_all_is_put_back(self):
        tp = FakeTextPlus("ab cde")
        ink = hr.measure(FakeComp(), tp, tp.text, 12)
        self.assertEqual(len(ink), 2)
        cx, cy, w, h = ink[1]
        self.assertAlmostEqual(cx, (130 + 160) / 2 / 1000)  # "cde": characters 3-5
        self.assertAlmostEqual(w, 30 / 1000)
        self.assertAlmostEqual(cy, 515 / 1000)              # the shadow off while measuring
        self.assertAlmostEqual(h, 30 / 1000)
        self.assertEqual((tp.inputs["Start"], tp.inputs["End"], tp.inputs["Enabled3"]), (0.0, 1.0, 1))


class Reel(Timeline):
    def GetStartFrame(self):
        return 1000                 # where on_timeline puts its clips


INK = [(0.4, 0.5, 0.1, 0.05), (0.6, 0.5, 0.1, 0.05)]


def highlighted(text="ab cd", **options):
    """A lone Text+ clip with the highlight built on it (no reload, so the Text+ stays the one returned)."""
    item = on_timeline(text_clip(text))
    comp, tp = item.comps[-1], text_tool(item)
    tp.SetInput("Size", 0.12)
    hr._build(comp, tp, text, [0.0, 12.0], INK, hl.clean_options({"pop": "off", **options}), 0.0, 120, 24.0,
              "even", [0.0, 0.5])
    return item, tp


class StaleTests(unittest.TestCase):
    """A highlight remembers what its boxes were measured for; Refresh measures them again."""

    def test_measured_is_the_words_and_the_inputs_they_sit_by_rounded(self):
        tp = text_tool(on_timeline(text_clip()))
        tp.SetInput("Size", 0.123456)
        tp.SetInput("Center", {1: 0.5, 2: 0.25})
        got = hr.measured(tp, "ab cd")
        self.assertEqual(set(got), {"text", *hr.MEASURED})
        self.assertEqual((got["text"], got["Size"], got["Center"]), ("ab cd", 0.1235, [0.5, 0.25]))
        self.assertIsNone(got["Font"])                      # an input Resolve has no value for

    def test_a_highlight_records_what_it_was_measured_for(self):
        _item, tp = highlighted()
        kept = hr.record(tp)["measured"]
        self.assertEqual((kept["text"], kept["Size"]), ("ab cd", 0.12))

    def test_a_record_without_a_measurement_is_never_stale(self):
        _item, tp = highlighted()
        rec = hr.record(tp)
        del rec["measured"]                                 # an earlier Buddy's
        tp.SetInput("Size", 0.5)
        self.assertFalse(hr.stale(tp, rec))
        self.assertFalse(hr.stale(tp, None))

    def test_it_is_stale_once_the_size_or_the_words_change(self):
        _item, tp = highlighted()
        rec = hr.record(tp)
        self.assertFalse(hr.stale(tp, rec))
        tp.SetInput("Size", 0.2)
        self.assertTrue(hr.stale(tp, rec))
        tp.SetInput("Size", 0.12)
        self.assertFalse(hr.stale(tp, rec))
        tp.SetInput("StyledText", "ab cd ef")
        self.assertTrue(hr.stale(tp, rec))

    def test_the_summary_counts_the_highlights_that_are_stale(self):
        fresh, _tp = highlighted()
        moved, moved_tp = highlighted()
        moved_tp.SetInput("Size", 0.3)
        plain = on_timeline(text_clip())
        got = hr.summary(controller(Reel(fresh, moved, plain)))
        self.assertEqual((got["titles"], got["highlighted"], got["stale"]), (3, 2, 1))
        self.assertEqual(hr.summary(controller(Reel(fresh)))["stale"], 0)
        self.assertEqual(hr.summary(controller(None))["stale"], 0)


class RefreshTests(unittest.TestCase):
    """run_apply with no options: every highlighted clip again, with its own."""

    def lit(self, **options):
        item = on_timeline(text_clip())
        with mock.patch.object(hr, "measure", return_value=INK):
            hr.apply(item, lambda text, kept: ([0.0, 12.0], "even"), hl.clean_options(options), 24.0)
        return item

    def test_each_clip_is_done_again_with_the_options_of_its_own_record(self):
        box, color, plain = self.lit(look="box", pop="off"), self.lit(look="color", pop="off"), on_timeline(text_clip())
        text_tool(box).SetInput("Size", 0.3)                # its words are another size now
        self.assertTrue(hr.stale(text_tool(box), hr.record(text_tool(box))))
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(hr, "measure", return_value=INK):
            got = hr.run_apply(controller(Reel(box, color, plain)), None, folder)
        self.assertEqual((got["applied"], got["other"], got["failed"]), (2, 1, []))
        looks = [hr.record(text_tool(item))["options"]["look"] for item in (box, color)]
        self.assertEqual(looks, ["box", "color"])
        self.assertIsNone(hr.record(text_tool(plain)))      # no highlight, none put on
        self.assertFalse(hr.stale(text_tool(box), hr.record(text_tool(box))))     # measured for the new size


class FeedTests(unittest.TestCase):
    """What Buddy's highlight tools hand back to when they come off."""

    def between(self, look="both"):
        """Text+ -> Glow (put in Fusion) -> Highlight's tools -> MediaOut1."""
        item, tp = highlighted(look=look)
        comp = tp.comp
        glow = comp.AddTool("Glow")
        glow.ConnectInput("Input", tp)
        for tool in comp.tools.values():
            for inp in list(tool.inputs.values()):
                if tool is not glow and hr._ours(tool) and inp.source is tp.Output:
                    inp.ConnectTo(glow.Output)
        return comp, tp, glow

    def test_a_tool_put_between_the_text_plus_and_the_highlight_stays_in_the_chain(self):
        comp, tp, glow = self.between()
        self.assertIs(comp.FindTool("MediaOut1").Input.source.tool, comp.FindTool(hr.MERGE_NAME))
        hr._take_out(comp, tp)
        self.assertIs(comp.FindTool("MediaOut1").Input.source.tool, glow)
        self.assertIs(glow.Input.source.tool, tp)
        self.assertIsNone(comp.FindTool(hr.MERGE_NAME))

    def test_without_one_it_goes_back_to_the_text_plus(self):
        item, tp = highlighted()
        hr._take_out(tp.comp, tp)
        self.assertIs(tp.comp.FindTool("MediaOut1").Input.source.tool, tp)

    def test_two_tools_between_leave_it_to_the_text_plus(self):
        comp, tp, glow = self.between()
        blur = comp.AddTool("Blur")
        comp.FindTool(hr.MERGE_NAME).ConnectInput("Background", blur)
        self.assertIs(hr._feed([t for t in comp.tools.values() if hr._ours(t)], tp), tp.Output)

    def test_the_text_plus_and_the_masks_paths_are_no_tool_between(self):
        _item, tp = highlighted()
        ours = [t for t in tp.comp.tools.values() if hr._ours(t)]
        self.assertIs(hr._feed(ours, tp), tp.Output)


class SummaryTests(unittest.TestCase):
    def say(self, **kw):
        from pages.text_animator.page import highlight_summary
        return highlight_summary({"applied": 0, "spoken": 0, "even": 0, "popless": 0, "other": 0, "failed": [], **kw})

    def test_what_it_says(self):
        self.assertIn("Select the Text+ captions", self.say()[0])
        self.assertIn("None of the selected clips is a Text+", self.say(other=2)[0])
        self.assertEqual(self.say(applied=1, spoken=1), ("The highlight is on 1 clip, timed from the transcript.", None))
        self.assertIn("share each clip's time evenly", self.say(applied=3, even=3)[0])
        self.assertIn("3 clips – 2 timed from the transcript, 1 evenly", self.say(applied=3, spoken=2, even=1)[0])
        # Animate by, a letter animation, character styling: whatever holds the text, said as one thing.
        self.assertIn("1 of the clips already has an animation or styling on its text",
                      self.say(applied=2, spoken=2, popless=1)[1])
        self.assertIn("3 of the clips already have", self.say(applied=4, spoken=4, popless=3)[1])

    def test_choices_are_checked_and_kept(self):
        from pages.text_animator.page import AnimationPage, DEFAULTS
        names = ("on_hl_option", "_hl", "_save")
        Page = type("Page", (), {n: getattr(AnimationPage, n) for n in names})
        page = Page()

        class Mem(dict):
            def save(self):
                pass
        page.settings = Mem(DEFAULTS)
        page._push_state = lambda: None
        page.on_hl_option({"key": "look", "value": "box"})
        page.on_hl_option({"key": "box_color", "value": "#00FF00"})
        page.on_hl_option({"key": "pop", "value": "enormous"})
        page.on_hl_option({"key": "nonsense", "value": "x"})
        page.on_hl_option({"key": "word_color", "value": 5})
        self.assertEqual(page._hl(), {**hl.DEFAULTS, "look": "box", "box_color": "#00ff00"})


class TimingTests(unittest.TestCase):
    """The Presets tab's timing strip: starts set by hand."""

    def test_hand_starts_land_on_frames_in_order_inside_the_clip(self):
        got = hr.clean_starts([0.01, 0.0, 0.5, 9.0], 4, 2.0, 24)
        self.assertEqual([round(t * 24) for t in got], [0, 1, 12, 47])     # a frame apart, the last on the last frame
        with self.assertRaises(ValueError):
            hr.clean_starts([0.0, 0.5], 3, 2.0, 24)                        # timing for other words
        with self.assertRaises(ValueError):
            hr.clean_starts([0.0, "soon"], 2, 2.0, 24)

    def test_hand_timing_is_kept_only_for_the_text_it_was_set_for(self):
        rec = {"timing": "manual", "text": TEXT, "starts": [0, .1, .2, .3, .4, .5]}
        self.assertEqual(hr.kept_starts(rec, TEXT), [0.0, .1, .2, .3, .4, .5])
        self.assertIsNone(hr.kept_starts(rec, TEXT + " too"))              # the words changed
        self.assertIsNone(hr.kept_starts({**rec, "timing": "transcript"}, TEXT))
        self.assertIsNone(hr.kept_starts({**rec, "starts": [0, .1]}, TEXT))
        self.assertIsNone(hr.kept_starts(None, TEXT))

    def test_what_it_says_with_hand_timing(self):
        from pages.text_animator.page import highlight_summary
        base = {"applied": 0, "hand": 0, "spoken": 0, "even": 0, "popless": 0, "other": 0, "failed": []}
        say = lambda **kw: highlight_summary({**base, **kw})[0]
        self.assertEqual(say(applied=1, hand=1), "The highlight is on 1 clip, with the timing you set.")
        self.assertEqual(say(applied=3, hand=3), "The highlight is on 3 clips, with the timing you set.")
        self.assertEqual(say(applied=3, hand=1, spoken=2),
                         "The highlight is on 3 clips – 1 with the timing you set, 2 timed automatically.")

    def test_the_timing_strip_only_sends_what_it_should(self):
        from pages.text_animator.page import AnimationPage
        Page = type("Page", (), {n: getattr(AnimationPage, n) for n in ("on_hl_timing", "_hl", "_done_timing")})
        page = Page()
        page.settings = {"highlight": {}}
        page.started = []
        page._start = lambda what, job, done: page.started.append(what)
        page.on_hl_timing({"id": 5, "starts": [0]})
        page.on_hl_timing({"id": "abc", "starts": "0,1"})
        page.on_hl_timing({"id": "abc", "starts": [0, 0.5]})
        self.assertEqual(page.started, ["timing"])


class TabTests(unittest.TestCase):
    """The highlight's tab is labelled Presets (its id is still textplus, so
    a remembered tab still opens it); Titles is a new tab, empty for now."""

    def test_the_page_draws_the_tabs_it_takes(self):
        from pages.text_animator.page import TABS
        html = (_paths.APP / "pages" / "text_animator" / "web" / "index.html").read_text(encoding="utf-8")
        nav = html[html.index('id="sections"'):html.index("</nav>")]
        tabs = re.findall(r'data-tab="(\w+)"[^>]*>([^<]+)<', nav)
        self.assertEqual([t for t, _label in tabs], list(TABS))
        self.assertEqual(dict(tabs)["textplus"], "Presets")
        self.assertEqual(dict(tabs)["titles"], "Titles")
        self.assertIn('id="panel-titles"', html)

    def test_titles_is_remembered_but_reads_no_selection(self):
        from pages.text_animator.page import AnimationPage
        saved, reads = [], []
        page = type("Page", (), {"on_tab": AnimationPage.on_tab})()
        page._player, page._save, page._read_selection = None, lambda *kv: saved.append(kv), lambda: reads.append(1)
        page._read_titles_timeline = lambda: None
        page.on_tab({"tab": "titles"})
        self.assertEqual((saved, reads), ([("tab", "titles")], []))
        page.on_tab({"tab": "textplus"})
        self.assertEqual(reads, [1])


if __name__ == "__main__":
    unittest.main()
