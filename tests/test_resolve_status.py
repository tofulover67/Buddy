"""The header's project and timeline (core/resolve_status.py, and the shell's
refresh_resolve_status): what's open in Resolve, read through the reader's
process on connecting, on coming to a tool and on coming back to Buddy."""

import unittest
from unittest import mock

import _paths  # noqa: F401
from core import resolve_reader_child as child
from core import resolve_status

try:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from core import shell_window
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class Timeline:
    def __init__(self, name="Cut v3", settings=None):
        self.name, self.settings = name, settings or {}

    def GetName(self): return self.name
    def GetSetting(self, key): return self.settings.get(key, "")


class Project:
    def __init__(self, timeline=None, settings=None):
        self.timeline, self.settings = timeline, settings or {}

    def GetName(self): return "Harbour Film"
    def GetCurrentTimeline(self): return self.timeline
    def GetSetting(self, key): return self.settings.get(key, "")


class Controller:
    def __init__(self, project=None):
        self.project = project
        self.resolve = mock.Mock(GetVersionString=mock.Mock(return_value="21.1"))

    def current_project(self):
        return self.project


FORMAT = {"timelineFrameRate": "23.976", "timelineResolutionWidth": "1920", "timelineResolutionHeight": "1080"}


class StatusTests(unittest.TestCase):
    def test_project_timeline_and_its_format(self):
        status = resolve_status.status(Controller(Project(Timeline(settings=FORMAT))))
        self.assertEqual(status, {"project": "Harbour Film", "timeline": "Cut v3", "fps": "23.976",
                                  "width": 1920, "height": 1080})

    def test_a_timeline_without_its_own_settings_shows_the_projects(self):
        project = Project(Timeline(), {"timelineFrameRate": 24.0, "timelineResolutionWidth": 3840,
                                       "timelineResolutionHeight": 2160})
        status = resolve_status.status(Controller(project))
        self.assertEqual((status["fps"], status["width"], status["height"]), ("24", 3840, 2160))

    def test_no_timeline_and_no_project(self):
        self.assertEqual(resolve_status.status(Controller(Project()))["timeline"], "")
        self.assertEqual(resolve_status.status(Controller(None))["project"], "")

    def test_a_resolve_that_raises_gives_what_was_read(self):
        timeline = Timeline()
        timeline.GetSetting = mock.Mock(side_effect=RuntimeError("gone"))
        status = resolve_status.status(Controller(Project(timeline)))
        self.assertEqual((status["timeline"], status["fps"], status["width"]), ("Cut v3", "", 0))

    def test_frame_rates_as_written(self):
        self.assertEqual([resolve_status.fps_text(v) for v in ("24.0", 23.976023976, "59.94", "", None, "x", 0)],
                         ["24", "23.976", "59.94", "", "", "", ""])

    def test_the_reader_process_knows_the_job(self):
        reply = child.answer({"id": 1, "do": "resolve.status"}, Controller(Project(Timeline(settings=FORMAT))))
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["result"]["timeline"], "Cut v3")


if HAVE_QT:
    class Calls:
        """ResolveCalls, answering at once."""
        def __init__(self, parent=None):
            self.asked = []

        def busy(self):
            return False

        def call(self, name, args, job, done):
            self.asked.append(name)
            done(job(), None)
            return True

    class Shell(QObject):
        refresh_resolve_status = shell_window.ShellWindow.refresh_resolve_status
        _on_resolve_status = shell_window.ShellWindow._on_resolve_status
        _set_connected = shell_window.ShellWindow._set_connected

        def __init__(self, controller):
            super().__init__()
            self.controller, self.connected, self.pages = controller, False, {}
            self._resolve_status, self._status_calls, self._status_asked = None, None, 0.0
            self.pushed = 0

        def push_header(self):
            self.pushed += 1


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ShellStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        patch = mock.patch.object(shell_window, "ResolveCalls", Calls)
        patch.start()
        self.addCleanup(patch.stop)
        self.project = Project(Timeline(settings=FORMAT))
        self.shell = Shell(Controller(self.project))

    def test_connecting_reads_what_is_open(self):
        self.shell._set_connected(True)
        self.assertEqual(self.shell._resolve_status["project"], "Harbour Film")
        self.assertEqual(self.shell._status_calls.asked, ["resolve.status"])

    def test_coming_to_a_tool_reads_again_but_not_more_than_once_a_moment(self):
        self.shell._set_connected(True)
        self.project.timeline = Timeline("Social 9x16")
        self.shell.refresh_resolve_status()             # right after: not asked again
        self.assertEqual(self.shell._resolve_status["timeline"], "Cut v3")
        self.shell._status_asked -= shell_window.STATUS_EVERY_S + 0.1
        pushed = self.shell.pushed
        self.shell.refresh_resolve_status()
        self.assertEqual(self.shell._resolve_status["timeline"], "Social 9x16")
        self.assertEqual(self.shell.pushed, pushed + 1)

    def test_a_forced_read_does_not_wait(self):
        self.shell._set_connected(True)
        self.project.timeline = Timeline("Social 9x16")
        self.shell.refresh_resolve_status(force=True)
        self.assertEqual(self.shell._resolve_status["timeline"], "Social 9x16")

    def test_nothing_changed_draws_nothing(self):
        self.shell._set_connected(True)
        pushed = self.shell.pushed
        self.shell._status_asked -= shell_window.STATUS_EVERY_S + 0.1
        self.shell.refresh_resolve_status()             # coming to a tool: the same project and timeline
        self.assertEqual(self.shell.pushed, pushed)

    def test_disconnected_forgets_it_and_asks_nothing(self):
        self.shell._set_connected(True)
        self.shell._set_connected(False)
        self.assertIsNone(self.shell._resolve_status)
        asked = list(self.shell._status_calls.asked)
        self.shell.refresh_resolve_status(force=True)
        self.assertEqual(self.shell._status_calls.asked, asked)

    def test_a_failed_read_shows_plain_connected(self):
        self.shell._set_connected(True)
        self.shell._on_resolve_status(None, RuntimeError("Resolve went away"))
        self.assertIsNone(self.shell._resolve_status)


if __name__ == "__main__":
    unittest.main()
