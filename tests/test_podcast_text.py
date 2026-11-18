"""Podcast's Text Edit (app/pages/podcast/text_edit/): the transcript laid on
the timeline, bleed rejection between mics, subtitles used as the transcript,
the cuts made on the open timeline and checked clip by clip, and their undo -
against fakes of Resolve and of the keyboard, nothing real."""

import copy
import importlib.util
import json
import os
import re
import tempfile
import unittest
from html.parser import HTMLParser
from unittest import mock

import numpy as np

import _paths
from pages.podcast.text_edit import keys as K
from pages.podcast.text_edit import levels as LV
from pages.podcast.text_edit import model as M
from pages.podcast.text_edit import resolve_ext, transcripts

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

HERE = _paths.APP / "pages" / "podcast" / "text_edit"
FPS = 25.0
START = 90000           # 01:00:00:00 at 25 fps


def timeline(items, audio=2, video=1, tid="tl-a", name="Tess and Sam"):
    return {"id": tid, "name": name, "fps": FPS, "start": START, "end": START + 5000, "drop": False,
            "tracks": {"audio": [{"index": i, "name": ["", "Tess", "Sam", "Olive"][i] if i < 4 else "", "locked": False}
                                 for i in range(1, audio + 1)],
                       "video": [{"index": i, "name": "", "locked": False} for i in range(1, video + 1)]},
            "items": items}


def item(kind, track, path, start, end, offset=0.0, channels=None, locked=False):
    return {"kind": kind, "track": track, "uid": f"{kind}{track}{start}", "name": os.path.basename(path or "title"),
            "path": path, "channels": channels, "start": START + start, "end": START + end, "offset": offset,
            "locked": locked}


def words(*spec):
    """("word", start, end[, p]) -> transcript words."""
    return [{"s": s[1], "e": s[2], "w": s[0], "p": s[3] if len(s) > 3 else 0.9} for s in spec]


class WordTests(unittest.TestCase):
    def test_words_follow_the_clip_where_it_is_now(self):
        tx = {"D:/Audio/tess.wav": {"language": "en", "words": words(("Hello", 10.2, 10.6), ("there", 10.7, 11.0),
                                                                       ("later", 30.0, 30.4))}}
        # The clip starts 10 s into the file, 4 s into the timeline, and is 15 s long.
        tl = timeline([item("audio", 1, "D:/Audio/tess.wav", 100, 475, offset=10.0)], audio=1)
        got = M.collect_words(tl, {1}, tx)
        self.assertEqual([w["t"] for w in got], ["Hello", "there"])        # "later" is trimmed off
        self.assertAlmostEqual(got[0]["s"], 4.2)
        # Slipped and moved in Resolve: the same words, the same keys, somewhere else.
        moved = timeline([item("audio", 1, "D:/Audio/tess.wav", 500, 875, offset=10.5)], audio=1)
        again = M.collect_words(moved, {1}, tx)
        self.assertEqual([w["key"] for w in again], [w["key"] for w in got][1:])
        self.assertAlmostEqual(again[0]["s"], 20.2)

    def test_overrides_and_channels_name_the_source(self):
        src = M.source_key("D:/Audio/rec.wav", [1])
        self.assertEqual(src, "D:/Audio/rec.wav#1")
        tx = {src: {"language": "en", "words": words(("helo", 1.0, 1.4)), "overrides": {"0": "hello"}}}
        tl = timeline([item("audio", 1, "D:/Audio/rec.wav", 0, 250, channels=[1])], audio=1)
        got = M.collect_words(tl, {1}, tx)
        self.assertEqual(got[0]["t"], "hello")
        self.assertTrue(got[0]["fixed"])

    def test_tokens_tile_the_dialogue_with_pauses_between(self):
        ws = [{"s": 1.0, "e": 1.4, "t": "So", "tr": 1, "key": "a:0", "lang": "en"},
              {"s": 1.5, "e": 1.8, "t": "um", "tr": 1, "key": "a:1", "lang": "en"},
              {"s": 4.0, "e": 4.5, "t": "yes.", "tr": 1, "key": "a:2", "lang": "en"}]
        toks = M.build_tokens(ws, (0.0, 6.0))
        self.assertEqual([t["k"] for t in toks], ["p", "w", "w", "p", "w", "p"])
        for a, b in zip(toks, toks[1:]):
            self.assertAlmostEqual(a["e"], b["s"])          # no holes, no overlaps
        self.assertEqual((toks[0]["s"], toks[-1]["e"]), (0.0, 6.0))
        self.assertTrue(toks[2]["f"])                       # "um" is a filler
        pause = toks[3]
        self.assertAlmostEqual(pause["s"], 1.8 + M.PAD)     # air kept after the word
        self.assertAlmostEqual(pause["e"], 4.0 - M.PAD)
        self.assertEqual(M.pauses_over(toks, 1.0), [pause["key"], toks[5]["key"]])     # the first is 0.88 s
        self.assertEqual(M.pauses_over(toks, 1.5), [pause["key"]])
        self.assertEqual(M.fillers(toks), ["a:1"])

    def test_fillers_depend_on_the_language(self):
        self.assertTrue(M.is_filler("Um,", "en"))
        self.assertFalse(M.is_filler("er", "de"))           # German "he"
        self.assertTrue(M.is_filler("ähm", "de"))
        self.assertFalse(M.is_filler("so", "en"))

    def test_cuts_merge_and_round_to_frames(self):
        toks = [{"key": "a", "s": 1.0, "e": 1.5, "k": "w"}, {"key": "b", "s": 1.5, "e": 2.0, "k": "w"},
                {"key": "c", "s": 3.0, "e": 3.01, "k": "w"}]
        self.assertEqual(M.cut_frames(toks, {"a", "b"}, FPS), [(25, 50)])
        self.assertEqual(M.cut_frames(toks, {"c"}, FPS), [])        # less than a frame

    def test_find_matches_a_phrase_the_last_word_by_its_start(self):
        toks = [{"id": 0, "k": "w", "t": "We're"}, {"id": 1, "k": "p"}, {"id": 2, "k": "w", "t": "going"},
                {"id": 3, "k": "w", "t": "home."}]
        self.assertEqual(M.find(toks, "we're go"), [[0, 2]])
        self.assertEqual(M.find(toks, "HOME"), [[3, 3]])
        self.assertEqual(M.find(toks, "going nowhere"), [])

    def test_paragraphs_are_turns_with_several_mics(self):
        toks = [{"k": "w", "tr": 1, "t": "Hi", "s": 0, "e": 1}, {"k": "w", "tr": 1, "t": "Sam.", "s": 1, "e": 2},
                {"k": "p", "s": 2, "e": 3}, {"k": "w", "tr": 2, "t": "Hey.", "s": 3, "e": 4},
                {"k": "w", "tr": 1, "t": "So", "s": 4, "e": 5}]
        self.assertEqual(M.paragraphs(toks, True), [(0, 2, 1), (3, 3, 2), (4, 4, 1)])
        one = [{"k": "w", "tr": 1, "t": "a", "s": 0, "e": 1}, {"k": "p", "s": 1, "e": 4},
               {"k": "w", "tr": 1, "t": "b", "s": 4, "e": 5}]
        self.assertEqual(M.paragraphs(one, False), [(0, 1, 1), (2, 2, 1)])

    def test_default_tracks_are_the_dialogue_and_its_cameras(self):
        tl = timeline([item("audio", 1, "D:/Audio/tess.wav", 0, 100), item("audio", 2, "D:/Footage/cam.mov", 0, 100),
                       item("video", 1, "D:/Footage/cam.mov", 0, 100), item("video", 2, "D:/Footage/broll.mov", 0, 50)],
                      video=2)
        self.assertEqual(M.default_tracks(tl), ([1, 2], [1]))

    def test_edit_points_mark_where_clips_meet(self):
        tl = timeline([item("audio", 1, "a.wav", 0, 100), item("audio", 1, "a.wav", 100, 200, offset=10)], audio=1)
        toks = [{"id": 0, "s": 0.0, "e": 2.0}, {"id": 1, "s": 2.0, "e": 4.5}, {"id": 2, "s": 4.5, "e": 8.0}]
        self.assertEqual(M.edit_points(tl, {1}, toks), [2])        # before the first token after the edit


