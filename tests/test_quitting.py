"""core/quitting.py: once Buddy has really decided to quit, it ends - the
window and tray icon gone at once, the single-instance pipe let go, and a
watchdog for a teardown that stalls. Nothing here ends the test process:
os._exit is replaced throughout."""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import _paths  # noqa: F401
from core import quitting

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from core import shell_window
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class Base(unittest.TestCase):
    def setUp(self):
        for name, value in (("_watchdog", None), ("_enabled", False)):
            self.addCleanup(setattr, quitting, name, getattr(quitting, name))
            setattr(quitting, name, value)
        self.exit = mock.patch.object(quitting.os, "_exit").start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        if quitting._watchdog:
            quitting._watchdog.cancel()


class QuittingTests(Base):
    def test_only_python_or_resolves_script_host_is_ended(self):
        for exe in (r"C:\Python\pythonw.exe", r"C:\Python\python.exe", "/usr/bin/python3.12",
                    r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fuscript.exe",
                    "/Applications/DaVinci Resolve/fuscript"):
            self.assertTrue(quitting.own_process(exe), exe)
        for exe in (r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe", ""):
            self.assertFalse(quitting.own_process(exe), exe)

    def test_nothing_is_armed_or_ended_until_main_enables_it(self):
        quitting.begin()
        self.assertFalse(quitting._watchdog)
        self.assertEqual(quitting.finish(3), 3)
        self.exit.assert_not_called()

    def test_once_enabled_the_loop_ending_ends_the_process(self):
        quitting.enable()
        llama = types.SimpleNamespace(stop_all=mock.Mock())
        with mock.patch.dict(sys.modules, {"pages.manual_chat.local_llama": llama}):
            quitting.finish(0)
        llama.stop_all.assert_called_once_with()       # atexit's job, which os._exit skips
        self.exit.assert_called_once_with(0)

    def test_the_watchdog_is_armed_once_and_ends_a_stuck_quit(self):
        quitting.enable()
        quitting.begin("closing")
        timer = quitting._watchdog
        self.assertTrue(timer.daemon)
        self.assertEqual(timer.interval, quitting.WATCHDOG_S)
        quitting.begin("about to quit")
        self.assertIs(quitting._watchdog, timer)
        timer.cancel()
        with mock.patch.object(quitting, "_trail") as trail:
            if sys.platform == "win32":
                with mock.patch("core.crash_log.dump_stacks") as dump:
                    quitting._stuck()
                self.assertIn("still running", dump.call_args.args[0])
            else:
                quitting._stuck()
        self.assertIn("ending it", trail.call_args.args[0])
        self.exit.assert_called_once_with(1)


@unittest.skipUnless(sys.platform == "win32", "the crash log is Windows'")
class StackDumpTests(unittest.TestCase):
    def test_a_stuck_buddy_leaves_every_threads_stack(self):
        import faulthandler
        from core import crash_log
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "crash.log")
            hook = sys.excepthook
            self.addCleanup(setattr, sys, "excepthook", hook)
            crash_log.enable(path)
            try:
                self.assertTrue(crash_log.dump_stacks("Buddy was still running"))
            finally:
                faulthandler.disable()
                crash_log._handle.close()
                crash_log._handle = None
            with open(path, encoding="utf-8") as f:
                text = f.read()
        self.assertIn("Buddy was still running", text)
        self.assertIn("test_a_stuck_buddy_leaves_every_threads_stack", text)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class CloseTests(Base):
    """The window's [X] with "Keep running in tray" off, and the tray's Quit."""

    class Shell:
        closeEvent = shell_window.ShellWindow.closeEvent
        _begin_quit = shell_window.ShellWindow._begin_quit
        _quit_app = shell_window.ShellWindow._quit_app

        def __init__(self, keep):
            self.shared_settings = {"keep_running_in_tray": keep, "_tray_notice_shown": True}
            self.tray_icon = mock.Mock()
            self._single_instance_server = mock.Mock()
            self.app = mock.Mock()
            self._quitting = self._quit_begun = False
            self.hidden = self.flushed = 0

        def hide(self):
            self.hidden += 1

        def close(self):
            self.closeEvent(mock.Mock())

        def confirm_quit_while_working(self, *_args):
            return True

        def _notify_pages_quitting(self):
            self.flushed += 1

        def _update_tray_show_action(self):
            pass

    def test_closing_with_the_tray_off_leaves_nothing_behind(self):
        shell = self.Shell(keep=False)
        with mock.patch.object(quitting, "begin") as begin:
            shell.closeEvent(mock.Mock())
        begin.assert_called_once_with("closing")
        self.assertEqual(shell.hidden, 1)
        shell.tray_icon.hide.assert_called_once_with()
        shell._single_instance_server.stop.assert_called_once_with()
        self.assertEqual(shell.flushed, 1)
        shell.app.quit.assert_called_once_with()

    def test_kept_in_the_tray_closing_only_hides(self):
        shell = self.Shell(keep=True)
        with mock.patch.object(quitting, "begin") as begin:
            shell.closeEvent(mock.Mock())
        begin.assert_not_called()
        shell.tray_icon.hide.assert_not_called()
        shell._single_instance_server.stop.assert_not_called()
        self.assertEqual(shell.flushed, 0)

    def test_the_trays_quit_goes_the_same_way_once(self):
        shell = self.Shell(keep=True)
        with mock.patch.object(quitting, "begin") as begin:
            shell._quit_app()
        begin.assert_called_once_with("quitting")
        shell.tray_icon.hide.assert_called_once_with()
        shell._single_instance_server.stop.assert_called_once_with()
        self.assertEqual(shell.flushed, 1)


if __name__ == "__main__":
    unittest.main()
