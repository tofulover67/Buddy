"""The DAW (pages/daw): its project as it's kept (project.py), the song lane's
waveform and clips, and the page's listen / place / undo / keep - on made-up
music and a fake timeline (test_sound_design's). Never Resolve. The page's
sound and its three layouts are web/*.js."""

import base64
import json
import os
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
        self.assertEqual(got["loop"], {"on": True, "start": 8.0, "end": 9.0})          # the end after the start
        self.assertEqual(got["velocity"], 0.8)

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
            _songs={}, _placing=False, _full_due=1.0, undo_stack=[], problem="", _worker=worker, settings=Settings(),
            emit=lambda name, data: self.sent.append((name, data)), _writable=lambda: controller,
            _listen=lambda uid, work, done: done(*work(lambda f: None, lambda: False)),
            PLAY_DIR=os.path.join(tmp.name, "play_along"))
        for name in ("on_listen", "on_place", "on_undo", "on_project", "_push_undo", "_clip", "_timeline_for_view"):
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
        path = song["url"].replace("file:///", "")
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

    def test_a_render_that_isnt_one_or_is_too_long_is_said(self):
        self.page.on_place({"frame": 86500, "rate": 48000, "channels": ["!!"]})
        self.assertEqual(self.last("alert")["title"], "Couldn't use that render")
        long = base64.b64encode(np.zeros(48000 * 151, dtype="<f4").tobytes()).decode()
        self.page.on_place({"frame": 86500, "rate": 48000, "channels": [long]})
        self.assertEqual(self.last("alert")["text"], "That sound is too long.")
        self.assertEqual(len(self.tl.tracks), 2)

    def test_the_project_is_kept_cleaned(self):
        self.page.on_project(_project(bpm=300))
        self.assertEqual(self.page.settings["project"]["bpm"], 240.0)
        self.page.on_project({"junk": True})                   # not a project: what was kept stays
        self.assertEqual(self.page.settings["project"]["bpm"], 240.0)
        self.assertEqual(self.page.settings.saves, 1)

    def test_the_view_gets_the_timelines_songs(self):
        got = self.page._timeline_for_view()
        self.assertEqual((got["id"], got["fps"], [s["id"] for s in got["songs"]]), ("t1", 24.0, ["m"]))


# ---------------------------------------------------------------- the view --