class SubtitleTests(unittest.TestCase):
    """Subtitles already on the timeline, kept as the transcript of the clips under them."""

    def test_a_subtitles_words_share_its_time_by_their_length(self):
        got = M.cue_words(2.0, 4.0, "Hi <i>there</i>  Tess")
        self.assertEqual([w for _s, _e, w in got], ["Hi", "there", "Tess"])
        self.assertEqual((got[0][0], got[-1][1]), (2.0, 4.0))
        self.assertLess(got[0][1] - got[0][0], got[1][1] - got[1][0])       # "Hi" is the shortest
        self.assertEqual(M.cue_words(4.0, 4.0, "nothing"), [])

    def test_words_land_in_the_source_of_the_clip_under_them(self):
        # tess.wav from 10 s into the file sits at 0-4 s; sam.wav follows at 4-8 s; A2 lies under both.
        tl = timeline([item("audio", 1, "D:/Audio/tess.wav", 0, 100, offset=10.0),
                       item("audio", 1, "D:/Audio/sam.wav", 100, 200),
                       item("audio", 2, "D:/Audio/room.wav", 0, 200)])
        found, lost = M.words_from_cues(tl, {1, 2}, [(25, 75, "Welcome back"), (125, 150, "Thanks"),
                                                      (400, 450, "nobody here")])
        self.assertEqual(lost, 2)
        self.assertEqual(sorted(found), ["D:/Audio/sam.wav", "D:/Audio/tess.wav"])      # the lowest track's clip
        tess = found["D:/Audio/tess.wav"]
        self.assertEqual([w["w"] for w in tess], ["Welcome", "back"])
        self.assertAlmostEqual(tess[0]["s"], 11.0)          # 1 s into the clip, 10 s into the file
        self.assertAlmostEqual(tess[-1]["e"], 13.0)
        # Laid back onto the timeline, the words are where the subtitles were.
        words_ = M.collect_words(tl, {1, 2}, {src: {"words": w} for src, w in found.items()})
        self.assertEqual([(w["t"], round(w["s"], 2)) for w in words_], [("Welcome", 1.0), ("back", 2.23), ("Thanks", 5.0)])

    def test_a_source_on_the_timeline_twice_keeps_each_word_once(self):
        tl = timeline([item("audio", 1, "D:/Audio/tess.wav", 0, 100), item("audio", 1, "D:/Audio/tess.wav", 100, 200)])
        found, _lost = M.words_from_cues(tl, {1}, [(0, 50, "Welcome"), (100, 150, "Welcome")])
        self.assertEqual(len(found["D:/Audio/tess.wav"]), 1)


