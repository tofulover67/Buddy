"""Podcast's Auto Switch (app/pages/podcast/switch/): who's talking on each
mic, voices told apart on a shared mic, the cut plan, the camera tracks
razored in an OTIO export, the off pieces turned off in the new timeline,
and the page between them. No Resolve, no model, no speech engine: made-up
mics (numpy), made-up voice prints and a fake Resolve."""

import os
import unittest
from unittest import mock

import numpy as np

import _paths  # noqa: F401
from pages.podcast.switch import cluster, cutplan, otio_split, rig, talk as talk_mod, voice_worker
from pages.podcast.switch import resolve_ext as sw_ext
from pages.project_setup import otio_engine as oe

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

FPS = 25.0
BLOCK = 0.1


# ------------------------------------------------------------ made-up mics --

def mic_energy(seconds, talking, level_db=-20.0, bleed_db=-36.0, room_db=-60.0, seed=0):
    """K-weighted energy per 0.1 s block: `level_db` while this mic's person
    talks (spans in seconds), `bleed_db` while someone else does (spans in
    `talking` marked "other"), room tone otherwise."""
    rng = np.random.default_rng(seed)
    n = int(round(seconds / BLOCK))
    db = np.full(n, room_db) + rng.normal(0, 1.0, n)
    for who, a, b in talking:
        lo, hi = int(a / BLOCK), int(b / BLOCK)
        db[lo:hi] = (level_db if who == "own" else bleed_db) + rng.normal(0, 1.5, hi - lo)
    return (10 ** ((db + 0.691) / 10)).astype(np.float32)


def clip(cid, start_s, end_s, offset=0.0):
    return {"id": cid, "start": int(start_s * FPS), "end": int(end_s * FPS), "offset": offset,
            "media": {"key": cid, "path": f"D:/Podcast/{cid}.wav"}, "channels": None, "peaks": cid,
            "enabled": True, "volume": 0.0}


TURNS = [("tess", 0, 10), ("sam", 10, 18), ("tess", 18, 30), ("sam", 30, 40)]


def two_mics():
    """Tess on A1, Sam on A2, each heard 16 dB down on the other's mic."""
    tess = mic_energy(40, [("own" if w == "tess" else "other", a, b) for w, a, b in TURNS], seed=1)
    sam = mic_energy(40, [("own" if w == "sam" else "other", a, b) for w, a, b in TURNS], seed=2)
    audio = {"tess": {"loud": tess, "block": BLOCK}, "sam": {"loud": sam, "block": BLOCK}}
    mics = [{"index": 1, "clips": [clip("tess", 0, 40)]}, {"index": 2, "clips": [clip("sam", 0, 40)]}]
    return mics, (lambda c: audio.get(c["id"]))


class TalkTests(unittest.TestCase):
    def test_each_mic_talks_while_its_own_person_does_and_bleed_doesnt_count(self):
        mics, audio_of = two_mics()
        talk = talk_mod.mics_talking(mics, audio_of, FPS)
        self.assertEqual(talk.order, [1, 2])
        tess, sam = talk.talk
        at = lambda s: int(round(s / BLOCK)) - talk.first  # noqa: E731
        for s in (2, 5, 20, 28):
            self.assertTrue(tess[at(s)], s)
            self.assertFalse(sam[at(s)], s)
        for s in (12, 16, 33, 38):
            self.assertTrue(sam[at(s)], s)
            self.assertFalse(tess[at(s)], s)

    def test_a_clip_not_measured_yet_is_said(self):
        mics, audio_of = two_mics()
        talk = talk_mod.mics_talking(mics, lambda c: None if c["id"] == "sam" else audio_of(c), FPS)
        self.assertEqual(talk.missing, ["sam"])
        self.assertFalse(talk.talk[1].any())

    def test_file_spans_are_seconds_into_each_file(self):
        mics, audio_of = two_mics()
        talk = talk_mod.mics_talking(mics, audio_of, FPS)
        later = dict(mics[1]["clips"][0], offset=100.0)          # the clip starts 100 s into its file
        spans = talk_mod.file_spans(talk, 1, [later], FPS, pad=0.0)["sam"]
        self.assertEqual(len(spans), 2)
        self.assertAlmostEqual(spans[0][0], 110.0, delta=0.5)
        self.assertAlmostEqual(spans[1][1], 140.0, delta=0.5)

    def test_voices_on_a_shared_mic_follow_their_prints(self):
        both = mic_energy(40, [("own", a, b) for _w, a, b in TURNS])
        audio = {"room": {"loud": both, "block": BLOCK}}
        mics = [{"index": 1, "clips": [clip("room", 0, 40)]}]
        talk = talk_mod.mics_talking(mics, lambda c: audio.get(c["id"]), FPS)
        self.assertTrue(talk.talk[0][int(15 / BLOCK)])
        windows, labels = [], []
        for who, a, b in TURNS:
            t = a
            while t + 1.5 <= b:
                windows.append(("room", t, t + 1.5))
                labels.append(0 if who == "tess" else 1)
                t += 0.75
        rows = talk_mod.voices_on_mic(talk, 0, mics[0]["clips"], windows, np.array(labels), FPS)
        self.assertEqual(rows.shape[0], 2)
        at = lambda s: int(round(s / BLOCK)) - talk.first  # noqa: E731
        self.assertTrue(rows[0][at(5)] and not rows[1][at(5)])
        self.assertTrue(rows[1][at(14)] and not rows[0][at(14)])
        self.assertTrue(rows[0][at(25)] and rows[1][at(35)])


