"""The Text+ tabs' long edits made in the Resolve reader's process
(text_animator/text_plus_resolve.py, core/busy_job.py): each job works from plain
arguments - the clips by GetUniqueId - with an answer that goes through JSON; the module
loads without Qt; and the tabs (text_plus.TextPlusTools) hand their edits to the reader,
keep Buddy free meanwhile, say what they always said and keep Undo on their own clips.

The tests' Resolve is a fake in this process (BUDDY_RESOLVE_READER is off), so a
stand-in reader makes each job here, the way resolve_reader_child.answer would, with
its arguments and answer through JSON."""

import json
import os
import subprocess
import sys
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

import _paths  # noqa: F401
from core import resolve_reader_child as child
from pages.text_animator import text_plus_resolve as tpr
from pages.text_animator.animation_engine import FusionAnimationEngine as E
from test_letter_animations import Comp as LetterComp
from test_text_animator_page import Clip, Host, HostPage, Mem, Resolve, Timeline, Tool, fake_timeline
from test_word_stack import DrtTests, sequence, tracks_of

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    from core.resolve_reader import ReadError
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

M = "pages.text_animator.text_plus_resolve"
# The jobs core/resolve_reader_child.py runs for these tabs.
JOBS_WANTED = {
    "textplus.read": (M, "read_here"),
    "textplus.space": (M, "space_here"),
    "textplus.bounding": (M, "bounding_here"),
    "textplus.position": (M, "position_here"),
    "textplus.animate": (M, "animate_here"),
    "textplus.unanimate": (M, "unanimate_here"),
    "textplus.style": (M, "style_here"),
    "textplus.stack": (M, "stack_here"),
}

APP = os.path.dirname(os.path.dirname(os.path.abspath(child.__file__)))
PROBE = r"""
import sys
sys.path.insert(0, sys.argv[1])
class NoQt:
    def find_spec(self, name, path=None, target=None):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ImportError("no Qt in the reader's process")
sys.meta_path.insert(0, NoQt())
from pages.text_animator import text_plus_resolve as tpr
print(",".join(sorted(n for n in dir(tpr) if n.endswith("_here") and not n.startswith("_"))))
"""


def through_the_pipe(name, args, controller):
    """The reader's process's reply to job `name`: its arguments and reply as the JSON
    lines its pipe carries, the job made by resolve_reader_child.answer itself."""
    with mock.patch.dict(child.JOBS, JOBS_WANTED):
        reply = child.answer({"id": 1, "do": name, "args": json.loads(json.dumps(args))}, controller)
    return json.loads(json.dumps(reply, default=child._plain))


def ref(clip):
    return [clip.GetUniqueId(), clip.track]


class Listed(LetterComp):
    """The letter animations' fake comp, listing its tools the way Resolve's comps do."""

    def GetToolList(self, _selected=False, kind=None):
        return dict(enumerate([t for t in self.tools if kind in (None, t.ID)], 1))


class AnimClip(Clip):
    """A Text+ clip whose comp the animation presets can key."""

    def __init__(self, name, start, end):
        super().__init__(name, start, end, None)
        self.comp = Listed(name)

    def GetFusionCompByIndex(self, _i):
        return self.comp

    def GetDuration(self):
        return self.end - self.start


def anim_timeline():
    s = 86400
    return Timeline([[AnimClip("One", s, s + 48), AnimClip("Two", s + 48, s + 96)]])


class Exported:
    """A timeline that exports as the .drt test_word_stack builds."""

    def __init__(self, name="Promo"):
        self.name = name

    def GetName(self):
        return self.name

    def SetName(self, name):
        self.name = name
        return True

    def GetUniqueId(self):
        return f"timeline-{self.name}"

    def Export(self, path, kind, _subtype):
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("project.xml", "<SM_Project/>")
            z.writestr("SeqContainer/seq.xml", sequence())
        return kind == "drt"


