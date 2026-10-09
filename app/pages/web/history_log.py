#!/usr/bin/env python3
"""
The Web tab's history: the pages visited, newest last, one file for each
profile (~/.buddy/web-history/<profile>.json). Private tabs are never in
it. Kept for KEEP_DAYS and at most MAX_ENTRIES; the History window
(history_window.py) searches it, opens from it and clears it.

An entry: {"id": n, "url": "https://...", "title": "...", "t": seconds}.
The same page again within MERGE_S is one visit (a reload, a page that
changes its address as it loads), its time moved on.
"""

import os
import time
from pathlib import Path

from core import atomic_io
from core.settings_store import BUDDY_DIR

FOLDER = os.path.join(BUDDY_DIR, "web-history")
KEEP_DAYS = 90
MAX_ENTRIES = 20000
MERGE_S = 10 * 60
MAX_URL = 2048
MAX_TITLE = 300
RANGES = {"hour": 3600, "day": 86400, "week": 7 * 86400, "all": None}


def path_for(profile_id, folder=None):
    return os.path.join(folder or FOLDER, f"{profile_id}.json")


def worth_keeping(url):
    """Only the web's own pages: not the new-tab page, files or data."""
    return isinstance(url, str) and url.startswith(("http://", "https://")) and len(url) <= MAX_URL


def clean(raw, now=None):
    """Entries read back from disk: well formed, oldest first, trimmed."""
    out = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        url, title, t, ident = e.get("url"), e.get("title"), e.get("t"), e.get("id")
        if not worth_keeping(url) or isinstance(t, bool) or not isinstance(t, (int, float)):
            continue
        if isinstance(ident, bool) or not isinstance(ident, int):
            continue
        out.append({"id": ident, "url": url, "title": title[:MAX_TITLE] if isinstance(title, str) else "",
                    "t": float(t)})
    out.sort(key=lambda e: e["t"])
    return trim(out, now)


def trim(entries, now=None):
    now = time.time() if now is None else now
    oldest = now - KEEP_DAYS * 86400
    kept = [e for e in entries if e["t"] >= oldest]
    return kept[-MAX_ENTRIES:]


class History:
    """One profile's history, read from and saved to its file."""

    def __init__(self, path):
        self.path = path
        try:
            raw = atomic_io.read_json(path, default=[])
        except Exception:                     # noqa: BLE001 - damaged past its backup: start again
            raw = []
        self.entries = clean(raw)
        self._next = max((e["id"] for e in self.entries), default=0) + 1
        self.dirty = False
        self._forget_backup = False

    def add(self, url, title="", now=None):
        """A page visited. Returns its entry, or None if it isn't kept."""
        if not worth_keeping(url):
            return None
        now = time.time() if now is None else now
        title = (title or "")[:MAX_TITLE]
        for e in reversed(self.entries[-20:]):
            if e["url"] == url and now - e["t"] < MERGE_S:
                e["t"] = now
                if title:
                    e["title"] = title
                self.entries.remove(e)
                self.entries.append(e)
                self.dirty = True
                return e
        entry = {"id": self._next, "url": url, "title": title, "t": now}
        self._next += 1
        self.entries.append(entry)
        if len(self.entries) > MAX_ENTRIES + 500:
            self.entries = trim(self.entries, now)
        self.dirty = True
        return entry

    def retitle(self, url, title):
        """A page's title arrived after its address: on its latest visit."""
        if not title:
            return
        for e in reversed(self.entries[-50:]):
            if e["url"] == url:
                if e["title"] != title[:MAX_TITLE]:
                    e["title"] = title[:MAX_TITLE]
                    self.dirty = True
                return

    def search(self, query="", limit=300):
        """Newest first: every word of `query` in the title or address.
        Returns (entries, more) - more: there are others past `limit`."""
        words = [w for w in (query or "").lower().split() if w]
        found = []
        for e in reversed(self.entries):
            if words:
                hay = f"{e['title']} {e['url']}".lower()
                if not all(w in hay for w in words):
                    continue
            found.append(e)
            if len(found) > limit:
                return found[:limit], True
        return found, False

    def suggest(self, text, limit=6, now=None):
        """The address bar's suggestions for what's typed: pages visited
        with every word in their title or address, once each - an address
        starting with what's typed first, then the most visited and the
        most recent. [{"url", "title"}]."""
        words = [w for w in (text or "").lower().split() if w]
        if not words:
            return []
        now = time.time() if now is None else now
        typed = words[0] if len(words) == 1 else ""
        pages = {}
        for e in reversed(self.entries):
            page = pages.get(e["url"])
            if page is not None:
                page["visits"] += 1
                page["title"] = page["title"] or e["title"]
                continue
            hay = f"{e['title']} {e['url']}".lower()
            if all(w in hay for w in words):
                pages[e["url"]] = {"url": e["url"], "title": e["title"], "visits": 1, "last": e["t"]}

        def score(page):
            bare = page["url"].lower().split("://", 1)[-1]
            bare = bare[4:] if bare.startswith("www.") else bare
            age = now - page["last"]
            return ((30 if typed and bare.startswith(typed) else 0) + min(page["visits"], 20)
                    + (6 if age < 86400 else 3 if age < 7 * 86400 else 0) - len(bare) / 1000)

        best = sorted(pages.values(), key=score, reverse=True)[:limit]
        return [{"url": p["url"], "title": p["title"]} for p in best]

    def remove(self, ids):
        ids = set(ids)
        before = len(self.entries)
        self.entries = [e for e in self.entries if e["id"] not in ids]
        self.dirty = self.dirty or len(self.entries) != before
        self._forget_backup = self._forget_backup or len(self.entries) != before

    def clear(self, span, now=None):
        """Forget the last hour, day or week ("hour", "day", "week") - or all of it."""
        if span not in RANGES:
            return
        seconds = RANGES[span]
        if seconds is None:
            self.entries = []
        else:
            since = (time.time() if now is None else now) - seconds
            self.entries = [e for e in self.entries if e["t"] < since]
        self.dirty = True
        self._forget_backup = True

    def save(self):
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        atomic_io.write_json(self.path, trim(self.entries), indent=None)
        if self._forget_backup:
            # A previous save (or damaged recovery copy) still contains
            # visits the user explicitly removed. Never recover those.
            for path in self._recovery_files():
                try:
                    path.unlink()
                except OSError:             # gone already, or held open: the save itself stands
                    pass
            self._forget_backup = False
        self.dirty = False

    def _recovery_files(self):
        path = Path(self.path)
        yield Path(atomic_io.backup_path(self.path))
        if path.parent.is_dir():
            yield from (entry for entry in path.parent.iterdir()
                        if entry.name.startswith(path.name + ".corrupt-"))

    def delete_file(self):
        self.entries = []
        self.dirty = False
        self._forget_backup = False
        for path in (Path(self.path), *self._recovery_files()):
            try:
                path.unlink()
            except OSError:
                pass