class ClusterTests(unittest.TestCase):
    def prints(self, sizes, spread=0.35, seed=3):
        rng = np.random.default_rng(seed)
        centres = rng.normal(size=(len(sizes), 256))
        rows, truth = [], []
        for k, n in enumerate(sizes):
            rows.append(centres[k] + rng.normal(scale=spread * np.linalg.norm(centres[k]) / 16, size=(n, 256)))
            truth += [k] * n
        order = rng.permutation(sum(sizes))
        return np.concatenate(rows)[order].astype(np.float32), np.array(truth)[order]

    def agree(self, labels, truth):
        """Share of prints whose voice matches, under the best naming."""
        best = 0
        for k in set(truth):
            best += max(np.sum((labels == j) & (truth == k)) for j in set(labels))
        return best / len(truth)

    def test_auto_finds_how_many_voices_there_are(self):
        for sizes in ([60, 40], [50, 40, 30]):
            prints, truth = self.prints(sizes)
            labels, counts = cluster.group(prints)
            self.assertEqual(len(counts), len(sizes), sizes)
            self.assertGreater(self.agree(labels, truth), 0.95)
            self.assertEqual(counts, sorted(counts, reverse=True))     # voice 1 talks most

    def test_one_voice_stays_one(self):
        prints, _truth = self.prints([80])
        _labels, counts = cluster.group(prints)
        self.assertEqual(counts, [80])

    def test_a_number_given_is_kept(self):
        prints, truth = self.prints([60, 40, 30])
        labels, counts = cluster.group(prints, 3)
        self.assertEqual(len(counts), 3)
        self.assertGreater(self.agree(labels, truth), 0.95)

    def test_a_cough_doesnt_become_a_person(self):
        prints, _truth = self.prints([100, 80, 2])
        _labels, counts = cluster.group(prints)
        self.assertEqual(len(counts), 2)

    def test_many_prints_are_sampled_not_merged_one_by_one(self):
        prints, truth = self.prints([1500, 1200])
        labels, counts = cluster.group(prints)
        self.assertEqual(len(counts), 2)
        self.assertGreater(self.agree(labels, truth), 0.95)

    def test_no_prints(self):
        labels, counts = cluster.group(np.zeros((0, 256), np.float32))
        self.assertEqual((len(labels), counts), (0, []))


class WorkerMathTests(unittest.TestCase):
    """voice_worker.py's own sums - the venv runs them; numpy here is enough."""

    def test_filterbank_is_kaldis_shape(self):
        feats = voice_worker.fbank(np.random.default_rng(0).normal(scale=0.1, size=16000), np)
        self.assertEqual(feats.shape, (98, 80))           # 1 s: 1 + (16000 - 400) // 160 frames
        self.assertTrue(np.isfinite(feats).all())

    def test_a_tone_lights_its_own_mel_bin(self):
        t = np.arange(16000) / 16000
        low = voice_worker.fbank(0.5 * np.sin(2 * np.pi * 300 * t), np).mean(axis=0)
        high = voice_worker.fbank(0.5 * np.sin(2 * np.pi * 3000 * t), np).mean(axis=0)
        self.assertLess(int(np.argmax(low)), int(np.argmax(high)))

    def test_windows_cover_the_speech(self):
        rate = voice_worker.RATE
        wins = voice_worker.windows([(0, 5 * rate), (6 * rate, int(6.3 * rate)), (8 * rate, 9 * rate)], 10 * rate)
        self.assertEqual(wins[0], (0, int(1.5 * rate)))
        self.assertTrue(all(b - a <= 1.5 * rate for a, b in wins))
        self.assertIn((8 * rate, 9 * rate), wins)                          # a short span is one window
        self.assertFalse(any(a == 6 * rate for a, _b in wins))           # under 0.5 s: none
        self.assertEqual(max(b for a, b in wins if a < 5 * rate), 5 * rate)   # the span's end is heard


# -------------------------------------------------------------- cut plan --

class Talkish:
    first, block = 0, BLOCK

    def __init__(self, n):
        self.length = n


def rows_from(turns, seconds, voices):
    n = int(round(seconds / BLOCK))
    rows = np.zeros((len(voices), n), dtype=bool)
    for who, a, b in turns:
        rows[voices.index(who), int(round(a / BLOCK)):int(round(b / BLOCK))] = True
    return rows, Talkish(n)


def shots_of(plan):
    return [(s.start / FPS, s.end / FPS, s.camera) for s in plan]


def cam(track, role="speaker", of_track=None, **settings):
    """A setup's camera placed on `track` (rig.clean_camera's fields)."""
    return dict(rig.clean_camera(dict(settings, role=role)), track=track, of_track=of_track, audio=[])


