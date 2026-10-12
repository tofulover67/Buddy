"""Footage Search: its Resolve calls against a stand-in Media Pool and
timeline (resolve_ext, no Qt), the transcript it reads, and the page with
the model and the index stood in for - nothing is downloaded or run."""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _paths  # noqa: F401
from core.media_search import indexer, model
from core.media_search import store as st
from core.resolve_bridge import ResolveConnectionError
from pages.footage_search import resolve_ext as rx

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


# ---------------------------------------------------------- a stand-in Resolve

class Clip:
    def __init__(self, uid, name, path, kind="Video + Audio", fps="24.000", frames="240"):
        self.uid, self.name = uid, name
        self.props = {"Clip Name": name, "File Path": path, "Type": kind, "FPS": fps, "Frames": frames}

    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return self.name

    def GetClipProperty(self):
        return dict(self.props)


class Bin:
    def __init__(self, name, clips=(), bins=()):
        self.name, self.clips, self.bins = name, list(clips), list(bins)

    def GetName(self):
        return self.name

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.bins


class Item:
    def __init__(self, start, end):
        self.start, self.end = start, end

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end


class Timeline:
    def __init__(self, tracks, fps="24", start=86400, playhead="01:00:10:00", locked=()):
        self.tracks = [list(t) for t in tracks]     # video tracks, V1 first: their items
        self.fps, self.start, self.playhead, self.locked = fps, start, playhead, set(locked)
        self.deleted = []

    def GetName(self):
        return "Edit v3"

    def GetSetting(self, name):
        return self.fps if name == "timelineFrameRate" else "0"

    def GetStartFrame(self):
        return self.start

    def GetCurrentTimecode(self):
        return self.playhead

    def GetTrackCount(self, kind):
        return len(self.tracks)

    def GetIsTrackLocked(self, kind, index):
        return index in self.locked

    def GetIsTrackEnabled(self, kind, index):
        return True

    def GetItemListInTrack(self, kind, index):
        return self.tracks[index - 1]

    def AddTrack(self, kind):
        self.tracks.append([])
        return True

    def DeleteClips(self, items, ripple):
        self.deleted += items
        return True


class Pool:
    def __init__(self, root, timeline, lands_at=None):
        self.root, self.timeline, self.lands_at = root, timeline, lands_at
        self.placed = []
        self.folder = self.selected = None

    def GetRootFolder(self):
        return self.root

    def SetCurrentFolder(self, folder):
        self.folder = folder
        return True

    def SetSelectedClip(self, clip):
        self.selected = clip
        return True

    def AppendToTimeline(self, infos):
        info, = infos
        self.placed.append(info)
        start = info["recordFrame"] if self.lands_at is None else self.lands_at
        item = Item(start, start + (info["endFrame"] - info["startFrame"]))
        self.timeline.tracks[info["trackIndex"] - 1].append(item)
        return [item]


def controller(pool, timeline, project_id="p1"):
    project = type("Project", (), {"GetMediaPool": lambda s: pool, "GetUniqueId": lambda s: project_id,
                                   "GetName": lambda s: "Harbour film", "GetCurrentTimeline": lambda s: timeline})()
    return type("Controller", (), {"current_project": lambda s: project, "resolve": None})()


def media_pool(timeline=None, lands_at=None):
    timeline = timeline or Timeline([[Item(86400, 90000)]])
    root = Bin("Master", [
        Clip("c1", "A001_0001", r"D:\Footage\A001_0001.MXF"),
        Clip("t1", "Edit v3", "", kind="Timeline"),
        Clip("c2", "Logo", r"D:\Graphics\logo.png", kind="Still", fps="", frames="1"),
        Clip("c3", "Raw take", r"D:\Footage\B002_0007.braw"),
        Clip("c4", "Stop motion", r"D:\Footage\sm.[0001-0240].png", kind="Video"),
        Clip("c5", "Voice", r"D:\Sound\voice.wav", kind="Audio"),
    ], [Bin("Day 2", [Clip("c6", "A002_0003", r"D:\Footage\A002_0003.mov", fps="50", frames="500")])])
    return Pool(root, timeline, lands_at), timeline


