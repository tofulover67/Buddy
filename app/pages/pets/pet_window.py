#!/usr/bin/env python3
"""
The pet's window: a small frameless, see-through, always-on-top window with
no parent, so it floats over Resolve and everything else and stays when
Buddy's window is hidden in the tray. The pet is drawn by web/pet/ (the
same sprites as the Pets tab's preview, web/sprites.js); what it does is
behaviour.py, ticked here a few times a second.

Where it stands is kept as the pet's feet - the bottom centre of the
window - in screen pixels, so the window can grow upwards for the speech
bubble and shrink back without the pet moving. Along the bottom of
whichever screen it's on by default; dragged (the page asks for a system
move, so the OS does the dragging), it stays at that height and walks
along it.

Protocol:
    to the view    pet, bubble
    from the view  click, drag, menu, ask, bubble
"""

import os
import random
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QMenu

from core.i18n import tr
from core.web_page import WebWindow

from .behaviour import SIZES, Behaviour

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "pet")

TICK_MS = 100
HOVER_GRACE_S = 0.35    # the pill stays this long after the mouse leaves
GWL_EXSTYLE = -20       # Windows: the native window's extended style...
WS_EX_TRANSPARENT = 0x20    # ...and its bit for "the mouse goes through"
WORK_CHECK_MS = 1000
SAVE_POS_MS = 600
BUBBLE_W = 300          # room for the bubble above the pet
BUBBLE_H = 128
WIDTH_RATIO = 1.3       # the pet's box is a little wider than high

# What a pet says when a pat opens no question field.
SAYINGS = ("Hi!", "Back to work?", "Nice edit.", "Need a break?", "Render's looking good.", "You've got this.")


def window_frame(size, bubble):
    """(width, height) of the window for a pet of this size (behaviour.SIZES
    key), with room for the speech bubble or without. No Qt; tested."""
    pet_h = SIZES.get(size, SIZES["medium"])
    pet_w = int(pet_h * WIDTH_RATIO)
    if bubble:
        return max(pet_w, BUBBLE_W), pet_h + BUBBLE_H
    return pet_w, pet_h


