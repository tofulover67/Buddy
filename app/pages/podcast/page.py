#!/usr/bin/env python3
"""
Podcast - a main tab of its own, under its own heading, holding three
tools as sub-tabs, each a page of its own in its own folder:

    sync/       Podcast Sync - lines up every camera and recorder by
                timecode or by sound, and builds a new stacked timeline
                (a track per camera, a track per mic).
    switch/     Auto Switch - cuts between the cameras of a stacked
                timeline by who is speaking, a camera pinned to each voice
                (even several voices on one mixed track).
    text_edit/  Text Edit - edit the timeline by editing its transcript.

This page only holds them: it shows one at a time (the sub-tab row is in
each page's own view, web/tabs.js) and passes the shell's hooks on.
"""

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout

from pages.base import ToolPage
from pages.podcast.switch.page import AutoSwitchPage
from pages.podcast.sync.page import PodcastSyncPage
from pages.podcast.text_edit.page import TextEditPage

TABS = ("sync", "switch", "text")
DEFAULTS = {"tab": "sync"}


class PodcastPage(ToolPage):
    tool_id = "podcast"
    display_name = "Podcast"
    category = "Podcast"

    def build_ui(self):
        self.settings = self.host.tool_settings(self.tool_id, dict(DEFAULTS))
        self.stack = QStackedWidget(self)
        self.pages = {"sync": PodcastSyncPage(self.host), "switch": AutoSwitchPage(self.host),
                      "text": TextEditPage(self.host)}
        for page in self.pages.values():
            page._podcast = self
            self.stack.addWidget(page)
        tab = self.settings.get("tab")
        self.stack.setCurrentWidget(self.pages[tab if tab in TABS else "sync"])
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

    def working_on(self):
        for page in self.pages.values():
            busy = page.working_on()
            if busy:
                return busy
        return None

    def settings_pages(self):
        return [p for page in self.pages.values() for p in page.settings_pages()]

    def on_setting(self, key, value, ui):
        for page in self.pages.values():
            if any(f.get("key") == key for p in page.settings_pages() for f in p.get("fields", [])):
                return page.on_setting(key, value, ui)
        return None

    def on_settings_action(self, action, ui):
        for page in self.pages.values():
            page.on_settings_action(action, ui)

    def ai_models(self):
        return [m for page in self.pages.values() for m in page.ai_models()]

    def ai_jobs(self):
        return [j for page in self.pages.values() for j in page.ai_jobs()]

    def models_busy(self):
        return next((why for why in (p.models_busy() for p in self.pages.values()) if why), "")

    def models_moving(self):
        for page in self.pages.values():
            page.models_moving()

    def models_moved(self):
        for page in self.pages.values():
            page.models_moved()

    def automation_steps(self):
        return [s for page in self.pages.values() for s in page.automation_steps()]

    def run_automation_step(self, step_id, options, run):
        for page in self.pages.values():
            if any(s.get("id") == step_id for s in page.automation_steps()):
                return page.run_automation_step(step_id, options, run)
        return super().run_automation_step(step_id, options, run)
