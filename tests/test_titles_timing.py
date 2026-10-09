"""Animation > Titles: the one clock (titles/timing.py) - entrances that start unseen
and land at rest, the stagger by word, line or all at once, the key word's beat,
spoken times, the exit, and keyframes that reproduce the clock on every frame."""

import unittest

import _paths  # noqa: F401
from pages.text_animator.titles import composer as C
from pages.text_animator.titles import timing as T


def fake(text, font, size):
    return len(text) * size * 0.55


def layout(text="3 secrets nobody tells you", look="hormozi", **doc_changes):
    doc = C.new_doc(text, look)
    doc.update(doc_changes)
    return doc, C.compose(doc, (1920, 1080), fake)


class EntranceTests(unittest.TestCase):
    def test_every_entrance_starts_unseen_and_lands_at_rest(self):
        for kind in T.KINDS:
            for direction in T.DIRECTIONS:
                start, end = T.entrance(kind, 0.0, direction), T.entrance(kind, 1.0, direction)
                self.assertEqual(start["alpha"], 0.0, kind)
                self.assertEqual(end, T.REST, (kind, direction))

    def test_never_scale_zero(self):
        for kind in T.KINDS:
            for p in (0, 0.01, 0.5, 1):
                self.assertGreaterEqual(T.entrance(kind, p)["scale"], T.MIN_SCALE)

    def test_pop_overshoots_and_slam_lands_hard(self):
        pop = [T.entrance("pop", p / 20)["scale"] for p in range(21)]
        self.assertAlmostEqual(pop[0], 0.6)
        self.assertGreater(max(pop), 1.07)
        slam = [T.entrance("slam", p / 20)["scale"] for p in range(21)]
        self.assertAlmostEqual(slam[0], 2.1)
        self.assertLess(min(slam), 0.95)
        self.assertGreater(T.entrance("slam", 0.1)["blur"], 0)

    def test_a_slide_moves_the_way_its_direction_says(self):
        up = T.entrance("slide", 0.0, "u")
        self.assertGreater(up["dy"], 0)                    # starts below, moves up
        self.assertAlmostEqual(up["dy"], T.SLIDE)
        right = T.entrance("slide", 0.0, "r")
        self.assertLess(right["dx"], 0)                    # starts left, moves right


