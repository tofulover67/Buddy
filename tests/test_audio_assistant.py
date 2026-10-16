"""Audio Assistant's Timeline tab: what resolve_ext.py reads from and writes to a
fake Resolve timeline (never a real project), what levels.py asks of it for each
control, the child process, how peaks.py folds decoded audio into the waveform
bytes and loudness the page draws, and keys.py's volume keys - the curve, and the
timeline files they go to and come from Resolve in. And the page's tabs: Timeline
and Sound Design, in Buddy's Chromium engine."""

import json
import sys
import time
import unittest

import _paths

try:
    import numpy as np
    from pages.audio_assistant import keys as K
    from pages.audio_assistant import peaks, resolve_ext
    HAVE_DEPS = True
except ImportError:  # pragma: no cover - numpy or PySide6 missing
    HAVE_DEPS = False


def qt_app():
    """The process's Qt application, made if there's none yet: a QApplication set up
    for web views, as TabTests' needs one and a bare QCoreApplication made first by
    another test can't be swapped for one."""
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    if QCoreApplication.instance() is None:
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        qt_app.made = QApplication([])
    return QCoreApplication.instance()


# ------------------------------------------------------------ fake Resolve --

class MediaPoolItem:
    def __init__(self, uid, path, fps="24.0", rate="48000", channels="2", codec="Linear PCM", name="", comments=""):
        self.uid, self.calls, self.name, self.comments = uid, 0, name, comments
        self.props = {"File Path": path, "FPS": fps, "Sample Rate": rate, "Audio Ch": channels, "Audio Codec": codec}

    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return self.name

    def GetMetadata(self, key):
        return self.comments if key == "Comments" else ""

    def GetClipProperty(self):
        self.calls += 1
        return dict(self.props)


class Item:
    def __init__(self, uid, name, start, end, mpi=None, source_start=0, volume=0.0, enabled=True, color="",
                 fades=(0.0, 0.0), isolation=None):
        self.uid, self.name, self.start, self.end, self.mpi = uid, name, start, end, mpi
        self.source_start, self.enabled, self.color, self.fades = source_start, enabled, color, fades
        self.props = {"AudioVolumeEnabled": True, "AudioVolume": volume, "AudioPan": 0.0}
        if isolation is not None:
            self.props.update({"AudioVoiceIsolationEnabled": isolation[0], "AudioVoiceIsolationAmount": isolation[1]})

    def GetUniqueId(self): return self.uid
    def GetName(self): return self.name
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetClipEnabled(self): return self.enabled
    def GetClipColor(self): return self.color
    def GetSourceStartFrame(self): return self.source_start
    def GetMediaPoolItem(self): return self.mpi
    def GetProperties(self): return dict(self.props)
    def GetFades(self): return {"FadeIn": self.fades[0], "FadeOut": self.fades[1]}

    def SetProperties(self, props):
        # Resolve's rule: one key it won't take (or a clip without the feature) and nothing changes.
        if any(key not in self.props for key in props):
            return False
        self.props.update(props)
        return True

    def SetFades(self, fades):
        if fades["FadeIn"] + fades["FadeOut"] > self.end - self.start:
            return False
        self.fades = (float(fades["FadeIn"]), float(fades["FadeOut"]))
        return True

    def AddTransition(self, options):
        self.transition = options
        return object() if self.name != "no handles" else None


class Timeline:
    def __init__(self, tracks, selected=(), timecode="01:00:10:00", markers=None, name="Edit 1", fcp7=None):
        self.tracks, self.selected, self.timecode = tracks, list(selected), timecode
        self.markers = markers or {}
        self.moved_to = None
        self.name, self.fcp7 = name, fcp7          # fcp7: what Export(EXPORT_FCP_7_XML) writes

    def GetUniqueId(self): return "tl-" + self.name
    def GetName(self): return self.name

    def Export(self, path, kind, subtype):
        if self.fcp7 is None or kind != Resolve.EXPORT_FCP_7_XML:
            return False
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.fcp7)
        return True
    def GetSetting(self, key): return {"timelineFrameRate": "24", "timelineDropFrameTimecode": "0"}.get(key)
    def GetStartFrame(self): return 86400
    def GetEndFrame(self): return 86400 + 24 * 60
    def GetTrackCount(self, kind): return len(self.tracks) if kind == "audio" else 0
    def GetItemListInTrack(self, kind, i): return self.tracks[i - 1]["items"]
    def GetTrackName(self, kind, i): return self.tracks[i - 1]["name"]
    def GetTrackSubType(self, kind, i): return self.tracks[i - 1].get("kind", "stereo")
    def GetIsTrackEnabled(self, kind, i): return self.tracks[i - 1].get("enabled", True)
    def GetIsTrackLocked(self, kind, i): return False
    def GetMarkers(self): return self.markers
    def GetCurrentTimecode(self): return self.timecode
    def GetSelectedClips(self): return self.selected

    def SetCurrentTimecode(self, tc):
        self.moved_to = tc
        return True

    def NormalizeAudioLevel(self, items, options):
        self.normalized = options
        for item in items:
            item.props["AudioVolume"] = options.get("targetLoudness", -1.0) + 10
        return True


class Resolve:
    NORMALIZE_AUDIO_SET_LEVEL_RELATIVE = 0.0
    NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT = 1.0
    EXPORT_FCP_7_XML = 3
    EXPORT_NONE = 0

    def GetProductName(self):
        return "DaVinci Resolve Studio"


class Controller:
    def __init__(self, timeline, others=()):
        self.timeline, self.others = timeline, list(others)
        self.resolve = Resolve()

    def current_project(self):
        tl, all_ = self.timeline, [self.timeline] + self.others

        class Project:
            def GetCurrentTimeline(self):
                return tl

            def GetTimelineCount(self):
                return len(all_)

            def GetTimelineByIndex(self, i):
                return all_[i - 1]
        return Project()


