"""Audio Assistant's automixer (automix.py) and Fit to length (fit.py),
on made-up recordings and music."""

import os
import tempfile
import unittest

import _paths  # noqa: F401

try:
    import numpy as np
    from pages.audio_assistant import automix, beats, fit, fx, render, resolve_ext
    HAVE_DEPS = True
except ImportError:  # pragma: no cover - numpy or PySide6 missing
    HAVE_DEPS = False

FPS = 24.0
START = 86400


def mic(pattern, block=0.1):
    """A mic's loudness blocks from (seconds, energy) runs."""
    out = []
    for seconds, level in pattern:
        out += [level] * int(round(seconds / block))
    return {"loud": np.array(out, dtype=np.float64), "block": block}


def take(uid, seconds=12, **extra):
    return dict({"id": uid, "start": START, "end": START + int(seconds * FPS), "offset": 0.0, "enabled": True,
                 "volume": 0.0, "keys": None, "curve": None}, **extra)


TALK, QUIET = 1e-2, 1e-9
BLEED = TALK * 10 ** -1.5            # the other voice, 15 dB down on this mic


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class AutomixTests(unittest.TestCase):
    def plan(self, audios, **setting):
        mics = [{"index": i + 1, "clips": [take(uid)]} for i, uid in enumerate(audios)]
        return automix.plan(mics, lambda c: audios[c["id"]], FPS, **setting)

    def level(self, found, track, sec):
        frames, gains = found["lines"][track]
        return float(np.interp(START + sec * FPS, frames, gains))

    def conversation(self):
        # A talks 0-3 s, B 4-7 s, A again 8-10 s; each mic hears the other 15 dB down.
        return {"a": mic([(3, TALK), (1, QUIET), (3, BLEED), (1, QUIET), (2, TALK), (2, QUIET)]),
                "b": mic([(3, BLEED), (1, QUIET), (3, TALK), (1, QUIET), (2, BLEED), (2, QUIET)])}

    def test_only_the_mic_of_whoever_talks_is_up(self):
        found = self.plan(self.conversation(), depth=-15, hold=0.8)
        self.assertEqual(self.level(found, 1, 1.5), 0.0)
        self.assertEqual(self.level(found, 2, 1.5), -15.0)            # B's mic: only A's bleed
        self.assertEqual(self.level(found, 2, 5.5), 0.0)
        self.assertEqual(self.level(found, 1, 5.5), -15.0)
        self.assertEqual(self.level(found, 1, 9.0), 0.0)
        self.assertEqual(found["together"], 0.0)

    def test_a_mic_opens_before_its_person_starts(self):
        found = self.plan(self.conversation())
        self.assertEqual(self.level(found, 2, 3.95), 0.0)             # up by the time B starts at 4 s
        self.assertLess(self.level(found, 2, 3.5), -10)

    def test_in_a_pause_the_last_to_talk_stays_up(self):
        found = self.plan(self.conversation())
        self.assertEqual(self.level(found, 1, 3.5), 0.0)              # nobody at 3-4 s: A was last
        self.assertEqual(self.level(found, 2, 7.5), 0.0)              # nobody at 7-8 s: B was last

    def test_both_talking_both_up(self):
        audios = {"a": mic([(4, TALK), (2, QUIET)]), "b": mic([(1, BLEED), (3, TALK * 0.5), (2, QUIET)])}
        found = self.plan(audios)
        self.assertEqual((self.level(found, 1, 2.5), self.level(found, 2, 2.5)), (0.0, 0.0))
        self.assertGreater(found["together"], 0.2)

    def test_a_quiet_talker_counts_against_their_own_loudness(self):
        quiet = 1e-4                                                  # 20 dB under A
        audios = {"a": mic([(3, TALK), (3, quiet * 10 ** -1.5), (2, QUIET)]),
                  "b": mic([(3, quiet * 10 ** -1.5 * 0.01), (3, quiet), (2, QUIET)])}
        found = self.plan(audios)
        self.assertEqual(self.level(found, 2, 4.5), 0.0)
        self.assertEqual(self.level(found, 1, 4.5), automix.DEFAULTS["depth"])

    def test_keys_sit_on_the_clips_own_volume(self):
        audios = self.conversation()
        found = self.plan(audios, depth=-12)
        keys = automix.keys(take("b", volume=-3.0), found["lines"][2], FPS)
        at = lambda sec: float(np.interp(sec, [k["t"] for k in keys], [k["db"] for k in keys]))
        self.assertAlmostEqual(at(1.5), -15.0, delta=0.01)
        self.assertAlmostEqual(at(5.5), -3.0, delta=0.01)

    def test_a_mic_up_all_through_gets_no_keys(self):
        mics = [{"index": 1, "clips": [take("a", 6)]}, {"index": 2, "clips": [take("b", 6)]}]
        audios = {"a": mic([(6, TALK)]), "b": mic([(6, BLEED)])}
        found = automix.plan(mics, lambda c: audios[c["id"]], FPS)
        self.assertEqual(automix.keys(take("a", 6), found["lines"][1], FPS), [])

    def test_unread_audio_and_silence(self):
        mics = [{"index": 1, "clips": [take("a")]}, {"index": 2, "clips": [take("b")]}]
        found = automix.plan(mics, lambda c: None, FPS)
        self.assertEqual((found["lines"], sorted(found["missing"])), ({}, ["a", "b"]))
        silent = self.plan({"a": mic([(6, QUIET)]), "b": mic([(6, QUIET)])})
        self.assertEqual(silent["lines"], {})

    def test_settings_are_kept_in_range(self):
        self.assertEqual(automix.settings({"depth": 0, "hold": "x"}), {"depth": -3.0, "hold": automix.DEFAULTS["hold"]})


