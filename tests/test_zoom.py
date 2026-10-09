"""Animation > Zoom: the zoom shapes, the framing maths, the keys for Fusion
and how zooms are kept on a clip (zoom.py), plus what the page accepts -
no Resolve, no Qt."""

import math
import unittest

import _paths  # noqa: F401
from pages.text_animator import zoom as zm


def shown(zooms, t, aspect=16 / 9, duration=10):
    """How far outside the picture the frame reaches at t (<= 0: not at all) -
    each frame corner taken back into the picture."""
    s, cx, cy, deg = zm.at(zooms, t, duration, aspect)
    th = math.radians(deg)
    worst = -1.0
    for px, py in ((0, 0), (1, 0), (0, 1), (1, 1)):
        dx, dy = (px - cx) * aspect, -(py - cy)
        ux = (dx * math.cos(th) + dy * math.sin(th)) / s / aspect + .5
        uy = .5 - (-dx * math.sin(th) + dy * math.cos(th)) / s
        worst = max(worst, -ux, ux - 1, -uy, uy - 1)
    return worst


def region(zooms, t, duration=10):
    """(left, right, top, bottom) of the picture on screen, untilted, shares."""
    s, cx, cy, _deg = zm.at(zooms, t, duration)
    return (0 - cx) / s + .5, (1 - cx) / s + .5, (0 - cy) / s + .5, (1 - cy) / s + .5


class ShapeTests(unittest.TestCase):
    def test_each_kind_starts_as_described(self):
        self.assertEqual(zm.new("punch", 1.0, 10.0)["in"], 0.0)
        push = zm.new("push", 1.0, 10.0)
        self.assertEqual((push["in"], push["out"]), (0.6, 0.0))
        pull = zm.new("pullout", 1.0, 10.0)
        self.assertEqual(pull["out"], pull["end"] - pull["start"])          # eases back out over all of it
        creep = zm.new("creep", 0.0, 10.0)
        self.assertEqual((creep["start"], creep["end"], creep["in"]), (0.0, 10.0, 10.0))

    def test_a_new_zoom_fits_before_the_next(self):
        z = zm.new("inout", 1.0, 10.0, room_end=2.0)
        self.assertEqual(z["end"], 2.0)
        self.assertLessEqual(z["in"] + z["out"], 1.0)
        self.assertIsNone(zm.new("push", 1.95, 10.0, room_end=2.0))         # no room

    def test_cleaning_keeps_zooms_apart_inside_the_clip_and_the_picture(self):
        zooms = zm.clean([{"kind": "push", "start": 2, "end": 5, "in": 9, "out": 9, "scale": 9, "x": 1.2, "y": -1},
                          {"kind": "nonsense", "start": 1, "end": 3, "scale": 1.5},
                          "not a zoom", {"start": 4.99, "end": 5.0}], 5.0)
        self.assertEqual([z["start"] for z in zooms], [1, 3])               # the second moved after the first
        first, second = zooms
        self.assertEqual(first["kind"], "push")                             # an unknown kind becomes a push
        self.assertEqual(second["scale"], zm.MAX_SCALE)
        self.assertLessEqual(second["in"] + second["out"], second["end"] - second["start"])
        self.assertEqual((second["x"], second["y"]), (1 - .5 / zm.MAX_SCALE, .5 / zm.MAX_SCALE))


