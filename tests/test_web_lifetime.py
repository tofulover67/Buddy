"""A closed web window is freed on the GUI thread, straight away - never left
for Python's cycle collector, which runs on whichever thread triggers it
(Ask Buddy's agent, a Resolve poll). Chromium aborts when a web view is
destroyed off the GUI thread: Buddy crashed every hour or two - on Windows
with an access violation in Qt6WebEngineCore.dll, on a Mac with no error.
core/web_page.py's _Bridge holds its window weakly (no cycle),
open_settings() deletes its dialog, and core/gui_gc.py only collects from a
GUI-thread timer. Offscreen, against a stand-in shell - never the real
settings - and, on Windows, against a real ShellWindow too."""

import gc
import os
import sys
import unittest
import weakref
from unittest import mock

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QTimer
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget
    import PySide6.QtWebEngineWidgets  # noqa: F401
    import core.shell_window as shell_window
    from core import gui_gc, message_dialog
    from core.message_dialog import MessageDialog, alert
    from core.settings_dialog import SettingsDialog
    from core.shell_window import ShellWindow
    from core.theme import get_theme_tokens
    from pages.base import ToolPage
    HAVE_WEB = True
except ImportError:  # pragma: no cover
    HAVE_WEB = False

# Where a leaked web window's objects come from.
WEB_MODULES = ("PySide6.QtWebEngine", "PySide6.QtWebChannel", "core.web_page", "core.message_dialog",
               "core.settings_dialog")


class Settings(dict):
    def save(self):
        pass


class Shell(QWidget if HAVE_WEB else object):
    """What a web window asks of the shell, and what open_settings() uses."""

    def __init__(self):
        super().__init__()
        self.shared_settings = Settings(theme="Resolve")
        self.pages, self._layout, self._current_tool_id = {}, "rail", None
        self.stack = self

    def theme_tokens(self):
        return get_theme_tokens("Resolve")

    def front(self):            # the pane stack's page in front: none here
        return None

    def _on_settings_applied(self):
        pass


def web_garbage():
    """Web-window objects Python's cycle collector found unreachable."""
    return [type(o).__name__ for o in gc.garbage
            if (type(o).__module__ or "").startswith(WEB_MODULES)]


def close_when_shown(shell, kind):
    """Closes the first `kind` dialog of `shell`'s once it's open (exec()
    blocks until then)."""
    timer = QTimer(shell)

    def check():
        for dialog in shell.findChildren(kind):
            if dialog.isVisible():
                timer.stop()
                dialog.done(QDialog.Rejected)

    timer.timeout.connect(check)
    timer.start(50)
    QTimer.singleShot(10000, timer.stop)


@unittest.skipUnless(HAVE_WEB, "PySide6 with QtWebEngine not installed")
class WebLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.shell = Shell()
        self.shell.resize(800, 600)
        self.shell.show()
        self.addCleanup(self._drop_shell)

    def _drop_shell(self):
        if self.shell is not None:
            self.shell.deleteLater()
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.shell = None

    def test_a_closed_message_leaves_no_web_view_for_the_collector(self):
        # Whatever earlier tests left behind isn't this test's garbage.
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        gc.collect()
        gc.set_debug(gc.DEBUG_SAVEALL)
        try:
            for text in ("One", "Two"):
                close_when_shown(self.shell, MessageDialog)
                alert(self.shell, "Test", text)
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertEqual(self.shell.findChildren(MessageDialog), [], "a closed message stayed alive")
            # Its window going is what used to turn it into cycle garbage.
            self._drop_shell()
            gc.collect()
            leaked = web_garbage()
        finally:
            gc.set_debug(0)
            gc.garbage.clear()
        self.assertEqual(leaked, [], "a closed window's web view was left for the cycle collector")

    def test_a_dropped_window_is_freed_without_the_collector(self):
        """The window and its bridge make no reference cycle: once Qt
        deletes it and the last reference goes, it's gone - no collection."""
        was = gc.isenabled()
        gc.disable()
        try:
            dialog = MessageDialog(self.shell, None, "Test", "Dropped")
            gone = weakref.ref(dialog)
            dialog.deleteLater()
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            del dialog
            self.assertIsNone(gone(), "the window was left for the cycle collector")
        finally:
            if was:
                gc.enable()

    def test_open_settings_leaves_no_dialog_behind(self):
        # Settings is a window of its own (show(), not exec()): open_settings()
        # returns at once, and the window is closed a moment later.
        for _ in range(2):
            close_when_shown(self.shell, SettingsDialog)
            ShellWindow.open_settings(self.shell)
            for _tick in range(200):            # until it has shown and been closed
                if not SettingsDialog.on_screen():
                    break
                _wait(50)
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(self.shell.findChildren(SettingsDialog), [])

    def test_open_settings_twice_is_one_window(self):
        ShellWindow.open_settings(self.shell)
        ShellWindow.open_settings(self.shell)
        try:
            self.assertEqual(len(self.shell.findChildren(SettingsDialog)), 1)
        finally:
            for dialog in self.shell.findChildren(SettingsDialog):
                dialog.done(QDialog.Rejected)
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@unittest.skipUnless(HAVE_WEB, "PySide6 with QtWebEngine not installed")
class GuiCollectorTests(unittest.TestCase):
    def test_collection_happens_on_the_gui_thread_only(self):
        app = QApplication.instance() or QApplication([])
        collector = gui_gc.install(app)
        self.addCleanup(collector.stop)
        self.assertFalse(gc.isenabled())   # no thread collects by itself
        self.assertTrue(collector._timer.isActive())
        self.assertIs(collector.thread(), app.thread())
        self.assertIsInstance(collector.collect(), int)


