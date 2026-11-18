"""The desktop layout's desk (app/core/desktop_window.py): the focus a click
inside a window's page gives it brings that window to the front (how a Mac
hears of the click), the windows move with the desk when the link bar goes
in above it, and a drag moves a picture of the window. Built offscreen with
stand-in pages - no web views, no settings, no Resolve."""

import os
import sys
import unittest

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget
    import shiboken6
    from core.desktop_window import DesktopArea
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


def _page():
    page = QWidget()
    page.field = QLineEdit(page)
    page.on_shown = lambda: None
    QVBoxLayout(page).addWidget(page.field)
    return page


# Windows sees the press itself on its way to the page (an event filter),
# so focus alone moves nothing there.
@unittest.skipUnless(sys.platform == "darwin", "macOS only")
@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class FrontOnFocusTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.shell = QWidget()
        self.addCleanup(self.shell.deleteLater)
        self.shell.resize(1400, 900)
        self.pages = {"alpha": _page(), "beta": _page()}
        self.desk = DesktopArea(self.shell, self.pages, {"alpha": "Alpha", "beta": "Beta"},
                                lambda page: None, lambda front: None)
        self.desk.setGeometry(0, 0, 1400, 900)
        self.shell.show()
        self.desk.show_desk()
        self.desk.open("alpha")
        self.desk.open("beta")
        self.shell.activateWindow()
        self.app.processEvents()

    def test_focus_inside_a_window_behind_brings_it_forward(self):
        self.assertEqual(self.desk.front(), "beta")
        self.pages["alpha"].field.setFocus()
        self.app.processEvents()
        self.assertEqual(self.desk.front(), "alpha")

    def test_focus_in_the_front_window_changes_nothing(self):
        self.pages["beta"].field.setFocus()
        self.app.processEvents()
        self.assertEqual(self.desk.front(), "beta")

    def test_a_hidden_desk_ignores_focus(self):
        self.desk.hide()
        self.pages["alpha"].field.setFocus()
        self.app.processEvents()
        self.assertEqual(self.desk.front(), "beta")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class DeskUnderTheLinkBar(unittest.TestCase):
    """The windows are native, the desk isn't: when the link bar goes in
    above the desk, the desk only moves - and the windows have to move with
    it, not stay over the bar."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.top = QWidget()
        self.addCleanup(self.top.deleteLater)
        self.top.resize(1000, 700)
        self.root = QVBoxLayout(self.top)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(0)
        self.desk = DesktopArea(self.top, {"tool": _page()}, {"tool": "Tool"}, lambda page: None, lambda front: None)
        self.root.addWidget(self.desk, 1)
        self.bar = QLabel("links")
        self.bar.setFixedHeight(40)
        self.root.addWidget(self.bar)                       # along the bottom, as under the other themes
        self.top.show()
        self.desk.show()
        self.app.processEvents()
        self.desk.open("tool")
        self.app.processEvents()

    def tearDown(self):
        self.top.close()
        self.app.processEvents()

    def native_y(self):
        win = self.desk.windows["tool"]
        return win.windowHandle().position().y(), win.mapTo(self.top, QPoint(0, 0)).y()

    def test_the_windows_move_down_with_the_desk(self):
        actual, expected = self.native_y()
        self.assertEqual(actual, expected)
        # The desktop layout puts the bar along the top: the desk moves down,
        # its size the same.
        self.root.removeWidget(self.bar)
        self.root.insertWidget(0, self.bar)
        self.app.processEvents()
        self.assertEqual(self.desk.y(), 40)
        actual, expected = self.native_y()
        self.assertEqual(actual, expected)
        self.assertGreaterEqual(actual, 40)                 # below the bar, never over it


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class DraggingAWindow(unittest.TestCase):
    """A drag by the title moves a picture of the window (_DragGhost); the
    window steps off the desk and lands once, where the picture is."""

    def setUp(self):
        import time
        from unittest import mock
        from core import desktop_window
        from core.desktop_window import DesktopArea

        class Page(QWidget):
            display_name = "Tool"

            def on_shown(self):
                pass

        self.dw, self.time = desktop_window, time
        self.button_down = True
        patch = mock.patch.object(desktop_window, "_primary_button_down", lambda: self.button_down)
        patch.start()
        self.addCleanup(patch.stop)
        # The drag asks the system where the pointer is, not the event: here, where the test last put it.
        self.pointer = QPoint(0, 0)
        patch = mock.patch.object(desktop_window, "_pointer", lambda: QPoint(self.pointer))
        patch.start()
        self.addCleanup(patch.stop)
        self.app = QApplication.instance() or QApplication([])
        self.top = QWidget()
        self.top.resize(1200, 800)
        self.moves = []
        self.desk = DesktopArea(self.top, {"tool": Page()}, {"tool": "Tool"}, lambda page: None, lambda front: None)
        self.desk.setGeometry(0, 0, 1200, 800)
        self.top.show()
        self.desk.show()
        self.wait()
        self.desk.open("tool")
        self.wait()
        self.win = self.desk.windows["tool"]
        self.start = self.win.geometry()

    def tearDown(self):
        self.top.close()
        self.top.deleteLater()
        self.wait()

    def wait(self, ms=0):
        end = self.time.monotonic() + ms / 1000
        while True:
            self.app.processEvents()
            if self.time.monotonic() >= end:
                return
            self.time.sleep(0.003)

    def mouse(self, kind, local):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        types = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove, "release": QEvent.MouseButtonRelease}
        buttons = Qt.NoButton if kind == "release" else Qt.LeftButton
        point = QPointF(local)
        if kind != "release":                   # let go where the pointer already is
            self.pointer = self.win.mapToGlobal(local)
        event = QMouseEvent(types[kind], point, QPointF(self.win.mapToGlobal(local)), Qt.LeftButton, buttons, Qt.NoModifier)
        {"press": self.win.mousePressEvent, "move": self.win.mouseMoveEvent,
         "release": self.win.mouseReleaseEvent}[kind](event)

    def drag_to(self, dx, dy):
        """Press on the title, move by (dx, dy) - in desk terms, so the
        window stepping off the desk doesn't change where the pointer is."""
        title = QPoint(40, 10)
        self.mouse("press", title)
        origin = self.win.mapToGlobal(title)
        target = origin + QPoint(dx, dy)

        def move():
            self.mouse("move", self.win.mapFromGlobal(target))
        move()
        return move

    def test_a_wobble_is_a_click_not_a_drag(self):
        self.drag_to(1, 1)
        self.assertIsNone(self.win._ghost)
        self.mouse("release", QPoint(41, 11))
        self.assertEqual(self.win.geometry(), self.start)

    def test_the_picture_moves_and_the_window_lands_once(self):
        move = self.drag_to(120, 70)
        ghost = self.win._ghost
        self.assertIsNotNone(ghost)
        self.assertTrue(ghost.isVisible())
        self.wait(60)                                            # the window steps off the desk
        self.assertEqual(self.win.x(), self.dw.OFF_DESK)
        move()
        target = self.start.translated(120, 70)
        self.assertEqual(ghost.pos(), self.desk.mapToGlobal(target.topLeft()))
        self.mouse("release", QPoint(0, 0))
        self.assertEqual(self.win.geometry(), target)
        self.assertEqual(tuple(self.desk.state.windows["tool"]["geom"]), target.getRect())
        self.assertIsNone(self.win._ghost)
        self.wait(self.dw.LAND_MS + 60)
        self.assertFalse(shiboken6.isValid(ghost))              # closed and gone

    def test_a_window_can_be_dragged_partly_off_the_desk_and_shows_only_whats_on_it(self):
        from core import desktop_layout as dl
        move = self.drag_to(5000, 5000)
        self.wait(60)
        move()
        ghost = self.win._ghost
        # The picture: stopped with KEEP of the title bar on the desk, and clipped to the desk.
        self.assertEqual(ghost.x(), self.desk.mapToGlobal(QPoint(1200 - dl.KEEP[0], 0)).x())
        self.assertLessEqual(ghost.mask().boundingRect().right(), dl.KEEP[0])
        self.mouse("release", QPoint(0, 0))
        self.assertEqual((self.win.x(), self.win.y()), (1200 - dl.KEEP[0], 800 - dl.KEEP[1]))
        seen = self.win.mask().boundingRect()
        self.assertLessEqual(seen.right(), dl.KEEP[0])                 # nothing drawn past the desk's edge
        self.assertLessEqual(seen.bottom(), dl.KEEP[1])
        # It stays there: laying the desk out again doesn't pull it back on.
        self.desk.sync()
        self.assertEqual((self.win.x(), self.win.y()), (1200 - dl.KEEP[0], 800 - dl.KEEP[1]))
        # Brought back, it's whole again.
        self.win.setGeometry(self.start)
        self.assertEqual(self.win.mask(), self.dw._shape(self.start.size()))

    def test_the_drag_goes_by_the_cursor_not_by_what_an_event_says(self):
        """Events sent to the window while it waits off the desk have carried
        positions thirty thousand pixels out: the window must not follow them."""
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        move = self.drag_to(120, 70)
        self.wait(60)
        move()
        where = self.pointer
        wild = QMouseEvent(QEvent.MouseMove, QPointF(40, 10), QPointF(-29000, -29000), Qt.LeftButton, Qt.LeftButton,
                           Qt.NoModifier)
        self.win.mouseMoveEvent(wild)
        target = self.start.translated(120, 70)
        self.assertEqual(self.win._ghost.pos(), self.desk.mapToGlobal(target.topLeft()))
        gone = QMouseEvent(QEvent.MouseButtonRelease, QPointF(40, 10), QPointF(-29000, -29000), Qt.LeftButton,
                           Qt.NoButton, Qt.NoModifier)
        self.pointer = where
        self.win.mouseReleaseEvent(gone)
        self.assertEqual(self.win.geometry(), target)

    def test_the_picture_follows_the_pointer_between_events(self):
        self.drag_to(120, 70)
        self.wait(60)
        self.pointer = self.pointer + QPoint(30, 10)             # the pointer moves; no event says so
        self.wait(self.dw.DRAG_CHECK_MS + 40)
        self.assertEqual(self.win._ghost.pos(), self.desk.mapToGlobal(self.start.translated(150, 80).topLeft()))

    def test_dropped_on_another_arranged_windows_place_they_trade_places(self):
        from core import desktop_layout as dl
        from core.desktop_window import DesktopArea

        class Page(QWidget):
            display_name = "Other"

            def on_shown(self):
                pass

        self.desk.pages["other"] = Page()
        self.desk.titles["other"] = "Other"
        self.desk.open("other")
        self.desk.arrange("columns")
        self.wait()
        left, right = dl.cells("columns", 2, self.desk.area())
        self.assertEqual(self.desk.state.slots, ["other", "tool"])
        self.assertEqual(self.win.geometry().getRect(), right)
        self.start = self.win.geometry()
        move = self.drag_to(-(right[0] - left[0]), 30)             # its title, over the other window's place
        self.wait(60)
        move()
        self.mouse("release", QPoint(0, 0))
        self.assertEqual(self.desk.state.slots, ["tool", "other"])
        self.assertEqual(self.win.geometry().getRect(), left)      # each in the other's place, exactly
        self.assertEqual(self.desk.windows["other"].geometry().getRect(), right)
        # Let go over empty desk (the margin), it's just moved - and nothing is arranged any more.
        self.start = self.win.geometry()
        move = self.drag_to(0, 300)
        self.wait(60)
        self.pointer = self.desk.mapToGlobal(QPoint(3, 3))
        self.win._follow()
        self.mouse("release", QPoint(0, 0))
        self.assertIsNone(self.desk.state.slots)

    def test_a_release_that_never_comes_still_lands_it(self):
        self.drag_to(80, 40)
        self.wait(60)
        self.button_down = False                                 # let go somewhere Buddy didn't hear
        self.wait(self.dw.DRAG_CHECK_MS + 60)
        self.assertIsNone(self.win._ghost)
        self.assertEqual(self.win.geometry(), self.start.translated(80, 40))

    def test_a_resize_is_the_window_itself_live(self):
        f = self.win.frame_rect()
        corner = QPoint(f.right() - 2, f.bottom() - 2)
        self.mouse("press", corner)
        origin = QPoint(self.pointer)
        changes = []
        self.desk.on_change = lambda front: changes.append(front)
        sizes = []
        for step in (50, 100, 150):
            self.pointer = origin + QPoint(step, step * 3 // 5)        # the pointer moves; the drag's tick sees it
            self.wait(self.dw.DRAG_CHECK_MS + 30)
            sizes.append(self.win.size())
        self.assertIsNone(self.win._ghost)                             # no picture: the window itself
        self.assertEqual(self.win.pos(), self.start.topLeft())         # and it never leaves the desk
        self.assertEqual([s.width() - self.start.width() for s in sizes], [50, 100, 150])
        self.assertEqual(self.win.scroller.geometry(), self.win.content_rect())     # the page has its new size now
        self.assertEqual(changes, [])                                  # nothing saved until it's let go
        self.mouse("release", QPoint(0, 0))
        target = self.start.adjusted(0, 0, 150, 90)
        self.assertEqual(self.win.geometry(), target)
        self.assertEqual(tuple(self.desk.state.windows["tool"]["geom"]), target.getRect())
        self.assertEqual(len(changes), 1)
        self.pointer = origin + QPoint(400, 400)                       # let go: the pointer no longer resizes it
        self.wait(self.dw.DRAG_CHECK_MS + 30)
        self.assertEqual(self.win.geometry(), target)

    def test_a_resize_let_go_elsewhere_stops_where_it_is(self):
        f = self.win.frame_rect()
        corner = QPoint(f.right() - 2, f.bottom() - 2)
        self.mouse("press", corner)
        self.pointer = self.pointer + QPoint(60, 30)
        self.wait(self.dw.DRAG_CHECK_MS + 30)
        self.button_down = False
        self.wait(self.dw.DRAG_CHECK_MS + 30)
        self.pointer = self.pointer + QPoint(300, 300)
        self.wait(self.dw.DRAG_CHECK_MS + 30)
        self.assertEqual(self.win.geometry(), self.start.adjusted(0, 0, 60, 30))
        self.assertEqual(tuple(self.desk.state.windows["tool"]["geom"]), self.start.adjusted(0, 0, 60, 30).getRect())

    def test_closing_mid_drag_leaves_no_picture(self):
        self.drag_to(80, 40)
        ghost = self.win._ghost
        self.wait(60)
        self.desk.close("tool")
        self.wait(self.dw.LAND_MS + 60)
        self.assertIsNone(self.win._ghost)
        self.assertFalse(shiboken6.isValid(ghost))              # closed and gone


if __name__ == "__main__":
    unittest.main()
