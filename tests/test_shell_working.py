"""A tool at work (ToolPage.working_on, pages/base.py): a spinner beside its
name in the sidebar and on its taskbar button, and Buddy asks before
quitting while it runs (core/shell_window.py)."""

import unittest
from unittest import mock

import _paths  # noqa: F401

try:
    from core import shell_window
    from pages.base import ToolPage
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class _Page:
    def __init__(self, name, doing=None):
        self.display_name, self.doing = name, doing

    def working_on(self):
        return self.doing

    rail_badge = ToolPage.rail_badge if HAVE_QT else None


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class WorkingTests(unittest.TestCase):
    class Shell:
        _working = shell_window.ShellWindow._working
        _badge = shell_window.ShellWindow._badge
        _check_working = shell_window.ShellWindow._check_working
        working_tools = shell_window.ShellWindow.working_tools
        confirm_quit_while_working = shell_window.ShellWindow.confirm_quit_while_working

        def __init__(self, pages):
            self.pages = pages
            self._working_seen = ()
            self.refreshed = 0

        def refresh_badges(self):
            self.refreshed += 1

    def test_a_tool_at_work_gets_the_spinner_badge(self):
        shell = self.Shell({"setup": _Page("Project Setup", "Making proxies"), "games": _Page("Games")})
        self.assertEqual(shell._badge("setup"), "working")
        self.assertIsNone(shell._badge("games"))
        self.assertEqual(shell.working_tools(), [("Project Setup", "Making proxies")])

    def test_marks_redraw_only_when_something_starts_or_stops(self):
        setup = _Page("Project Setup")
        shell = self.Shell({"setup": setup})
        shell._check_working()
        shell._check_working()
        self.assertEqual(shell.refreshed, 1)
        setup.doing = "Making proxies"
        shell._check_working()
        shell._check_working()
        self.assertEqual(shell.refreshed, 2)

    def test_quitting_asks_only_while_something_works(self):
        shell = self.Shell({"setup": _Page("Project Setup")})
        with mock.patch.object(shell_window, "confirm") as confirm:
            self.assertTrue(shell.confirm_quit_while_working())
        confirm.assert_not_called()
        shell.pages["setup"].doing = "Making proxies"
        for answer in (False, True):
            with mock.patch.object(shell_window, "confirm", return_value=answer) as confirm:
                self.assertIs(shell.confirm_quit_while_working(), answer)
            self.assertIn("Project Setup – Making proxies", confirm.call_args.args[2])
            self.assertEqual(confirm.call_args.kwargs["cancel"], "Keep working")

    def test_a_broken_tool_never_blocks_quitting(self):
        class Broken(_Page):
            def working_on(self):
                raise RuntimeError("bug")
        shell = self.Shell({"x": Broken("X")})
        self.assertIsNone(shell._badge("x"))
        self.assertTrue(shell.confirm_quit_while_working())


if __name__ == "__main__":
    unittest.main()