class FramingTests(unittest.TestCase):
    Z = zm.clean_one({"kind": "inout", "start": 1, "end": 4, "in": 1, "out": 1, "scale": 2, "x": .75, "y": .3}, 10)

    def test_it_eases_in_holds_and_eases_out(self):
        self.assertEqual(zm.amount(self.Z, 0.5, 10), 0)
        self.assertAlmostEqual(zm.amount(self.Z, 1.5, 10), .5)
        self.assertEqual(zm.amount(self.Z, 2.5, 10), 1)
        self.assertAlmostEqual(zm.amount(self.Z, 3.5, 10), .5)
        self.assertEqual(zm.amount(self.Z, 4.0, 10), 0)
        held = {**self.Z, "end": 10, "out": 0}
        self.assertEqual(zm.amount(held, 10.0, 10), 1)                      # one to the clip's end holds there

    def test_full_zoom_shows_exactly_its_framing(self):
        left, right, top, bottom = region([self.Z], 2.5)
        self.assertAlmostEqual(left, .5)
        self.assertAlmostEqual(right, 1.0)
        self.assertAlmostEqual(top, .05)
        self.assertAlmostEqual(bottom, .55)

    def test_the_zoom_grows_evenly_to_the_eye(self):
        z = {**self.Z, "kind": "push", "in": 1}
        sizes = [zm.at([z], 1 + k / 4, 10)[0] for k in range(5)]
        logs = [math.log(s) for s in sizes]
        self.assertAlmostEqual(logs[-1], math.log(2))
        self.assertEqual(logs, sorted(logs))

    def test_never_anything_outside_the_picture_any_kind_any_shape(self):
        for aspect in (16 / 9, 9 / 16, 1.0):
            for kind in zm.ORDER:
                z = zm.new(kind, .5, 6.0)
                z.update(x=.97, y=.03, tilt=15)                             # a corner, and turned
                z = zm.clean_one(z, 6.0)
                worst = max(shown([z], k / 50, aspect, 6.0) for k in range(300))
                self.assertLess(worst, 1e-6, (kind, aspect))

    def test_the_tilt_is_held_to_what_the_zoom_covers(self):
        self.assertEqual(zm.max_turn(1.0, 16 / 9), 0.0)
        self.assertLess(zm.max_turn(1.1, 16 / 9), 5)
        z = zm.clean_one({"kind": "tilt", "start": 0, "end": 4, "in": 1, "out": 1, "scale": 1.05, "tilt": 20}, 10)
        self.assertLessEqual(abs(zm.at([z], 2, 10)[3]), zm.max_turn(1.05, 16 / 9) + 1e-9)


