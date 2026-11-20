"""More of the project for Ask Buddy, against a fake Resolve: the additive
actions added to actions.py (flags, empty metadata fields, markers by time),
several of them on one card (plans), and resolve_ext.py's wider reads - the
render queue, the Media Pool's bins, audio tracks and a clip's grade."""

import os
import tempfile
import unittest

import _paths  # noqa: F401
from pages.manual_chat import actions, resolve_ext
from test_manual_chat_actions import (
    FakeClip,
    FakeController,
    FakeFolder,
    FakeItem,
    FakeProject,
    FakeTimeline,
)


class Timeline(FakeTimeline):
    fps = "24"

    def GetSetting(self, key):
        return self.fps if key == "timelineFrameRate" else ""

    def GetTrackName(self, kind, index):
        return f"Dialogue {index}"

    def GetTrackSubType(self, kind, index):
        return "stereo"

    def GetIsTrackEnabled(self, kind, index):
        return index != 2

    def GetTrackCount(self, kind):
        return len(self.tracks) if kind == "video" else len(getattr(self, "audio", []))

    def GetItemListInTrack(self, kind, track):
        return (self.tracks if kind == "video" else self.audio)[track - 1]

    def GetSelectedClips(self):
        return list(getattr(self, "selected", []))

    def GetCurrentTimecode(self):
        return "01:00:00:00"


class Item(FakeItem):
    def __init__(self, name, start=0, duration=10):
        super().__init__(name, start, duration)
        self.flags = []

    def GetFlagList(self):
        return list(self.flags)

    def AddFlag(self, color):
        self.flags.append(color)
        return True

    def GetUniqueId(self):
        return f"id-{self.name}"


class Clip(FakeClip):
    def __init__(self, name, metadata=None, path=""):
        super().__init__(name)
        self.metadata, self.path = dict(metadata or {}), path
        self.refuse = set()

    def GetClipProperty(self, key):
        return self.path if key == "File Path" else super().GetClipProperty(key)

    def GetMetadata(self):
        return dict(self.metadata)

    def SetMetadata(self, values):
        if set(values) & self.refuse:
            return False
        self.metadata.update(values)
        return True


def setup(tracks=None, root=None):
    timeline = Timeline("Edit v1", "tl-1", tracks)
    project = FakeProject("Show", "pr-1", timeline, root)
    return FakeController(project), project, timeline


class MarkerTimeTests(unittest.TestCase):
    def test_a_time_or_a_timecode_becomes_the_frame(self):
        controller, _, timeline = setup()
        proposal = actions.preview(controller, "add_markers", {"markers": [
            {"time": "0:10", "name": "Intro"}, {"time": "1:00:05", "name": "Act two"},
            {"timecode": "01:00:20:12", "name": "Sting"}, {"frame": 7}]})
        self.assertEqual([frame for frame, *_ in proposal.plan], [240, 86520, 492, 7])   # 24 fps, starting at 86400
        self.assertIn("frame 240 (timeline frame 86640)", proposal.details[0])
        actions.execute(controller, proposal)
        self.assertEqual(timeline.markers[240]["name"], "Intro")

    def test_two_markers_on_one_frame_are_refused(self):
        controller, _, _ = setup()
        with self.assertRaises(actions.ActionError) as caught:
            actions.preview(controller, "add_markers", {"markers": [{"time": "0:10"}, {"frame": 240}]})
        self.assertIn("only one marker per frame", str(caught.exception))

    def test_nonsense_and_an_unknown_frame_rate_say_so(self):
        controller, _, timeline = setup()
        for marker, why in (({"time": "soon"}, "isn't a time"), ({"timecode": "10 past"}, "isn't a timecode"),
                            ({"name": "nowhere"}, "no usable 'frame'")):
            with self.assertRaises(actions.ActionError) as caught:
                actions.preview(controller, "add_markers", {"markers": [marker]})
            self.assertIn(why, str(caught.exception))
        timeline.fps = ""
        with self.assertRaises(actions.ActionError) as caught:
            actions.preview(controller, "add_markers", {"markers": [{"time": "0:10"}]})
        self.assertIn("frame rate couldn't be read", str(caught.exception))
        actions.preview(controller, "add_markers", {"markers": [{"frame": 3}]})        # a frame needs no frame rate


class FlagTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = Item("A", 86400), Item("B", 86410)
        self.b.flags = ["Red"]
        self.controller, _, _ = setup(tracks=[[self.a, self.b]])
        self.both = [{"track": "V1", "index": 1}, {"track": "V1", "index": 2}]

    def test_a_flag_is_added_and_one_already_there_is_left(self):
        proposal = actions.preview(self.controller, "add_clip_flags", {"color": "Red", "clips": self.both})
        self.assertFalse(proposal.destructive)
        self.assertEqual(len(proposal.plan), 1)
        self.assertTrue(any("already has a Red flag" in line for line in proposal.details))
        self.assertEqual(actions.execute(self.controller, proposal), "Flagged 1 of 1 clip(s).")
        self.assertEqual((self.a.flags, self.b.flags), (["Red"], ["Red"]))

    def test_nothing_to_flag_and_a_colour_resolve_doesnt_have(self):
        with self.assertRaises(actions.ActionError):
            actions.preview(self.controller, "add_clip_flags", {"color": "Red", "clips": self.both[1:]})
        with self.assertRaises(actions.ActionError):
            actions.preview(self.controller, "add_clip_flags", {"color": "Orange", "clips": self.both})


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.wide = Clip("A001_wide", {"Scene": "12"})
        self.close = Clip("A002_close")
        self.controller, _, _ = setup(root=FakeFolder("Master", [self.wide, self.close]))

    def fill(self, clips):
        return actions.preview(self.controller, "fill_clip_metadata", {"clips": clips})

    def test_empty_fields_are_filled(self):
        proposal = self.fill([{"name": "A002_close", "metadata": {"Scene": "12", "Keywords": "interview"}},
                              {"name": "A001_wide", "metadata": {"Shot": "3"}}])
        self.assertFalse(proposal.destructive)
        self.assertIn("3 empty metadata field(s) on 2", proposal.summary)
        actions.execute(self.controller, proposal)
        self.assertEqual(self.close.metadata, {"Scene": "12", "Keywords": "interview"})
        self.assertEqual(self.wide.metadata, {"Scene": "12", "Shot": "3"})

    def test_a_field_that_says_something_is_never_replaced(self):
        with self.assertRaises(actions.ActionError) as caught:
            self.fill([{"name": "A001_wide", "metadata": {"Scene": "14"}}])
        self.assertIn("already has Scene set to '12'", str(caught.exception))
        self.assertEqual(self.wide.metadata, {"Scene": "12"})

    def test_filled_in_since_the_card_appeared_is_left_too(self):
        proposal = self.fill([{"name": "A002_close", "metadata": {"Scene": "12", "Shot": "1"}}])
        self.close.metadata["Scene"] = "9"                       # typed in Resolve meanwhile
        self.close.refuse = {"Shot"}                             # and a field Resolve won't take
        log = []
        self.assertEqual(actions.execute(self.controller, proposal, log=log.append),
                         "Filled in 0 of 2 metadata field(s).")
        self.assertEqual(self.close.metadata, {"Scene": "9"})
        self.assertTrue(any("filled in since" in line for line in log))

    def test_names_are_never_guessed(self):
        for clips in ([{"name": "A009", "metadata": {"Scene": "1"}}], [{"name": "A001_wide"}],
                      [{"name": "A001_wide", "metadata": {"Shot": ""}}], ["A001_wide"]):
            with self.assertRaises(actions.ActionError):
                self.fill(clips)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.a = Item("A", 86400)
        self.clip = Clip("A001_wide")
        self.controller, self.project, self.timeline = setup(
            tracks=[[self.a]], root=FakeFolder("Master", [self.clip]))
        self.steps = [
            {"action_id": "add_markers", "arguments": {"markers": [{"time": "0:01", "name": "Start"}]}},
            {"action_id": "add_clip_flags", "arguments": {"color": "Blue", "clips": [{"track": "V1", "index": 1}]}},
            {"action_id": "fill_clip_metadata",
             "arguments": {"clips": [{"name": "A001_wide", "metadata": {"Scene": "1"}}]}},
        ]

    def test_one_card_one_apply_in_order(self):
        plan = actions.preview_plan(self.controller, self.steps)
        self.assertFalse(plan.destructive)
        self.assertEqual(plan.summary, "3 changes, applied in this order.")
        self.assertEqual([line for line in plan.details if line[0].isdigit()],
                         ["1. Add 1 marker(s) to 'Edit v1'.", "2. Add a Blue flag to 1 timeline clip(s).",
                          "3. Fill in 1 empty metadata field(s) on 1 Media Pool clip(s)."])
        self.assertIn("frame 24", plan.as_text())
        log = []
        outcome = actions.execute(self.controller, plan, log=log.append)
        self.assertEqual(outcome, "Added 1 of 1 marker(s). Flagged 1 of 1 clip(s). Filled in 1 of 1 metadata field(s).")
        self.assertEqual((list(self.timeline.markers), self.a.flags, self.clip.metadata),
                         ([24], ["Blue"], {"Scene": "1"}))
        self.assertEqual(len(log), 3)

    def test_only_changes_that_add(self):
        delete = {"action_id": "delete_markers", "arguments": {"color": "Blue"}}
        with self.assertRaises(actions.ActionError) as caught:
            actions.preview_plan(self.controller, self.steps[:1] + [delete])
        self.assertIn("may only hold changes that add", str(caught.exception))
        for action_id in actions.DESTRUCTIVE_ACTIONS:
            with self.assertRaises(actions.ActionError):
                actions.preview_plan(self.controller, self.steps[:1] + [{"action_id": action_id, "arguments": {}}])

    def test_what_a_plan_has_to_be(self):
        for steps, why in ((self.steps[:1], "two or more"), (self.steps + self.steps, "more than one plan may hold"),
                           (self.steps[:1] * 2, "in the plan twice"), ([self.steps[0], "flag them"], "should be"),
                           ([self.steps[0], {"action_id": "add_clip_flags", "arguments": {"color": "Mauve"}}],
                            "Step 2 (add_clip_flags)")):
            with self.assertRaises(actions.ActionError) as caught:
                actions.preview_plan(self.controller, steps)
            self.assertIn(why, str(caught.exception))

    def test_a_timeline_switched_since_stops_all_of_it(self):
        plan = actions.preview_plan(self.controller, [self.steps[2], self.steps[0]])     # metadata first
        self.project.timeline = Timeline("Edit v2", "tl-2")
        with self.assertRaises(actions.ActionError):
            actions.execute(self.controller, plan)
        self.assertEqual(self.clip.metadata, {})                 # not even the step that didn't need the timeline

    def test_a_step_that_fails_says_what_was_done_before_it(self):
        plan = actions.preview_plan(self.controller, self.steps[:2])
        self.a.AddFlag = lambda color: 1 / 0
        with self.assertRaises(actions.ActionError) as caught:
            actions.execute(self.controller, plan)
        self.assertIn("step 2", str(caught.exception))
        self.assertIn("Applied before it: Added 1 of 1 marker(s).", str(caught.exception))


