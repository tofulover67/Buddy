"""The DAW (pages/daw): its project as it's kept (project.py), the song lane's
waveform and clips, and the page's listen / place / undo / keep - on made-up
music and a fake timeline (test_sound_design's). Never Resolve. The page's
sound and its three layouts are web/*.js."""

import base64
import json
import os
import shutil
import tempfile
import time
import types
import unittest
from unittest import mock

import _paths  # noqa: F401

try:
    import numpy as np
    from pages.daw import project as P
    HAVE_DEPS = True
except ImportError:  # pragma: no cover - numpy missing
    HAVE_DEPS = False


def _project(**over):
    base = {"bpm": 124, "tonic": 9, "mode": "minor", "tracks": [
        {"id": "chords", "name": "Chords", "kind": "inst", "sound": "pad", "values": {"tone": 60, "room": 40},
         "clips": [{"id": "c1", "name": "Am F C G", "start": 0, "len": 16, "notes": [[57, 0, 4, 0.8], [60, 0, 4, 0.8]]}]},
        {"id": "drums", "name": "Drums", "kind": "drums", "sound": "boom", "clips": []},
    ]}
    base.update(over)
    return base


@unittest.skipUnless(HAVE_DEPS, "numpy not installed")
class ProjectTests(unittest.TestCase):
    def test_a_project_is_kept_as_it_was_made(self):
        got = P.clean(_project())
        self.assertEqual((got["bpm"], got["tonic"], got["mode"]), (124.0, 9, "minor"))
        self.assertEqual([t["id"] for t in got["tracks"]], ["chords", "drums"])
        chords = got["tracks"][0]
        self.assertEqual((chords["sound"], chords["values"]), ("pad", {"tone": 60.0, "room": 40.0}))
        self.assertEqual(chords["clips"][0]["notes"], [[57, 0.0, 4.0, 0.8], [60, 0.0, 4.0, 0.8]])
        self.assertEqual(got["tracks"][1]["sound"], "boom")

    def test_what_isnt_a_project_is_not_kept(self):
        for raw in (None, [], "x", {"bpm": 120}, {"tracks": "no"}):
            self.assertIsNone(P.clean(raw))

    def test_numbers_are_kept_in_range_and_junk_falls_back(self):
        got = P.clean(_project(bpm=999, tonic=-3, mode="dorian", snap=5, view="mixer", voicing=True,
                               loop={"on": "yes", "start": 8, "end": 2}, velocity=float("nan")))
        self.assertEqual((got["bpm"], got["tonic"], got["mode"], got["snap"], got["view"], got["voicing"]),
                         (240.0, 0, "minor", 4, "arrange", "triad"))
        self.assertEqual(got["loop"], {"on": True, "start": 8.0, "end": 8.25})         # the end after the start
        self.assertEqual(got["velocity"], 0.8)
        self.assertEqual((P.clean(_project(pre_roll=True))["pre_roll"], P.clean(_project(pre_roll="yes"))["pre_roll"]), (True, False))

    def test_a_tracks_sound_is_one_of_its_kind(self):
        got = P.clean(_project(tracks=[{"kind": "drums", "sound": "pad"}, {"kind": "inst", "sound": "boom"},
                                       {"kind": "theremin", "sound": "bell"}]))
        self.assertEqual([(t["kind"], t["sound"]) for t in got["tracks"]], [("drums", "tight"), ("inst", "pad"), ("inst", "bell")])
        self.assertEqual([t["id"] for t in got["tracks"]], ["t0", "t1", "t2"])        # given ids where they had none

    def test_bad_notes_go_and_long_ones_end_with_their_clip(self):
        clip = {"id": "c", "start": 4, "len": 4, "notes": [
            [60, 0, 1, 0.8], [60, 3.5, 4, 0.8], [200, 0, 1, 0.5], [60, 5, 1, 0.5], ["C", 0, 1, 1], [60, 1, 1], [61, 1, 0.5, 0]]}
        got = P.clean(_project(tracks=[{"kind": "inst", "clips": [clip]}]))
        self.assertEqual(got["tracks"][0]["clips"][0]["notes"], [[60, 0.0, 1.0, 0.8], [60, 3.5, 0.5, 0.8], [127, 0.0, 1.0, 0.5],
                                                                  [61, 1.0, 0.5, 0.05]])

    def test_settings_keep_short_names_and_numbers_only(self):
        got = P.clean(_project(tracks=[{"kind": "inst", "values": {"tone": 50, "Bad Key": 1, "room": "lots", "echo": True,
                                                                    "x" * 30: 1, "drive": 1e9}}]))
        self.assertEqual(got["tracks"][0]["values"], {"tone": 50.0, "drive": 1000.0})

    def test_ids_are_unique_and_safe(self):
        got = P.clean(_project(tracks=[{"id": "a", "kind": "inst"}, {"id": "a", "kind": "inst"}, {"id": "<script>", "kind": "inst"}],
                               sel={"track": "a b", "clip": "c1"}))
        self.assertEqual([t["id"] for t in got["tracks"]], ["a", "t2"])
        self.assertEqual(got["sel"], {"track": "", "clip": "c1"})

    def test_what_one_key_plays_is_kept_in_bounds(self):
        got = P.clean(_project(key_chord={"type": "maj7", "voicing": "drop2", "inversion": 7}))
        self.assertEqual(got["key_chord"], {"type": "maj7", "voicing": "drop2", "inversion": 3})
        got = P.clean(_project(key_chord={"type": "<b>", "voicing": "sideways", "inversion": "two"}))
        self.assertEqual(got["key_chord"], {"type": "off", "voicing": "close", "inversion": 0})
        self.assertEqual(P.clean(_project())["key_chord"], {"type": "off", "voicing": "close", "inversion": 0})

    def test_songs_tempos_set_by_hand_are_kept_by_file(self):
        got = P.clean(_project(grids={"d:\\music\\bright days.wav": {"bpm": 91.4999, "first": 0.51234},
                                      "/Users/you/Music/fast.wav": {"bpm": 900, "first": -500},   # in range
                                      "no tempo": {"first": 1}, "": {"bpm": 90, "first": 0}, "not a grid": [90, 0]}))
        self.assertEqual(got["grids"], {"d:\\music\\bright days.wav": {"bpm": 91.5, "first": 0.5123},
                                        "/Users/you/Music/fast.wav": {"bpm": 300.0, "first": -60.0}})
        self.assertEqual(P.clean(_project())["grids"], {})
        many = {f"/Users/you/Music/song{i}.wav": {"bpm": 100, "first": 0} for i in range(P.MOST_GRIDS + 5)}
        kept = P.clean(_project(grids=many))["grids"]
        self.assertEqual(len(kept), P.MOST_GRIDS)
        self.assertIn(f"/Users/you/Music/song{P.MOST_GRIDS + 4}.wav", kept)          # the latest
        self.assertNotIn("/Users/you/Music/song0.wav", kept)

    def test_where_the_beats_were_lined_up_is_kept(self):
        got = P.clean(_project(anchor={"song": "d:\\music\\x.wav", "at": 8, "origin": -0.25}, edit_audio=False))
        self.assertEqual((got["anchor"], got["edit_audio"]), ({"song": "d:\\music\\x.wav", "at": 8.0, "origin": -0.25}, False))
        for bad in (None, "x", {"song": 3, "at": 0, "origin": 0}, {"song": "a", "at": "8", "origin": 0}):
            self.assertIsNone(P.clean(_project(anchor=bad))["anchor"])       # none: a project from before
        self.assertTrue(P.clean(_project())["edit_audio"])
        self.assertEqual((P.clean(_project(align="timeline"))["align"], P.clean(_project(align="x"))["align"]), ("timeline", "song"))
        self.assertEqual(P.clean(_project(anchor={"song": "a", "at": -3.1, "origin": 0}))["anchor"]["at"], -3.1)   # the timeline's: any beat
        got = P.clean(_project(snap_clips=False, tracks=[{"kind": "inst", "off": True, "clips": [
            {"id": "a", "start": 0, "len": 4, "notes": [], "off": True}, {"id": "b", "start": 4, "len": 4, "notes": [], "off": "yes"}]}]))
        self.assertEqual((got["snap_clips"], got["tracks"][0]["off"], [c.get("off") for c in got["tracks"][0]["clips"]]), (False, True, [True, None]))

    def test_a_huge_project_is_cut_to_what_can_be_kept(self):
        notes = [[60, i / 100, 0.01, 0.5] for i in range(P.MOST_NOTES + 50)]
        clips = [{"id": f"c{i}", "start": i * 4, "len": 64, "notes": notes} for i in range(P.MOST_CLIPS + 3)]
        tracks = [{"id": f"t{i}", "kind": "inst", "clips": clips} for i in range(P.MOST_TRACKS + 4)]
        got = P.clean(_project(tracks=tracks))
        self.assertEqual(len(got["tracks"]), P.MOST_TRACKS)
        self.assertEqual(len(got["tracks"][0]["clips"]), P.MOST_CLIPS)
        self.assertEqual(len(got["tracks"][0]["clips"][0]["notes"]), P.MOST_NOTES)
        self.assertLessEqual(sum(len(c["notes"]) for t in got["tracks"] for c in t["clips"]), P.MOST_ALL_NOTES)


# ---------------------------------------------------------------- the page --


