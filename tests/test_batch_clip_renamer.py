"""Batch Clip Renamer: the rename rules (renamer.py, no Qt) and the page
driven against fake Media Pool clips - never a real Resolve."""

import json
import os
import threading
import unittest

import _paths  # noqa: F401
from pages.batch_clip_renamer import renamer, resolve_ext

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    from _child_reader import ChildReader, load_without_qt
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

# A big rename and its Undo in the Resolve reader's process: the lines
# core/resolve_reader_child.py JOBS has for them.
JOBS = {
    "renamer.rename": ("pages.batch_clip_renamer.resolve_ext", "rename"),
    "renamer.undo": ("pages.batch_clip_renamer.resolve_ext", "undo"),
}


def seq(base, start="1"):
    return {"mode": "sequential", "base": base, "start": start}


def rep(find, replace, match_case=True):
    return {"mode": "replace", "find": find, "replace": replace, "match_case": match_case}


class RuleTests(unittest.TestCase):
    def test_sequential_numbering(self):
        rows, problem = renamer.plan(["a", "b", "c"], seq("  Drone ", "9"))
        self.assertIsNone(problem)
        self.assertEqual([r["new"] for r in rows], ["Drone_09", "Drone_10", "Drone_11"])
        self.assertTrue(all(r["changed"] for r in rows))
        rows, _ = renamer.plan(["x"] * 3, seq("S", "99"))
        self.assertEqual(rows[-1]["new"], "S_101")

    def test_start_number(self):
        self.assertEqual(renamer.parse_start(""), 1)
        self.assertEqual(renamer.parse_start(" 7 "), 7)
        self.assertEqual(renamer.parse_start("0"), 0)
        for bad in ("-1", "1.5", "one"):
            self.assertIsNone(renamer.parse_start(bad), bad)
        rows, problem = renamer.plan(["a"], seq("B", "x"))
        self.assertEqual(problem[0], "start")
        self.assertFalse(rows[0]["changed"])

    def test_an_empty_field_is_a_quiet_problem(self):
        self.assertEqual(renamer.plan(["a"], seq(""))[1], ("base", ""))
        self.assertEqual(renamer.plan(["a"], rep("", "x"))[1], ("find", ""))

    def test_renaming_to_the_same_name_is_not_a_change(self):
        rows, _ = renamer.plan(["S_01", "S_02"], seq("S"))
        self.assertFalse(any(r["changed"] for r in rows))

    def test_find_and_replace(self):
        rows, _ = renamer.plan(["A001_cam", "B002_CAM", "other"], rep("cam", "Camera"))
        self.assertEqual([r["new"] for r in rows], ["A001_Camera", "B002_CAM", "other"])
        rows, _ = renamer.plan(["A001_cam", "B002_CAM"], rep("cam", "Camera", match_case=False))
        self.assertEqual([r["new"] for r in rows], ["A001_Camera", "B002_Camera"])

    def test_find_text_is_literal_not_a_pattern(self):
        rows, _ = renamer.plan(["a.b", "axb"], rep(".", "_"))
        self.assertEqual([r["new"] for r in rows], ["a_b", "axb"])
        rows, _ = renamer.plan(["x"], rep("x", r"\1\g<0>"))
        self.assertEqual(rows[0]["new"], r"\1\g<0>")

    def test_a_name_is_never_emptied(self):
        rows, _ = renamer.plan(["take", "take_2"], rep("take", ""))
        self.assertEqual((rows[0]["new"], rows[0]["changed"], rows[0]["skipped"]), ("take", False, "would be empty"))
        self.assertEqual(rows[1]["new"], "_2")

    def test_duplicates(self):
        rows, _ = renamer.plan(["a_1", "b_1", "c_2"], rep("a_", "b_"))
        self.assertEqual(renamer.duplicate_names(rows), ["b_1"])


# ---------------------------------------------------------- fake Resolve --

class Clip:
    def __init__(self, uid, name, kind="Video", accept=True):
        self.uid, self.name, self.kind, self.accept = uid, name, kind, accept

    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return self.name

    def GetClipProperty(self, key):
        return {"Clip Name": self.name, "Type": self.kind}[key]

    def SetClipProperty(self, key, value):
        if key == "Clip Name" and self.accept:
            self.name = value
            return True
        return False


class Folder:
    def __init__(self, clips, name="Day 1", subs=()):
        self.clips, self.name, self.subs = clips, name, list(subs)

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return "folder-1"

    def GetClipList(self):
        return list(self.clips)

    def GetSubFolderList(self):
        return list(self.subs)


class Pool:
    def __init__(self, folder):
        self.folder, self.selected = folder, []
        self.root = Folder([], name="Master", subs=[folder])

    def GetCurrentFolder(self):
        return self.folder

    def GetRootFolder(self):
        return self.root

    def GetSelectedClips(self):
        return list(self.selected)


class Project:
    def __init__(self, pool):
        self.pool = pool

    def GetMediaPool(self):
        return self.pool