class StackProject:
    """The project Stack imports its new timeline into (its Media Pool too)."""

    def __init__(self, timeline):
        self.timelines, self.current, self.imported = [timeline], timeline, []

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1]

    def GetMediaPool(self):
        return self

    def GetRootFolder(self):
        return SimpleNamespace(GetSubFolderList=lambda: [])

    def ImportTimelineFromFile(self, path, options):
        with zipfile.ZipFile(path) as z:
            self.imported.append((z.read("SeqContainer/seq.xml").decode(), options))
        made = Exported("stacked.drt")
        self.timelines.append(made)
        return made


def stack_controller(project):
    pm = SimpleNamespace(GetCurrentProject=lambda: project)
    return SimpleNamespace(resolve=SimpleNamespace(EXPORT_DRT="drt", EXPORT_NONE="none", GetProjectManager=lambda: pm))


# ------------------------------------------------------------------ the jobs --

class JobTests(unittest.TestCase):
    """Each job as the reader's process makes it: plain arguments in, JSON out."""

    def setUp(self):
        self.timeline = fake_timeline()
        self.controller = SimpleNamespace(resolve=Resolve(self.timeline))

    def clip(self, track, index=0):
        return self.timeline.tracks[track][index]

    def test_the_module_loads_without_qt(self):
        run = subprocess.run([sys.executable, "-c", PROBE, APP], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, BUDDY_RESOLVE_READER="off"))
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        self.assertEqual(run.stdout.strip().split(","), sorted(f for _m, f in JOBS_WANTED.values()))

    def test_the_jobs_are_named_in_the_reader(self):
        for name, target in JOBS_WANTED.items():
            self.assertEqual(child.JOBS.get(name), target, f"{name} isn't in resolve_reader_child.JOBS")

    def test_position_from_ids(self):
        hello, later = self.timeline.tracks[1]
        reply = through_the_pipe("textplus.position", {
            "timeline_id": self.timeline.GetUniqueId(), "clips": [ref(hello), ref(later), ["gone", 2]],
            "references": [[2, 0.3, 0.3]]}, self.controller)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(later.tool.center(), (0.3, 0.3))
        result = reply["result"]
        self.assertEqual((result["updated"], result["scanned"]), (1, 2))      # "Hello" is there; "gone" isn't found
        self.assertEqual(result["steps"], [{"i": 1, "old": [0.5, 0.5], "new": [0.3, 0.3]}])
        self.assertEqual(result["log"], [])

    def test_another_timeline_open_now_changes_nothing(self):
        later = self.clip(1, 1)
        reply = through_the_pipe("textplus.position", {"timeline_id": "timeline-another", "clips": [ref(later)],
                                                       "references": [[2, 0.1, 0.1]]}, self.controller)
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["type"], "TextPlusJobError")
        self.assertIn("different timeline", reply["error"])
        self.assertEqual(later.tool.center(), (0.5, 0.5))

    def test_animations_on_and_off_from_ids(self):
        timeline = anim_timeline()
        controller = SimpleNamespace(resolve=Resolve(timeline))
        one, two = timeline.tracks[0]
        args = {"timeline_id": timeline.GetUniqueId(), "clips": [ref(one), ref(two)]}
        reply = through_the_pipe("textplus.animate", {**args, "preset": "Pop (Scale)", "speed": "Medium",
                                                      "direction": "From Left", "fps": 24.0,
                                                      "fallback_center": [0.5, 0.5]}, controller)
        result = reply["result"]
        self.assertEqual((result["count"], result["left"], result["total"]), (2, 0, 2))
        self.assertEqual(E.preset_on(one.comp.text_plus), ("Pop (Scale)", "Medium", "From Left", 24.0))
        self.assertEqual(result["steps"][1], {"i": 1, "old": None, "new": ["Pop (Scale)", "Medium", "From Left", 24.0],
                                              "key": ["Two", 86448, 86496, 1]})
        reply = through_the_pipe("textplus.unanimate", {**args, "fps": 24.0}, controller)
        self.assertEqual((reply["result"]["count"], reply["result"]["stuck"]), (2, 0))
        self.assertEqual(reply["result"]["steps"][0]["old"], ["Pop (Scale)", "Medium", "From Left", 24.0])
        self.assertIsNone(E.preset_on(one.comp.text_plus))

    def test_reads_and_bounding_from_ids(self):
        title, hello = self.clip(0), self.clip(1)
        args = {"timeline_id": self.timeline.GetUniqueId(), "clips": [ref(title), ref(hello)]}
        words = through_the_pipe("textplus.read", {**args, "kind": "words"}, self.controller)["result"]["clips"]
        self.assertEqual((words[1]["state"]["text"], words[1]["state"]["center"], words[1]["span"]),
                         ("Hello", [0.3, 0.3], [86400, 86450]))
        fits = through_the_pipe("textplus.read", {**args, "kind": "bounding"}, self.controller)["result"]["clips"]
        self.assertEqual({k: fits[0][k] for k in ("name", "tool", "words", "font", "size", "center")},
                         {"name": "Title", "tool": True, "words": "Big Title", "font": "Arial", "size": 0.1,
                          "center": [0.5, 0.7]})
        plan = [{"i": 0, "name": "Title", "processed": True, "cap": [0.8, 0.7], "size": 0.2, "center": [0.5, 0.7]},
                {"i": 1, "line": "  - Warning: Missing Center/Size for clip 'Hello' – skipped."}]
        reply = through_the_pipe("textplus.bounding", {**args, "plan": plan, "scanned": 2, "scope_desc": "the test",
                                                       "width": 1920, "height": 1080}, self.controller)
        self.assertEqual(reply["result"]["updated"], 1)
        self.assertEqual((title.tool.GetInput("Size"), title.tool.center()), (0.2, (0.5, 0.7)))
        log = reply["result"]["log"]
        self.assertEqual(log[:2], ["  - Warning: Missing Center/Size for clip 'Hello' – skipped.",
                                   "Applied Bounding resize to 1/2 clip(s) on the test."])
        self.assertTrue(log[2].startswith("[Bounding] 1 clip(s) never temporally overlap"), log)   # then the snap

    def test_space_words_from_ids(self):
        hello, world = self.clip(1), self.clip(2)
        reply = through_the_pipe("textplus.space", {"timeline_id": "", "clips": [ref(hello), ref(world)],
                                                    "centers": [[0.4, 0.5], [0.7, 0.3]]}, self.controller)
        self.assertEqual(reply["result"]["moved"], 1)                          # "World" was there already
        self.assertEqual(reply["result"]["steps"], [{"i": 0, "old": [0.3, 0.3], "new": [0.4, 0.5]}])
        self.assertEqual(hello.tool.center(), (0.4, 0.5))

    def test_stack_from_a_plan_of_lists(self):
        project = StackProject(Exported())
        plan = [[track, start, new, frames] for (track, start), (new, frames) in DrtTests.MOVES.items()]
        reply = through_the_pipe("textplus.stack", {"timeline_id": "timeline-Promo", "plan": plan},
                                 stack_controller(project))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["result"], {"name": "Promo (Word-by-word)"})
        self.assertIs(project.current, project.timelines[-1])                 # opened
        xml, options = project.imported[0]
        self.assertEqual(len(tracks_of(xml)[0]), 4)                           # restacked by the plan
        self.assertEqual((options["timelineName"], options["importSourceClips"]), ("Promo (Word-by-word)", False))


