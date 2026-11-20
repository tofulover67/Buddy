"""core/fusion_comp.py (a comp as text) and core/fusion_place.py (a generated comp put
on the timeline as a new clip): what's written, where the clip goes, and that nothing
of anyone else's is ever covered or changed."""

import unittest

import _paths  # noqa: F401
from _fake_graphics import Project, Timeline

from core import fusion_comp as fc
from core import fusion_place as fp

TAG = "BuddyCounter"


def write(n):
    return fc.composition([fc.media_out(None, (0, 0))], n, {TAG: "bc-1"}), "bc-1"


class CompTextTests(unittest.TestCase):
    def test_a_colour_is_read_from_hex_and_rgba(self):
        self.assertEqual(fc.rgba("#ff0000"), (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(fc.rgba("#00ff0080")[3], 128 / 255)
        self.assertEqual(fc.rgba("rgba(255, 0, 0, 0.5)"), (1.0, 0.0, 0.0, 0.5))
        self.assertEqual(fc.rgba("nonsense", "#000000"), (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(fc.rgba("#gggggg", "#000000"), (0.0, 0.0, 0.0, 1.0))

    def test_text_goes_in_as_it_is(self):
        self.assertEqual(fc.input_("StyledText", 'He said "né"\n'), 'StyledText = Input { Value = "He said \\"né\\"\\n", },')
        self.assertEqual(fc.input_("Center", (0.5, 0.25)), "Center = Input { Value = { 0.5, 0.25 }, },")

    def test_a_text_spline_keys_each_change(self):
        text = fc.text_spline("CounterText", [(0, "$0"), (5, "$1,000")])
        self.assertIn('[0] = { 0, Flags = { Linear = true, LockedY = true }, Value = Text { Value = "$0" } },', text)
        self.assertIn('[5] = { 1, Flags = { Linear = true, LockedY = true }, Value = Text { Value = "$1,000" } },', text)

    def test_keys_in_the_middle_of_a_straight_run_are_left_out(self):
        self.assertEqual(fc.straight([(0, 0.0), (1, 0.0), (2, 0.0), (3, 1.0), (4, 2.0), (5, 2.0)]),
                         [(0, 0.0), (2, 0.0), (4, 2.0), (5, 2.0)])

    def test_a_composition_carries_its_tag_and_range(self):
        text, _gid = write(48)
        self.assertIn("RenderRange = { 0, 47 }", text)
        self.assertIn('CustomData = { TEMPLATE_ID = "Text+", BuddyCounter = "bc-1" }', text)
        self.assertTrue(text.rstrip().endswith("}"))

    def test_titles_still_write_their_comps_with_it(self):
        from pages.text_animator.titles import textplus_comp
        self.assertIs(textplus_comp._spline, fc.spline)
        self.assertIs(textplus_comp._merge, fc.merge)


class PlaceTests(unittest.TestCase):
    def setUp(self):
        self.timeline = Timeline()
        self.project = Project(self.timeline)

    def place(self, length=72, **kw):
        return fp.place_comp(self.project, self.timeline, write, TAG, length, what="counter", **kw)

    def test_it_goes_at_the_playhead_on_the_only_track(self):
        self.timeline.playhead = 86400 + 240
        done = self.place()
        self.assertEqual(done, {"id": "bc-1", "track": 1, "start": 86640, "end": 86712, "replaced": False})
        (item,) = self.timeline.tracks[1]
        self.assertEqual(item.comp.GetData(TAG), "bc-1")
        self.assertIn("RenderRange = { 0, 71 }", item.comp.text)        # written for the frames it got

    def test_the_playhead_is_put_back_where_it_was(self):
        self.timeline.playhead = 86400 + 100
        self.place()
        self.assertEqual(self.timeline.playhead, 86500)

    def test_someone_elses_clip_is_never_covered(self):
        footage = self.timeline.put(1, 86400, 86900)
        done = self.place()
        self.assertEqual(done["track"], 2)                              # a new track above it
        self.assertEqual(self.timeline.tracks[1], [footage])
        self.assertEqual((footage.start, footage.end), (86400, 86900))

    def test_the_topmost_free_track_is_used(self):
        self.timeline.put(1, 86400, 86900)
        self.timeline.put(2, 90000, 90100)                              # elsewhere on V2
        self.assertEqual(self.place()["track"], 2)

    def test_buddys_own_earlier_one_on_the_spot_is_replaced(self):
        first = self.place()
        again = self.place()
        self.assertEqual((again["track"], again["replaced"]), (first["track"], True))
        self.assertEqual(len(self.timeline.tracks[1]), 1)

    def test_or_left_and_the_new_one_put_above_it(self):
        self.place()
        again = self.place(replace=False)
        self.assertEqual((again["track"], again["replaced"]), (2, False))
        self.assertEqual((len(self.timeline.tracks[1]), len(self.timeline.tracks[2])), (1, 1))

    def test_another_kind_of_buddys_graphic_is_someone_elses(self):
        self.timeline.put(1, 86400, 86500, "Text+", {"BuddyTitle": "bt-9"})
        self.assertEqual(self.place()["track"], 2)
        self.assertEqual(len(self.timeline.tracks[1]), 1)

    def test_a_named_track_with_a_clip_in_the_way_is_refused(self):
        self.timeline.put(1, 86400, 86900, "Interview.mov")
        with self.assertRaises(fp.PlaceError) as caught:
            self.place(track=1)
        self.assertIn('V1 already has "Interview.mov" at 0:00.0', str(caught.exception))
        self.assertEqual(len(self.timeline.tracks[1]), 1)

    def test_a_comp_resolve_wont_take_leaves_nothing_behind(self):
        self.timeline.takes_comps = False
        with self.assertRaises(fp.PlaceError) as caught:
            self.place()
        self.assertIn("nothing was added", str(caught.exception))
        self.assertEqual(self.timeline.tracks[1], [])

    def test_without_the_template_nothing_is_placed(self):
        self.project = Project(self.timeline, template=False)
        with self.assertRaises(fp.PlaceError):
            self.place()
        self.assertEqual(self.timeline.tracks[1], [])

    def test_a_track_resolve_wont_add_is_said(self):
        self.timeline.put(1, 86400, 86900)
        self.timeline.adds_tracks = False
        with self.assertRaises(fp.PlaceError) as caught:
            self.place()
        self.assertIn("wouldn't add video track 2", str(caught.exception))

    def test_the_templates_own_frames_are_counted(self):
        # A 24 fps template on a 30 fps timeline: 90 timeline frames are 72 of its own.
        self.assertEqual(fp.source_frames_for(90, 24.0, 30.0), 72)
        self.assertEqual(fp.source_frames_for(90, 24.0, 24.0), 90)

    def test_timecodes_and_clocks(self):
        self.assertEqual(fp.timecode_frames("01:00:10:00", 24), 86640)
        self.assertEqual(fp.timecode_frames("00:01:00;02", 29.97, True), 1800)
        self.assertIsNone(fp.timecode_frames("soon", 24))
        self.assertEqual(fp.clock(86400 + 36, 24, 86400), "0:01.5")
        self.assertEqual(fp.clock(86400 * 2, 24, 86400), "1:00:00.0")


if __name__ == "__main__":
    unittest.main()