def _page(tool_id):
    class Page(ToolPage):
        def build_ui(self):
            QVBoxLayout(self).addWidget(QWidget())

    Page.tool_id, Page.display_name, Page.category = tool_id, tool_id.title(), "Tools"
    return Page


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


# Each dialog here is closed 1.5 s after it's asked for, drawn or not. A
# machine that takes longer than that to draw one - the headless Mac the
# release's tests run on can - would show it with nothing left to close it,
# and wait for good; the stand-in shell above covers the same ground there.
@unittest.skipUnless(sys.platform == "win32", "Windows only")
@unittest.skipUnless(HAVE_WEB, "PySide6 with QtWebEngine not installed")
class ShellWindowLifetimeTests(unittest.TestCase):
    """The same against a real ShellWindow, with stand-in pages and
    in-memory settings, like test_dual_view."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.settings = Settings()
        for p in [mock.patch.object(shell_window, "SharedSettings", lambda: self.settings),
                  mock.patch.object(shell_window, "resolve_connect", side_effect=RuntimeError("no Resolve")),
                  mock.patch.object(shell_window.AnnouncementChecker, "start", lambda self: None),
                  mock.patch.object(shell_window.QSystemTrayIcon, "isSystemTrayAvailable", return_value=False)]:
            p.start()
            self.addCleanup(p.stop)
        self.win = shell_window.ShellWindow(self.app, [("Tools", _page("alpha"))])
        self.addCleanup(self.win.deleteLater)
        self.win.show()
        self.app.processEvents()
        self.addCleanup(self._restore_gc)
        gc.collect()

    def _restore_gc(self):
        gc.set_debug(0)
        gc.garbage.clear()
        gc.enable()

    @staticmethod
    def _close_dialogs_soon(kind):
        QTimer.singleShot(1500, lambda: [w.close() for w in QApplication.topLevelWidgets() if isinstance(w, kind)])

    def _live_qt_garbage(self):
        """Web-view objects the cycle collector would destroy that still exist
        in Qt. (Other tests' background jobs can finish, and turn into garbage,
        while this one runs - they aren't what this checks.)"""
        gc.set_debug(gc.DEBUG_SAVEALL)
        gc.collect()
        web = ("PySide6.QtWebEngine", "PySide6.QtWebChannel", "core.web_page", "core.message_dialog")
        return [type(o).__name__ for o in gc.garbage
                if isinstance(o, shiboken6.Shiboken.Object) and shiboken6.isValid(o)
                and (type(o).__module__ or "").startswith(web)]

    def test_a_closed_message_leaves_nothing_for_the_cycle_collector(self):
        gc.disable()
        # Whatever earlier tests left for the collector isn't this test's.
        gc.set_debug(gc.DEBUG_SAVEALL)
        gc.collect()
        gc.garbage.clear()
        gc.set_debug(0)
        self._close_dialogs_soon(message_dialog.MessageDialog)
        message_dialog.alert(self.win, "Test", "Closed a moment from now")
        _wait(300)
        self.assertEqual(self._live_qt_garbage(), [])

    def test_settings_is_freed_once_closed(self):
        QTimer.singleShot(1500, lambda: [d.reject() for d in self.win.findChildren(SettingsDialog)])
        self.win.open_settings()
        _wait(2000)   # rejected at 1.5 s; deleteLater runs from the event loop
        self.assertEqual(self.win.findChildren(SettingsDialog), [])


if __name__ == "__main__":
    unittest.main()