SPEAKERS = [cam(1), cam(2)]


class CutPlanTests(unittest.TestCase):
    OFF = {"lead": 0.0, "min_shot": 0.5, "ignore": 0.0}

    def plan(self, rows, t, voice_cams, cams, span, **opts):
        return shots_of(cutplan.plan(rows, voice_cams, cams, t, FPS, span, dict(self.OFF, **opts), seed=7))

    def test_each_voice_gets_its_camera(self):
        rows, t = rows_from(TURNS, 40, ["tess", "sam"])
        self.assertEqual(self.plan(rows, t, [1, 2], SPEAKERS, (0, 1000)),
                         [(0, 10, 1), (10, 18, 2), (18, 30, 1), (30, 40, 2)])

    def test_cuts_come_a_little_early(self):
        rows, t = rows_from(TURNS, 40, ["tess", "sam"])
        self.assertEqual([s[0] for s in self.plan(rows, t, [1, 2], SPEAKERS, (0, 1000), lead=0.2)],
                         [0, 9.8, 17.8, 29.8])

    def test_a_yeah_doesnt_move_the_camera(self):
        rows, t = rows_from(TURNS + [("sam", 5, 5.4)], 40, ["tess", "sam"])
        self.assertEqual(self.plan(rows, t, [1, 2], SPEAKERS, (0, 1000), ignore=1.0)[0], (0, 10, 1))

    def test_crosstalk_goes_wide_or_stays(self):
        rows, t = rows_from([("tess", 0, 10), ("sam", 8, 20)], 20, ["tess", "sam"])
        self.assertEqual(self.plan(rows, t, [1, 2], SPEAKERS + [cam(3, "wide")], (0, 500)),
                         [(0, 8, 1), (8, 10, 3), (10, 20, 2)])
        self.assertEqual(self.plan(rows, t, [1, 2], SPEAKERS + [cam(3, "wide", crosstalk=False)], (0, 500)),
                         [(0, 10, 1), (10, 20, 2)])
        self.assertEqual(self.plan(rows, t, [1, 2], SPEAKERS, (0, 500)), [(0, 10, 1), (10, 20, 2)])

    def test_two_wides_take_turns(self):
        turns = [("tess", 0, 10), ("sam", 8, 20), ("tess", 18, 30)]
        rows, t = rows_from(turns, 30, ["tess", "sam"])
        shots = self.plan(rows, t, [1, 2], SPEAKERS + [cam(3, "wide"), cam(4, "wide")], (0, 750))
        self.assertEqual([c for _a, _b, c in shots], [1, 3, 2, 4, 1])

    def test_no_shot_is_shorter_than_the_least(self):
        rows, t = rows_from([("tess", 0, 10), ("sam", 10, 11), ("tess", 11, 20)], 20, ["tess", "sam"])
        self.assertEqual(self.plan(rows, t, [1, 2], SPEAKERS, (0, 500), min_shot=2.0), [(0, 20, 1)])

    def test_a_long_silence_can_go_wide(self):
        rows, t = rows_from([("tess", 0, 10), ("sam", 15, 20)], 20, ["tess", "sam"])
        self.assertEqual(self.plan(rows, t, [1, 2], SPEAKERS + [cam(3, "wide", quiet=True)], (0, 500)),
                         [(0, 10, 1), (10, 15, 3), (15, 20, 2)])

    def test_cutaways_to_the_wide_in_a_long_shot(self):
        rows, t = rows_from([("tess", 0, 60)], 60, ["tess", "sam"])
        self.assertEqual(self.plan(rows, t, [1, 2], SPEAKERS + [cam(3, "wide", cutaway=20)], (0, 1500)),
                         [(0, 20, 1), (20, 23, 3), (23, 43, 1), (43, 46, 3), (46, 60, 1)])

    def test_the_wide_can_hold_the_start_and_the_end(self):
        rows, t = rows_from([("tess", 0, 60)], 60, ["tess", "sam"])
        self.assertEqual(self.plan(rows, t, [1, 2], SPEAKERS + [cam(3, "wide", ends=5)], (0, 1500)),
                         [(0, 5, 3), (5, 55, 1), (55, 60, 3)])

    def test_a_second_angle_takes_turns_with_its_speaker(self):
        rows, t = rows_from([("tess", 0, 60), ("sam", 60, 70)], 70, ["tess", "sam"])
        shots = self.plan(rows, t, [1, 2], SPEAKERS + [cam(4, "angle", of_track=1, every=12)], (0, 1750))
        self.assertEqual(shots, [(0, 12, 1), (12, 24, 4), (24, 36, 1), (36, 48, 4), (48, 60, 1), (60, 70, 2)])

    def test_an_angle_of_nobody_is_never_used(self):
        rows, t = rows_from([("tess", 0, 60)], 60, ["tess", "sam"])
        self.assertEqual(self.plan(rows, t, [1, 2], SPEAKERS + [cam(4, "angle")], (0, 1500)), [(0, 60, 1)])

    def test_a_listener_reacts_in_a_long_monologue(self):
        rows, t = rows_from([("tess", 0, 60)], 60, ["tess", "sam"])
        sam = cam(2, reaction=True, react_after=20, react_len=3)
        self.assertEqual(self.plan(rows, t, [1, 2], [cam(1), sam], (0, 1500)),
                         [(0, 20, 1), (20, 23, 2), (23, 43, 1), (43, 46, 2), (46, 60, 1)])

    def test_random_cutaways_come_within_their_gaps_and_are_the_same_each_time(self):
        rows, t = rows_from([("tess", 0, 120)], 120, ["tess", "sam"])
        detail = cam(5, "cutaway", gap_min=10, gap_max=15, length=3)
        shots = self.plan(rows, t, [1, 2], SPEAKERS + [detail], (0, 3000))
        self.assertEqual(shots, self.plan(rows, t, [1, 2], SPEAKERS + [detail], (0, 3000)))
        away = [(a, b) for a, b, c in shots if c == 5]
        self.assertGreaterEqual(len(away), 5)
        self.assertTrue(all(abs((b - a) - 3) < 0.05 for a, b in away))
        gaps = [b2[0] - a1[1] for a1, b2 in zip(away, away[1:])]
        self.assertTrue(all(9.9 <= g <= 15.1 for g in gaps), gaps)

    def test_a_voice_with_no_camera_never_takes_the_screen(self):
        rows, t = rows_from(TURNS, 40, ["tess", "sam"])
        self.assertEqual(self.plan(rows, t, [1, None], SPEAKERS, (0, 1000)), [(0, 40, 1)])

    def test_the_plan_covers_the_whole_span(self):
        rows, t = rows_from([("sam", 5, 10)], 12, ["tess", "sam"])
        plan = cutplan.plan(rows, [1, 2], SPEAKERS, t, FPS, (25, 400), self.OFF)
        self.assertEqual((plan[0].start, plan[-1].end), (25, 400))
        self.assertTrue(all(a.end == b.start for a, b in zip(plan, plan[1:])))

    def test_options_are_kept_in_range(self):
        opts = cutplan.options({"min_shot": 999, "lead": "x", "ignore": -1})
        self.assertEqual(opts["min_shot"], 20.0)
        self.assertEqual(opts["lead"], cutplan.DEFAULTS["lead"])
        self.assertEqual(opts["ignore"], 0.0)


