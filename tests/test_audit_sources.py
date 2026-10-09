"""Audit's recording checks (pages/audit/sources.py) on made-up recordings,
fed through a stand-in for the decoder - and the rows checks.py makes."""

import unittest

import _paths  # noqa: F401

try:
    import numpy as np
    from pages.audit import checks, sources
    HAVE_DEPS = True
except ImportError:  # pragma: no cover - numpy or PySide6 missing
    HAVE_DEPS = False

RATE = 48000


def speech(seconds, seed=0, level=0.2):
    """Voice-like: band-limited noise in 0.4 s syllables with 0.25 s pauses."""
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    noise = rng.standard_normal(n)
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    spec[(freqs < 300) | (freqs > 3000)] = 0
    voice = np.fft.irfft(spec, n)
    voice *= level / np.sqrt(np.mean(voice ** 2))
    t = np.arange(n) / RATE
    on = (t % 0.65) < 0.4
    return (voice * on + rng.standard_normal(n) * 1e-4).astype(np.float32)


def listen(x, stereo_track=True):
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = x[:, None]
    lst = sources.Listener(0.0, len(x) / RATE, stereo_track)
    lst.rate = RATE
    for i in range(0, len(x), 4096):                  # buffers, as the decoder hands them over
        lst.add(x[i:i + 4096])
    return lst.result()


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class ListenerTests(unittest.TestCase):
    def test_clean_speech_has_nothing(self):
        got = listen(np.column_stack([speech(6), speech(6, 1)]))
        self.assertEqual((got["clipped"], got["dropouts"], got["pops"], got["wind"], got["side"], got["phase"]),
                         ([], [], [], [], None, None))

    def test_full_scale_runs_are_clipping(self):
        x = speech(6, level=0.2)
        x[int(2.0 * RATE):int(2.3 * RATE)] *= 20
        x = np.clip(x, -1.0, 1.0)
        got = listen(x)
        self.assertEqual(len(got["clipped"]), 1)
        self.assertAlmostEqual(got["clipped"][0][0], 2.0, delta=0.15)

    def test_clipped_then_turned_down_is_found_by_its_flat_tops(self):
        x = np.clip(speech(6, level=0.2) * 8, -1.0, 1.0) * 0.5          # flat at 0.5
        got = listen(x)
        self.assertTrue(got["clipped"])

    def test_digital_silence_in_the_sound_is_a_dropout(self):
        x = speech(6)
        x[int(1.5 * RATE):int(1.53 * RATE)] = 0.0                       # 30 ms, mid-syllable
        x[int(5.5 * RATE):] = 0.0                                       # the end: not a dropout
        got = listen(x)
        self.assertEqual(len(got["dropouts"]), 1)
        a, b = got["dropouts"][0]
        self.assertAlmostEqual(a, 1.5, delta=0.001)
        self.assertAlmostEqual(b - a, 0.03, delta=0.001)

    def test_a_dropout_across_two_buffers_is_one(self):
        x = speech(3)
        x[4000:4600] = 0.0                       # 12.5 ms over the first buffer's end (4096), mid-syllable
        got = listen(x)
        self.assertEqual(len(got["dropouts"]), 1)
        a, b = got["dropouts"][0]
        self.assertAlmostEqual((a * RATE, b * RATE), (4000, 4600), delta=1)

    def test_sound_on_one_side(self):
        v = speech(4)
        got = listen(np.column_stack([v, np.zeros_like(v)]))
        self.assertEqual(got["side"], "left")
        self.assertEqual(listen(np.column_stack([np.zeros_like(v), v]))["side"], "right")
        self.assertIsNone(listen(np.column_stack([v, np.zeros_like(v)]), stereo_track=False)["side"])

    def test_left_and_right_cancelling_is_out_of_phase(self):
        v = speech(4)
        got = listen(np.column_stack([v, -v * 0.9]))
        self.assertLess(got["phase"], sources.PHASE_LIMIT)
        self.assertIsNone(listen(np.column_stack([v, v * 0.9]))["phase"])

    def test_a_thump_in_speech_is_a_pop(self):
        x = speech(6)
        k = int(0.08 * RATE)
        at = int(3.0 * RATE)
        x[at:at + k] += (0.8 * np.sin(2 * np.pi * 50 * np.arange(k) / RATE) * np.hanning(k)).astype(np.float32)
        got = listen(x)
        self.assertEqual(len(got["pops"]), 1)
        self.assertAlmostEqual(got["pops"][0][0], 3.0, delta=0.1)

    def test_held_rumble_is_wind(self):
        x = speech(8)
        n = int(3 * RATE)
        rng = np.random.default_rng(5)
        rumble = np.convolve(rng.standard_normal(n), np.ones(600) / 600, mode="same")
        rumble *= 0.3 / np.sqrt(np.mean(rumble ** 2))
        x[int(2 * RATE):int(2 * RATE) + n] += rumble.astype(np.float32)
        got = listen(x)
        self.assertEqual(len(got["wind"]), 1)
        self.assertGreater(got["wind"][0][1] - got["wind"][0][0], 2.0)

    def test_music_with_bass_is_left_alone(self):
        t = np.arange(int(6 * RATE)) / RATE
        music = (0.3 * np.sin(2 * np.pi * 55 * t) * (1 + np.sign(np.sin(2 * np.pi * 2 * t))) / 2
                 + 0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        got = listen(music)
        self.assertEqual((got["pops"], got["wind"]), ([], []))


class FakeStream:
    """sources.listen()'s decoder: the file's samples, in buffers."""

    def __init__(self, files):
        self.files, self.opened = files, []

    def __call__(self, path, take, progress=None, cancelled=lambda: False, until_ms=None):
        self.opened.append(path)
        x = self.files.get(path)
        if x is None:
            return "The file is offline."
        for i in range(0, len(x), 8192):
            if take(x[i:i + 8192], RATE):
                break
        return None


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class ListenTests(unittest.TestCase):
    FPS = 24.0

    def scan(self, items, subtype="stereo", enabled=True):
        return {"fps": self.FPS, "start": 86400, "drop": False, "tracks": [
            {"kind": "audio", "index": 2, "enabled": enabled, "subtype": subtype, "items": items}]}

    def item(self, name, start, seconds, source_s=0.0, path="D:/talk.wav", **extra):
        return dict({"name": name, "start": start, "end": start + int(seconds * self.FPS), "enabled": True,
                     "path": path, "online": True, "source_s": source_s, "channels": None}, **extra)

    def test_each_clip_hears_only_its_own_stretch_of_one_decode(self):
        x = speech(10)
        x[int(7.5 * RATE):int(7.53 * RATE)] = 0.0                       # a dropout 7.5 s into the file
        stream = FakeStream({"D:/talk.wav": x[:, None]})
        scan = self.scan([self.item("first", 86400, 3), self.item("second", 86500, 3, source_s=6.0)])
        events, unread = sources.listen(scan, stream=stream)
        self.assertEqual((stream.opened, unread), (["D:/talk.wav"], []))
        found = {e["clip"]: e["found"] for e in events}
        self.assertEqual(found["first"]["dropouts"], [])
        self.assertEqual(len(found["second"]["dropouts"]), 1)
        self.assertAlmostEqual(found["second"]["dropouts"][0][0], 1.5, delta=0.001)     # into the clip
        others = {c["id"] for c in checks.CHECKS if not c.get("recording")} - checks.SOUND
        result = checks.run(scan, {}, others, sound={"mixed": False, "sources": events, "unread": []})
        group = next(g for g in result["groups"] if g["id"] == "dropouts")
        row = group["rows"][0]
        self.assertEqual((row["frame"], row["where"], row["clip"]), (86500 + 36, "A2", "second"))
        self.assertNotIn("clipping", result["passed"] + [g["id"] for g in result["groups"]])  # mix not measured

    def test_clips_not_heard_are_not_listened_to(self):
        stream = FakeStream({"D:/talk.wav": speech(4)[:, None]})
        events, _ = sources.listen(self.scan([self.item("off", 86400, 2, enabled=False)]), stream=stream)
        self.assertEqual((events, stream.opened), ([], []))
        events, _ = sources.listen(self.scan([self.item("a", 86400, 2)], enabled=False), stream=stream)
        self.assertEqual(events, [])

    def test_a_file_that_cant_be_read_is_said(self):
        events, unread = sources.listen(self.scan([self.item("gone", 86400, 2, path="D:/gone.wav")]),
                                        stream=FakeStream({}))
        self.assertEqual((events, unread), ([], [["D:/gone.wav", "The file is offline."]]))

    def test_a_channel_the_decoder_hasnt_got(self):
        stream = FakeStream({"D:/cam.mxf": speech(3)[:, None]})
        _events, unread = sources.listen(self.scan([self.item("cam", 86400, 2, path="D:/cam.mxf", channels=[1])]),
                                         stream=stream)
        self.assertIn("channel 2", unread[0][1])


if __name__ == "__main__":
    unittest.main()