class ClockTests(unittest.TestCase):
    def test_words_in_reading_order_and_the_key_on_a_beat(self):
        doc, lay = layout()
        clock = T.timing(doc, lay, 3.0)
        starts = {w["id"]: w["start"] for w in clock["words"]}
        ids = [b["id"] for b in lay["boxes"]]
        for i, wid in enumerate(ids):
            want = i * 0.1 + (T.KEY_BEAT if wid == "3#1" else 0)
            self.assertAlmostEqual(starts[wid], want)
        self.assertAlmostEqual(clock["intro"], max(w["start"] + w["len"] for w in clock["words"]))

    def test_by_line_and_all_at_once(self):
        doc, lay = layout(plays="line")
        clock = T.timing(doc, lay, 3.0)
        for w, b in zip(clock["words"], lay["boxes"]):
            want = b["line"] * 0.1 * T.LINE_STEP + (T.KEY_BEAT if b["role"] == "key" else 0)
            self.assertAlmostEqual(w["start"], want)
        doc, lay = layout(plays="all")
        self.assertEqual({round(w["start"], 6) for w in T.timing(doc, lay, 3.0)["words"] if w["id"] != "3#1"}, {0.0})

    def test_spoken_times_win(self):
        doc, lay = layout(times={"tells#1": 1.25})
        clock = T.timing(doc, lay, 3.0)
        self.assertEqual(next(w["start"] for w in clock["words"] if w["id"] == "tells#1"), 1.25)

    def test_the_cascade_of_animations(self):
        doc, lay = layout(anim="slide", roleAnim={"key": "slam"})
        doc["over"]["anim"] = {"tells#1": "fade"}
        kinds = {w["id"]: w["kind"] for w in T.timing(doc, lay, 3.0)["words"]}
        self.assertEqual(kinds["3#1"], "slam")
        self.assertEqual(kinds["tells#1"], "fade")
        self.assertEqual(kinds["secrets#1"], "slide")
        doc, lay = layout()
        self.assertEqual({w["kind"] for w in T.timing(doc, lay, 3.0)["words"]}, {"pop"})   # the look's

    def test_the_exit_ends_with_the_title(self):
        for order in ("same", "reverse", "all"):
            doc, lay = layout(exit={"on": True, "style": "reverse", "order": order})
            clock = T.timing(doc, lay, 3.0)
            ends = [w["exit"]["at"] + w["exit"]["len"] for w in clock["words"]]
            self.assertAlmostEqual(max(ends), 3.0, places=6, msg=order)
            for w in clock["words"]:
                self.assertAlmostEqual(T.state(w, 3.0)["alpha"], 0.0)    # gone at the end
                self.assertEqual(T.state(w, clock["intro"] + 0.01), T.REST)  # held between

    def test_a_reverse_exit_plays_the_entrance_backwards(self):
        doc, lay = layout(anim="slide", exit={"on": True, "style": "reverse", "order": "same"})
        w = T.timing(doc, lay, 3.0)["words"][1]
        p = 0.3
        got, want = T.state(w, w["exit"]["at"] + p * w["exit"]["len"]), T.entrance("slide", 1 - p, "u")
        for channel in T.REST:
            self.assertAlmostEqual(got[channel], want[channel], places=9)

    def test_a_words_own_exit_time_and_length(self):
        exit_on = {"on": True, "style": "fade", "order": "same"}
        doc, lay = layout(exit=exit_on)
        before = {w["id"]: w["exit"] for w in T.timing(doc, lay, 3.0)["words"]}
        doc, lay = layout(exit=exit_on, exitTimes={"nobody#1": 1.6})
        doc["over"]["exitLen"] = {"nobody#1": 0.5}
        after = {w["id"]: w for w in T.timing(doc, lay, 3.0)["words"]}
        self.assertAlmostEqual(after["nobody#1"]["exit"]["at"], 1.6)
        self.assertAlmostEqual(after["nobody#1"]["exit"]["len"], 0.5)
        for wid in ("3#1", "secrets#1", "tells#1", "you#1"):              # the others keep theirs
            self.assertAlmostEqual(after[wid]["exit"]["at"], before[wid]["at"])
            self.assertAlmostEqual(after[wid]["exit"]["len"], before[wid]["len"])
        # Never before its entrance has played, never past the title's end.
        doc["exitTimes"] = {"nobody#1": 0.0, "you#1": 9.0}
        after = {w["id"]: w for w in T.timing(doc, lay, 3.0)["words"]}
        w = after["nobody#1"]
        self.assertAlmostEqual(w["exit"]["at"], w["start"] + w["len"])
        self.assertAlmostEqual(after["you#1"]["exit"]["at"] + after["you#1"]["exit"]["len"], 3.0)

    def test_exits_of_their_own_and_which_way_they_go(self):
        on = {"on": True, "style": "slide", "order": "same"}
        doc, lay = layout(exit=on)
        w = {x["id"]: x for x in T.timing(doc, lay, 3.0)["words"]}
        # By default it leaves the way it came in (up); reversed, back where it came from.
        self.assertEqual((w["3#1"]["exit"]["towards"], w["3#1"]["exit"]["style"]), ("u", "slide"))
        doc["over"]["exitAnim"] = {"3#1": "reverse", "secrets#1": "fade"}
        doc["over"]["exitDir"] = {"nobody#1": "l"}
        gone = {"len": 0.4, "ch": {"alpha": [{"t": 0, "v": 1, "hi": [0, 0], "ho": [1 / 3, 0]}, {"t": 1, "v": 0, "hi": [-1 / 3, 0], "ho": [0, 0]}]}}
        doc["over"]["exitCurves"] = {"tells#1": gone}
        w = {x["id"]: x for x in T.timing(doc, lay, 3.0)["words"]}
        self.assertEqual((w["3#1"]["exit"]["style"], w["3#1"]["exit"]["towards"]), ("reverse", "d"))
        self.assertEqual(w["secrets#1"]["exit"]["style"], "fade")
        self.assertEqual(w["nobody#1"]["exit"]["towards"], "l")
        # Leaving to the left: it ends up left of where it was.
        ex = w["nobody#1"]["exit"]
        self.assertLess(T.state(w["nobody#1"], ex["at"] + ex["len"] * 0.99)["dx"], 0)
        # Its own drawing, from at rest to gone, as it plays - its length too.
        ex = w["tells#1"]["exit"]
        self.assertEqual((ex["style"], ex["drawn"], ex["len"]), ("custom", "word", 0.4))
        self.assertAlmostEqual(T.state(w["tells#1"], ex["at"] + 0.25 * ex["len"])["alpha"], 0.84375)   # flat handles: eased
        # The title's own drawing, for every word without its own exit.
        doc["exitCurves"] = gone
        w = {x["id"]: x for x in T.timing(doc, lay, 3.0)["words"]}
        self.assertEqual((w["you#1"]["exit"]["drawn"], w["3#1"]["exit"]["style"]), ("title", "reverse"))
        doc["exit"] = {**on, "dir": "r"}
        w = {x["id"]: x for x in T.timing(doc, lay, 3.0)["words"]}
        self.assertEqual((w["you#1"]["exit"]["towards"], w["nobody#1"]["exit"]["towards"]), ("r", "l"))

    def test_too_short_a_title_still_plays_everything(self):
        doc, lay = layout(exit={"on": True, "style": "fade", "order": "same"})
        clock = T.timing(doc, lay, 0.2)
        for w in clock["words"]:
            self.assertGreaterEqual(w["exit"]["at"], clock["intro"] - 1e-9)
        self.assertGreater(T.min_duration(clock), clock["intro"] + clock["exit"])