# ---------------------------------------------------------------- setups --

VIDEO = [{"index": 1, "name": "Tess cam", "clips": [1]}, {"index": 2, "name": "Sam cam", "clips": [1]},
         {"index": 3, "name": "Wide", "clips": [1]}, {"index": 4, "name": "Video 4", "clips": []}]
MICS = [{"index": 1, "raw_name": "Tess lav", "camera_sound": False},
        {"index": 2, "raw_name": "Sam lav", "camera_sound": False},
        {"index": 3, "raw_name": "", "camera_sound": True}]


class RigTests(unittest.TestCase):
    def test_a_first_setup_has_a_camera_per_track_with_its_mic_linked(self):
        setup = rig.from_timeline(VIDEO, MICS)
        self.assertEqual([(c["name"], c["role"], c["audio_names"]) for c in setup["cameras"]],
                         [("Tess cam", "speaker", ["Tess lav"]), ("Sam cam", "speaker", ["Sam lav"]),
                          ("Wide", "wide", [])])
        where = rig.place(setup, VIDEO, MICS, {})
        self.assertEqual([where[c["id"]] for c in setup["cameras"]],
                         [{"track": 1, "audio": [1]}, {"track": 2, "audio": [2]}, {"track": 3, "audio": []}])

    def test_a_setup_finds_its_tracks_by_name_on_another_timeline(self):
        setup = rig.from_timeline(VIDEO, MICS)
        other = [{"index": 1, "name": "Wide", "clips": [1]}, {"index": 2, "name": "Sam cam", "clips": [1]},
                 {"index": 3, "name": "Tess cam", "clips": [1]}]
        where = rig.place(setup, other, [{"index": 5, "raw_name": "Tess lav"}], {})
        self.assertEqual([where[c["id"]]["track"] for c in setup["cameras"]], [3, 2, 1])
        self.assertEqual(where[setup["cameras"][0]["id"]]["audio"], [5])

    def test_a_timelines_own_choice_wins_and_a_missing_camera_is_off_it(self):
        setup = rig.from_timeline(VIDEO, MICS)
        rig.new_camera(setup, "Detail")
        first, extra = setup["cameras"][0]["id"], setup["cameras"][-1]["id"]
        where = rig.place(setup, VIDEO, MICS, {"tracks": {first: 3}, "audio": {first: []}})
        self.assertEqual(where[first], {"track": 3, "audio": []})
        self.assertIsNone(where[extra]["track"])              # three tracks with clips, four cameras

    def test_everything_saved_is_cleaned(self):
        setups = rig.clean_setups({"s1": {"name": "  Studio ", "cameras": [
            {"id": "a", "name": "A", "role": "nope", "every": 1e9, "reaction": "yes", "of": "a"},
            {"id": "a", "name": "B", "role": "cutaway", "gap_min": 50, "gap_max": 10}]}, "": {}})
        self.assertEqual(list(setups), ["s1"])
        a, b = setups["s1"]["cameras"]
        self.assertEqual((setups["s1"]["name"], a["role"], a["every"], a["reaction"], a["of"]),
                         ("Studio", "speaker", 600.0, False, None))
        self.assertNotEqual(b["id"], "a")
        self.assertEqual((b["gap_min"], b["gap_max"]), (50.0, 50.0))


