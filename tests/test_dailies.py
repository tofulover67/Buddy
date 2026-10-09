"""Dailies catalog, shorthand and metadata writes without Resolve or Qt."""

import json
import unittest

import _paths  # noqa: F401
from pages.dailies import model, resolve_ext


class Clip:
    def __init__(self, uid, name, path, kind="Video"):
        self.uid = uid
        self.name = name
        self.props = {"Clip Name": name, "File Path": path, "Type": kind, "Duration": "00:00:10:00"}
        self.meta = {"Comments": "Previous note", "Tag": "0"}

    def GetUniqueId(self): return self.uid
    def GetName(self): return self.name
    def GetClipProperty(self, key=None): return self.props if key is None else self.props.get(key, "")
    def GetMetadata(self, key=None): return self.meta if key is None else self.meta.get(key, "")
    def SetMetadata(self, changes): self.meta.update(changes); return True
    def GetClipColor(self): return ""


class Folder:
    def __init__(self, uid, name, clips=(), children=()):
        self.uid, self.name, self.clips, self.children = uid, name, list(clips), list(children)

    def GetUniqueId(self): return self.uid
    def GetName(self): return self.name
    def GetClipList(self): return self.clips
    def GetSubFolderList(self): return self.children


class Project:
    def __init__(self, root, current): self.root, self.current = root, current
    def GetUniqueId(self): return "project-1"
    def GetName(self): return "Film"
    def GetMediaPool(self): return self
    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.current


class Controller:
    def __init__(self, project): self.project = project
    def current_project(self): return self.project


