"""Audit: each check's rules on made-up timelines, the read from (fake)
Resolve, and the page's check / go to / choose flow."""

import os
import time
import unittest
from unittest import mock

import _paths  # noqa: F401
import numpy as np

from pages.audit import audio, checks, resolve_ext

FPS = 24


def clip(start, end, name="clip.mov", **extra):
    return {"name": name, "start": start, "end": end, "enabled": True, "markers": [], **extra}


def video(start, end, name="clip.mov", fps=FPS, res=(1920, 1080), path=None, **extra):
    return clip(start, end, name, type="Video + Audio", fps=fps, res=res, online=True,
                path=path or f"D:/media/{name}", **extra)


def track(kind, index, items, enabled=True, name=""):
    return {"kind": kind, "index": index, "name": name, "enabled": enabled, "items": items}


def scan(*tracks, start=86400, markers=(), width=1920, height=1080, fps=FPS):
    return {"name": "Edit", "id": "tl-1", "fps": fps, "drop": False, "width": width, "height": height,
            "start": start, "end": start + 10000, "markers": list(markers), "tracks": list(tracks)}


def rows(check, data):
    return checks.RUNNERS[check](data)


class CheckTests(unittest.TestCase):
    def test_a_missing_file_is_one_row_though_its_picture_and_sound_are_two_clips(self):
        gone = dict(video(86400, 86500, "A001.mov"), online=False)
        data = scan(track("video", 1, [gone]), track("audio", 1, [dict(gone)]))
        found = rows("offline", data)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["where"], found[0]["detail"], found[0]["tc"]), ("V1", "D:/media/A001.mov", "01:00:00:00"))

    def test_black_before_the_first_clip_and_between_clips(self):
        data = scan(track("video", 1, [video(86403, 86500), video(86548, 86600)]))
        head, middle = rows("black", data)
        self.assertEqual((head["severity"], head["detail"], head["clip"]), (checks.WARNING, "3 frames of black", "Before the first clip"))
        self.assertEqual((middle["severity"], middle["detail"], middle["frame"]), (checks.NOTE, "2.0 s of black", 86500))

    def test_a_clip_on_any_track_above_covers_the_gap_but_not_one_turned_off(self):
        v1 = track("video", 1, [video(86400, 86500), video(86510, 86600)])
        self.assertEqual(rows("black", scan(v1, track("video", 2, [video(86490, 86520)]))), [])
        self.assertEqual(len(rows("black", scan(v1, track("video", 2, [dict(video(86490, 86520), enabled=False)])))), 1)
        self.assertEqual(len(rows("black", scan(v1, track("video", 2, [video(86490, 86520)], enabled=False)))), 1)
        adjustment = clip(86490, 86520, "Adjustment Clip")
        self.assertEqual(len(rows("black", scan(v1, track("video", 2, [adjustment])))), 1)

    def test_one_frame_of_black_is_a_slip(self):
        data = scan(track("video", 1, [video(86400, 86500), video(86501, 86600)]))
        self.assertEqual(rows("black", data)[0]["detail"], "1 frame of black")

    def test_flash_frames(self):
        data = scan(track("video", 1, [video(86400, 86401, "a"), video(86401, 86403, "b"), video(86403, 86406, "c")]))
        self.assertEqual([(r["clip"], r["detail"]) for r in rows("flash", data)], [("a", "1 frame long"), ("b", "2 frames long")])

    def test_tracks_and_clips_turned_off(self):
        data = scan(track("video", 2, [video(86400, 86500), video(86600, 86700)], enabled=False, name="Graphics"),
                    track("audio", 3, [], enabled=False),
                    track("video", 1, [dict(video(86400, 86500, "x"), enabled=False)]))
        off = rows("tracks_off", data)
        self.assertEqual([(r["where"], r["clip"], r["detail"]) for r in off], [("V2", "Graphics", "Turned off – 2 clips on it")])
        self.assertEqual([(r["where"], r["clip"]) for r in rows("clips_off", data)], [("V1", "x")])

    def test_frame_rate_one_row_per_file(self):
        data = scan(track("video", 1, [video(86500, 86600, "a.mp4", fps=29.97), video(86400, 86450, "a.mp4", fps=29.97),
                                       video(86700, 86800, "b.mp4", fps=24.0),
                                       dict(video(86900, 87000, "still.png", fps=24.0), type="Still")]),
                    fps=60)
        found = rows("rate", data)
        self.assertEqual([(r["clip"], r["frame"], r["times"], r["detail"]) for r in found],
                         [("a.mp4", 86400, 2, "29.97 fps on a 60 fps timeline"), ("b.mp4", 86700, 1, "24 fps on a 60 fps timeline")])

    def test_pictures_smaller_than_the_timeline(self):
        data = scan(track("video", 1, [dict(video(86400, 86500, "logo.png"), res=(641, 479), type="Still"),
                                       video(86500, 86600, "wide.mov", res=(3840, 1000))]))
        self.assertEqual([(r["clip"], r["detail"]) for r in rows("low_res", data)],
                         [("logo.png", "641×479 on a 1920×1080 timeline")])

    def test_to_do_markers(self):
        marker = lambda frame, name, note="": {"frame": frame, "color": "Red", "name": name, "note": note}
        data = scan(track("video", 1, [dict(video(86500, 86600), markers=[marker(30, "Marker", "TODO swap shot")])]),
                    markers=[marker(10, "fix colour"), marker(20, "Fixed"), marker(40, "prefix"), marker(50, "Logo??")])
        found = rows("todo", data)
        self.assertEqual([(r["frame"], r["clip"], r["detail"]) for r in found],
                         [(86410, "fix colour", "Timeline marker"), (86450, "Logo??", "Timeline marker"),
                          (86500, "TODO swap shot", "Marker on a clip")])

    def test_fonts_and_styles_not_installed(self):
        title = lambda font, style: video(86400, 86500, "Text+", titles=[{"font": font, "style": style, "text": "Hi"}])
        data = scan(track("video", 1, [title("Open Sans", "SemiBold"), title("OpenSans", "Bold"),
                                       title("Brand Sans", "Regular"), title("Brand Sans", "Bold"), title("Arial", "Fancy")]))
        installed = {"Open Sans": ["Regular", "SemiBold"], "Arial": []}
        found = checks.check_fonts(data, installed)
        self.assertEqual(sorted((r["clip"], r["times"]) for r in found), [("Brand Sans", 2), ("OpenSans Bold", 1)])
        self.assertTrue(all(r["severity"] == checks.PROBLEM and r["detail"] == "Not installed" for r in found))

    def test_titles_left_empty_or_as_they_started(self):
        title = lambda text: video(86400, 86500, "Text+", titles=[{"font": "Arial", "style": "", "text": text}])
        data = scan(track("video", 1, [title("Custom Title"), title("  "), title("Lorem ipsum dolor"), title("Act One")]))
        self.assertEqual([r["detail"] for r in rows("title_text", data)],
                         ["Still says “Custom Title”", "Empty", "Still says “Lorem ipsum dolor”"])

    def test_subtitle_lines_and_reading_speed(self):
        sub = lambda start, end, text: clip(start, end, text)
        data = scan(track("subtitle", 1, [
            sub(86400, 86448, "Short and fine."),
            sub(86448, 86496, "This line is much longer than forty-two characters wide."),
            sub(86496, 86544, "One\nTwo\nThree"),
            sub(86544, 86568, "Too many words for one second here."),
            sub(86568, 86580, "Hi"),
        ]))
        self.assertEqual([r["detail"] for r in rows("sub_length", data)], ["A line of 56 characters", "3 lines"])
        self.assertEqual([r["detail"] for r in rows("sub_speed", data)],
                         ["28 characters a second", "35 characters a second", "On screen for 0.50 s"])
        self.assertEqual(rows("sub_length", data)[1]["clip"], "One / Two / Three")

    def test_run_groups_worst_first_and_skips_what_is_off(self):
        data = scan(track("video", 1, [dict(video(86400, 86401, "a"), online=False), video(86401, 86500, fps=30)]))
        result = checks.run(data, fonts=None, off={"flash"})
        self.assertEqual([g["id"] for g in result["groups"]], ["offline", "rate"])
        self.assertIn("fonts", result["skipped"])
        self.assertIn("flash", result["skipped"])
        self.assertIn("black", result["passed"])
        self.assertEqual(result["counts"], {"problem": 1, "warning": 0, "note": 1})

    def test_a_long_list_is_cut_but_counted(self):
        many = [video(86400 + i * 10, 86401 + i * 10, f"c{i}") for i in range(checks.MAX_ROWS + 5)]
        result = checks.run(scan(track("video", 1, many)), fonts={}, off={"black"})
        flash = next(g for g in result["groups"] if g["id"] == "flash")
        self.assertEqual((flash["count"], len(flash["rows"])), (checks.MAX_ROWS + 5, checks.MAX_ROWS))
        self.assertEqual(result["counts"]["warning"], checks.MAX_ROWS + 5)

    def test_every_check_has_a_runner(self):
        self.assertEqual({c["id"] for c in checks.CHECKS},
                         set(checks.RUNNERS) | checks.NEEDS_FONTS | set(checks.SOUND_RUNNERS))
        self.assertEqual(checks.SOUND, set(checks.SOUND_RUNNERS))