class KindTests(unittest.TestCase):
    def test_bounce_goes_past_and_settles(self):
        z = zm.new("bounce", 0, 5)
        sizes = [zm.at([z], k / 100, 5)[0] for k in range(150)]
        self.assertGreater(max(sizes), z["scale"] + .02)
        self.assertAlmostEqual(sizes[-1], z["scale"])

    def test_step_cuts_in_by_steps(self):
        z = zm.new("step", 0, 5)
        z["steps"] = 3
        z = zm.clean_one(z, 5)
        self.assertEqual(len({round(zm.at([z], k / 100, 5)[0], 4) for k in range(200)}), 3)

    def test_pulse_beats_and_comes_back(self):
        z = zm.new("pulse", 0, 5)
        beat = [zm.at([z], k / 50, 5)[0] for k in range(26)]
        self.assertGreater(max(beat), 1.05)
        self.assertAlmostEqual(beat[25], 1.0, places=3)                     # back by the end of the beat

    def test_impact_shakes_then_stills(self):
        z = zm.new("impact", 0, 5)
        still = zm.at([z], .9, 5)
        moved = max(abs(zm.at([z], .06 + k / 100, 5)[1] - still[1]) for k in range(20))
        self.assertGreater(moved, .005)
        self.assertEqual(zm.at([z], .9, 5), zm.at([z], .95, 5))

    def test_pan_and_follow_go_through_their_framings(self):
        z = zm.new("follow", 0, 8)
        for p in z["points"]:
            when = min(z["start"] + p["at"], z["end"] - 1e-3)               # at its end it's over: just before
            s, left, _r, top, _b = (zm.at([z], when, 8)[0], *region([z], when, 8))
            self.assertAlmostEqual(s, p["scale"])
            self.assertAlmostEqual(left + .5 / s, p["x"])
        pan = zm.new("pan", 1, 8, room_end=3)                               # cut short: the path fits
        self.assertEqual(pan["points"][-1]["at"], 2.0)

    def test_settings_are_cleaned(self):
        z = zm.clean_one({"kind": "pulse", "start": 0, "end": 2, "period": 99, "tilt": -90, "shake": 3, "blur": "yes",
                          "steps": 9}, 5)
        self.assertEqual((z["period"], z["tilt"], z["shake"]), (4.0, -zm.MAX_TILT, 1.0))
        self.assertNotIn("blur", z)                                         # only True turns it on
        self.assertNotIn("steps", z)                                        # a step's own setting
        lone = zm.clean_one({"kind": "pan", "start": 0, "end": 2, "points": [{"at": 1}]}, 5)
        self.assertEqual(len(lone["points"]), 2)                            # a path needs two framings

    def test_kept_in_comp_frames_and_back(self):
        z = zm.new("follow", 1, 10)
        z.update(tilt=3, shake=.4, blur=True)
        z = zm.clean_one(z, 10)
        kept = zm.to_comp([z], 29.97, 12)
        self.assertEqual(kept[0]["start_f"], round(12 + 29.97, 3))
        back = zm.from_comp(kept, 29.97, 12, 10)[0]
        for k in ("kind", "tilt", "shake", "blur"):
            self.assertEqual(back[k], z[k])
        for a, b in zip(back["points"], z["points"]):
            self.assertAlmostEqual(a["at"], b["at"], places=3)
        # Trimmed a second off the front later: the zoom stays on its picture.
        moved = zm.from_comp(kept, 29.97, 12 + 29.97, 9)
        self.assertEqual(moved[0]["start"], 0.0)

    def test_keys_are_y_up_and_only_where_it_moves(self):
        z = zm.clean_one({"kind": "inout", "start": 1, "end": 4, "in": 1, "out": 1, "scale": 2, "x": .75, "y": .3}, 10)
        keys = zm.keys([z], 10, 240, 24.0, base=100)
        self.assertEqual(set(keys), {"Size", "Center.X", "Center.Y", "Angle"})
        self.assertLess(len(keys["Size"]), 80)
        self.assertEqual(len(keys["Angle"]), 2)                             # never turns: one value, both ends
        s, cx, cy, _deg = zm.at([z], 2.5, 10)
        held = keys["Center.Y"][max(f for f in keys["Center.Y"] if f <= 100 + 60)]       # a hold: its last key
        self.assertAlmostEqual(held["value"], 1 - cy, places=5)                          # Fusion's y goes up
        self.assertTrue(zm.blurred([{**z, "blur": True}]))


class PasteTests(unittest.TestCase):
    def zooms(self):
        return zm.clean([zm.new("push", 1, 10, room_end=3), zm.new("follow", 6, 10)], 10)

    def test_at_the_same_times_cuts_what_runs_past_the_end(self):
        pasted = zm.adapt(self.zooms(), 10, 7, "same")
        self.assertEqual([z["start"] for z in pasted], [1, 6])
        self.assertEqual(pasted[1]["end"], 7)                               # cut at the clip's end
        self.assertEqual(len(zm.adapt(self.zooms(), 10, 4, "same")), 1)     # the second starts after it: left out
        self.assertEqual(zm.adapt(self.zooms(), 10, .5, "same"), [])

    def test_stretched_keeps_every_moment_in_proportion(self):
        original = self.zooms()
        pasted = zm.adapt(original, 10, 5, "stretch")
        for a, b in zip(pasted, original):
            for k in ("start", "end", "in", "out"):
                self.assertAlmostEqual(a[k], b[k] / 2)
            self.assertEqual((a["scale"], a["x"], a["y"]), (b["scale"], b["x"], b["y"]))   # framings: shares
        pulse = zm.clean_one({**zm.new("pulse", 2, 10), "period": 1.0}, 10)
        self.assertAlmostEqual(zm.adapt([pulse], 10, 5, "stretch")[0]["period"], .5)
        for a, b in zip(pasted[1]["points"], original[1]["points"]):
            self.assertAlmostEqual(a["at"], b["at"] / 2)
        self.assertEqual(original[0]["start"], 1)                           # the copied ones left alone

    def test_rubbish_pastes_nothing(self):
        self.assertEqual(zm.adapt(None, 10, 5), [])
        self.assertEqual(zm.adapt(["nope", 3], 10, 5), [])
        self.assertEqual(len(zm.adapt(self.zooms(), 0, 5, "stretch")), 1)  # no length to stretch from: same times

    def test_paste_onto_every_selected_clip(self):
        from pages.text_animator import motion_resolve as mr
        from pages.text_animator import zoom_resolve as zr

        class Item:
            def __init__(self, name, frames):
                self.name, self.frames = name, frames

            def GetDuration(self):
                return self.frames

            def GetName(self):
                return self.name

        class Timeline:
            def GetSetting(self, _key):
                return "24"

        items = [(Item("long", 240), "video"), (Item("short", 24), "video"), (Item("title", 240), "video"),
                 (Item("broken", 120), "video"), (Item("sound", 240), "audio")]
        put = {}

        def apply(item, zooms, fps):
            if item.name == "broken":
                raise RuntimeError("no comp")
            put[item.name] = zooms
            return len(zooms)

        saved = (mr.current_timeline, mr.selected_items, mr.classify, zr.apply)
        mr.current_timeline = lambda _c: (None, Timeline())
        mr.selected_items = lambda _t: items
        mr.classify = lambda item, kind: "title" if item.name == "title" else kind
        zr.apply = apply
        try:
            result = zr.run_paste(None, self.zooms(), 10, "same")
        finally:
            mr.current_timeline, mr.selected_items, mr.classify, zr.apply = saved
        self.assertEqual(result, {"applied": 1, "empty": 1, "failed": [("broken", "no comp")]})
        self.assertEqual([z["start"] for z in put["long"]], [1, 6])         # only the video clips, and only 1 s+