class ResolveTests(unittest.TestCase):
    def setUp(self):
        rx._SCANNED.update(project=None, objects={}, folders={}, rows={})

    def test_the_scan_lists_footage_and_stills_with_a_file_to_look_at(self):
        pool, timeline = media_pool()
        found = rx.scan(controller(pool, timeline))
        self.assertEqual((found["project_name"], found["timeline"]), ("Harbour film", {"name": "Edit v3", "fps": 24.0}))
        rows = {c["id"]: c for c in found["clips"]}
        self.assertEqual(sorted(rows), ["c1", "c2", "c6"])                  # no timeline, raw, sequence or sound
        self.assertEqual(found["other"], 2)                                 # the raw take and the sequence
        self.assertEqual((rows["c1"]["type"], rows["c1"]["seconds"], rows["c1"]["bin"]), ("Video", 10.0, "Master"))
        self.assertEqual((rows["c2"]["type"], rows["c2"]["seconds"]), ("Image", rx.STILL_SECONDS))
        self.assertEqual((rows["c6"]["bin"], rows["c6"]["fps"], rows["c6"]["seconds"]), ("Master/Day 2", 50.0, 10.0))
        json.dumps(found)                                                   # it crosses to Buddy as JSON

    def test_find_in_media_pool_opens_the_bin_and_selects_the_clip(self):
        pool, timeline = media_pool()
        self.assertEqual(rx.reveal(controller(pool, timeline), "p1", "c6"), "Day 2")      # scans first, in a fresh process
        self.assertEqual((pool.folder.name, pool.selected.uid), ("Day 2", "c6"))
        with self.assertRaises(ResolveConnectionError):
            rx.reveal(controller(pool, timeline, "another project"), "p1", "c6")
        with self.assertRaises(ResolveConnectionError):
            rx.reveal(controller(pool, timeline), "p1", "gone")

    def test_place_at_the_playhead_goes_on_a_free_track_above_v1(self):
        pool, timeline = media_pool(Timeline([[Item(86400, 90000)], []]))
        result = rx.place(controller(pool, timeline), "p1", "c1", source_start=2.5, seconds=5.0)
        self.assertEqual(result, {"track": 2, "timeline": "Edit v3"})
        info, = pool.placed
        self.assertEqual((info["startFrame"], info["endFrame"], info["recordFrame"], info["trackIndex"], info["mediaType"]),
                         (60, 180, 86400 + 240, 2, 1))                      # 2.5 s in, 5 s long, at 01:00:10:00, picture only
        self.assertEqual(timeline.tracks[0], timeline.tracks[0][:1])        # V1 is as it was

    def test_place_over_a_line_counts_the_clips_own_frames(self):
        pool, timeline = media_pool(Timeline([[Item(86400, 90000)], []]))
        rx.place(controller(pool, timeline), "p1", "c6", source_start=4.0, seconds=3.0, record_seconds=12.5)
        info, = pool.placed
        self.assertEqual(info["recordFrame"], 86400 + 300)                  # 12.5 s from the timeline's first frame
        self.assertEqual((info["startFrame"], info["endFrame"]), (200, 350))    # 50 fps footage: 3 s is 150 of its frames

    def test_place_never_runs_past_the_end_of_the_clip(self):
        pool, timeline = media_pool(Timeline([[], []]))
        rx.place(controller(pool, timeline), "p1", "c1", source_start=9.0, seconds=5.0)
        info, = pool.placed
        self.assertEqual((info["startFrame"], info["endFrame"]), (216, 240))

    def test_a_track_that_is_taken_or_locked_is_passed_over_and_one_is_added_if_need_be(self):
        at = 86400 + 240
        pool, timeline = media_pool(Timeline([[], [Item(at + 10, at + 50)], []], locked={3}))
        result = rx.place(controller(pool, timeline), "p1", "c1", 0, 5.0)
        self.assertEqual((result["track"], len(timeline.tracks)), (4, 4))    # V2 has a clip there, V3 is locked: a new V4
        self.assertEqual(len(timeline.tracks[1]), 1)                        # what was on V2 is untouched

    def test_a_clip_that_lands_somewhere_else_is_taken_off_again(self):
        pool, timeline = media_pool(Timeline([[], []]), lands_at=86400)
        with self.assertRaises(ResolveConnectionError) as caught:
            rx.place(controller(pool, timeline), "p1", "c1", 0, 5.0)
        self.assertIn("taken off again", str(caught.exception))
        self.assertEqual(len(timeline.deleted), 1)

    def test_a_still_lasts_as_long_as_the_line(self):
        pool, timeline = media_pool(Timeline([[], []]))
        rx.place(controller(pool, timeline), "p1", "c2", 0, 4.0, record_seconds=0)
        info, = pool.placed
        self.assertEqual((info["startFrame"], info["endFrame"], info["recordFrame"]), (0, 96, 86400))

    def test_frame_counts_between_rates(self):
        self.assertEqual(rx.source_frames_for(120, 24, 24), 120)
        self.assertEqual(rx.source_frames_for(72, 50, 24), 150)
        self.assertEqual(rx.source_frames_for(29, 24, 30), 23)              # never a frame long
        self.assertEqual(rx._timecode_frames("01:00:10:00", 24), 86400 + 240)
        self.assertEqual(rx._timecode_frames("00:01:00;02", 29.97), 1800)   # drop frame

    def test_its_jobs_are_ones_the_reader_process_can_run(self):
        from core import resolve_reader_child as child
        for name in ("footage.scan", "footage.reveal", "footage.place"):
            module, function = child.JOBS[name]
            self.assertTrue(callable(getattr(__import__(module, fromlist=[function]), function)))


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, segments, age=0):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"language": "en", "segments": segments}, f)
        os.utime(path, (time.time() - age, time.time() - age))

    def test_the_newest_transcripts_lines_worth_finding_footage_for(self):
        from pages.footage_search.page import latest_transcript
        self.assertIsNone(latest_transcript(self.dir))
        self.write("Old cut 2026-09-01 090000.json", [{"start": 0, "end": 2, "text": "An older cut of it."}], age=3600)
        self.write("Edit v3 2026-10-12 101500.json", [
            {"start": 1.0, "end": 4.5, "text": " We drove out past the dunes  before dawn."},
            {"start": 5.0, "end": 5.4, "text": " Yeah."},                               # says too little
            {"start": 6.0, "end": 9.0, "text": " The lake was completely still.", "words": []},
            {"start": 9.0, "end": 9.0, "text": " No time at all here."},
            {"text": "No times at all."}])
        name, lines = latest_transcript(self.dir)
        self.assertEqual(name, "Edit v3 2026-10-12 101500")
        self.assertEqual(lines, [{"start": 1.0, "end": 4.5, "text": "We drove out past the dunes before dawn."},
                                 {"start": 6.0, "end": 9.0, "text": "The lake was completely still."}])

    def test_a_damaged_transcript_is_passed_over(self):
        from pages.footage_search.page import latest_transcript
        self.write("Good 2026-10-01 100000.json", [{"start": 0, "end": 3, "text": "Three words here."}], age=60)
        with open(os.path.join(self.dir, "Broken 2026-10-12 100000.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(latest_transcript(self.dir)[0], "Good 2026-10-01 100000")


class RegistryTests(unittest.TestCase):
    def test_it_is_a_tool_under_media_and_assets_not_yet_on_a_mac(self):
        # Read, not imported: importing registry.py pulls in every page.
        source = (_paths.APP / "registry.py").read_text(encoding="utf-8")
        self.assertIn('("Media & Assets", _tool("footage_search", "Footage Search", "Media & Assets", '
                      '"pages.footage_search.page", "FootageSearchPage")),', source)


# ------------------------------------------------------------------ page --

class Mem(dict):
    def save(self):
        pass


class Host:
    def __init__(self, controller=None):
        self.controller = controller
        self.connected = controller is not None
        self.shared_settings = {"theme": "Resolve"}

    def ensure_connected(self):
        return self.controller

    def tool_settings(self, tool_id, defaults=None):
        return Mem(defaults or {})

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")

    def set_busy(self, on, message=None):
        pass


def vec(*head):
    return list(head) + [0.0] * (st.DIM - len(head))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(lambda: [self.app.processEvents() or time.sleep(0.01) for _ in range(5)])
        # Two clips on disk, in an index of their own: the harbour at dawn and a market.
        self.harbour, self.market = str(self.dir / "A001_0001.mov"), str(self.dir / "A002_0003.mov")
        for path in (self.harbour, self.market):
            Path(path).write_bytes(b"footage")
        store = st.Store(self.dir / "index.sqlite", model=model.NAME)
        store.put(self.harbour, "video", [st.Piece(st.KIND_FRAME, vec(1, 0), 0, 2), st.Piece(st.KIND_FRAME, vec(1, 0.1), 40, 2)])
        store.put(self.market, "video", [st.Piece(st.KIND_FRAME, vec(0, 1), 10, 2)])
        self.index = indexer.Indexer(store, ffmpeg=lambda: None)
        words = {"harbour": vec(1, 0), "market": vec(0, 1)}
        patches = [mock.patch.object(indexer, "_SHARED", self.index),
                   mock.patch.object(model, "ready", lambda: (True, "")),
                   mock.patch.object(model, "missing", lambda: []),
                   mock.patch.object(model, "extras", lambda: []),
                   mock.patch.object(model, "gpu", lambda: ""),
                   mock.patch.object(model, "supported", lambda: True),
                   mock.patch.object(model, "embed_texts",
                                     lambda texts, timeout=0: [next((v for w, v in words.items() if w in t), vec(0, 0, 1))
                                                               for t in texts])]
        self.catalog = {"project_id": "p1", "project_name": "Harbour film", "timeline": {"name": "Edit v3", "fps": 24.0},
                        "other": 0, "clips": [
                            {"id": "c1", "name": "A001_0001", "bin": "Master", "path": self.harbour, "type": "Video",
                             "seconds": 60.0, "fps": 24.0, "frames": 1440},
                            {"id": "c2", "name": "A002_0003", "bin": "Master/Day 2", "path": self.market, "type": "Video",
                             "seconds": 30.0, "fps": 24.0, "frames": 720},
                            {"id": "c3", "name": "Offline", "bin": "Master", "path": str(self.dir / "gone.mov"),
                             "type": "Video", "seconds": 5.0, "fps": 24.0, "frames": 120}]}
        self.calls = []
        patches += [mock.patch.object(rx, "scan", lambda controller: self.catalog),
                    mock.patch.object(rx, "reveal", lambda controller, *a: self.calls.append(("reveal", a)) or "Day 2"),
                    mock.patch.object(rx, "place", lambda controller, **k: self.calls.append(("place", k)) or
                                      {"track": 2, "timeline": "Edit v3"})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        from pages.footage_search import page as page_mod
        self.page_mod = page_mod
        self.host = Host(controller=object())
        self.page = page_mod.FootageSearchPage(self.host)
        self.addCleanup(self.page.deleteLater)
        self.addCleanup(self.page.on_app_quitting)
        self.events = []
        self.page.emit = lambda name, payload=None: self.events.append((name, payload))

    def last(self, name):
        return [p for n, p in self.events if n == name][-1]

    def pump(self, until, seconds=10):
        end = time.time() + seconds
        while not until() and time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(until())

    def scanned(self):
        self.page.on_refresh(None)
        self.pump(lambda: self.page.catalog is not None)

    def test_ask_buddy_knows_what_the_tool_does(self):
        from core.tools_kb import get_tool
        info = get_tool("footage_search", [("Media & Assets", self.page_mod.FootageSearchPage)])
        self.assertIsNotNone(info)
        self.assertTrue(info.is_available)

    def test_without_resolve_it_says_to_connect(self):
        self.host.connected = False
        self.page.on_refresh(None)
        self.assertEqual(self.last("state")["problem"], "Connect to Resolve to search its Media Pool.")

    def test_the_status_counts_what_is_indexed_and_what_is_offline(self):
        self.scanned()
        state = self.last("state")
        self.assertEqual((state["project"], state["clips"], state["indexed"], state["waiting"], state["offline"]),
                         ("Harbour film", 3, 2, 0, 1))

    def test_a_description_finds_moments_closest_first(self):
        self.scanned()
        self.page.on_find({"query": "boats in the  harbour"})
        self.pump(lambda: self.page.results is not None and not self.page.finding)
        rows = self.last("results")["rows"]
        self.assertEqual([(r["name"], r["time"]) for r in rows], [("A001_0001", "0:00"), ("A001_0001", "0:40")])
        self.assertEqual(self.last("results")["query"], "boats in the harbour")
        self.page.on_find({"query": ""})
        self.assertIsNone(self.last("results")["rows"])

    def test_find_in_media_pool_and_place_ask_resolve_for_that_clip(self):
        self.scanned()
        self.page.on_reveal({"clip": "c2"})
        self.pump(lambda: self.calls)
        self.assertEqual(self.calls[0], ("reveal", ("p1", "c2")))
        self.page.on_place({"clip": "c1", "at": 40.0})
        self.pump(lambda: len(self.calls) == 2)
        self.assertEqual(self.calls[1], ("place", {"expected_project_id": "p1", "clip_id": "c1", "source_start": 40.0,
                                                   "seconds": self.page_mod.PLACE_SECONDS, "record_seconds": None}))
        self.pump(lambda: any(p["text"] == "Placed on V2." for n, p in self.events if n == "toast"))
        self.page.on_place({"clip": "not in the pool", "at": 0})
        self.assertEqual(len(self.calls), 2)

    def test_broll_suggests_footage_for_each_line_and_places_it_over_the_line(self):
        self.scanned()
        lines = [{"start": 1.0, "end": 4.0, "text": "We set off from the harbour at dawn."},
                 {"start": 20.0, "end": 41.0, "text": "By noon we had reached the market square."}]
        with mock.patch.object(self.page_mod, "latest_transcript", lambda: ("Edit v3 2026-10-12 101500", lines)):
            self.page.on_tab({"tab": "broll"})
            self.pump(lambda: all("picks" in line for line in lines))
        shown = self.last("broll")
        self.assertEqual(shown["name"], "Edit v3 2026-10-12 101500")
        self.assertEqual([line["picks"][0]["name"] for line in shown["lines"]], ["A001_0001", "A002_0003"])
        self.assertEqual(shown["lines"][1]["seconds"], 10.0)                # a long line: ten seconds of footage at most
        self.page.on_place({"clip": "c2", "at": 11.0, "line": 1})
        self.pump(lambda: self.calls)
        self.assertEqual(self.calls[0][1]["record_seconds"], 20.0)
        self.assertEqual(self.calls[0][1]["seconds"], 10.0)

    def test_no_transcript_is_said_not_guessed(self):
        self.scanned()
        with mock.patch.object(self.page_mod, "latest_transcript", lambda: None):
            self.page.on_tab({"tab": "broll"})
        self.assertIsNone(self.last("broll")["lines"])

    def test_index_queues_the_clips_that_are_on_disk(self):
        self.scanned()
        with mock.patch.object(self.index, "add", return_value=2) as add:
            self.page.on_index(None)
        paths, lengths, store = add.call_args[0]
        self.assertEqual(sorted(paths), sorted([self.harbour, self.market]))        # not the offline one
        self.assertEqual(lengths[self.harbour], 60.0)
        self.assertIs(store, self.index.store)                                      # Buddy's own index, to start with

    # ------------------------------------------------- where the index is --

    def place(self, folder):
        from PySide6.QtWidgets import QFileDialog
        with mock.patch.object(QFileDialog, "getExistingDirectory", lambda *_a, **_k: folder):
            self.page.on_index_place({"where": "folder"})

    def test_a_project_can_keep_its_index_in_a_folder_and_takes_its_entries_along(self):
        self.scanned()
        job = str(self.dir / "Harbour film")
        os.makedirs(job)
        self.place(job)
        state = self.last("state")
        self.assertEqual((state["index_folder"], state["indexed"], state["waiting"]), (job, 2, 0))   # nothing to read again
        self.assertTrue(os.path.isfile(os.path.join(job, st.INDEX_NAME)))
        self.assertEqual(self.page.settings["index_folders"], {"p1": {"folder": job, "name": "Harbour film"}})
        self.assertIn(job, [p["text"] for n, p in self.events if n == "toast"][-1])
        # Searching and indexing now go to that index.
        self.page.on_find({"query": "boats in the harbour"})
        self.pump(lambda: self.page.results is not None and not self.page.finding)
        self.assertEqual([r["name"] for r in self.last("results")["rows"]], ["A001_0001", "A001_0001"])
        with mock.patch.object(self.index, "add", return_value=0) as add:
            self.page.on_index(None)
        self.assertEqual(str(add.call_args[0][2].path), os.path.join(job, st.INDEX_NAME))
        self.assertEqual(self.index.store.counts(), (2, 3))                         # Buddy's own index is as it was

    def test_another_project_is_still_in_buddys_own_index(self):
        self.scanned()
        job = str(self.dir / "Harbour film")
        os.makedirs(job)
        self.place(job)
        self.catalog = dict(self.catalog, project_id="p2", project_name="Market film")
        self.scanned_again()
        self.assertEqual(self.last("state")["index_folder"], "")

    def scanned_again(self):
        self.page.catalog = None
        self.scanned()

    def test_back_to_buddys_folder_brings_the_entries_back(self):
        self.scanned()
        job = str(self.dir / "Harbour film")
        os.makedirs(job)
        self.place(job)
        self.index.store.forget([self.harbour, self.market])                        # as if never indexed there
        self.page.on_index_place({"where": "buddy"})
        state = self.last("state")
        self.assertEqual((state["index_folder"], state["indexed"]), ("", 2))
        self.assertEqual(self.page.settings.get("index_folders"), {})

    def test_a_folder_that_is_not_there_is_said_and_nothing_is_made_in_its_place(self):
        self.scanned()
        job = str(self.dir / "Harbour film")
        os.makedirs(job)
        self.place(job)
        shutil.rmtree(job)                                                          # the drive unplugged
        self.scanned_again()
        state = self.last("state")
        self.assertIn("isn't there now", state["index_trouble"])
        self.assertEqual(state["indexed"], 0)
        self.page.on_find({"query": "boats in the harbour"})
        self.assertIsNone(self.last("results")["rows"])
        with mock.patch.object(self.index, "add") as add:
            self.page.on_index(None)
        add.assert_not_called()
        self.assertFalse(os.path.exists(job))

    def test_not_while_the_index_is_filling(self):
        self.scanned()
        with mock.patch.object(self.index, "status", lambda: {"running": True, "done": 0, "total": 2, "current": "A001",
                                                              "part": 0.0, "failed": 0, "needs_ffmpeg": 0, "error": ""}):
            self.place(str(self.dir))
        self.assertEqual(self.last("toast")["text"], "Stop indexing first.")
        self.assertFalse(self.page.settings.get("index_folders"))


if __name__ == "__main__":
    unittest.main()
