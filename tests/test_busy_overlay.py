"""The shell's busy overlay (app/core/busy_overlay.py) hands the keyboard
back when the work is done: to what had it before, never on to the next
widget in the window (the header's Report a bug button, the first one in
the tab order) - and a dialog or field that took it meanwhile keeps it."""

import os
import unittest

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class FocusTests(unittest.TestCase):
    def setUp(self):
        from core.busy_overlay import BusyOverlay
        self.app = QApplication.instance() or QApplication([])
        self.window = QWidget()
        layout = QVBoxLayout(self.window)
        self.bug = QPushButton("Report a bug")          # first in the tab order, as the header is
        self.page = QWidget()
        page_layout = QVBoxLayout(self.page)
        self.field = QLineEdit()
        self.other = QLineEdit()
        page_layout.addWidget(self.field)
        page_layout.addWidget(self.other)
        layout.addWidget(self.bug)
        layout.addWidget(self.page)
        self.overlay = BusyOverlay(self.window)
        self.window.resize(400, 300)
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        self.addCleanup(self.window.deleteLater)

    def focused(self):
        self.app.processEvents()
        return self.window.focusWidget()

    def test_the_focus_goes_back_where_it_was(self):
        self.field.setFocus()
        self.overlay.start("Working")
        self.assertIs(self.focused(), self.overlay)
        self.overlay.restart()                           # a theme change mid-job
        self.overlay.stop()
        self.assertIs(self.focused(), self.field)

    def test_a_widget_gone_meanwhile_leaves_the_focus_nowhere_not_on_the_bug_button(self):
        self.field.setFocus()
        self.overlay.start("Working")
        self.field.hide()
        self.overlay.stop()
        self.assertIsNot(self.focused(), self.bug)
        self.assertFalse(self.bug.hasFocus())

    def test_something_that_took_the_focus_meanwhile_keeps_it(self):
        self.field.setFocus()
        self.overlay.start("Working")
        self.other.setFocus()
        self.overlay.stop()
        self.assertIs(self.focused(), self.other)


if __name__ == "__main__":
    unittest.main()
