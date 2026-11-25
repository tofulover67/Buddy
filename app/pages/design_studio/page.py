"""Design Studio - one main tab under Editing Tools for the tools that draw something
and add it to the timeline as a new Fusion clip, each a sub-tab:

    Design        (pages/design/)        a canvas to draw graphic assets on
    Interactions  (pages/interactions/)  shapes that answer a recorded cursor
    Panels        (pages/panels/)        a mock app window with pages, driven by a cursor

They keep their own pages, settings and tool ids; this page stacks them and each one's
view draws the same tab row (Data Graphics' web/tabs.js), whose click comes back here
through the page's on_switch_tab (GraphicPage's, which looks for `_graphics`). The shell
opens a tab by its tool's id too (switch_tool("design") - Ask Buddy's "Open Design"),
through SUB_TOOLS and show_tool().
"""

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout

from pages.base import ToolPage
from pages.design.page import DesignPage
from pages.interactions.page import InteractionsPage
from pages.panels.page import PanelsPage

DEFAULTS = {"tab": "design"}


class DesignStudioPage(ToolPage):
    tool_id = "design_studio"
    display_name = "Design Studio"
    category = "Editing Tools"
    # tab -> the tool on it, in tab order
    TABS = (("design", DesignPage), ("interactions", InteractionsPage), ("panels", PanelsPage))
    SUB_TOOLS = tuple(page_cls.tool_id for _tab, page_cls in TABS)

    def build_ui(self):
        self.settings = self.host.tool_settings(self.tool_id, dict(DEFAULTS))
        self.tabs = QStackedWidget(self)
        self.by_tab = {}
        for tab, page_cls in self.TABS:
            page = page_cls(self.host)
            page._graphics = self
            self.by_tab[tab] = page
            self.tabs.addWidget(page)
        self.tabs.setCurrentWidget(self.by_tab.get(self.settings.get("tab")) or self.by_tab[self.TABS[0][0]])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

    def current(self):
        return self.tabs.currentWidget()

    def show_tab(self, tab):
        page = self.by_tab.get(tab)
        if page is None or page is self.current():
            return
        self.tabs.setCurrentWidget(page)
        self.settings["tab"] = tab
        self.settings.save()
        if self.isVisible():
            page.on_shown()

    def show_tool(self, tool_id):
        """The tab holding the tool with this id."""
        for tab, page in self.by_tab.items():
            if page.tool_id == tool_id:
                self.show_tab(tab)

    def working_on(self):
        """What any of its tools is in the middle of, for the shell's header."""
        for page in self.by_tab.values():
            doing = page.working_on() if hasattr(page, "working_on") else None
            if doing:
                return doing
        return None

    # -- the shell's hooks, passed on -------------------------------------

    def on_shown(self):
        self.current().on_shown()

    def on_theme_changed(self):
        for page in self.by_tab.values():
            page.on_theme_changed()

    def on_connection_changed(self, connected):
        for page in self.by_tab.values():
            page.on_connection_changed(connected)

    def on_app_quitting(self):
        for page in self.by_tab.values():
            page.on_app_quitting()
