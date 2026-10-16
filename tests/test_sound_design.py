"""Audio Assistant's Sound Design: a song's key and tempo and the moments in
it (song.py), on made-up music - and placing a made sound on the timeline
(resolve_ext.place_sound / remove_sound) on a fake one. Never Resolve."""

import base64
import os
import tempfile
import unittest

import _paths  # noqa: F401

try:
    import numpy as np
    from pages.audio_assistant import render, resolve_ext, song, sounds
    HAVE_DEPS = True
except ImportError:  # pragma: no cover - numpy or PySide6 missing
    HAVE_DEPS = False

RATE = 22050
BPM = 120.0
BEAT = 60.0 / BPM
BAR = BEAT * 4


def _tone(freq, seconds, level):
    t = np.arange(int(seconds * RATE)) / RATE
    wave = sum(np.sin(2 * np.pi * freq * h * t) / h for h in (1, 2, 3))
    return level * wave * np.minimum(1.0, t / 0.01) * np.exp(-t * 1.5)


def _kick(level):
    t = np.arange(int(0.25 * RATE)) / RATE
    return level * np.sin(2 * np.pi * (50 + 80 * np.exp(-t * 30)) * t) * np.exp(-t * 14)


def made_up_song(chords, loud_bars, quiet_bars, end_s, stop_on_hit=True):
    """A 4/4 song at BPM, one chord a bar: `chords` [[MIDI notes]] repeated.
    Bars in loud_bars get the band (bass, louder chords, a kick on every
    beat), the rest a soft pad and a soft kick; quiet_bars almost nothing."""
    n = int(end_s * RATE)
    out = np.zeros(n)
    bars = int(round(end_s / BAR))
    for b in range(bars):
        at = int(b * BAR * RATE)
        notes = chords[b % len(chords)]
        loud = b in loud_bars
        level = 0.02 if b in quiet_bars else 0.18 if loud else 0.05
        for note in notes:
            f = 440.0 * 2 ** ((note - 69) / 12)
            part = _tone(f, BAR, level)
            out[at:at + len(part)] += part[:max(0, n - at)]
        if loud:
            f = 440.0 * 2 ** ((notes[0] - 12 - 69) / 12)
            part = _tone(f, BAR, 0.25)
            out[at:at + len(part)] += part[:max(0, n - at)]
        for k in range(4):
            hit = _kick((0.9 if k == 0 else 0.6) * (1.0 if loud else 0.35 if b not in quiet_bars else 0.05))
            s = at + int(k * BEAT * RATE)
            out[s:s + len(hit)] += hit[:max(0, n - s)]
    if not stop_on_hit:
        out *= np.minimum(1.0, (end_s - np.arange(n) / RATE) / (4 * BAR))       # fades out over its last bars
    return np.clip(out, -1, 1)