class SpreadTests(unittest.TestCase):
    """Copies that fan out (looks with "anim": "spread" copies - Echo's key word)."""

    def echo(self, **doc_changes):
        doc, lay = layout("You need to STOP doing this", "echo", **doc_changes)
        clock = T.timing(doc, lay, 3.0)
        key = next(i for i, b in enumerate(lay["boxes"]) if b["role"] == "key")
        return clock, clock["words"][key], [w for i, w in enumerate(clock["words"]) if i != key]

    def test_the_copies_leave_once_the_word_is_half_in(self):
        clock, key, others = self.echo()
        fan = key["spread"]
        self.assertAlmostEqual(fan["start"], key["start"] + T.COPY_START * key["len"])
        self.assertAlmostEqual(fan["len"], T.COPY_TRAVEL + T.COPY_LAG)          # 2 copies a side
        self.assertTrue(all(w["spread"] is None for w in others))              # no copies, no spread
        self.assertGreaterEqual(clock["intro"], fan["start"] + fan["len"] - 1e-9)
        self.assertEqual(T.state(key, fan["start"])["spread"], 0.0)
        self.assertEqual(T.state(key, fan["start"] + fan["len"])["spread"], 1.0)
        self.assertEqual(T.state(others[0], 0.0)["spread"], 1.0)                # at rest

    def test_the_nearest_copy_goes_first_and_all_land(self):
        group = {"n": 2, "anim": "spread"}
        for s in (0.1, 0.3, 0.6):
            near, far = T.copy_at(group, 1, s), T.copy_at(group, 2, s)
            self.assertGreaterEqual(near[0], far[0])
        self.assertEqual(T.copy_at(group, 1, 0.0), (0.0, 0.0))                  # behind the word, unseen
        self.assertEqual(T.copy_at(group, 2, 1.0), (1.0, 1.0))
        self.assertEqual(T.copy_at({"n": 2}, 2, 0.0), (1.0, 1.0))               # a still group stays put
        out = [T.copy_at(group, 2, s / 20)[0] for s in range(21)]
        self.assertEqual(out, sorted(out))

    def test_the_copies_fold_back_in_as_the_word_leaves(self):
        _clock, key, _others = self.echo(exit={"on": True, "style": "reverse", "order": "same"})
        out = key["exit"]
        self.assertEqual(T.state(key, out["at"] - 0.01)["spread"], 1.0)
        self.assertAlmostEqual(T.state(key, out["at"] + out["len"] / 2)["spread"], 0.5)
        self.assertAlmostEqual(T.state(key, out["at"] + out["len"])["spread"], 0.0)

    def test_a_pulse_breathes_through_the_hold_and_ends_where_the_exit_starts(self):
        _clock, key, others = self.echo(pulse=True, exit={"on": True, "style": "reverse", "order": "same"})
        beat, fan = key["pulse"], key["spread"]
        self.assertAlmostEqual(beat["start"], fan["start"] + fan["len"])
        self.assertAlmostEqual(beat["start"] + beat["len"], key["exit"]["at"])
        self.assertEqual(beat["breaths"], max(1, round(beat["len"] / T.PULSE_PERIOD)))
        self.assertTrue(all(w["pulse"] is None for w in others))
        at = lambda p: T.state(key, beat["start"] + p * beat["len"])["pulse"]   # noqa: E731
        self.assertEqual((at(0.0), at(1.0)), (0.0, 0.0))                      # nothing jumps
        self.assertAlmostEqual(at(0.5 / beat["breaths"]), 1.0)                 # the furthest in
        # A breath takes the copies in and dims them; a still group doesn't breathe.
        group = {"n": 2, "anim": "spread"}
        out, shown = T.copy_at(group, 2, 1.0, 1.0)
        self.assertAlmostEqual(out, 1 - T.PULSE_IN)
        self.assertAlmostEqual(shown, 1 - T.PULSE_DIM)
        self.assertEqual(T.copy_at({"n": 2}, 2, 1.0, 1.0), (1.0, 1.0))
        self.assertIn("pulse", T.keys(key, 30, 90))

    def test_no_pulse_unless_asked_or_too_short_a_hold(self):
        _clock, key, _others = self.echo()
        self.assertIsNone(key["pulse"])
        doc, lay = layout("You need to STOP doing this", "echo", pulse=True)
        clock = T.timing(doc, lay, 1.2)
        self.assertTrue(all(w["pulse"] is None for w in clock["words"]))

    def test_the_spread_is_keyed_too(self):
        _clock, key, _others = self.echo(exit={"on": True, "style": "reverse", "order": "same"})
        keys = T.keys(key, 30, 90)
        self.assertIn("spread", keys)
        for f, v in keys["spread"]:
            self.assertAlmostEqual(v, T.state(key, f / 30)["spread"], places=5)


