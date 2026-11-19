#!/usr/bin/env python3
"""
Data Graphics - a main tab of its own, under Editing Tools, for graphics made
from numbers, each kind a sub-tab and a page of its own in its own folder:

    counters/   Counters - a number that runs up or down to where it's set.
    graphs/     Graphs - bar, line and pie charts that draw themselves.

Neither is built yet: each page says what it will do and that it's coming.
How they're meant to be built is in docs/DATA_GRAPHICS_PLAN.md.

This page only holds them: it shows one at a time (the sub-tab row is in
each page's own view, web/tabs.js) and passes the shell's hooks on.
"""

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout

from pages.base import ToolPage
from pages.data_graphics.counters.page import CountersPage
from pages.data_graphics.graphs.page import GraphsPage

TABS = ("counters", "graphs")
DEFAULTS = {"tab": "counters"}


class DataGraphicsPage(ToolPage):
    tool_id = "data_graphics"
    display_name = "Data Graphics"
    category = "Editing Tools"

    def build_ui(self):
        self.settings = self.host.tool_settings(self.tool_id, dict(DEFAULTS))
        self.stack = QStackedWidget(self)
        self.pages = {"counters": CountersPage(self.host), "graphs": GraphsPage(self.host)}
        for page in self.pages.values():
            page._graphics = self
            self.stack.addWidget(page)
        tab = self.settings.get("tab")
        self.stack.setCurrentWidget(self.pages[tab if tab in TABS else "counters"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

    def current(self):
        return self.stack.currentWidget()

    def show_tab(self, tab):
        page = self.pages.get(tab)
        if page is None or page is self.current():
            return
        self.stack.setCurrentWidget(page)
        self.settings["tab"] = tab
        self.settings.save()
        if self.isVisible():
            page.on_shown()

    # -- the shell's hooks, passed on -------------------------------------

    def on_shown(self):
        self.current().on_shown()

    def on_theme_changed(self):
        for page in self.pages.values():
            page.on_theme_changed()

    def on_connection_changed(self, connected):
        for page in self.pages.values():
            page.on_connection_changed(connected)

    def on_app_quitting(self):
        for page in self.pages.values():
            page.on_app_quitting()