# A minor: Am, Dm, E, Am - the E major chord's G sharp says minor, not C major.
A_MINOR = [[57, 60, 64], [57, 62, 65], [56, 59, 64], [57, 60, 64]]
# E flat major: E♭, A♭, B♭, E♭.
E_FLAT = [[63, 67, 70], [56, 60, 63], [58, 62, 65], [63, 67, 70]]


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class KeyTests(unittest.TestCase):
    def test_a_minor_is_heard_as_a_minor(self):
        key = song.key_of(song.chroma(made_up_song(A_MINOR, range(0, 12), (), 24.0), RATE))
        self.assertEqual((key["tonic"], key["mode"], key["name"]), (9, "minor", "A minor"))
        self.assertEqual((key["camelot"], key["relative"]), ("8A", "C major"))

    def test_a_flat_key_is_spelled_with_flats(self):
        key = song.key_of(song.chroma(made_up_song(E_FLAT, range(0, 12), (), 24.0), RATE))
        self.assertEqual((key["name"], key["camelot"], key["relative"]), ("E♭ major", "5B", "C minor"))

    def test_camelot_codes_go_round_the_wheel(self):
        self.assertEqual(song.camelot(0, "major"), "8B")
        self.assertEqual(song.camelot(7, "major"), "9B")          # a fifth up: one step
        self.assertEqual(song.camelot(5, "major"), "7B")
        self.assertEqual(song.camelot(6, "minor"), "11A")         # F♯ minor
        self.assertEqual(song.camelot(4, "major"), "12B")

    def test_silence_has_no_key(self):
        self.assertIsNone(song.key_of(song.chroma(np.zeros(RATE * 3), RATE)))


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class MomentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Bars 0-7 an intro, 8-15 the band (a drop at 16 s), 16-17 almost
        # nothing (a break at 32 s), 18-21 the band again, then it stops.
        cls.got = song.analyse(made_up_song(A_MINOR, set(range(8, 16)) | set(range(18, 22)), {16, 17}, 44.0), RATE)

    def test_the_tempo_and_the_bars(self):
        self.assertAlmostEqual(self.got["bpm"], BPM, delta=1.0)
        self.assertAlmostEqual(self.got["bar_s"], BAR, delta=0.05)
        self.assertEqual(len(self.got["bars"]), len(self.got["levels"]))
        self.assertEqual(self.got["key"]["name"], "A minor")

    def test_the_drop_the_break_and_the_end_are_found(self):
        kinds = {}
        for m in self.got["moments"]:
            kinds.setdefault(m["kind"], m["t"])                          # the first of each
        self.assertAlmostEqual(kinds["drop"], 16.0, delta=0.15)
        self.assertAlmostEqual(kinds["break"], 32.0, delta=0.15)
        self.assertIn("end", kinds)
        times = [m["t"] for m in self.got["moments"]]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(0 < m["strength"] <= 1 for m in self.got["moments"]))

    def test_moments_are_bars_apart(self):
        bars = sorted(m["bar"] for m in self.got["moments"])
        self.assertTrue(all(b - a >= song.MIN_APART_BARS for a, b in zip(bars, bars[1:])))

    def test_a_song_that_fades_out_has_no_last_hit(self):
        got = song.analyse(made_up_song(A_MINOR, range(0, 12), (), 24.0, stop_on_hit=False), RATE)
        self.assertNotIn("end", [m["kind"] for m in got["moments"]])

    def test_no_beat_still_has_a_key(self):
        t = np.arange(RATE * 6) / RATE
        pad = sum(np.sin(2 * np.pi * 440.0 * 2 ** ((n - 69) / 12) * t) for n in (57, 60, 64)) * 0.1
        got = song.analyse(pad, RATE)
        self.assertIsNone(got["bpm"])
        self.assertEqual(got["moments"], [])
        self.assertIn(got["key"]["tonic"], (9, 0))               # A minor, or its relative


# ------------------------------------------------------------- on disk --


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class SaveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.music = os.path.join(self.root, "Music", "song.wav")
        os.makedirs(os.path.dirname(self.music))
        self.fallback = os.path.join(self.root, "fallback")

    def channels(self, *chans):
        return [base64.b64encode(np.asarray(c, dtype="<f4").tobytes()).decode() for c in chans]

    def test_the_pages_channels_come_back_as_samples(self):
        got = sounds.decode(self.channels([0.0, 0.5, -0.5], [0.1, 0.2, 0.3]), 48000)
        self.assertEqual(got.shape, (3, 2))
        self.assertAlmostEqual(got[1, 0], 0.5)

    def test_anything_that_isnt_a_sound_is_refused(self):
        for bad, rate in ((self.channels([0.1], [0.1, 0.2]), 48000), (self.channels([0.1]), 12345),
                          (["not base64!"], 48000), ([], 48000), (self.channels([float("nan")]), 48000),
                          (self.channels([0.0] * 10, [0.0] * 10, [0.0] * 10), 48000)):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                sounds.decode(bad, rate)

    def test_saved_beside_the_music_with_a_sidecar_tidy_up_knows(self):
        x = np.zeros((4800, 2))
        x[100] = 0.5
        got = sounds.save(x, 48000, {"kind": "riser", "bars": 2}, "Riser A♯ minor 124 BPM", self.music, "p1",
                          fallback=self.fallback)
        self.assertTrue(got["beside"] and not got["reused"])
        self.assertEqual(os.path.dirname(got["path"]), os.path.join(os.path.dirname(self.music), render.FOLDER_NAME))
        self.assertTrue(os.path.basename(got["path"]).startswith("Riser A# minor 124 BPM - Buddy "))
        self.assertAlmostEqual(got["seconds"], 0.1)
        side = render.read_sidecar(got["path"])
        self.assertEqual((side["sound"]["kind"], side["projects"]), ("riser", ["p1"]))
        self.assertIsNone(resolve_ext._fx_of({"path": got["path"]}))             # not processed audio
        self.assertIn(got["path"], render.buddy_wavs_in(os.path.dirname(got["path"])))
        again = sounds.save(x, 48000, {"kind": "riser"}, "Riser A♯ minor 124 BPM", self.music, "p2",
                            fallback=self.fallback)
        self.assertEqual((again["path"], again["reused"]), (got["path"], True))
        self.assertEqual(render.read_sidecar(got["path"])["projects"], ["p1", "p2"])

    def test_no_music_or_a_folder_it_cant_write_goes_to_buddys_own(self):
        x = np.zeros((480, 1))
        got = sounds.save(x, 48000, {}, 'Whoosh: "fast"', None, "p1", fallback=self.fallback)
        self.assertEqual(os.path.dirname(got["path"]), self.fallback)
        self.assertTrue(os.path.basename(got["path"]).startswith("Whoosh fast - Buddy "))
        got = sounds.save(x + 0.1, 48000, {}, "Impact", self.music, "p1", fallback=self.fallback,
                          writable=lambda _f: False)
        self.assertEqual((os.path.dirname(got["path"]), got["beside"]), (self.fallback, False))