class ViewTests(unittest.TestCase):
    """index.html in a web view, no Resolve: the three layouts over one project, the
    computer's keys, a progression, recording, placing - what page.py would send it
    played in by hand (Buddy.receive), what it sends caught."""

    # A made-up song: A minor at 124 BPM, its first bar half a second into the clip.
    BEAT = 60 / 124
    SONG = {"id": "m1", "name": "Bright Days (Music).wav", "bpm": 124, "key": {"tonic": 9, "mode": "minor", "name": "A minor"},
            "bars": [0.5 + i * 4 * 60 / 124 for i in range(40)], "moments": [], "start": 86424, "end": 89000, "fps": 24,
            "url": "", "wave": [128] * 400, "wave_per_s": 50, "length_s": 80}
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
        self.js("Daw.P.count_in = false; Daw.select(Daw.P.tracks[0].id, ''); return true;")
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
        self.assertGreater(self.js(beat), before)
        self.assertTrue(self.js(wheel % "{altKey: true, deltaY: 0, deltaX: 240}"))             # ...as Qt on Windows sends it
        # The arrows: a beat, Shift a bar.
        self.js("Daw.P.cursor = 5; Daw.changed('cursor'); return true;")
        self.key("ArrowRight", shift=True)
        self.assertEqual(self.js("return Daw.P.cursor;"), 8)

    def test_bars_to_play_are_chosen_by_dragging_across_them(self):
        """A click in the ruler moves the playhead; a drag across bars plays just those -
        whole bars, lit down the tracks, the playhead to their start; a lit end drags for
        more or fewer; a double-click is one bar; a clip's menu plays its bars."""
        # Each step finds the ruler again: choosing bars draws it anew. x is in beats.
        ruler = """const b = document.querySelector('.bars'), r = b.getBoundingClientRect(), px = %s;
                   const t = %s ? b.querySelector(%s) : b, o = {button: 0, bubbles: true, pointerId: 1, shiftKey: %s};
                   const p = (type, beat) => t.dispatchEvent(new PointerEvent(type, Object.assign({clientX: r.left + beat * px, clientY: r.top + 15}, o)));
                   %s return true;"""
        px = self.js("return parseFloat(getComputedStyle(document.querySelector('.lanes-grid')).getPropertyValue('--beat'));")

        def act(steps, target=None, shift=False):
            self.js(ruler % (px, "true" if target else "false", json.dumps(target or ""), "true" if shift else "false", steps))

        state = "return [Daw.P.loop.start, Daw.P.loop.end, Daw.P.loop.on, Daw.P.cursor];"
        self.js("Daw.P.loop.on = false; Daw.changed('loop'); return true;")
        act("p('pointerdown', 2); p('pointerup', 2);")                         # a click: the playhead only
        self.assertEqual(self.js(state), [0, 16, False, 2])
        act("p('pointerdown', 5.5); p('pointermove', 9); p('pointermove', 14.2); p('pointerup', 14.2);")
        self.assertEqual(self.js(state), [4, 16, True, 4])                       # bars 2-4, from bar 2
        self.assertEqual(self.js("return document.querySelector('.range-label').textContent;"), "2–4")
        self.assertIn("2–4", self.js("return document.querySelector('.daw-bar .tbtn.toggle').textContent;"))
        shade = self.js("const s = document.querySelector('.loop-shade'); return [s.hidden, parseFloat(s.style.left), parseFloat(s.style.width)];")
        self.assertEqual(shade, [False, 150 + 4 * px, 12 * px])                  # lit down the tracks
        act("p('pointerdown', 16); p('pointermove', 23.6); p('pointerup', 23.6);", ".range-edge.end")
        self.assertEqual(self.js(state)[:2], [4, 24])                            # dragged out to bar 6
        act("p('pointerdown', 6.5); p('pointermove', 3.2); p('pointerup', 3.2);", shift=True)
        self.assertEqual(self.js(state)[:2], [3, 7])                             # Shift: beats, either way
        self.assertEqual(self.js("return document.querySelector('.range-label').textContent;"), "1.4–2.3")
        self.js(f"""const b = document.querySelector('.bars'), r = b.getBoundingClientRect();
                    b.dispatchEvent(new MouseEvent('dblclick', {{clientX: r.left + 30.5 * {px}, clientY: r.top + 15, bubbles: true}}));
                    return true;""")
        self.assertEqual(self.js(state), [28, 32, True, 28])                     # just bar 8
        self.js("Daw.undo(); return true;")
        self.assertEqual(self.js(state)[:2], [3, 7])
        # Playing, new bars are gone to straight away.
        self.js("Daw.start(); return true;")
        self.until("Daw.playing() && Daw.position() > 3.2")
        act("p('pointerdown', 33); p('pointermove', 41); p('pointerup', 41);")
        self.until("Daw.position() >= 32 && Daw.position() < 33")
        self.assertEqual(self.js(state)[:2], [32, 44])
        self.js("Daw.stop(); return true;")
        # A clip's menu: its bars.
        self.js("""const t = Daw.P.tracks[0]; t.clips.push({id: 'k1', name: 'Hook', start: 48, len: 8, notes: [[60, 0, 1, 0.8]]});
                   Daw.changed('clips'); return true;""")
        self.js("""const c = document.querySelector('.clip[data-clip=k1]');
                   c.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 300, clientY: 300}));
                   [...document.querySelectorAll('.menu-pop button')].find(b => b.textContent === 'Play these bars').click(); return true;""")
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