class FixTests(unittest.TestCase):
    """What checks.py offers to fix, and how resolve_ext.fix() is to find it."""

    def test_tracks_and_clips_turned_off_turn_on_one_by_one_or_all_at_once(self):
        off = dict(video(86400, 86500, "a"), enabled=False)
        data = scan(track("video", 1, [off, dict(video(86500, 86600, "b"), enabled=False)]),
                    track("video", 2, [video(86400, 86500)], enabled=False))
        self.assertEqual(rows("tracks_off", data)[0]["fix"], {"do": "track_on", "kind": "video", "index": 2})
        first = rows("clips_off", data)[0]["fix"]
        self.assertEqual(first, {"do": "clip_on", "kind": "video", "index": 1, "start": 86400, "end": 86500, "name": "a"})
        group = next(g for g in checks.run(data)["groups"] if g["id"] == "clips_off")
        self.assertEqual(group["fix_all"]["label"], "Turn all on")
        self.assertEqual(len(group["fix_all"]["fixes"]), 2)
        one = next(g for g in checks.run(data)["groups"] if g["id"] == "tracks_off")
        self.assertIsNone(one["fix_all"])                   # one row: its own button is enough

    def test_a_gap_closes_only_where_nothing_at_all_is_on_the_timeline(self):
        music = scan(track("video", 1, [video(86400, 86500), video(86600, 86700)]),
                     track("audio", 1, [clip(86400, 86700, "music.wav")]))
        self.assertIsNone(rows("black", music)[0]["fix"])
        silent = scan(track("video", 1, [video(86400, 86500), video(86600, 86700)]),
                      track("audio", 1, [clip(86400, 86500, "a.wav")]))
        self.assertEqual(rows("black", silent)[0]["fix"], {"do": "close_gap", "start": 86500, "end": 86600})
        countdown = scan(track("video", 1, [video(86448, 86500)]))
        self.assertIsNone(rows("black", countdown)[0]["fix"])  # before the first clip: left alone

    def test_a_flash_frame_goes_with_its_sound_and_says_what_is_left(self):
        flash = video(86500, 86501, "f.mov", path="D:/f.mov")
        sound = dict(clip(86500, 86501, "f.mov"), path="D:/f.mov")
        alone = scan(track("video", 1, [video(86400, 86500), flash, video(86501, 86600)]), track("audio", 1, [sound]))
        fix = rows("flash", alone)[0]["fix"]
        self.assertEqual((fix["do"], fix["then"], len(fix["sound"])), ("delete_clip", "close", 1))
        music = scan(track("video", 1, [video(86400, 86500), flash, video(86501, 86600)]),
                     track("audio", 1, [sound]), track("audio", 2, [clip(86400, 86600, "music.wav")]))
        self.assertEqual(rows("flash", music)[0]["fix"]["then"], "black")
        under = scan(track("video", 1, [video(86400, 86600)]), track("video", 2, [flash]))
        self.assertEqual(rows("flash", under)[0]["fix"]["then"], "")

    def test_a_to_do_marker_can_be_removed(self):
        data = scan(track("video", 1, [video(86400, 86500, "a", markers=[
            {"frame": 5, "color": "Red", "name": "fix", "note": ""}])]),
            markers=[{"frame": 30, "color": "Blue", "name": "TODO", "note": "grade"}])
        on_timeline, on_clip = rows("todo", data)
        self.assertEqual(on_timeline["fix"], {"do": "delete_marker", "frame": 30, "marker": "TODO", "note": "grade",
                                              "color": "Blue"})
        self.assertEqual(on_clip["fix"]["clip"]["start"], 86400)
        self.assertEqual(on_clip["fix"]["frame"], 5)

    def test_checks_with_nothing_to_do_offer_nothing(self):
        data = scan(track("video", 1, [video(86400, 86500, fps=30)]))
        self.assertIsNone(rows("rate", data)[0]["fix"])


