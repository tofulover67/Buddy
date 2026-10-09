"""ShellWindow.ensure_connected (core/shell_window.py): a tool's action
that has to connect to Resolve first waits for the connect worker in an
event loop - the window keeps drawing (timers keep firing) instead of
freezing for the probe - and still returns the controller or raises."""

import sys
import time
import unittest
from unittest import mock

import _paths  # noqa: F401

try:
    from PySide6.QtCore import QObject, QTimer
    from PySide6.QtWidgets import QApplication
    from core import shell_window
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


if HAVE_QT:
    class Shell(QObject):
        """Just the connecting part of the shell."""
        ensure_connected = shell_window.ShellWindow.ensure_connected
        _connect = shell_window.ShellWindow._connect
        _connect_and_wait = shell_window.ShellWindow._connect_and_wait
        _connect_here = shell_window.ShellWindow._connect_here
        _on_connect_done = shell_window.ShellWindow._on_connect_done
        _forget_connect_worker = shell_window.ShellWindow._forget_connect_worker

        def __init__(self):
            super().__init__()
            self.controller = None
            self._connect_worker = None
            self.busy_overlay = None
            self.connected = []

        def _set_connected(self, on):
            self.connected.append(on)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class EnsureConnectedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def slow(self, result, seconds=0.4):
        def connect():
            time.sleep(seconds)
            if isinstance(result, Exception):
                raise result
            return result
        return mock.patch.object(shell_window, "resolve_connect", side_effect=connect)

    def test_the_window_keeps_going_while_it_connects(self):
        shell, controller, ticks = Shell(), object(), []
        started = time.perf_counter()
        timer = QTimer(interval=20, timeout=lambda: ticks.append(time.perf_counter() - started))
        timer.start()
        with self.slow(controller):
            self.assertIs(shell.ensure_connected(), controller)
        timer.stop()
        self.assertGreaterEqual(len(ticks), 5)                    # a frozen window: none until the end
        self.assertLess(ticks[0], 0.2)
        self.assertEqual(shell.connected, [True])
        self.assertIsNone(shell._connect_worker)
        with mock.patch.object(shell_window, "resolve_connect") as again:
            self.assertIs(shell.ensure_connected(), controller)   # connected: no second attempt
        again.assert_not_called()

    def test_no_resolve_still_raises(self):
        shell = Shell()
        with self.slow(RuntimeError("no Resolve"), 0.1):
            with self.assertRaises(shell_window.ResolveConnectionError):
                shell.ensure_connected()
        self.assertEqual(shell.connected, [False])

    def test_a_second_call_while_connecting_waits_for_the_same_attempt(self):
        shell, controller, answers = Shell(), object(), []
        QTimer.singleShot(50, lambda: answers.append(shell.ensure_connected()))   # mid-wait, from the loop
        with self.slow(controller) as connect:
            answers.append(shell.ensure_connected())
        self.assertEqual(answers, [controller, controller])
        self.assertEqual(connect.call_count, 1)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ConnectionFailedTests(unittest.TestCase):
    """Reconnect failing says how to get Resolve back: quit Buddy (from the
    tray - the menu bar on a Mac - when it keeps running there), restart
    Resolve, open Buddy again."""

    class Shell:
        _connection_failed = shell_window.ShellWindow._connection_failed

        def __init__(self, tray=True, keep=True):
            self.tray_icon = object() if tray else None
            self.shared_settings = {"keep_running_in_tray": keep}
            self.quit = 0

        def _quit_app(self):
            self.quit += 1

    def failed(self, shell, error, answer=False):
        with mock.patch.object(shell_window, "confirm", return_value=answer) as confirm, \
                mock.patch.object(shell_window, "alert") as alert:
            shell._connection_failed(error)
        return confirm, alert

    def test_kept_in_the_tray_it_says_to_quit_from_the_tray(self):
        shell = self.Shell()
        confirm, alert = self.failed(shell, shell_window.ResolveConnectionError("Could not reach"))
        alert.assert_not_called()
        text = confirm.call_args.args[2]
        self.assertIs(text, shell_window.RESTART_STEPS_TRAY)
        for step in ("menu bar" if sys.platform == "darwin" else "system tray",
                     "choose Quit", "Quit DaVinci Resolve", "Open Buddy again"):
            self.assertIn(step, text)
        self.assertEqual(confirm.call_args.kwargs["ok"], "Quit Buddy")
        self.assertEqual(shell.quit, 0)                      # Later: nothing quits

    def test_without_the_tray_it_just_says_close(self):
        for shell in (self.Shell(keep=False), self.Shell(tray=False)):
            confirm, _alert = self.failed(shell, shell_window.ResolveConnectionError("Could not reach"))
            self.assertIs(confirm.call_args.args[2], shell_window.RESTART_STEPS)
            self.assertNotIn("tray", shell_window.RESTART_STEPS)

    def test_quit_buddy_quits(self):
        shell = self.Shell()
        self.failed(shell, shell_window.ResolveConnectionError("Could not reach"), answer=True)
        self.assertEqual(shell.quit, 1)

    def test_another_error_is_shown_as_it_is(self):
        shell = self.Shell()
        confirm, alert = self.failed(shell, ImportError("No module named DaVinciResolveScript"))
        confirm.assert_not_called()
        self.assertEqual(alert.call_args.args[2], "No module named DaVinciResolveScript")

    def test_translated(self):
        from core import i18n
        for text in (shell_window.RESTART_STEPS_TRAY, shell_window.RESTART_STEPS):
            for language in i18n.LANGUAGES:
                if language != i18n.DEFAULT_LANGUAGE:
                    self.assertNotEqual(i18n.translate(text, language), text, language)


if __name__ == "__main__":
    unittest.main()
