"""Podcast's Shorts (app/pages/podcast/shorts/): the clips selected on the
timeline grouped into sections, the short built from a duplicate of the
timeline (everything else deleted from the copy, the sections closed up,
the copy reshaped), and the page between them. No Resolve: a fake one that
duplicates, deletes and moves as the real one does."""

import os
import re
import tempfile
import unittest
from unittest import mock

import _paths
from core import resolve_reader_child
from pages.podcast.shorts import model as M
from pages.podcast.shorts import resolve_ext

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

START = 90000           # 01:00:00:00 at 25 fps


# ---------------------------------------------------------------- model --

class ModelTests(unittest.TestCase):
    def test_clips_that_touch_are_one_section(self):
        clips = M.clean([("video", 1, 100, 200), ("audio", 1, 100, 200), ("video", 1, 200, 260),
                         ("video", 1, 400, 450), {"kind": "Audio", "track": 2, "start": 410, "end": 450}])
        parts = M.sections(clips)
        self.assertEqual([(s["start"], s["end"], len(s["clips"])) for s in parts], [(100, 260, 3), (400, 450, 2)])
        self.assertEqual(M.length(parts), 210)
        self.assertEqual(M.tracks(parts[1]["clips"]), "V1 A2")

    def test_rows_that_are_no_clip_are_left_out(self):
        self.assertEqual(M.clean([("video", 1, 10, 10), ("title", 1, 0, 5), ("audio", 0, 0, 5), None,
                                  ("audio", 1, 0, 5), ("audio", 1, 0, 5)]), [("audio", 1, 0, 5)])
        self.assertEqual(M.plans([]), [])

    def test_a_short_closes_its_sections_up_from_the_start(self):
        parts = M.sections(M.clean([("video", 1, 100, 200), ("audio", 1, 120, 200), ("video", 1, 400, 450)]))
        self.assertEqual(dict(M.moved(parts)), {("video", 1, 100, 200): ("video", 1, 0, 100),
                                                ("audio", 1, 120, 200): ("audio", 1, 20, 100),
                                                ("video", 1, 400, 450): ("video", 1, 100, 150)})
        self.assertEqual(len(M.plans(M.clean([("video", 1, 100, 200), ("video", 1, 400, 450)]), separate=True)), 2)

    def test_names_are_ones_nobody_has(self):
        base = M.default_name("Tess and Sam")
        self.assertEqual(base, "Tess and Sam – Short")
        self.assertEqual(M.default_name("Tess and Sam – Short 3"), base)      # a short of a short
        self.assertEqual(M.names(base, 1, set()), ["Tess and Sam – Short 1"])
        self.assertEqual(M.names(base, 2, {"Tess and Sam – Short 1"}), ["Tess and Sam – Short 2", "Tess and Sam – Short 3"])
        self.assertEqual(M.names("Best bit", 1, set(), numbered=False), ["Best bit"])
        self.assertEqual(M.names("Best bit", 1, {"Best bit"}, numbered=False), ["Best bit 2"])

    def test_a_typed_name_loses_what_resolve_refuses(self):
        self.assertEqual(M.clean_name('  Sam: "the  best/worst" day?  '), "Sam the best worst day")
        self.assertEqual(M.clean_name(None), "")
        self.assertEqual(len(M.clean_name("x" * 300)), M.NAME_MAX)


# ------------------------------------------------------- a fake Resolve --

class Item:
    def __init__(self, timeline, kind, track, start, end, name):
        self.timeline, self.kind, self.track, self.start, self.end, self.name = timeline, kind, track, start, end, name

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end

    def GetName(self):
        return self.name

    def GetTrackTypeAndIndex(self):
        return [self.kind, self.track]

    def SetPosition(self, position, _linked=True):
        if not self.timeline.project.can_set_position:
            return False
        to = int(position["startFrame"])
        self.start, self.end = to, to + self.end - self.start
        return True


