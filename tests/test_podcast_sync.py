"""Podcast Sync (pages/podcast/sync/): telling the devices apart, the fine
pass and its drift measurement on synthetic audio, the stacked layout, the
whole run on hand-written OTIO documents, and the page against a fake
Resolve - nothing here reads real media or talks to a real Resolve."""

import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.podcast.sync import engine
from pages.project_setup import otio_engine as oe

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:  # pragma: no cover
    HAVE_NUMPY = False

RATE = 24.0


def _rt(value, rate=RATE):
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": value}


def _range(start, duration, rate=RATE):
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(start, rate), "duration": _rt(duration, rate)}


def clip(name, seconds, path, link=None, tc=0.0):
    """An OTIO clip of `seconds`, whose file starts at timecode `tc` seconds."""
    media = {"OTIO_SCHEMA": "ExternalReference.1", "target_url": path,
             "available_range": _range(tc * RATE, seconds * RATE)}
    metadata = {"Resolve_OTIO": {"Link Group ID": link}} if link is not None else {}
    return {"OTIO_SCHEMA": "Clip.2", "name": name, "metadata": metadata,
            "source_range": _range(tc * RATE, seconds * RATE),
            "media_references": {"DEFAULT_MEDIA": media}, "active_media_reference_key": "DEFAULT_MEDIA"}


def document(video, audio):
    """Clips one after another on V1 and on A1 - as a timeline made from
    Media Pool clips has them."""
    tracks = [oe._make_track("Video", "Video 1", list(video)), oe._make_track("Audio", "Audio 1", list(audio))]
    return {"OTIO_SCHEMA": "Timeline.1", "name": "T", "metadata": {},
            "tracks": {"OTIO_SCHEMA": "Stack.1", "children": tracks}}


def podcast(tc=True):
    """Two cameras (Cam A split into two files) and a two-track recorder."""
    t = (lambda s: s) if tc else (lambda _s: 0.0)
    cams = [("A001_0001", 600, "D:/Footage/CamA/A001_0001.MXF", t(36000.0)),
            ("A001_0002", 400, "D:/Footage/CamA/A001_0002.MXF", t(36600.0)),
            ("B001_0001", 1000, "D:/Footage/CamB/B001_0001.MXF", t(36010.0))]
    video, audio = [], []
    for i, (name, seconds, path, start) in enumerate(cams):
        video.append(clip(name, seconds, path, link=i + 1, tc=start))
        audio.append(clip(name, seconds, path, link=i + 1, tc=start))
    for name, path in (("ZOOM0001_Tr1", "D:/Footage/Recorder/ZOOM0001_Tr1.WAV"),
                       ("ZOOM0001_Tr2", "D:/Footage/Recorder/ZOOM0001_Tr2.WAV")):
        audio.append(clip(name, 1100, path, tc=t(35995.0)))
    return document(video, audio)


def placed(doc):
    """{(kind, track name): [(clip name, start seconds)]} of a built document."""
    out = {}
    for track in doc["tracks"]["children"]:
        position, items = 0.0, []
        for child in track["children"]:
            if child["OTIO_SCHEMA"].startswith("Clip"):
                items.append((child["name"], position / RATE))
            position += child["source_range"]["duration"]["value"]
        out[(track["kind"], track["name"])] = items
    return out