class GradeTests(unittest.TestCase):
    def cube(self, rows, size=2, head='TITLE "Generated by Resolve"\nLUT_3D_SIZE {size}\n\n'):
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".cube")
        with os.fdopen(fd, "w") as f:
            f.write(head.format(size=size) + "\n".join(" ".join(str(v) for v in r) for r in rows) + "\n")
        self.addCleanup(os.remove, path)
        return path

    def test_a_cube_reads_red_first_as_bytes(self):
        from pages.text_animator import zoom_resolve as zr
        plain = [(r, g, b) for b in (0, 1) for g in (0, 1) for r in (0, 1)]
        size, data = zr.read_cube(self.cube(plain))
        self.assertEqual(size, 2)
        self.assertEqual(list(data[:6]), [0, 0, 0, 255, 0, 0])              # red changes fastest
        self.assertTrue(zr.is_identity(size, data))
        warm = [(min(1, r * 1.5), g * .8, b * .8 + .1) for r, g, b in plain]
        size, data = zr.read_cube(self.cube(warm))
        self.assertEqual(list(data[3:6]), [255, 0, 26])
        self.assertFalse(zr.is_identity(size, data))

    def test_a_broken_cube_is_refused(self):
        from pages.text_animator import zoom_resolve as zr
        with self.assertRaises(ValueError):
            zr.read_cube(self.cube([(0, 0, 0)] * 7))
        with self.assertRaises(ValueError):
            zr.read_cube(self.cube([(0, 0, 0)] * 8, head="no size\n"))

    def test_the_grade_round_trip_goes_back_to_the_page_it_left(self):
        from pages.text_animator import motion_resolve as mr
        from pages.text_animator import zoom_resolve as zr
        pages, wrote = [], []

        class Item:
            def GetUniqueId(self):
                return "clip"

            def ExportLUT(self, kind, path):
                wrote.append(pages[-1])
                with open(path, "w") as f:
                    f.write("LUT_3D_SIZE 2\n" + "\n".join(f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)))
                return True

        class Resolve:
            EXPORT_LUT_33PTCUBE = 1

            def GetCurrentPage(self):
                return "edit"

            def OpenPage(self, name):
                pages.append(name)

        import os
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "buddy_grade_test.cube")
        saved = (mr.current_timeline, mr.selected_items, mr.classify)
        mr.current_timeline = lambda _c: (None, object())
        mr.selected_items = lambda _t: [(Item(), "video")]
        mr.classify = lambda _item, kind: kind
        try:
            size, _data = zr.export_grade(type("C", (), {"resolve": Resolve()})(), "clip", path)
            with self.assertRaises(RuntimeError):
                zr.export_grade(type("C", (), {"resolve": Resolve()})(), "other", path)
        finally:
            mr.current_timeline, mr.selected_items, mr.classify = saved
        self.assertEqual((size, pages, wrote), (2, ["color", "edit"], ["color"]))
        self.assertFalse(os.path.exists(path))                              # the file isn't left behind


