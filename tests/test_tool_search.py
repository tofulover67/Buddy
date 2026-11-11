"""The tool search - the sidebar's search field and the desktop taskbar's
Search box: what it can find (core/tool_search.py), and how it matches
(web/shell/tool_search.js) in the sidebar's own page, fed by hand."""

import json
import os
import time
import unittest

import _paths  # noqa: F401
from core import nav_layout, tool_search
from core.tools_kb import ToolInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _Page:
    is_placeholder = False


class Relink(_Page):
    tool_id, display_name = "relink", "Relink"


class Renamer(_Page):
    tool_id, display_name = "renamer", "Renamer"


class Manager(_Page):
    tool_id, display_name = "manager", "Manager"
    TABS = (("renamer", Renamer), ("relink", Relink))
    SUB_TOOLS = ("renamer", "relink")


class Notes(_Page):
    tool_id, display_name = "notes", "Notes"


class Soon(_Page):
    tool_id, display_name, is_placeholder = "soon", "Soon", True


REGISTRY = [("Media", Manager), ("Media", Soon), ("Extras", Notes)]
KB = [ToolInfo("relink", "Relink", "Relinks offline clips.", ["Picks one of several copies"]),
      ToolInfo("notes", "Notes", "", ["Keeps notes"])]


class EntryTests(unittest.TestCase):
    def test_every_working_tool_with_where_it_is_and_what_it_does(self):
        layout = nav_layout.default_layout(REGISTRY)
        layout[-1]["visible"] = False                 # hidden from the sidebar: still found
        found = tool_search.entries(REGISTRY, layout, kb=KB)
        self.assertEqual([(e["id"], e["where"]) for e in found],
                         [("manager", "Media"), ("renamer", "Manager"), ("relink", "Manager"), ("notes", "Extras")])
        self.assertEqual(found[2]["about"], ["Relinks offline clips.", "Picks one of several copies"])
        self.assertEqual(found[3]["about"], ["Keeps notes"])        # an empty summary left out

    def test_buddys_own_tools(self):
        from registry import REGISTRY as real
        found = {e["id"]: e for e in tool_search.entries(real, nav_layout.default_layout(real))}
        self.assertEqual(found["media_relink"]["where"], "Media Manager")
        self.assertTrue(any("offline" in line for line in found["media_relink"]["about"]))
        placeholders = {cls.tool_id for _c, cls in real if cls.is_placeholder}
        self.assertFalse(placeholders & set(found))


class RailSearchTests(unittest.TestCase):
    """rail/index.html in a web view, no shell: the rail and its tools sent by
    hand (Buddy.receive), the search typed into."""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("QtWebEngine not installed")
        from PySide6.QtWidgets import QApplication
        from test_audio_assistant import qt_app
        cls.app = qt_app()
        if not isinstance(cls.app, QApplication):
            raise unittest.SkipTest("a QCoreApplication was made first; a web view needs a QApplication")

    def setUp(self):
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from shiboken6 import delete
        self.view = QWebEngineView()
        self.addCleanup(delete, self.view)
        self.view.resize(240, 700)
        loop, loaded = QEventLoop(), []
        self.view.loadFinished.connect(lambda ok: (loaded.append(ok), loop.quit()))
        self.view.setUrl(QUrl.fromLocalFile(str(_paths.APP / "web" / "shell" / "rail" / "index.html")))
        self.view.show()
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        self.assertEqual(loaded, [True])
        from registry import REGISTRY as real
        layout = nav_layout.default_layout(real)
        names = {cls.tool_id: cls.display_name for _c, cls in real}
        items = [{"type": "tool", "id": e["id"], "label": names[e["id"]]} for e in layout if e["type"] == "tool"][:4]
        self.js(f"Buddy.receive('rail', {json.dumps({'items': items, 'more': 0, 'tools': tool_search.entries(real, layout)})})")

    def js(self, code):
        from PySide6.QtCore import QEventLoop, QTimer
        loop, out = QEventLoop(), {}
        self.view.page().runJavaScript(code, 0, lambda v: (out.update(v=v), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        return out.get("v")

    def type(self, text):
        self.js(f"search.value = {json.dumps(text)}; search.dispatchEvent(new Event('input'))")

    def results(self):
        return json.loads(self.js("JSON.stringify([...document.querySelectorAll('.nav-result')].map(b => b.dataset.id))"))

    def key(self, name):
        self.js(f"search.dispatchEvent(new KeyboardEvent('keydown', {{key: '{name}', bubbles: true}}))")

    def test_by_name_first_then_by_what_a_tool_does(self):
        self.assertEqual(self.js("document.querySelectorAll('#rail-list .nav-item').length"), 4)
        self.type("relink")
        self.assertEqual(self.results()[0], "media_relink")
        self.type("offline")
        self.assertEqual(self.results()[0], "media_relink")      # its summary, over a passing mention
        self.assertIn("offline", self.js("document.querySelector('.nav-result-note mark').textContent").lower())
        self.type("colour picker zzz")                         # every word has to be found
        self.assertEqual(self.results(), [])
        self.assertEqual(self.js("document.querySelector('.nav-empty').textContent"), "No tools match that")

    def test_keys_choose_open_and_clear(self):
        self.type("media")
        found = self.results()
        self.assertGreater(len(found), 1)
        self.key("ArrowDown")
        self.assertEqual(self.js("document.querySelector('.nav-result.active').dataset.id"), found[1])
        self.key("Escape")
        self.assertEqual(self.js("search.value"), "")
        self.assertEqual(self.js("document.querySelectorAll('#rail-list .nav-item').length"), 4)   # the tools again
        self.type("relink")
        self.key("Enter")                                      # opens it, and the search empties
        self.assertEqual(self.js("search.value"), "")
        self.assertEqual(self.results(), [])

    def test_a_rail_update_keeps_the_search(self):
        self.type("relink")
        self.js("Buddy.receive('rail', {items: [], more: 0})")      # a badge changed: no tools resent
        time.sleep(0.05)
        self.assertEqual(self.results()[0], "media_relink")


if __name__ == "__main__":
    unittest.main()