class DeviceTests(unittest.TestCase):
    def test_a_device_is_its_folder_or_what_its_name_says(self):
        self.assertEqual(engine.device_of("D:/Footage/CamA/A001_0001.MXF"), "CamA")
        self.assertEqual(engine.device_of(r"D:\Footage\Sony\PRIVATE\M4ROOT\CLIP\C0001.MP4"), "Sony")
        self.assertEqual(engine.device_of("D:/Footage/A001_0003.MXF"), "A")
        self.assertEqual(engine.device_of("/Users/you/Movies/Podcast/Audio/Tess lav.wav"), "Tess lav")

    def test_a_name_without_its_clip_counters_is_the_device(self):
        for path, device in (("A001_C003_TESS.MOV", "TESS"), ("B001_C002_SAM.MOV", "SAM"), ("C0001.MP4", "C"),
                             ("C0010.MP4", "C"), ("GH010023.MP4", "GH"), ("DJI_0001.MP4", "DJI"),
                             ("ZOOM0002_Tr1.WAV", "ZOOM"), ("Ep 12 Cam A.mp4", "Ep Cam A"), ("Cam1.mov", "Cam1"),
                             ("Take 3.wav", "Take")):
            with self.subTest(path):
                self.assertEqual(engine.name_device("D:/Shows/" + path), device)

    def test_an_episode_folder_holding_everything_isnt_every_recordings_device(self):
        doc = document([clip("A001_C003_TESS", 600, "D:/Shows/Ep 12/A001_C003_TESS.MOV", link=1),
                        clip("B001_C002_SAM", 600, "D:/Shows/Ep 12/B001_C002_SAM.MOV", link=2)],
                       [clip("A001_C003_TESS", 600, "D:/Shows/Ep 12/A001_C003_TESS.MOV", link=1),
                        clip("B001_C002_SAM", 600, "D:/Shows/Ep 12/B001_C002_SAM.MOV", link=2),
                        clip("TESS_LAV", 610, "D:/Shows/Ep 12/TESS_LAV.WAV"),
                        clip("SAM_LAV", 610, "D:/Shows/Ep 12/SAM_LAV.WAV")])
        devices = engine.group_devices(engine.read_clips(doc))
        self.assertEqual(sorted((d.name, d.kind) for d in devices),
                         [("SAM", "camera"), ("SAM LAV", "mic"), ("TESS", "camera"), ("TESS LAV", "mic")])

    def test_a_folder_holding_several_cameras_goes_by_the_names(self):
        doc = document([clip("Cam A", 600, "D:/Shows/Ep 12/Cameras/Cam A.mp4", link=1),
                        clip("Cam B", 600, "D:/Shows/Ep 12/Cameras/Cam B.mp4", link=2)],
                       [clip("Cam A", 600, "D:/Shows/Ep 12/Cameras/Cam A.mp4", link=1),
                        clip("Cam B", 600, "D:/Shows/Ep 12/Cameras/Cam B.mp4", link=2),
                        clip("ZOOM0001_Tr1", 610, "D:/Shows/Ep 12/Zoom/ZOOM0001_Tr1.WAV")])
        devices = engine.group_devices(engine.read_clips(doc))
        self.assertEqual(sorted(d.name for d in devices), ["Cam A", "Cam B", "Zoom"])

    def test_a_multitrack_recorders_files_are_lanes(self):
        self.assertEqual(engine.lane_of("D:/Rec/ZOOM0001_Tr1.WAV"), "TR1")
        self.assertEqual(engine.lane_of("D:/Rec/ZOOM0001_LR.WAV"), "LR")
        self.assertEqual(engine.lane_of("D:/Rec/Take 3_MIC2.wav"), "MIC2")
        self.assertEqual(engine.lane_of("D:/Footage/CamA/A001_0001.MXF"), "")

    def test_devices_are_grouped_renamed_and_ordered(self):
        clips = engine.read_clips(podcast())
        devices = engine.group_devices(clips)
        self.assertEqual([(d.name, d.kind, len(d.clips)) for d in devices],
                         [("CamA", "camera", 2), ("CamB", "camera", 1), ("Recorder", "mic", 2)])
        # Split files in recording order.
        self.assertEqual([c.name for c in devices[0].clips], ["A001_0001", "A001_0002"])
        renamed = {c.path: "Wide" for c in clips if c.device == "CamB"}
        devices = engine.group_devices(clips, renamed, order=["Wide", "CamA"])
        self.assertEqual([d.name for d in devices], ["Wide", "CamA", "Recorder"])

    def test_the_master_is_the_recorder_unless_one_is_chosen(self):
        devices = engine.group_devices(engine.read_clips(podcast()))
        self.assertEqual(engine.master_device(devices).name, "Recorder")
        self.assertEqual(engine.master_device(devices, "CamB").name, "CamB")


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.doc = podcast()
        self.clips = engine.read_clips(self.doc)
        self.devices = engine.group_devices(self.clips)
        positions, _w, _u = engine.by_timecode(self.clips)
        self.positions = positions

    def test_timecode_puts_every_device_on_its_own_tracks(self):
        out, tracks = engine.layout(self.doc, self.clips, self.devices, self.positions)
        got = placed(out)
        self.assertEqual([t["name"] for t in tracks if t["kind"] == "Video"], ["CamA", "CamB"])
        # Mics first, then the cameras' own sound.
        self.assertEqual([t["name"] for t in tracks if t["kind"] == "Audio"],
                         ["Recorder TR1", "Recorder TR2", "CamA", "CamB"])
        self.assertEqual(got[("Video", "CamA")], [("A001_0001", 5.0), ("A001_0002", 605.0)])
        self.assertEqual(got[("Video", "CamB")], [("B001_0001", 15.0)])
        self.assertEqual(got[("Audio", "Recorder TR1")], [("ZOOM0001_Tr1", 0.0)])
        self.assertEqual(got[("Audio", "CamA")], [("A001_0001", 5.0), ("A001_0002", 605.0)])

    def test_camera_sound_can_be_turned_off_or_left_out(self):
        _out, tracks = engine.layout(self.doc, self.clips, self.devices, self.positions, "muted")
        self.assertEqual([t["enabled"] for t in tracks if t["kind"] == "Audio"], [True, True, False, False])
        out, tracks = engine.layout(self.doc, self.clips, self.devices, self.positions, "none")
        self.assertEqual([t["name"] for t in tracks if t["kind"] == "Audio"], ["Recorder TR1", "Recorder TR2"])
        self.assertEqual(len([c for c in out["tracks"]["children"] if c["kind"] == "Audio"]), 2)

    def test_an_unplaced_clip_goes_after_the_rest(self):
        positions = dict(self.positions)
        lost = next(c for c in self.clips if c.name == "B001_0001")
        del positions[lost.id]
        out, _tracks = engine.layout(self.doc, self.clips, self.devices, positions)
        start = placed(out)[("Video", "CamB")][0][1]
        self.assertGreaterEqual(start, 1100.0 + engine.UNSYNCED_GAP_S - 1)

    def test_overlapping_clips_of_one_device_get_a_second_track(self):
        positions = dict(self.positions)
        second = next(c for c in self.clips if c.name == "A001_0002")
        positions[second.id] = positions[second.id] - 100       # now inside the first file
        _out, tracks = engine.layout(self.doc, self.clips, self.devices, positions)
        self.assertEqual([t["name"] for t in tracks if t["kind"] == "Video"], ["CamA (1)", "CamA (2)", "CamB"])