# ---------------------------------------------------------- on the timeline --


class _Item:
    def __init__(self, uid, start, end, track):
        self.uid, self.start, self.end, self.track = uid, start, end, track

    def GetUniqueId(self): return self.uid
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetTrackTypeAndIndex(self): return ["audio", self.track.index]


class _Track:
    def __init__(self, name, items=()):
        self.name, self.items, self.index = name, list(items), 0


class _Timeline:
    def __init__(self, tracks, start=86400):
        self.tracks, self.start = tracks, start
        self.renumber()

    def renumber(self):
        for i, t in enumerate(self.tracks, 1):
            t.index = i

    def GetSetting(self, key): return "24"
    def GetStartFrame(self): return self.start
    def GetTrackCount(self, kind): return len(self.tracks)
    def GetItemListInTrack(self, kind, i): return self.tracks[i - 1].items
    def GetTrackName(self, kind, i): return self.tracks[i - 1].name or f"Audio {i}"
    def SetTrackName(self, kind, i, name): self.tracks[i - 1].name = name; return True
    def GetTrackSubType(self, kind, i): return "stereo"
    def GetIsTrackEnabled(self, kind, i): return True
    def GetIsTrackLocked(self, kind, i): return False

    def AddTrack(self, kind, options=None):
        self.tracks.insert(options["index"] - 1, _Track(None))
        self.renumber()
        return True

    def DeleteTrack(self, kind, i):
        del self.tracks[i - 1]
        self.renumber()
        return True

    def DeleteClips(self, items, ripple=False):
        for t in self.tracks:
            t.items = [i for i in t.items if i not in items]
        return True


class _Wav:
    def __init__(self, path): self.path = path
    def GetClipProperty(self, key=None, *_a): return {"File Path": self.path, "FPS": "24"}.get(key) if key else {}
    def GetUniqueId(self): return "mpi-" + os.path.basename(self.path)


class _Folder:
    def __init__(self, name): self.name, self.clips, self.subs = name, [], []
    def GetName(self): return self.name
    def GetClipList(self): return self.clips
    def GetSubFolderList(self): return self.subs


class _Pool:
    def __init__(self, timeline, shift=0):
        self.timeline, self.root, self.current, self.shift = timeline, _Folder("Master"), None, shift
        self.appended = []

    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.current or self.root
    def SetCurrentFolder(self, folder): self.current = folder; return True

    def AddSubFolder(self, parent, name):
        made = _Folder(name)
        parent.subs.append(made)
        return made

    def ImportMedia(self, paths):
        got = [_Wav(p) for p in paths]
        self.GetCurrentFolder().clips += got
        return got

    def AppendToTimeline(self, infos):
        out = []
        for info in infos:
            self.appended.append(info)
            track = self.timeline.tracks[info["trackIndex"] - 1]
            start = info["recordFrame"] + self.shift
            item = _Item(f"s{len(self.appended)}", start, start + info["endFrame"] - info["startFrame"], track)
            track.items.append(item)
            out.append(item)
        return out


class _Project:
    def __init__(self, timeline, pool): self.timeline, self.pool = timeline, pool
    def GetMediaPool(self): return self.pool
    def GetCurrentTimeline(self): return self.timeline


