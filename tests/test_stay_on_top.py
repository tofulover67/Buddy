"""Settings' "Keep Buddy on top of Resolve" (ShellWindow.apply_stay_on_top):
the windows Buddy has open go on top with it - Settings itself is open when
it changes, and left behind under the on-top Buddy, the modal dialog refused
every click on Buddy unseen. A window pinned on its own keeps its pin."""

import os
import unittest

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog, QWidget
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class StayOnTopTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        from core.shell_window import ShellWindow
        self.apply = ShellWindow.apply_stay_on_top

        class Main(QWidget):
            pass
        self.main = Main()
        self.main.app = self.app
        self.main.show()
        self.settings = QDialog(self.main)
        self.settings.show()
        self.pinned = QWidget(self.main, Qt.Window | Qt.WindowStaysOnTopHint)
        self.pinned.show()
        self.stranger = QDialog()                       # not Buddy's
        self.stranger.show()
        for w in (self.main, self.settings, self.pinned, self.stranger):
            self.addCleanup(w.deleteLater)
        self.app.processEvents()

    def on_top(self, widget):
        return bool(widget.windowFlags() & Qt.WindowStaysOnTopHint) and \
            bool(widget.windowHandle().flags() & Qt.WindowStaysOnTopHint)

    def test_open_windows_go_on_top_with_buddy_and_come_back_down(self):
        self.apply(self.main, True)
        self.assertTrue(self.on_top(self.main))
        self.assertTrue(self.on_top(self.settings))
        self.assertTrue(self.main.isVisible() and self.settings.isVisible())
        self.assertIs(self.settings.windowHandle().transientParent(), self.main.windowHandle())
        self.assertFalse(self.on_top(self.stranger))
        self.apply(self.main, False)
        self.assertFalse(self.on_top(self.main))
        self.assertFalse(self.on_top(self.settings))
        self.assertTrue(self.main.isVisible() and self.settings.isVisible())

    def test_a_window_pinned_on_its_own_keeps_its_pin(self):
        self.apply(self.main, True)
        self.apply(self.main, False)
        self.assertTrue(self.on_top(self.pinned))

    def test_on_and_off_again_keeps_every_other_flag(self):
        """The title bar's X: PySide's ~ on the flag lost the close-button
        hint, and Windows greyed the X out."""
        hints = (Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint
                 | Qt.WindowCloseButtonHint)
        self.main.setWindowFlags(hints)
        self.main.show()
        self.settings.show()
        self.app.processEvents()
        before, settings_before = self.main.windowFlags(), self.settings.windowFlags()
        self.apply(self.main, True)
        self.apply(self.main, False)
        self.assertEqual(self.main.windowFlags().value, before.value)
        self.assertEqual(self.main.windowHandle().flags().value, before.value)
        self.assertEqual(self.settings.windowFlags().value, settings_before.value)

    def test_the_same_setting_again_changes_nothing(self):
        self.apply(self.main, False)
        self.assertFalse(self.on_top(self.settings))
        self.assertIsNone(self.settings.property("buddy_on_top"))


if __name__ == "__main__":
    unittest.main()