class RunTests(unittest.TestCase):
    def test_a_timecode_run_without_sound(self):
        out, result = engine.sync(podcast(), "timecode", fine=False)
        self.assertEqual(result["placed"], 5)
        self.assertEqual(result["unplaced"], [])
        self.assertEqual(len([t for t in result["tracks"] if t["kind"] == "Video"]), 2)
        self.assertIn(("Video", "CamA"), placed(out))
        self.assertEqual(result["warnings"], [])          # a recorder's Tr1 and Tr2 overlap by design

    def test_one_camera_claiming_one_time_twice_is_two_days(self):
        doc = podcast()
        for track in doc["tracks"]["children"]:                         # A001_0002 back inside A001_0001
            second = track["children"][1]
            for part in (second["source_range"], second["media_references"]["DEFAULT_MEDIA"]["available_range"]):
                part["start_time"]["value"] = 36100.0 * RATE
        _out, result = engine.sync(doc, "timecode", fine=False)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("CamA", result["warnings"][0])

    def test_no_timecode_says_so(self):
        with self.assertRaises(oe.OtioError):
            engine.sync(podcast(tc=False), "timecode", fine=False)

    def test_sound_needs_ffmpeg(self):
        with self.assertRaises(oe.OtioError):
            engine.sync(podcast(tc=False), "waveform", ffmpeg_path=None)

    def test_without_ffmpeg_fine_tuning_is_skipped_and_said(self):
        _out, result = engine.sync(podcast(), "timecode", ffmpeg_path=None, fine=True)
        self.assertFalse(result["fine"])
        self.assertTrue(any("ffmpeg" in w for w in result["warnings"]))

    def test_a_sound_run_places_by_the_groups(self):
        doc = podcast(tc=False)
        clips = engine.read_clips(doc)
        ids = {c.name: c.id for c in clips}
        group = {ids["ZOOM0001_Tr1"]: 0.0, ids["ZOOM0001_Tr2"]: 0.0, ids["A001_0001"]: 5.0,
                 ids["A001_0002"]: 605.0, ids["B001_0001"]: 15.0}
        with mock.patch.object(oe, "build_sync_groups", return_value=([group], 5)):
            out, result = engine.sync(doc, "waveform", ffmpeg_path="ffmpeg", fine=False)
        self.assertEqual(result["placed"], 5)
        self.assertEqual(placed(out)[("Video", "CamA")], [("A001_0001", 5.0), ("A001_0002", 605.0)])

    def test_timecode_then_sound_places_a_recorder_with_no_timecode(self):
        doc = podcast()
        # The recorder has no timecode: its files start at 00:00:00:00.
        for child in doc["tracks"]["children"][1]["children"]:
            if child["name"].startswith("ZOOM"):
                child["source_range"]["start_time"]["value"] = 0.0
                child["media_references"]["DEFAULT_MEDIA"]["available_range"]["start_time"]["value"] = 0.0
        clips = engine.read_clips(doc)
        ids = {c.name: c.id for c in clips}
        group = {ids["ZOOM0001_Tr1"]: 0.0, ids["A001_0001"]: 5.0, ids["B001_0001"]: 15.0}
        with mock.patch.object(oe, "build_sync_groups", return_value=([group], 5)), \
                mock.patch.object(engine, "refine", side_effect=lambda c, d, p, *a, **k: (p, {})):
            out, result = engine.sync(doc, "timecode", ffmpeg_path="ffmpeg", fine=True)
        got = placed(out)
        self.assertEqual(got[("Audio", "Recorder TR1")], [("ZOOM0001_Tr1", 0.0)])
        self.assertEqual(got[("Video", "CamA")][0], ("A001_0001", 5.0))
        self.assertEqual([r["clip"] for r in result["unplaced"]], ["ZOOM0001_Tr2"])