class BleedTests(unittest.TestCase):
    """Two mics, each hearing both people: Tess on A1 (a hot mic), Sam on A2."""

    def setUp(self):
        n = int(40 / LV.HOP)
        rng = np.random.default_rng(3)
        self.a = np.full(n, -70.0, dtype=np.float32) + rng.normal(0, 1, n).astype(np.float32)
        self.b = np.full(n, -62.0, dtype=np.float32) + rng.normal(0, 1, n).astype(np.float32)

        def talk(arr, other, s, e, own, heard):
            i, j = int(s / LV.HOP), int(e / LV.HOP)
            arr[i:j] = own
            other[i:j] = heard

        talk(self.a, self.b, 1, 6, -12.0, -30.0)        # Tess: hot on her mic, faint on Sam's
        talk(self.b, self.a, 8, 14, -26.0, -38.0)       # Sam: quiet, but louder on his than on Tess's
        talk(self.a, self.b, 16, 20, -12.0, -30.0)
        talk(self.b, self.a, 22, 26, -26.0, -38.0)
        self.mics = LV.Mics({1: self.a, 2: self.b})

    def w(self, t, s, e, tr, p=0.9):
        return {"t": t, "s": s, "e": e, "tr": tr, "p": p, "key": f"{tr}:{t}:{s}"}

    def test_each_word_comes_from_the_mic_it_was_said_into(self):
        ws = [self.w("Welcome", 2.0, 2.6, 1), self.w("welcome", 2.05, 2.6, 2, p=0.95),     # heard twice
              self.w("Thanks", 9.0, 9.5, 2), self.w("thanks", 9.02, 9.5, 1, p=0.99),
              self.w("faint", 18.0, 18.5, 2)]       # bleed of Tess on Sam's mic, which Tess's mic heard as "right"
        ws.append(self.w("right", 18.0, 18.5, 1))
        ws.sort(key=lambda w: (w["s"], w["tr"]))
        kept = LV.filter_words(ws, self.mics)
        self.assertEqual(sorted((w["t"], w["tr"]) for w in kept),
                         [("Thanks", 2), ("Welcome", 1), ("right", 1)])

    def test_a_word_only_the_wrong_mic_heard_goes_to_its_person(self):
        kept = LV.filter_words([self.w("yeah", 23.0, 23.4, 1)], self.mics)
        self.assertEqual([(w["t"], w["tr"]) for w in kept], [("yeah", 2)])

    def test_the_hot_mic_is_evened_out(self):
        # A1 is ~14 dB hotter: louder in dB even when Sam talks, but past
        # the pair's even point it's still Sam's.
        self.assertGreater(self.mics.dominance(2, 1, 9.0, 9.5), LV.DECIDE_DB)
        self.assertGreater(self.mics.dominance(1, 2, 3.0, 3.5), LV.DECIDE_DB)

    def test_levels_lay_onto_the_timeline_from_each_clip(self):
        tl = timeline([item("audio", 1, "a.wav", 50, 150, offset=10.0)], audio=1)
        hop = 0.05
        data = np.arange(400, dtype=np.float32)       # level i at i*hop seconds into the file
        got = LV.track_levels(tl, {1}, {"a.wav": (hop, data)}, 8.0)[1]
        self.assertTrue(np.isnan(got[0]))             # before the clip
        self.assertAlmostEqual(float(got[int(round(2.0 / LV.HOP))]), 200.0)   # timeline 2 s = file 10 s
        raw = {"hop": 0.05, "db": "ZGQ="}             # bytes 100, 100: 0 dBFS
        self.assertEqual(M.level_array(raw)[1].tolist(), [0.0, 0.0])

    def test_without_levels_only_exact_doubles_go(self):
        ws = [self.w("Hi", 1.0, 1.3, 1, p=0.6), self.w("hi", 1.05, 1.3, 2, p=0.9), self.w("there", 2.0, 2.3, 1)]
        self.assertEqual([(w["t"], w["tr"]) for w in LV.filter_words(ws, None)], [("hi", 2), ("there", 1)])


class LiveLayoutTests(unittest.TestCase):
    def test_a_ripple_moves_splits_and_removes(self):
        snap = {("audio", 1): [(0, 100, 0), (100, 300, 500)], ("video", 1): [(0, 300, 0)]}
        out = M.rippled(snap, 150, 200)
        self.assertEqual(out[("audio", 1)], [(0, 100, 0), (100, 150, 500), (150, 250, 600)])
        self.assertEqual(out[("video", 1)], [(0, 150, 0), (150, 250, 200)])
        # Resolve keeping a clip whole where nothing's missing in it counts the same.
        self.assertTrue(M.same_layout({("v", 1): [(0, 100, 0), (100, 200, 100)]}, {("v", 1): [(0, 200, 0)]}))
        self.assertFalse(M.same_layout({("v", 1): [(0, 100, 0)]}, {("v", 1): [(0, 99, 0)]}))
        # Resolve's own rounding of where a split clip starts in its source: a frame, no more.
        cut = {("v", 1): [(0, 150, 0), (150, 250, 200)]}
        self.assertTrue(M.same_layout(cut, {("v", 1): [(0, 150, 0), (150, 250, 199)]}))
        self.assertFalse(M.same_layout(cut, {("v", 1): [(0, 150, 0), (150, 250, 198)]}))
        self.assertFalse(M.same_layout(cut, {("v", 1): [(0, 150, 0), (151, 250, 200)]}))
        # A recording razored into pieces whose source frames don't quite follow on (as Resolve
        # reads them): the cut's second half runs on into the next piece, or doesn't - the same.
        before = {("v", 1): [(0, 300, 0), (300, 400, 299)]}
        self.assertTrue(M.same_layout(M.rippled(before, 100, 150), {("v", 1): [(0, 100, 0), (100, 250, 149), (250, 350, 299)]}))
        self.assertTrue(M.same_layout(M.rippled(before, 100, 150), {("v", 1): [(0, 100, 0), (100, 350, 149)]}))
        self.assertFalse(M.same_layout(M.rippled(before, 100, 150), before))

    def test_what_went_wrong_is_named(self):
        before = {("audio", 1): [(0, 300, 0)], ("audio", 2): [(0, 300, 0)]}
        expected = M.rippled(before, 100, 150)
        half = {("audio", 1): expected[("audio", 1)], ("audio", 2): before[("audio", 2)]}
        self.assertIn("A2", M.layout_problem(expected, half, {"before": before}))
        self.assertEqual(M.layout_problem(expected, before, {"before": before}), "Resolve didn't make the cut.")


