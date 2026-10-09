#!/usr/bin/env python3
"""
The Web tab's History window (web page in history/): the pages visited in
the profile in use, newest first and grouped by day - a search, a page
opened in a new tab (or the one you're on), entries taken off the list,
and Clear browsing data (clear_window.py). A window of its own beside
Resolve, like the Downloads window.

The browser (page.py WebBrowserPage) keeps the history (history_log.py);
this is a view of it.

Protocol (history/):
    to the view    history {items, more, query, profile}
    from the view  search, more, open, remove, clear, close
"""

import os

from PySide6.QtCore import Qt

from core.link_peek_web import _place
from core.web_page import WebDialog, _theme_host
from pages.web import history_log

PAGE = 300          # entries sent at a time; More sends the next lot


class HistoryWindow(WebDialog):
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")
    SIZE = (600, 700)

    def __init__(self, owner):
        self.owner = owner
        self.query = ""
        self.limit = PAGE
        super().__init__(_theme_host(owner), owner.window(), "History", self.SIZE)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setModal(False)
        _place(self, self.SIZE)

    def web_ready(self):
        self.refresh()

    def refresh(self):
        self.emit("history", self.owner.history_view(self.query, self.limit))

    # What the page asks; the browser does it.
    def on_search(self, payload):
        query = (payload or {}).get("query")
        self.query = query[:200] if isinstance(query, str) else ""
        self.limit = PAGE
        self.refresh()

    def on_more(self, _payload=None):
        self.limit += PAGE
        self.refresh()

    def on_open(self, payload):
        payload = payload or {}
        url = payload.get("url")
        if history_log.worth_keeping(url):
            self.owner.open_from_history(url, here=bool(payload.get("here")))

    def on_remove(self, payload):
        ids = (payload or {}).get("ids")
        ids = [i for i in ids if isinstance(i, int) and not isinstance(i, bool)] if isinstance(ids, list) else []
        if ids:
            self.owner.remove_history(ids)

    def on_clear(self, _payload=None):
        self.owner.show_clear_data()

    def on_close(self, _payload=None):
        self.close()