def place_in(area, anchor, frame):
    """The window's top-left for feet at `anchor` (x, y) with a window of
    `frame` (w, h), kept inside `area` (left, top, right, bottom). Returns
    (x, y, anchor_x, anchor_y): the anchor as clamped."""
    left, top, right, bottom = area
    w, h = frame
    ax = max(left + w // 2, min(anchor[0], right + 1 - (w - w // 2)))
    ay = max(top + h, min(anchor[1], bottom + 1))
    return ax - w // 2, ay - h, ax, ay


class PetWindow(WebWindow):
    web_dir = WEB_DIR
    transparent = True

    def __init__(self, owner, values):
        self.owner = owner
        self.values = dict(values)
        self._bubble = False            # the speech bubble is up (the page says)
        self._pill = False              # the mouse is over the pet: the hover pill is up
        self._hover_lost = None         # when the mouse left the pet (a grace period before the pill goes)
        self._anchor = None
        self._placing = False
        self._working = False
        self._last_tick = time.monotonic()
        self.behaviour = Behaviour(walks=values["walk"], sleep_after_s=values["sleep_minutes"] * 60)
        super().__init__(owner.host)
        kind = Qt.Window if sys.platform == "darwin" else Qt.Tool     # a Tool window hides with the app on a Mac
        self.setWindowFlags(kind | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle(tr("Pet"))
        self._anchor = self._start_anchor()
        self._place()
        self._tick_timer = QTimer(self, interval=TICK_MS, timeout=self._tick)
        self._tick_timer.start()
        self._work_timer = QTimer(self, interval=WORK_CHECK_MS, timeout=self._check_working)
        self._work_timer.start()
        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_POS_MS, timeout=self._save_pos)

    # ------------------------------------------------------------ geometry --

    def _screen(self):
        screen = None
        if self._anchor is not None:
            screen = QApplication.screenAt(QPoint(*self._anchor))
        if screen is None:
            host_window = getattr(self.owner.host, "windowHandle", None)
            handle = host_window() if host_window else None
            screen = handle.screen() if handle is not None else None
        return screen or QApplication.primaryScreen()

    def _area(self):
        g = self._screen().availableGeometry()
        return g.left(), g.top(), g.right(), g.bottom()

    def _start_anchor(self):
        pos = self.values.get("pos")
        if pos:
            return (pos["x"], pos["y"])
        left, _top, right, bottom = self._area()
        return (right - 220 if right - left > 500 else (left + right) // 2, bottom + 1)

    def _place(self):
        """The window around the pet's feet, inside its screen; the pet's
        walking room is that screen's width."""
        area = self._area()
        # Always with the room above for the bubble and the pill: a window
        # resized under the mouse showed its old picture top-aligned for a
        # frame (Chromium's), so the pet jumped - and the hover that grew it
        # ended, shrinking it again. The room is see-through and, except
        # while something's up in it, lets clicks through (_set_clickable).
        frame = window_frame(self.values["size"], True)
        x, y, ax, ay = place_in(area, self._anchor, frame)
        self._anchor = (ax, ay)
        pet_w = window_frame(self.values["size"], False)[0]
        self.behaviour.set_width(max(0, area[2] - area[0] + 1 - pet_w))
        self._placing = True
        try:
            self.setMinimumSize(*frame)
            self.setMaximumSize(*frame)
            self.setGeometry(x, y, *frame)
        finally:
            self._placing = False

    def _room(self) -> bool:
        """Whether something is up in the room above the pet - the bubble or the pill."""
        return self._bubble or self._pill

    def _set_clickable(self, on):
        """Whether the mouse reaches this window at all. Off, clicks go
        through to whatever is behind the see-through room above the pet;
        on while the mouse is over the pet or the pill or bubble is up.
        Windows: the native window's transparent-for-input style, toggled
        without Qt remaking the window. Elsewhere the window stays
        clickable (its room above the pet takes clicks)."""
        if sys.platform != "win32" or on == getattr(self, "_clickable", True):
            return
        self._clickable = on
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_TRANSPARENT) if on else (style | WS_EX_TRANSPARENT)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except (AttributeError, OSError, ValueError):
            pass

    def _pet_rect(self):
        """Where the pet itself is on screen (the window's bottom middle)."""
        pet_w, pet_h = window_frame(self.values["size"], False)
        return QRect(self.x() + (self.width() - pet_w) // 2, self.y() + self.height() - pet_h, pet_w, pet_h)

    def _check_hover(self):
        """The hover pill, from where the mouse is on screen - not from the
        page's enter and leave events, which the window's own growing and
        shrinking under the mouse would fire again and again. It goes a
        moment after the mouse leaves the pet (or, with the pill up, the
        whole window), so a move up to the pill doesn't lose it."""
        over = (self.geometry() if self._room() else self._pet_rect()).contains(QCursor.pos())
        if over:
            self._hover_lost = None
            if not self._pill:
                self._pill = True
                self.emit("hover", {"on": True})
        elif self._pill:
            now = time.monotonic()
            if self._hover_lost is None:
                self._hover_lost = now
            elif now - self._hover_lost >= HOVER_GRACE_S:
                self._pill, self._hover_lost = False, None
                self.emit("hover", {"on": False})
        self._set_clickable(self._room())

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._placing or self._anchor is None:
            return
        # Dragged (the system move): the feet are where the window now is.
        w, h = self.width(), self.height()
        self._anchor = (self.x() + w // 2, self.y() + h)
        left = self._area()[0]
        pet_w = window_frame(self.values["size"], False)[0]
        self.behaviour.set_x(self._anchor[0] - pet_w // 2 - left)
        self._save_timer.start()

    def _save_pos(self):
        if self._anchor is not None:
            self.owner.remember_pos({"x": self._anchor[0], "y": self._anchor[1]})

    # ---------------------------------------------------------------- life --

    def apply(self, values):
        """The tab's settings changed."""
        self.values = dict(values)
        self.behaviour.walks = values["walk"]
        self.behaviour.sleep_after_s = values["sleep_minutes"] * 60
        self._place()
        self._push_pet()

    def web_ready(self):
        self._push_pet()

    def _push_pet(self):
        skins = self.values.get("skins") if isinstance(self.values.get("skins"), dict) else {}
        colours = self.values.get("colours") if isinstance(self.values.get("colours"), dict) else {}
        self.emit("pet", {"species": self.values["species"], "name": self.values["name"],
                          "skin": skins.get(self.values["species"], "classic"),
                          "colour": colours.get(self.values["species"], "#7c5cff"),
                          "size": self.values["size"], "px": SIZES[self.values["size"]],
                          "state": self.behaviour.state, "facing": self.behaviour.facing,
                          "ask": self.values["ask"]})

    def _check_working(self):
        working = self.owner.working()
        if working and not self._working:
            self.behaviour.activity()
        self._working = working

    def _tick(self):
        now = time.monotonic()
        dt, self._last_tick = now - self._last_tick, now
        self._check_hover()
        if self._room():
            self.behaviour.stand_still()        # not out from under the bubble or the pill
        changed = self.behaviour.tick(dt, working=self._working)
        if self.behaviour.state == "walk":
            left = self._area()[0]
            pet_w = window_frame(self.values["size"], False)[0]
            self._anchor = (int(left + self.behaviour.x + pet_w // 2), self._anchor[1])
            self._place()
        if changed:
            self._push_pet()

    def pat(self):
        """Patted: counted, happy, and - unless turned off - a question field."""
        self.owner.achieve("pats", 1)
        self.behaviour.poke()
        self._push_pet()
        if self.values["ask"]:
            self.emit("bubble", {"kind": "ask"})
        else:
            self.emit("bubble", {"kind": "say", "text": random.choice(SAYINGS)})

    def celebrate(self, achievement):
        self.behaviour.cheer()
        self._push_pet()
        self.emit("bubble", {"kind": "cheer", "title": achievement["title"]})

    # ------------------------------------------------------ from the view --

    def on_click(self, _payload=None):
        self.pat()

    def on_drag(self, _payload=None):
        handle = self.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def on_bubble(self, payload):
        """The bubble opened or closed: while it's up, the room above the pet takes the mouse."""
        self._bubble = bool((payload or {}).get("open"))
        self._set_clickable(self._room())

    def changeEvent(self, event):
        # A click anywhere else - another window, the desktop - takes the
        # window's activation: the question field goes away with it.
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self._bubble and not self.isActiveWindow():
            self.emit("bubble", None)

    def showEvent(self, event):
        super().showEvent(event)
        # Shown (or shown again), the native window may be fresh: the
        # click-through is put back as the next tick finds it.
        self._clickable = None

    def on_ask(self, payload):
        self.behaviour.activity()
        self.owner.ask_buddy((payload or {}).get("text"))

    def on_open(self, _payload=None):
        """The hover pill's Open Buddy: the window to the front, as it was."""
        self.behaviour.activity()
        self.owner.host.bring_to_front()

    def on_menu(self, _payload=None):
        menu = QMenu(self)
        menu.addAction(tr("Pets…")).triggered.connect(lambda: self._open("pets"))
        menu.addAction(tr("Achievements…")).triggered.connect(lambda: self._open("achievements"))
        menu.addSeparator()
        menu.addAction(tr("Hide pet")).triggered.connect(self.owner.hide_pet)
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def _open(self, page_id):
        """Settings, on the Pets or the Achievements page."""
        self.owner.host.bring_to_front()
        self.owner.host.open_settings(page_id)

    def closeEvent(self, event):
        self._tick_timer.stop()
        self._work_timer.stop()
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_pos()
        super().closeEvent(event)