RATE = 22050


def song(sections, bpm=120.0, rate=RATE, seed=3):
    """Made-up music: (bars, chord root Hz) sections, a kick on beats 1 and 3,
    a hat on each - and a second of silence after."""
    beat = 60 / bpm
    total = sum(b for b, _ in sections) * 4 * beat
    n = int(total * rate) + rate
    t = np.arange(n) / rate
    x = np.zeros(n)
    at = 0.0
    for bars, root in sections:
        a, b = int(at * rate), int((at + bars * 4 * beat) * rate)
        for ratio in (1, 1.26, 1.5):
            x[a:b] += 0.08 * np.sin(2 * np.pi * root * ratio * t[a:b])
        at += bars * 4 * beat
    k = int(0.12 * rate)
    kick = np.sin(2 * np.pi * 55 * np.arange(k) / rate) * np.exp(-np.arange(k) / (0.03 * rate))
    hat = np.random.default_rng(seed).standard_normal(int(0.03 * rate)) * np.exp(-np.arange(int(0.03 * rate)) / (0.005 * rate))
    i = 0
    while i * beat < at:
        s = int(i * beat * rate)
        if i % 2 == 0:
            x[s:s + k] += (0.9 if i % 4 == 0 else 0.6) * kick[:len(x[s:s + k])]
        x[s:s + len(hat)] += 0.15 * hat[:len(x[s:s + len(hat)])]
        i += 1
    return x.astype(np.float32), at


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class FitTests(unittest.TestCase):
    SECTIONS = [(4, 110), (8, 147), (8, 196), (8, 147), (8, 196), (4, 165)]      # intro, verse, chorus x2, outro

    @classmethod
    def setUpClass(cls):
        cls.x, cls.length = song(cls.SECTIONS)
        cls.found = beats.find(cls.x, RATE)

    def ending_lands(self, plan, out):
        """The song's last half second is the fitted song's, end_s before its end."""
        gap = int(round(plan["end_s"] * RATE))
        end = int(round(plan["song_s"] * RATE))
        trimmed = max(0, -gap)
        got = out[len(out) - max(gap, 0) - RATE // 2:len(out) - max(gap, 0), 0]
        return np.allclose(got, self.x[end - trimmed - RATE // 2:end - trimmed], atol=1e-5)

    def test_the_song_ends_where_its_sound_does(self):
        self.assertAlmostEqual(fit.song_end(self.x, RATE), self.length, delta=0.1)

    def test_shorter_cuts_whole_bars_between_alike_places(self):
        plans = fit.plans(self.x, RATE, 50.0, self.found)
        best = plans[0]
        self.assertEqual(best["sound"], "seamless")
        self.assertGreater(best["b"], best["a"])
        self.assertAlmostEqual(best["removed_s"], 30.0, delta=0.1)              # 15 bars at 120 BPM
        # Both sides of the cut are in the same kind of section.
        section = lambda s: [root for (bars, root), start in zip(self.SECTIONS, np.cumsum([0] + [b * 2 for b, _ in self.SECTIONS]))
                             if start <= s < start + bars * 2][0]
        self.assertEqual(section(best["a"] - 1), section(best["b"] - 1))
        out = fit.build(self.x, RATE, best)
        self.assertEqual(len(out), int(round(50.0 * RATE)))
        self.assertTrue(self.ending_lands(best, out))

    def test_longer_plays_bars_again(self):
        plans = fit.plans(self.x, RATE, 140.0, self.found)
        best = plans[0]
        self.assertLess(best["b"], best["a"])
        self.assertLess(best["removed_s"], -55)
        out = fit.build(self.x, RATE, best)
        self.assertEqual(len(out), int(round(140.0 * RATE)))
        self.assertTrue(self.ending_lands(best, out))

    def test_what_bars_cant_make_up_goes_round_the_song(self):
        plan = fit.plans(self.x, RATE, 61.3, self.found)[0]
        self.assertAlmostEqual(plan["end_s"], fit.END_ROOM_S)                   # silence after it first
        self.assertGreater(plan["start_s"], 0)                                  # then a late start
        out = fit.build(self.x, RATE, plan)
        self.assertEqual(float(np.abs(out[:int(plan["start_s"] * RATE) - 10]).max()), 0.0)
        self.assertEqual(float(np.abs(out[-int(plan["end_s"] * RATE) + 10:]).max()), 0.0)
        self.assertTrue(self.ending_lands(plan, out))

    def test_near_enough_needs_no_cut(self):
        plan = fit.plans(self.x, RATE, self.length + 0.3, self.found)[0]
        self.assertIsNone(plan["a"])
        self.assertEqual(fit.segments(plan), [(0.0, plan["song_s"])])

    def test_no_beat_no_plans(self):
        self.assertEqual(fit.plans(self.x, RATE, 30.0, None), [])
        self.assertEqual(fit.plans(self.x[:RATE * 4], RATE, 2.0, beats.find(self.x[:RATE * 4], RATE)), [])

    def test_the_jump_is_lined_up_on_the_waveform(self):
        plan = dict(fit.plans(self.x, RATE, 50.0, self.found)[0])
        nudged = dict(plan, b=plan["b"] + 0.006)                                # a beat found 6 ms late
        (_s, _a), (b, _e) = fit.segments(nudged, self.x, RATE)
        self.assertAlmostEqual(b, plan["b"], delta=0.0035)

    def test_saved_with_a_sidecar_that_says_its_a_fit(self):
        plan = fit.plans(self.x, RATE, 50.0, self.found)[0]
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "song.wav")
            render.write_wav(source, self.x, RATE)
            got, error = fit.save(source, 0.0, None, plan, self.x, RATE, project="p1")
            self.assertIsNone(error)
            self.assertTrue(got["path"].startswith(os.path.join(folder, render.FOLDER_NAME)))
            self.assertEqual(got["frames"], int(round(50.0 * RATE)))
            fx_ = resolve_ext._fx_of({"path": got["path"]})
            self.assertEqual((fx_["source"], fx_["fit"]["length_s"]), (os.path.abspath(source), 50.0))
            again, _ = fit.save(source, 0.0, None, plan, self.x, RATE, project="p2")
            self.assertTrue(again["reused"])
            self.assertEqual(render.read_sidecar(got["path"])["projects"], ["p1", "p2"])


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class FitLinkTests(unittest.TestCase):
    def clip(self, uid, start, end, path, enabled=True, fx_=None):
        return {"id": uid, "name": os.path.basename(path), "start": start, "end": end, "enabled": enabled,
                "offset": 0.0, "media": {"path": path, "key": uid, "fx": fx_}, "channels": None, "curve": None}

    def test_a_fitted_clip_finds_its_song_and_isnt_an_effects_clip(self):
        plan = {"a": 30.0, "b": 60.0, "times": 1, "length_s": 50.0, "song_s": 80.0, "bars": 15}
        side = {"chain": [], "source": "D:/m/song.wav", "file_start": 0.0, "channels_used": None, "fit": plan}
        tracks = [{"index": 1, "clips": [self.clip("song", 100, 100 + 80 * 24, "D:/m/song.wav", enabled=False)]},
                  {"index": 2, "clips": [self.clip("fitted", 100, 100 + 50 * 24, "D:/m/Buddy Audio/song - Buddy 1.wav",
                                                   fx_=side)]}]
        resolve_ext.link_effects(tracks)
        fitted = tracks[1]["clips"][0]
        self.assertIsNone(fitted["fx"])
        self.assertEqual((fitted["fit"]["original"], fitted["fit"]["length_s"], fitted["fit"]["longer"]),
                         ("song", 50.0, False))
        # Effects on a fitted clip are made from its own WAV, not the song.
        self.assertEqual(fx.source(fitted, FPS)["path"], "D:/m/Buddy Audio/song - Buddy 1.wav")


if __name__ == "__main__":
    unittest.main()
