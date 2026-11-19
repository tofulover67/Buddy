"""Data Graphics (pages/data_graphics/): a tab of its own holding Counters and
Graphs, neither built yet - so what's tested is that it's wired in, that each
page draws the same two tabs, and that nothing says it can do what it can't."""

import ast
import os
import re
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401

from core import i18n, more_tools, tool_dirs
from core.tools_kb import get_tool

ROOT = _paths.APP / "pages" / "data_graphics"
PAGES = (("counters", "Counters"), ("graphs", "Graphs"))

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAVE_WEB = True
except ImportError:
    HAVE_WEB = False


class WiringTests(unittest.TestCase):
    def test_its_in_the_sidebar_under_editing_tools(self):
        source = (_paths.APP / "registry.py").read_text(encoding="utf-8")
        rows = [ast.literal_eval(f"({m})") for m in re.findall(r'_tool\(("data_graphics".*?"DataGraphicsPage")', source)]
        self.assertEqual(rows, [("data_graphics", "Data Graphics", "Editing Tools",
                                 "pages.data_graphics.page", "DataGraphicsPage")])

    def test_every_page_draws_the_same_two_tabs(self):
        for folder, _name in PAGES:
            html = (ROOT / folder / "web" / "index.html").read_text(encoding="utf-8")
            tabs = re.findall(r'data-tab="(\w+)" aria-selected="(\w+)">([^<]+)<', html)
            self.assertEqual([(t, label) for t, _s, label in tabs], list(PAGES), folder)
            self.assertEqual([t for t, selected, _l in tabs if selected == "true"], [folder], folder)

    def test_each_page_has_a_folder_for_its_settings(self):
        for name in ("data_graphics", "data_counters", "data_graphs"):
            self.assertIn(name, tool_dirs.BUCKETS)

    def test_what_the_pages_say_is_translated(self):
        said = ["Data Graphics", more_tools.BLURBS["data_graphics"]]
        for folder, _name in PAGES:
            html = (ROOT / folder / "web" / "index.html").read_text(encoding="utf-8")
            said += [i18n.normalize(text) for text in re.findall(r"<(?:h1|h2|p|button)[^>]*>([^<]+)<", html)]
        self.assertGreaterEqual(len(said), 12)
        for text in said:
            with self.subTest(text):
                self.assertNotEqual(i18n.translate(text, "Deutsch"), text)

    def test_ask_buddy_is_told_nothing_can_be_made_yet(self):
        tool = get_tool("data_graphics")
        self.assertIn("Neither is built yet", tool.summary)
        self.assertEqual(len(tool.capabilities), 1)
        self.assertIn("Nothing yet", tool.capabilities[0])


class Mem(dict):
    def save(self):
        self.saved = dict(self)


class Host:
    def __init__(self):
        self.buckets = {}

    def tool_settings(self, tool_id, defaults=None):
        return self.buckets.setdefault(tool_id, Mem(defaults or {}))

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class PageTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": self.home.name, "BUDDY_RESOLVE_READER": "off"})
        patcher.start()
        self.addCleanup(patcher.stop)
        from pages.data_graphics.page import DataGraphicsPage
        self.host = Host()
        self.page = DataGraphicsPage(self.host)
        self.addCleanup(self.page.deleteLater)

    def test_it_opens_on_counters_and_isnt_a_placeholder(self):
        self.assertFalse(self.page.is_placeholder)
        self.assertIs(self.page.current(), self.page.pages["counters"])

    def test_a_tab_asked_for_is_shown_and_remembered(self):
        self.page.pages["counters"].on_switch_tab({"tab": "graphs"})
        self.assertIs(self.page.current(), self.page.pages["graphs"])
        self.assertEqual(self.host.buckets["data_graphics"].saved["tab"], "graphs")
        again = type(self.page)(self.host)
        self.addCleanup(again.deleteLater)
        self.assertIs(again.current(), again.pages["graphs"])

    def test_a_tab_it_doesnt_have_changes_nothing(self):
        self.page.pages["counters"].on_switch_tab({"tab": "maps"})
        self.page.pages["counters"].on_switch_tab(None)
        self.assertIs(self.page.current(), self.page.pages["counters"])


if __name__ == "__main__":
    unittest.main()