class SoundTests(unittest.TestCase):
    TARGET = audio.TARGET_BY_ID["streaming"]

    def sound(self, lufs=-14.0, peaks=(), clipped=()):
        return {"lufs": lufs, "peak_db": -1.5, "seconds": 60, "peaks": [list(p) for p in peaks], "clipped": list(clipped)}

    def test_clipping_is_one_row_per_place(self):
        data = scan(track("audio", 1, [clip(86400, 87000)]))
        found = checks.check_clipping(data, self.sound(clipped=[10, 11, 12, 200]), self.TARGET)
        self.assertEqual([(r["frame"], r["detail"]) for r in found], [(86410, "Hits full scale"), (86600, "Hits full scale")])
        long = checks.check_clipping(data, self.sound(clipped=list(range(0, 48, 4))), self.TARGET)
        self.assertEqual(long[0]["detail"], "Hits full scale for 1.9 s")
        self.assertTrue(long[0]["own"])

    def test_peaks_over_the_targets_ceiling_but_not_where_it_clips(self):
        data = scan(track("audio", 1, [clip(86400, 87000)]))
        sound = self.sound(peaks=[(5, -1.5), (6, -0.4), (100, -0.2), (300, -2.5)], clipped=[100])
        found = checks.check_peaks(data, sound, self.TARGET)
        self.assertEqual([(r["frame"], r["detail"]) for r in found], [(86406, "Peaks at -0.4 dBTP")])
        atsc = checks.check_peaks(data, sound, audio.TARGET_BY_ID["atsc"])          # ceiling -2
        self.assertEqual(atsc[0]["detail"], "Peaks at -0.4 dBTP")
        self.assertEqual(len(atsc), 1)          # -2.5 is under -2 too; 5 and 6 are one place

    def test_loudness_against_the_target(self):
        data = scan(track("audio", 1, [clip(86400, 87000)]))
        self.assertEqual(checks.check_loudness(data, self.sound(lufs=-14.6), self.TARGET), [])
        quiet = checks.check_loudness(data, self.sound(lufs=-19.2), self.TARGET)[0]
        self.assertEqual((quiet["severity"], quiet["detail"]),
                         ("note", "-19.2 LUFS – 5.2 LU quieter than the -14 LUFS target"))
        loud = checks.check_loudness(data, self.sound(lufs=-20.0), audio.TARGET_BY_ID["ebu"])[0]
        self.assertEqual((loud["severity"], loud["detail"]),
                         ("warning", "-20.0 LUFS – 3.0 LU louder than the -23 LUFS target"))
        silent = checks.check_loudness(data, self.sound(lufs=None), self.TARGET)
        self.assertEqual(silent[0]["detail"], "No sound at all")
        self.assertEqual(checks.check_loudness(scan(), self.sound(lufs=None), self.TARGET), [])

    def test_sound_checks_wait_for_a_measurement(self):
        data = scan(track("audio", 1, [clip(86400, 87000)]))
        before = checks.run(data)
        self.assertTrue(checks.SOUND <= set(before["skipped"]))
        self.assertFalse(checks.SOUND & set(before["passed"]))
        after = checks.run(data, sound=self.sound(clipped=[3]), target=self.TARGET)
        self.assertEqual(after["groups"][0]["id"], "clipping")
        self.assertIn("loudness", after["passed"])


