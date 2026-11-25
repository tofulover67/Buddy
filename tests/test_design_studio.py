"""Design Studio (pages/design_studio/): the tools that draw something for the timeline,
as the tabs of one sidebar entry - the saved sidebar keeps its place, every view draws
the same tab row, and the host shows one tool at a time and opens one by its own id."""

import re
import types
import unittest

import _paths  # noqa: F401
from core import nav_layout
from core.tools_kb import get_tool

try:
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    from pages.base import ToolPage
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

# tab -> the folder of the tool on it, in tab order
TABS = (("design", "design"), ("interactions", "interactions"), ("panels", "panels"))


class Page:
    def __init__(self, tool_id):
        self.tool_id = tool_id


class NavTests(unittest.TestCase):
    def test_the_old_entries_become_one_at_the_first_ones_place(self):
        registry = [("Setup", Page("setup")), ("Editing Tools", Page("design_studio"))]
        saved = [{"type": "divider", "label": "Editing Tools"},
                 {"type": "tool", "id": "interactions", "visible": False},
                 {"type": "tool", "id": "setup", "visible": True},
                 {"type": "tool", "id": "design", "visible": True}]
        self.assertEqual(nav_layout.reconcile(saved, registry), [
            {"type": "divider", "label": "Editing Tools"},
            {"type": "tool", "id": "design_studio", "visible": True},
            {"type": "tool", "id": "setup", "visible": True},
        ])

    def test_the_registry_has_the_studio_and_not_its_tabs(self):
        source = (_paths.APP / "registry.py").read_text(encoding="utf-8")
        self.assertIn("DesignStudioPage", source)
        for page in ("DesignPage", "InteractionsPage", "PanelsPage"):
            self.assertNotIn(f'"{page}"', source)


class ViewTests(unittest.TestCase):
    def test_every_view_draws_the_same_tab_row_with_its_own_tab_chosen(self):
        for chosen, folder in TABS:
            html = (_paths.APP / "pages" / folder / "web" / "index.html").read_text(encoding="utf-8")
            with self.subTest(folder):
                self.assertIn("data_graphics/web/tabs.js", html)
                self.assertIn('id="graphics-tabs" aria-label="Design Studio tools"', html)
                tabs = re.findall(r'data-tab="(\w+)" aria-selected="(\w+)"', html)
                self.assertEqual(tabs, [(tab, str(tab == chosen).lower()) for tab, _folder in TABS])


if HAVE_QT:
    class FakeTool(ToolPage):
        shown = 0
        busy = None

        def build_ui(self):
            pass

        def on_shown(self):
            self.shown += 1

        def working_on(self):
            return self.busy

    class FakeDesign(FakeTool):
        tool_id = "design"

    class FakeInteractions(FakeTool):
        tool_id = "interactions"

    class FakePanels(FakeTool):
        tool_id = "panels"

    class Settings(dict):
        saves = 0

        def save(self):
            self.saves += 1


def delete(widget):
    widget.deleteLater()


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        from pages.design_studio.page import DesignStudioPage
        self.cls = DesignStudioPage
        self.settings = Settings()

        class Studio(DesignStudioPage):
            TABS = (("design", FakeDesign), ("interactions", FakeInteractions), ("panels", FakePanels))

        def tool_settings(_tool_id, defaults):
            for key, value in defaults.items():
                self.settings.setdefault(key, value)
            return self.settings
        self.make = lambda: Studio(types.SimpleNamespace(tool_settings=tool_settings))
        self.page = self.make()
        self.addCleanup(delete, self.page)

    def test_its_tabs_are_the_three_tools_and_ask_buddy_can_offer_each(self):
        self.assertEqual(self.cls.SUB_TOOLS, ("design", "interactions", "panels"))
        self.assertEqual([tab for tab, _cls in self.cls.TABS], [tab for tab, _folder in TABS])
        for tool_id in self.cls.SUB_TOOLS:
            self.assertTrue(get_tool(tool_id, [("Editing Tools", self.cls)]).is_available, tool_id)

    def test_a_tab_is_shown_remembered_and_only_the_one_in_view_hears_it(self):
        design, panels = self.page.by_tab["design"], self.page.by_tab["panels"]
        self.assertIs(self.page.current(), design)
        self.page.show_tab("panels")
        self.assertIs(self.page.current(), panels)
        self.assertEqual((self.settings["tab"], self.settings.saves), ("panels", 1))
        self.page.show_tab("panels")            # already there: nothing saved again
        self.page.show_tab("nowhere")
        self.assertEqual(self.settings.saves, 1)
        self.page.on_shown()
        self.assertEqual((design.shown, panels.shown), (0, 1))
        again = self.make()
        self.addCleanup(delete, again)
        self.assertEqual(again.current().tool_id, "panels")

    def test_a_tool_is_opened_by_its_own_id_and_its_work_is_the_studios(self):
        self.page.show_tool("interactions")
        self.assertEqual(self.page.current().tool_id, "interactions")
        self.assertIsNone(self.page.working_on())
        self.page.by_tab["panels"].busy = "Adding a panel"
        self.assertEqual(self.page.working_on(), "Adding a panel")


if __name__ == "__main__":
    unittest.main()