class RegistryTests(unittest.TestCase):
    def test_the_tables_agree_and_the_new_actions_only_add(self):
        self.assertEqual(set(actions.ACTIONS), set(actions.ACTION_SUMMARIES))
        self.assertLessEqual(actions.DESTRUCTIVE_ACTIONS | actions.TIMELINE_ACTIONS, set(actions.ACTIONS))
        for action_id in ("add_clip_flags", "fill_clip_metadata", "add_markers"):
            self.assertNotIn(action_id, actions.DESTRUCTIVE_ACTIONS)
        additive, _destructive = actions.help_lines()
        self.assertIn("add a flag to clips on the timeline", additive)


class Graph:
    def GetNumNodes(self):
        return 2

    def GetNodeLabel(self, n):
        return ["Balance", ""][n - 1]

    def GetLUT(self, n):
        return ["", "Film Looks/Example.cube"][n - 1]

    def GetToolsInNode(self, n):
        return [["Primaries"], []][n - 1]


class Graded(Item):
    def GetNodeGraph(self):
        return Graph()

    def GetCurrentVersion(self):
        return {"versionName": "Version 2", "versionType": 0}

    def GetColorGroup(self):
        return FakeFolder("Interviews")

    def GetClipEnabled(self):
        return True

    def GetProperties(self):
        return {"ZoomX": 1.2, "ZoomY": 1.2, "Opacity": 100.0, "Something else": 1}