class _Controller:
    def __init__(self, project): self.project = project
    def current_project(self): return self.project


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class PlaceTests(unittest.TestCase):
    def setUp(self):
        self.music = _Track("Music", [])
        self.music.items = [_Item("m", 86400, 90000, self.music)]
        self.timeline = _Timeline([_Track("Dialogue"), self.music, _Track("SFX")])
        self.pool = _Pool(self.timeline)
        self.controller = _Controller(_Project(self.timeline, self.pool))

    def test_a_sound_goes_on_a_buddy_track_under_the_music(self):
        got = resolve_ext.place_sound(self.controller, r"D:\Music\Buddy Audio\Riser - Buddy 1.wav", 86500, 2.0, 2)
        self.assertEqual(got["track"], 3)
        self.assertEqual([self.timeline.GetTrackName("audio", i) for i in range(1, 5)],
                         ["Dialogue", "Music", "Buddy", "SFX"])
        placed = self.timeline.tracks[2].items[0]
        self.assertEqual((placed.GetStart(), placed.GetEnd()), (86500, 86548))
        self.assertEqual(self.pool.root.subs[0].GetName(), resolve_ext.K.BIN_NAME)      # imported into Buddy's bin
        # Undo: the sound and its empty track go.
        resolve_ext.remove_sound(self.controller, got["id"])
        self.assertEqual(len(self.timeline.tracks), 3)

    def test_a_sound_before_the_timelines_start_loses_its_head(self):
        resolve_ext.place_sound(self.controller, r"D:\x - Buddy 2.wav", 86400 - 12, 1.0, 2)
        info = self.pool.appended[-1]
        self.assertEqual((info["startFrame"], info["endFrame"], info["recordFrame"]), (12, 24, 86400))

    def test_a_sound_resolve_put_elsewhere_is_taken_back(self):
        self.pool.shift = 3
        with self.assertRaises(resolve_ext.ResolveConnectionError):
            resolve_ext.place_sound(self.controller, r"D:\x - Buddy 3.wav", 86500, 1.0, 2)
        self.assertEqual(len(self.timeline.tracks), 3)                          # the track it added went too