def fcp7(*clips, rate=24):
    """A Resolve-style FCP 7 XML export: clips are (name, start, end, in, [(when, gain, eased)])."""
    items = []
    for name, start, end, in_, frames in clips:
        keys = "".join(f"<keyframe><when>{w}</when><value>{g}</value>"
                       + ("<inbez><horiz>1</horiz><vert>1</vert></inbez>" if e else "") + "</keyframe>"
                       for w, g, e in frames)
        items.append(f"""<clipitem id="{name} 0"><name>{name}</name><rate><timebase>{rate}</timebase><ntsc>FALSE</ntsc></rate>
            <start>{start}</start><end>{end}</end><in>{in_}</in><out>{in_ + end - start}</out>
            <filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid><parameter><name>Level</name>
            <value>1</value>{keys}</parameter></effect></filter></clipitem>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE xmeml><xmeml version="5"><sequence>
        <rate><timebase>{rate}</timebase><ntsc>FALSE</ntsc></rate><media><video><track/></video>
        <audio><track>{''.join(items)}</track></audio></media></sequence></xmeml>"""


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class ReadTimelineTests(unittest.TestCase):
    def setUp(self):
        self.camera = MediaPoolItem("m1", r"D:\shoot\C001.MP4", fps="29.97")
        self.mic = MediaPoolItem("m2", r"D:\shoot\mic.wav")
        self.a = Item("c1", "C001.MP4", 86400, 86640, self.camera, source_start=299.7, enabled=False)
        self.b = Item("c2", "mic.wav", 86424, 86800, self.mic, volume=22.0, color="Teal", fades=(12.0, 6.0),
                      isolation=(True, 60.0))
        self.c = Item("c3", "mic.wav", 86900, 87000, self.mic, source_start=48)
        self.timeline = Timeline([
            {"name": "Camera", "items": [self.a], "kind": "adaptive16", "enabled": False},
            {"name": "Mic", "items": [self.b, self.c]},
        ], selected=[self.b], markers={"48": {"color": "Blue", "name": "Intro"}})
        self.cache = {}
        self.data, self.items = resolve_ext.read_timeline(Controller(self.timeline), self.cache)

    def test_tracks_and_clips_are_read_in_order(self):
        tracks = self.data["tracks"]
        self.assertEqual([t["name"] for t in tracks], ["Camera", "Mic"])
        self.assertEqual(tracks[0]["kind"], "adaptive16")
        self.assertFalse(tracks[0]["enabled"])
        self.assertEqual([c["id"] for c in tracks[1]["clips"]], ["c2", "c3"])
        self.assertEqual(set(self.items), {"c1", "c2", "c3"})
        self.assertEqual((self.data["start"], self.data["end"], self.data["fps"]), (86400, 87840, 24.0))

    def test_a_clip_carries_its_levels_and_colour(self):
        clip = self.data["tracks"][1]["clips"][0]
        self.assertEqual(clip["volume"], 22.0)
        self.assertEqual((clip["fade_in"], clip["fade_out"]), (12.0, 6.0))
        self.assertEqual(clip["color"], resolve_ext.CLIP_COLORS["Teal"])
        self.assertEqual(clip["isolation"], {"on": True, "amount": 60.0})
        self.assertFalse(self.data["tracks"][0]["clips"][0]["enabled"])

    def test_isolation_is_none_where_resolve_has_none(self):
        # A camera MXF's clip reads None for it: the page says "Not available".
        self.assertIsNone(self.data["tracks"][0]["clips"][0]["isolation"])

    def test_offset_is_the_source_frame_in_the_medias_own_rate(self):
        camera = self.data["tracks"][0]["clips"][0]
        self.assertAlmostEqual(camera["offset"], 10.0)       # 299.7 frames at 29.97
        self.assertAlmostEqual(self.data["tracks"][1]["clips"][1]["offset"], 2.0)   # 48 at 24

    def test_a_file_is_described_once_and_shares_its_key(self):
        b, c = self.data["tracks"][1]["clips"]
        self.assertEqual(self.mic.calls, 1)
        self.assertEqual(b["media"]["key"], c["media"]["key"])
        self.assertEqual((b["media"]["rate"], b["media"]["channels"]), (48000.0, 2))
        resolve_ext.read_timeline(Controller(self.timeline), self.cache)
        self.assertEqual(self.mic.calls, 1)

    def test_markers_are_absolute_frames(self):
        self.assertEqual(self.data["markers"][0]["frame"], 86448)
        self.assertEqual(self.data["markers"][0]["name"], "Intro")

    def test_live_read(self):
        self.b.props["AudioVolume"] = -3.0
        live = resolve_ext.read_live(Controller(self.timeline), 24.0, {"c2": self.items["c2"]})
        self.assertEqual(live["playhead"], 86400 + 240)
        self.assertEqual(live["resolve_selection"], ["c2"])
        self.assertEqual(live["levels"]["c2"]["volume"], -3.0)

    def test_the_reader_process_keeps_the_items_for_the_live_read(self):
        # As in core/resolve_reader.py's process: plain data out, the items kept there.
        import json
        data = resolve_ext.read_timeline_here(Controller(self.timeline))
        self.assertEqual(json.loads(json.dumps(data)), data)
        self.b.props["AudioVolume"] = -6.0
        live = resolve_ext.read_live_here(Controller(self.timeline), 24.0, ["c2", "gone"])
        self.assertEqual(list(live["levels"]), ["c2"])
        self.assertEqual(live["levels"]["c2"]["volume"], -6.0)
        calls = self.mic.calls
        resolve_ext.read_timeline_here(Controller(self.timeline), fresh=True)    # Refresh: files read again
        self.assertEqual(self.mic.calls, calls + 1)

    def test_no_timeline_is_said(self):
        with self.assertRaises(resolve_ext.ResolveConnectionError):
            resolve_ext.read_timeline(Controller(None), {})

    def test_the_pictures_cuts_and_end_are_read(self):
        # Every video clip's first frame, on any track, is a cut (the timeline's start isn't);
        # the picture ends where its last clip does. Markers keep how long they are.
        video = [[Item("v1", "A001.mov", 86400, 86500), Item("v2", "A002.mov", 86500, 86620)],
                 [Item("v3", "Title", 86550, 86580)]]

        class WithPicture(Timeline):
            def GetTrackCount(self, kind):
                return len(video) if kind == "video" else super().GetTrackCount(kind)

            def GetItemListInTrack(self, kind, i):
                return video[i - 1] if kind == "video" else super().GetItemListInTrack(kind, i)

        tl = WithPicture(self.timeline.tracks, markers={48: {"color": "Red", "name": "Hit", "duration": 12}})
        data, _ = resolve_ext.read_timeline(Controller(tl), {})
        self.assertEqual((data["cuts"], data["video_end"]), ([86500, 86550], 86620))
        self.assertEqual([(m["frame"], m["name"], m["duration"]) for m in data["markers"]], [(86448, "Hit", 12)])

    def test_a_crossfade_is_read_as_one_not_a_clip(self):
        self.timeline.tracks[1]["items"].append(Item("x1", "Cross Fade +3 dB", 86790, 86810))
        data, _items = resolve_ext.read_timeline(Controller(self.timeline), {})
        fade = data["tracks"][1]["clips"][-1]
        self.assertTrue(fade["transition"])
        self.assertNotIn("media", fade)
        self.assertTrue(data["studio"])


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class ReadKeysTests(unittest.TestCase):
    """Keys on the timeline: a clip keyed in Resolve, and a Buddy curve clip."""

    def test_a_clip_keyed_in_resolve_reads_its_keys_in_file_time(self):
        mic = MediaPoolItem("m2", r"D:\shoot\mic.wav")
        clip = Item("c2", "mic.wav", 86424, 86800, mic, source_start=48)       # 2 s into the file
        # Keys at source frames 72 and 96 (3 s and 4 s), the second eased; 0.1 is -20 dB.
        tl = Timeline([{"name": "Mic", "items": [clip]}],
                      fcp7=fcp7(("mic.wav", 24, 400, 48, [(72, 1, False), (96, 0.1, True)])))
        data, _ = resolve_ext.read_timeline(Controller(tl), {})
        read = data["tracks"][0]["clips"][0]
        self.assertEqual(read["keys"], [{"t": 3.0, "db": 0.0, "ease": False}, {"t": 4.0, "db": -20.0, "ease": True}])
        self.assertIsNone(read["curve"])

    def test_no_export_means_no_keys(self):
        mic = MediaPoolItem("m2", r"D:\shoot\mic.wav")
        data, _ = resolve_ext.read_timeline(Controller(Timeline([{"name": "Mic", "items": [
            Item("c2", "mic.wav", 86424, 86800, mic)]}])), {})
        self.assertIsNone(data["tracks"][0]["clips"][0]["keys"])

    def test_a_buddy_curve_clip_takes_its_files_waveform_and_buddys_keys(self):
        mic = MediaPoolItem("m2", r"D:\shoot\mic.wav")
        ours = [{"t": 2.0, "db": 0.0, "ease": False}, {"t": 3.0, "db": -12.0, "ease": True}]
        inner = Item("i1", "mic.wav", 86400, 86776, mic, source_start=24)     # 1 s into the file
        # Resolve holds the expanded keys (as Buddy wrote them), in source frames.
        written = K.expand(ours, 24.0, 1.0)
        nested = Timeline([{"name": "A1", "items": [inner]}], name="mic.wav (Buddy curve)",
                          fcp7=fcp7(("mic.wav", 0, 376, 24, [(round(k["t"] * 24), 10 ** (k["db"] / 20), False)
                                                           for k in written])))
        tl_mpi = MediaPoolItem("t1", "", name="mic.wav (Buddy curve)", comments=K.note(ours, "c2"))
        outer = Item("n1", "mic.wav (Buddy curve)", 86424, 86800, tl_mpi, volume=6.0)
        tl = Timeline([{"name": "A2", "items": [outer]}])
        data, _ = resolve_ext.read_timeline(Controller(tl, [nested]), {})
        clip = data["tracks"][0]["clips"][0]
        self.assertEqual(clip["curve"], {"original": "c2", "edited": False})
        self.assertEqual(clip["keys"], ours)                        # Buddy's own, eases and all
        self.assertAlmostEqual(clip["offset"], 1.0)
        self.assertEqual(clip["media"]["path"], r"D:\shoot\mic.wav")
        # Changed in Resolve since: its keys are what's shown.
        nested.fcp7 = fcp7(("mic.wav", 0, 376, 24, [(48, 1, False), (60, 0.5, False)]))
        clip = resolve_ext.read_timeline(Controller(tl, [nested]), {})[0]["tracks"][0]["clips"][0]
        self.assertTrue(clip["curve"]["edited"])
        self.assertEqual([k["t"] for k in clip["keys"]], [2.0, 2.5])

    def test_a_nested_timeline_that_isnt_buddys_is_left_alone(self):
        other = MediaPoolItem("t2", "", name="Intro music", comments="")
        data, _ = resolve_ext.read_timeline(Controller(Timeline([{"name": "A1", "items": [
            Item("n2", "Intro music", 86400, 86500, other)]}])), {})
        clip = data["tracks"][0]["clips"][0]
        self.assertEqual((clip["media"], clip["keys"], clip["curve"]), (None, None, None))


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class KeysTests(unittest.TestCase):
    def test_straight_in_db_between_keys_and_flat_outside(self):
        keys = [{"t": 1.0, "db": 0.0, "ease": False}, {"t": 3.0, "db": -20.0, "ease": False}]
        self.assertEqual(list(K.curve_db(keys, [0.0, 1.0, 2.0, 2.5, 3.0, 9.0])), [0.0, 0.0, -10.0, -15.0, -20.0, -20.0])
        self.assertEqual(float(K.curve_db(keys[:1], 5.0)), 0.0)
        self.assertEqual(float(K.curve_db([], 5.0)), 0.0)

    def test_an_eased_key_is_flat_into_it(self):
        keys = [{"t": 0.0, "db": 0.0, "ease": False}, {"t": 1.0, "db": -20.0, "ease": True}]
        near = K.curve_db(keys, [0.98, 0.99, 1.0])
        self.assertLess(abs(near[0] - near[2]), 0.05)                # hardly moving as it arrives
        self.assertLess(K.curve_db(keys, 0.5), -10.0)                 # so ahead of the straight line mid-way

    def test_ease_in_flattens_arriving_and_ease_out_leaving(self):
        arrive = [{"t": 0.0, "db": 0.0, "ease": False}, {"t": 1.0, "db": -20.0, "ease": "in"}]
        leave = [{"t": 0.0, "db": 0.0, "ease": "out"}, {"t": 1.0, "db": -20.0, "ease": False}]
        a = K.curve_db(arrive, [0.0, 0.01, 0.99, 1.0])
        self.assertLess(abs(a[2] - a[3]), 0.05)                       # flat into the eased key...
        self.assertGreater(abs(a[1] - a[0]), 0.15)                    # ...not out of the linear one (0.2: its slope)
        b = K.curve_db(leave, [0.0, 0.01, 0.99, 1.0])
        self.assertLess(abs(b[1] - b[0]), 0.05)
        self.assertGreater(abs(b[3] - b[2]), 0.15)
        # Mirror images, and "in and out" (True) is both at once.
        self.assertAlmostEqual(float(K.curve_db(arrive, 0.3)), -20.0 - float(K.curve_db(leave, 0.7)), places=6)
        both = [{"t": 0.0, "db": 0.0, "ease": True}, {"t": 1.0, "db": -20.0, "ease": True}]
        self.assertAlmostEqual(float(K.curve_db(both, 0.5)), -10.0, places=6)

    def test_an_ease_only_bends_its_own_side(self):
        # A key easing in bends the stretch before it, not the one after.
        keys = [{"t": 0.0, "db": 0.0}, {"t": 1.0, "db": -10.0, "ease": "in"}, {"t": 2.0, "db": -20.0}]
        self.assertAlmostEqual(float(K.curve_db(keys, 1.5)), -15.0, places=6)
        self.assertNotAlmostEqual(float(K.curve_db(keys, 0.5)), -5.0, places=2)
        straight_after = K.expand(keys, 24.0)
        self.assertEqual([k["t"] for k in straight_after if k["t"] > 1.0], [2.0])   # nothing added after it
        self.assertGreater(len([k for k in straight_after if 0.0 < k["t"] < 1.0]), 2)

    def test_eases_are_cleaned_and_old_curves_read_as_before(self):
        got = K.clean([{"t": 0, "db": 0, "ease": True}, {"t": 1, "db": 0, "ease": "both"}, {"t": 2, "db": 0, "ease": "in"},
                       {"t": 3, "db": 0, "ease": "out"}, {"t": 4, "db": 0, "ease": "sideways"}, {"t": 5, "db": 0}])
        self.assertEqual([k["ease"] for k in got], [True, True, "in", "out", False, False])
        noted = K.read_note(K.note([{"t": 1.0, "db": -3.0, "ease": "out"}], "clip-1"))
        self.assertEqual(noted["keys"][0]["ease"], "out")

    def test_expand_writes_eases_as_short_straight_pieces_on_frames(self):
        keys = [{"t": 1.0, "db": 0.0, "ease": True}, {"t": 3.0, "db": -20.0, "ease": True}, {"t": 4.0, "db": -20.0}]
        out = K.expand(keys, 24.0, origin=0.5)
        self.assertFalse(any(k["ease"] for k in out))
        for k in out:
            frames = (k["t"] - 0.5) * 24
            self.assertAlmostEqual(frames, round(frames), places=4)
        t = np.linspace(1.0, 4.0, 400)
        self.assertLess(np.abs(K.curve_db(out, t) - K.curve_db(K.clean(keys), t)).max(), 0.3)
        plain = [{"t": 1.0, "db": 0.0, "ease": False}, {"t": 2.0, "db": -6.0, "ease": False}]
        self.assertEqual(K.expand(plain, 24.0), plain)

    def test_clean_sorts_clamps_and_drops_junk(self):
        self.assertEqual(K.clean([{"t": 2, "db": 50}, {"t": 1, "db": -300, "ease": 1}, {"t": "x", "db": 0}, {}]),
                         [{"t": 1.0, "db": -100.0, "ease": True}, {"t": 2.0, "db": 30.0, "ease": False}])

    def test_the_fcpxml_puts_the_clip_and_keys_where_resolve_lands_them(self):
        import xml.etree.ElementTree as ET
        from fractions import Fraction
        media_frame = K.frame_duration(29.97)
        media_start = 146120 * media_frame                          # a camera file's 01:21:15;16
        source_in = 100 * media_frame
        keys = [{"t": float(source_in) + 1.0, "db": -6.0, "ease": False}]
        xml = K.nested_fcpxml("C1.MP4 (Buddy curve)", r"E:\My Media\C1.MP4", 24.0, 86400, 240, media_start,
                              source_in, 2, keys, media_frame=media_frame)
        root = ET.fromstring(xml)
        clip = root.find(".//asset-clip")

        def seconds(text):
            return Fraction(text[:-1]) if text != "0s" else Fraction(0)
        # A quarter of a file frame early (Resolve rounds a clip's start up)...
        self.assertEqual(seconds(clip.get("start")), media_start + source_in - media_frame / 4)
        self.assertEqual(clip.get("srcEnable"), "audio")
        self.assertEqual(seconds(root.find(".//sequence").get("tcStart")), 3600)
        # ...and a key on its timeline frame exactly, a sliver after (Resolve rounds a key down).
        key = root.find(".//keyframe")
        self.assertEqual(seconds(key.get("time")), media_start + source_in + 1 + Fraction(1, 24 * 64))
        self.assertEqual(key.get("value"), "-6dB")
        self.assertEqual(root.find(".//media-rep").get("src"), "file://localhost/E:/My%20Media/C1.MP4")
        self.assertEqual(root.find(".//asset").get("name"), "C1.MP4")
        self.assertEqual(K.file_url("/Volumes/SSD/a b.wav"), "file://localhost/Volumes/SSD/a%20b.wav")

    def test_a_lone_key_goes_as_two_at_its_level(self):
        # Resolve drops a keyframeAnimation with one keyframe (read back as none).
        self.assertEqual(K.expand([{"t": 5.0, "db": -6.0}], 24.0, 2.0),
                         [{"t": 2.0, "db": -6.0, "ease": False}, {"t": 5.0, "db": -6.0, "ease": False}])
        on_start = K.expand([{"t": 2.0, "db": -6.0, "ease": True}], 24.0, 2.0)
        self.assertEqual([k["t"] for k in on_start], [2.0, round(2.0 + 1 / 24, 6)])
        self.assertEqual({k["db"] for k in on_start}, {-6.0})
        self.assertEqual(len(K.expand([], 24.0)), 0)

    def test_a_clip_from_the_files_first_frame_isnt_moved_before_the_file(self):
        # A camera MXF (start TC 23:34:38:20, 23.976) from its first frame on a 24 fps
        # timeline: a quarter frame early was before the file, Resolve clamped it,
        # and the curve came in 1268 frames of 1269 ("didn't line the curve up").
        import xml.etree.ElementTree as ET
        from fractions import Fraction
        media_frame = K.frame_duration(23.976)
        media_start = 2035516 * media_frame
        xml = K.nested_fcpxml("A001_0119.MXF (Buddy curve)", r"D:\Footage\A001_0119.MXF", 24.0, 86400, 1269,
                              media_start, Fraction(0), 8, [], media_frame=media_frame)
        clip = ET.fromstring(xml).find(".//asset-clip")
        self.assertEqual(Fraction(clip.get("start")[:-1]), media_start)
        self.assertEqual(Fraction(clip.get("duration")[:-1]), Fraction(1269, 24))

    def test_ntsc_rates_are_exact(self):
        from fractions import Fraction
        self.assertEqual(K.frame_duration(23.976), Fraction(1001, 24000))
        self.assertEqual(K.frame_duration(29.97), Fraction(1001, 30000))
        self.assertEqual(K.frame_duration(25), Fraction(1, 25))

    def test_names_and_the_note(self):
        self.assertEqual(K.curve_name("a.wav", {"a.wav (Buddy curve)"}), "a.wav (Buddy curve 2)")
        self.assertTrue(K.is_curve_name("a.wav (Buddy curve 2)"))
        self.assertFalse(K.is_curve_name("a.wav"))
        keys = [{"t": 1.0, "db": -3.0, "ease": True}]
        self.assertEqual(K.read_note(K.note(keys, "c9")), {"original": "c9", "keys": keys})
        self.assertIsNone(K.read_note("my own comment"))


class TrackTimeline:
    """Audio tracks as Resolve 21.1 treats them: AddTrack with {"index"} inserts
    there and moves the rest down (measured), or appends (inserts=False: an
    older Resolve); default names follow position."""

    def __init__(self, tracks, inserts=True):
        # tracks: [(name or None for Resolve's default, [(uid, start, end)], enabled)]
        self.tracks = [{"name": n, "items": [Item(u, u, s, e) for u, s, e in clips], "enabled": on}
                       for n, clips, on in tracks]
        self.inserts = inserts

    def GetTrackCount(self, kind): return len(self.tracks)
    def GetItemListInTrack(self, kind, i): return self.tracks[i - 1]["items"]
    def GetTrackName(self, kind, i): return self.tracks[i - 1]["name"] or f"Audio {i}"
    def SetTrackName(self, kind, i, name): self.tracks[i - 1]["name"] = name; return True
    def GetTrackSubType(self, kind, i): return "mono"
    def GetIsTrackEnabled(self, kind, i): return self.tracks[i - 1]["enabled"]
    def GetIsTrackLocked(self, kind, i): return False

    def AddTrack(self, kind, options=None):
        new = {"name": None, "items": [], "enabled": True}
        if self.inserts and isinstance(options, dict):
            self.tracks.insert(options["index"] - 1, new)
        else:
            self.tracks.append(new)
        return True

    def DeleteTrack(self, kind, i):
        del self.tracks[i - 1]
        return True

    def names(self):
        return [self.GetTrackName("audio", i) for i in range(1, len(self.tracks) + 1)]


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class BuddyTrackTests(unittest.TestCase):
    def test_a_new_buddy_track_goes_right_under_the_clips_track(self):
        tl = TrackTimeline([("Dialogue", [("d", 0, 100)], True), ("Music", [("m", 200, 300)], True),
                            ("SFX", [("s", 0, 50)], True)])
        self.assertEqual(resolve_ext._buddy_track(tl, 1, 0, 100), (2, True))
        self.assertEqual(tl.names(), ["Dialogue", "Buddy", "Music", "SFX"])
        # not the music's gap at 0-100, which is where the old rule put it
        self.assertEqual([i.GetUniqueId() for i in tl.tracks[2]["items"]], ["m"])

    def test_the_same_buddy_track_is_reused_while_nothing_overlaps(self):
        tl = TrackTimeline([("Dialogue", [], True), ("Buddy", [("c1", 0, 100)], True), ("Music", [], True)])
        self.assertEqual(resolve_ext._buddy_track(tl, 1, 100, 200), (2, False))       # touching, not overlapping
        self.assertEqual(resolve_ext._buddy_track(tl, 1, 50, 150), (3, True))         # overlaps: a second one
        self.assertEqual(tl.names(), ["Dialogue", "Buddy", "Buddy", "Music"])
        tl.tracks[2]["items"] = [Item("c2", "c2", 50, 150)]
        self.assertEqual(resolve_ext._buddy_track(tl, 1, 400, 500), (2, False))        # the first with room

    def test_a_turned_off_buddy_track_is_passed_over(self):
        tl = TrackTimeline([("Dialogue", [], True), ("Buddy", [], False)])
        self.assertEqual(resolve_ext._buddy_track(tl, 1, 0, 10), (3, True))

    def test_another_tracks_buddy_run_is_not_this_ones(self):
        tl = TrackTimeline([("Dialogue", [], True), ("Music", [], True), ("Buddy", [], True)])
        self.assertEqual(resolve_ext._buddy_track(tl, 1, 0, 10), (2, True))
        self.assertEqual(tl.names(), ["Dialogue", "Buddy", "Music", "Buddy"])

    def test_a_resolve_that_appends_gets_it_at_the_bottom(self):
        tl = TrackTimeline([("Dialogue", [("d", 0, 100)], True), ("Music", [("m", 0, 300)], True)], inserts=False)
        self.assertEqual(resolve_ext._buddy_track(tl, 1, 0, 100), (3, True))
        self.assertEqual(tl.names(), ["Dialogue", "Music", "Buddy"])            # Music keeps its name

    def test_only_an_empty_buddy_track_is_dropped(self):
        tl = TrackTimeline([("Dialogue", [], True), ("Buddy", [("c", 0, 1)], True), ("Buddy", [], True),
                            (None, [], True)])
        for index in (1, 2, 4):
            resolve_ext._drop_empty_buddy_track(tl, index)
        self.assertEqual(len(tl.tracks), 4)
        resolve_ext._drop_empty_buddy_track(tl, 3)
        self.assertEqual(tl.names(), ["Dialogue", "Buddy", "Audio 3"])


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class WriteTests(unittest.TestCase):
    def setUp(self):
        mic = MediaPoolItem("m2", r"D:\shoot\mic.wav")
        self.b = Item("c2", "mic.wav", 86424, 86800, mic, volume=22.0, fades=(12.0, 6.0), isolation=(False, 0.0))
        self.c = Item("c3", "mic.wav", 86800, 87000, mic)
        self.timeline = Timeline([{"name": "Mic", "items": [self.b, self.c]}])
        self.controller = Controller(self.timeline)

    def test_apply_writes_reads_back_and_undo_puts_it_back(self):
        result = resolve_ext.apply(self.controller, {
            "c2": {"props": {"AudioVolume": -3.0, "AudioPan": 20.0}, "fades": {"FadeIn": 24}},
            "c3": {"props": {"AudioVolume": 1.5}}})
        self.assertEqual(result["failed"], [])
        self.assertEqual((result["after"]["c2"]["volume"], result["after"]["c2"]["pan"]), (-3.0, 20.0))
        self.assertEqual((result["after"]["c2"]["fade_in"], result["after"]["c2"]["fade_out"]), (24.0, 6.0))
        self.assertEqual(result["before"]["c2"], {"props": {"AudioVolume": 22.0, "AudioPan": 0.0},
                                                  "fades": {"FadeIn": 12, "FadeOut": 6}})
        undone = resolve_ext.apply(self.controller, result["before"])
        self.assertEqual(undone["failed"], [])
        self.assertEqual((self.b.props["AudioVolume"], self.b.props["AudioPan"], self.b.fades), (22.0, 0.0, (12.0, 6.0)))
        self.assertEqual(self.c.props["AudioVolume"], 0.0)

    def test_a_refused_clip_is_left_whole(self):
        # c3 has no Voice Isolation: Resolve refuses its whole change.
        result = resolve_ext.apply(self.controller, {
            "c3": {"props": {"AudioVolume": -6.0, "AudioVoiceIsolationEnabled": True}},
            "gone": {"props": {"AudioVolume": 0.0}}})
        self.assertEqual(sorted(result["failed"]), ["c3", "gone"])
        self.assertEqual(self.c.props["AudioVolume"], 0.0)
        self.assertNotIn("c3", result["before"])

    def test_a_refused_fade_puts_the_properties_back(self):
        result = resolve_ext.apply(self.controller, {"c3": {"props": {"AudioVolume": -6.0}, "fades": {"FadeIn": 500}}})
        self.assertEqual(result["failed"], ["c3"])
        self.assertEqual(self.c.props["AudioVolume"], 0.0)

    def test_crossfade_counts_the_refusals(self):
        self.c.name = "no handles"
        added, failed = resolve_ext.crossfade(self.controller, ["c2", "c3"], "Cross Fade 0 dB", 12)
        self.assertEqual((added, failed), (1, ["c3"]))
        self.assertEqual(self.b.transition, {"type": "Cross Fade 0 dB", "category": "audio", "position": "end",
                                             "alignment": "center", "duration": 12})

    def test_the_child_process_seeks_and_reports(self):
        import json
        import os
        import tempfile
        from pages.audio_assistant import resolve_child
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            code = resolve_child.main(path, {"do": "seek", "timecode": "01:00:05:03"}, connect=lambda: self.controller)
            self.assertEqual(code, 0)
            self.assertEqual(self.timeline.moved_to, "01:00:05:03")
            self.timeline.SetCurrentTimecode = lambda _tc: False       # playing: Resolve refuses
            self.assertEqual(resolve_child.main(path, {"do": "seek", "timecode": "01:00:06:00"},
                                                connect=lambda: self.controller), 2)
            with open(path, encoding="utf-8") as f:
                self.assertFalse(json.load(f)["ok"])
            self.assertEqual(resolve_child.main(path, {"do": "nope"}, connect=lambda: self.controller), 2)
        finally:
            os.remove(path)

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_the_child_never_runs_on_resolves_script_host(self):
        # From Resolve's Scripts menu sys.executable is fuscript.exe, which runs
        # a .py with no __file__: the child died and every seek "didn't answer".
        from unittest import mock
        from pages.audio_assistant import page
        host = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fuscript.exe"
        real = r"C:\Python314\python.exe"
        with mock.patch.object(page.sys, "executable", host):
            with mock.patch("core.startup_manager.running_python_exe", return_value=real):
                self.assertEqual(page.child_python(), real)
            with mock.patch("core.startup_manager.running_python_exe", return_value=None):
                self.assertIsNone(page.child_python())
        with mock.patch.object(page.sys, "executable", real), \
                mock.patch("core.startup_manager.running_python_exe", return_value=None):
            self.assertEqual(page.child_python(), real)       # run from source: that python is fine


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class SeekFallbackTests(unittest.TestCase):
    """macOS: a child process can't reach the free Resolve, so the page seeks
    from its own process instead - only once a click or drag is over."""

    def setUp(self):
        import json
        import os
        import tempfile
        import types
        from unittest import mock
        from pages.audio_assistant import page as page_mod
        self.timeline = Timeline([{"name": "Mic", "items": []}])
        controller = Controller(self.timeline)

        class Worker:
            def busy(self):
                return False

            def start(self, job, done):
                done(job(), None)

        class Timer:
            def start(self): pass
            def stop(self): pass

        fd, self.result = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump({"ok": False, "error": "Couldn't reach Resolve: None"}, f)

        class Proc:                                   # the child: couldn't connect
            returncode = 1
            def poll(self): return 1

        p = types.SimpleNamespace(
            timeline={"fps": 24.0, "drop_frame": False}, _seek=None, _seek_final=False, _child_connects=None,
            _seek_quiet_until=0.0, _worker=Worker(), _child_timer=Timer(), _controller=lambda: controller,
            emit=lambda *a: None)
        for name in ("_check_child", "_start_seek", "_seek_here", "_on_seek"):
            setattr(p, name, types.MethodType(getattr(page_mod.AudioAssistantPage, name), p))
        p._run_child = lambda command, done: setattr(p, "_child", {"do": command["do"], "command": command})
        p._child = {"proc": Proc(), "result": self.result, "done": None, "do": "seek", "started": 1e18,
                    "frame": 86400 + 24 * 5}
        self.page = p
        # The page seeks this way on macOS only, so the test runs as if there.
        patch = mock.patch.object(page_mod.sys, "platform", "darwin")
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_child_that_cant_connect_hands_the_seek_to_the_page(self):
        self.page._check_child()
        self.assertIs(self.page._child_connects, False)
        self.assertEqual(self.timeline.moved_to, "01:00:05:00")
        # From then on, a drag's steps wait; the frame it ends on goes.
        self.page._seek, self.page._seek_final = 86400 + 24 * 6, False
        self.page._start_seek()
        self.assertEqual(self.timeline.moved_to, "01:00:05:00")
        self.page._seek, self.page._seek_final = 86400 + 24 * 7, True
        self.page._start_seek()
        self.assertEqual(self.timeline.moved_to, "01:00:07:00")


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class MixerTests(unittest.TestCase):
    def setUp(self):
        from pages.audio_assistant import levels
        self.levels = levels
        self.clips = {
            "a": {"id": "a", "start": 0, "end": 100, "volume": 22.0, "fade_in": 10, "fade_out": 0,
                  "isolation": {"on": False, "amount": 0}, "leveler": None},
            "b": {"id": "b", "start": 100, "end": 150, "volume": -3.0, "fade_in": 0, "fade_out": 0,
                  "isolation": None, "leveler": {"on": False, "mode": 0}},
            "x": {"id": "x", "start": 95, "end": 105, "transition": True},
        }

    def test_set_to_and_change_by(self):
        self.assertEqual(self.levels.changes(self.clips, {"ids": ["a", "b"], "volume": -6})["b"],
                         {"props": {"AudioVolume": -6.0}})
        by = self.levels.changes(self.clips, {"ids": ["a", "b"], "volume_by": 10})
        self.assertEqual((by["a"]["props"]["AudioVolume"], by["b"]["props"]["AudioVolume"]), (30.0, 7.0))  # +32 caps at +30

    def test_features_go_only_to_clips_that_have_them(self):
        out = self.levels.changes(self.clips, {"ids": ["a", "b"], "isolation": {"on": True, "amount": 250}})
        self.assertEqual(out, {"a": {"props": {"AudioVoiceIsolationEnabled": True, "AudioVoiceIsolationAmount": 100}}})
        out = self.levels.changes(self.clips, {"ids": ["a", "b"], "leveler": {"on": True, "mode": 2, "gain": 9}})
        self.assertEqual(out["b"]["props"], {"AudioDialogueLevelerEnabled": True, "AudioDialogueLevelerMode": 2.0,
                                             "AudioDialogueLevelerOutputGain": 6.0})
        self.assertNotIn("a", out)

    def test_fades_never_outrun_the_clip(self):
        out = self.levels.changes(self.clips, {"ids": ["b"], "fade_in": 30, "fade_out": 40})
        self.assertEqual(out["b"]["fades"], {"FadeIn": 30, "FadeOut": 20})
        out = self.levels.changes(self.clips, {"ids": ["a"], "fade_out": 200.4})
        self.assertEqual(out["a"]["fades"], {"FadeOut": 90})           # 100 frames, 10 of them fading in
        self.assertEqual(self.levels.changes(self.clips, {"ids": ["x"], "volume": 0}), {})   # not a clip

    def test_cuts_between_selected_clips(self):
        tracks = [{"clips": [self.clips["a"], self.clips["b"]]}]
        self.assertEqual(self.levels.cuts(tracks, ["a", "b"]), ["a"])
        self.assertEqual(self.levels.cuts(tracks, ["a"]), [])
        tracks[0]["clips"].append(self.clips["x"])                     # a crossfade is there already
        self.assertEqual(self.levels.cuts(tracks, ["a", "b"]), [])

    def test_every_preset_has_one_target(self):
        for preset in self.levels.PRESETS:
            self.assertEqual(len({"loudness", "peak"} & set(preset)), 1, preset["id"])


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class MatchTests(unittest.TestCase):
    """Match: the clip volume that lands a clip on a target, by Buddy's own BS.1770 sums."""

    def setUp(self):
        from pages.audio_assistant import levels
        self.levels = levels
        rate, fps = 48000, 24.0
        t = np.arange(rate * 20) / rate
        # One file: 10 s of tone at -20 LUFS (both channels at -20 dBFS... see peaks' test), then 10 s at -30.
        tone = np.sin(2 * np.pi * 997 * t) * np.where(t < 10, 0.1, 0.1 / np.sqrt(10))
        loud = peaks.LoudnessAccumulator()
        acc = peaks.PeakAccumulator()
        stereo = np.stack([tone, tone], 1)
        for s in range(0, len(stereo), 4800):
            loud.add(stereo[s:s + 4800], rate)
            acc.add(stereo[s:s + 4800], rate)
        self.audio = {"codes": np.frombuffer(acc.codes(), np.uint8), "loud": np.frombuffer(loud.energies(), np.float32),
                      "rate": peaks.PEAK_RATE, "floor": peaks.FLOOR_DB, "block": peaks.LOUD_BLOCK_S}
        self.fps = fps
        # Clip "a" plays the loud half at +0 dB, "b" the quiet half at +5 dB.
        self.clips = {
            "a": {"id": "a", "start": 0, "end": 240, "offset": 0.0, "volume": 0.0, "media": {"key": "f"}},
            "b": {"id": "b", "start": 240, "end": 480, "offset": 10.0, "volume": 5.0, "media": {"key": "f"}},
            "c": {"id": "c", "start": 480, "end": 500, "offset": 0.0, "volume": 0.0, "media": None},
        }
        self.audio_of = lambda clip: self.audio if clip.get("media") else None

    def lufs(self, uid, volume):
        clip = dict(self.clips[uid], volume=volume)
        return self.levels.clip_lufs(clip, self.audio, self.fps)

    def test_each_clip_lands_on_the_target(self):
        self.assertAlmostEqual(self.lufs("a", 0.0), -20.0, delta=0.1)
        self.assertAlmostEqual(self.lufs("b", 5.0), -25.0, delta=0.1)
        youtube = self.levels.PRESET_IDS["youtube"]
        change, report = self.levels.match(self.clips, ["a", "b", "c"], self.audio_of, self.fps, youtube)
        self.assertEqual(report["missing"], ["c"])
        for uid in ("a", "b"):
            volume = change[uid]["props"]["AudioVolume"]
            self.assertAlmostEqual(self.lufs(uid, volume), -14.0, delta=0.1)

    def test_together_moves_every_clip_by_the_same(self):
        change, _report = self.levels.match(self.clips, ["a", "b"], self.audio_of, self.fps,
                                            self.levels.PRESET_IDS["ebu"], independent=False)
        moved = [change[u]["props"]["AudioVolume"] - self.clips[u]["volume"] for u in ("a", "b")]
        self.assertAlmostEqual(moved[0], moved[1], delta=0.11)

    def test_peak_target_and_the_volume_limit(self):
        change, _ = self.levels.match(self.clips, ["a"], self.audio_of, self.fps, self.levels.PRESET_IDS["peak"])
        self.assertAlmostEqual(change["a"]["props"]["AudioVolume"], 19.0, delta=0.3)    # -20 dBFS peak to -1
        # Turned right down, a clip still measures (the -70 gate reads it before its volume)...
        self.clips["b"]["volume"] = -60.0
        change, report = self.levels.match(self.clips, ["b"], self.audio_of, self.fps, self.levels.PRESET_IDS["youtube"])
        self.assertAlmostEqual(change["b"]["props"]["AudioVolume"], 16.0, delta=0.2)   # -30 LUFS raw, to -14
        # ...and past Resolve's +30 dB it's said.
        change, report = self.levels.match(self.clips, ["b"], self.audio_of, self.fps, {"loudness": 10.0})
        self.assertEqual((change["b"]["props"]["AudioVolume"], report["clamped"]), (30.0, ["b"]))

    def test_keys_are_what_a_clip_is_heard_at(self):
        flat = self.lufs("a", -10.0)
        keyed = dict(self.clips["a"], keys=[{"t": 0.0, "db": -10.0, "ease": False}])      # keyed in Resolve
        self.assertAlmostEqual(self.levels.clip_lufs(keyed, self.audio, self.fps), flat, delta=0.05)
        curve = dict(keyed, volume=5.0, curve={"original": "x"})                          # a Buddy curve: +5 on top
        self.assertAlmostEqual(self.levels.clip_lufs(curve, self.audio, self.fps), flat + 5.0, delta=0.05)
        self.assertAlmostEqual(self.levels.clip_peak(curve, self.audio, self.fps), -20.0 - 10.0 + 5.0, delta=0.3)

    def test_a_clip_keyed_in_resolve_keeps_its_volume(self):
        self.clips["a"]["keys"] = [{"t": 0.0, "db": -10.0, "ease": False}]
        change, report = self.levels.match(self.clips, ["a", "b"], self.audio_of, self.fps,
                                           self.levels.PRESET_IDS["youtube"])
        self.assertEqual((report["keyed"], sorted(change)), (["a"], ["b"]))
        self.assertEqual(self.levels.changes(self.clips, {"ids": ["a", "b"], "volume_by": 3}),
                         {"b": {"props": {"AudioVolume": 8.0}}})

    def test_fades_count_as_the_panel_shows_them(self):
        faded = dict(self.clips["a"], fade_in=120)                                    # half the clip fading in
        self.assertLess(self.levels.clip_lufs(faded, self.audio, self.fps), self.lufs("a", 0.0) - 0.5)


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class PeakTests(unittest.TestCase):
    def test_codes_run_from_the_floor_to_full_scale(self):
        codes = peaks.to_codes([0.0, 1e-4, 0.5, 1.0, 2.0])
        self.assertEqual(codes[0], 0)
        self.assertEqual(codes[1], 0)                        # -80 dB: under the floor
        self.assertEqual(codes[2], round((-6.0206 - peaks.FLOOR_DB) / -peaks.FLOOR_DB * 255))
        self.assertEqual(codes[3], 255)
        self.assertEqual(codes[4], 255)

    def test_buffers_that_straddle_peaks_fold_into_one_per_hundredth(self):
        rate = 44100
        t = np.arange(rate * 2) / rate
        wave = np.where(t < 1, 0.5, 0.25) * np.sin(2 * np.pi * 440 * t)
        stereo = np.stack([wave, wave * 0.1], axis=1)
        acc = peaks.PeakAccumulator()
        for start in range(0, len(stereo), 1000):            # 1000 isn't a multiple of 441
            acc.add(stereo[start:start + 1000], rate)
        codes = np.frombuffer(acc.codes(), dtype=np.uint8)
        self.assertEqual(len(codes), 2 * peaks.PEAK_RATE)
        half, quarter = peaks.to_codes([0.5])[0], peaks.to_codes([0.25])[0]
        self.assertTrue(np.all(np.abs(codes[5:95].astype(int) - half) <= 1))
        self.assertTrue(np.all(np.abs(codes[105:195].astype(int) - quarter) <= 1))

    def test_mono_samples_are_taken_too(self):
        acc = peaks.PeakAccumulator()
        acc.add(np.full(480, -1.0), 48000)
        self.assertEqual(acc.codes(), bytes([255]))

    def loudness(self, samples, rate, chunk=1000):
        acc = peaks.LoudnessAccumulator()
        for start in range(0, len(samples), chunk):
            acc.add(samples[start:start + chunk], rate)
        return peaks.integrated_lufs(np.frombuffer(acc.energies(), dtype=np.float32))

    def test_loudness_matches_bs1770(self):
        # A 997 Hz sine at 0 dBFS in one channel is -3.01 LKFS, by the standard's own definition.
        for rate in (48000, 44100):
            t = np.arange(rate * 5) / rate
            sine = np.sin(2 * np.pi * 997 * t)
            self.assertAlmostEqual(self.loudness(np.stack([sine, np.zeros_like(sine)], 1), rate), -3.01, delta=0.05)
            self.assertAlmostEqual(self.loudness(np.stack([sine, sine], 1) * 0.1, rate), -20.0, delta=0.05)

    def test_loudness_gates_out_silence(self):
        rate = 48000
        t = np.arange(rate * 4) / rate
        tone = 0.1 * np.sin(2 * np.pi * 997 * t)
        with_gap = np.concatenate([tone, np.zeros(rate * 4), tone])[:, None]
        # Averaged plainly, 4 s of silence in 12 would cost 1.76 dB. Gated, only the few
        # blocks that straddle an edge (part tone, within 10 LU) count - about 0.2 dB.
        self.assertAlmostEqual(self.loudness(with_gap, rate), self.loudness(tone[:, None], rate), delta=0.3)
        self.assertIsNone(self.loudness(np.zeros((rate, 1)), rate))

    def test_cache_round_trip(self):
        import os
        import tempfile
        real = peaks.CACHE_DIR
        with tempfile.TemporaryDirectory() as folder:
            peaks.CACHE_DIR = os.path.join(folder, "peaks")
            media = os.path.join(folder, "a.wav")
            with open(media, "wb") as f:
                f.write(b"RIFF")
            try:
                self.assertIsNone(peaks.load_cached(media))
                peaks.save_cached(media, (b"\x01\x02", b"\x00" * 8, 2))
                self.assertEqual(peaks.load_cached(media), (b"\x01\x02", b"\x00" * 8, 2))
            finally:
                peaks.CACHE_DIR = real


# ------------------------------------------------ the audit's five findings --

@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class ChannelTests(unittest.TestCase):
    """A clip is drawn and measured by the channels Resolve maps to it."""

    def decoded(self, samples, rate=48000):
        acc, loud = peaks.PeakAccumulator(), peaks.LoudnessAccumulator()
        for start in range(0, len(samples), 1000):
            acc.add(samples[start:start + 1000], rate)
            loud.add(samples[start:start + 1000], rate)
        return acc.per_channel().tobytes(), loud.per_channel().tobytes(), acc.channels

    def lufs(self, folded):
        return peaks.integrated_lufs(np.frombuffer(folded[1], dtype=np.float32))

    def test_a_clip_on_one_channel_is_measured_by_that_channel(self):
        # The audit's case: a loud left, a quiet right, a clip mapped to the right.
        rate = 48000
        t = np.arange(rate * 4) / rate
        sine = np.sin(2 * np.pi * 997 * t)
        result = self.decoded(np.stack([sine, sine * 0.01], axis=1), rate)
        whole, _ = peaks.fold(result)
        left, _ = peaks.fold(result, [0])
        right, _ = peaks.fold(result, [1])
        self.assertAlmostEqual(self.lufs(left), -3.01, delta=0.05)
        self.assertAlmostEqual(self.lufs(right), -43.01, delta=0.05)
        self.assertGreater(self.lufs(whole), self.lufs(left))           # both summed: louder still
        full = np.frombuffer(right[0], dtype=np.uint8)
        self.assertTrue(np.all(np.abs(full[5:-5].astype(int) - peaks.to_codes([0.01])[0]) <= 1))

    def test_all_channels_fold_as_before(self):
        rate = 48000
        stereo = np.stack([np.full(rate, 0.5), np.full(rate, 0.25)], axis=1)
        acc = peaks.PeakAccumulator()
        acc.add(stereo, rate)
        result = self.decoded(stereo, rate)
        self.assertEqual(peaks.fold(result)[0][0], acc.codes())          # what codes() gave before
        self.assertEqual(acc.per_channel().shape, (peaks.PEAK_RATE, 2))

    def test_a_channel_the_decoder_did_not_give_is_said_not_guessed(self):
        mono = self.decoded(np.full((4800, 1), 0.5))                     # a camera MXF: one stream decoded
        self.assertEqual(peaks.fold(mono, [1]),
                         (None, "Buddy can read only the first channel of this file, and the clip plays channel 2."))
        self.assertIsNotNone(peaks.fold(mono, [0])[0])

    def test_the_clip_carries_its_channels_and_waveform_key(self):
        media = {"key": "abc", "channels": 2}

        def item(mapping):
            return type("Item", (), {"GetSourceAudioChannelMapping": lambda self: mapping})()

        right = {}
        resolve_ext._play(right, item('{"track_mapping":{"1":{"channel_idx":[2],"type":"mono"}}}'), media)
        self.assertEqual((right["channels"], right["peaks"]), ([1], "abc:1"))
        both = {}
        resolve_ext._play(both, item('{"track_mapping":{"1":{"channel_idx":[1,2],"type":"stereo"}}}'), media)
        self.assertEqual((both["channels"], both["peaks"]), (None, "abc"))    # all of them: the same as none
        none = {}
        resolve_ext._play(none, object(), media)                               # no mapping to read
        self.assertEqual((none["channels"], none["peaks"]), (None, "abc"))


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class AppendingResolveTests(unittest.TestCase):
    """A Resolve that appends the track asked for at an index: no user track is renamed."""

    def test_the_audits_case(self):
        tl = TrackTimeline([("Dialogue", [("d", 0, 100)], True), ("Music", [], True), ("SFX", [], True)],
                           inserts=False)
        self.assertEqual(resolve_ext._buddy_track(tl, 1, 0, 100), (4, True))
        self.assertEqual(tl.names(), ["Dialogue", "Music", "SFX", "Buddy"])

    def test_inserting_under_default_named_tracks_is_still_seen(self):
        tl = TrackTimeline([("Dialogue", [("d", 0, 100)], True), (None, [("m", 0, 50)], True), (None, [], True)])
        self.assertEqual(resolve_ext._buddy_track(tl, 1, 0, 100), (2, True))
        self.assertEqual(tl.names(), ["Dialogue", "Buddy", "Audio 3", "Audio 4"])

    def test_empty_default_tracks_either_way_take_the_place_asked_for(self):
        for inserts in (True, False):
            tl = TrackTimeline([("Dialogue", [("d", 0, 100)], True), (None, [], True)], inserts=inserts)
            self.assertEqual(resolve_ext._buddy_track(tl, 1, 0, 100), (2, True))
            self.assertEqual(tl.names()[:2], ["Dialogue", "Buddy"])

    def test_the_new_track_is_found_by_names_and_clips(self):
        d, m = (("named", "Dialogue"), ("d",)), (("named", "Music"), ())
        new = (("default", "Audio"), ())
        self.assertEqual(resolve_ext._new_track([d, m], [d, new, m], 2), 2)      # inserted
        self.assertEqual(resolve_ext._new_track([d, m], [d, m, new], 2), 3)      # appended
        self.assertIsNone(resolve_ext._new_track([d, m], [d, m, m], 2))          # nothing new: no guess


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class RefreshAndQuitTests(unittest.TestCase):
    def test_refresh_forgets_the_files(self):
        from types import SimpleNamespace
        from unittest import mock
        from pages.audio_assistant.page import AudioAssistantPage
        loader = mock.Mock()
        page = SimpleNamespace(_full_due=5.0, problem="x", _media_cache={"mpi-1": {"path": "old.wav"}},
                               _peaks={"k": None}, _peak_errors={"k": "offline"}, _audio={"k": {}}, _shown={"k"},
                               _loader=loader, _poll=mock.Mock())
        AudioAssistantPage.on_refresh(page)
        self.assertEqual((page._media_cache, page._peaks, page._peak_errors, page._audio, page._shown),
                         ({}, {}, {}, {}, set()))
        loader.forget.assert_called_once_with()
        page._poll.assert_called_once_with(connect=True)

    def test_forget_keeps_what_is_queued_or_decoding(self):
        loader = peaks.PeakLoader()
        loader._asked = {"a", "b", "c"}
        loader._queue = [("b", "b.wav")]
        loader._thread = type("T", (), {"key": "c"})()
        loader.forget()
        self.assertEqual(loader._asked, {"b", "c"})
        loader._thread = None

    def test_a_decode_that_will_not_stop_is_kept_not_destroyed(self):
        from unittest import mock
        loader = peaks.PeakLoader()
        stuck = mock.Mock()
        stuck.wait.return_value = False
        stuck.isRunning.return_value = True
        loader._thread = stuck
        before = len(peaks._STILL_RUNNING)
        loader.shutdown()
        self.assertTrue(loader._stop.is_set())
        stuck.setParent.assert_called_once_with(None)
        self.assertIs(peaks._STILL_RUNNING[-1], stuck)
        del peaks._STILL_RUNNING[before:]

    def test_a_stalled_decode_stops_when_asked_not_after_the_stall(self):
        import os
        import tempfile
        import time
        from unittest import mock
        from PySide6.QtCore import QObject, Signal

        qt_app()                                                      # _stream's event loop needs one

        class Stalled(QObject):
            """A decoder that starts and then sends nothing at all."""
            bufferReady, finished, durationChanged = Signal(), Signal(), Signal(int)
            error = Signal(object)
            def setSource(self, url): pass
            def start(self): pass
            def stop(self): pass
            def position(self): return 0
            def errorString(self): return ""

        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        began = time.monotonic()
        try:
            with mock.patch("PySide6.QtMultimedia.QAudioDecoder", Stalled):
                why = peaks._stream(path, lambda s, r: None, cancelled=lambda: time.monotonic() - began > 0.2)
        finally:
            os.remove(path)
        self.assertEqual(why, "Stopped.")
        self.assertLess(time.monotonic() - began, 1.5)          # not STALL_MS (20 s)


# ---------------------------------------------------------------- the tabs --

def _timeline(tl_id):
    """A made-up timeline as page.py sends it: ten minutes, two tracks, a clip on each."""
    start = 86400
    clip = lambda i: {"id": f"{tl_id}-c{i}", "name": "A001.wav", "start": start + 100, "end": start + 24 * 500,
                      "offset": 0, "volume": 0, "pan": 0, "fade_in": 0, "fade_out": 0, "enabled": True,
                      "peaks": None}
    return {"id": tl_id, "fps": 24, "start": start, "end": start + 24 * 600, "drop_frame": False, "markers": [],
            "tracks": [{"index": i, "name": f"Track {i}", "kind": "mono", "enabled": True, "locked": False,
                        "clips": [clip(i)]} for i in (1, 2)]}


@unittest.skipUnless(HAVE_DEPS, "numpy / PySide6 not installed")
class TabTests(unittest.TestCase):
    """index.html in a web view, no Resolve: the page opens on Timeline, Sound Design
    is a placeholder of its own, and the tracks come back at their width after it -
    even when the timeline was read while it showed."""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        except ImportError:  # pragma: no cover - PySide6 without its web engine
            raise unittest.SkipTest("QtWebEngine not installed")
        from PySide6.QtWidgets import QApplication
        cls.app = qt_app()
        if not isinstance(cls.app, QApplication):     # another file's test made a bare one
            raise unittest.SkipTest("a QCoreApplication was made first; a web view needs a QApplication")

    def setUp(self):
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from shiboken6 import delete
        self.view = QWebEngineView()
        self.addCleanup(delete, self.view)
        self.view.resize(1000, 700)
        # As page.py sets it: sound may start without a click first (Sound Design's preview plays on it).
        from PySide6.QtWebEngineCore import QWebEngineSettings
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        loop, loaded = QEventLoop(), []
        self.view.loadFinished.connect(lambda ok: (loaded.append(ok), loop.quit()))
        self.view.setUrl(QUrl.fromLocalFile(str(_paths.APP / "pages" / "audio_assistant" / "web" / "index.html")))
        self.view.show()
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        self.assertEqual(loaded, [True])

    def js(self, body):
        from PySide6.QtCore import QEventLoop, QTimer
        loop, result = QEventLoop(), []
        self.view.page().runJavaScript("JSON.stringify((() => {" + body + "})())", 0,
                                       lambda value: (result.append(value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        self.assertTrue(result, "JavaScript timed out")
        return json.loads(result[0])

    def until(self, expr):
        """Waits (5 s at most) for a JavaScript expression to come true."""
        from PySide6.QtCore import QEventLoop, QTimer
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.js(f"return !!({expr});"):
                return
            loop = QEventLoop()
            QTimer.singleShot(50, loop.quit)
            loop.exec()
        self.fail("Timed out waiting for " + expr)

    def click(self, tab):
        self.js(f"document.querySelector('#tabs [data-tab={tab}]').click(); return true;")

    def shown(self):
        return self.js("""return {
            selected: [...document.querySelectorAll('#tabs button')].filter(b => b.getAttribute('aria-selected') === 'true')
                          .map(b => b.dataset.tab),
            timeline: !document.getElementById('panel-timeline').hidden,
            sound: !document.getElementById('panel-sound-design').hidden};""")

    # The tracks drawn the width of their box, fitted to it unless zoomed in.
    DRAWN = ("scroller.clientWidth > 0 && canvas.style.width === scroller.clientWidth + 'px'"
             " && (!fit || Math.abs(ppf - fitPpf()) < 1e-9)")

    def receive(self, name, payload):
        self.js(f"Buddy.receive({json.dumps(name)}, {json.dumps(payload)}); return true;")

    def test_it_opens_on_timeline_and_sound_design_has_a_panel_of_its_own(self):
        self.assertEqual(self.js("return [...document.querySelectorAll('#tabs button[data-tab]')].map(b => b.textContent);"),
                         ["Timeline", "Sound Design"])
        # Where the Keys tab was: a button to the DAW, which it grew into.
        # (timeline.js took Buddy.send when it loaded; with no Qt here, a send is logged.)
        self.js("window.__sent = []; const log = console.log;"
                " console.log = (tag, name, data) => (tag === '[Buddy.send]' ? window.__sent.push([name, data]) : log(tag, name, data));"
                " document.querySelector('#tabs [data-action=open_daw]').click(); console.log = log; return true;")
        self.assertEqual(self.js("return window.__sent.map(s => s[0]);"), ["open_daw"])
        self.assertEqual(self.shown(), {"selected": ["timeline"], "timeline": True, "sound": False})
        self.assertEqual(self.shown(), {"selected": ["timeline"], "timeline": True, "sound": False})
        self.click("sound-design")
        self.assertEqual(self.shown(), {"selected": ["sound-design"], "timeline": False, "sound": True})
        self.assertEqual(self.js("return [...document.querySelectorAll('#panel-sound-design .section-title')]"
                                 ".map(h => h.textContent);"), ["Song", "Sounds", "Moments"])
        self.click("timeline")
        self.assertEqual(self.shown(), {"selected": ["timeline"], "timeline": True, "sound": False})

    def settle(self):
        """Waits a few frames: the ResizeObserver's run after a tab switch has happened."""
        self.js("window.__frames = 0; requestAnimationFrame(() => requestAnimationFrame(() =>"
                " requestAnimationFrame(() => { window.__frames = 1; }))); return true;")
        self.until("window.__frames === 1")

    def test_the_tracks_come_back_where_they_were(self):
        self.receive("timeline", _timeline("tl-1"))
        self.until(self.DRAWN)
        self.js("setZoom(ppf * 20, 0); scroller.scrollLeft = 3000; return true;")
        self.until(f"!fit && Math.round(scroller.scrollLeft) === 3000 && {self.DRAWN}")
        zoom, width = self.js("return [ppf, canvas.width];")
        self.click("sound-design")
        self.receive("timeline", _timeline("tl-1"))                # read again meanwhile
        self.settle()
        self.assertEqual(self.js("return canvas.width;"), width)    # the last picture kept, not shrunk to nothing
        self.click("timeline")
        self.settle()
        self.until(self.DRAWN)
        self.assertEqual(self.js("return [ppf, Math.round(scroller.scrollLeft)];"), [zoom, 3000])

    def test_a_timeline_read_while_sound_design_shows_is_fitted_once_back(self):
        self.click("sound-design")
        self.receive("timeline", _timeline("tl-2"))
        self.click("timeline")
        self.until(f"fit && ppf > 0 && {self.DRAWN}")

    def test_keys_leave_the_timeline_alone_while_it_is_hidden(self):
        self.receive("timeline", _timeline("tl-1"))
        self.receive("selection", {"ids": ["tl-1-c1"], "from_resolve": False})
        escape = "document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));"
        self.click("sound-design")
        self.js(escape + "return true;")
        self.assertEqual(self.js("return [...SEL];"), ["tl-1-c1"])
        self.click("timeline")
        self.js(escape + "return true;")
        self.assertEqual(self.js("return [...SEL];"), [])

    # ------------------------------------------------------- sound design --

    SONG = {"id": "tl-1-c2", "bpm": 124.0, "beat_s": 0.48387, "bar_s": 1.93548, "length_s": 499.0,
            "bars": [0.0, 1.935, 3.871, 5.806], "levels": [-30.0, -29.0, -14.0, -15.0],
            "key": {"tonic": 9, "mode": "minor", "name": "A minor", "camelot": "8A", "relative": "C major",
                    "clear": True, "also": None},
            "moments": [{"t": 3.871, "bar": 2, "kind": "drop", "strength": 0.9, "frame": 86593}],
            "start": 86500, "end": 98400, "name": "A001.wav"}

    def sound_design(self):
        tl = _timeline("tl-1")
        for track in tl["tracks"]:
            for clip in track["clips"]:
                clip["media"] = {"path": rf"D:\Music\{clip['id']}.wav", "key": clip["id"]}
        self.receive("timeline", tl)
        self.click("sound-design")
        self.js("window.__sent = []; Buddy.send = (name, data) => window.__sent.push([name, data]); return true;")

    def test_the_song_is_guessed_and_listened_to(self):
        self.sound_design()
        self.until("document.querySelector('.sd-song').value === 'tl-1-c1'")       # the longest; a tie: the first
        self.js("document.querySelector('.sd-song-row .btn').click(); return true;")
        self.assertEqual(self.js("return window.__sent;"), [["sd_listen", {"id": "tl-1-c1"}]])

    def test_what_buddy_heard_shows_and_a_moment_places_its_sound(self):
        self.sound_design()
        self.js("const s = document.querySelector('.sd-song'); s.value = 'tl-1-c2';"
                " s.dispatchEvent(new Event('change')); return true;")
        self.receive("sd_song", self.SONG)
        tiles = self.js("return [...document.querySelectorAll('.sd-tiles .tile b')].map(b => b.textContent);")
        self.assertEqual(tiles, ["A minor", "124 BPM", "1"])
        self.assertEqual(self.js("return [...document.querySelectorAll('.sd-moment .sd-put')].map(b => b.textContent);"),
                         ["Riser into it", "Impact on it"])
        self.js("document.querySelector('.sd-moment .sd-put').click(); return true;")
        self.until("window.__sent.length === 1")
        name, sent = self.js("const [n, d] = window.__sent[0];"
                             " return [n, {kind: d.kind, frame: d.frame, sync: d.sync, rate: d.rate, label: d.label,"
                             " song: d.song, channels: d.channels.length, bpm: d.recipe.song.bpm, tonic: d.recipe.song.tonic}];")
        self.assertEqual(name, "sd_place")
        self.assertEqual({k: sent[k] for k in ("kind", "frame", "rate", "song", "channels", "bpm", "tonic")},
                         {"kind": "riser", "frame": 86593, "rate": 48000, "song": "tl-1-c2", "channels": 2,
                          "bpm": 124.0, "tonic": 9})
        self.assertAlmostEqual(sent["sync"], 2 * 4 * 60 / 124.0, places=3)       # 2 bars: its top on the drop
        self.assertEqual(sent["label"], "Riser 2 bars A minor 124 BPM")
        # Placing again waits for the first to be in.
        self.assertTrue(self.js("return document.querySelector('.sd-moment .sd-put').disabled;"))
        self.receive("sd_placed", {"ok": True})
        self.assertFalse(self.js("return document.querySelector('.sd-moment .sd-put').disabled;"))

    def test_the_sounds_mini_timeline_scrubs_and_its_settings_are_in_groups(self):
        self.sound_design()
        self.until("/beats before the moment/.test(document.querySelector('.sd-readout').textContent)")
        self.assertEqual(self.groups(), ["Timing", "Noise", "Tone", "Sub", "Flutter", "Colour", "Echo and reverb", "Output"])
        self.assertEqual(self.js("return [...document.querySelectorAll('.sd-group')].map(g => g.classList.contains('open'));"),
                         [True, True, True, False, False, False, False, False])
        # A drag across the middle of it: the playhead follows - 2 bars at 120 BPM is 4 s, the moment at the end.
        # An arrow over it (not a text cursor); left and right only while dragging.
        cursors = self.js("""const c = document.querySelector('.sd-wave'), r = c.getBoundingClientRect();
                   const at = f => ({clientX: r.left + r.width * f, clientY: r.top + 40, button: 0, pointerId: 1, bubbles: true});
                   const seen = [getComputedStyle(c).cursor];
                   c.dispatchEvent(new PointerEvent('pointerdown', at(0.25)));
                   seen.push(getComputedStyle(c).cursor);
                   c.dispatchEvent(new PointerEvent('pointermove', at(0.5)));
                   c.dispatchEvent(new PointerEvent('pointerup', at(0.5)));
                   seen.push(getComputedStyle(c).cursor); return seen;""")
        self.assertEqual(cursors, ["default", "ew-resize", "default"])
        text = self.js("return document.querySelector('.sd-readout').textContent;")
        seconds = float(text.split(" s")[0])
        self.assertGreater(seconds, 1.0)
        self.assertIn("beats before the moment", text)
        # The Impact's own groups, a reversed swell among its settings.
        self.js("document.querySelector('[data-kind=impact]').click(); return true;")
        self.assertEqual(self.groups(), ["Hit", "Sub", "Body", "Crack", "Tone", "Ring", "Colour", "Echo and reverb", "Output"])
        self.assertIn("Reverse swell", self.js("return document.querySelector('.sd-group').textContent;"))

    def groups(self):
        return self.js("return [...document.querySelectorAll('.sd-group .group-title')].map(s => s.textContent);")

    def setting(self, key):
        """A setting's row: its slider's or list's value, and whether its group shows it's changed."""
        return self.js(f"""const row = document.querySelector('.ctl[data-key={key}]'), box = row.closest('.sd-group');
                           const input = row.querySelector('input, select');
                           return [input.type === 'checkbox' ? input.checked : input.value, box.classList.contains('changed'),
                                   box.querySelector('.sd-reset').disabled];""")

    def test_a_settings_name_resets_it_and_a_groups_reset_does_the_group(self):
        self.sound_design()
        self.until("document.querySelector('.ctl[data-key=climb]')")
        default = self.setting("climb")
        self.assertEqual(default, ["12", False, True])                   # as it started: no dot, Reset greyed
        self.js("""const r = document.querySelector('.ctl[data-key=climb] input');
                   r.value = -5; r.dispatchEvent(new Event('input')); return true;""")
        self.assertEqual(self.setting("climb"), ["-5", True, False])
        self.js("document.querySelector('.ctl[data-key=climb] .ctl-label').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));"
                " return true;")
        self.assertEqual(self.setting("climb"), default)
        # A list's name too.
        self.js("""const s = document.querySelector('.ctl[data-key=wave] select');
                   s.value = 'square'; s.dispatchEvent(new Event('change')); return true;""")
        self.assertEqual(self.setting("wave")[:2], ["square", True])
        self.js("document.querySelector('.ctl[data-key=wave] .ctl-label').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));"
                " return true;")
        self.assertEqual(self.setting("wave")[:2], ["sawtooth", False])
        # The Tone group's Reset puts back all of it, its switch too.
        self.js("""const set = (k, v) => { const r = document.querySelector(`.ctl[data-key=${k}] input`); r.value = v;
                       r.dispatchEvent(new Event('input')); };
                   set('voices', 6); set('detune', 40);
                   const box = document.querySelector('.ctl[data-key=voices]').closest('.sd-group');
                   const on = box.querySelector('.sd-group-head input'); on.checked = false; on.dispatchEvent(new Event('change'));
                   return true;""")
        self.assertTrue(self.js("return document.querySelector('.ctl[data-key=voices]').closest('.sd-group').classList.contains('off');"))
        self.js("document.querySelector('.ctl[data-key=voices]').closest('.sd-group').querySelector('.sd-reset').click(); return true;")
        self.assertEqual([self.setting("voices")[0], self.setting("detune")[0]], ["3", "14"])
        self.assertFalse(self.js("return document.querySelector('.ctl[data-key=voices]').closest('.sd-group').classList.contains('off');"))
        self.assertEqual(self.setting("voices")[1:], [False, True])

    def test_a_preset_sets_its_settings_and_stays_lit_until_one_changes(self):
        self.sound_design()
        self.until("document.querySelector('.sd-preset')")
        lit = "return [...document.querySelectorAll('.sd-preset')].filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.textContent);"
        self.assertEqual(self.js(lit), ["Classic"])
        self.js("[...document.querySelectorAll('.sd-preset')].find(b => b.textContent === 'Noise sweep').click(); return true;")
        self.assertEqual(self.js(lit), ["Noise sweep"])
        self.assertEqual(self.setting("air_q")[0], "3")
        self.assertTrue(self.js("return document.querySelector('.ctl[data-key=wave]').closest('.sd-group').classList.contains('off');"))
        self.js("""const r = document.querySelector('.ctl[data-key=air] input'); r.value = 10;
                   r.dispatchEvent(new Event('input')); return true;""")
        self.assertEqual(self.js(lit), [])
        # Another preset puts the mini timeline's playhead back at the start.
        readout = "document.querySelector('.sd-readout').textContent"
        self.until(f"/beats before the moment/.test({readout})")
        self.js("""const c = document.querySelector('.sd-wave'), r = c.getBoundingClientRect();
                   const at = {clientX: r.left + r.width * 0.6, clientY: r.top + 40, button: 0, pointerId: 1, bubbles: true};
                   c.dispatchEvent(new PointerEvent('pointerdown', at)); c.dispatchEvent(new PointerEvent('pointerup', at));
                   return true;""")
        self.assertFalse(self.js(f"return {readout}.startsWith('0.00 s');"))
        self.js("[...document.querySelectorAll('.sd-preset')].find(b => b.textContent === 'Cinematic').click(); return true;")
        self.until(f"{readout}.startsWith('0.00 s') && !/Making/.test({readout})")

    def test_the_last_settings_come_back_and_saved_sounds_load_and_save(self):
        self.sound_design()
        self.receive("sd_library", {
            "saved": [{"id": "s1", "name": "Big build", "kind": "riser", "values": {"climb": 20, "voices": 6}, "seed": 7},
                      {"id": "s2", "name": "Thud", "kind": "impact", "values": {"sub": 40}, "seed": 1}],
            "last": {"kind": "riser", "values": {"riser": {"climb": -7}}, "seed": {"riser": 3}}})
        self.until("document.querySelector('.ctl[data-key=climb]')")
        self.assertEqual(self.setting("climb")[0], "-7")                       # as it was left last time
        self.assertEqual(self.js("return document.querySelector('.sd-seed').value;"), "3")
        chips = "return [...document.querySelectorAll('.sd-saved-chip')].map(c => [c.textContent, c.getAttribute('aria-pressed')]);"
        self.assertEqual(self.js(chips), [["Big build", "false"]])                # this sound's only
        self.assertNotIn("null", self.js("return document.querySelector('.sd-saved').textContent;"))
        self.js("document.querySelector('.sd-saved-chip').click(); return true;")
        self.assertEqual([self.setting("climb")[0], self.setting("voices")[0]], ["20", "6"])
        self.assertEqual(self.js(chips), [["Big build", "true"]])
        self.assertEqual(self.js("return document.querySelector('.sd-seed').value;"), "7")
        # Changed from there, then saved: always as a new sound - "Big build" itself stays as it was.
        self.js("""const r = document.querySelector('.ctl[data-key=voices] input'); r.value = 2;
                   r.dispatchEvent(new Event('input')); return true;""")
        self.assertEqual(self.js(chips), [["Big build", "false"]])
        self.assertFalse(self.js("return [...document.querySelectorAll('.sd-saved .btn')].some(b => /changes/i.test(b.textContent));"))
        self.js("document.querySelector('.sd-save').click(); return true;")
        self.until("document.querySelector('.modal input')")
        self.assertEqual(self.js("return document.querySelector('.modal input').value;"), "Big build 2")
        self.js("""const i = document.querySelector('.modal input'); i.value = 'Wide riser';
                   [...document.querySelectorAll('.modal .btn')].find(b => b.textContent === 'Save').click(); return true;""")
        sent = self.js("return window.__sent.filter(s => s[0] === 'sd_saved_put').map(s => [s[1].id, s[1].name, s[1].kind, s[1].seed, s[1].values.voices]);")
        self.assertEqual(len(sent), 1)
        self.assertNotEqual(sent[0][0], "s1")                                     # a new one...
        self.assertEqual(sent[0][1:], ["Wide riser", "riser", 7, 2])
        self.assertEqual([c[0] for c in self.js(chips)], ["Big build", "Wide riser"])
        # Right-click: rename and delete, nothing that writes over it.
        self.js("document.querySelector('.sd-saved-chip').dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, clientX: 40, clientY: 40}));"
                " return true;")
        self.assertEqual(self.js("return [...document.querySelectorAll('.menu-pop button')].map(b => b.textContent);"),
                         ["Rename…", "Delete"])
        # And it places as any sound does - a saved sound's settings, onto the playhead.
        self.js("document.querySelector('.sd-saved-chip').click(); return true;")
        self.receive("playhead", {"frame": 86600})
        self.js("[...document.querySelectorAll('.sd-transport .btn')].find(b => b.textContent === 'Place at the playhead').click(); return true;")
        self.until("window.__sent.some(s => s[0] === 'sd_place')")
        placed = self.js("const d = window.__sent.find(s => s[0] === 'sd_place')[1]; return [d.frame, d.recipe.values.climb, d.recipe.seed, 'replace' in d];")
        self.assertEqual(placed, [86600, 20, 7, False])

    def test_space_plays_and_stops_and_nothing_else(self):
        self.sound_design()
        self.until("/beats before the moment/.test(document.querySelector('.sd-readout').textContent)")
        press = """const t = document.querySelector(%s); t.focus();
                   const down = new KeyboardEvent('keydown', {key: ' ', code: 'Space', bubbles: true, cancelable: true});
                   const up = new KeyboardEvent('keyup', {key: ' ', code: 'Space', bubbles: true, cancelable: true});
                   t.dispatchEvent(down); t.dispatchEvent(up); return [down.defaultPrevented, up.defaultPrevented];"""
        for target in ("'.sd-song'", "'.ctl[data-key=wave] select'", "'.sd-preset'", "'.ctl[data-key=climb] input'"):
            with self.subTest(target):
                self.assertEqual(self.js(press % target), [True, True])     # no list opens, no button goes
        play = "return document.querySelector('.sd-transport .btn:not(.ghost)').textContent;"
        self.until("document.querySelector('.sd-transport .btn:not(.ghost)').textContent === 'Play'"
                   " || document.querySelector('.sd-transport .btn:not(.ghost)').textContent === 'Stop'")
        before = self.js(play)
        self.js(press % "'.sd-wave'")
        self.until(f"document.querySelector('.sd-transport .btn:not(.ghost)').textContent !== {json.dumps(before)}")


if __name__ == "__main__":
    unittest.main()