class KeyTests(unittest.TestCase):
    def test_shortcuts_are_spelled_one_way(self):
        self.assertEqual(K.canonical("shift + backspace"), "Shift+Backspace")
        self.assertEqual(K.canonical("Cmd+Shift+a"), "Ctrl+Shift+A")
        self.assertEqual(K.canonical("Option+x"), "Alt+X")
        self.assertEqual(K.canonical("Ctrl+Alt"), "")
        self.assertEqual(K.canonical("A+B"), "")

    def test_presets_and_custom_keys(self):
        keys = K.load({"keys": {"ripple_delete": "shift+delete", "undo": "nonsense"}})
        self.assertEqual(keys["ripple_delete"], "Shift+Delete")
        self.assertEqual(keys["undo"], "Ctrl+Z")
        self.assertEqual(K.preset_of(K.load({})), "resolve")
        self.assertEqual(K.preset_of(keys), "")

    def test_a_shortcut_is_pressed_and_released_in_order(self):
        class Board:
            def __init__(self):
                self.log = []

            def press(self, key):
                self.log.append(("down", str(key)))

            def release(self, key):
                self.log.append(("up", str(key)))

        board = Board()
        with mock.patch.object(K, "SETTLE_S", 0):
            K.Presser(board, focus=lambda: True).press("Shift+Backspace")
        self.assertEqual([d for d, _k in board.log], ["down", "down", "up", "up"])
        self.assertIn("shift", board.log[0][1])
        self.assertIn("backspace", board.log[1][1])
        self.assertEqual(board.log[-1][1], board.log[0][1])


class TranscriptCacheTests(unittest.TestCase):
    def test_saved_by_file_channels_and_language_and_corrected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tess.wav")
            with open(path, "wb") as f:
                f.write(b"RIFF0000WAVE")
            data = {"v": 1, "language": "en", "words": words(("helo", 0.1, 0.4)), "overrides": {}}
            self.assertTrue(transcripts.save(path, data, [0], "", folder=d))
            self.assertIsNone(transcripts.cached(path, None, "", folder=d))      # all channels: another source
            self.assertTrue(transcripts.correct(path, [0], "", 0, "hello", folder=d))
            self.assertEqual(transcripts.cached(path, [0], "", folder=d)["overrides"], {"0": "hello"})
            self.assertTrue(transcripts.correct(path, [0], "", 0, "helo", folder=d))      # back as heard
            self.assertEqual(transcripts.cached(path, [0], "", folder=d)["overrides"], {})

    def test_the_workers_words_flatten_with_their_probability(self):
        result = {"segments": [{"words": [{"start": 0.0, "end": 0.3, "word": " Um,", "p": 0.42},
                                          {"start": 0.3, "end": 0.6, "word": " hi"}]}]}
        self.assertEqual(transcripts.words_of(result), [{"s": 0.0, "e": 0.3, "w": "Um,", "p": 0.42},
                                                        {"s": 0.3, "e": 0.6, "w": "hi", "p": None}])