# ------------------------------------------------------------------ OTIO --

R = 25.0


def _rt(v):
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": R, "value": float(v)}


def otio_clip(name, start, dur, link=None):
    return {"OTIO_SCHEMA": "Clip.2", "name": name,
            "metadata": {"Resolve_OTIO": {"Link Group ID": link}} if link is not None else {},
            "source_range": {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(start), "duration": _rt(dur)},
            "media_references": {"DEFAULT_MEDIA": {"OTIO_SCHEMA": "ExternalReference.1",
                                                   "target_url": f"file:///D:/Footage/{name}.mov"}},
            "active_media_reference_key": "DEFAULT_MEDIA", "enabled": True}


def otio_doc():
    tracks = [
        oe._make_track("Video", "V1", [otio_clip("A001", 100, 1000, link=1)]),
        oe._make_track("Video", "V2", [oe._make_gap(50, R), otio_clip("B001", 0, 950, link=2)]),
        oe._make_track("Video", "V3", [oe._make_gap(200, R), otio_clip("Broll", 10, 100)]),
        oe._make_track("Audio", "A1", [otio_clip("A001", 100, 1000, link=1)]),
    ]
    return {"OTIO_SCHEMA": "Timeline.1", "name": "T", "metadata": {},
            "tracks": {"OTIO_SCHEMA": "Stack.1", "children": tracks}}


def cut(*spans):
    return [cutplan.Shot(a, b, c) for a, b, c in spans]


class OtioSplitTests(unittest.TestCase):
    def test_each_camera_is_razored_at_every_switch_and_nothing_moves(self):
        shots = cut((86400, 86700, 1), (86700, 86900, 2), (86900, 87400, 1))
        doc, pieces, sound, warnings = otio_split.split(otio_doc(), shots, [1, 2], start_frame=86400)
        self.assertEqual((warnings, sound), ([], []))
        v1, v2, v3, a1 = doc["tracks"]["children"]
        self.assertEqual([(c["source_range"]["start_time"]["value"], c["source_range"]["duration"]["value"])
                          for c in v1["children"]], [(100, 300), (400, 200), (600, 500)])
        self.assertEqual([oe._item_duration(c) for c in v2["children"]], [50, 250, 200, 500])
        self.assertEqual(v2["children"][1]["source_range"]["start_time"]["value"], 0)
        self.assertEqual(v2["children"][2]["source_range"]["start_time"]["value"], 250)
        self.assertEqual(v3, otio_doc()["tracks"]["children"][2])          # B-roll as it was
        self.assertEqual(a1, otio_doc()["tracks"]["children"][3])          # and the sound
        self.assertEqual(pieces, [(1, 0, 300, True), (1, 300, 500, False), (1, 500, 1000, True),
                                  (2, 50, 300, False), (2, 300, 500, True), (2, 500, 1000, False)])
        self.assertNotIn("Link Group ID", v1["children"][0]["metadata"]["Resolve_OTIO"])

    def test_a_clip_wholly_inside_one_shot_is_left_whole(self):
        doc, pieces, _s, _w = otio_split.split(otio_doc(), cut((0, 2000, 1)), [1])
        self.assertEqual(doc["tracks"]["children"][0], otio_doc()["tracks"]["children"][0])
        self.assertEqual(pieces, [(1, 0, 1000, True)])

    def test_transitions_on_camera_tracks_go(self):
        doc = otio_doc()
        doc["tracks"]["children"][0]["children"].insert(0, {"OTIO_SCHEMA": "Transition.1", "name": "Dissolve",
                                                            "in_offset": _rt(5), "out_offset": _rt(5)})
        _doc, _p, _s, warnings = otio_split.split(doc, cut((0, 2000, 1)), [1])
        self.assertEqual(len(warnings), 1)

    def test_a_muted_cameras_own_sound_is_cut_the_same_and_stays_linked(self):
        shots = cut((86400, 86700, 1), (86700, 86900, 2), (86900, 87400, 1))
        doc, _pieces, sound, _w = otio_split.split(otio_doc(), shots, [1, 2], 86400, muted=[1])
        v1, _v2, _v3, a1 = doc["tracks"]["children"]
        self.assertEqual([(c["source_range"]["start_time"]["value"], c["source_range"]["duration"]["value"])
                          for c in a1["children"]], [(100, 300), (400, 200), (600, 500)])
        self.assertEqual(sound, [(1, 0, 300), (1, 300, 500), (1, 500, 1000)])
        links = [c["metadata"]["Resolve_OTIO"]["Link Group ID"] for c in v1["children"]]
        self.assertEqual(links, [c["metadata"]["Resolve_OTIO"]["Link Group ID"] for c in a1["children"]])
        self.assertEqual(len(set(links)), 3)
        self.assertNotIn(1, links)                                   # new groups, not the whole clip's

    def test_an_unmuted_cameras_sound_is_left_whole(self):
        shots = cut((86400, 86700, 1), (86700, 87400, 2))
        doc, _p, sound, _w = otio_split.split(otio_doc(), shots, [1, 2], 86400, muted=[2])
        self.assertEqual(sound, [])
        self.assertEqual(doc["tracks"]["children"][3], otio_doc()["tracks"]["children"][3])

    def test_compound_clips_are_refused(self):
        doc = otio_doc()
        doc["tracks"]["children"][0]["children"].append({"OTIO_SCHEMA": "Stack.1", "name": "Nest", "children": []})
        with self.assertRaises(oe.OtioError):
            otio_split.split(doc, cut((0, 2000, 1)), [1])


