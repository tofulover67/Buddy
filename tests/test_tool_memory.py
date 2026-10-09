"""Letting go of tools out of sight a while (core/tool_memory.py): how many
stay ready on a machine of each size, which ones go, and a page that's
holding on to something (typed text, a game, an unsaved edit - buddy.js
keepAlive) or busy lately staying put. Stand-in tools, no Chromium."""

import os
import time
import unittest

import _paths  # noqa: F401
from core import tool_memory as tm

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget
    from core.web_page import WebSurface
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class PolicyTests(unittest.TestCase):
    def test_a_smaller_machine_keeps_fewer_tools_for_less_long(self):
        self.assertEqual(tm.policy(8, {}), (2, 2))
        self.assertEqual(tm.policy(6, {}), (2, 2))
        self.assertEqual(tm.policy(16, {}), (3, 5))
        self.assertEqual(tm.policy(32, {}), (3, 10))
        self.assertEqual(tm.policy(None, {}), (3, 10))         # can't tell: the roomy choice

    def test_set_by_hand(self):
        self.assertEqual(tm.policy(8, {"BUDDY_KEEP_TOOLS": "5", "BUDDY_LET_GO_MINUTES": "30"}), (5, 30))
        self.assertEqual(tm.policy(8, {"BUDDY_LET_GO_MINUTES": "0"}), (2, 0))      # never
        self.assertEqual(tm.policy(8, {"BUDDY_KEEP_TOOLS": "0"}), (1, 2))          # the one on screen at least
        self.assertEqual(tm.policy(8, {"BUDDY_KEEP_TOOLS": "lots"}), (2, 2))

    def test_the_machine_has_some_memory(self):
        ram = tm.memory_gb()
        self.assertTrue(ram is None or ram > 0.5)


class ChoiceTests(unittest.TestCase):
    def test_the_most_recent_stay_and_so_does_anything_seen_lately(self):
        now = 10_000.0
        seen = {"on_screen": None, "a": now - 30, "b": now - 700, "c": now - 900, "d": now - 1200}
        # Keep 3: on screen, a, b. Of c and d, only those out of sight 10 minutes go.
        self.assertEqual(tm.to_let_go(seen, 3, 10, now), ["c", "d"])
        self.assertEqual(tm.to_let_go(seen, 3, 18, now), ["d"])
        self.assertEqual(tm.to_let_go(seen, 1, 10, now), ["b", "c", "d"])
        self.assertEqual(tm.to_let_go(seen, 3, 0, now), [])                       # never

    def test_tools_on_screen_never_go(self):
        self.assertEqual(tm.to_let_go({"x": None, "y": None, "z": None}, 1, 1, 10_000.0), [])


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class AskingThePageTests(unittest.TestCase):
    """WebSurface.ask_to_let_go / let_go, on a stand-in view."""

    class View:
        def __init__(self, visible=False, keep=False):
            self.visible, self.keep, self.state = visible, keep, None

        def isVisible(self):
            return self.visible

        def page(self):
            return self

        def runJavaScript(self, script, world, callback):
            self.asked = script
            callback(self.keep)

        def lifecycleState(self):
            from PySide6.QtWebEngineCore import QWebEnginePage
            return self.state or QWebEnginePage.LifecycleState.Active

        def setLifecycleState(self, state):
            self.state = state

        @property
        def label(self):
            return "stand-in"

    class Surface:
        _ready = True
        _queue = []
        _trail_label = WebSurface._trail_label if HAVE_QT else None

    def surface(self, **view):
        s = self.Surface()
        s.view = self.View(**view)
        return s

    def ask(self, s, quiet_for=60, now=None):
        answers = []
        WebSurface.ask_to_let_go(s, answers.append, quiet_for, now)
        return answers

    def test_a_quiet_page_holding_nothing_may_go(self):
        self.assertEqual(self.ask(self.surface()), [True])

    def test_its_script_says_keep(self):
        s = self.surface(keep=True)
        self.assertEqual(self.ask(s), [False])
        self.assertIn("keepAlive", s.view.asked)
        s.view.keep = None                         # no answer (no Buddy on the page): kept
        self.assertEqual(self.ask(s), [False])

    def test_on_screen_loading_or_busy_lately_stays(self):
        self.assertEqual(self.ask(self.surface(visible=True)), [False])
        s = self.surface()
        s._ready = False
        self.assertEqual(self.ask(s), [False])
        s = self.surface()
        s._last_emit = time.monotonic()
        self.assertEqual(self.ask(s, quiet_for=600), [False])
        self.assertEqual(self.ask(s, quiet_for=600, now=time.monotonic() + 601), [True])

    def test_let_go_discards_the_page_and_holds_its_events(self):
        from PySide6.QtWebEngineCore import QWebEnginePage
        s = self.surface()
        self.assertTrue(WebSurface.let_go(s))
        self.assertEqual(s.view.state, QWebEnginePage.LifecycleState.Discarded)
        self.assertFalse(s._ready)
        self.assertFalse(WebSurface.let_go(s))     # already gone
        self.assertFalse(WebSurface.let_go(self.surface(visible=True)))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ToolMemoryTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        os.environ["BUDDY_KEEP_TOOLS"] = "1"
        os.environ["BUDDY_LET_GO_MINUTES"] = "0.001"           # 60 ms
        self.addCleanup(os.environ.pop, "BUDDY_KEEP_TOOLS", None)
        self.addCleanup(os.environ.pop, "BUDDY_LET_GO_MINUTES", None)

        class Tool(WebSurface, QWidget):
            def __init__(tool, holding=False):
                QWidget.__init__(tool)
                tool.holding, tool.gone = holding, False

            def ask_to_let_go(tool, done, quiet_for, now=None):
                done(not tool.holding)

            def let_go(tool):
                tool.gone = True
                return True

        self.shell = QWidget()
        self.addCleanup(self.shell.deleteLater)
        self.shell.pages = {"a": Tool(), "b": Tool(), "c": Tool(holding=True), "qt": QWidget()}
        for page in self.shell.pages.values():
            page.setParent(self.shell)
            page.hide()
        self.shell.show()
        self.memory = tm.ToolMemory(self.shell, ram_gb=32)

    def visit(self, *tools):
        for tool in tools:
            page = self.shell.pages[tool]
            page.show()
            self.app.processEvents()
            page.hide()
            self.app.processEvents()

    def test_tools_out_of_sight_long_enough_go_unless_holding_on(self):
        pages = self.shell.pages
        self.visit("a", "b", "c", "qt")
        pages["a"].show()                          # back on screen: stays
        self.app.processEvents()
        time.sleep(0.1)
        self.memory.check()
        self.assertFalse(pages["a"].gone)
        self.assertTrue(pages["b"].gone)
        self.assertFalse(pages["c"].gone)          # holding on (a game, typed text)
        self.assertNotIn("b", self.memory.seen)    # gone: nothing more to let go until it's shown again
        self.assertNotIn("qt", self.memory.seen)   # not a web page: nothing to let go
        self.assertIsNone(pages["b"]._drawn_size)  # drawn again from the start when it's back

    def test_a_tool_never_shown_is_left_alone(self):
        time.sleep(0.1)
        self.memory.check()
        self.assertFalse(any(getattr(p, "gone", False) for p in self.shell.pages.values()))

    def test_never_lets_go_when_told_not_to(self):
        os.environ["BUDDY_LET_GO_MINUTES"] = "0"
        memory = tm.ToolMemory(self.shell, ram_gb=4)
        self.assertFalse(memory._timer.isActive())


if __name__ == "__main__":
    unittest.main()