class WorkerTests(unittest.TestCase):
    """worker.py runs in the transcription venv; what's tested here needs only numpy."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("te_worker", _paths.APP / "pages" / "transcribe" / "worker.py")
        cls.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.worker)

    def test_whisper_words_carry_their_probability_and_fillers_get_a_hesitant_primer(self):
        w = self.worker
        seen = {}

        class Word:
            def __init__(self, start, end, word, probability):
                self.start, self.end, self.word, self.probability = start, end, word, probability

        class Seg:
            start, end, text = 0.0, 1.0, " Um, hi"
            words = [Word(0.0, 0.4, " Um,", 0.61), Word(0.5, 1.0, " hi", 0.98)]

        class Model:
            def transcribe(self, audio, **kw):
                seen.update(kw)
                return [Seg()], None

        with mock.patch.object(w, "emit"):
            out = w._whisper_segments(Model(), None, "en", "", 1.0, fillers=True)
        self.assertEqual([x["p"] for x in out[0]["words"]], [0.61, 0.98])
        self.assertIn("Um,", seen["hotwords"])
        with mock.patch.object(w, "emit"):
            w._whisper_segments(Model(), None, "en", "", 1.0)
        self.assertEqual(seen["hotwords"], w.PRIMERS["en"])          # Subtitles unchanged

    def test_levels_are_written_every_50_ms(self):
        audio = np.concatenate([np.zeros(800, np.float32), np.full(800, 0.5, np.float32)])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lv.json")
            self.worker._write_levels(audio, 16000, path)
            with open(path, encoding="utf-8") as f:
                hop, db = M.level_array(json.load(f))
        self.assertAlmostEqual(hop, 0.05)
        self.assertEqual(len(db), 2)
        self.assertEqual(db[0], -100.0)
        self.assertAlmostEqual(float(db[1]), -6.0, delta=1.0)


# ------------------------------------------------------- a fake Resolve --

class FakeMpi:
    def __init__(self, path):
        self.path = path

    def GetUniqueId(self):
        return "mpi-" + self.path

    def GetClipProperty(self, *_a):
        return {"File Path": self.path, "FPS": str(FPS), "Audio Ch": "2"}


class FakeItem:
    text = None             # a subtitle's: what it says

    def __init__(self, path, start, end, source):
        self.path, self.start, self.end, self.source = path, start, end, source

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end

    def GetSourceStartFrame(self):
        return self.source

    def GetMediaPoolItem(self):
        return FakeMpi(self.path) if self.path else None

    def GetUniqueId(self):
        return f"{self.path}@{self.start}"

    def GetName(self):
        return self.text if self.text is not None else os.path.basename(self.path or "Text+")

    def GetSourceAudioChannelMapping(self):
        return json.dumps({"track_mapping": {"1": {"channel_idx": [2]}}})


class FakeTimeline:
    """Two dialogue tracks and a camera; Ripple Delete (by its keys) cuts In-Out
    from the tracks whose Auto Track Selector is on."""

    def __init__(self):
        self.tracks = {("audio", 1): [FakeItem("D:/Audio/tess.wav", START, START + 500, 0)],
                       ("audio", 2): [FakeItem("D:/Audio/sam.wav", START, START + 500, 0)],
                       ("video", 1): [FakeItem("D:/Footage/cam.mov", START, START + 500, 0)]}
        self.selected = {("audio", 1), ("audio", 2), ("video", 1)}
        self.marks = None
        self.history, self.future = [], []
        self.timecode = "01:00:00:00"

    def GetUniqueId(self):
        return "tl-a"

    def GetName(self):
        return "Tess and Sam"

    def GetSetting(self, key):
        return {"timelineFrameRate": "25", "timelineDropFrameTimecode": "0"}.get(key, "")

    def GetStartFrame(self):
        return START

    def GetEndFrame(self):
        return START + 500

    def GetTrackCount(self, kind):
        return max((n for k, n in self.tracks if k == kind), default=0)

    def GetTrackName(self, kind, n):
        return {("audio", 1): "Tess", ("audio", 2): "Sam"}.get((kind, n), "")

    def GetIsTrackLocked(self, *_a):
        return False

    def GetItemListInTrack(self, kind, n):
        return list(self.tracks.get((kind, n), []))

    def SetMarkInOut(self, first, last, _kind="all"):
        if first < START:               # this Resolve counts from 00:00:00:00: absolute frames
            return False
        self.marks = (first, last)
        return True

    def GetMarkInOut(self):
        return {"video": {"in": self.marks[0], "out": self.marks[1]}} if self.marks else {}

    def ClearMarkInOut(self, *_a):
        self.marks = None
        return True

    def GetCurrentTimecode(self):
        return self.timecode

    def SetCurrentTimecode(self, tc):
        self.timecode = tc
        return True

    def _state(self):
        return {k: [(i.path, i.start, i.end, i.source) for i in v] for k, v in self.tracks.items()}

    def _restore(self, state):
        self.tracks = {k: [FakeItem(*row) for row in v] for k, v in state.items()}

    def ripple(self):
        first, last = self.marks
        end = last + 1
        self.history.append(self._state())
        self.future.clear()
        for key in list(self.tracks):
            if key not in self.selected:
                continue
            rows = []
            for i in self.tracks[key]:
                if i.end <= first:
                    rows.append(i)
                elif i.start >= end:
                    rows.append(FakeItem(i.path, i.start - (end - first), i.end - (end - first), i.source))
                else:
                    if i.start < first:
                        rows.append(FakeItem(i.path, i.start, first, i.source))
                    if i.end > end:
                        rows.append(FakeItem(i.path, first, i.end - (end - first), i.source + max(end, i.start) - i.start))
            self.tracks[key] = rows

    def undo(self):
        if self.history:
            self.future.append(self._state())
            self._restore(self.history.pop())

    def redo(self):
        if self.future:
            self.history.append(self._state())
            self._restore(self.future.pop())


def subtitle(first, end, text):
    it = FakeItem("", START + first, START + end, 0)
    it.text = text
    return it


class FakeController:
    def __init__(self, tl):
        self.tl = tl
        self.resolve = mock.Mock(EXPORT_OTIO=1, EXPORT_NONE=0)

    def current_project(self):
        project = mock.Mock()
        project.GetCurrentTimeline.return_value = self.tl
        return project


class ResolveReadTests(unittest.TestCase):
    def test_the_timeline_is_read_with_each_clips_channels(self):
        tl = FakeTimeline()
        data = resolve_ext.read_timeline(FakeController(tl), {})
        self.assertEqual(data["fps"], 25.0)
        a1 = next(i for i in data["items"] if i["kind"] == "audio" and i["track"] == 1)
        self.assertEqual((a1["path"], a1["channels"], a1["start"]), ("D:/Audio/tess.wav", [1], START))
        self.assertEqual(data["tracks"]["audio"][0]["name"], "Tess")
        before = data["sig"]
        tl.tracks[("audio", 1)][0].start += 1
        self.assertNotEqual(resolve_ext.read_timeline(FakeController(tl), {})["sig"], before)

    def test_subtitle_tracks_are_counted_and_read_as_text(self):
        tl = FakeTimeline()
        self.assertEqual(resolve_ext.read_timeline(FakeController(tl), {})["subtitles"], [])
        tl.tracks[("subtitle", 1)] = [subtitle(150, 175, "Thanks  Tess."), subtitle(25, 60, "Welcome back.")]
        data = resolve_ext.subtitle_cues(FakeController(tl), {}, 1, "tl-a")
        self.assertEqual(data["timeline"]["subtitles"], [{"index": 1, "count": 2, "name": ""}])
        self.assertEqual(data["cues"], [[25, 60, "Welcome back."], [150, 175, "Thanks Tess."]])
        self.assertFalse([i for i in data["timeline"]["items"] if i["kind"] == "subtitle"])
        with self.assertRaises(resolve_ext.ResolveConnectionError):
            resolve_ext.subtitle_cues(FakeController(tl), {}, 1, "another")

    def test_in_and_out_find_which_frames_resolve_counts_from(self):
        tl = FakeTimeline()
        resolve_ext._BASIS["value"] = None
        self.assertTrue(resolve_ext.set_marks(FakeController(tl), 100, 150))
        self.assertEqual(tl.marks, (START + 100, START + 149))        # absolute, Out the last frame in
        self.assertEqual(resolve_ext._BASIS["value"], "abs")
        resolve_ext._BASIS["value"] = None


class Mem(dict):
    def save(self):
        pass


class Host:
    def __init__(self, controller):
        self.controller = controller
        self.connected = True
        self.tools = {}
        self.busy = []

    def tool_settings(self, tool_id, defaults=None):
        return self.tools.setdefault(tool_id, Mem(defaults or {}))

    def ensure_connected(self):
        return self.controller

    def set_busy(self, on, message=None):
        self.busy.append((on, message))

    def pump_busy(self, message=None):
        pass

    def theme_tokens(self):
        return {"surface": "#202020"}


class KeysFor:
    """Stands in for keys.Presser: Ripple Delete and Undo do to the fake what Resolve would."""

    def __init__(self, tl, broken=False):
        self.tl, self.broken, self.pressed = tl, broken, []

    def focus(self):
        return True

    def press(self, combo):
        self.pressed.append(combo)
        if combo == "Shift+Backspace" and not self.broken:
            self.tl.ripple()
        elif combo == "Ctrl+Z":
            self.tl.undo()
        elif combo == "Ctrl+Shift+Z":
            self.tl.redo()


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": self.home.name, "BUDDY_RESOLVE_READER": "off"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self):
        from pages.podcast.text_edit.page import TextEditPage
        resolve_ext._BASIS["value"] = None
        self.tl = FakeTimeline()
        self.host = Host(FakeController(self.tl))
        page = TextEditPage(self.host)
        self.addCleanup(page.deleteLater)
        page._read_timer.stop()
        page._head_timer.stop()
        self.events = []
        page.emit = lambda name, payload=None: self.events.append((name, payload))

        def call(name, args, job, done):
            try:
                result, error = job(), None
            except Exception as exc:  # noqa: BLE001
                result, error = None, exc
            done(result, error)
            return True

        page._calls.call = call
        page._calls.busy = lambda: False
        page._calls.wait_idle = lambda timeout: True
        page.transcripts = {
            "D:/Audio/tess.wav#1": {"language": "en", "words": words(("Welcome", 1.0, 1.5), ("um", 1.6, 1.9, 0.3),
                                                                      ("back.", 2.0, 2.4))},
            "D:/Audio/sam.wav#1": {"language": "en", "words": words(("Thanks", 6.0, 6.5), ("Tess.", 6.6, 7.0))},
        }
        page._read()
        return page

    def last(self, name):
        return [p for n, p in self.events if n == name][-1]

    def test_the_transcript_reads_as_turns(self):
        page = self.make()
        doc = self.last("doc")
        self.assertTrue(doc["multi"])
        self.assertEqual(doc["speakers"], {"1": "Tess", "2": "Sam"})
        self.assertEqual([t.get("t") for t in doc["tokens"] if t["k"] == "w"], ["Welcome", "um", "back.", "Thanks", "Tess."])
        self.assertEqual(doc["fillers"], 1)
        self.assertEqual([p[2] for p in doc["paras"]], [1, 2])
        self.assertTrue(any(t.get("lo") for t in doc["tokens"]))       # "um" was unsure
        self.assertIsNotNone(page.timeline)

    def test_a_stale_selection_is_refused(self):
        page = self.make()
        page._presser = KeysFor(self.tl)
        page.on_strike({"ids": [0], "version": -1})
        self.assertEqual(self.last("toast")["text"], "The transcript changed – try again.")
        self.assertEqual(page._presser.pressed, [])

    def test_several_cuts_are_undone_and_redone_a_step_at_a_time(self):
        page = self.make()
        page._presser = KeysFor(self.tl)
        ends = [self.tl.tracks[("audio", 1)][-1].end]
        with mock.patch.object(K, "available", return_value=""), mock.patch("pages.podcast.text_edit.page.time.sleep"):
            for word in ("um", "Thanks", "Welcome"):
                doc = self.last("doc")
                at = next(i for i, t in enumerate(doc["tokens"]) if t.get("t") == word)
                page.on_strike({"ids": [at], "version": doc["version"]})
                ends.append(self.tl.tracks[("audio", 1)][-1].end)
            self.assertEqual(len(page.undo_stack), 3)
            self.assertTrue(ends[0] > ends[1] > ends[2] > ends[3])
            self.assertNotIn("um", [t.get("t") for t in self.last("doc")["tokens"]])
            for back in (2, 1, 0):          # each Undo: one cut back, no more
                page.on_undo()
                self.assertEqual(self.tl.tracks[("audio", 1)][-1].end, ends[back])
            self.assertFalse(self.last("state")["can_undo"])
            self.assertIn("um", [t.get("t") for t in self.last("doc")["tokens"]])
            for again in (1, 2):
                page.on_redo()
                self.assertEqual(self.tl.tracks[("audio", 1)][-1].end, ends[again])
            self.assertTrue(self.last("state")["can_redo"])
        self.assertFalse([p for n, p in self.events if n == "alert"])
        # An edit made in Resolve itself: Buddy's steps no longer line up with Resolve's, and go.
        for items in self.tl.tracks.values():
            for it in items:
                it.start += 50
                it.end += 50
        page._read()
        self.assertFalse(self.last("state")["can_undo"] or self.last("state")["can_redo"])

    def test_filler_words_are_cut_in_one_step(self):
        page = self.make()
        page._presser = KeysFor(self.tl)
        self.assertEqual(self.last("doc")["fillers"], 1)
        with mock.patch.object(K, "available", return_value=""), mock.patch("pages.podcast.text_edit.page.time.sleep"):
            page.on_remove_fillers()
        self.assertEqual(self.last("doc")["fillers"], 0)
        self.assertEqual(len(page.undo_stack), 1)
        page.on_remove_fillers()
        self.assertEqual(self.last("toast")["text"], "No filler words to remove.")

    def test_a_live_cut_is_made_by_resolve_and_checked(self):
        page = self.make()
        page._presser = KeysFor(self.tl)
        doc = self.last("doc")
        um = next(i for i, t in enumerate(doc["tokens"]) if t.get("t") == "um")
        with mock.patch.object(K, "available", return_value=""), mock.patch("pages.podcast.text_edit.page.time.sleep"):
            page.on_strike({"ids": [um], "version": doc["version"]})
        self.assertIn("Shift+Backspace", page._presser.pressed)
        self.assertLess(self.tl.tracks[("audio", 1)][-1].end, START + 500)
        self.assertEqual(self.tl.tracks[("audio", 1)][-1].end, self.tl.tracks[("video", 1)][-1].end)
        self.assertFalse([p for n, p in self.events if n == "alert"])
        self.assertTrue(page.undo_stack and page.undo_stack[-1]["kind"] == "live")
        # Undo presses Resolve's Undo until the timeline is back.
        with mock.patch.object(K, "available", return_value=""), mock.patch("pages.podcast.text_edit.page.time.sleep"):
            page.on_undo()
        self.assertEqual(self.tl.tracks[("audio", 1)][0].end, START + 500)

    def test_a_track_resolve_didnt_cut_is_undone_and_named(self):
        page = self.make()
        page._presser = KeysFor(self.tl)
        self.tl.selected.discard(("audio", 2))          # its Auto Track Selector off
        doc = self.last("doc")
        um = next(i for i, t in enumerate(doc["tokens"]) if t.get("t") == "um")
        with mock.patch.object(K, "available", return_value=""), \
                mock.patch("pages.podcast.text_edit.page.time.sleep"), \
                mock.patch.object(type(page), "_out_inclusive", True):
            from pages.podcast.text_edit import page as page_mod
            with mock.patch.object(page_mod, "CUT_WAIT_S", 0.01):
                page.on_strike({"ids": [um], "version": doc["version"]})
        alert = self.last("alert")
        self.assertIn("A2", alert["title"])
        self.assertEqual(self.tl.tracks[("audio", 1)][0].end, START + 500)       # put back

    def test_subtitles_on_the_timeline_become_the_transcript(self):
        page = self.make()
        page.transcripts.clear()
        self.tl.tracks[("subtitle", 1)] = [subtitle(25, 60, "Welcome um back."), subtitle(150, 175, "Thanks Tess."),
                                           subtitle(900, 950, "Past the end.")]
        page._sig = None
        page._read()
        doc = self.last("doc")
        self.assertEqual(doc["subtitles"], [{"index": 1, "count": 3, "name": ""}])
        self.assertFalse([t for t in doc["tokens"] if t["k"] == "w"])
        self.assertFalse(doc["estimated"])
        saved = {}
        with mock.patch.object(transcripts, "save", lambda path, data, channels=None, language="", folder=None:
                               saved.update({path: data}) or True):
            page.on_use_subtitles({"index": 1})
            doc = self.last("doc")
            self.assertEqual([t["t"] for t in doc["tokens"] if t["k"] == "w"], ["Welcome", "um", "back.", "Thanks", "Tess."])
            self.assertTrue(doc["estimated"])
            self.assertEqual(doc["fillers"], 1)
            self.assertEqual(list(saved), ["D:/Audio/tess.wav"])           # A1, the lowest dialogue track
            self.assertEqual(saved["D:/Audio/tess.wav"]["engine"], transcripts.SUBTITLES)
            toasts = [p["text"] for n, p in self.events if n == "toast"]
            self.assertIn("Using the timeline's subtitles – 5 words.", toasts)
            self.assertIn("3 subtitle words have no dialogue clip under them and were left out.", toasts)
            # The words sit where their subtitle does: "Welcome" starts the one at frame 25.
            welcome = next(t for t in doc["tokens"] if t.get("t") == "Welcome")
            self.assertLessEqual(welcome["s"], 1.0)
            self.assertGreater(welcome["e"], 1.0)
            # Transcribe still offers to hear it properly: subtitles' text isn't "already transcribed".
            with mock.patch.object(transcripts, "engine_plan", return_value=(None, "No engine in this test.")):
                page.on_transcribe()
            self.assertEqual(self.last("alert")["text"], "No engine in this test.")
            # A track with nothing on it, or one that isn't there, is refused.
            page.on_use_subtitles({"index": 4})
            self.assertEqual(self.last("toast")["text"], "There are no subtitles on this timeline.")

    def test_subtitles_over_no_dialogue_say_so(self):
        page = self.make()
        self.tl.tracks[("subtitle", 1)] = [subtitle(900, 950, "Past the end.")]
        page._sig = None
        page._read()
        before = dict(page.transcripts)
        with mock.patch.object(transcripts, "save", return_value=True):
            page.on_use_subtitles()
        self.assertEqual(self.last("alert")["title"], "No subtitles over the dialogue")
        self.assertEqual(page.transcripts, before)

    def test_tracks_are_shown_by_the_files_they_play(self):
        self.make()
        tracks = self.last("doc")["tracks"]
        self.assertEqual([(t["short"], t["file"], t["name"]) for t in tracks["audio"]],
                         [("A1", "tess.wav", "Tess"), ("A2", "sam.wav", "Sam")])
        self.assertTrue(all(t["file"] for t in tracks["video"]))     # the camera's file, not the track's name

    def test_tracks_can_be_left_out_but_not_every_dialogue_one(self):
        page = self.make()
        page.on_toggle_track({"kind": "audio", "index": 2})
        self.assertEqual([t["on"] for t in self.last("doc")["tracks"]["audio"]], [True, False])
        page.on_toggle_track({"kind": "audio", "index": 1})
        self.assertEqual(self.last("toast")["text"], "Keep at least one dialogue track on.")

    def test_options_are_checked(self):
        page = self.make()
        page.on_option({"key": "text_size", "value": "nope"})
        self.assertEqual(page.settings["text_size"], "medium")
        page.on_option({"key": "mode", "value": "build"})          # no such choice any more
        self.assertNotIn("mode", page.settings)
        page.on_option({"key": "pause_over", "value": 0.1})
        self.assertEqual(page.settings["pause_over"], M.PAUSE_REMOVE_MIN)
        page.on_option({"key": "follow", "value": False})
        self.assertFalse(self.last("state")["follow"])


class ViewContractTests(unittest.TestCase):
    """What tests/test_web_theme.py checks for pages/*/web, for this nested one."""

    def setUp(self):
        self.js = (HERE / "web" / "text_edit.js").read_text(encoding="utf-8")
        self.html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
        self.css = (HERE / "web" / "text_edit.css").read_text(encoding="utf-8")
        self.py = (HERE / "page.py").read_text(encoding="utf-8")

    def test_every_action_and_event_is_handled(self):
        sent = set(re.findall(r"\bsend\(\"(\w+)\"", self.js)) | {"switch_tab"}
        for action in sent:
            self.assertRegex(self.py, rf"def on_{action}\(self", action)
        emitted = set(re.findall(r"self\.emit\(\"(\w+)\"", self.py))
        handled = set(re.findall(r"\bBuddy\.on\(\"(\w+)\"", self.js))
        self.assertFalse(emitted - handled, sorted(emitted - handled))

    def test_only_local_files_and_theme_variables(self):
        web = HERE / "web"
        for src in re.findall(r'(?:src|href)="([^"]+)"', self.html):
            if not src.startswith("qrc:"):
                self.assertTrue((web / src).resolve().exists(), src)
        from core import theme as t
        from core.web_theme import web_theme
        used = set(re.findall(r"var\(--([\w-]+)", self.css))
        local = set(re.findall(r"^\s+--([\w-]+):", self.css, re.M)) | set(re.findall(r"[;{]\s*--([\w-]+):", self.css))
        for name in t.list_themes():
            for sub in t.list_subthemes(name):
                have = set(web_theme(name, sub, t.get_theme_tokens(name, sub))["vars"])
                self.assertFalse(used - local - have, f"{name}/{sub}: {sorted(used - local - have)}")
        text = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        for m in re.finditer(r"font(?:-size)?\s*:\s*([^;}]*)", text):
            self.assertNotRegex(m.group(1), r"\b\d+(\.\d+)?(px|pt|rem)\b", m.group(0))


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found, self._skip = set(), 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        for key in ("title", "placeholder", "aria-label"):
            if attrs.get(key):
                self.found.add(attrs[key])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and len(re.findall(r"[^\W\d_]", data)) >= 2:
            self.found.add(" ".join(data.split()))


class TranslationTests(unittest.TestCase):
    def strings(self):
        parser = _Text()
        parser.feed((HERE / "web" / "index.html").read_text(encoding="utf-8"))
        found = set(parser.found)
        js = (HERE / "web" / "text_edit.js").read_text(encoding="utf-8")
        found |= set(re.findall(r'Buddy\.t\("([^"]+)"', js))
        found |= set(re.findall(r'(?:text|title|label|ok):\s*"([^"]{3,})"', js))
        found |= set(re.findall(r'(?:empty|notice)\(\s*"([^"]{3,})"', js))
        found |= set(re.findall(r'\?\s*"([A-Z][^"]{8,})"', js)) | set(re.findall(r':\s*"([A-Z][^"]{8,})"\)?;', js))
        py = (HERE / "page.py").read_text(encoding="utf-8")
        found |= set(re.findall(r'"(?:text|title)":\s*"([^"{]{3,})"', py))
        found |= {re.sub(r"\{[^}]+\}", lambda m: "{" + {"len(keys)": "n", "seconds:g": "s", "made": "made",
                                                          "len(cuts)": "n"}.get(m.group(0)[1:-1], m.group(0)[1:-1]) + "}", s)
                  for s in re.findall(r'"(?:text|title)":\s*f"([^"]{3,})"', py)}
        found |= {c for c in K.LABELS.values()} | {p["label"] for p in K.PRESETS.values()}
        found |= {"Best installed", page_strings.ADVICE, "Removing {n} filler words…", "Removing {n} pauses…",
                  "Cutting…", "Undoing…", "Redoing…",
                  "Press Transcribe: each dialogue clip is transcribed once, on this computer.",
                  "Put the recording on the timeline, and pick its tracks in Options.",
                  "Click to leave {track} out", "Click to include {track}", page_strings.NO_OVERLAP,
                  "Use them as the transcript straight away, or press Transcribe for exact word timings."}
        found |= {"Pressing keys in Resolve needs the pynput package, which isn't installed.",
                  "Text Edit transcription", "Text Edit is transcribing – try again when it's done."}
        return {s for s in found if not s.startswith("A1") and re.search(r"[A-Za-z]{2}", s)}

    def test_every_string_is_translated(self):
        from core import i18n
        # Exactly: a near miss can come out half translated through a template.
        missing = sorted(s for s in self.strings() if s not in i18n.TRANSLATIONS)
        self.assertEqual(missing, [])


from pages.podcast.text_edit import page as page_strings  # noqa: E402 - after the Qt check above


if __name__ == "__main__":
    unittest.main()