@unittest.skipUnless(HAVE_DEPS, "numpy not installed")
class SongLaneTests(unittest.TestCase):
    def setUp(self):
        from pages.daw import page as page_mod
        self.page_mod = page_mod

    def test_the_waveform_is_the_loudest_of_each_moment(self):
        rate = 1000
        samples = np.zeros((rate * 2, 2))
        samples[rate // 2, 1] = -1.0          # one full-scale sample, on the right
        wave = self.page_mod.waveform(samples, rate, per_s=10)
        self.assertEqual(len(wave), 20)
        self.assertEqual(wave[5], 255)
        self.assertEqual(sum(wave), 255)
        self.assertEqual(self.page_mod.waveform(np.zeros((0, 2)), rate), [])
        many = self.page_mod.waveform(np.ones(48000 * 900), 48000)                    # 15 minutes
        self.assertLessEqual(len(many), self.page_mod.WAVE_MOST)

    def test_the_song_can_be_any_clip_with_a_file_but_a_curve(self):
        timeline = {"tracks": [
            {"index": 1, "name": "Dialogue", "clips": [{"id": "d", "name": "Tess.wav", "start": 0, "end": 10, "media": {"path": "D:\\x.wav"}}]},
            {"index": 2, "name": "Music", "clips": [
                {"id": "m", "name": "Song.wav", "start": 5, "end": 50, "media": {"path": "D:\\m.wav"}},
                {"id": "n", "name": "Nested", "start": 60, "end": 70, "media": None},
                {"id": "c", "name": "Curve", "start": 80, "end": 90, "media": {"path": "D:\\c.wav"}, "curve": {"edited": True}}]}]}
        got = self.page_mod.song_clips(timeline)
        self.assertEqual([(s["id"], s["track"], s["track_name"]) for s in got], [("d", 1, "Dialogue"), ("m", 2, "Music")])
        self.assertEqual(self.page_mod.song_clips(None), [])


@unittest.skipUnless(HAVE_DEPS, "numpy not installed")
class PageTests(unittest.TestCase):
    """page.py's listen, place, undo and project keeping, their work run straight away."""

    def setUp(self):
        from test_sound_design import A_MINOR, RATE, _Controller, _Item, _Pool, _Project, _Timeline, _Track, made_up_song
        from pages.daw import page as page_mod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.music_path = os.path.join(tmp.name, "Music", "song.wav")
        os.makedirs(os.path.dirname(self.music_path))
        self.decoded = []
        song = made_up_song(A_MINOR, set(range(8, 16)), {16, 17}, 44.0)

        def decode(path, start_s, end_s, progress, cancelled, channels=None):
            self.decoded.append((path, start_s, end_s))
            return (np.stack([song, song], axis=1), RATE), None

        patch = mock.patch.object(page_mod, "peaks_decode_range", decode)
        patch.start()
        self.addCleanup(patch.stop)
        music = _Track("Music", [])
        music.items = [_Item("m", 86424, 86424 + 24 * 44, music)]
        self.tl = _Timeline([_Track("Dialogue"), music])
        controller = _Controller(_Project(self.tl, _Pool(self.tl)))
        clip = {"id": "m", "name": "song.wav", "start": 86424, "end": 86424 + 24 * 44, "offset": 0.0,
                "media": {"path": self.music_path, "key": "m"}, "channels": None}
        self.sent = []
        worker = types.SimpleNamespace(start=lambda fn, done: done(fn(), None))

        class Settings(dict):
            saves = 0

            def save(self):
                self.saves += 1

        Page = page_mod.DawPage
        p = types.SimpleNamespace(
            timeline={"id": "t1", "project": "p1", "name": "Edit", "fps": 24.0, "start": 86400, "end": 90000,
                      "tracks": [{"index": 1, "name": "Dialogue", "clips": []}, {"index": 2, "name": "Music", "clips": [clip]}]},
            _songs={}, _placing=False, _stems=None, _full_due=1.0, undo_stack=[], problem="", _worker=worker, settings=Settings(),
            emit=lambda name, data: self.sent.append((name, data)), _writable=lambda: controller,
            _listen=lambda uid, work, done: done(*work(lambda f: None, lambda: False)),
            PLAY_DIR=os.path.join(tmp.name, "play_along"), scope="", projects_dir=lambda: os.path.join(tmp.name, "projects"))
        for name in ("on_listen", "on_place", "_stem_in", "_stems_dropped", "_place_failed", "on_undo", "on_project",
                     "_push_undo", "_clip", "_timeline_for_view",
                     "_project_path", "_kept", "_send_project"):
            setattr(p, name, types.MethodType(getattr(Page, name), p))
        self.page = p

    def last(self, name):
        return [d for n, d in self.sent if n == name][-1]

    def test_the_song_is_heard_written_once_and_drawn(self):
        self.page.on_listen({"id": "m"})
        song = self.last("song")
        self.assertEqual((song["id"], song["key"]["name"], song["start"], song["fps"]), ("m", "A minor", 86424, 24.0))
        self.assertAlmostEqual(song["bpm"], 120, delta=2)
        self.assertTrue(song["bars"])
        self.assertTrue(song["wave"] and max(song["wave"]) <= 255)
        self.assertNotIn("levels", song)
        self.assertEqual((song["media"], song["offset"]), (os.path.normcase(self.music_path), 0.0))   # a hand-set tempo's key
        from PySide6.QtCore import QUrl
        path = QUrl(song["url"]).toLocalFile()                 # "C:/..." on Windows, "/Users/..." on a Mac
        self.assertTrue(os.path.isfile(path))
        self.page.on_listen({"id": "m"})                       # again: what was heard is kept
        self.assertEqual(len(self.decoded), 1)
        self.page.on_listen({"id": "nope"})
        self.assertIn("error", self.last("song"))

    def test_a_render_is_placed_under_the_song_and_undone(self):
        mix = np.zeros(48000, dtype="<f4")
        mix[100] = 0.5
        chans = [base64.b64encode(mix.tobytes()).decode()] * 2
        self.page.on_place({"what": "mix", "label": "DAW mix A minor 124 BPM", "frame": 86436, "rate": 48000,
                            "channels": chans, "song": "m", "recipe": {"kind": "daw"}})
        self.assertEqual(self.last("placed")["ok"], True)
        self.assertEqual(self.last("toast")["text"], "Placed on A3")
        placed = self.tl.tracks[2].items[0]
        self.assertEqual((placed.GetStart(), placed.GetEnd()), (86436, 86460))
        self.assertEqual(self.last("undo")["label"], "Place the mix")
        self.page.on_undo()
        self.assertEqual(len(self.tl.tracks), 2)
        self.assertEqual(self.last("toast")["text"], "Undone: Place the mix")
        self.assertEqual(self.last("undo")["label"], None)

    def stem(self, index, count, name, at=86436, sets="s1", channels=None):
        x = np.zeros(48000, dtype="<f4")
        x[100 + index] = 0.5
        chans = channels or [base64.b64encode(x.tobytes()).decode()] * 2
        self.page.on_place({"what": "stems", "label": f"DAW stem {name}", "frame": at, "rate": 48000, "channels": chans,
                            "song": "m", "recipe": {"kind": "daw"}, "stem": {"set": sets, "index": index, "count": count, "name": name}})

    def test_stems_are_placed_together_each_on_its_own_track_and_undone_together(self):
        self.stem(0, 2, "Drums")
        self.assertEqual([n for n, _ in self.sent if n in ("placed", "alert")], [])            # waits for the last
        self.assertEqual(len(self.tl.tracks), 2)
        self.stem(1, 2, "Bass")
        self.assertEqual(self.last("placed")["tracks"], [3, 4])
        self.assertEqual([t.name for t in self.tl.tracks], ["Dialogue", "Music", "Buddy - Drums", "Buddy - Bass"])
        self.assertEqual(self.last("toast")["text"], "Placed 2 stems on A3–A4")
        self.assertEqual(self.last("undo")["label"], "Place the stems")
        self.page.on_undo()
        self.assertEqual(len(self.tl.tracks), 2)
        self.assertEqual(self.last("toast")["text"], "Undone: Place the stems")

    def test_a_set_of_stems_that_breaks_is_said_once_and_nothing_placed(self):
        self.stem(0, 3, "Drums")
        self.stem(1, 3, "Bass", channels=["!!"])
        self.stem(2, 3, "Keys")
        self.assertEqual([n for n, _ in self.sent if n == "alert"], ["alert"])
        self.assertEqual(len(self.tl.tracks), 2)
        self.stem(1, 2, "Bass", sets="s2")                       # a set that doesn't start at its first
        self.assertIn("Place them again", self.last("alert")["text"])
        self.stem(0, 1, "Keys", sets="s3")                       # and the next set is fine
        self.assertEqual(self.last("placed")["tracks"], [3])
        self.assertEqual(self.tl.tracks[2].name, "Buddy - Keys")

    def test_a_render_that_isnt_one_or_is_too_long_is_said(self):
        self.page.on_place({"frame": 86500, "rate": 48000, "channels": ["!!"]})
        self.assertEqual(self.last("alert")["title"], "Couldn't use that render")
        long = base64.b64encode(np.zeros(48000 * 151, dtype="<f4").tobytes()).decode()
        self.page.on_place({"frame": 86500, "rate": 48000, "channels": [long]})
        self.assertEqual(self.last("alert")["text"], "That sound is too long.")
        self.assertEqual(len(self.tl.tracks), 2)

    def test_the_project_is_kept_cleaned(self):
        self.page.on_project(_project(bpm=300))                        # with no timeline yet: the one kept in the settings
        self.assertEqual(self.page.settings["project"]["bpm"], 240.0)
        self.page.on_project({"junk": True})                   # not a project: what was kept stays
        self.assertEqual(self.page.settings["project"]["bpm"], 240.0)
        self.assertEqual(self.page.settings.saves, 1)

    def test_each_timeline_keeps_its_own_project(self):
        from pages.daw import page as page_mod
        page = self.page
        page.settings["project"] = _project(bpm=99)                     # kept before there was one each
        page.scope = page_mod.scope_of(page.timeline)
        self.assertEqual(page.scope, "p1|t1")
        page._send_project()                                            # the first timeline takes it
        sent = self.last("project")
        self.assertEqual((sent["scope"], sent["project"]["bpm"]), ("p1|t1", 99.0))
        page.on_project({"scope": "p1|t1", "project": _project(bpm=101)})
        # Another timeline: a new project (None) - and its own, once there's one.
        page.scope = "p1|t2"
        page._send_project()
        self.assertEqual(self.last("project"), {"scope": "p1|t2", "project": None})
        page.on_project({"scope": "p1|t2", "project": _project(bpm=140)})
        page.on_project({"scope": "p1|t1", "project": _project(bpm=102)})   # a late save of the one before: its own
        page.scope = "p1|t1"
        page._send_project()
        self.assertEqual(self.last("project")["project"]["bpm"], 102.0)
        page.scope = "p1|t2"
        page._send_project()
        self.assertEqual(self.last("project")["project"]["bpm"], 140.0)
        self.assertEqual(page.settings["project"]["bpm"], 99.0)          # the old one's left as it was
        self.assertEqual(len([f for f in os.listdir(page.projects_dir()) if f.endswith(".json")]), 2)
        page.on_project({"scope": "p1|t2", "project": {"junk": True}})   # not a project: nothing changes
        page._send_project()
        self.assertEqual(self.last("project")["project"]["bpm"], 140.0)

    def test_the_view_gets_the_timelines_songs(self):
        got = self.page._timeline_for_view()
        self.assertEqual((got["id"], got["fps"], [s["id"] for s in got["songs"]]), ("t1", 24.0, ["m"]))

    def test_the_view_gets_the_edit_to_score_to(self):
        # Its cuts, where the picture ends, and its markers - but not Buddy's own beat markers.
        self.page.timeline.update(cuts=[86460, 86520], video_end=88000, markers=[
            {"frame": 86500, "color": "#E8664C", "name": "Logo hit", "beat": False, "duration": 1},
            {"frame": 86510, "color": "#4C9BE8", "name": "", "beat": True, "duration": 1},
            {"frame": 86700, "color": "#3FAE4B", "name": "Title", "beat": False, "duration": 48}])
        got = self.page._timeline_for_view()
        self.assertEqual((got["cuts"], got["video_end"]), ([86460, 86520], 88000))
        self.assertEqual([(m["frame"], m["name"], m["duration"]) for m in got["markers"]],
                         [(86500, "Logo hit", 1), (86700, "Title", 48)])
        del self.page.timeline["cuts"], self.page.timeline["markers"], self.page.timeline["video_end"]
        got = self.page._timeline_for_view()                   # a read from before: none of it, nothing breaks
        self.assertEqual((got["cuts"], got["markers"], got["video_end"]), ([], [], None))


@unittest.skipUnless(HAVE_DEPS, "numpy not installed")
class EditAudioTests(unittest.TestCase):
    """The timeline's audio for the reference lanes, and their waveforms."""

    def test_the_edits_audio_tracks_and_clips(self):
        from pages.daw import page as page_mod
        timeline = {"tracks": [
            {"index": 1, "name": "Dialogue", "clips": [
                {"id": "a", "name": "Tess.wav", "start": 10, "end": 50, "offset": 1.23456, "peaks": "k1", "color": "#E8664C",
                 "enabled": True, "media": {"path": "D:\\Footage\\Tess.wav", "key": "k1"}},
                {"id": "x", "name": "Cross", "start": 50, "end": 54, "transition": True}]},
            {"index": 2, "name": "Empty", "clips": []},
            {"index": 3, "name": "Music", "clips": [{"id": "m", "name": "song.wav", "start": 0, "end": 99, "enabled": False}]}]}
        got = page_mod.edit_audio(timeline)
        self.assertEqual([(t["index"], t["name"], [c["id"] for c in t["clips"]]) for t in got],
                         [(1, "Dialogue", ["a"]), (3, "Music", ["m"])])                 # no crossfades, no empty tracks
        self.assertEqual(got[0]["clips"][0], {"id": "a", "name": "Tess.wav", "start": 10, "end": 50, "offset": 1.2346,
                                              "peaks": "k1", "color": "#E8664C", "enabled": True})
        self.assertNotIn("media", got[0]["clips"][0])                                   # no paths to the view
        many = {"tracks": [{"index": 1, "name": "A", "clips": [{"id": str(i), "start": i, "end": i + 1}
                                                                for i in range(page_mod.MOST_AUDIO_CLIPS + 9)]}]}
        self.assertEqual(len(page_mod.edit_audio(many)[0]["clips"]), page_mod.MOST_AUDIO_CLIPS)

    def test_a_clips_waveform_is_its_channels_thinned(self):
        from pages.daw import page as page_mod
        # Two channels, 100 peaks a second: the left loud for the first second, the right after.
        left, right = [200] * 100 + [0] * 100, [0] * 100 + [90] * 100
        codes = bytes(v for pair in zip(left, right) for v in pair)
        loud = np.zeros(4, dtype=np.float32).tobytes()
        result = (codes, loud, 2)
        both = np.frombuffer(page_mod.reference_peaks(result, None), dtype=np.uint8)
        self.assertEqual((len(both), both[0], both[-1]), (50, 200, 90))                 # 25 a second, the loudest
        right_only = np.frombuffer(page_mod.reference_peaks(result, [1]), dtype=np.uint8)
        self.assertEqual((right_only[0], right_only[-1]), (0, 90))
        self.assertIsNone(page_mod.reference_peaks(None, None))                          # couldn't be read
        self.assertIsNone(page_mod.reference_peaks(result, [5]))                         # a channel it hasn't

    def test_each_clips_waveform_is_sent_once_its_file_is_read(self):
        from pages.daw import page as page_mod
        sent = []
        clip = {"id": "a", "start": 0, "end": 10, "media": {"path": "D:\\Footage\\Tess.wav", "key": "k1"},
                "peaks": "k1:1", "channels": [1]}
        p = types.SimpleNamespace(timeline={"tracks": [{"index": 1, "clips": [clip, dict(clip, id="b")]}]},
                                  _peaks={}, _peaks_sent=set(), emit=lambda name, data: sent.append((name, data)))
        for name in ("_send_peaks", "_on_peaks"):
            setattr(p, name, types.MethodType(getattr(page_mod.DawPage, name), p))
        p._send_peaks()
        self.assertEqual(sent, [])                                                      # not read yet
        p._on_peaks("k1", (bytes([10, 250] * 8), np.zeros(16, dtype=np.float32).tobytes(), 2), None)
        self.assertEqual(len(sent), 1)                                                  # once for the two clips
        name, data = sent[0]
        self.assertEqual((name, data["key"], data["rate"]), ("peaks", "k1:1", page_mod.REF_PER_S))
        self.assertEqual(base64.b64decode(data["data"]), bytes([250, 250]))
        p._send_peaks()
        self.assertEqual(len(sent), 1)
        p._on_peaks("k1", None, "Couldn't read it")                                     # read again, and failed
        self.assertEqual(sent[-1][1]["data"], None)


# ---------------------------------------------------------------- the view --


class ViewTests(unittest.TestCase):
    """index.html in a web view, no Resolve: the three layouts over one project, the
    computer's keys, a progression, recording, placing - what page.py would send it
    played in by hand (Buddy.receive), what it sends caught."""

    # A made-up song: A minor at 124 BPM, its first bar half a second into the clip.
    BEAT = 60 / 124
    SONG = {"id": "m1", "name": "Bright Days (Music).wav", "bpm": 124, "key": {"tonic": 9, "mode": "minor", "name": "A minor"},
            "bars": [0.5 + i * 4 * 60 / 124 for i in range(40)], "moments": [], "start": 86424, "end": 89000, "fps": 24,
            "url": "", "wave": [128] * 400, "wave_per_s": 50, "length_s": 80,
            "media": "d:\\music\\bright days (music).wav", "offset": 0.0}
    TIMELINE = {"id": "t1", "name": "Edit", "fps": 24, "start": 86400, "end": 90000,
                "songs": [{"id": "m1", "name": "Bright Days (Music).wav", "track": 2, "track_name": "Music", "start": 86424, "end": 89000},
                          {"id": "d1", "name": "Tess.wav", "track": 1, "track_name": "Dialogue", "start": 86400, "end": 90000}]}

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        except ImportError:  # pragma: no cover - PySide6 without its web engine
            raise unittest.SkipTest("QtWebEngine not installed")
        from PySide6.QtWidgets import QApplication
        from test_audio_assistant import qt_app
        cls.app = qt_app()
        if not isinstance(cls.app, QApplication):     # another file's test made a bare one
            raise unittest.SkipTest("a QCoreApplication was made first; a web view needs a QApplication")

    def setUp(self):
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtWebEngineCore import QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from shiboken6 import delete
        self.view = QWebEngineView()
        self.addCleanup(delete, self.view)
        self.view.resize(1300, 820)
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        loop, loaded = QEventLoop(), []
        self.view.loadFinished.connect(lambda ok: (loaded.append(ok), loop.quit()))
        self.view.setUrl(QUrl.fromLocalFile(str(_paths.APP / "pages" / "daw" / "web" / "index.html")))
        self.view.show()
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        self.assertEqual(loaded, [True])
        self.js("window.__sent = []; Buddy.send = (name, data) => window.__sent.push([name, data]); return true;")
        self.receive("project", None)
        self.addCleanup(lambda: self.js("Daw.stop(); return true;"))

    def js(self, body):
        from PySide6.QtCore import QEventLoop, QTimer
        loop, result = QEventLoop(), []
        self.view.page().runJavaScript("JSON.stringify((() => {" + body + "})())", 0,
                                       lambda value: (result.append(value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        self.assertTrue(result, "JavaScript timed out")
        return json.loads(result[0])

    def until(self, expr, seconds=5):
        from PySide6.QtCore import QEventLoop, QTimer
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.js(f"return !!({expr});"):
                return
            loop = QEventLoop()
            QTimer.singleShot(50, loop.quit)
            loop.exec()
        self.fail("Timed out waiting for " + expr)

    def wait(self, ms):
        self.js(f"window.__wait = Date.now() + {ms}; return true;")
        self.until("Date.now() > window.__wait")

    def receive(self, name, payload):
        self.js(f"Buddy.receive({json.dumps(name)}, {json.dumps(payload)}); return true;")

    def key(self, code, kind="keydown", ctrl=False, shift=False, on="document.body"):
        self.js(f"{on}.dispatchEvent(new KeyboardEvent('{kind}', {{code: '{code}', key: '{' ' if code == 'Space' else 'x'}',"
                f" ctrlKey: {str(ctrl).lower()}, shiftKey: {str(shift).lower()}, bubbles: true, cancelable: true}})); return true;")

    def with_song(self):
        self.receive("state", {"connected": True, "problem": "", "busy": False})
        self.receive("timeline", self.TIMELINE)
        self.receive("playhead", {"frame": 86500})
        self.receive("song", self.SONG)

    def sounding(self):
        return self.js("return [...Daw.sounding()].sort((a, b) => a - b);")

    def test_the_three_layouts_switch_and_playing_carries_on(self):
        layouts = "return [...document.querySelectorAll('.daw-bar .seg button')].map(b => [b.textContent, b.getAttribute('aria-pressed')]);"
        self.assertEqual(self.js(layouts), [["Arrange", "true"], ["Console", "false"], ["Perform", "false"]])
        self.js("Daw.start(); return true;")
        self.until("Daw.playing() && Daw.position() > 0.2")
        self.js("[...document.querySelectorAll('.daw-bar .seg button')][1].click(); return true;")
        self.assertEqual(self.js("return [Daw.P.view, Daw.playing(), document.querySelector('[data-view=console]').hidden];"),
                         ["console", True, False])
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {code: 'Digit3', key: '3', ctrlKey: true, bubbles: true}));"
                " return true;")
        self.assertEqual(self.js("return [Daw.P.view, Daw.playing()];"), ["perform", True])
        before = self.js("return Daw.position();")
        self.wait(200)
        self.assertGreater(self.js("return Daw.position();"), before)      # still going

    def test_the_song_is_guessed_and_the_daw_follows_its_key_and_tempo(self):
        self.receive("timeline", self.TIMELINE)
        self.assertIn(["listen", {"id": "m1"}], self.js("return window.__sent;"))   # the music, not the interview
        self.receive("song", self.SONG)
        self.assertEqual(self.js("return [Daw.keyName(), Daw.bpm()];"), ["A minor", 124])
        self.assertTrue(self.js("return !!document.querySelector('.lane.song canvas.wave');"))
        self.assertIn("A minor", self.js("return document.querySelector('.song-pick').textContent;"))

    def test_the_picture_lane_shows_the_edit_and_follows_resolves_playhead(self):
        # The made-up song's first bar is 0.5 s (12 frames) in at 124 BPM: a frame's beat is
        # (frame - 86424 - 12) / 24 / (60 / 124).
        # Beat 0 is the timeline's start on the song's grid: its bar 1 is beat 4.
        beat_of = lambda frame: (frame - 86436) / 24 / (60 / 124) + 4
        edit = dict(self.TIMELINE, cuts=[86460, 86580], video_end=87200, markers=[
            {"frame": 86520, "color": "#E8664C", "name": "Logo hit", "duration": 1},
            {"frame": 86700, "color": "#4C9BE8", "name": "Title", "duration": 48}])
        self.receive("state", {"connected": True, "problem": "", "busy": False})
        self.receive("timeline", edit)
        self.receive("playhead", {"frame": 86500})                             # where Resolve's is to begin with
        self.receive("song", dict(self.SONG, moments=[{"frame": 86436 + 96, "kind": "drop", "t": 4.5, "bar": 2}]))   # not shown
        px = self.js("return parseFloat(getComputedStyle(document.querySelector('.lanes-grid')).getPropertyValue('--beat'));")
        lane = self.js("""const l = document.querySelector('.lane.picture'), x0 = l.querySelector('.body').getBoundingClientRect().left;
            const x = n => n.getBoundingClientRect().left - x0;
            return {cuts: [...l.querySelectorAll('.cut')].map(c => x(c) + c.offsetWidth / 2),      // a tick: the middle of its grip
                    marks: [...l.querySelectorAll('.mark')].map(m => [x(m), m.getBoundingClientRect().width, m.title]),
                    end: x(l.querySelector('.pic-end')),
                    guides: document.querySelectorAll('.pic-guides .guide').length,
                    tags: document.querySelectorAll('.lane.song .moment').length};""")
        for got, frame in zip(lane["cuts"], (86460, 86580)):
            self.assertAlmostEqual(got, beat_of(frame) * px, delta=1)
        self.assertAlmostEqual(lane["marks"][0][0], beat_of(86520) * px, delta=0.5)
        self.assertAlmostEqual(lane["marks"][1][1], 48 / 24 / (60 / 124) * px, delta=0.5)   # as long as the marker
        self.assertIn("Title", lane["marks"][1][2])
        self.assertAlmostEqual(lane["end"], beat_of(87200) * px, delta=0.5)
        self.assertEqual((lane["guides"], lane["tags"]), (4, 0))               # the song's moments aren't tagged
        # A marker clicked: the playhead there.
        self.js("""const m = document.querySelectorAll('.lane.picture .mark')[1];
                   m.dispatchEvent(new PointerEvent('pointerdown', {button: 0, bubbles: true, cancelable: true})); return true;""")
        self.assertAlmostEqual(self.js("return Daw.P.cursor;"), beat_of(86700), places=6)
        # Resolve's playhead moved while the DAW is stopped: the DAW's goes with it...
        self.receive("playhead", {"frame": 86436 + 240})
        self.assertAlmostEqual(self.js("return Daw.P.cursor;"), round(beat_of(86436 + 240), 3), places=6)
        self.js("Daw.P.cursor = 2; return true;")
        self.receive("playhead", {"frame": 86436 + 240})                       # ...only when it moves
        self.assertEqual(self.js("return Daw.P.cursor;"), 2)
        self.js("document.querySelector('.pic-follow').click(); return true;")    # Follow off
        self.assertFalse(self.js("return Daw.P.follow_resolve;"))
        self.receive("playhead", {"frame": 86436 + 480})
        self.assertEqual(self.js("return Daw.P.cursor;"), 2)

    def test_with_no_song_place_lines_up_with_the_picture(self):
        # No song: beat 0 is the timeline's first frame, at the project's tempo (120 BPM here).
        self.receive("state", {"connected": True, "problem": "", "busy": False})
        self.receive("timeline", dict(self.TIMELINE, songs=[], cuts=[86400 + 24 * 3]))
        self.assertAlmostEqual(self.js("return DawApp.beatOfFrame(86400 + 24 * 3);"), 6.0, places=9)   # 3 s = 6 beats
        self.js("document.querySelector('.daw-bar .btn.accent').click(); return true;")
        self.assertIn("Lined up with the picture", self.js("return document.querySelector('.menu-pop').textContent;"))
        self.js("[...document.querySelectorAll('.menu-pop button')].find(b => /whole mix/.test(b.textContent)).click(); return true;")
        self.until("window.__sent.some(s => s[0] === 'place')", seconds=15)
        self.assertEqual(self.js("return window.__sent.find(s => s[0] === 'place')[1].frame;"), 86400)   # bar 1: its first frame

    def test_a_song_with_no_steady_beat_says_the_tempo_is_the_projects_own(self):
        tempo = "const s = document.querySelector('.lcd span'); return [s.textContent, s.classList.contains('unsure'), document.querySelector('.lcd').title];"
        self.receive("timeline", self.TIMELINE)
        self.receive("song", dict(self.SONG, bpm=None, bars=[], grid=None))
        text, unsure, title = self.js(tempo)
        self.assertEqual((text, unsure), ("120 BPM", True))                 # the project's, and said so
        self.assertIn("steady beat", title)
        self.receive("song", self.SONG)
        self.assertEqual(self.js(tempo)[:2], ["124 BPM", False])

    def test_the_computer_keys_play_the_selected_track_in_its_key(self):
        self.with_song()
        self.js("DawApp.show('console'); return true;")
        roots = self.js("return [...document.querySelectorAll('.piano .pk.root')].map(k => Number(k.dataset.midi) % 12);")
        self.assertTrue(roots and set(roots) == {9})                          # A, the key's root
        marked = self.js("return [...document.querySelectorAll('.piano .pk.in, .piano .pk.root')].map(k => Number(k.dataset.midi) % 12);")
        self.assertEqual(sorted(set(marked)), [0, 2, 4, 5, 7, 9, 11])         # A minor's notes
        self.key("KeyD")                                                      # A is C4: D is E4
        self.assertEqual(self.sounding(), [64])
        self.key("KeyD", "keyup")
        self.assertEqual(self.sounding(), [])
        self.key("KeyX")                                                      # an octave up
        self.assertEqual(self.js("return Daw.P.octave;"), 5)
        # On a drum track the white keys are the kit: C the kick, D the snare.
        self.js("Daw.select(Daw.P.tracks.find(t => t.kind === 'drums').id); return true;")
        self.key("KeyS")
        self.assertEqual(self.sounding(), [38])
        self.key("KeyS", "keyup")

    def test_one_key_plays_a_chord_or_an_interval(self):
        self.with_song()                                                      # A minor
        self.js("DawApp.show('console'); Daw.select(Daw.P.tracks[0].id); return true;")
        pick = """const s = document.querySelector('.con-keys %s'); s.value = %s;
                  s.dispatchEvent(new Event('change')); return true;"""
        groups = self.js("return [...document.querySelectorAll('.con-keys .chord-type optgroup')].map(g => g.label);")
        self.assertEqual(groups, ["In the key", "Chords", "Intervals"])
        self.js(pick % (".chord-type", "'key_triad'"))
        self.key("KeyA")                                                       # C4: C is in A minor - C major
        self.assertEqual(self.sounding(), [60, 64, 67])
        self.key("KeyA", "keyup")
        self.key("KeyD")                                                       # E4: E minor (no G sharp in the key)
        self.assertEqual(self.sounding(), [64, 67, 71])
        self.key("KeyD", "keyup")
        self.js(pick % (".chord-type", "'maj7'"))
        self.key("KeyA")
        self.assertEqual(self.sounding(), [60, 64, 67, 71])
        self.key("KeyA", "keyup")
        self.js(pick % ("select[aria-label=Inversion]", "'1'"))
        self.key("KeyA")
        self.assertEqual(self.sounding(), [64, 67, 71, 72])                    # the C on top
        self.key("KeyA", "keyup")
        self.js(pick % ("select[aria-label=Inversion]", "'0'") + "")
        self.js(pick % ("select[aria-label=Voicing]", "'bass'"))
        self.key("KeyA")
        self.assertEqual(self.sounding(), [48, 60, 64, 67, 71])                # the root again below
        self.key("KeyA", "keyup")
        self.assertEqual(self.js("return Daw.P.key_chord;"), {"type": "maj7", "voicing": "bass", "inversion": 0})
        # Every one listed plays something sensible: two notes or more, low to high, in range;
        # those "in the key" never a note outside it.
        report = self.js("""const key = {tonic: 9, mode: 'minor'}, scale = [0, 2, 3, 5, 7, 8, 10], bad = [];
            let n = 0;
            for (const g of Chords.GROUPS) for (const [id] of g.items) {
                if (id === 'off') continue;
                for (const midi of [48, 61, 66, 72]) for (const voicing of ['close', 'open', 'drop2', 'bass']) for (const inversion of [0, 1, 2, 3]) {
                    n++;
                    const out = Chords.notes(midi, {type: id, voicing, inversion}, key);
                    const sorted = out.every((m, i) => i === 0 || m > out[i - 1]);
                    const inKey = !id.startsWith('key_') || out.every(m => scale.includes(((m - 9) % 12 + 12) % 12));
                    if (out.length < 2 || !sorted || !inKey || out.some(m => m < 0 || m > 127)) bad.push([id, midi, voicing, inversion, out]);
                }
            }
            return {n, bad: bad.slice(0, 5), types: Chords.TYPES.size};""")
        self.assertEqual(report["bad"], [])
        self.assertGreaterEqual(report["types"], 60)
        # A drum track's keys are its drums, chords or not; the pads play their own chords.
        self.js("Daw.select(Daw.P.tracks.find(t => t.kind === 'drums').id); return true;")
        self.assertTrue(self.js("return document.querySelector('.con-keys .chord-type').disabled;"))
        self.key("KeyA")
        self.assertEqual(self.sounding(), [36])
        self.key("KeyA", "keyup")

    def test_a_list_with_the_focus_leaves_the_keys_to_the_keyboard(self):
        # Chords picked from its list with the mouse: the list lets go of the focus.
        self.with_song()
        self.js("DawApp.show('console'); Daw.select(Daw.P.tracks[0].id); return true;")
        chord = "document.querySelector('.con-keys .chord-type')"
        self.js(f"const s = {chord}; s.focus(); s.value = 'key_triad';"
                " s.dispatchEvent(new Event('change', {bubbles: true})); return true;")
        self.assertEqual(self.js(f"return [Daw.P.key_chord.type, document.activeElement === {chord}];"), ["key_triad", False])
        # Still holding it (tabbed to): a letter plays its note, and isn't the list's type-ahead.
        press = f"""const s = {chord}; s.focus();
                    const e = new KeyboardEvent('keydown', {{code: %s, key: %s, bubbles: true, cancelable: true}});
                    s.dispatchEvent(e); return e.defaultPrevented;"""
        self.assertTrue(self.js(press % ("'KeyA'", "'a'")))
        self.assertEqual(self.sounding(), [60, 64, 67])                       # C major, in A minor
        self.key("KeyA", "keyup", on=chord)
        self.assertEqual(self.js(f"return [Daw.P.key_chord.type, {chord}.value];"), ["key_triad", "key_triad"])
        self.assertFalse(self.js(press % ("'ArrowDown'", "'ArrowDown'")))     # its own keys stay its own
        self.assertEqual(self.sounding(), [])

    def test_a_progression_is_written_in_the_key_and_undone(self):
        self.with_song()
        self.js("Daw.select(Daw.P.tracks[0].id); return true;")                # Chords, a pad
        # Console's "Write a progression…": a menu of them, in the key.
        self.js("DawApp.show('console'); [...document.querySelectorAll('.con-clipbar .btn')].find(b => /progression/.test(b.textContent)).click();"
                " [...document.querySelectorAll('.menu-pop button')].find(b => b.textContent === 'i–VI–III–VII').click(); return true;")
        bars = self.js("""const c = Daw.selClip(), bars = {};
                          for (const [m, s] of c.notes) (bars[s] = bars[s] || []).push(m % 12);
                          return Object.keys(bars).sort((a, b) => a - b).map(b => bars[b].sort((x, y) => x - y));""")
        self.assertEqual(bars, [[0, 4, 9], [0, 5, 9], [0, 4, 7], [2, 7, 11]])   # Am, F, C, G
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {code: 'KeyZ', key: 'z', ctrlKey: true, bubbles: true}));"
                " return true;")
        self.assertEqual(self.js("return Daw.P.tracks[0].clips.length;"), 0)
        self.until("window.__sent.some(s => s[0] === 'project')")              # kept for next time

    def test_recording_a_note_then_placing_the_mix_lined_up_with_the_song(self):
        self.with_song()
        self.js("Daw.P.count_in = false; Daw.P.loop.on = false; Daw.P.tracks[2].clips = []; Daw.P.cursor = Daw.songAt(); Daw.select(Daw.P.tracks[0].id, ''); return true;")
        self.key("KeyR")
        self.until("Daw.recording() && Daw.position() > 0.3")
        self.key("KeyA")
        self.wait(300)
        self.key("KeyA", "keyup")
        self.key("KeyR")
        self.until("!Daw.playing()")
        notes = self.js("return Daw.P.tracks[0].clips.flatMap(c => c.notes);")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0][0], 60)                                      # C4
        self.assertEqual(notes[0][1] * 4 % 1, 0)                               # on the 1/16 grid
        self.assertGreaterEqual(notes[0][2], 0.25)
        # Place, the whole mix: rendered here, its first bar where the song's is.
        self.js("document.querySelector('.daw-bar .btn.accent').click(); return true;")
        self.js("[...document.querySelectorAll('.menu-pop button')].find(b => /whole mix/.test(b.textContent)).click(); return true;")
        self.until("window.__sent.some(s => s[0] === 'place')", seconds=15)
        sent = self.js("const d = window.__sent.find(s => s[0] === 'place')[1];"
                       " return [d.what, d.frame, d.rate, d.channels.length, d.song, d.label];")
        self.assertEqual(sent, ["mix", 86424 + round(0.5 * 24), 48000, 2, "m1", "DAW mix A minor 124 BPM"])

    def test_stems_are_rendered_a_track_each_and_sent_one_at_a_time(self):
        self.with_song()
        self.js("""Daw.edit("x", () => { for (const t of Daw.P.tracks) t.clips = [];
                   Daw.P.tracks[0].clips.push({id: "k1", name: "K", start: 0, len: 4, notes: [[60, 0, 1, 0.8], [64, 2, 1, 0.8]]});
                   Daw.P.tracks[2].clips.push({id: "d1", name: "D", start: 0, len: 4, notes: [[36, 0, 0.25, 0.9], [36, 2, 0.25, 0.9]]}); });
                   return true;""")
        heard =self.js("const r = Daw.usedRange(); return Daw.stemTracks(r.from, r.to).map(t => t.name);")
        self.assertGreaterEqual(len(heard), 2)
        muted = self.js("const t = Daw.P.tracks.find(t => t.name === %s); t.mute = true;"
                        " const r = Daw.usedRange(); const got = Daw.stemTracks(r.from, r.to).map(t => t.name);"
                        " t.mute = false; return got;" % json.dumps(heard[0]))
        self.assertEqual(muted, heard[1:])                                         # what's heard, as the mix
        self.js("document.querySelector('.daw-bar .btn.accent').click(); return true;")
        self.js("[...document.querySelectorAll('.menu-pop button')].find(b => /Stems/.test(b.textContent)).click(); return true;")
        self.until(f"window.__sent.filter(s => s[0] === 'place').length === {len(heard)}", seconds=30)
        got = self.js("""const sent = window.__sent.filter(s => s[0] === 'place').map(s => s[1]);
                         const samples = d => { const bin = atob(d.channels[0]), bytes = new Uint8Array(bin.length);
                             for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                             return new Float32Array(bytes.buffer); };
                         const sum = new Float32Array(Math.max(...sent.map(d => samples(d).length)));
                         for (const d of sent) samples(d).forEach((v, i) => { sum[i] += v; });
                         let peak = 0;
                         for (const v of sum) peak = Math.max(peak, Math.abs(v));
                         return {stems: sent.map(d => [d.what, d.frame, d.stem.index, d.stem.count, d.stem.name, d.label]),
                                 sets: new Set(sent.map(d => d.stem.set)).size,
                                 peak};""")
        self.assertEqual([s[2:5] for s in got["stems"]], [[i, len(heard), n] for i, n in enumerate(heard)])
        self.assertEqual({(s[0], s[1]) for s in got["stems"]}, {("stems", got["stems"][0][1])})   # one place, one set
        self.assertEqual(got["sets"], 1)
        self.assertEqual(got["stems"][0][5], f"DAW stem {heard[0]} A minor 124 BPM")
        self.assertTrue(0 < got["peak"] <= 10 ** (-1 / 20) + 1e-4)                 # together, no louder than a mix

    def test_the_song_is_followed_on_its_grid_and_placed_on_the_beat(self):
        # The grid song.py fits through the beats: its tempo to the thousandth, its first bar
        # between two frames (0.52 s is 12.48 frames at 24 fps).
        song = dict(self.SONG, grid={"bpm": 123.456, "first": 0.52})
        self.receive("state", {"connected": True, "problem": "", "busy": False})
        self.receive("timeline", self.TIMELINE)
        self.receive("song", song)
        self.assertEqual(self.js("return Daw.bpm();"), 123.456)
        self.assertAlmostEqual(self.js("return Daw.songTime(20);"), 0.52 + 16 * 60 / 123.456, places=9)   # its bar 1 is beat 4
        self.js("Daw.P.tracks[2].clips[0].start = 4; return true;")                                       # the beat, from there
        # The drums' beat starts on bar 1: placed on the frame before it, the render that much
        # silence in - the kick lands on the beat, not on the nearest frame.
        self.js("document.querySelector('.daw-bar .btn.accent').click(); return true;")
        self.js("[...document.querySelectorAll('.menu-pop button')].find(b => /whole mix/.test(b.textContent)).click(); return true;")
        self.until("window.__sent.some(s => s[0] === 'place')", seconds=15)
        got = self.js("""const d = window.__sent.find(s => s[0] === 'place')[1];
                         const bin = atob(d.channels[0]), bytes = new Uint8Array(bin.length);
                         for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                         const x = new Float32Array(bytes.buffer);
                         return [d.frame, x.findIndex(v => Math.abs(v) > 1e-3)];""")
        self.js("""Daw.render(4, 8).then(b => { window.__plain = b.getChannelData(0).findIndex(v => Math.abs(v) > 1e-3); });
                   return true;""")                                        # the same, from the beat itself
        self.until("window.__plain !== undefined", seconds=15)
        self.assertEqual(got[0], 86424 + 12)
        self.assertAlmostEqual((got[1] - self.js("return window.__plain;")) / 48000, 0.48 / 24, delta=0.0005)

    def test_bar_1_before_the_timelines_start_is_placed_from_its_first_frame(self):
        # Bar 1 begins 0.44 s before the timeline (the song's bars carried back to its start):
        # a note on its 2nd beat, placed, is the clip's first frame's, that much in.
        self.with_song()
        self.js("""Daw.edit("x", () => { for (const t of Daw.P.tracks) t.clips = [];
                   Daw.P.tracks[2].clips.push({id: "n1", name: "N", start: 0, len: 4, notes: [[36, 1, 0.25, 0.9]]}); });
                   return true;""")
        start = self.js("return DawApp.frameOfBeat(0);")
        self.assertLess(start, 86400)
        self.js("document.querySelector('.daw-bar .btn.accent').click(); return true;")
        self.js("[...document.querySelectorAll('.menu-pop button')].find(b => /whole mix/.test(b.textContent)).click(); return true;")
        self.until("window.__sent.some(s => s[0] === 'place')", seconds=15)
        got = self.js("""const d = window.__sent.find(s => s[0] === 'place')[1];
                         const bin = atob(d.channels[0]), bytes = new Uint8Array(bin.length);
                         for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                         const x = new Float32Array(bytes.buffer);
                         return [d.frame, x.findIndex(v => Math.abs(v) > 1e-3) / 48000];""")
        self.js("""Daw.render(0, 4).then(b => { window.__plain = b.getChannelData(0).findIndex(v => Math.abs(v) > 1e-3) / 48000; });
                   return true;""")                                        # the same, from bar 1 itself
        self.until("window.__plain !== undefined", seconds=15)
        self.assertEqual(got[0], 86400)
        self.assertAlmostEqual(got[1], self.js("return window.__plain;") - (86400 - start) / 24, delta=0.0005)

    def test_an_older_projects_clips_stay_with_the_song(self):
        # Kept before beat 0 was the timeline's start: its beats were the song's, from its bar 1.
        old = {"bpm": 124, "song": "m1", "loop": {"on": True, "start": 0, "end": 8}, "cursor": 2, "tracks": [
            {"id": "t1", "kind": "inst", "sound": "pad", "clips": [{"id": "c1", "name": "Hit", "start": 0, "len": 4, "notes": [[60, 0, 1, 0.8]]}]}]}
        self.receive("project", old)
        self.with_song()
        got = self.js("return [Daw.P.tracks[0].clips[0].start, Daw.P.loop, Daw.P.cursor, Daw.P.anchor.at];")
        self.assertEqual(got, [4, {"on": True, "start": 4, "end": 12}, 6, 4])           # a bar on: the song's bar 1
        self.assertAlmostEqual(self.js("return Daw.songTime(4);"), 0.5, places=6)
        # The song moved two bars later in the edit: they go with it.
        self.receive("song", dict(self.SONG, start=86424 + round(2 * 4 * 60 / 124 * 24)))
        self.assertEqual(self.js("return Daw.P.tracks[0].clips[0].start;"), 12)

    def test_consoles_roll_reaches_to_the_loops_end_and_a_note_there_grows_the_clip(self):
        self.js("""const t = Daw.P.tracks[0];
                   t.clips = [{id: "c1", name: "Clip", start: 0, len: 4, notes: []}];
                   Daw.P.loop = {on: true, start: 0, end: 16}; Daw.select(t.id, "c1"); DawApp.show('console'); return true;""")
        grid = "const g = document.querySelector('.roll-grid'); return [parseFloat(g.style.width), g.getBoundingClientRect().left, g.getBoundingClientRect().top];"
        width, left, top = self.js(grid)
        px = width / 16                                                      # the loop's 4 bars, not the clip's 1
        self.assertGreaterEqual(px, 24)
        self.js(f"""const g = document.querySelector('.roll-grid');
                    g.dispatchEvent(new PointerEvent('pointerdown', {{button: 0, bubbles: true, clientX: {left + 10.1 * px}, clientY: {top + 60}}}));
                    g.dispatchEvent(new PointerEvent('pointerup', {{button: 0, bubbles: true, clientX: {left + 10.1 * px}, clientY: {top + 60}}}));
                    return true;""")
        got = self.js("const c = Daw.P.tracks[0].clips[0]; return [c.len, c.notes.map(n => n[1])];")
        self.assertEqual(got, [12, [10]])                                    # grown to the bar the note ends in
        self.assertIn("3", self.js("return document.querySelector('.con-clipbar select').selectedOptions[0].textContent;"))
        self.js("Daw.undo(); return true;")
        self.assertEqual(self.js("return Daw.P.tracks[0].clips[0].len;"), 4)
        # Never into the track's next clip; with the loop off, just the clip.
        room = "return DawViews.console.room(Daw.P.tracks[0].clips[0]);"
        self.js("""Daw.P.tracks[0].clips.push({id: "c2", name: "Next", start: 8, len: 4, notes: []}); return true;""")
        self.assertEqual(self.js(room), 8)
        self.js("Daw.P.loop.on = false; return true;")
        self.assertEqual(self.js(room), 4)

    def test_perform_picks_the_track_writes_chords_and_records_the_pads(self):
        self.js("DawApp.show('perform'); return true;")
        names = self.js("return [...document.querySelectorAll('.perf-top button')].map(b => b.textContent);")
        self.assertEqual(names, ["Chords", "Bass", "Drums", "Lead"])
        self.js("[...document.querySelectorAll('.perf-top button')][2].click(); return true;")
        self.assertEqual(self.js("return Daw.selTrack().kind;"), "drums")             # the keys play the kit now
        self.js("[...document.querySelectorAll('.perf-top button')][0].click(); return true;")
        # Its sound, beside the pads.
        self.js("""const s = document.querySelector('.pcard .ch select'); s.value = 'keys';
                   s.dispatchEvent(new Event('change', {bubbles: true})); return true;""")
        self.assertEqual(self.js("return Daw.P.tracks[0].sound;"), "keys")
        # Write: each pad's chord at the playhead, a bar long, the playhead on to the next bar.
        self.js("Daw.P.cursor = 0; Daw.P.tracks[0].clips = []; document.querySelector('.pads-write').click(); return true;")
        for i in (0, 5):
            self.js(f"""const p = document.querySelector('[data-pad="{i}"]');
                        p.dispatchEvent(new PointerEvent('pointerdown', {{button: 0, bubbles: true}}));
                        p.dispatchEvent(new PointerEvent('pointerup', {{button: 0, bubbles: true}})); return true;""")
        got = self.js("""const c = Daw.P.tracks[0].clips; return [c.length, c[0].start, c[0].len, Daw.P.cursor,
                           [...new Set(c[0].notes.map(n => n[1]))], c[0].notes.every(n => n[2] === 4)];""")
        self.assertEqual(got, [1, 0, 8, 8, [0, 4], True])
        first = self.js("return Daw.P.tracks[0].clips[0].notes.filter(n => n[1] === 0).map(n => n[0]).sort((a, b) => a - b);")
        self.assertEqual(first, sorted(self.js("return Daw.chordsInKey()[0].notes;")))
        self.js("Daw.undo(); return true;")
        self.assertEqual(self.js("return [Daw.P.tracks[0].clips[0].len, Daw.P.cursor];"), [4, 4])
        # Record: the pads' track, from the playhead.
        self.js("[...document.querySelectorAll('.perf-top button')][2].click(); document.querySelector('.pads-rec').click(); return true;")
        self.assertEqual(self.js("return [Daw.recording(), Daw.selTrack().name];"), [True, "Chords"])

    def test_the_bars_line_up_with_the_song_or_the_timeline(self):
        self.with_song()                       # the song's bar 1: 1.5 s into the edit, beat 4 on its grid
        # With the song (the default): its first downbeat is bar 1, the bars before it 0, -1...
        got = self.js("return [Daw.P.align, Daw.barOf(Daw.songAt()), Daw.barOf(0), document.querySelector('.bars span').textContent];")
        self.assertEqual(got, ["song", 1, 0, "0"])
        self.assertIn("1–4", self.js("return document.querySelector('.tbtn .loop-bars').textContent;"))   # a new project's loop: the song's first bars
        clip = self.js("const c = Daw.P.tracks[2].clips[0]; return [c.start, DawApp.frameOfBeat(c.start)];")
        self.assertEqual(clip[0], 4)
        # With the timeline: bar 1 on its first frame; nothing moves in time.
        self.js("[...document.querySelectorAll('.align-pick button')].find(b => b.textContent === 'Timeline').click(); return true;")
        got = self.js("const c = Daw.P.tracks[2].clips[0]; return [Daw.P.align, DawApp.frameOfBeat(0), Daw.barOf(0), DawApp.frameOfBeat(c.start)];")
        self.assertEqual(got[:3], ["timeline", 86400, 1])
        self.assertAlmostEqual(got[3], clip[1], places=2)
        self.assertAlmostEqual(self.js("return Daw.songAt();"), 1.5 / (60 / 124), places=4)
        self.js("Daw.undo(); return true;")                                  # back with the song
        self.assertEqual(self.js("return [Daw.P.align, Daw.songAt(), Daw.P.tracks[2].clips[0].start];"), ["song", 4, 4])
        self.assertLess(self.js("return DawApp.frameOfBeat(0);"), 86400)

    def test_each_timeline_has_its_own_project_and_saves_go_to_theirs(self):
        self.receive("project", {"scope": "p|t1", "project": {"bpm": 97, "tracks": [{"id": "x", "name": "Keys", "kind": "inst", "sound": "keys", "clips": []}]}})
        self.assertEqual(self.js("return [Daw.scope, Daw.P.bpm, Daw.P.tracks.map(t => t.name)];"), ["p|t1", 97, ["Keys"]])
        self.js("window.__sent = []; Daw.edit('x', () => { Daw.P.bpm = 98; }); return true;")
        # Another timeline before the save went: it goes first, with its own; the new one is a new project.
        self.receive("project", {"scope": "p|t2", "project": None})
        sent = self.js("return window.__sent.filter(s => s[0] === 'project').map(s => [s[1].scope, s[1].project.bpm]);")
        self.assertEqual(sent, [["p|t1", 98]])
        self.assertEqual(self.js("return [Daw.scope, Daw.P.tracks.map(t => t.name)];"), ["p|t2", ["Chords", "Bass", "Drums", "Lead"]])
        self.until("window.__sent.filter(s => s[0] === 'project').length === 2", seconds=3)
        self.assertEqual(self.js("return window.__sent.filter(s => s[0] === 'project')[1][1].scope;"), "p|t2")

    def test_clips_snap_to_the_song_s_beats_or_go_where_they_re_let_go(self):
        self.with_song()
        self.js("[...document.querySelectorAll('.align-pick button')].find(b => b.textContent === 'Timeline').click(); return true;")
        at = self.js("return Daw.songAt();")                           # 3.1 beats: the song's beats are off the grid
        self.js("""const t = Daw.P.tracks[0]; t.clips = [{id: "c1", name: "Clip", start: 0, len: 4, notes: [[60, 0, 1, 0.8]]}];
                   Daw.select(t.id, "c1"); Daw.changed('clips'); return true;""")
        px = self.js("return parseFloat(getComputedStyle(document.querySelector('.lanes-grid')).getPropertyValue('--beat'));")
        drag = """const n = document.querySelector('.clip[data-clip="c1"]'), r = n.getBoundingClientRect(), y = r.top + 20;
                  n.dispatchEvent(new PointerEvent('pointerdown', {button: 0, bubbles: true, clientX: r.left + 20, clientY: y}));
                  n.dispatchEvent(new PointerEvent('pointermove', {bubbles: true, clientX: r.left + 20 + %s, clientY: y}));
                  n.dispatchEvent(new PointerEvent('pointerup', {bubbles: true, clientX: r.left + 20 + %s, clientY: y}));
                  return Daw.P.tracks[0].clips[0].start;"""
        to = at + 5 - 0.02                                             # a hair before a beat of the song
        got = self.js(drag % (to * px, to * px))
        self.assertAlmostEqual(got, round(at + 5, 4), places=3)         # on it
        self.assertTrue(self.js("return document.querySelector('.magnet').getAttribute('aria-pressed') === 'true';"))
        self.key("KeyN")                                                # snapping off: where it's let go
        self.assertFalse(self.js("return Daw.P.snap_clips;"))
        start = self.js("return Daw.P.tracks[0].clips[0].start;")
        got = self.js(drag % (2.37 * px, 2.37 * px))
        self.assertAlmostEqual(got, start + 2.37, delta=0.02)

    def test_a_beat_is_copied_and_pasted_at_the_playhead_again_and_again(self):
        self.js("""const d = Daw.P.tracks.find(t => t.kind === 'drums');
                   for (const t of Daw.P.tracks) t.clips = [];
                   d.clips = [{id: "b1", name: "Beat", start: 0, len: 4, notes: [[36, 0, 0.25, 0.9], [38, 1, 0.25, 0.9]]}];
                   Daw.select(d.id, "b1"); Daw.changed('clips'); return true;""")
        beats = "const d = Daw.P.tracks.find(t => t.kind === 'drums'); return d.clips.map(c => [c.start, c.len, c.notes.length]);"
        self.key("KeyV", ctrl=True)                                         # nothing copied yet: said
        self.assertIn("Copy a clip first", self.js("return document.querySelector('.toast').textContent;"))
        self.key("KeyC", ctrl=True)
        self.js("DawApp.moveCursor(8); return true;")
        self.key("KeyV", ctrl=True)
        self.key("KeyV", ctrl=True)                                         # again: straight after the last
        self.assertEqual(self.js(beats), [[0, 4, 2], [8, 4, 2], [12, 4, 2]])
        self.assertEqual(self.js("return Daw.P.cursor;"), 16)
        ids = self.js("const d = Daw.P.tracks.find(t => t.kind === 'drums'); return d.clips.map(c => c.id);")
        self.assertEqual(len(set(ids)), 3)                                  # each its own clip
        self.assertEqual(self.js("return Daw.undoLabel();"), "Paste a clip")
        # An instrument track selected: the beat goes on the drum track it came from.
        self.js("Daw.select(Daw.P.tracks.find(t => t.kind !== 'drums').id, ''); return true;")
        self.key("KeyV", ctrl=True)
        self.assertEqual(self.js(beats)[-1], [16, 4, 2])
        self.assertEqual(self.js("return Daw.P.tracks.filter(t => t.kind !== 'drums').every(t => !t.clips.length);"), True)
        # Cut: gone, and pasted back where the playhead is.
        self.js("const d = Daw.P.tracks.find(t => t.kind === 'drums'); Daw.select(d.id, d.clips[0].id); return true;")
        self.key("KeyX", ctrl=True)
        self.assertEqual([b[0] for b in self.js(beats)], [8, 12, 16])
        self.assertEqual(self.js("return Daw.undoLabel();"), "Cut a clip")
        self.js("DawApp.moveCursor(24); return true;")
        self.key("KeyV", ctrl=True)
        self.assertEqual([b[0] for b in self.js(beats)], [8, 12, 16, 24])
        # Right-click an empty stretch of a track: Paste here.
        self.js("Daw.changed('clips'); return true;")
        self.js("""const body = document.querySelector('.lane[data-track="' + Daw.P.tracks.find(t => t.kind === 'drums').id + '"] .body');
                   const r = body.getBoundingClientRect();
                   body.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: r.left + 2, clientY: r.top + 5}));
                   return true;""")
        self.js("[...document.querySelectorAll('.menu-pop button')].find(b => b.textContent === 'Paste here').click(); return true;")
        self.assertEqual([b[0] for b in self.js(beats)], [0, 8, 12, 16, 24])

    def test_d_or_the_menus_disable_a_clip_or_a_track(self):
        self.js("""const t = Daw.P.tracks[0]; t.clips = [{id: "c1", name: "Clip", start: 0, len: 4, notes: [[60, 0, 1, 0.8]]}];
                   Daw.P.tracks[2].clips = []; Daw.select(t.id, "c1"); Daw.changed('clips'); return true;""")
        self.key("KeyD")                                                # the tracks haven't the focus: a note
        self.assertEqual(self.sounding(), [64])
        self.key("KeyD", "keyup")
        self.js("""const n = document.querySelector('.clip[data-clip="c1"]');
                   n.dispatchEvent(new PointerEvent('pointerdown', {button: 0, bubbles: true}));
                   n.dispatchEvent(new PointerEvent('pointerup', {bubbles: true})); return true;""")
        self.key("KeyD")                                                # the tracks clicked last: the clip
        self.assertEqual(self.js("return [Daw.P.tracks[0].clips[0].off, Daw.usedRange(), !!document.querySelector('.clip.off')];"),
                         [True, None, True])                            # not placed, drawn struck through
        self.js("""document.querySelector('.clip[data-clip="c1"]').dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, clientX: 300, clientY: 300}));
                   [...document.querySelectorAll('.menu-pop button')].find(b => /Enable the clip/.test(b.textContent)).click(); return true;""")
        self.assertIsNone(self.js("return Daw.P.tracks[0].clips[0].off || null;"))
        # A track, from its name's menu: not heard.
        self.js("""document.querySelector('.lane[data-track] .head').dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, clientX: 100, clientY: 200}));
                   [...document.querySelectorAll('.menu-pop button')].find(b => /Disable the track/.test(b.textContent)).click(); return true;""")
        self.assertEqual(self.js("return [Daw.P.tracks[0].off, Daw.audible(Daw.P.tracks[0]), !!document.querySelector('.lane.off')];"), [True, False, True])
        self.js("Daw.undo(); return true;")
        self.assertTrue(self.js("return Daw.audible(Daw.P.tracks[0]);"))

    def test_a_long_edit_makes_only_whats_near_the_view(self):
        # 10 minutes, a cut every second: 599 of them, 2 beats apart at 120 BPM.
        self.receive("state", {"connected": True, "problem": "", "busy": False})
        self.receive("timeline", dict(self.TIMELINE, songs=[], end=86400 + 24 * 600, cuts=[86400 + 24 * i for i in range(1, 600)]))
        cuts = "return [...document.querySelectorAll('.lane.picture .cut')].map(c => Number(c.dataset.beat));"
        near = self.js(cuts)
        self.assertTrue(near and len(near) < 300, len(near))
        self.assertEqual(min(near), 2)
        self.assertEqual(self.js("return document.querySelectorAll('.pic-guides .guide').length;"), len(near))
        # Scrolled to the end: the cuts there are made, the ones at the start gone.
        self.js("const l = document.querySelector('.lanes'); l.scrollLeft = l.scrollWidth; l.dispatchEvent(new Event('scroll')); return true;")
        self.until("[...document.querySelectorAll('.lane.picture .cut')].some(c => Number(c.dataset.beat) >= 1190)")
        self.assertGreater(min(self.js(cuts)), 2)
        # The mixer's meters are still the mixer's (not the waveforms').
        self.assertTrue(self.js("return [...document.querySelectorAll('.daw-mixer .strip')].length > 0;"))

    def test_the_whole_timeline_and_its_audio_are_shown(self):
        tl = dict(self.TIMELINE, end=86400 + 24 * 200, audio=[
            {"index": 1, "name": "Dialogue", "clips": [
                {"id": "d1", "name": "Tess.wav", "start": 86400, "end": 86400 + 24 * 10, "offset": 2.0, "peaks": "k1",
                 "color": "", "enabled": True},
                {"id": "d2", "name": "Sam.wav", "start": 86400 + 24 * 150, "end": 86400 + 24 * 160, "offset": 0.0, "peaks": "k2",
                 "color": "#E8664C", "enabled": False}]},
            {"index": 2, "name": "Music", "clips": [
                {"id": "m1", "name": "Bright Days (Music).wav", "start": 86424, "end": 89000, "offset": 0.0, "peaks": None,
                 "color": "", "enabled": True}]}])
        self.receive("state", {"connected": True, "problem": "", "busy": False})
        self.receive("timeline", tl)
        self.receive("song", self.SONG)
        # The DAW spans the edit: 200 s at 124 BPM is 413 beats and a bit, in whole bars.
        self.assertGreaterEqual(self.js("return Daw.projectBeats();"), (200 + 0.44) / (60 / 124))
        lanes = self.js("""return [...document.querySelectorAll('.lane.edit-audio')].map(l => [l.querySelector('.head').textContent,
                               [...l.querySelectorAll('.eclip')].map(c => [c.textContent, c.classList.contains('off'), c.classList.contains('is-song')])]);""")
        self.assertEqual(lanes, [["A1Dialogue", [["Tess.wav", False, False], ["Sam.wav", True, False]]],
                                 ["A2Music", [["Bright Days (Music).wav", False, True]]]])
        # Where a clip is: the beat of its first frame.
        px = self.js("return parseFloat(getComputedStyle(document.querySelector('.lanes-grid')).getPropertyValue('--beat'));")
        x = self.js("""const l = document.querySelectorAll('.lane.edit-audio')[0], b = l.querySelector('.body').getBoundingClientRect().left;
                       return l.querySelectorAll('.eclip')[1].getBoundingClientRect().left - b;""")
        self.assertAlmostEqual(x, self.js(f"return Daw.beatOfFrame({86400 + 24 * 150});") * px, delta=1)
        # A waveform as page.py sends it: drawn where the clip is (2 s into its file).
        loud = base64.b64encode(bytes([0] * 50 + [255] * 250)).decode()
        self.receive("peaks", {"key": "k1", "rate": 25, "data": loud})
        self.assertEqual(self.js("return DawApp.peaks('k1').codes.length;"), 300)
        self.wait(400)
        lit = self.js("""const c = document.querySelector('.lane.edit-audio .wave'), g = c.getContext('2d');
                         const d = g.getImageData(0, 0, c.width, c.height).data; let n = 0;
                         for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n;""")
        self.assertGreater(lit, 0)
        # Hidden with its heading; kept so.
        self.js("document.querySelector('.edit-toggle').click(); return true;")
        self.assertEqual(self.js("return [document.querySelectorAll('.lane.edit-audio').length, Daw.P.edit_audio];"), [0, False])

    def test_the_songs_file_is_played_on_the_daws_own_clock(self):
        # The WAV page.py writes is read into the engine (a file:// read the page allows),
        # then played from a buffer, started on the beat - not by an <audio> and a timer.
        import wave
        from PySide6.QtCore import QUrl
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "song-0123456789abcdef.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"\x00\x10" * 2 * 44100 * 3)
        self.receive("song", dict(self.SONG, url=QUrl.fromLocalFile(path).toString(), length_s=3))
        self.until("Daw.songReady()", seconds=10)
        self.js("Daw.start(); return true;")
        self.until("Daw.playing() && Daw.position() > 0.5")

    # The song's tempo, put right by hand (app.js's strip, engine.js's setSongGrid).
    TEMPO = """const bar = document.querySelector('.tempo-bar');
        return {open: !bar.hidden, bpm: bar.querySelector('.tempo-bpm').value, grid: Daw.songGrid(), hand: Daw.handGrid(),
                daw: Daw.bpm(), cursor: Daw.P.cursor, state: bar.querySelector('.tempo-state').textContent,
                reset: [...bar.querySelectorAll('.tempo-end .btn')].some(b => /heard/.test(b.textContent) && !b.hidden),
                times: [...bar.querySelectorAll('.tempo-x')].map(b => [b.textContent, b.disabled])};"""

    def tempo_button(self, pattern, by="textContent"):
        self.js(f"[...document.querySelectorAll('.tempo-bar button')].find(b => {pattern}.test(b.{by})).click(); return true;")

    def type_bpm(self, value):
        self.js(f"const f = document.querySelector('.tempo-bpm'); f.focus(); f.value = '{value}';"
                " f.dispatchEvent(new Event('change', {bubbles: true})); return true;")

    def test_the_songs_tempo_is_corrected_by_hand(self):
        beat = 60 / 124
        self.with_song()
        self.js("document.querySelector('.lane.song .song-tempo').click(); return true;")    # the song lane's tempo
        got = self.js(self.TEMPO)
        self.assertEqual((got["open"], got["bpm"], got["hand"], got["state"]), (True, "124", None, "As Buddy heard it"))
        self.assertEqual(got["times"], [["½62", False], ["¾93", False], ["4/3165.3", False], ["2×248", False]])
        # A library's BPM typed in: the song's grid and the DAW's tempo, bar 1 where it was.
        self.type_bpm("123.5")
        got = self.js(self.TEMPO)
        self.assertEqual((got["grid"], got["daw"], got["state"]), ({"bpm": 123.5, "first": 0.5}, 123.5, "Set by hand"))
        self.assertTrue(got["reset"])
        self.assertEqual(self.js("return Daw.P.grids;"), {"d:\\music\\bright days (music).wav": {"bpm": 123.5, "first": 0.5}})
        # Heard 4:3 off: three quarters as fast. Twice: double, and back.
        self.tempo_button("/¾/")
        self.assertAlmostEqual(self.js("return Daw.bpm();"), 92.625, places=6)
        self.tempo_button("/^2×/")
        self.assertAlmostEqual(self.js("return Daw.bpm();"), 185.25, places=6)
        self.js("Daw.undo(); Daw.undo(); return true;")
        self.assertEqual(self.js("return Daw.songGrid();"), {"bpm": 123.5, "first": 0.5})
        self.tempo_button("/heard/")                                       # what Buddy heard again
        got = self.js(self.TEMPO)
        self.assertEqual((got["grid"], got["hand"], got["bpm"], got["reset"]), ({"bpm": 124, "first": 0.5}, None, "124", False))
        self.assertEqual(self.js("return Daw.P.grids;"), {})
        # Bar 1 a beat later; the grid nudged later 5 ms, then 1 ms (Shift).
        self.tempo_button("/a beat later/", "title")
        self.assertAlmostEqual(self.js("return Daw.songGrid().first;"), 0.5 + beat, places=4)
        self.tempo_button("/^5 ms/")
        self.js("const b = [...document.querySelectorAll('.tempo-nudge')][1];"
                " b.dispatchEvent(new MouseEvent('click', {shiftKey: true, bubbles: true})); return true;")
        self.assertAlmostEqual(self.js("return Daw.songGrid().first;"), 0.5 + beat + 0.006, places=4)
        self.assertEqual(self.js("return document.querySelector('.tempo-first').textContent;"), f"{0.5 + beat + 0.006:.3f} s")

    def test_a_downbeat_is_put_where_the_song_is_heard(self):
        beat = 60 / 124
        self.with_song()
        self.js("DawApp.openTempo(); return true;")
        # The song's bar 1 (0.5 s in) is beat 4: the bars carried back to the timeline's start.
        self.assertEqual(self.js("return Daw.songAt();"), 4)
        # The playhead a hair after the 2nd beat of a bar: that beat is a downbeat now, on the
        # grid. The bars run on from it both ways - bar 1 the first at the timeline's start - and
        # the playhead, on the same moment of the song, is just after a bar's start.
        self.js("Daw.P.cursor = 13.05; return true;")
        self.tempo_button("/^Downbeat here/")
        got = self.js(self.TEMPO)
        self.assertAlmostEqual(got["grid"]["first"], 0.5 + beat, places=4)    # the song's first downbeat
        self.assertAlmostEqual(got["cursor"], 16.05, places=3)
        self.assertEqual(self.js("return Daw.songAt();"), 8)
        # Well off any beat: there, to the millisecond.
        self.js("Daw.P.cursor = 22.4; return true;")
        here = self.js("return Daw.songTime(22.4);")
        bar = 4 * beat
        self.tempo_button("/^Downbeat here/")
        first = self.js("return Daw.songGrid().first;")
        self.assertAlmostEqual(first, here - bar * int(here / bar), places=4)
        # Bar 1 is at the timeline's start, or less than a bar before it; Place puts the song's
        # downbeats where they are in the edit.
        start = self.js("return DawApp.frameOfBeat(0);")
        self.assertTrue(86400 - bar * 24 < start <= 86400, start)
        self.assertAlmostEqual(self.js("return DawApp.frameOfBeat(Daw.songAt());"), 86424 + first * 24, places=2)

    def test_correcting_while_it_plays_goes_on_from_the_same_moment(self):
        self.with_song()
        self.js("Daw.P.loop.on = false; Daw.P.cursor = 8; DawApp.openTempo(); Daw.start(); return true;")
        self.until("Daw.playing() && Daw.position() > 8.5")
        before = self.js("return Daw.songTime(Daw.position());")
        self.type_bpm("100")
        got = self.js("return [Daw.playing(), Daw.bpm(), Daw.songTime(Daw.position())];")
        self.assertEqual(got[:2], [True, 100])
        self.assertAlmostEqual(got[2], before, delta=0.25)                  # the song carried on

    def test_a_tempo_set_by_hand_is_kept_for_the_songs_file(self):
        self.with_song()
        self.js("DawApp.openTempo(); return true;")
        self.type_bpm("90")
        kept = self.js("return Daw.P.grids;")
        self.assertEqual(kept, {"d:\\music\\bright days (music).wav": {"bpm": 90, "first": 0.5}})
        # The clip trimmed 2 s at its head (listened to again): bar 1 is the same moment of the music.
        self.receive("song", dict(self.SONG, offset=2.0))
        self.assertEqual(self.js("return Daw.songGrid();"), {"bpm": 90, "first": -1.5})
        # Another song: what Buddy heard in it.
        self.receive("song", dict(self.SONG, media="d:\\music\\other.wav", bpm=100, grid={"bpm": 100.02, "first": 0.1}))
        self.assertEqual(self.js("return Daw.songGrid();"), {"bpm": 100.02, "first": 0.1})

    def test_pre_roll_plays_from_a_beat_before_the_playhead(self):
        self.with_song()                                                    # 124 BPM: a beat is 0.48 s
        self.js("Daw.P.loop.on = false; Daw.P.cursor = 8; return true;")
        self.js("[...document.querySelectorAll('.daw-bar .tbtn.toggle')].find(b => b.textContent === 'Pre-roll').click(); return true;")
        self.assertTrue(self.js("return Daw.P.pre_roll;"))
        self.js("window.__t0 = performance.now(); Daw.start(); window.__at = Daw.position(); return true;")
        self.assertEqual(self.js("return window.__at;"), 8)                 # the playhead waits on its beat...
        self.until("Daw.position() > 8.02")
        self.assertGreater(self.js("return (performance.now() - window.__t0) / 1000;"), 60 / 124 * 0.9)   # ...a beat
        # Off: it starts at once.
        self.js("Daw.stop(); Daw.P.cursor = 8; DawApp.syncBar();"
                " [...document.querySelectorAll('.daw-bar .tbtn.toggle')].find(b => b.textContent === 'Pre-roll').click();"
                " window.__t0 = performance.now(); Daw.start(); return true;")
        self.until("Daw.position() > 8.02")
        self.assertLess(self.js("return (performance.now() - window.__t0) / 1000;"), 60 / 124 * 0.9)

    def test_a_song_with_no_steady_beat_gets_its_tempo_typed(self):
        self.receive("timeline", self.TIMELINE)
        self.receive("song", dict(self.SONG, bpm=None, bars=[], grid=None))
        self.js("[...document.querySelectorAll('.lcd')][0].click(); return true;")             # Key and tempo...
        self.js("document.querySelector('.modal .kt-tempo').click(); return true;")             # ...the song's tempo
        got = self.js(self.TEMPO)
        self.assertEqual((got["open"], got["grid"], got["bpm"]), (True, None, "120"))
        self.assertIn("no steady beat", got["state"])
        self.assertFalse(self.js("return !!document.querySelector('.modal');"))
        self.type_bpm("96")
        self.assertEqual(self.js("return [Daw.songBpm(), Daw.bpm(), document.querySelector('.lcd .unsure') === null];"), [96, 96, True])

    def test_the_tracks_scroll_zoom_and_the_ruler_moves_the_playhead(self):
        self.with_song()                                          # 80 s of song: more than fits
        wheel = """const lanes = document.querySelector('.lanes'), r = lanes.getBoundingClientRect();
                   const e = new WheelEvent('wheel', Object.assign({deltaY: 120, bubbles: true, cancelable: true,
                       clientX: r.left + 400, clientY: r.top + 100}, %s));
                   lanes.querySelector('.lane .body').dispatchEvent(e); return e.defaultPrevented;"""
        beat = "return parseFloat(getComputedStyle(document.querySelector('.lanes-grid')).getPropertyValue('--beat'));"
        self.assertTrue(self.js("const l = document.querySelector('.lanes'); return l.scrollWidth > l.clientWidth;"))
        self.assertTrue(self.js(wheel % "{shiftKey: true}"))                                   # Shift + scroll: along
        self.assertGreater(self.js("return document.querySelector('.lanes').scrollLeft;"), 0)
        before = self.js(beat)
        self.assertTrue(self.js(wheel % "{altKey: true, deltaY: -240}"))                       # Alt + scroll: zoom in
        self.until(f"parseFloat(getComputedStyle(document.querySelector('.lanes-grid')).getPropertyValue('--beat')) > {before}")
        # A step draws nothing again (the clips stay the same nodes, placed in beats); once
        # the zooming stops, all is drawn again at the new size.
        self.js("window.__clip = document.querySelector('.clip'); return true;")
        zoomed = self.js(beat)
        self.assertTrue(self.js(wheel % "{altKey: true, deltaY: 0, deltaX: 240}"))             # ...as Qt on Windows sends it
        self.until(f"parseFloat(getComputedStyle(document.querySelector('.lanes-grid')).getPropertyValue('--beat')) < {zoomed}")
        self.assertTrue(self.js("return document.querySelector('.clip') === window.__clip;"))
        clip = "const c = document.querySelector('.clip'), px = parseFloat(getComputedStyle(document.querySelector('.lanes-grid')).getPropertyValue('--beat'));"
        self.assertAlmostEqual(self.js(clip + " return (c.getBoundingClientRect().width + 1) / px;"), 4, delta=0.05)   # a bar, at any zoom
        # Settled: its notes drawn again at its new size, in place - the same clip, nothing rebuilt.
        self.until("(() => { const c = window.__clip.querySelector('canvas');"
                   " return c.width === Math.round(c.clientWidth * (window.devicePixelRatio || 1)); })()")
        self.assertTrue(self.js("return document.querySelector('.clip') === window.__clip;"))
        # The arrows: a beat, Shift a bar.
        self.js("Daw.P.cursor = 5; Daw.changed('cursor'); return true;")
        self.key("ArrowRight", shift=True)
        self.assertEqual(self.js("return Daw.P.cursor;"), 8)

    def test_bars_to_loop_are_chosen_in_the_loop_strip_and_the_playhead_in_the_bars(self):
        """The ruler's two rows. The bar numbers only move the playhead - a click, or a drag
        to scrub, never the loop. The loop strip above them only chooses bars: a drag across
        it loops just those - whole bars, lit down the tracks, the playhead to their start;
        the lit bars slide, an end drags for more or fewer, a click turns the loop on or off;
        a double-click is one bar; a clip's menu loops its bars."""
        # Each step finds the ruler again: choosing bars draws it anew. x is in beats.
        ruler = """const b = document.querySelector(%s), r = b.getBoundingClientRect(), px = %s;
                   const t = %s ? b.querySelector(%s) : b, o = {button: 0, bubbles: true, pointerId: 1, shiftKey: %s};
                   const p = (type, beat) => t.dispatchEvent(new PointerEvent(type, Object.assign({clientX: r.left + beat * px, clientY: r.top + 6}, o)));
                   %s return true;"""
        px = self.js("return parseFloat(getComputedStyle(document.querySelector('.lanes-grid')).getPropertyValue('--beat'));")

        def act(steps, target=None, shift=False, row=".loopbar"):
            self.js(ruler % (json.dumps(row), px, "true" if target else "false", json.dumps(target or ""),
                             "true" if shift else "false", steps))

        state = "return [Daw.P.loop.start, Daw.P.loop.end, Daw.P.loop.on, Daw.P.cursor];"
        self.js("Daw.P.loop.on = false; Daw.changed('loop'); return true;")
        act("p('pointerdown', 2); p('pointerup', 2);", row=".bars")                       # a click: the playhead
        self.assertEqual(self.js(state), [0, 16, False, 2])
        act("p('pointerdown', 3); p('pointermove', 9); p('pointermove', 22.1); p('pointerup', 22.1);", row=".bars")
        self.assertEqual(self.js(state), [0, 16, False, 22])                             # a drag: scrubbed, no loop
        self.assertEqual(self.js("return document.querySelector('.range').classList.contains('off');"), True)   # dimmed, still there
        act("p('pointerdown', 5.5); p('pointermove', 9); p('pointermove', 14.2); p('pointerup', 14.2);", shift=True)
        self.assertEqual(self.js(state), [4, 16, True, 4])                               # Shift: whole bars - 2-4, from bar 2
        self.assertEqual(self.js("return document.querySelector('.range-label').textContent;"), "2–4")
        self.assertIn("2–4", self.js("return document.querySelector('.daw-bar .tbtn.toggle').textContent;"))
        shade = self.js("const s = document.querySelector('.loop-shade'); return [s.hidden, parseFloat(s.style.left), parseFloat(s.style.width)];")
        self.assertEqual(shade, [False, 150 + 4 * px, 12 * px])                          # lit down the tracks
        act("p('pointerdown', 16); p('pointermove', 23.6); p('pointerup', 23.6);", ".range-edge.end", shift=True)
        self.assertEqual(self.js(state)[:2], [4, 24])                                    # dragged out to bar 6
        act("p('pointerdown', 10); p('pointermove', 18.3); p('pointerup', 18.3);", ".range-label", shift=True)
        self.assertEqual(self.js(state), [12, 32, True, 12])                             # slid on two bars, length kept
        act("p('pointerdown', 20); p('pointerup', 20);", ".range-label")                 # a click on it: the loop off...
        self.assertEqual(self.js(state)[:3], [12, 32, False])
        self.assertEqual(self.js("return [document.querySelector('.loop-shade').hidden, document.querySelector('.range').classList.contains('off')];"),
                         [True, True])
        act("p('pointerdown', 20); p('pointerup', 20);", ".range-label")                 # ...and on again
        self.assertEqual(self.js(state)[:3], [12, 32, True])
        act("p('pointerdown', 6.5); p('pointermove', 3.2); p('pointerup', 3.2);")
        self.assertEqual(self.js(state)[:2], [3.25, 6.5])                                # without: any length, on the 1/16 grid, either way
        self.assertIn("1–2", self.js("return document.querySelector('.daw-bar .tbtn.toggle').textContent;"))   # bars 1 to 2
        self.js(f"""const b = document.querySelector('.loopbar'), r = b.getBoundingClientRect();
                    b.dispatchEvent(new MouseEvent('dblclick', {{clientX: r.left + 30.5 * {px}, clientY: r.top + 6, bubbles: true}}));
                    return true;""")
        self.assertEqual(self.js(state), [28, 32, True, 28])                             # just bar 8
        self.js("Daw.undo(); return true;")
        self.assertEqual(self.js(state)[:2], [3.25, 6.5])
        # Playing, new bars are gone to straight away.
        self.js("Daw.start(); return true;")
        self.until("Daw.playing() && Daw.position() > 3.2")
        act("p('pointerdown', 33); p('pointermove', 41); p('pointerup', 41);", shift=True)
        self.until("Daw.position() >= 32 && Daw.position() < 33")
        self.assertEqual(self.js(state)[:2], [32, 44])
        self.js("Daw.stop(); return true;")
        # A clip's menu: its bars.
        self.js("""const t = Daw.P.tracks[0]; t.clips.push({id: 'k1', name: 'Hook', start: 48, len: 8, notes: [[60, 0, 1, 0.8]]});
                   Daw.changed('clips'); return true;""")
        self.js("""const c = document.querySelector('.clip[data-clip=k1]');
                   c.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 300, clientY: 300}));
                   [...document.querySelectorAll('.menu-pop button')].find(b => b.textContent === 'Loop these bars').click(); return true;""")
        self.assertEqual(self.js(state), [48, 56, True, 48])

    def test_space_only_plays_and_pauses(self):
        # On a focused button, the snap list, a knob or the search box: Space is caught first,
        # so no button is pressed, no list opens and no knob turns - only text takes a space.
        press = """const t = document.querySelector(%s); t.focus();
                   const down = new KeyboardEvent('keydown', {key: ' ', code: 'Space', bubbles: true, cancelable: true});
                   const up = new KeyboardEvent('keyup', {key: ' ', code: 'Space', bubbles: true, cancelable: true});
                   t.dispatchEvent(down); t.dispatchEvent(up); return [down.defaultPrevented, up.defaultPrevented];"""
        loop = self.js("return Daw.P.loop.on;")
        self.assertEqual(self.js(press % "'.daw-bar .tbtn.toggle'"), [True, True])           # the Loop button
        self.assertEqual(self.js("return [Daw.playing(), Daw.P.loop.on];"), [True, loop])
        self.until("Daw.position() > 0.5")
        self.assertEqual(self.js(press % "'.daw-bar select.snap'"), [True, True])             # paused...
        self.assertFalse(self.js("return Daw.playing();"))
        paused = self.js("return Daw.P.cursor;")
        self.assertGreater(paused, 0.4)                                                       # ...where it got to
        self.assertEqual(self.js(press % "'.daw-mixer .knob'"), [True, True])             # plays on from there
        self.until("Daw.playing()")
        self.assertGreaterEqual(self.js("return Daw.position();"), paused - 0.01)
        self.js(press % "'.daw-mixer .knob'")
        self.assertEqual(self.js(press % "'.daw-search'"), [False, False])                    # a space, typed
        self.assertFalse(self.js("return Daw.playing();"))

if __name__ == "__main__":
    unittest.main()