class DailiesTests(unittest.TestCase):
    def test_shorthand_keeps_free_notes_and_updates_metadata(self):
        clip = Clip("one", "Take 1", "C:/footage/a.mov")
        changes = model.changes_for({"log": "S1 sh02 t3 Great look", "tag": "1"}, clip.meta)
        self.assertEqual(changes["Scene"], "1")
        self.assertEqual(changes["Shot"], "2")
        self.assertEqual(changes["Take"], "3")
        self.assertEqual(changes["Tag"], "1")
        self.assertIn("S1 sh02 t3 Great look", changes["Comments"])
        project = Project(Folder("root", "Master", [clip]), None)
        saved, failures, written = resolve_ext.apply_drafts(Controller(project), "project-1", {"one": clip},
                                                             {"one": {"log": "S1 sh02 t3 Great look", "tag": "1"}})
        self.assertEqual((saved, failures), (["one"], []))
        self.assertEqual(written["one"]["Scene"], "1")
        self.assertEqual(clip.meta["Scene"], "1")
        self.assertEqual(clip.meta["Tag"], "1")
        self.assertEqual(model.comments_with_log(clip.meta["Comments"], "S1 sh02 t3 Great look"), clip.meta["Comments"])

    def test_catalog_uses_current_bin_and_excludes_timelines(self):
        first = Clip("a", "A", "C:/a.mov")
        second = Clip("b", "B", "C:/b.wav", "Audio")
        timeline = Clip("tl", "Timeline 1", "", "Timeline")
        child = Folder("child", "Day 1", [second])
        root = Folder("root", "Master", [first, timeline], [child])
        result = resolve_ext.scan(Controller(Project(root, child)))
        self.assertEqual(list(result["clips"]), ["a", "b"])
        self.assertEqual(result["current_ids"], ["b"])
        self.assertEqual(result["current_bin_id"], "child")
        self.assertEqual(result["clips"]["b"]["type"], "Audio")

    def test_the_current_bins_sub_bins_are_read_too(self):
        day = Folder("day", "Day 1", [Clip("d", "D", "C:/d.mov")],
                     [Folder("cam-a", "A cam", [Clip("a", "A", "C:/a.mov")],
                             [Folder("roll", "Roll 1", [Clip("r", "R", "C:/r.mov")])]),
                      Folder("cam-b", "B cam", [Clip("b", "B", "C:/b.mov")])])
        other = Folder("other", "Day 2", [Clip("o", "O", "C:/o.mov")])
        root = Folder("root", "Master", [Clip("m", "M", "C:/m.mov")], [day, other])
        result = resolve_ext.scan(Controller(Project(root, day)))
        self.assertEqual(result["current_ids"], ["d"])
        self.assertEqual(result["current_tree_ids"], ["d", "a", "r", "b"])
        self.assertEqual(result["clips"]["r"]["bin"], "Master/Day 1/A cam/Roll 1")

    def test_location_follows_current_bin_without_scanning_clips(self):
        first = Folder("first", "Day 1")
        second = Folder("second", "Day 2")
        project = Project(Folder("root", "Master", children=[first, second]), first)
        controller = Controller(project)
        self.assertEqual(resolve_ext.current_location(controller), ("project-1", "first"))
        project.current = second
        self.assertEqual(resolve_ext.current_location(controller), ("project-1", "second"))

    def test_project_change_blocks_metadata_write(self):
        clip = Clip("one", "Take 1", "C:/a.mov")
        project = Project(Folder("root", "Master", [clip]), None)
        with self.assertRaisesRegex(Exception, "project changed"):
            resolve_ext.apply_drafts(Controller(project), "different", {"one": clip}, {"one": {"tag": "-1"}})
        self.assertEqual(clip.meta["Tag"], "0")

    def test_notes_are_written_to_the_clips_this_process_scanned(self):
        # As in the Resolve reader's process: the scan's rows go back as
        # plain data, its Media Pool items stay here for the write.
        clip = Clip("one", "Take 1", "C:/a.mov")
        controller = Controller(Project(Folder("root", "Master", [clip]), None))
        rows = resolve_ext.scan_rows(controller)
        self.assertNotIn("objects", rows)
        json.dumps(rows)
        saved, failures, written = resolve_ext.apply_by_id(controller, "project-1", {"one": {"tag": "-1"}})
        self.assertEqual((saved, failures, written), (["one"], [], {"one": {"Tag": "-1"}}))
        self.assertEqual(clip.meta["Tag"], "-1")

    def test_a_fresh_process_scans_before_writing(self):
        clip = Clip("two", "Take 2", "C:/b.mov")
        controller = Controller(Project(Folder("root", "Master", [clip]), None))
        resolve_ext._SCANNED.update(project=None, objects={})
        saved, failures, _written = resolve_ext.apply_by_id(controller, "project-1", {"two": {"tag": "1"}})
        self.assertEqual((saved, failures), (["two"], []))
        self.assertEqual(clip.meta["Tag"], "1")

    def test_the_reader_process_answers_dailies_reads(self):
        from core import resolve_reader_child as child
        clip = Clip("one", "Take 1", "C:/a.mov")
        folder = Folder("root", "Master", [clip])
        reply = child.answer({"id": 4, "do": "dailies.location"}, Controller(Project(folder, folder)))
        self.assertEqual(reply, {"id": 4, "ok": True, "result": ("project-1", "root")})
        reply = child.answer({"id": 5, "do": "dailies.scan"}, Controller(Project(folder, folder)))
        self.assertEqual(list(reply["result"]["clips"]), ["one"])

    def test_find_in_media_pool_opens_the_clips_bin_and_selects_it(self):
        """A clip's right-click "Find in Media Pool" - from the reader process,
        like every other Dailies call, and from the Edit page when Resolve is
        on one with no Media Pool."""
        from core import resolve_reader_child as child
        clip = Clip("two", "Take 2", "C:/b.mov")
        day = Folder("day", "Day 1", [clip])
        root = Folder("root", "Master", [Clip("one", "Take 1", "C:/a.mov")], [day])
        project = Project(root, root)
        project.selected = []
        project.SetCurrentFolder = lambda folder: setattr(project, "current", folder) or True
        project.SetSelectedClip = lambda item: project.selected.append(item) or True
        pages = []
        controller = Controller(project)
        controller.resolve = type("R", (), {"GetCurrentPage": lambda self: "color",
                                            "OpenPage": lambda self, page: pages.append(page) or True})()
        resolve_ext._SCANNED.update(project=None, objects={}, folders={})
        reply = child.answer({"id": 7, "do": "dailies.reveal",
                              "args": {"expected_project_id": "project-1", "clip_id": "two"}}, controller)
        self.assertEqual(reply, {"id": 7, "ok": True, "result": "Day 1"})
        self.assertIs(project.current, day)
        self.assertEqual(project.selected, [clip])
        self.assertEqual(pages, ["edit"])
        reply = child.answer({"id": 8, "do": "dailies.reveal",
                              "args": {"expected_project_id": "project-1", "clip_id": "gone"}}, controller)
        self.assertFalse(reply["ok"])
        self.assertIn("isn't in the Media Pool", reply["error"])

    def test_clearing_review_log_removes_only_the_buddy_block(self):
        with_note = model.comments_with_log("Previous note", "s1 Good shot")
        changes = model.changes_for({"log": "", "log_edited": True}, {"Comments": with_note})
        self.assertEqual(changes, {"Comments": "Previous note"})

    def test_clip_colour_is_written_or_cleared(self):
        self.assertEqual(model.changes_for({"color": "Teal"}, {"Clip Color": ""}), {"Clip Color": "Teal"})
        self.assertEqual(model.changes_for({"color": ""}, {"Clip Color": "Teal"}), {"Clip Color": ""})
        self.assertEqual(model.changes_for({"color": None}, {"Clip Color": "Teal"}), {})
        self.assertEqual(model.changes_for({"color": "Mauve"}, {}), {})

    def test_clip_length_on_the_tape(self):
        self.assertAlmostEqual(model.clip_seconds("Video", "960", 59.94), 16.016, places=3)
        self.assertAlmostEqual(model.clip_seconds("Video", "240", "23.976 DF"), 10.01, places=2)
        self.assertEqual(model.clip_seconds("Image", "1", 24), model.STILL_SECONDS)
        self.assertEqual(model.clip_seconds("Audio", "", ""), model.STILL_SECONDS)

    def test_shorthand_is_not_read_after_an_apostrophe(self):
        # Speech added from the transcript: "that's 2", "don't 4" are no scene or take.
        self.assertEqual(model.quick_fields("That's 2 people, we don't 4 times, it’s 3"), {})
        self.assertEqual(model.quick_fields("(s4) sh1 t2"), {"Scene": "4", "Shot": "1", "Take": "2"})

    def test_resolves_type_says_video_whatever_the_extension(self):
        self.assertEqual(model.media_type("Video + Audio", "C:/a/clip.m4v"), "Video")
        self.assertEqual(model.media_type("Audio", "C:/a/sound.wav"), "Audio")
        self.assertEqual(model.media_type("Still", "C:/a/still.webp"), "Image")
        self.assertEqual(model.media_type("Compound", ""), "Other")

    def test_typed_scene_overrides_shorthand(self):
        changes = model.changes_for({"log": "s1 Good shot", "fields": {"Scene": "Opening"}}, {})
        self.assertEqual(changes["Scene"], "Opening")