class MeterTests(unittest.TestCase):
    RATE = 48000

    def feed(self, samples, fps=24, chunk=4096):
        meter = audio.Meter(fps)
        for i in range(0, len(samples), chunk):
            meter.add(samples[i:i + chunk], self.RATE)
        return meter.result()

    def test_a_peak_between_samples_is_found(self):
        t = np.arange(self.RATE) / self.RATE
        tone = 0.9 * np.sin(2 * np.pi * 12000 * t + np.pi / 4)      # every sample at 0.64, the wave at 0.9
        result = self.feed(np.stack([tone, tone], 1))
        self.assertAlmostEqual(result["peak_db"], 20 * np.log10(0.9), delta=0.15)
        self.assertGreater(result["peak_db"], 20 * np.log10(np.abs(tone).max()) + 2)

    def test_loudness_of_a_reference_tone(self):
        tone = 0.1 * np.sin(2 * np.pi * 997 * np.arange(self.RATE * 10) / self.RATE)
        result = self.feed(np.stack([tone, tone], 1))
        self.assertAlmostEqual(result["lufs"], -20.0, delta=0.1)       # -20 dBFS in each of two channels
        self.assertEqual(result["peaks"], [])                         # never near the ceiling
        self.assertIsNone(self.feed(np.zeros((self.RATE * 2, 2)))["lufs"])

    def test_clipping_and_where_it_is(self):
        quiet = np.zeros(self.RATE)
        hot = np.clip(1.5 * np.sin(2 * np.pi * 100 * np.arange(self.RATE // 2) / self.RATE), -1, 1)
        result = self.feed(np.concatenate([quiet, hot, quiet]), fps=25, chunk=1000)
        self.assertEqual(result["clipped"][0], 25)                    # one second in
        self.assertEqual(result["clipped"][-1], 37)
        self.assertTrue(all(f >= 25 for f, _db in result["peaks"]))


# ------------------------------------------------------------ fake Resolve --

class FakeTool:
    def __init__(self, inputs):
        self.inputs = inputs

    def GetInput(self, name):
        return self.inputs.get(name)


class FakeComp:
    def __init__(self, tools):
        self.tools = tools

    def GetToolList(self, _selected, kind):
        return {i + 1: t for i, t in enumerate(self.tools)} if kind == "TextPlus" else {}


class FakeMediaPoolItem:
    def __init__(self, uid, props):
        self.uid, self.props, self.reads = uid, props, 0

    def GetUniqueId(self):
        return self.uid

    def GetClipProperty(self):
        self.reads += 1
        return self.props


class FakeItem:
    def __init__(self, name, start, end, mpi=None, comps=(), enabled=True, markers=None):
        self.name, self.start, self.end, self.mpi = name, start, end, mpi
        self.comps, self.enabled, self.markers = list(comps), enabled, markers or {}

    def SetClipEnabled(self, on):
        self.enabled = on
        return True

    def DeleteMarkerAtFrame(self, frame):
        return self.markers.pop(frame, None) is not None

    def GetName(self): return self.name
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetClipEnabled(self): return self.enabled
    def GetMarkers(self): return self.markers
    def GetMediaPoolItem(self): return self.mpi
    def GetFusionCompCount(self): return len(self.comps)
    def GetFusionCompByIndex(self, i): return self.comps[i - 1]


class FakeTimeline:
    def __init__(self, tracks, uid="tl-1"):
        self.tracks, self.uid, self.playhead = tracks, uid, None
        self.markers = {12: {"color": "Red", "name": "TODO", "note": "", "duration": 1}}
        self.locked, self.deleted = set(), []

    def GetIsTrackLocked(self, kind, i): return (kind, i) in self.locked

    def SetTrackEnable(self, kind, i, on):
        self.tracks[kind][i - 1] = (on, self.tracks[kind][i - 1][1])
        return True

    def DeleteClips(self, items, ripple):
        for _on, listed in (t for kind in self.tracks.values() for t in kind):
            listed[:] = [i for i in listed if not any(i is gone for gone in items)]
        self.deleted.append(([i.name for i in items], ripple))
        return True

    def DeleteMarkerAtFrame(self, frame):
        return self.markers.pop(frame, None) is not None

    def GetSetting(self, key):
        return {"timelineFrameRate": "24", "timelineResolutionWidth": "1920", "timelineResolutionHeight": "1080",
                "timelineDropFrameTimecode": "0"}.get(key)

    def GetTrackCount(self, kind): return len(self.tracks.get(kind, []))
    def GetItemListInTrack(self, kind, i): return self.tracks[kind][i - 1][1]
    def GetIsTrackEnabled(self, kind, i): return self.tracks[kind][i - 1][0]
    def GetTrackName(self, kind, i): return f"{kind} {i}"
    def GetName(self): return "Edit"
    def GetUniqueId(self): return self.uid
    def GetStartFrame(self): return 86400
    def GetEndFrame(self): return 87400
    def GetMarkers(self): return dict(self.markers)

    def SetCurrentTimecode(self, tc):
        self.playhead = tc
        return True


class FakeController:
    def __init__(self, timeline):
        self.timeline = timeline

    def current_project(self):
        return mock.Mock(GetCurrentTimeline=lambda: self.timeline)


class ResolveExtTests(unittest.TestCase):
    def setUp(self):
        self.shared = FakeMediaPoolItem("m1", {"Type": "Video + Audio", "File Path": "Z:/nowhere/A001.mov",
                                               "Online Status": "Online", "FPS": "29.97", "Resolution": "3840x2160"})
        title = FakeComp([FakeTool({"Font": "Open Sans", "Style": "SemiBold", "StyledText": "Hello"})])
        self.timeline = FakeTimeline({
            "video": [(True, [FakeItem("A001.mov", 86400, 86500, self.shared), FakeItem("Text+", 86500, 86600, comps=[title])])],
            "audio": [(False, [FakeItem("A001.mov", 86400, 86500, self.shared)])],
            "subtitle": [(True, [FakeItem("Hello there", 86400, 86448)])],
        })

    def test_scan_reads_tracks_clips_titles_and_subtitles(self):
        data = resolve_ext.scan(FakeController(self.timeline))
        self.assertEqual((data["fps"], data["width"], data["start"], data["id"]), (24.0, 1920, 86400, "tl-1"))
        v1, a1, s1 = data["tracks"]
        first = v1["items"][0]
        self.assertEqual((first["fps"], first["res"], first["online"]), (29.97, (3840, 2160), False))   # not on disk
        self.assertEqual(v1["items"][1]["titles"], [{"font": "Open Sans", "style": "SemiBold", "text": "Hello"}])
        self.assertFalse(a1["enabled"])
        self.assertEqual(s1["items"][0]["name"], "Hello there")
        self.assertEqual(data["markers"][0]["frame"], 12)
        self.assertEqual(self.shared.reads, 1)          # one media pool item: its properties read once

    def test_resolves_offline_word_counts_even_with_a_file_there(self):
        self.shared.props.update({"File Path": __file__, "Online Status": "Offline"})
        self.assertFalse(resolve_ext.scan(FakeController(self.timeline))["tracks"][0]["items"][0]["online"])
        self.shared.props["Online Status"] = "Online"
        self.assertTrue(resolve_ext.scan(FakeController(self.timeline))["tracks"][0]["items"][0]["online"])

    def test_goto_moves_the_playhead_on_the_checked_timeline_only(self):
        controller = FakeController(self.timeline)
        self.assertEqual(resolve_ext.goto(controller, 86424, "tl-1"), "01:00:01:00")
        self.assertEqual(self.timeline.playhead, "01:00:01:00")
        self.timeline.uid = "tl-2"
        with self.assertRaises(resolve_ext.ResolveConnectionError):
            resolve_ext.goto(controller, 86400, "tl-1")

    def test_fixes_turn_on_delete_and_remove_on_the_checked_timeline(self):
        controller = FakeController(self.timeline)
        data = resolve_ext.scan(controller)
        flash = {"do": "delete_clip", "kind": "video", "index": 1, "start": 86400, "end": 86500, "name": "A001.mov",
                 "sound": [{"kind": "audio", "index": 1, "start": 86400, "end": 86500, "name": "A001.mov"}],
                 "then": "black"}
        done = resolve_ext.fix(controller, [
            {"do": "track_on", "kind": "audio", "index": 1},
            {"do": "delete_marker", "frame": 12, "marker": "TODO", "note": "", "color": "Red"},
            flash], data["id"])
        self.assertEqual(done, {"done": 3, "failed": []})
        self.assertTrue(self.timeline.tracks["audio"][0][0])
        self.assertEqual(self.timeline.markers, {})
        self.assertEqual(self.timeline.deleted, [(["A001.mov", "A001.mov"], False)])
        again = resolve_ext.fix(controller, [flash], data["id"])          # gone now: said, not guessed
        self.assertEqual(again["done"], 0)
        self.assertIn("changed since the check", again["failed"][0])

    def test_a_clip_on_a_locked_track_is_left_alone(self):
        self.timeline.tracks["video"][0][1][0].enabled = False
        self.timeline.locked.add(("video", 1))
        controller = FakeController(self.timeline)
        result = resolve_ext.fix(controller, [{"do": "clip_on", "kind": "video", "index": 1, "start": 86400,
                                               "end": 86500, "name": "A001.mov"}])
        self.assertEqual(result["done"], 0)
        self.assertIn("V1 is locked", result["failed"][0])
        self.assertFalse(self.timeline.tracks["video"][0][1][0].enabled)

    def test_a_flash_frame_with_nothing_around_it_ripples(self):
        self.timeline.tracks = {"video": [(True, [FakeItem("a", 86400, 86500), FakeItem("f", 86500, 86501),
                                                  FakeItem("b", 86501, 86600)])]}
        fix = {"do": "delete_clip", "kind": "video", "index": 1, "start": 86500, "end": 86501, "name": "f",
               "sound": [], "then": "close"}
        self.assertEqual(resolve_ext.fix(FakeController(self.timeline), [fix])["done"], 1)
        self.assertEqual(self.timeline.deleted, [(["f"], True)])

    def test_fixing_another_timeline_is_refused(self):
        with self.assertRaises(resolve_ext.ResolveConnectionError):
            resolve_ext.fix(FakeController(self.timeline), [{"do": "track_on", "kind": "audio", "index": 1}], "tl-9")
        self.assertFalse(self.timeline.tracks["audio"][0][0])

    def test_a_marker_changed_since_the_check_stays(self):
        seen = {"do": "delete_marker", "frame": 12, "marker": "TODO", "note": "", "color": "Red"}
        for changed in ({"marker": "fix"}, {"note": "grade the sky"}, {"color": "Blue"}):
            with self.subTest(changed):
                result = resolve_ext.fix(FakeController(self.timeline), [{**seen, **changed}])
                self.assertEqual(result["done"], 0)
                self.assertIn("changed since the check", result["failed"][0])
                self.assertIn(12, self.timeline.markers)

    def test_no_timeline_is_said_plainly(self):
        with self.assertRaises(resolve_ext.ResolveConnectionError) as caught:
            resolve_ext.scan(FakeController(None))
        self.assertIn("No timeline", str(caught.exception))


# --------------------------------------------------------------------- page --

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete
    from pages.audit.page import AuditPage
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class Settings(dict):
    saved = 0

    def save(self):
        self.saved += 1


class Host:
    def __init__(self, connected=True):
        self.connected = connected
        self.controller = object()
        self.settings = None
        self.switched = []

    def tool_settings(self, _tool, defaults):
        self.settings = Settings(defaults)
        return self.settings

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")

    def ensure_connected(self):
        if not self.connected:
            raise resolve_ext.ResolveConnectionError("Could not reach DaVinci Resolve.")
        return self.controller

    def switch_tool(self, tool_id):
        self.switched.append(tool_id)

    def refresh_badges(self, *_a, **_k):
        pass


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageTests(unittest.TestCase):
    def setUp(self):
        if QCoreApplication.instance() is not None and not isinstance(QCoreApplication.instance(), QApplication):
            # An earlier test file made a plain QCoreApplication (to decode): a page's
            # widgets would crash the run on it. Run this file on its own.
            self.skipTest("needs a QApplication - another test made a QCoreApplication first")
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self.data = scan(track("video", 1, [dict(video(86400, 86500, "gone.mov"), online=False), video(86500, 86501, "f")]))
        patch = mock.patch.object(resolve_ext, "scan", side_effect=lambda _c: self.data)
        patch.start()
        self.addCleanup(patch.stop)
        self.host = Host()
        self.page = AuditPage(self.host)
        self.addCleanup(delete, self.page)
        self.addCleanup(self.page._calls.wait_idle, 5)      # before the delete: no thread left running
        self.states, self.toasts = [], []
        self.page.emit = lambda name, value=None: (self.states if name == "state" else self.toasts).append(value)

    def settle(self):
        deadline = time.monotonic() + 5
        while self.page.checking and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.page.checking)

    def test_showing_the_page_checks_once_when_connected(self):
        self.page.on_shown()
        self.settle()
        state = self.states[-1]
        self.assertTrue(state["done"])
        self.assertEqual([g["id"] for g in state["groups"]], ["offline", "flash"])
        self.assertEqual(state["groups"][0]["fix"], {"tool_id": "media_relink", "label": "Open Media Relink"})
        self.assertEqual(state["counts"]["problem"], 1)
        self.assertTrue(state["fonts_unchecked"])        # offscreen Qt can't see the system's fonts
        self.page.on_shown()                             # shown again: the result stands
        self.assertFalse(self.page.checking)
        self.assertEqual(resolve_ext.scan.call_count, 1)

    def test_not_connected_says_so_and_does_not_check_on_show(self):
        self.host.connected = False
        self.page.on_shown()
        self.assertEqual(resolve_ext.scan.call_count, 0)
        self.page.on_check()
        self.assertEqual(self.states[-1]["problem"], "Could not reach DaVinci Resolve.")
        self.assertFalse(self.states[-1]["done"])

    def test_turning_a_check_off_reruns_on_the_last_read(self):
        self.page.on_check()
        self.settle()
        self.page.on_toggle({"id": "flash", "on": False})
        self.assertEqual(self.host.settings["off"], ["flash"])
        self.assertEqual(self.host.settings.saved, 1)
        state = self.states[-1]
        self.assertEqual([g["id"] for g in state["groups"]], ["offline"])
        self.assertFalse(next(c for c in state["checks"] if c["id"] == "flash")["on"])
        self.assertEqual(resolve_ext.scan.call_count, 1)  # no second read
        self.page.on_toggle({"id": "nonsense", "on": False})
        self.assertEqual(self.host.settings["off"], ["flash"])

    def test_go_to_and_the_fix_button(self):
        self.page.on_check()
        self.settle()
        with mock.patch.object(resolve_ext, "goto", return_value="01:00:00:00") as goto:
            self.page.on_goto({"frame": 86400})
            deadline = time.monotonic() + 5
            while not goto.called and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)
        goto.assert_called_once_with(self.host.controller, 86400, "tl-1")
        self.page.on_open_tool({"tool_id": "media_relink"})
        self.page.on_open_tool({"tool_id": "games"})       # only the fixes' own tools
        self.assertEqual(self.host.switched, ["media_relink"])

    def wait_for(self, done):
        deadline = time.monotonic() + 5
        while not done() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        self.assertTrue(done())

    def test_a_fix_is_made_then_the_timeline_is_checked_again(self):
        self.page.on_check()
        self.settle()
        flash = next(g for g in self.states[-1]["groups"] if g["id"] == "flash")["rows"][0]["fix"]
        self.page.sound = {"timeline_id": "tl-1", "result": {}, "checked_at": 0}
        with mock.patch.object(resolve_ext, "fix", return_value={"done": 1, "failed": []}) as fix:
            self.page.on_fix({"fixes": [flash, {"do": "format_disk"}]})
            self.assertTrue(self.states[-1]["fixing"])
            self.wait_for(lambda: fix.called and not self.page.fixing)
        fix.assert_called_once_with(self.host.controller, [flash], "tl-1")     # only fixes Audit knows
        self.assertEqual(self.toasts[-1], {"text": "Fixed – checking again"})
        self.assertIsNone(self.page.sound)                 # the mix may have changed
        self.settle()
        self.assertEqual(resolve_ext.scan.call_count, 2)

    def test_a_fix_that_could_not_be_made_says_why(self):
        self.page.on_check()
        self.settle()
        with mock.patch.object(resolve_ext, "fix", return_value={"done": 0, "failed": ["V1 is locked in Resolve – unlock it first."]}):
            self.page.on_fix({"fixes": [{"do": "track_on", "kind": "video", "index": 1}]})
            self.wait_for(lambda: not self.page.fixing)
        self.assertEqual(self.toasts[-1], {"text": "Couldn't fix it: V1 is locked in Resolve – unlock it first."})

    def test_check_sound_renders_measures_and_lists_what_it_found(self):
        self.page.on_check()
        self.settle()
        measured = {"lufs": -20.0, "peak_db": 0.0, "seconds": 4.0, "peaks": [[3, 0.0]], "clipped": [3]}
        renders = []
        render = mock.Mock(side_effect=lambda folder, **kw: renders.append(kw) or os.path.join(folder, "mix.mov"))
        timeline = mock.Mock(GetUniqueId=lambda: "tl-1")
        self.host.controller = mock.Mock(current_project=lambda: mock.Mock(GetCurrentTimeline=lambda: timeline))
        with mock.patch("pages.transcribe.resolve_ext.TranscribeController") as controller, \
                mock.patch.object(audio, "measure", return_value=(measured, None)), \
                mock.patch.object(audio, "remove") as remove:
            controller.return_value.render_timeline_audio = render
            self.page.on_check_sound()
            self.assertTrue(self.states[-1]["sound"]["running"])
            self.wait_for(lambda: self.page.sound_job is None)
        self.assertEqual(renders[0]["prefix"], "buddy_audit")
        remove.assert_called_once()                         # the render doesn't stay
        state = self.states[-1]
        self.assertEqual((state["sound"]["lufs"], state["sound"]["done"]), (-20.0, True))
        self.assertEqual([g["id"] for g in state["groups"]][:2], ["offline", "clipping"])   # problems first
        self.assertIn("loudness", [g["id"] for g in state["groups"]])   # -20 against -14: a note
        self.page.on_target({"id": "ebu"})                  # -20 against -23: off by 3 too, now a warning
        loud = next(g for g in self.states[-1]["groups"] if g["id"] == "loudness")
        self.assertEqual(loud["severity"], "warning")
        self.assertEqual(self.host.settings["target"], "ebu")

    def test_a_sound_check_of_another_timeline_is_refused(self):
        self.page.on_check()
        self.settle()
        timeline = mock.Mock(GetUniqueId=lambda: "tl-2")
        self.host.controller = mock.Mock(current_project=lambda: mock.Mock(GetCurrentTimeline=lambda: timeline))
        with mock.patch("pages.transcribe.resolve_ext.TranscribeController") as controller:
            self.page.on_check_sound()
            self.wait_for(lambda: self.page.sound_job is None)
        controller.assert_not_called()
        self.assertIn("different timeline", self.states[-1]["sound"]["error"])


if __name__ == "__main__":
    unittest.main()
