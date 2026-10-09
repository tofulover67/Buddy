"""Put app/ (and the transcribe worker's folder) on sys.path for the tests.

The modules tested here are the pure ones - no Qt, no Resolve, no models -
so the suite runs with plain Python on any machine:

    python -m unittest discover tests
"""

import os
import sys
from pathlib import Path

# Web pages in tests draw offscreen, where there's no real graphics
# context: Chromium's GPU compositor keeps losing it and, now and then,
# crashes the test process outright (on macOS, a "Python quit unexpectedly"
# dialog). Drawing in software instead changes no layout. Set before
# QtWebEngine starts - every test imports this module first.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
# Pages read Resolve through a process of their own (core/resolve_reader.py);
# the tests' fake Resolve lives in this one, so they read the old way.
os.environ.setdefault("BUDDY_RESOLVE_READER", "off")

APP = Path(__file__).resolve().parents[1] / "app"
TRANSCRIBE = APP / "pages" / "transcribe"
for p in (str(APP), str(TRANSCRIBE), str(APP.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

# No test touches the real login Keychain, where Buddy Network keeps the key
# that locks its files on a Mac (e2e.py): they all get an in-memory one.
if sys.platform == "darwin":
    import fake_keychain  # noqa: E402

    fake_keychain.install()

def _drain_qt():
    """Before Python tears down: the widgets and pages tests left behind
    deleted while Qt still runs. Left to the interpreter's shutdown, their
    deleteLater()s and web profiles went in no fixed order and the process
    crashed (0xC0000005) after printing OK - a failed run to CI."""
    qt = sys.modules.get("PySide6.QtCore")
    if qt is None:
        return
    app = qt.QCoreApplication.instance()
    if app is None:
        return
    import gc
    widgets = sys.modules.get("PySide6.QtWidgets")
    if widgets is not None and isinstance(app, widgets.QApplication):
        for widget in widgets.QApplication.topLevelWidgets():
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
    for _ in range(3):
        qt.QCoreApplication.sendPostedEvents(None, qt.QEvent.DeferredDelete)
        app.processEvents()
        gc.collect()
    qt.QCoreApplication.sendPostedEvents(None, qt.QEvent.DeferredDelete)


# Run as the tests finish - not from atexit, where PySide's own handler (made
# later, so run first) has already taken the application down. A test file
# run by itself goes through unittest.main(); pytest through conftest.py.
import unittest  # noqa: E402

if not getattr(unittest.TestProgram, "_buddy_drains", False):
    _run_tests = unittest.TestProgram.runTests

    def _run_then_drain(self, *args, **kwargs):
        try:
            return _run_tests(self, *args, **kwargs)
        finally:
            _drain_qt()

    unittest.TestProgram.runTests = _run_then_drain
    unittest.TestProgram._buddy_drains = True
