"""The running timer in Buddy's header: Time Tracker's timer, with Pause and
Resume, whatever tool is in front (TrackerEngine.header_status, the shell's
_header_timer)."""

import tempfile
import unittest

import _paths  # noqa: F401
from pages.time_tracker.data_manager import DataManager
from pages.time_tracker.engine import TrackerEngine

try:
    from core import shell_window
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class EngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dm = DataManager(base_dir=self._tmp.name)
        self.engine = TrackerEngine(self.dm)
        self.engine.sync_manual_mode()

    def test_nothing_tracked_is_nothing_in_the_header(self):
        self.assertIsNone(self.engine.header_status())
        self.engine.header_toggle()                         # nothing to pause: does nothing
        self.assertIsNone(self.dm.get_open_entry())

    def test_a_tracked_project_shows_and_pauses_from_the_header(self):
        self.engine.on_project_detected("Harbour Film", True)
        status = self.engine.header_status()
        self.assertEqual((status["project"], status["running"], status["away"]), ("Harbour Film", True, False))
        self.engine.header_toggle()
        self.assertFalse(self.engine.header_status()["running"])
        self.assertTrue(self.dm.settings["manually_paused"])            # as the page's own Pause does
        self.engine.header_toggle()
        self.assertTrue(self.engine.header_status()["running"])
        self.assertIsNotNone(self.dm.get_open_entry())                  # the same entry, never ended

    def test_manual_tracking_pauses_its_own_way(self):
        self.dm.settings["manual_tracking"] = True
        self.engine.sync_manual_mode()
        self.engine.detected_project_name = "Harbour Film"
        self.engine.manual_toggle(True)
        self.assertTrue(self.engine.header_status()["running"])
        self.engine.header_toggle()
        self.assertFalse(self.engine.header_status()["running"])
        self.assertFalse(self.dm.settings.get("manually_paused", False))    # auto mode's switch, left alone


class Page:
    is_placeholder = False

    def __init__(self, status=None):
        self.status, self.actions = status, []

    def header_status(self):
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    def header_action(self, action):
        self.actions.append(action)
        self.status = dict(self.status, running=not self.status["running"])


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ShellTests(unittest.TestCase):
    class Shell:
        _header_timer = shell_window.ShellWindow._header_timer if HAVE_QT else None
        header_timer_action = shell_window.ShellWindow.header_timer_action if HAVE_QT else None
        _check_working = shell_window.ShellWindow._check_working if HAVE_QT else None
        _working = shell_window.ShellWindow._working if HAVE_QT else None

        def __init__(self, pages):
            self.pages, self._working_seen = pages, ()
            self.pushed, self.opened = 0, []

        def refresh_badges(self):
            pass

        def push_header(self):
            self.pushed += 1

        def switch_tool(self, tool_id):
            self.opened.append(tool_id)

    def timer(self, **more):
        return dict({"project": "Harbour Film", "seconds": 75, "running": True, "away": False}, **more)

    def test_the_tool_with_a_timer_is_the_headers(self):
        shell = self.Shell({"games": Page(), "time_tracker": Page(self.timer())})
        self.assertEqual(shell._header_timer(), dict(self.timer(), tool_id="time_tracker"))
        self.assertIsNone(self.Shell({"games": Page(), "broken": Page(RuntimeError("no"))})._header_timer())
        placeholder = Page(self.timer())
        placeholder.is_placeholder = True
        self.assertIsNone(self.Shell({"time_tracker": placeholder})._header_timer())

    def test_the_header_is_sent_again_only_when_the_timer_changes(self):
        tracker = Page(self.timer())
        shell = self.Shell({"time_tracker": tracker})
        shell._check_working()
        shell._check_working()
        self.assertEqual(shell.pushed, 1)                   # it started
        tracker.status = self.timer(seconds=90)             # the clock counts in the header itself
        shell._check_working()
        self.assertEqual(shell.pushed, 1)
        tracker.status = self.timer(running=False, away=True)
        shell._check_working()
        tracker.status = None
        shell._check_working()
        self.assertEqual(shell.pushed, 3)

    def test_pause_goes_to_the_tool_and_a_click_opens_it(self):
        tracker = Page(self.timer())
        shell = self.Shell({"time_tracker": tracker})
        shell.header_timer_action("toggle")
        self.assertEqual((tracker.actions, shell.pushed), (["toggle"], 1))
        shell._check_working()
        self.assertEqual(shell.pushed, 1)                   # already drawn
        shell.header_timer_action("open")
        self.assertEqual(shell.opened, ["time_tracker"])
        self.Shell({}).header_timer_action("toggle")        # no timer: nothing to do


if __name__ == "__main__":
    unittest.main()