# ------------------------------------------------------------------ the tabs --

class Reader:
    """The Resolve reader's stand-in: holds each job asked for until the test answers,
    then makes it as the reader's process would (through_the_pipe), on the same fake
    Resolve - or as `canned` says."""

    def __init__(self, controller, canned=None):
        self.controller, self.canned, self.asked, self.pending = controller, canned or {}, [], []

    def available(self):
        return True

    def ask(self, name, args, done):
        self.asked.append((name, args))
        self.pending.append((name, args, done))
        return True

    def names(self):
        return [name for name, _args in self.asked]

    def answer(self, count=None):
        """The jobs in hand in turn, a step asked for meanwhile too - `count` of them, or all."""
        while self.pending and count != 0:
            name, args, done = self.pending.pop(0)
            if name in self.canned:
                reply = self.canned[name](args)
            else:
                reply = through_the_pipe(name, args, self.controller)
            if reply["ok"]:
                done(reply["result"], None)
            else:
                done(None, ReadError(reply["error"], reply["type"]))
            count = None if count is None else count - 1


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TabTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        from pages.text_animator import canvas_math as cm
        saved = dict(cm._baselines)
        self.addCleanup(lambda: (cm._baselines.clear(), cm._baselines.update(saved)))

    def tools(self, timeline=None, reader=True, tab="layout", canned=None):
        """(TextPlusTools on a fake page, its host, its reader stand-in or None)."""
        from pages.text_animator.text_plus import TextPlusTools
        host = Host(timeline)
        page = HostPage(host)
        page.tab = tab
        tools = TextPlusTools(page)
        self.addCleanup(tools.deleteLater)
        stand_in = None
        if reader:
            stand_in = tools._calls.reader = Reader(host.controller, canned)
        tools._refresh_live_preview(force=True)
        page.logs.clear()               # (the first read's measuring: only one of two pages does it)
        return tools, host, stand_in

    @staticmethod
    def last(tools, name):
        name = name if name in ("toast", "alert") else f"tp_{name}"
        return [p for n, p in tools.page.sent if n == name][-1]

    @staticmethod
    def logs(tools):
        return [text for text, _kind in tools.page.logs]

    @staticmethod
    def places(host):
        return [(c.tool.center(), c.tool.GetInput("Size")) for track in host.timeline.tracks for c in track]

    def test_apply_position_is_made_in_the_readers_process_and_undone_here(self):
        tools, host, reader = self.tools()
        later = host.timeline.tracks[1][1]
        tools.on_apply_position()
        self.assertEqual(reader.names(), ["textplus.position"])
        name, args = reader.asked[0]
        self.assertIn([later.GetUniqueId(), 2], args["clips"])               # the clips as ids
        self.assertEqual(host.busy, [True])                                  # the overlay up, Buddy free meanwhile
        self.assertEqual(later.tool.center(), (0.5, 0.5))                    # nothing made here
        tools.on_apply_position()                                            # a second click meanwhile: nothing
        tools.on_undo()
        self.assertEqual(len(reader.asked), 1)
        with mock.patch.object(tools, "_read_live_preview") as read:
            tools._refresh_live_preview(force=True)                          # nor does the poll read here meanwhile
            tools._on_where({"project": "P", "timeline": "T", "timecode": "01:00:00:00"})
        read.assert_not_called()
        reader.answer()
        self.assertEqual(host.busy, [True, False])
        self.assertEqual(later.tool.center(), (0.3, 0.3))                    # "Later" follows "Hello"
        self.assertEqual(self.last(tools, "toast")["text"], "Position copied to 1 Text+ clip(s)")
        tools.on_undo()                                                      # on Buddy's own clips
        self.assertEqual(later.tool.center(), (0.5, 0.5))

    def test_it_says_what_it_always_said(self):
        here, here_host, _ = self.tools(reader=False)
        there, there_host, reader = self.tools()
        for tools in (here, there):
            tools.on_apply_position()
        reader.answer()
        self.assertEqual(self.logs(there), self.logs(here))
        self.assertEqual(self.places(there_host), self.places(here_host))

    def test_an_edit_the_reader_couldnt_make_is_said(self):
        tools, host, reader = self.tools()
        tools.on_apply_position()
        host.timeline.GetUniqueId = lambda: "timeline-another"               # another timeline opened meanwhile
        reader.answer()
        self.assertEqual(host.busy, [True, False])
        self.assertIn("[Error in apply_position_to_timeline]: A different timeline is open in Resolve now – "
                      "nothing was changed.", self.logs(tools))
        self.assertEqual(tools._live_preview_undo_stack, [])
        self.assertEqual(host.timeline.tracks[1][1].tool.center(), (0.5, 0.5))
        tools.on_apply_position()                                            # and it can be pressed again
        self.assertEqual(len(reader.asked), 2)

    def test_bounding_is_read_there_measured_here_and_written_there(self):
        here, here_host, _ = self.tools(reader=False)
        there, there_host, reader = self.tools()
        for tools in (here, there):
            tools.on_bounding({"on": True, "left": 0.2, "right": 0.8})
            tools.on_apply_bounding()
        reader.answer(1)
        self.assertEqual(reader.names(), ["textplus.read", "textplus.bounding"])
        self.assertEqual(there_host.busy, [True])                           # up between the two
        reader.answer()
        self.assertEqual(there_host.busy, [True, False])
        self.assertEqual(self.places(there_host), self.places(here_host))
        self.assertNotEqual(there_host.timeline.tracks[0][0].tool.GetInput("Size"), 0.1)
        self.assertEqual(self.logs(there), self.logs(here))
        self.assertEqual(self.last(there, "toast"), self.last(here, "toast"))

    def word_timeline(self):
        s = 86400
        words = [Clip(w, s + i * 4, s + 60, Tool(w, (0.5, 0.5), 0.08)) for i, w in enumerate(("big", "world", "Hello"))]
        later = [Clip("one", s + 200, s + 260, Tool("one", (0.5, 0.3), 0.08)),
                 Clip("more", s + 210, s + 260, Tool("more", (0.5, 0.3), 0.08))]
        return Timeline([[words[0], later[0]], [words[1], later[1]], [words[2]]])

    def test_space_words_over_the_timeline_reads_there_and_writes_there(self):
        here, here_host, _ = self.tools(self.word_timeline(), reader=False, tab="wordbyword")
        there, there_host, reader = self.tools(self.word_timeline(), tab="wordbyword")
        for tools in (here, there):
            tools.on_set({"name": "wbw_scope", "value": "timeline"})
            tools.on_set({"name": "wbw_lines", "value": "1"})
            tools.on_space_words({})
        reader.answer()
        self.assertEqual(reader.names(), ["textplus.read", "textplus.space"])
        self.assertEqual(there_host.busy, [True, False])
        self.assertEqual(self.places(there_host), self.places(here_host))
        self.assertNotEqual(there_host.timeline.tracks[0][0].tool.center(), (0.5, 0.5))
        self.assertEqual(self.last(there, "toast")["text"], "Spaced 2 groups of words")
        self.assertEqual(self.logs(there), self.logs(here))
        there.on_undo()                                                      # one step, on Buddy's own clips
        self.assertEqual({c.tool.center() for c in there_host.timeline.tracks[0]}, {(0.5, 0.5), (0.5, 0.3)})

    def test_animations_go_on_there_and_undo_puts_back_what_was_on(self):
        tools, host, reader = self.tools(anim_timeline(), tab="animation")
        one = host.timeline.tracks[0][0]
        tools.on_apply_animation()
        self.assertEqual(reader.names(), ["textplus.animate"])
        self.assertIsNone(E.preset_on(one.comp.text_plus))                   # not yet: it's the reader's
        reader.answer()
        self.assertEqual(E.preset_on(one.comp.text_plus)[0], "Pop (Scale)")
        self.assertEqual(self.last(tools, "toast")["text"], "Animated 2 Text+ clip(s)")
        tools.on_remove_animations()
        reader.answer()
        self.assertIsNone(E.preset_on(one.comp.text_plus))
        self.assertEqual(self.last(tools, "toast")["text"], "Removed animations from 2 Text+ clip(s)")
        tools.on_undo()                                                      # the preset JSON brought back, put on
        self.assertEqual(E.preset_on(one.comp.text_plus),
                         ("Pop (Scale)", tools.opts.anim_speed, tools.opts.anim_direction, 24.0))
        self.assertEqual(self.last(tools, "toast")["text"], "Undid animation change")

    def test_font_style_goes_on_there_without_pumping(self):
        tools, host, reader = self.tools(tab="style")
        tools.on_set({"name": "font_size", "value": 0.2})
        tools.on_set({"name": "style_scope", "value": "track"})
        tools.on_set({"name": "style_track", "value": 2})
        tools.on_apply_style()
        self.assertEqual(reader.names(), ["textplus.style"])
        self.assertFalse(tools._preview_timer.isActive())                    # no poll while it's made
        reader.answer()
        self.assertEqual([c.tool.GetInput("Size") for c in host.timeline.tracks[1]], [0.2, 0.2])
        self.assertEqual(host.timeline.tracks[0][0].tool.GetInput("Size"), 0.1)
        self.assertEqual(host.pumped, [])                                    # Buddy was free: nothing to pump
        self.assertTrue(tools._preview_timer.isActive())
        self.assertEqual(self.last(tools, "toast")["text"], "Styled 2 Text+ clip(s)")

    def stack_tools(self, canned):
        s = 86400
        footage = Clip("C0001.MP4", s, s + 700, None)
        words = [Clip(w, s + i * 3, s + i * 3 + 3, Tool(w)) for i, w in enumerate(["You", "have", "to", "see"])]
        timeline = Timeline([[footage], words])
        timeline.playhead = "01:00:00:20"                                    # frame 86420
        timeline.selected = list(words)
        return self.tools(timeline, tab="wordbyword", canned=canned)

    def test_stack_is_made_there(self):
        tools, host, reader = self.stack_tools({"textplus.stack": lambda args: {
            "ok": True, "result": {"name": "Promo (Word-by-word)"}}})
        tools.on_stack_words()
        name, args = reader.asked[0]
        s = 86400
        self.assertEqual((name, args), ("textplus.stack", {"timeline_id": host.timeline.GetUniqueId(), "plan": [
            [2, s, 2, 20], [2, s + 3, 3, 17], [2, s + 6, 4, 14], [2, s + 9, 5, 11]]}))
        reader.answer()
        self.assertEqual(host.busy, [True, False])
        self.assertEqual(self.last(tools, "toast")["text"], "Stacked 4 Text+ clips on 4 tracks in 'Promo (Word-by-word)'")

    def test_a_stack_resolve_refused_is_said(self):
        tools, host, reader = self.stack_tools({"textplus.stack": lambda args: {
            "ok": False, "error": "Resolve didn't import the new timeline.", "type": "DrtError"}})
        tools.on_stack_words()
        reader.answer()
        self.assertEqual(self.last(tools, "alert")["text"], "Resolve didn't import the new timeline.")
        self.assertEqual(self.logs(tools)[-1], "[Error] Resolve didn't import the new timeline.")

    def test_the_style_settings_go_through_json(self):
        from pages.text_animator.options import Options
        from pages.text_animator.text_plus import TextPlusTools
        settings = TextPlusTools._style_settings(SimpleNamespace(opts=Options(Mem())))
        self.assertEqual(json.loads(json.dumps(settings)).keys(), settings.keys())
        tool = Tool("Hi")
        done, kept = tpr.style_tool(tool, "'Hi'", json.loads(json.dumps({**settings, "font_on": True})))
        self.assertTrue(done)
        self.assertFalse(kept)
        self.assertEqual(tool.GetInput("Size"), settings["font_size"])


if __name__ == "__main__":
    unittest.main()