class AdjustmentTests(unittest.TestCase):
    """Zooms on an adjustment clip: told apart from titles, and what's under it."""

    class Comp:
        def __init__(self, *kinds):
            self.kinds = kinds

        def GetToolList(self, _selected, kind):
            return {1: kind} if kind in self.kinds else {}

    class Item:
        def __init__(self, name, start, end, track=1, kind="Video", comps=(), enabled=True, src=0):
            self.name, self.start, self.end, self.track = name, start, end, track
            self.kind, self.comps, self.enabled, self.src = kind, comps, enabled, src

        def GetName(self): return self.name
        def GetStart(self): return self.start
        def GetEnd(self): return self.end
        def GetDuration(self): return self.end - self.start
        def GetTrackTypeAndIndex(self): return ["video", self.track]
        def GetClipEnabled(self): return self.enabled
        def GetFusionCompCount(self): return len(self.comps)
        def GetFusionCompByIndex(self, i): return self.comps[i - 1]
        def GetUniqueId(self): return self.name
        def GetSourceStartFrame(self): return self.src
        def GetSpeed(self): return {"Percentage": 100}

        def GetMediaPoolItem(self):
            if self.kind is None:
                return None
            item = self

            class Pool:
                def GetClipProperty(self, key=None):
                    props = {"Type": item.kind, "File Path": __file__, "FPS": 24, "Resolution": "1920x1080"}
                    return props[key] if key else props
            return Pool()

    class Timeline:
        def __init__(self, items, off=()):
            self.items, self.off = items, off

        def GetItemListInTrack(self, _type, track):
            return [i for i in self.items if i.track == track]

        def GetIsTrackEnabled(self, _type, track):
            return track not in self.off

        def GetSetting(self, key):
            return {"timelineResolutionWidth": "3840", "timelineResolutionHeight": "2160"}.get(key)

    def test_told_from_titles_by_what_is_inside(self):
        from pages.text_animator import zoom_resolve as zr
        Item, Comp = self.Item, self.Comp
        self.assertEqual(zr.kind_of(Item("adj", 0, 10, kind=None), "video"), zr.ADJUST)           # no comp yet
        self.assertEqual(zr.kind_of(Item("adj", 0, 10, kind=None, comps=[Comp("MediaIn", "Transform")]), "video"),
                         zr.ADJUST)                                                                 # zoomed already
        self.assertEqual(zr.kind_of(Item("t", 0, 10, kind=None, comps=[Comp("TextPlus")]), "video"), "title")
        self.assertEqual(zr.kind_of(Item("v", 0, 10), "video"), "video")
        self.assertEqual(zr.kind_of(Item("a", 0, 10, kind=None), "audio"), "not_video")

    def test_under_it_the_top_picture_at_each_moment(self):
        from pages.text_animator import zoom_resolve as zr
        Item = self.Item
        adj = Item("adj", 100, 220, track=4, kind=None)
        items = [adj,
                 Item("wide", 50, 300, track=1, src=24),                    # V1: all along
                 Item("close", 140, 160, track=2),                         # V2 covers V1 here
                 Item("still", 180, 200, track=2, kind="Still"),
                 Item("title", 100, 220, track=3, kind=None),              # titles don't hide what's under
                 Item("hidden", 100, 220, track=2, enabled=False),
                 Item("off", 100, 220, track=5)]                            # above it: not under
        parts = zr.under(self.Timeline(items), adj, 24.0)
        self.assertEqual([(p["name"], p["from"] * 24, p["to"] * 24) for p in parts],
                         [("wide", 0, 40), ("close", 40, 60), ("wide", 60, 80), ("still", 80, 100), ("wide", 100, 120)])
        self.assertAlmostEqual(parts[0]["start"], (24 + 50) / 24)            # 50 frames into "wide" at its 24th source frame
        self.assertEqual(parts[3]["kind"], "image")
        self.assertEqual(zr.under(self.Timeline(items, off=(1, 2)), adj, 24.0), [])     # tracks switched off

    def test_its_info_is_the_timeline_frame(self):
        from pages.text_animator import zoom_resolve as zr
        adj = self.Item("adj", 0, 48, track=2, kind=None)
        info = zr.adjust_info(adj, self.Timeline([adj, self.Item("v", 0, 48)]), 24.0)
        self.assertEqual((info["kind"], info["width"], info["height"], info["duration"]), ("adjust", 3840, 2160, 2.0))
        self.assertEqual(len(info["parts"]), 1)
        self.assertTrue(info["exists"])