class KeyTests(unittest.TestCase):
    def test_linear_keys_reproduce_the_clock_on_every_frame(self):
        doc, lay = layout(anim="slam", exit={"on": True, "style": "slide", "order": "reverse"}, dir="l")
        fps, frames = 24, 72
        for w in T.timing(doc, lay, frames / fps)["words"]:
            keys = T.keys(w, fps, frames)
            self.assertIn("alpha", keys)
            for channel, pts in keys.items():
                for f in range(frames):
                    want = T.state(w, f / fps)[channel]
                    if f <= pts[0][0]:
                        got = pts[0][1]
                    elif f >= pts[-1][0]:
                        got = pts[-1][1]
                    else:
                        (f0, v0), (f1, v1) = next((a, b) for a, b in zip(pts, pts[1:]) if a[0] <= f <= b[0])
                        got = v0 + (v1 - v0) * (f - f0) / (f1 - f0)
                    self.assertAlmostEqual(got, want, places=5, msg=(w["id"], channel, f))

    def test_a_still_channel_isnt_keyed(self):
        doc, lay = layout(anim="fade")
        keys = T.keys(T.timing(doc, lay, 3.0)["words"][0], 30, 90)
        self.assertEqual(set(keys), {"alpha"})


def _old_entrance(kind, p, direction="u"):
    """The entrances as formulas, as they were before they became curves (2026-10-05)."""
    c, eo, ei, eio = T._clamp, T.ease_out, T.ease_in, T.ease_in_out
    p = c(p)
    s = dict(T.REST)
    if kind == "pop":
        s["alpha"] = c(p / 0.33)
        s["scale"] = (0.6 + 0.48 * eo(p / 0.6)) if p < 0.6 else (1.08 - 0.08 * eio((p - 0.6) / 0.4))
    elif kind == "slam":
        s["alpha"] = c(p / 0.25)
        s["scale"] = (2.1 - 1.16 * ei(p / 0.65)) if p < 0.65 else (0.94 + 0.06 * eo((p - 0.65) / 0.35))
        s["blur"] = 0.12 * (1 - c(p / 0.5))
    elif kind == "slide":
        vx, vy = T.DIRECTIONS[direction]
        left = 1 - eo(p)
        s["dx"], s["dy"] = -vx * T.SLIDE * left, -vy * T.SLIDE * left
        s["alpha"] = c(p / 0.4)
        s["blur"] = 0.06 * left
    elif kind == "blur":
        vx, vy = T.DIRECTIONS[direction]
        left = 1 - eo(p)
        s["blur"] = 0.35 * left
        s["dx"], s["dy"] = -vx * 0.02 * left, -vy * 0.02 * left
        s["alpha"] = c(p / 0.5)
    elif kind == "type":
        s["alpha"] = 1.0 if p >= 1 else 0.0
    else:
        s["alpha"] = eio(p)
    s["scale"] = max(s["scale"], T.MIN_SCALE)
    return s