class ReadTests(unittest.TestCase):
    def test_the_render_queue(self):
        controller, project, _ = setup()
        project.GetCurrentRenderFormatAndCodec = lambda: {"format": "mp4", "codec": "H264"}
        project.IsRenderingInProgress = lambda: False
        project.GetCurrentRenderMode = lambda: 1
        project.GetRenderJobList = lambda: [
            {"JobId": "j1", "RenderJobName": "Job 1", "TimelineName": "Edit v1", "TargetDir": "D:\\Exports",
             "FormatWidth": 1920, "FormatHeight": 1080, "VideoCodec": "H.264", "Unlisted": "x"}]
        project.GetRenderJobStatus = lambda job: {"JobStatus": "Failed", "CompletionPercentage": 40,
                                                  "Error": "Disk full"}
        state = resolve_ext.render_state(controller)
        self.assertEqual(state["current_format_and_codec"], {"format": "mp4", "codec": "H264"})
        self.assertEqual(state["render_mode"], "single clip")
        self.assertEqual(state["jobs"], [{"RenderJobName": "Job 1", "TimelineName": "Edit v1",
                                          "TargetDir": "D:\\Exports", "FormatWidth": 1920, "FormatHeight": 1080,
                                          "VideoCodec": "H.264", "JobStatus": "Failed",
                                          "CompletionPercentage": 40, "Error": "Disk full"}])

    def test_a_resolve_without_those_calls_still_answers(self):
        controller, _, _ = setup()
        self.assertEqual(resolve_ext.render_state(controller)["jobs"], [])
        self.assertEqual(resolve_ext._grade(FakeItem("older")), {})       # no node graph, versions or flags to ask

    def test_the_media_pool(self):
        with tempfile.TemporaryDirectory() as folder:
            here = os.path.join(folder, "A001_wide.mov")
            open(here, "w").close()
            gone = os.path.join(folder, "A002_close.mov")
            sub = FakeFolder("Day 1", [Clip("A002_close", path=gone), FakeClip("Cut 1", "Timeline")])
            root = FakeFolder("Master", [Clip("A001_wide", path=here), Clip("Title")], [sub])
            controller, project, _ = setup(root=root)
            project.pool.GetCurrentFolder = lambda: sub
            pool = resolve_ext.media_pool_overview(controller)
        self.assertEqual((pool["clips"], pool["timelines"], pool["current_bin"]), (3, 1, "Day 1"))
        self.assertEqual(pool["bins"], [{"bin": "Master", "clips": 2, "timelines": 0},
                                        {"bin": "Master / Day 1", "clips": 1, "timelines": 1}])
        self.assertEqual((pool["source_files_not_found"], pool["source_files_checked"]), (["A002_close"], 3))

    def test_audio_tracks(self):
        controller, _, timeline = setup()
        timeline.audio = [[Item("VO"), Item("VO 2")], []]
        self.assertEqual(resolve_ext.audio_tracks(controller)["tracks"], [
            {"track": "A1", "name": "Dialogue 1", "layout": "stereo", "clips": 2, "enabled": True},
            {"track": "A2", "name": "Dialogue 2", "layout": "stereo", "clips": 0, "enabled": False}])

    def test_a_clips_grade(self):
        clip = Graded("Interview", 86400)
        clip.flags = ["Green"]
        controller, _, timeline = setup(tracks=[[clip]])
        timeline.selected = [clip]
        found = resolve_ext.focused_clip_grade(controller)
        self.assertNotIn("_items", found)
        grade = found["clips"][0]["grade"]
        self.assertEqual(grade["nodes"], [{"node": 1, "label": "Balance", "tools": ["Primaries"]},
                                          {"node": 2, "lut": "Film Looks/Example.cube"}])
        self.assertEqual((grade["version"], grade["color_group"], grade["flags"], grade["enabled"]),
                         ({"name": "Version 2", "type": "local"}, "Interviews", ["Green"], True))
        self.assertEqual(grade["inspector"], {"ZoomX": 1.2, "ZoomY": 1.2, "Opacity": 100.0})
        self.assertNotIn("grade", resolve_ext.focused_clip(controller)["clips"][0])      # only when asked for


if __name__ == "__main__":
    unittest.main()