class PageTests(unittest.TestCase):
    def test_apply_only_sends_what_it_should(self):
        from pages.text_animator.page import AnimationPage
        Page = type("Page", (), {n: getattr(AnimationPage, n) for n in ("on_zm_apply", "_done_zoom")})
        page = Page()
        page.started = []
        page._start = lambda what, job, done: page.started.append(what)
        page.on_zm_apply({"id": 5, "zooms": []})
        page.on_zm_apply({"id": "clip", "zooms": "none"})
        page.on_zm_apply({"id": "clip", "zooms": [{"kind": "push"}]})
        page.on_zm_apply({"id": "clip", "zooms": []})
        self.assertEqual(page.started, ["zoom", "unzoom"])

    def page(self):
        from pages.text_animator.page import AnimationPage
        names = ("_zm_clipboard", "on_zm_copy", "on_zm_paste", "on_zm_paste_many", "_done_paste")
        page = type("Page", (), {n: getattr(AnimationPage, n) for n in names})()
        page.settings, page.emitted, page.started, page.working = {}, [], [], "paste"
        page._save = page.settings.__setitem__
        page.emit = lambda name, data: page.emitted.append((name, data))
        page._start = lambda what, job, done: page.started.append(what)
        page._push_state = page._read_after = lambda: None
        return page

    def test_copy_then_paste_onto_a_shorter_clip(self):
        page = self.page()
        page.on_zm_paste({"id": "b", "mode": "same", "duration": 5})       # nothing copied: nothing happens
        page.on_zm_copy({"zooms": [], "duration": 10})
        self.assertIsNone(page._zm_clipboard())
        page.on_zm_copy({"zooms": [zm.new("push", 1, 10), zm.new("punch", 6, 10)], "duration": 10, "name": "A"})
        self.assertEqual(page._zm_clipboard()["name"], "A")
        page.on_zm_paste({"id": "b", "mode": "sideways", "duration": 5})
        page.on_zm_paste({"id": "b", "mode": "stretch", "duration": 5})
        page.on_zm_paste({"id": "b", "mode": "same", "duration": 5})
        page.on_zm_paste({"id": "b", "mode": "same", "duration": .5})
        pasted = [d for name, d in page.emitted if name == "zm_pasted"]
        self.assertEqual([[z["start"] for z in d["zooms"]] for d in pasted], [[.5, 3], [1]])
        self.assertIn("shorter", page.emitted[-1][1]["text"])

    def test_paste_onto_many_says_what_happened(self):
        page = self.page()
        page.on_zm_paste_many({"mode": "same"})                             # nothing copied
        page.settings["zoom_clipboard"] = {"zooms": [zm.new("push", 1, 10)], "duration": 10}
        page.on_zm_paste_many({"mode": "stretch"})
        self.assertEqual(page.started, ["paste"])
        page._done_paste({"applied": 3, "empty": 1, "failed": [("C", "no comp")]}, None)
        self.assertEqual(page.emitted[0][1]["text"], "The zooms are on 3 of 4 clips – the others are shorter than where they start.")
        self.assertEqual(page.emitted[1][0], "alert")
        self.assertEqual(page.working, "")


if __name__ == "__main__":
    unittest.main()