# ---------------------------------------------------------- fake Resolve --

class Item:
    def __init__(self, start):
        self.start, self.enabled = start, True

    def GetStart(self):
        return self.start

    def SetClipEnabled(self, on):
        self.enabled = on
        return True

    def GetClipEnabled(self):
        return self.enabled


class Timeline:
    def __init__(self, name="Show (Synced)", uid="tl-1", items=None):
        self.name, self.uid, self.items = name, uid, items or {}

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid

    def GetStartFrame(self):
        return 86400

    def GetItemListInTrack(self, kind, index):
        return self.items.get((kind, index), self.items.get(index, []) if kind == "video" else [])


class Project:
    def __init__(self, current):
        self.current = current

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True


class Controller:
    def __init__(self, project):
        self.project = project
        self.resolve = mock.Mock()
        self.resolve.GetProjectManager.return_value.GetCurrentProject.return_value = project

    def current_project(self):
        return self.project


class BuildTests(unittest.TestCase):
    def test_the_new_timeline_has_the_off_pieces_turned_off(self):
        source = Timeline()
        project = Project(source)
        made = Timeline("Show (Synced) (Auto Switch)", "tl-2",
                        {1: [Item(86400), Item(86700), Item(86900)], 2: [Item(86450), Item(86700), Item(86900)]})
        shots = cut((86400, 86700, 1), (86700, 86900, 2), (86900, 87400, 1))
        with mock.patch.object(oe, "read_current_timeline", return_value=(otio_doc(), [], "D:/tmp")), \
                mock.patch.object(oe, "import_rebuilt", return_value=made) as imported:
            result = sw_ext.build(Controller(project), shots, [1, 2], "tl-1")
        self.assertEqual(result["sound_off"], 0)
        self.assertEqual(imported.call_args[0][3:5], ("Show (Synced)", "(Auto Switch)"))
        self.assertEqual([i.enabled for i in made.items[1]], [True, False, True])
        self.assertEqual([i.enabled for i in made.items[2]], [False, True, False])
        self.assertEqual((result["off"], result["failed"]), (3, 0))
        self.assertIs(project.current, made)
        self.assertEqual(source.items, {})                                  # the open timeline: untouched

    def test_a_muted_cameras_sound_pieces_are_turned_off(self):
        made = Timeline("Show (Synced) (Auto Switch)", "tl-2",
                        {1: [Item(86400), Item(86700), Item(86900)], 2: [Item(86450), Item(86700), Item(86900)],
                         ("audio", 1): [Item(86400), Item(86700), Item(86900)]})
        shots = cut((86400, 86700, 1), (86700, 86900, 2), (86900, 87400, 1))
        with mock.patch.object(oe, "read_current_timeline", return_value=(otio_doc(), [], "D:/tmp")), \
                mock.patch.object(oe, "import_rebuilt", return_value=made):
            result = sw_ext.build(Controller(Project(Timeline())), shots, [1, 2], "tl-1", muted=[1])
        self.assertEqual([i.enabled for i in made.items[("audio", 1)]], [False, False, False])
        self.assertEqual((result["sound_off"], result["sound_failed"]), (3, 0))

    def test_another_timeline_open_since_is_refused(self):
        with self.assertRaises(sw_ext.BuildError):
            sw_ext.build(Controller(Project(Timeline(uid="other"))), cut((0, 10, 1)), [1], "tl-1")

    def test_camera_files_are_camera_sound(self):
        self.assertTrue(sw_ext.is_camera_sound("D:/Footage/A001_0001.MXF"))
        self.assertFalse(sw_ext.is_camera_sound("D:/Audio/Tess.wav"))


# ------------------------------------------------------------ voice model --