SPIN = {"len": 0.5, "ch": {"rot": [{"t": 0, "v": -30, "hi": [0, 0], "ho": [0.1, 20]},
                                   {"t": 0.7, "v": 4, "hi": [-0.05, 0], "ho": [0.1, 0]},
                                   {"t": 1, "v": 0, "hi": [-0.1, 0], "ho": [0, 0]}],
                           "alpha": [{"t": 0, "v": 0}, {"t": 0.3, "v": 1}, {"t": 1, "v": 1}]}}


class CurveTests(unittest.TestCase):
    def test_the_built_in_curves_are_exactly_the_old_easings(self):
        for kind in T.KINDS:
            for direction in T.DIRECTIONS:
                for i in range(401):
                    want, got = _old_entrance(kind, i / 400, direction), T.entrance(kind, i / 400, direction)
                    for channel in want:
                        self.assertAlmostEqual(got[channel], want[channel], places=12, msg=(kind, direction, i, channel))

    def test_a_drawn_curve_is_cleaned(self):
        self.assertIsNone(T.clean_curve({"ch": {"scale": [{"t": 0, "v": 1}]}}))           # one key isn't a curve
        # Keys out of order in the middle (the ends are pinned to the entrance's ends).
        self.assertIsNone(T.clean_curve({"ch": {"scale": [{"t": 0, "v": 1}, {"t": 0.6, "v": 2}, {"t": 0.3, "v": 2}, {"t": 1, "v": 1}]}}))
        self.assertIsNone(T.clean_curve("spin"))
        c = T.clean_curve({"len": 99, "ch": {"scale": [{"t": 0.2, "v": 2, "ho": [5, 1]}, {"t": 0.4, "v": 1.5, "hi": [-5, 0]},
                                                       {"t": 0.9, "v": 1}], "nope": [{"t": 0, "v": 0}, {"t": 1, "v": 1}]}})
        self.assertEqual(c["len"], 4.0)
        self.assertEqual(list(c["ch"]), ["scale"])
        keys = c["ch"]["scale"]
        self.assertEqual((keys[0]["t"], keys[-1]["t"]), (0.0, 1.0))      # stretched to the whole entrance
        self.assertAlmostEqual(keys[0]["ho"][0], 0.4)                     # no further than the next key
        self.assertAlmostEqual(keys[1]["hi"][0], -0.4)
        self.assertIsNone(T.clean_curve({"ch": {"scale": [{"t": 0, "v": float("nan")}, {"t": 1, "v": 1}]}}))

    def test_free_handles_still_go_forward_in_time(self):
        keys = T.clean_curve(SPIN)["ch"]["rot"]
        last = T.eval_keys(keys, 0.0)
        self.assertEqual(last, -30)
        values = [T.eval_keys(keys, i / 200) for i in range(201)]
        self.assertEqual(values[-1], 0)
        self.assertGreater(max(values), 3.5)             # the overshoot past rest is drawn
        # A step key holds until the next.
        step = [{"t": 0, "v": 0, "hi": [0, 0], "ho": [0, 0], "step": True}, {"t": 1, "v": 1, "hi": [0, 0], "ho": [0, 0]}]
        self.assertEqual((T.eval_keys(step, 0.99), T.eval_keys(step, 1.0)), (0, 1))

    def test_whose_curve_a_word_plays(self):
        doc, lay = layout(curves=SPIN)
        words = {w["id"]: w for w in T.timing(doc, lay, 3.0)["words"]}
        self.assertTrue(all(w["kind"] == "custom" and w["drawn"] == "title" and w["len"] == 0.5 for w in words.values()))
        own = {"len": 0.2, "ch": {"scale": [{"t": 0, "v": 3}, {"t": 1, "v": 1}]}}
        doc["over"]["curves"] = {"nobody#1": own}
        doc["over"]["anim"] = {"tells#1": "slam"}
        words = {w["id"]: w for w in T.timing(doc, lay, 3.0)["words"]}
        self.assertEqual((words["nobody#1"]["drawn"], words["nobody#1"]["len"]), ("word", 0.2))
        self.assertEqual((words["tells#1"]["kind"], words["tells#1"]["drawn"], words["tells#1"]["curve"]), ("slam", None, None))
        self.assertEqual(words["you#1"]["drawn"], "title")
        self.assertAlmostEqual(T.state(words["nobody#1"], words["nobody#1"]["start"])["scale"], 3.0)

    def test_a_words_own_length_and_direction(self):
        doc, lay = layout(anim="slide")
        doc["over"]["len"] = {"you#1": 0.6}
        doc["over"]["dir"] = {"you#1": "r"}
        w = next(w for w in T.timing(doc, lay, 3.0)["words"] if w["id"] == "you#1")
        self.assertEqual((w["len"], w["dir"]), (0.6, "r"))
        self.assertLess(T.state(w, w["start"] + 0.01)["dx"], 0)        # comes in from the left, moving right

    def test_a_reversed_exit_plays_the_words_own_curve_backwards(self):
        doc, lay = layout(curves=SPIN, exit={"on": True, "style": "reverse", "order": "same"})
        w = T.timing(doc, lay, 3.0)["words"][0]
        self.assertEqual(w["exit"]["len"], 0.5)
        for p in (0.1, 0.5, 0.9):
            got = T.state(w, w["exit"]["at"] + p * 0.5)
            want = T.apply_curve(T.clean_curve(SPIN), 1 - p, w["dir"])
            self.assertAlmostEqual(got["rot"], want["rot"])
            self.assertAlmostEqual(got["alpha"], want["alpha"])

    def test_a_drawn_curve_is_keyed_frame_for_frame(self):
        doc, lay = layout(curves=SPIN)
        fps, frames = 30, 90
        for w in T.timing(doc, lay, frames / fps)["words"]:
            keys = T.keys(w, fps, frames)
            self.assertIn("rot", keys)
            for f, v in keys["rot"]:
                self.assertAlmostEqual(v, T.state(w, f / fps)["rot"], places=5)


if __name__ == "__main__":
    unittest.main()