class Controller:
    def __init__(self, clips):
        self.pool = Pool(Folder(clips))

    def current_project(self):
        return Project(self.pool)


class Host:
    def __init__(self, controller):
        self.controller, self.connected = controller, True
        self.shared_settings = {"theme": "Resolve"}
        self.busy, self.messages, self.pumped = [], [], []

    def ensure_connected(self):
        return self.controller

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")

    def set_busy(self, on, message=None):
        self.busy.append(on)
        if on:
            self.messages.append(message)

    def pump_busy(self, message=None):
        self.pumped.append(message)


class JobTests(unittest.TestCase):
    """The rename and its Undo as the Resolve reader's process makes them:
    from plain args, with plain answers - and an Undo made in a process
    that didn't rename finds the clips again by their ids."""

    def setUp(self):
        self.clips = [Clip("1", "A001"), Clip("2", "A002"), Clip("t", "Timeline 1", kind="Timeline"),
                      Clip("3", "B001")]
        self.controller = Controller(self.clips)
        self.addCleanup(resolve_ext._RENAMED.clear)

    def plain(self, value):
        return json.loads(json.dumps(value))

    def test_a_rename_from_plain_args(self):
        preview = resolve_ext.preview(self.controller, "bin")
        args = self.plain({"scope": "bin", "keys": preview["keys"], "names": preview["names"],
                           "inputs": rep("A0", "Cam_")})
        self.clips[1].accept = False
        result = resolve_ext.rename(self.controller, **args)
        self.assertEqual(self.plain(result), result)
        self.assertEqual(result, {"changed": False, "where": "Day 1", "todo": 2,
                                  "done": [["1", "A001", "Cam_01"]], "failed": [["2", "A002", "Cam_02"]]})
        self.assertEqual([c.name for c in self.clips], ["Cam_01", "A002", "Timeline 1", "B001"])

    def test_nothing_is_renamed_when_the_clips_changed_since_the_preview(self):
        preview = resolve_ext.preview(self.controller, "bin")
        self.clips[3].name = "B001 renamed in Resolve"
        result = resolve_ext.rename(self.controller, "bin", preview["keys"], preview["names"], seq("Shot"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["names"], ["A001", "A002", "B001 renamed in Resolve"])
        self.assertEqual([c.name for c in self.clips][:2], ["A001", "A002"])
        self.controller.pool.folder.clips = []
        with self.assertRaises(resolve_ext.TargetError):
            resolve_ext.rename(self.controller, "bin", [], [], seq("Shot"))

    def test_an_undo_in_a_process_that_didnt_rename_finds_the_clips_by_id(self):
        preview = resolve_ext.preview(self.controller, "bin")
        done = resolve_ext.rename(self.controller, "bin", preview["keys"], preview["names"], seq("Shot"))["done"]
        resolve_ext._RENAMED.clear()                                     # a fresh reader process
        moved = self.clips.pop(0)                                       # into another bin since
        self.controller.pool.root.subs.append(Folder([moved], name="Day 2"))
        self.clips[0].name = "Renamed again"
        result = resolve_ext.undo(self.controller, self.plain(done))
        self.assertEqual(result, {"restored": 2, "skipped": 1})
        self.assertEqual([moved.name] + [c.name for c in self.clips], ["A001", "Renamed again", "Timeline 1", "B001"])

    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_the_jobs_load_without_qt(self):
        self.assertEqual(load_without_qt(JOBS), "")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        from pages.batch_clip_renamer.page import BatchClipRenamerPage
        self.clips = [Clip("1", "A001"), Clip("2", "A002"), Clip("t", "Timeline 1", kind="Timeline"),
                      Clip("3", "B001")]
        self.host = Host(Controller(self.clips))
        self.page = BatchClipRenamerPage(self.host)
        self.page._poll.stop()
        self.addCleanup(self.page.deleteLater)
        self.events = []
        self.page.emit = lambda name, payload=None: self.events.append((name, payload))
        self.page.on_shown()
        self.settle()

    def last(self, name):
        return [p for n, p in self.events if n == name][-1]

    def settle(self):
        """Lets the page's Resolve read (on its worker, in the tests) finish and
        its answer - which can start the next read - arrive."""
        for _ in range(4):
            self.assertTrue(self.page._calls.worker.wait_idle(5))
            for thread in threading.enumerate():
                if thread.name == "resolve-worker":
                    thread.join(5)
            self.app.processEvents()

    def names(self):
        return [c.name for c in self.clips]

    def test_preview_leaves_timelines_out_and_changes_nothing(self):
        self.page.on_inputs(seq("Shot"))
        state = self.last("state")
        self.assertEqual([r["new"] for r in state["rows"]], ["Shot_01", "Shot_02", "Shot_03"])
        self.assertEqual((state["where"], state["changes"]), ("Day 1", 3))
        self.assertEqual(self.names(), ["A001", "A002", "Timeline 1", "B001"])   # preview only

    def test_rename_then_undo(self):
        self.page.on_inputs(rep("A0", "Cam_"))
        self.page.on_rename(None)
        self.settle()
        self.assertEqual(self.names(), ["Cam_01", "Cam_02", "Timeline 1", "B001"])
        self.assertEqual(self.last("state")["undo"]["count"], 2)
        self.assertEqual(self.last("state")["changes"], 0)   # nothing left to match
        self.clips[1].name = "Renamed in Resolve"
        self.page.on_undo(None)
        self.settle()
        self.assertEqual(self.names(), ["A001", "Renamed in Resolve", "Timeline 1", "B001"])
        self.assertIsNone(self.last("state")["undo"])
        self.assertIn("left alone", self.last("log")[-1]["text"])
        self.assertEqual(self.host.busy, [])            # a small rename: here, no overlay

    def more_clips(self, count=25):
        self.clips.extend(Clip(str(10 + n), f"C{n:03}") for n in range(count))

    def test_a_big_rename_and_its_undo_are_made_in_the_reader_process(self):
        self.more_clips()
        reader = self.page._calls.reader = ChildReader(self.host.controller, JOBS)
        self.page.on_refresh(None)
        reader.run()
        self.page.on_inputs(seq("Shot"))
        self.assertEqual(self.last("state")["changes"], 28)
        self.page.on_rename(None)
        self.assertEqual((self.names()[0], self.host.busy), ("A001", [True]))   # sent, the overlay up
        reader.run()
        self.assertEqual(self.host.busy, [True, False])
        self.assertEqual(self.host.messages, ["Renaming 28 clips…"])
        self.assertEqual(self.names()[:5], ["Shot_01", "Shot_02", "Timeline 1", "Shot_03", "Shot_04"])
        self.assertEqual(self.last("state")["undo"]["count"], 28)
        self.assertEqual(self.last("log")[-1]["text"], "Renamed 28 clips in Day 1.")
        resolve_ext._RENAMED.clear()                    # the reader's process started afresh since
        self.clips[1].name = "Renamed in Resolve"
        self.page.on_undo(None)
        reader.run()
        self.assertEqual([n for n, _ in reader.asked if n in JOBS], ["renamer.rename", "renamer.undo"])
        self.assertEqual(self.host.messages, ["Renaming 28 clips…", "Renaming 28 clips…"])
        self.assertEqual(self.names()[:5], ["A001", "Renamed in Resolve", "Timeline 1", "B001", "C000"])
        self.assertEqual(self.last("log")[-1]["text"], "Undid the rename: 27 clips back to their old name. "
                                                       "1 had been renamed again since and were left alone.")
        self.assertEqual(self.host.pumped, [])         # no progress from the reader's process

    def test_a_big_rename_made_here_says_how_far_it_has_got(self):
        self.more_clips()
        self.page.on_refresh(None)
        self.settle()
        self.page.on_inputs(seq("Shot"))
        self.page.on_rename(None)
        self.settle()
        self.assertEqual(self.names()[-1], "Shot_28")
        self.assertEqual(self.host.pumped, ["Renaming clips… 25 of 28"])
        self.page.on_undo(None)
        self.settle()
        self.assertEqual(self.names()[-1], "C024")
        self.assertEqual(self.host.busy, [True, False, True, False])

    def test_rename_refuses_when_resolve_changed_since_the_preview(self):
        self.page.on_inputs(seq("Shot"))
        self.clips.append(Clip("4", "C001"))        # a clip lands in the bin
        self.page.on_rename(None)
        self.settle()
        self.assertEqual(self.names()[:2], ["A001", "A002"])
        self.assertEqual(self.last("state")["total"], 4)   # preview redrawn with it
        self.page.on_rename(None)                   # a second press, having looked
        self.settle()
        self.assertEqual(self.names()[-1], "Shot_04")

    def test_selected_scope(self):
        self.page.on_scope({"scope": "selected"})
        self.settle()
        self.assertEqual(self.last("state")["error"], "No clips are selected in the Media Pool.")
        self.host.controller.pool.selected = [self.clips[2], self.clips[3]]   # a timeline and a clip
        self.page.on_scope({"scope": "bin"})
        self.settle()
        self.page.on_scope({"scope": "selected"})
        self.settle()
        self.page.on_inputs(seq("Pick"))
        self.assertEqual([r["new"] for r in self.last("state")["rows"]], ["Pick_01"])

    def test_a_refused_rename_is_reported(self):
        self.clips[0].accept = False
        self.page.on_inputs(seq("Shot"))
        self.page.on_rename(None)
        self.settle()
        self.assertEqual(self.names(), ["A001", "Shot_02", "Timeline 1", "Shot_03"])
        self.assertIn("refused to rename 1: A001", self.last("log")[-1]["text"])

    def test_not_connected(self):
        self.host.connected = False
        self.page.on_shown()
        self.settle()
        state = self.last("state")
        self.assertFalse(state["connected"])
        self.assertEqual(state["rows"], [])


if __name__ == "__main__":
    unittest.main()