class VoiceModelTests(unittest.TestCase):
    def test_the_voice_model_is_pinned_and_holds_no_code(self):
        from pages.transcribe import env_setup as es
        entry = es.VOICE_MODELS[0]
        pin = es.model_pins()[entry["id"]]
        self.assertEqual(pin["repo"], entry["repo"])
        self.assertRegex(pin["revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(sorted(pin["files"]), sorted(entry["files"]))
        for name, f in pin["files"].items():
            self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
            self.assertFalse(name.endswith((".py", ".pt", ".pth", ".pkl")), name)
        self.assertEqual(es.model_patterns(entry), entry["files"])

    def test_it_moves_with_the_models_folder(self):
        import tempfile
        from pathlib import Path
        from pages.transcribe import env_setup as es
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / es.VOICE_IDS[0]).mkdir()
            self.assertEqual([p.name for p in es.own_models(Path(tmp))], es.VOICE_IDS)


# ------------------------------------------------------------------ page --

class Mem(dict):
    def save(self):
        pass


class Host:
    connected = True

    def __init__(self):
        self.tools, self.busy = {}, []

    def tool_settings(self, tool_id, defaults=None):
        return self.tools.setdefault(tool_id, Mem(defaults or {}))

    def ensure_connected(self):
        return object()

    def set_busy(self, on, text=None):
        self.busy.append(on)

    def theme_tokens(self):
        return {"surface": "#222222"}


def peaks_result(energy):
    """peaks.decode()'s answer for a one-channel file of these energies."""
    return (np.zeros(len(energy), dtype=np.uint8).tobytes(), energy.astype(np.float32).tobytes(), 1)


def timeline_data():
    mics, _audio_of = two_mics()
    tracks = [{"index": 1, "name": "Tess", "clips": mics[0]["clips"]},
              {"index": 2, "name": "Sam", "clips": mics[1]["clips"]},
              {"index": 3, "name": "Audio 3", "clips": [dict(clip("cam", 0, 40),
                                                             media={"key": "cam", "path": "D:/Footage/A001.MOV"})]}]
    video = [{"index": 1, "name": "Tess cam", "clips": [{"start": 0, "end": 1000, "name": "A001"}]},
             {"index": 2, "name": "Sam cam", "clips": [{"start": 0, "end": 1000, "name": "B001"}]},
             {"index": 3, "name": "Wide", "clips": [{"start": 0, "end": 1000, "name": "C001"}]}]
    return {"id": "tl-1", "name": "Show (Synced)", "fps": FPS, "start": 0, "end": 1000, "tracks": tracks,
            "video": video}


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])

    def make(self):
        from pages.podcast.switch import page as page_mod
        self.host = Host()
        page = page_mod.AutoSwitchPage(self.host)
        self.addCleanup(page.deleteLater)
        self.events = []
        page.emit = lambda name, payload=None: self.events.append((name, payload))
        page._calls.call = lambda name, args, job, done: done(timeline_data(), None) or True
        mics, audio_of = two_mics()
        page._peaks = {"tess": peaks_result(audio_of(mics[0]["clips"][0])["loud"]),
                       "sam": peaks_result(audio_of(mics[1]["clips"][0])["loud"])}
        return page

    def last(self, name):
        found = [p for n, p in self.events if n == name]
        return found[-1] if found else None

    def test_analyse_pins_each_voice_to_its_camera_and_plans_the_cuts(self):
        page = self.make()
        page.on_analyse()
        setup = self.last("setup")
        self.assertEqual([a["label"] for a in setup["audio"]], ["tess.wav · A1", "sam.wav · A2"])  # no camera sound
        self.assertEqual([(c["file"], c["role"], c["track"], c["audio"]) for c in setup["cameras"]],
                         [("A001", "speaker", 1, [1]), ("B001", "speaker", 2, [2]), ("C001", "wide", 3, [])])
        self.assertEqual(setup["setups"], [{"id": "setup1", "name": "My setup"}])
        ids = [c["id"] for c in setup["cameras"]]
        voices = self.last("voices")["voices"]
        self.assertEqual([(v["track"], v["camera"]) for v in voices], [(1, ids[0]), (2, ids[1])])
        plan = self.last("plan")
        self.assertEqual([s["camera"] for s in plan["shots"]], [1, 2, 1, 2])
        self.assertEqual(plan["shots"][0]["start"], 0)
        self.assertEqual(plan["shots"][-1]["end"], 1000)

    def test_changing_a_camera_or_an_option_replans_and_is_kept(self):
        page = self.make()
        page.on_analyse()
        tess = self.last("setup")["cameras"][0]["id"]
        page.on_map({"voice": "2", "camera": tess})
        self.assertEqual([s["camera"] for s in self.last("plan")["shots"]], [1])
        page.on_cut({"key": "min_shot", "value": 15})
        self.assertEqual(self.last("cut")["min_shot"], 15.0)
        self.assertEqual(self.host.tools["podcast_switch"]["options"]["min_shot"], 15.0)
        first = self.host
        again = self.make()
        again.settings = first.tool_settings("podcast_switch")
        again.on_analyse()
        self.assertEqual(again._memory()["cameras"]["2"], tess)

    def test_linking_a_mic_pins_its_voice_and_mutes_the_camera_at_build(self):
        page = self.make()
        page.on_analyse()
        tess, sam, wide = [c["id"] for c in self.last("setup")["cameras"]]
        page.on_cam({"id": tess, "key": "audio", "value": []})
        page.on_cam({"id": sam, "key": "audio", "value": [1]})         # Tess's mic heard on Sam's camera
        self.assertEqual(self.last("setup")["cameras"][1]["audio"], [1])
        self.assertEqual(page._memory()["cameras"]["1"], sam)
        with mock.patch.object(sw_ext, "build", return_value={"name": "X", "off": 0, "failed": 0, "wanted": 0,
                                                               "sound_off": 2, "sound_failed": 0,
                                                               "warnings": []}) as build:
            page._calls.wait_idle = lambda timeout: True
            page.on_build()
        self.assertEqual(build.call_args[0][4], [2])                     # Tess's camera: its own sound
        self.assertIn("own sound", self.last("toast")["text"])

    def test_opening_the_tab_reads_the_timeline(self):
        page = self.make()
        page.on_shown()
        self.assertEqual(self.last("setup")["cameras"][0]["label"], "A001 · V1")
        self.assertEqual(len(self.last("voices")["voices"]), 2)

    def test_a_camera_with_no_one_on_it_is_an_extra_shot_with_a_use(self):
        page = self.make()
        page.on_analyse()
        tess, sam, wide = [c["id"] for c in self.last("setup")["cameras"]]
        page.on_cam({"id": sam, "key": "audio", "value": []})
        page.on_map({"voice": "2", "camera": tess})
        self.assertEqual(self.last("setup")["cameras"][1]["role"], "off")          # nobody on it: not used
        page.on_cam({"id": sam, "key": "role", "value": "angle"})
        page.on_cam({"id": sam, "key": "of", "value": tess})
        cam = self.last("setup")["cameras"][1]
        self.assertEqual((cam["role"], cam["of"]), ("angle", tess))
        page.on_cut({"key": "every", "value": 8})
        self.assertEqual(page._setup()["rules"]["every"], 8.0)
        self.assertEqual(self.last("cut")["every"], 8.0)
        self.assertIn(2, {s["camera"] for s in self.last("plan")["shots"]})        # the angle takes turns
        page.on_map({"voice": "2", "camera": sam})                                  # a voice on it: a person's
        self.assertEqual(self.last("setup")["cameras"][1]["role"], "speaker")

    def test_a_voice_is_named_and_its_camera_remembers_who_it_shows(self):
        page = self.make()
        page.on_analyse()
        page.on_name({"voice": "1", "name": "  Tess  "})
        voices = self.last("voices")
        self.assertEqual(voices["voices"][0]["name"], "Tess")
        self.assertEqual(voices["mics"][0]["label"], "tess.wav · A1")
        self.assertEqual(page._setup()["cameras"][0]["person"], "Tess")
        self.assertEqual(self.last("plan")["rows"][0]["label"], "Tess")

    def test_setups_are_kept_copied_and_deleted(self):
        page = self.make()
        page.on_analyse()
        page.on_setup_copy({"name": "Studio B"})
        self.assertEqual([x["name"] for x in self.last("setup")["setups"]], ["My setup", "Studio B"])
        page.on_setup_pick({"id": "setup1"})
        self.assertEqual(self.last("setup")["setup"], "setup1")
        page.on_setup_delete()
        self.assertEqual([x["name"] for x in self.last("setup")["setups"]], ["Studio B"])
        self.assertEqual(list(self.host.tools["podcast_switch"]["setups"]), ["setup2"])

    def test_an_older_wide_and_its_options_start_the_first_setup(self):
        page = self.make()
        page.settings["options"] = {"min_shot": 2.0, "cutaway": 30, "quiet": True}
        page.settings["timelines"] = {"tl-1": {"mics": {}, "people": {}, "cameras": {"1": 2}, "wide": 1}}
        page._legacy = {"cutaway": 30, "quiet": True}
        page.on_analyse()
        cams = self.last("setup")["cameras"]
        self.assertEqual([c["role"] for c in cams], ["wide", "speaker", "wide"])
        self.assertEqual((page._setup()["rules"]["cutaway"], page._setup()["rules"]["quiet"]), (30.0, True))
        self.assertEqual(page._memory()["cameras"]["1"], cams[1]["id"])          # the track became its camera

    def test_a_shared_mic_without_the_engine_says_what_it_needs(self):
        page = self.make()
        page.on_analyse()
        with mock.patch("pages.podcast.switch.jobs.engine_installed", return_value=False):
            page.on_people({"track": 1, "value": "auto"})
            self.assertTrue(self.last("state")["needs_voices"])
        self.assertEqual(len(self.last("voices")["voices"]), 2)          # still one voice per mic meanwhile

    def test_build_hands_the_plan_to_resolve(self):
        page = self.make()
        page.on_analyse()
        with mock.patch.object(sw_ext, "build", return_value={"name": "Show (Synced) (Auto Switch)", "off": 6,
                                                               "failed": 0, "wanted": 6, "warnings": []}) as build:
            page._calls.wait_idle = lambda timeout: True
            page.on_build()
        shots, cameras, tid, muted = build.call_args[0][1:]
        self.assertEqual(len(shots), 4)
        self.assertEqual(cameras, [1, 2, 3])
        self.assertEqual(tid, "tl-1")
        self.assertEqual(muted, [1, 2])                         # each speaker heard through its own lav
        self.assertEqual(self.host.busy, [True, False])
        self.assertIn("Auto Switch", self.last("toast")["text"])


if __name__ == "__main__":
    unittest.main()
