#!/usr/bin/env python3
"""
A new user's first start: a sidebar with a few key tools, and a tour of
Buddy's window.

A new install - no settings.json yet, and no sign Buddy has run here
before - starts with STARTER_TOOLS in the rail (nav_layout.starter_layout).
The other tools are in it too, hidden but still built and running, and
More tools at the foot of the rail (core/more_tools.py) puts them back.
Someone updating keeps the sidebar they have and gets no tour.

The tour opens on a welcome card - Take the tour, Skip, Don't show this
again - then dims the window and lights up each part of it in turn, with a
card beside it. It's a see-through web view over the whole window
(app/web/shell/tour/), the way the header's dropdown lies over the panes.
Skip asks again at the next start; Don't show this again, or taking the
tour, is the end of it. Settings > General > Take the tour starts it again
whenever.

It's the sidebar layout's tour: the desktop layout's tool windows are
native, so nothing can be drawn over them, and there it waits.

Protocol:
    to the view    step
    from the view  start, next, back, skip
"""

import json
import os

from PySide6.QtCore import QEvent, QPoint, QRect, QTimer

from core import atomic_io, nav_layout
from core.web_page import WEB_COMMON_DIR, WebWindow

# What a new install's rail shows, in the rail's own order.
STARTER_TOOLS = (
    "manual_chat", "project_setup", "media_manager", "text_animator", "transcribe", "audio_assistant",
    "time_tracker", "buddy_network",
)

# True while the tour is still to be offered at start-up: set for a new
# install, cleared by Don't show this again or by taking it.
TOUR_KEY = "tour_pending"

# After the window first shows: the rail and the first tool have drawn.
TOUR_DELAY_MS = 900
# The header and rail are web views of their own: until both are loaded
# there's nothing to point at. Asked again this often, for this long.
READY_POLL_MS = 150
READY_WAIT_MS = 10000
# A window resized under the tour: the light moves once it settles.
REFIT_MS = 120

TRAY_TEXT = {
    "win": "Closing the window keeps Buddy running in the system tray, so Time Tracker keeps tracking. "
           "To quit, right-click Buddy's icon there and choose Quit.",
    "mac": "Closing the window keeps Buddy running in the menu bar, so Time Tracker keeps tracking. "
           "To quit, click Buddy's icon there and choose Quit.",
}


def is_new_install(shared, other_files=()) -> bool:
    """No settings.json (nor its backup), and none of `other_files` either -
    files an earlier run of Buddy wrote whatever was done with it (the
    achievements' record of the days it was used)."""
    if not getattr(shared, "is_new", False):
        return False
    return not any(os.path.exists(p) or os.path.exists(atomic_io.backup_path(p)) for p in other_files)


def set_up_new_install(shared, registry):
    """The starter sidebar and the tour, saved straight away: the next start
    finds a settings.json, so this happens once."""
    shared[nav_layout.SETTINGS_KEY] = nav_layout.starter_layout(registry, STARTER_TOOLS)
    shared[TOUR_KEY] = True
    shared.save()


def _step(step_id, title, text, target=None):
    return {"id": step_id, "title": title, "text": list(text), "target": target}


def tour_steps(ask=True, more=0, tray=None) -> list:
    """The tour, welcome card first. ask: Ask Buddy is in the rail. more:
    how many tools More tools can add. tray: "win" or "mac" when closing the
    window keeps Buddy running, else None. A step's target is what it
    lights up (TourView._find) - None for a card in the middle."""
    steps = [
        _step("welcome", "Welcome to Buddy", [
            "Buddy is a set of tools for DaVinci Resolve, in one window beside it. Want a quick look round? "
            "It takes about a minute."]),
        _step("rail", "Your tools", [
            "Each tool has a page of its own. Pick one here to open it, or right-click it for its settings."],
            "rail"),
    ]
    if more == 1:
        steps.append(_step("more", "There's more", [
            "Buddy starts with a few key tools. One more is ready whenever you want it – add it here."], "more"))
    elif more > 1:
        steps.append(_step("more", "There's more", [
            f"Buddy starts with a few key tools. {more} more are ready whenever you want them – add any of "
            f"them here."], "more"))
    steps += [
        _step("connection", "Buddy and Resolve", [
            "Buddy works on the project open in DaVinci Resolve, and this says whether it can reach it. "
            "After starting Resolve or opening another project, press this button to connect again."],
            "connection"),
        _step("pane", "Where tools work", [
            "The tool you pick opens here. Tools keep running when you switch to another – Time Tracker "
            "carries on tracking while you use the rest."], "pane"),
    ]
    if ask:
        steps.append(_step("ask", "Ask Buddy", [
            "Questions about Resolve? Ask Buddy answers them, and can read your project to help – it changes "
            "it only if you say yes.",
            "It runs on an AI model you choose in Settings > AI: your own key for an online one, or one on "
            "this computer."], "tool:manual_chat"))
    steps += [
        _step("split", "Two tools at once", [
            "Dual view puts a second tool beside this one – say, Ask Buddy on one side while you work in "
            "another tool."], "split"),
        _step("bug", "Something not right?", [
            "Send a bug report from here: say what went wrong and add screenshots if they help. It goes to "
            "Buddy's developer, with the versions of Buddy, your system and Resolve."], "bug"),
        _step("settings", "Settings", [
            "Themes, language, the tool Buddy opens on and the sidebar's order are all in Settings – and "
            "this tour, to take again."], "settings"),
    ]
    done = ["That's the tour. Pick a tool to get started."]
    if tray in TRAY_TEXT:
        done.append(TRAY_TEXT[tray])
    steps.append(_step("done", "You're all set", done))
    return steps


