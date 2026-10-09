#!/usr/bin/env python3
"""
The Web tab's Clear browsing data window (web page in clear/): what to
forget - history, the address bar's sites, recently closed tabs, the
Downloads list, cookies and sign-ins, cached files, site permissions - and
from when: the last hour, day, week or all time. The profile in use only.
Opened from the menu (Ctrl+Shift+Del), History and Settings.

The browser (page.py WebBrowserPage.clear_data) does the clearing.

Protocol (clear/):
    to the view    clear {profile, many}
    from the view  clear {span, what}, close
"""

import os

from PySide6.QtCore import Qt

from core.link_peek_web import _place
from core.web_page import WebDialog, _theme_host
from pages.web import history_log

KINDS = ("history", "sites", "closed", "downloads", "cookies", "cache", "permissions")


class ClearWindow(WebDialog):
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clear")
    SIZE = (440, 520)

    def __init__(self, owner):
        self.owner = owner
        super().__init__(_theme_host(owner), owner.window(), "Clear browsing data", self.SIZE)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setModal(False)
        _place(self, self.SIZE)

    def web_ready(self):
        self.emit("clear", {"profile": self.owner.current_profile()["name"], "many": len(self.owner.profiles) > 1})

    def on_clear(self, payload):
        payload = payload or {}
        span = payload.get("span")
        what = [w for w in payload.get("what") or [] if w in KINDS] if isinstance(payload.get("what"), list) else []
        if span not in history_log.RANGES or not what:
            return
        self.owner.clear_data(span, what)
        self.emit("cleared", {"cookies": "cookies" in what})

    def on_close(self, _payload=None):
        self.close()