@unittest.skipUnless(HAVE_NUMPY, "needs numpy")
class FinePassTests(unittest.TestCase):
    """A master recorder and a camera whose clock runs 50 ppm fast, as
    synthetic "speech": bursts of noise. The camera's first-pass position is
    15 ms off."""
    SR = engine.REFINE_RATE

    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(7)
        seconds = 1200
        n = seconds * cls.SR
        envelope = np.repeat(rng.random(seconds * 4) > 0.45, cls.SR // 4).astype(np.float64)
        envelope = np.convolve(envelope, np.ones(200) / 200, mode="same")
        cls.master = rng.standard_normal(n) * envelope * rng.uniform(0.3, 1.0, n // 1000 + 1).repeat(1000)[:n]
        cls.offset, cls.error = 100.0, 50e-6

    def loader(self, path, start, seconds, rate):
        count = int(round(seconds * rate))
        if path.endswith("REC.WAV"):
            first = int(round(start * rate))
            return self.master[first:first + count].copy()
        times = self.offset + (start + np.arange(count) / rate) * (1 + self.error)
        noise = np.random.default_rng(int(start * 10)).standard_normal(count) * 0.05
        return 0.6 * np.interp(times * rate, np.arange(len(self.master)), self.master) + noise

    def test_best_lag_finds_a_shift_to_a_fraction_of_a_millisecond(self):
        ref = self.master[10 * self.SR:31 * self.SR]
        sample = self.master[int(10.3125 * self.SR):int(30.3125 * self.SR)]
        at, standout = engine.best_lag(ref, sample, self.SR)
        self.assertAlmostEqual(at, 0.3125, delta=0.0005)
        self.assertGreater(standout, engine.REFINE_MIN_STANDOUT)

    def test_fit_line_ignores_one_bad_check(self):
        slope, value = engine.fit_line([(0, 0.0), (100, 0.01), (200, 0.02), (300, 0.5), (400, 0.04)])
        self.assertAlmostEqual(slope, 0.0001, places=6)
        self.assertAlmostEqual(value, 0.0, places=6)

    def test_the_fine_pass_places_it_and_measures_the_drift(self):
        doc = document([clip("C0001", 900, "D:/Footage/CamA/C0001.MP4", link=1)],
                       [clip("C0001", 900, "D:/Footage/CamA/C0001.MP4", link=1),
                        clip("REC", 1200, "D:/Footage/Recorder/REC.WAV")])
        clips = engine.read_clips(doc)
        devices = engine.group_devices(clips)
        cam = next(c for c in clips if c.device == "CamA")
        rec = next(c for c in clips if c.device == "Recorder")
        positions = {rec.id: 0.0, cam.id: self.offset + 0.015}
        out, drift = engine.refine(clips, devices, positions, None, lambda *a: None, loader=self.loader)
        # Placed by the middle of what it shares: 450 s in, 22.5 ms on.
        self.assertAlmostEqual(out[cam.id], self.offset + self.error * 450, delta=0.001)
        self.assertEqual(out[rec.id], 0.0)
        self.assertAlmostEqual(drift[cam.id]["ms_per_hour"], 180.0, delta=8.0)
        self.assertEqual(drift[cam.id]["against"], "Recorder")
        self.assertEqual(drift[cam.id]["checks"], 5)

    def test_a_drifting_clip_is_flagged_in_the_result(self):
        doc = document([clip("C0001", 900, "D:/Footage/CamA/C0001.MP4", link=1, tc=36100.0)],
                       [clip("C0001", 900, "D:/Footage/CamA/C0001.MP4", link=1, tc=36100.0),
                        clip("REC", 1200, "D:/Footage/Recorder/REC.WAV", tc=36000.0)])
        _out, result = engine.sync(doc, "timecode", ffmpeg_path="ffmpeg", fine=True, loader=self.loader)
        self.assertEqual(result["master"], "Recorder")
        (row,) = result["drift"]
        self.assertTrue(row["warn"])                     # 45 ms over the file: more than a frame
        self.assertAlmostEqual(row["frames"], 0.045 * RATE, delta=0.2)
        self.assertEqual(engine.device_drift(result["drift"]), {"CamA": row["ms_per_hour"]})


# ------------------------------------------------------------------ page --

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class _Settings(dict):
    def save(self):
        pass


class _Item:
    def __init__(self, name, kind="Video + Audio"):
        self.name, self.kind = name, kind

    def GetClipProperty(self, key):
        return self.kind if key == "Type" else ""

    def GetName(self):
        return self.name


class _Timeline:
    def __init__(self, name):
        self.name = name
        self.renamed, self.disabled = {}, []

    def GetName(self):
        return self.name

    def SetTrackName(self, kind, number, name):
        self.renamed[(kind, number)] = name
        return True

    def SetTrackEnable(self, kind, number, on):
        self.disabled.append((kind, number))
        return True


class _Folder:
    def __init__(self, clips):
        self.clips = clips

    def GetName(self):
        return "Episode 4"

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return []


class _Pool:
    def __init__(self, project):
        self.project = project
        self.folder = _Folder([_Item("A001_0001"), _Item("ZOOM0001_Tr1", "Audio"), _Item("Timeline 1", "Timeline")])
        self.selected = []
        self.made, self.deleted = [], []

    def GetSelectedClips(self):
        return self.selected

    def GetCurrentFolder(self):
        return self.folder

    def GetRootFolder(self):
        return self.folder

    def SetCurrentFolder(self, folder):
        return True

    def CreateTimelineFromClips(self, name, clips):
        timeline = _Timeline(name)
        self.made.append((name, [c.GetName() for c in clips]))
        self.project.current = timeline
        return timeline

    def DeleteTimelines(self, timelines):
        self.deleted.extend(t.GetName() for t in timelines)
        return True


class _Project:
    def __init__(self):
        self.current = _Timeline("Edit")
        self.pool = _Pool(self)

    def GetMediaPool(self):
        return self.pool

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True

    def GetTimelineCount(self):
        return 1

    def GetTimelineByIndex(self, _i):
        return self.current


class _Controller:
    def __init__(self, project):
        self.project = project
        self.imported = []

    def get_project(self):
        return self.project

    def get_current_bin(self):
        return self.project.pool.folder

    def get_bin_clips(self, folder, recursive=False):
        return [c for c in folder.GetClipList() if c.GetClipProperty("Type") != "Timeline"]

    def get_current_timeline(self):
        return self.project.current

    def export_timeline_otio(self, timeline, path):
        import json
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(podcast(), fh)
        return True

    def import_timeline_file(self, path, name):
        timeline = _Timeline(name)
        self.imported.append(name)
        return timeline


class _Host:
    def __init__(self):
        self.controller = object()
        self.busy = []
        self.shared_settings = {"theme": "Resolve"}

    def tool_settings(self, tool_id, defaults=None):
        return _Settings(defaults or {})

    def ensure_connected(self):
        return self.controller

    def set_busy(self, on, text=None):
        self.busy.append(on)

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve", None)

    def __getattr__(self, name):
        return lambda *a, **k: None


@unittest.skipUnless(HAVE_QT, "needs PySide6")
class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.home = tempfile.TemporaryDirectory()
        os.environ["BUDDY_HOME"] = cls.home.name

    def setUp(self):
        from pages.podcast.sync import page as sync_page
        self.mod = sync_page
        self.project = _Project()
        self.controller = _Controller(self.project)
        patcher = mock.patch("pages.project_setup.resolve_ext.ProjectSetupController",
                             lambda _shell: self.controller)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.page = sync_page.PodcastSyncPage(_Host())
        self.sent = []
        self.page.emit = lambda name, payload=None: self.sent.append((name, payload))

    def last(self, name):
        return next(p for n, p in reversed(self.sent) if n == name)

    def test_nothing_selected_says_so(self):
        self.page.on_scan()
        self.assertIn("Select the podcast's clips", self.last("alert")["text"])

    def test_reading_the_bin_uses_a_temporary_timeline_and_deletes_it(self):
        self.page.on_option({"key": "source", "value": "bin"})
        before = self.project.current
        self.page.on_scan()
        (name, clips), = self.project.pool.made
        self.assertEqual(clips, ["A001_0001", "ZOOM0001_Tr1"])        # the timeline left out
        self.assertEqual(self.project.pool.deleted, [name])
        self.assertIs(self.project.current, before)
        devices = self.last("devices")
        self.assertEqual([d["name"] for d in devices["devices"]], ["CamA", "CamB", "Recorder"])
        self.assertEqual(devices["master"], "Recorder")
        self.assertEqual(devices["what"], {"kind": "bin", "name": "Episode 4"})

    def test_renaming_moving_and_ordering_devices(self):
        self.page.on_option({"key": "source", "value": "timeline"})
        self.page.on_scan()
        self.page.on_rename_device({"device": "CamB", "name": "  Wide   shot "})
        names = [d["name"] for d in self.last("devices")["devices"]]
        self.assertEqual(names, ["CamA", "Wide shot", "Recorder"])
        self.page.on_move_device({"device": "Wide shot", "step": -1})
        self.assertEqual([d["name"] for d in self.last("devices")["devices"]], ["Wide shot", "CamA", "Recorder"])
        path = "D:/Footage/CamA/A001_0002.MXF"
        self.page.on_move_clip({"path": path, "device": "__new__"})
        names = [d["name"] for d in self.last("devices")["devices"]]
        self.assertIn("CamA (2)", names)
        self.page.on_master({"device": "Wide shot"})
        self.assertEqual(self.last("devices")["master"], "Wide shot")

    def test_a_sync_runs_on_a_worker_and_imports_a_new_timeline(self):
        self.page.on_option({"key": "source", "value": "timeline"})
        self.page.on_option({"key": "fine", "value": False})
        self.page.on_option({"key": "camera_audio", "value": "muted"})
        with mock.patch.object(self.mod.PodcastSyncPage, "_ffmpeg", return_value=None):
            self.page.on_sync()
            self.page._worker.wait(10000)
            for _ in range(20):
                self.app.processEvents()
        result = self.last("result")
        self.assertEqual(self.controller.imported, ["Edit (Podcast Sync)"])
        self.assertEqual(result["placed"], 5)
        self.assertEqual(result["timeline"], "Edit (Podcast Sync)")
        self.assertIsNone(self.last("job"))
        self.assertIsNone(self.page.working_on())

    def test_sound_without_ffmpeg_is_refused(self):
        self.page.on_option({"key": "method", "value": "waveform"})
        with mock.patch.object(self.mod.PodcastSyncPage, "_ffmpeg", return_value=None):
            self.page.on_sync()
        self.assertEqual(self.last("alert")["title"], "ffmpeg is needed")
        self.assertIsNone(self.page._worker)


if __name__ == "__main__":
    unittest.main()