# Where the elements are in a header or rail view: a box each, in the
# view's CSS pixels - scrolled into view first (a short window's rail
# scrolls). As JSON text: runJavaScript hands an object or array back to
# PySide6 as an empty string.
FIND_JS = """(() => {
    const nodes = %s.map(s => document.querySelector(s)).filter(n => n && n.getClientRects().length);
    if (nodes.length) nodes[0].scrollIntoView({block: "nearest"});
    return JSON.stringify(nodes.map(n => n.getBoundingClientRect())
                               .map(b => ({x: b.left, y: b.top, w: b.width, h: b.height})));
})()"""

# Header elements by step target - each lit on its own (the bug button
# sits between the connection's status and its button).
HEADER_TARGETS = {
    "connection": ["#status", "#connect"],
    "split": ["#split"],
    "bug": ["#bug"],
    "settings": ["#settings"],
}


class TourView(WebWindow):
    """The tour, over the whole of the shell's window. `steps` from
    tour_steps(); `start` 0 opens on the welcome card, 1 goes straight to
    the first part of the window (Settings' Take the tour)."""

    web_dir = os.path.join(WEB_COMMON_DIR, "shell", "tour")
    transparent = True

    def __init__(self, shell, steps, start=0):
        self.shell = shell
        self.steps = steps
        self.index = max(0, min(start, len(steps) - 1))
        self._asked = 0             # the latest _find; an older answer is dropped
        super().__init__(shell)
        central = shell.centralWidget()
        self.setParent(central)
        self.setGeometry(central.rect())
        central.installEventFilter(self)
        self._refit = QTimer(self, singleShot=True, interval=REFIT_MS)
        self._refit.timeout.connect(self._show_step)
        self.hide()                 # until the page is there to draw

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(obj.rect())
            self.raise_()
            self._refit.start()
        return False

    def web_ready(self):
        self._show_step()
        self.show()
        self.raise_()
        self.view.setFocus()

    def finish(self):
        """Gone: nothing it was waiting on (a resize, a lookup) draws again."""
        self._refit.stop()
        self._asked += 1
        self.parentWidget().removeEventFilter(self)
        self.hide()
        self.deleteLater()

    # --------------------------------------------------------- the steps --
    def _show_step(self):
        step = self.steps[self.index]
        self._asked += 1
        asked = self._asked
        self._find(step["target"], lambda rects: asked == self._asked and self._send(step, rects))

    def _send(self, step, rects):
        self.raise_()               # above the header, which raises itself as it's placed
        self.emit("step", {
            "id": step["id"], "title": step["title"], "text": step["text"],
            "rects": [{"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()} for r in rects],
            # Where the card goes: under what's along the top, else beside it.
            "side": "below" if step["target"] in HEADER_TARGETS else "right",
            "welcome": step["id"] == "welcome",
            "number": self.index, "count": len(self.steps) - 1,
            "first": self.index <= 1, "last": self.index == len(self.steps) - 1,
        })

    def _find(self, target, done):
        """done([QRect in this view]) - empty when it isn't on screen."""
        shell = self.shell
        if target == "rail":
            done(self._rect_of(shell.rail))
        elif target == "pane":
            done(self._rect_of(shell.main_pane))
        elif target == "more":
            self._ask(shell.rail, ["#more"], done)
        elif isinstance(target, str) and target.startswith("tool:"):
            self._ask(shell.rail, [f'[data-id="{target[5:]}"]'], done)
        elif target in HEADER_TARGETS:
            self._ask(shell.header, HEADER_TARGETS[target], done)
        else:
            done([])

    def _rect_of(self, widget):
        if widget is None or not widget.isVisible():
            return []
        return [QRect(widget.mapTo(self.parentWidget(), QPoint(0, 0)), widget.size())]

    def _ask(self, chrome, selectors, done):
        """Where `selectors` are in one of the shell's web views (the header,
        the rail)."""
        if chrome is None or not chrome.isVisible():
            done([])
            return
        origin = chrome.mapTo(self.parentWidget(), QPoint(0, 0))
        zoom = chrome.view.zoomFactor()

        def got(text):
            rects = []
            try:
                for box in json.loads(text):
                    x, y, w, h = (float(box[k]) * zoom for k in ("x", "y", "w", "h"))
                    if w > 0 and h > 0:
                        rects.append(QRect(origin.x() + round(x), origin.y() + round(y), round(w), round(h)))
            except (KeyError, TypeError, ValueError):
                rects = []
            done(rects)

        chrome.view.page().runJavaScript(FIND_JS % json.dumps(selectors), 0, got)

    # ------------------------------------------------------ from the view --
    def on_start(self, _payload=None):
        """Take the tour: it won't be offered at start-up again."""
        self.shell.end_tour_offer()
        self._go(1)

    def on_next(self, _payload=None):
        if self.index >= len(self.steps) - 1:
            self.shell.close_tour()
        else:
            self._go(self.index + 1)

    def on_back(self, _payload=None):
        self._go(self.index - 1)

    def on_skip(self, payload=None):
        """Skip on the welcome card asks again next time, unless Don't show
        this again is ticked; anywhere later, the tour was taken."""
        if self.index > 0 or bool((payload or {}).get("never")):
            self.shell.end_tour_offer()
        self.shell.close_tour()

    def _go(self, index):
        if 1 <= index < len(self.steps) and index != self.index:
            self.index = index
            self._show_step()