class OldTimeline:
    """A timeline as Resolve before 21.0.4 has it: its selection can't be read."""

    def __init__(self, project, name, layout=(), uid=None):
        self.project, self.name = project, name
        self.uid = uid or f"tl-{len(project.timelines) + 1}"
        self.items = [Item(self, *row) for row in layout]
        self.selected = []
        self.settings = {"timelineFrameRate": "25", "timelineResolutionWidth": "1920",
                         "timelineResolutionHeight": "1080", "useCustomSettings": "0"}
        project.timelines.append(self)

    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return self.name

    def GetStartFrame(self):
        return START

    def GetSetting(self, key):
        return self.settings.get(key, "")

    def SetSetting(self, key, value):
        if key.startswith("timelineResolution") and (self.settings["useCustomSettings"] != "1"
                                                     or not self.project.can_resize):
            return False
        self.settings[key] = value
        return True

    def GetTrackCount(self, kind):
        return max([i.track for i in self.items if i.kind == kind], default=0)

    def GetItemListInTrack(self, kind, track):
        return sorted((i for i in self.items if i.kind == kind and i.track == track), key=lambda i: i.start)

    def DuplicateTimeline(self, name):
        if any(t.name == name for t in self.project.timelines):
            return None
        copy = type(self)(self.project, name, [(i.kind, i.track, i.start, i.end, i.name) for i in self.items])
        copy.settings = dict(self.settings)
        self.project.current = copy
        return copy

    def DeleteClips(self, items, _ripple=False):
        if self.project.locked:
            return False
        self.items = [i for i in self.items if i not in items]
        return True

    def MoveItems(self, items, position, _linked=True):
        if not self.project.can_move_items:
            return False
        shift = int(position["startFrame"]) - min(i.start for i in items)
        for item in items:
            item.start, item.end = item.start + shift, item.end + shift
        return True


class Timeline(OldTimeline):
    def GetSelectedClips(self):
        return list(self.selected)


class Project:
    def __init__(self):
        self.timelines, self.current, self.deleted = [], None, []
        self.locked, self.can_move_items, self.can_set_position, self.can_resize = False, True, True, True

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def GetMediaPool(self):
        pool = mock.Mock()
        pool.DeleteTimelines.side_effect = self._delete
        return pool

    def _delete(self, timelines):
        for timeline in timelines:
            self.timelines.remove(timeline)
            self.deleted.append(timeline.name)
        return True


class Controller:
    def __init__(self, project):
        self.project = project

    def current_project(self):
        return self.project


def clip(start, end, name="A001_0001.MXF"):
    """A camera, its mic and a second mic, cut at the same frames."""
    return [("video", 1, START + start, START + end, name), ("audio", 1, START + start, START + end, "tess.wav"),
            ("audio", 2, START + start, START + end, "sam.wav")]


def podcast(kind=Timeline):
    """Five stretches on three tracks, and music under all of it; the second
    and fourth stretches selected on every track but the music's."""
    project = Project()
    layout = [row for a, b in ((0, 250), (250, 750), (750, 1500), (1500, 1700), (1700, 3000)) for row in clip(a, b)]
    timeline = kind(project, "Tess and Sam", layout + [("audio", 3, START, START + 3000, "music.wav")])
    project.current = timeline
    timeline.selected = [i for i in timeline.items if i.track != 3 and i.start - START in (250, 1500)]
    return project, timeline


def layout(timeline):
    return sorted((i.kind, i.track, i.start - START, i.end - START) for i in timeline.items)


def selection(project):
    return resolve_ext.read(Controller(project))


class ReadTests(unittest.TestCase):
    def test_what_is_selected_is_read_from_the_timelines_start(self):
        project, _tl = podcast()
        data = selection(project)
        self.assertEqual((data["name"], data["fps"], data["size"], data["can_select"]),
                         ("Tess and Sam", 25.0, [1920, 1080], True))
        self.assertEqual(M.sections(M.clean(data["selection"]))[0]["start"], 250)
        self.assertEqual(len(data["selection"]), 6)

    def test_an_older_resolve_says_it_cant_tell(self):
        project, _tl = podcast(OldTimeline)
        data = selection(project)
        self.assertFalse(data["can_select"])
        self.assertEqual(data["selection"], [])


