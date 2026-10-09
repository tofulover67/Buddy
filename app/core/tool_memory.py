#!/usr/bin/env python3
"""
Letting go of tools that haven't been used for a while.

Every tool drawn keeps its pictures while it's hidden, so switching back to
it is instant - ~135 MB a tool at 4K, ~35 MB at 1080p (measured
2026-10-04; on integrated graphics that's all RAM). Buddy keeps the few
tools used most recently ready; one that has been out of sight longer than
a while is let go (Chromium discards the page, WebSurface.let_go), and comes
back the next time it's shown - reloaded and redrawn from the state Python
keeps, as a first visit (~250 ms instead of ~130 ms).

A tool is kept anyway while it holds something only its page has: text
typed into a field and still there, a game in progress, an unsaved edit
(buddy.js keepAlive) - or while it's been sending its page anything lately
(a job running, an answer coming in).

How many stay ready, and for how long, goes by the computer's memory
(policy()): fewer and sooner on a small machine. BUDDY_KEEP_TOOLS and
BUDDY_LET_GO_MINUTES set them by hand; BUDDY_LET_GO_MINUTES=0 never lets go.
"""

import ctypes
import os
import sys
import time

from PySide6.QtCore import QEvent, QObject, QTimer

CHECK_MS = 30_000            # how often hidden tools are looked at
GB = 1024 ** 3


def memory_gb():
    """The computer's RAM in GB, or None if it can't be told."""
    try:
        if sys.platform == "win32":
            class Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = Status()
            status.dwLength = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys / GB
            return None
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / GB
    except (AttributeError, OSError, ValueError):
        return None


def policy(ram_gb, environ=os.environ):
    """(tools kept ready, minutes out of sight before one is let go); 0
    minutes: never. A small machine keeps fewer, for less long."""
    if ram_gb is None or ram_gb > 16:
        keep, minutes = 3, 10
    elif ram_gb > 8:
        keep, minutes = 3, 5
    else:
        keep, minutes = 2, 2
    try:
        keep = max(1, int(environ.get("BUDDY_KEEP_TOOLS") or keep))
        minutes = max(0.0, float(environ.get("BUDDY_LET_GO_MINUTES") or minutes))
    except ValueError:
        pass
    return keep, minutes


def to_let_go(seen, keep, minutes, now):
    """Which tools to let go: seen is {tool: when it was last on screen
    (time.monotonic()), or None if it's on screen now}. The `keep` seen most
    recently stay, and so does any seen less than `minutes` ago."""
    if minutes <= 0:
        return []
    order = sorted(seen, key=lambda t: float("inf") if seen[t] is None else seen[t], reverse=True)
    return [t for t in order[keep:] if seen[t] is not None and now - seen[t] >= minutes * 60]


class ToolMemory(QObject):
    """Watches the shell's tools and lets go of the ones out of sight long
    enough (see the module docstring)."""

    def __init__(self, shell, ram_gb=None):
        super().__init__(shell)
        self.shell = shell
        self.keep, self.minutes = policy(ram_gb if ram_gb is not None else memory_gb())
        self.seen = {}               # tool id -> last on screen (monotonic), None while on screen
        self._tools = {}             # page -> tool id
        self._timer = QTimer(self, interval=CHECK_MS, timeout=self.check)
        if self.minutes > 0:
            for tool, page in shell.pages.items():
                self._tools[page] = tool
                page.installEventFilter(self)
            self._timer.start()

    def eventFilter(self, obj, event):
        # A tool on screen, or out of sight from now (its own window too, in the desktop layout).
        tool = self._tools.get(obj)
        if tool is not None:
            if event.type() == QEvent.Show:
                self.seen[tool] = None
            elif event.type() == QEvent.Hide and tool in self.seen:
                self.seen[tool] = time.monotonic()
        return False

    def surfaces(self, page):
        """The web pages a tool shows (Media Manager holds two)."""
        from core.web_page import WebSurface
        return [w for w in (page, *page.findChildren(QObject)) if isinstance(w, WebSurface)]

    def note(self, now=None):
        """Brings `seen` up to date: what's on screen now, and when the rest were
        last (show and hide events keep it so; this catches anything they missed)."""
        now = time.monotonic() if now is None else now
        for tool, page in self.shell.pages.items():
            if page.isVisible():
                self.seen[tool] = None
            elif tool in self.seen and self.seen[tool] is None:
                self.seen[tool] = now

    def check(self):
        now = time.monotonic()
        self.note(now)
        for tool in to_let_go(self.seen, self.keep, self.minutes, now):
            page = self.shell.pages.get(tool)
            surfaces = self.surfaces(page) if page is not None else []
            if not surfaces:
                self.seen.pop(tool, None)          # nothing of it to let go (a Qt page, the Web tab)
                continue
            self._ask(tool, page, surfaces, now)

    def _ask(self, tool, page, surfaces, now):
        """Lets the tool go if none of its pages is holding on to anything."""
        answers = []

        def answered(may):
            answers.append(may)
            if len(answers) < len(surfaces) or not all(answers):
                return
            if page.isVisible() or self.seen.get(tool, 0) is None:
                return                             # shown again while it was being asked
            if any([s.let_go() for s in surfaces]):
                page._drawn_size = None            # core/shell_window.py: drawn again, from the start
            self.seen.pop(tool, None)              # nothing left to let go until it's shown again

        for surface in surfaces:
            surface.ask_to_let_go(answered, quiet_for=self.minutes * 60, now=now)