# ---------------------------------------------------------------- the page --


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class PageTests(unittest.TestCase):
    """page.py's sd_listen and sd_place, their work run straight away: a
    made-up song heard, a sound saved beside it, placed and undone."""

    def setUp(self):
        import types
        from unittest import mock
        from pages.audio_assistant import page as page_mod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.music_path = os.path.join(tmp.name, "Music", "song.wav")
        os.makedirs(os.path.dirname(self.music_path))
        self.decoded = []
        song_samples = made_up_song(A_MINOR, set(range(8, 16)) | set(range(18, 22)), {16, 17}, 44.0)

        def decode(path, start_s, end_s, progress, cancelled, channels=None):
            self.decoded.append((path, start_s, end_s))
            return (song_samples, RATE), None

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

        Page = page_mod.AudioAssistantPage
        p = types.SimpleNamespace(
            timeline={"id": "t1", "project": "p1", "fps": 24.0, "start": 86400,
                      "tracks": [{"index": 1, "clips": []}, {"index": 2, "clips": [clip]}]},
            _songs={}, _sd_placing=False, _full_due=1.0, undo_stack=[], problem="", _worker=worker,
            FIT_MOST_S=600, SD_UNDO=Page.SD_UNDO, SD_ADDED=Page.SD_ADDED, settings=Settings(),
            emit=lambda name, data: self.sent.append((name, data)), _writable=lambda: controller,
            _start_fx=lambda kind, uid, work, done: (done(*work(lambda f: None, lambda: False)), True)[1],
            _push_undo=lambda: None)
        for name in ("on_sd_listen", "on_sd_place", "_undo_sound", "_track_of", "_clips", "on_sd_saved_put",
                     "on_sd_saved_remove", "on_sd_last", "_library", "_keep_library", "_push_library"):
            setattr(p, name, types.MethodType(getattr(Page, name), p))
        self.page = p

    def last(self, name):
        return [d for n, d in self.sent if n == name][-1]

    def test_listen_place_and_undo(self):
        self.page.on_sd_listen({"id": "m"})
        heard = self.last("sd_song")
        self.assertEqual((heard["id"], heard["key"]["name"], heard["start"]), ("m", "A minor", 86424))
        drop = next(m for m in heard["moments"] if m["kind"] == "drop")
        self.assertAlmostEqual(drop["frame"], 86424 + 16 * 24, delta=4)          # 16 s into the clip
        self.page.on_sd_listen({"id": "m"})                                       # again: what was heard is kept
        self.assertEqual(len(self.decoded), 1)
        # A one-second sound whose moment is half a second in, onto the drop.
        sound = np.zeros(48000, dtype="<f4")
        sound[24000] = 0.5
        chans = [base64.b64encode(sound.tobytes()).decode()] * 2
        self.page.on_sd_place({"kind": "impact", "label": "Impact A minor", "frame": drop["frame"], "sync": 0.5,
                               "rate": 48000, "channels": chans, "song": "m", "recipe": {"kind": "impact"}})
        self.assertEqual(self.last("sd_placed")["ok"], True)
        self.assertEqual(self.last("toast")["text"], "Impact added on A3")
        placed = self.tl.tracks[2].items[0]
        self.assertEqual(placed.GetStart(), drop["frame"] - 12)
        saved = os.listdir(os.path.join(os.path.dirname(self.music_path), render.FOLDER_NAME))
        self.assertEqual(sum(n.endswith(".wav") for n in saved), 1)
        entry = self.page.undo_stack[-1]
        self.assertEqual((entry["label"], entry["sound"]["id"]), ("Add an impact", placed.GetUniqueId()))
        self.page._undo_sound(self.page.undo_stack.pop(), self.page._writable())
        self.assertEqual(len(self.tl.tracks), 2)
        self.assertEqual(self.last("toast")["text"], "Undone: Add an impact")

    def test_a_sound_that_isnt_one_is_said_and_nothing_placed(self):
        self.page.on_sd_place({"kind": "impact", "frame": 86500, "sync": 0, "rate": 48000, "channels": ["!!"]})
        self.assertEqual(self.last("sd_placed")["ok"], False)
        self.assertEqual(self.last("alert")["title"], "Couldn't use that sound")
        self.page.on_sd_place({"kind": "laser", "frame": 86500})               # not a kind Buddy makes: ignored
        self.assertEqual(len(self.tl.tracks), 2)

    def chans(self, at=24000, n=48000):
        sound = np.zeros(n, dtype="<f4")
        sound[at] = 0.5
        return [base64.b64encode(sound.tobytes()).decode()] * 2

    def test_saved_sounds_are_kept_cleaned_and_go_when_deleted(self):
        self.page.on_sd_saved_put({"id": "s1", "name": "  Big   build ", "kind": "riser", "values": {"climb": 20, "x y": 1}, "seed": 70000})
        self.page.on_sd_saved_put({"id": "s2", "name": "Thud", "kind": "impact", "values": {"sub": 40}})
        self.page.on_sd_saved_put({"id": "bad id!", "name": "Nope", "kind": "riser"})              # not kept
        self.page.on_sd_saved_put({"id": "s3", "name": "Laser", "kind": "laser"})                 # nor this
        saved = self.last("sd_library")["saved"]
        self.assertEqual([(s["id"], s["name"], s["values"], s["seed"]) for s in saved],
                         [("s1", "Big build", {"climb": 20}, 9999), ("s2", "Thud", {"sub": 40}, 1)])
        self.page.on_sd_saved_put({"id": "s1", "name": "Bigger build", "kind": "riser", "values": {"climb": 24}, "seed": 2})
        saved = self.last("sd_library")["saved"]
        self.assertEqual([(s["id"], s["name"]) for s in saved], [("s1", "Bigger build"), ("s2", "Thud")])   # in its place
        self.page.on_sd_saved_remove({"id": "s1"})
        self.assertEqual([s["id"] for s in self.last("sd_library")["saved"]], ["s2"])
        self.page.on_sd_last({"kind": "impact", "values": {"impact": {"sub": 10}, "laser": {}}, "seed": {"impact": 4}})
        self.assertEqual(self.page._library()["last"], {"kind": "impact", "values": {"impact": {"sub": 10}}, "seed": {"impact": 4}})
        self.assertGreaterEqual(self.page.settings.saves, 4)



class LibraryTests(unittest.TestCase):
    """What the page sends for the library, cleaned before it's kept."""

    def test_settings_are_kept_to_what_a_sound_can_have(self):
        self.assertEqual(sounds.clean_values({"air": 70.123456, "wave": "sawtooth", "air_on": False, "n": float("inf"),
                                              "x": [1], "long": "y" * 50, "Upper": 1}),
                         {"air": 70.1235, "wave": "sawtooth", "air_on": False})
        many = [{"id": f"s{i}", "name": f"S{i}", "kind": "riser"} for i in range(sounds.MOST_SAVED + 5)]
        self.assertEqual(len(sounds.clean_saved(many)), sounds.MOST_SAVED)
        self.assertEqual(sounds.clean_saved([{"id": "a", "name": "A", "kind": "riser"}, {"id": "a", "name": "B", "kind": "riser"}]),
                         [{"id": "a", "name": "A", "kind": "riser", "values": {}, "seed": 1, "made": ""}])


if __name__ == "__main__":
    unittest.main()