class BuildTests(unittest.TestCase):
    def build(self, project, **options):
        data = selection(project)
        return resolve_ext.build_here(Controller(project), data["selection"], data["id"], **options)

    def test_a_short_is_the_selection_closed_up_in_a_copy(self):
        project, tl = podcast()
        before = layout(tl)
        result = self.build(project)
        self.assertEqual([m["name"] for m in result["made"]], ["Tess and Sam – Short 1"])
        self.assertEqual((result["made"][0]["frames"], result["made"][0]["clips"]), (700, 6))
        self.assertEqual(result["warnings"], [])
        short = project.timelines[1]
        self.assertEqual(layout(short), sorted([(k, t, a, b) for a, b in ((0, 500), (500, 700))
                                                for k, t in (("video", 1), ("audio", 1), ("audio", 2))]))
        self.assertEqual((short.settings["timelineResolutionWidth"], short.settings["timelineResolutionHeight"],
                          short.settings["timelineInputResMismatchBehavior"]), ("1080", "1920", "scaleToCrop"))
        self.assertIs(project.current, short)
        # The podcast itself: every clip where it was, its size its own.
        self.assertEqual(layout(tl), before)
        self.assertEqual(tl.settings["timelineResolutionWidth"], "1920")
        self.assertEqual(result["source"], {"id": tl.uid, "name": "Tess and Sam"})

    def test_the_next_short_gets_the_next_number_and_a_typed_name_is_kept(self):
        project, tl = podcast()
        self.build(project)
        project.current = tl
        self.assertEqual(self.build(project)["made"][0]["name"], "Tess and Sam – Short 2")
        project.current = tl
        self.assertEqual(self.build(project, name="Sam's: best bit")["made"][0]["name"], "Sam's best bit")
        project.current = tl
        self.assertEqual(self.build(project, name="Sam's best bit")["made"][0]["name"], "Sam's best bit 2")

    def test_a_short_for_each_section(self):
        project, tl = podcast()
        result = self.build(project, separate=True, fmt="same", open_new=False)
        self.assertEqual([(m["name"], m["frames"]) for m in result["made"]],
                         [("Tess and Sam – Short 1", 500), ("Tess and Sam – Short 2", 200)])
        self.assertEqual(layout(project.timelines[2]), [("audio", 1, 0, 200), ("audio", 2, 0, 200), ("video", 1, 0, 200)])
        self.assertEqual(project.timelines[2].settings["timelineResolutionWidth"], "1920")
        self.assertIs(project.current, tl)          # asked to stay on the podcast
        self.assertEqual(result["opened"], "")

    def test_clips_are_moved_one_by_one_where_resolve_cant_move_them_together(self):
        project, _tl = podcast()
        project.can_move_items = False
        result = self.build(project)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(layout(project.timelines[1])[-1], ("video", 1, 500, 700))

    def test_gaps_resolve_wont_close_are_said_and_the_short_kept(self):
        project, _tl = podcast()
        project.can_move_items = project.can_set_position = False
        result = self.build(project)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Delete Gaps", result["warnings"][0])
        self.assertEqual(layout(project.timelines[1])[-1], ("video", 1, 1500, 1700))

    def test_a_size_resolve_wont_take_is_said(self):
        project, _tl = podcast()
        project.can_resize = False
        result = self.build(project, fmt="square")
        self.assertIn("Timeline Settings", result["warnings"][0])

    def test_a_copy_that_cant_be_cut_down_is_deleted_again(self):
        project, tl = podcast()
        project.locked = True
        with self.assertRaises(resolve_ext.BuildError) as raised:
            self.build(project)
        self.assertIn("unlock", str(raised.exception))
        self.assertEqual(project.timelines, [tl])
        self.assertEqual(project.deleted, ["Tess and Sam – Short 1"])
        self.assertIs(project.current, tl)

    def test_a_selection_gone_stale_makes_nothing(self):
        project, tl = podcast()
        data = selection(project)
        tl.selected[0].end -= 10
        with self.assertRaises(resolve_ext.BuildError):
            resolve_ext.build(Controller(project), data["selection"], data["id"])
        self.assertEqual(project.timelines, [tl])
        with self.assertRaises(resolve_ext.BuildError):
            resolve_ext.build(Controller(project), data["selection"], "another")
        with self.assertRaises(resolve_ext.BuildError):
            resolve_ext.build(Controller(project), [], tl.uid)

    def test_back_opens_the_timeline_the_shorts_came_from(self):
        project, tl = podcast()
        self.build(project)
        self.assertEqual(resolve_ext.open_here(Controller(project), tl.uid), "Tess and Sam")
        self.assertIs(project.current, tl)
        with self.assertRaises(resolve_ext.BuildError):
            resolve_ext.open_here(Controller(project), "gone")


# ----------------------------------------------------------------- page --

class Mem(dict):
    def save(self):
        pass


