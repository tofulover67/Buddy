"""Audio Assistant's Effects: a processed clip found and tied to the clip it
stands in for (resolve_ext.link_effects), what a clip's effects are made from
and the stretch Preview plays (fx.py), and the Preview and Apply jobs - on
made-up timelines and throwaway files, never Resolve."""

import os
import tempfile
import unittest
import wave
from unittest import mock

import _paths  # noqa: F401

try:
    import numpy as np
    from pages.audio_assistant import beats, ducking, fx, render, resolve_ext, riding
    HAVE_DEPS = True
except ImportError:  # pragma: no cover - numpy or PySide6 missing
    HAVE_DEPS = False

FPS = 24.0
RATE = 48000


def clip(uid, start, end, path, enabled=True, offset=0.0, fx_=None, **extra):
    media = {"path": path, "key": uid, "fx": fx_}
    return {"id": uid, "name": os.path.basename(path), "start": start, "end": end, "enabled": enabled,
            "offset": offset, "media": media, "channels": None, "curve": None, **extra}


def side(source, chain=(), file_start=0.5, channels=None):
    return {"chain": list(chain), "source": source, "file_start": file_start, "channels_used": channels}


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class LinkTests(unittest.TestCase):
    def test_a_processed_clip_finds_the_clip_it_stands_in_for(self):
        chain = [{"id": "denoise", "on": True, "strength": 0.5}]
        tracks = [
            {"index": 1, "clips": [clip("a", 100, 200, "D:/m/talk.wav", enabled=False),
                                   clip("b", 200, 300, "D:/m/talk.wav", enabled=False)]},
            {"index": 2, "clips": [clip("p", 100, 200, "D:/m/Buddy Audio/talk - Buddy 1.wav",
                                        fx_=side("D:/m/talk.wav", chain))]},
            {"index": 3, "clips": [clip("q", 200, 300, "D:/m/Buddy Audio/talk - Buddy 2.wav",
                                        fx_=side("D:/m/other.wav"))]},
        ]
        resolve_ext.link_effects(tracks)
        processed = tracks[1]["clips"][0]["fx"]
        self.assertEqual(processed, {"chain": chain, "original": "a"})
        self.assertIsNone(tracks[2]["clips"][0]["fx"]["original"])       # another file: not its clip
        self.assertIsNone(tracks[0]["clips"][0]["fx"])

    def test_a_clip_turned_on_or_elsewhere_is_not_the_original(self):
        tracks = [
            {"index": 1, "clips": [clip("on", 100, 200, "D:/m/talk.wav"),
                                   clip("later", 150, 250, "D:/m/talk.wav", enabled=False)]},
            {"index": 2, "clips": [clip("p", 100, 200, "D:/m/p.wav", fx_=side("D:/m/talk.wav"))]},
        ]
        resolve_ext.link_effects(tracks)
        self.assertIsNone(tracks[1]["clips"][0]["fx"]["original"])

    def test_a_sidecar_makes_a_wav_a_processed_one(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = os.path.join(folder, "talk - Buddy 1.wav")
            render.write_wav(wav, np.zeros(10), RATE)
            self.assertIsNone(resolve_ext._fx_of({"path": wav}))             # no sidecar: anyone's WAV
            render.write_json(render.sidecar_path(wav), {"version": render.SIDECAR_VERSION,
                                                         "source": {"path": "D:/m/talk.wav"}, "file_start": 1.5,
                                                         "chain": [{"id": "gate", "on": True, "strength": 0.4}]})
            got = resolve_ext._fx_of({"path": wav})
            self.assertEqual((got["source"], got["file_start"]), ("D:/m/talk.wav", 1.5))
            self.assertIsNone(resolve_ext._fx_of({"path": "D:/m/talk.mp4"}))


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class SourceTests(unittest.TestCase):
    def test_a_plain_clip_is_made_from_its_own_file(self):
        c = clip("a", 100, 148, "D:/m/talk.wav", offset=3.0, channels=[1])
        self.assertEqual(fx.source(c, FPS), {"path": "D:/m/talk.wav", "start_s": 3.0, "end_s": 5.0,
                                             "channels": [1], "chain": []})

    def test_a_processed_clip_is_made_again_from_the_recording(self):
        chain = [{"id": "hum", "on": True, "strength": 0.6}]
        c = clip("p", 100, 148, "D:/m/Buddy Audio/x.wav", offset=1.0, fx_=side("D:/m/talk.wav", chain, 2.0, [0]))
        got = fx.source(c, FPS)
        self.assertEqual((got["path"], got["start_s"], got["end_s"], got["channels"], got["chain"]),
                         ("D:/m/talk.wav", 3.0, 5.0, [0], chain))

    def test_no_file_nothing_to_make_it_from(self):
        self.assertIsNone(fx.source(dict(clip("a", 0, 10, ""), media=None), FPS))

    def test_preview_plays_from_the_playhead_when_its_on_the_clip(self):
        c = clip("a", 100, 100 + 24 * 60, "D:/m/talk.wav", offset=10.0)
        src = fx.source(c, FPS)
        self.assertEqual(fx.preview_span(c, src, FPS, 100 + 24 * 5), (15.0, 15.0 + fx.PREVIEW_S, 100 + 24 * 5))
        self.assertEqual(fx.preview_span(c, src, FPS, 50)[2], 100)                  # before it: from its start
        self.assertEqual(fx.preview_span(c, src, FPS, c["end"] - 5)[2], 100)        # too near its end
        short = clip("s", 100, 148, "D:/m/talk.wav")
        self.assertEqual(fx.preview_span(short, fx.source(short, FPS), FPS, None), (0.0, 2.0, 100))


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class JobTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="buddy-fx-")
        patcher = mock.patch.object(fx, "PREVIEW_DIR", self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        t = np.arange(RATE * 3) / RATE
        self.audio = np.stack([0.2 * np.sin(2 * np.pi * 220 * t), np.random.default_rng(0).normal(0, 0.01, len(t))], 1)

    def decoded(self, path, start_s, end_s, progress=None, cancelled=lambda: False, channels=None):
        self.asked = (path, start_s, end_s, channels)
        x = self.audio[int(start_s * RATE):int(end_s * RATE)]
        return (x[:, channels] if channels else x, RATE), None

    def test_preview_writes_before_and_after_and_keeps_the_stretch(self):
        src = {"path": "D:/m/talk.wav", "start_s": 0.0, "end_s": 3.0, "channels": None, "chain": []}
        chain = [{"id": "rumble", "on": True, "strength": 0.6}, {"id": "denoise", "on": True, "strength": 0.5}]
        with mock.patch.object(fx, "decode_range", side_effect=self.decoded) as decode:
            result, error = fx.preview_work(src, (0.5, 2.5, 112), chain, None)(lambda f: None, lambda: False)
            self.assertIsNone(error)
            for key in ("before", "after"):
                with wave.open(result[key], "rb") as w:
                    self.assertEqual((w.getsampwidth(), w.getnchannels(), w.getnframes()), (2, 2, 2 * RATE))
            again, _ = fx.preview_work(src, (0.5, 2.5, 112), chain[:1], result["decoded"])(lambda f: None, lambda: False)
        self.assertEqual(decode.call_count, 1)                    # the same stretch: not decoded again
        self.assertFalse(os.path.exists(result["after"]))         # the last preview's files make way
        self.assertTrue(os.path.exists(again["after"]))

    def test_a_stopped_preview_says_so(self):
        src = {"path": "D:/m/talk.wav", "start_s": 0.0, "end_s": 3.0, "channels": None, "chain": []}
        with mock.patch.object(fx, "decode_range", side_effect=self.decoded):
            result, error = fx.preview_work(src, (0, 3, 0), [{"id": "rumble"}], None)(lambda f: None, lambda: True)
        self.assertEqual((result, error), (None, "Stopped."))

    def test_apply_renders_the_whole_clip_with_frame_handles(self):
        src = {"path": __file__, "start_s": 1.0, "end_s": 2.0, "channels": [0], "chain": []}
        with mock.patch.object(render, "output_folder", return_value=(self.folder, True)), \
                mock.patch.object(fx, "decode_range", side_effect=self.decoded):
            result, error = fx.render_work(src, [{"id": "rumble"}], FPS, "proj-1")(lambda f: None, lambda: False)
        self.assertIsNone(error)
        self.assertEqual(result["source"], __file__)
        self.assertEqual((self.asked[1], self.asked[3]), (0.0, [0]))        # a whole second of handle, from 0
        self.assertEqual(render.read_sidecar(result["path"])["projects"], ["proj-1"])


def talk(pattern, block=0.1, loud=1e-2, quiet=1e-9):
    """A file's loudness blocks from a pattern of (seconds, talking)."""
    out = []
    for seconds, on in pattern:
        out += [loud if on else quiet] * int(round(seconds / block))
    return {"loud": np.array(out), "block": block}


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class DuckingTests(unittest.TestCase):
    START = 86400

    def voice(self, uid="v", seconds=8, **extra):
        return dict({"id": uid, "start": self.START, "end": self.START + int(seconds * FPS), "offset": 0.0,
                     "enabled": True, "volume": 0.0, "keys": None, "curve": None}, **extra)

    def music(self, **extra):
        return dict({"id": "m", "start": self.START, "end": self.START + int(8 * FPS), "offset": 5.0,
                     "enabled": True, "volume": -6.0, "keys": None, "curve": None}, **extra)

    def test_speech_is_where_the_voice_is_loud(self):
        audio = talk([(2, True), (1, False), (2, True), (3, False)])
        spans, missing = ducking.speech([self.voice()], lambda c: audio, FPS)
        self.assertEqual(missing, [])
        seconds = [((a - self.START) / FPS, (b - self.START) / FPS) for a, b in spans]
        self.assertEqual(len(seconds), 2)
        self.assertAlmostEqual(seconds[0][0], 0.0, delta=0.1)
        self.assertAlmostEqual(seconds[0][1], 2.0, delta=0.15)
        self.assertAlmostEqual(seconds[1][0], 3.0, delta=0.15)

    def test_clips_turned_off_or_unread_are_not_heard(self):
        audio = talk([(2, True)])
        spans, missing = ducking.speech([self.voice(enabled=False), self.voice("w")], lambda c: None, FPS)
        self.assertEqual((spans, missing), ([], ["w"]))
        self.assertEqual(ducking.speech([self.voice(enabled=False)], lambda c: audio, FPS), ([], []))

    def test_a_quiet_voice_is_found_against_its_own_loudness(self):
        audio = talk([(2, True), (2, False), (2, True)], loud=1e-5, quiet=1e-11)
        spans, _ = ducking.speech([self.voice()], lambda c: audio, FPS)
        self.assertEqual(len(spans), 2)

    def test_the_music_dips_under_the_speech_and_comes_back(self):
        spans = [(self.START + 24, self.START + 72)]                      # 1 s to 3 s
        keys = ducking.duck_keys(self.music(), spans, FPS, depth=-12, fade=0.25, pause=0.5)
        level = lambda sec: float(np.interp(5.0 + sec, [k["t"] for k in keys], [k["db"] for k in keys]))
        self.assertEqual(level(0.0), -6.0)
        self.assertEqual(level(2.0), -18.0)                               # under the speech: down 12
        self.assertEqual(level(7.0), -6.0)
        self.assertEqual([k["db"] for k in keys][:2], [-6.0, -6.0])       # level until the fade starts
        self.assertLess(level(0.9), -6.0)                                 # already going down before it

    def test_short_pauses_stay_down(self):
        spans = [(self.START + 24, self.START + 48), (self.START + 60, self.START + 96)]   # a 0.5 s pause
        keys = ducking.duck_keys(self.music(), spans, FPS, depth=-10, fade=0.1, pause=0.8)
        self.assertTrue(all(k["db"] == -16.0 for k in keys if 5 + 1.0 <= k["t"] <= 5 + 4.0))
        apart = ducking.duck_keys(self.music(), spans, FPS, depth=-10, fade=0.1, pause=0.2)
        self.assertIn(-6.0, [k["db"] for k in apart if 5 + 2.0 < k["t"] < 5 + 2.5])

    def test_a_curve_keeps_its_shape_under_the_dips(self):
        own = [{"t": 5.0, "db": 0.0, "ease": False}, {"t": 13.0, "db": -8.0, "ease": False}]
        clip = self.music(keys=own, curve={"original": "x", "edited": False}, volume=2.0)
        keys = ducking.duck_keys(clip, [(self.START + 48, self.START + 96)], FPS, depth=-10, fade=0.2)
        at = lambda t: float(np.interp(t, [k["t"] for k in keys], [k["db"] for k in keys]))
        self.assertAlmostEqual(at(5.0), 2.0)                              # its line, with its volume on top
        self.assertAlmostEqual(at(8.0), 2.0 - 3.0 - 10.0, delta=0.05)     # its line at 8 s, ducked

    def test_no_speech_under_the_clip_no_keys(self):
        self.assertEqual(ducking.duck_keys(self.music(), [(self.START + 500, self.START + 600)], FPS), [])

    def test_settings_are_kept_in_range(self):
        self.assertEqual(ducking.settings({"depth": 5, "fade": "x", "pause": 99}),
                         {"depth": -1.0, "fade": ducking.DEFAULTS["fade"], "pause": 5.0})


def drums(bpm, seconds=20.0, first=0.37, rate=RATE):
    """A kick on every bar's first beat and a hit on every beat, over a chord
    and a little noise - and when the beats are."""
    rng = np.random.default_rng(int(bpm))
    x = rng.normal(0, 0.01, int(seconds * rate)) + 0.05 * np.sin(2 * np.pi * 220 * np.arange(int(seconds * rate)) / rate)
    length = int(0.15 * rate)
    env = np.exp(-np.arange(length) / rate * 30)
    at, i, truth = first, 0, []
    while at < seconds - 0.2:
        n = int(at * rate)
        if i % 4 == 0:
            x[n:n + length] += 0.8 * np.sin(2 * np.pi * 60 * np.arange(length) / rate) * env
        x[n:n + length // 3] += 0.3 * rng.normal(0, 1, length // 3) * env[:length // 3]
        truth.append(at)
        at, i = at + 60.0 / bpm, i + 1
    return x, np.array(truth)


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class BeatTests(unittest.TestCase):
    def test_the_beats_and_the_bars_are_found(self):
        for bpm in (90, 128, 140):
            x, truth = drums(bpm)
            found = beats.find(x, RATE)
            self.assertAlmostEqual(found["bpm"], bpm, delta=1.0)
            got = np.array(found["beats"])
            worst = max(np.min(np.abs(got - t)) for t in truth[2:-2])
            self.assertLess(worst, 0.5 / 24, bpm)                       # within half a frame at 24 fps
            downbeats = got[found["bars"]]
            self.assertLess(min(np.abs(downbeats - truth[4])), 0.03)     # bar 2 starts on the kick

    def test_no_beat_in_noise_a_tone_or_too_little(self):
        rng = np.random.default_rng(3)
        t = np.arange(RATE * 10) / RATE
        self.assertIsNone(beats.find(rng.normal(0, 0.1, RATE * 10), RATE))
        self.assertIsNone(beats.find(0.3 * np.sin(2 * np.pi * 440 * t), RATE))
        self.assertIsNone(beats.find(drums(120, seconds=2)[0], RATE))

    def test_twice_and_half_as_fast(self):
        found = {"bpm": 87.0, "beats": [0.0, 0.69, 1.38, 2.07, 2.76, 3.45], "bars": [1, 5]}
        double = beats.scaled(found, "double")
        self.assertEqual(double["bpm"], 174.0)
        self.assertEqual(len(double["beats"]), 11)
        self.assertAlmostEqual(double["beats"][1], 0.345)
        half = beats.scaled(found, "half")
        self.assertEqual((half["bpm"], half["beats"]), (43.5, [0.69, 2.07, 3.45]))     # the bars' beats kept
        self.assertIs(beats.scaled(found, "auto"), found)


class MarkerTimeline:
    def __init__(self, markers):
        self.markers = dict(markers)

    def GetMarkers(self):
        return dict(self.markers)

    def AddMarker(self, frame, color, name, note, duration, custom):
        if frame in self.markers or not name:
            return False
        self.markers[frame] = {"color": color, "name": name, "note": note, "duration": duration, "customData": custom}
        return True

    def DeleteMarkerAtFrame(self, frame):
        return self.markers.pop(frame, None) is not None


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class BeatMarkerTests(unittest.TestCase):
    def controller(self, timeline):
        project = mock.Mock(GetCurrentTimeline=lambda: timeline)
        return mock.Mock(current_project=lambda: project)

    def test_buddys_markers_come_off_and_nobody_elses(self):
        tl = MarkerTimeline({10: {"color": "Red", "name": "Fix", "note": "", "customData": ""}})
        c = self.controller(tl)
        added = resolve_ext.add_beat_markers(c, [{"frame": 10, "color": "Cyan", "name": "Bar 1"},
                                                 {"frame": 20, "color": "Cyan", "name": "1.2", "note": "120 BPM"},
                                                 {"frame": 30, "color": "Cyan", "name": "1.3"}])
        self.assertEqual(added, [20, 30])                           # 10 has the user's marker
        removed = resolve_ext.remove_beat_markers(c, [20])
        self.assertEqual(removed, [{"frame": 20, "color": "Cyan", "name": "1.2", "note": "120 BPM"}])
        self.assertEqual([r["frame"] for r in resolve_ext.remove_beat_markers(c)], [30])
        self.assertEqual(list(tl.markers), [10])                    # the user's stays
        self.assertEqual(resolve_ext.add_beat_markers(c, removed), [20])       # an undo puts them back


def phrased(levels, block=0.1):
    """Loudness blocks for phrases at those LUFS (None: a pause), (seconds, LUFS) each."""
    out = []
    for seconds, lufs in levels:
        out += [1e-9 if lufs is None else 10 ** ((lufs + 0.691) / 10)] * int(round(seconds / block))
    return {"loud": np.array(out), "block": block}


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class RidingTests(unittest.TestCase):
    def clip(self, **extra):
        return dict({"id": "v", "start": 86400, "end": 86400 + 8 * 24, "offset": 0.0, "enabled": True,
                     "volume": -2.0, "keys": None, "curve": None}, **extra)

    AUDIO = [(2, -14), (0.5, None), (2, -26), (0.5, None), (2, -20), (1, None)]

    def test_each_phrase_is_brought_to_the_clips_own_loudness(self):
        keys = riding.ride_keys(self.clip(), phrased(self.AUDIO), FPS, amount=1.0, most=15)
        at = lambda sec: float(np.interp(sec, [k["t"] for k in keys], [k["db"] for k in keys]))
        evened = [-14 + at(1.0) + 2, -26 + at(3.5) + 2, -20 + at(6.0) + 2]       # without the clip's volume
        self.assertLess(max(evened) - min(evened), 0.5)
        self.assertAlmostEqual(at(1.0) - at(6.0), -6.0, delta=0.3)

    def test_the_gain_changes_only_in_the_pauses(self):
        keys = riding.ride_keys(self.clip(), phrased(self.AUDIO), FPS, amount=1.0, most=15)
        at = lambda sec: float(np.interp(sec, [k["t"] for k in keys], [k["db"] for k in keys]))
        self.assertAlmostEqual(at(0.1), at(1.9), delta=0.01)          # steady through the first phrase
        self.assertAlmostEqual(at(2.6), at(4.4), delta=0.01)

    def test_amount_and_the_limit(self):
        half = riding.ride_keys(self.clip(), phrased(self.AUDIO), FPS, amount=0.5, most=15)
        capped = riding.ride_keys(self.clip(), phrased(self.AUDIO), FPS, amount=1.0, most=3)
        full = riding.ride_keys(self.clip(), phrased(self.AUDIO), FPS, amount=1.0, most=15)
        spread = lambda keys: max(k["db"] for k in keys) - min(k["db"] for k in keys)
        self.assertAlmostEqual(spread(half), spread(full) / 2, delta=0.2)
        self.assertLessEqual(max(abs(k["db"] + 2.0) for k in capped), 3.0 + 1e-6)

    def test_one_phrase_nothing_to_even(self):
        self.assertEqual(riding.ride_keys(self.clip(), phrased([(3, -20), (1, None)]), FPS), [])


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class MixPlanTests(unittest.TestCase):
    """Mix to a target's sums (page._mix_plan), on a stand-in page."""

    def plan(self, preset, measured, tracks):
        from types import SimpleNamespace
        from pages.audio_assistant import levels
        from pages.audio_assistant.page import AudioAssistantPage
        page = SimpleNamespace(timeline={"tracks": tracks}, MIX_CEILING=AudioAssistantPage.MIX_CEILING)
        page._mix_clips = lambda: AudioAssistantPage._mix_clips(page)
        return AudioAssistantPage._mix_plan(page, levels.PRESET_IDS[preset], measured)

    def tracks(self):
        c = lambda uid, volume=0.0, **extra: dict({"id": uid, "volume": volume, "enabled": True, "keys": None,
                                                   "curve": None}, **extra)
        return [{"index": 1, "enabled": True, "clips": [c("a"), c("b", 29.0), c("k", keys=[{"t": 0, "db": 0}])]},
                {"index": 2, "enabled": False, "clips": [c("off")]},
                {"index": 3, "enabled": True, "clips": [c("x", enabled=False), {"id": "t", "transition": True}]}]

    def test_every_clip_moves_by_what_takes_the_mix_to_the_target(self):
        plan = self.plan("youtube", {"lufs": -18.9, "peak_db": -8.0}, self.tracks())
        self.assertEqual((plan["gain"], plan["limited"]), (4.9, False))
        self.assertEqual((plan["clips"], plan["keyed"], plan["clamped"]), (2, 1, 1))   # b would pass +30

    def test_the_peaks_hold_it_under_the_ceiling(self):
        plan = self.plan("youtube", {"lufs": -18.9, "peak_db": -3.0}, self.tracks())
        self.assertEqual((plan["gain"], plan["wanted"], plan["limited"]), (2.0, 4.9, True))
        atsc = self.plan("atsc", {"lufs": -20.0, "peak_db": -1.0}, self.tracks())
        self.assertEqual((atsc["gain"], atsc["ceiling"]), (-4.0, -2.0))              # too loud: down to -24

    def test_a_silent_mix_says_so(self):
        self.assertIn("error", self.plan("ebu", {"lufs": None, "peak_db": None}, self.tracks()))


if __name__ == "__main__":
    unittest.main()