class Host:
    def __init__(self, controller):
        self.controller, self.connected, self.tools, self.busy = controller, True, {}, []

    def tool_settings(self, tool_id, defaults=None):
        return self.tools.setdefault(tool_id, Mem(defaults or {}))

    def ensure_connected(self):
        return self.controller

    def set_busy(self, on, message=None):
        self.busy.append((on, message))

    def theme_tokens(self):
        return {"surface": "#202020"}


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

    def make(self, kind=Timeline):
        from pages.podcast.shorts.page import ShortsPage
        self.project, self.tl = podcast(kind)
        self.host = Host(Controller(self.project))
        page = ShortsPage(self.host)
        self.addCleanup(page.deleteLater)
        page._read_timer.stop()
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
        page._calls.reader = mock.Mock(available=lambda: False)
        page._read()
        return page

    def last(self, name):
        return [p for n, p in self.events if n == name][-1]

    def test_the_selection_is_shown_as_sections(self):
        self.make()
        state = self.last("state")
        self.assertEqual((state["timeline"], state["clips"], state["seconds"]), ("Tess and Sam", 6, 28.0))
        self.assertEqual(state["sections"], [{"start": 10.0, "end": 30.0, "clips": 3, "tracks": "V1 A1 A2"},
                                             {"start": 60.0, "end": 68.0, "clips": 3, "tracks": "V1 A1 A2"}])
        self.assertEqual((state["default_name"], state["over"], state["problem"]), ("Tess and Sam – Short", False, ""))

    def test_the_same_state_isnt_sent_twice_and_a_new_selection_is(self):
        page = self.make()
        sent = len(self.events)
        page._read()
        self.assertEqual(len(self.events), sent)
        self.tl.selected = self.tl.selected[:3]
        page._read()
        self.assertEqual(len(self.last("state")["sections"]), 1)

    def test_an_older_resolve_is_told_what_it_needs(self):
        self.make(OldTimeline)
        self.assertIn("21.0.4", self.last("state")["problem"])

    def test_make_builds_the_short_and_offers_the_way_back(self):
        page = self.make()
        page.on_build({"name": ""})
        self.assertEqual(self.host.busy, [(True, "Making the short…"), (False, None)])
        self.assertEqual(len(self.project.timelines), 2)
        made = self.last("made")
        self.assertEqual(made["shorts"], [{"name": "Tess and Sam – Short 1", "seconds": 28.0, "clips": 6}])
        self.assertEqual((made["source"], made["can_go_back"]), ("Tess and Sam", True))
        self.assertIn("Your timeline is as it was", self.last("toast")["text"])
        self.assertEqual(self.last("state")["timeline"], "Tess and Sam – Short 1")
        page.on_back()
        self.assertIs(self.project.current, self.tl)
        self.assertFalse(self.last("made")["can_go_back"])

    def test_a_short_each_is_only_made_when_asked_and_there_are_several(self):
        page = self.make()
        page.on_option({"key": "separate", "value": True})
        page.on_option({"key": "open_new", "value": False})
        page.on_option({"key": "format", "value": "nonsense"})
        page.on_build({})
        self.assertEqual(self.host.busy[0], (True, "Making the shorts…"))
        self.assertEqual([s["name"] for s in self.last("made")["shorts"]],
                         ["Tess and Sam – Short 2", "Tess and Sam – Short 1"])
        self.assertIs(self.project.current, self.tl)

    def test_nothing_selected_makes_nothing_and_a_failure_is_said(self):
        page = self.make()
        self.tl.selected = []
        page._read()
        page.on_build({})
        self.assertEqual(len(self.project.timelines), 1)
        self.assertIn("Select the clips", self.last("toast")["text"])
        self.tl.selected = [i for i in self.tl.items if i.track == 1]
        page._read()
        self.project.locked = True
        page.on_build({})
        self.assertEqual(self.last("alert")["title"], "Couldn't make the short")
        self.assertEqual(len(self.project.timelines), 1)

    def test_podcast_holds_it_as_its_fourth_tab(self):
        from pages.podcast import page as podcast_page
        self.assertEqual(podcast_page.TABS, ("sync", "switch", "text", "shorts"))


class WiringTests(unittest.TestCase):
    def test_every_podcast_page_draws_the_same_four_tabs(self):
        root = _paths.APP / "pages" / "podcast"
        for folder, own in (("sync", "sync"), ("switch", "switch"), ("text_edit", "text"), ("shorts", "shorts")):
            html = (root / folder / "web" / "index.html").read_text(encoding="utf-8")
            tabs = re.findall(r'data-tab="(\w+)" aria-selected="(\w+)"><span class="pod-n">(\d)</span>([^<]+)<', html)
            self.assertEqual([(t, n, label) for t, _s, n, label in tabs],
                             [("sync", "1", "Sync"), ("switch", "2", "Auto Switch"), ("text", "3", "Text Edit"),
                              ("shorts", "4", "Shorts")], folder)
            self.assertEqual([t for t, selected, _n, _l in tabs if selected == "true"], [own], folder)

    def test_its_jobs_run_in_the_readers_process(self):
        for name in ("podshorts.read", "podshorts.build", "podshorts.open"):
            self.assertIn(name, resolve_reader_child.JOBS)


if __name__ == "__main__":
    unittest.main()
