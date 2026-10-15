#!/usr/bin/env python3
"""
The pet's window: a small frameless, see-through, always-on-top window with
no parent, so it floats over Resolve and everything else and stays when
Buddy's window is hidden in the tray. The pet is drawn by web/pet/ (the
same sprites as the Pets tab's preview, web/sprites.js); what it does is
behaviour.py, ticked here a few times a second.

Where it stands is kept as the pet's feet - the bottom centre of its box,
a little above the window's bottom (the room under it and to either side
is for it to swing into) - in screen pixels. Along the bottom of
whichever screen it's on, walking along it; with nothing under its feet
(left in mid-air by an older Buddy, or with Physics off, or its floor
gone), it falls - with Physics on. The
pet's own box stays on the screen; the window's see-through edges may
hang off it, and the bubble and the pill above the pet are slid back in
("room").

Picked up (the page says "grab" once a press has moved, and where the pet
hangs from), the window moves itself after the mouse every frame while it
dangles; let go ("drop", or the button found up), it's thrown, bounces off
the screens' edges and lands on a floor - physics.py does the sums, the
page draws the swing ("pose") and the bump of a landing ("land").

With Physics off (a Pets setting) a drag is the system's own move ("drag"),
as before there was any: the pet stays where it's put, at any height, and
walks along there - nothing makes it fall.

Protocol:
    to the view    pet, bubble, hover, pose, land, room
    from the view  click, grab, drag, drop, menu, ask, open, bubble
"""

import os
import random
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu

from core.i18n import tr
from core.web_page import WebWindow

from . import physics
from .behaviour import SIZES, Behaviour

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "pet")

TICK_MS = 100
FRAME_MS = 16           # held or in the air: a frame this often, timed by time.perf_counter() (before
                        # Python 3.13, monotonic() moves in 16 ms steps on Windows - too coarse for a throw)
HOVER_GRACE_S = 0.35    # the pill stays this long after the mouse leaves
GWL_EXSTYLE = -20       # Windows: the native window's extended style...
WS_EX_TRANSPARENT = 0x20    # ...and its bit for "the mouse goes through"
WORK_CHECK_MS = 1000
SAVE_POS_MS = 600
BUBBLE_W = 300          # room for the bubble above the pet
BUBBLE_H = 128
WIDTH_RATIO = 1.3       # the pet's box is a little wider than high
SWING_ROOM = 0.35       # room below and to either side of the pet, a share of its height: swung, it isn't cut off
POSE_DEG = 0.2          # the page hears of a new pose when it has turned this much...
POSE_SCALE = 0.003      # ...or stretched this much (core/web_page.emit() is not free)

# What a pet says when a pat opens no question field.
SAYINGS = ("Hi!", "Back to work?", "Nice edit.", "Need a break?", "Render's looking good.", "You've got this.")


def window_frame(size, bubble):
    """(width, height) of the window for a pet of this size (behaviour.SIZES
    key): with room for the speech bubble above it and its swing around it
    (room_below), or without - the pet's own box. No Qt; tested."""
    pet_h = SIZES.get(size, SIZES["medium"])
    pet_w = int(pet_h * WIDTH_RATIO)
    if bubble:
        below = room_below(size)
        return max(pet_w + 2 * below, BUBBLE_W), pet_h + BUBBLE_H + below
    return pet_w, pet_h


def room_below(size):
    """How far the window reaches below the pet's feet (and at least as far
    either side of it): see-through room for its swing when it's held."""
    return int(SIZES.get(size, SIZES["medium"]) * SWING_ROOM)


def place_in(area, anchor, frame, pet_w=None, below=0):
    """The window's top-left for feet at `anchor` (x, y) with a window of
    `frame` (w, h), reaching `below` px under the feet, in `area` (left,
    top, right, bottom). Returns (x, y, anchor_x, anchor_y): the anchor as
    clamped. Sideways, the pet's own box (`pet_w` wide, at the window's
    middle; without it, the whole window) stays inside the area - the
    window's see-through sides may hang off it. Up and down, the feet stay
    in it and the window's top - the room above - does too; the room below
    hangs past the bottom."""
    left, top, right, bottom = area
    w, h = frame
    pet_w = w if pet_w is None else pet_w
    ax = max(left + pet_w // 2, min(anchor[0], right + 1 - (pet_w - pet_w // 2)))
    ay = max(top + h - below, min(anchor[1], bottom + 1))
    return ax - w // 2, ay - (h - below), ax, ay


def room_shift(area, x, w):
    """How far the page slides what's above the pet (the bubble, the pill)
    to keep it on screen, for a window `w` wide at `x` in `area`: right
    (+) by as much as the window hangs off the area's left, left (-) by as
    much as it hangs off its right."""
    left, _top, right, _bottom = area
    return max(0, left - x) - max(0, x + w - 1 - right)


def _system_button_down():
    """Whether the primary mouse button is down right now, asked of the
    system itself - or None where it can't be asked."""
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            vk = 0x02 if user32.GetSystemMetrics(23) else 0x01      # SM_SWAPBUTTON: VK_RBUTTON : VK_LBUTTON
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)
        except (AttributeError, OSError):
            pass
    elif sys.platform == "darwin":
        try:
            from AppKit import NSEvent
            return bool(NSEvent.pressedMouseButtons() & 1)
        except (ImportError, AttributeError):
            pass
    return None


def _primary_button_down():
    """Whether the primary mouse button is down right now: a release the
    page never heard (let go over another window, or with Chromium busy)
    mustn't leave the pet stuck to the mouse. Qt's word where the system
    can't be asked."""
    down = _system_button_down()
    return bool(QGuiApplication.mouseButtons() & Qt.LeftButton) if down is None else down


def _fraction(value, default):
    """A fraction of the pet's box from the page: 0..1, or the default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return default
    return min(1.0, max(0.0, float(value)))


def _xy(point):
    """A QPoint as (x, y)."""
    return point.x(), point.y()


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
        self._placed_at = None          # where Buddy last put the window (its own moves aren't the user's)
        self._shift = 0                 # how far the page slides the bubble and the pill in (room_shift)
        self._motion = None             # a physics.Motion while it's held or in the air...
        self._map = None                # ...and the screens it's over (physics.Screens: desk coordinates)
        self._sent_pose = None          # the pose the page has
        self._last_frame = time.perf_counter()
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
        self._walk_from_here()          # not from the middle of the screen on its first walk
        self._tick_timer = QTimer(self, interval=TICK_MS, timeout=self._tick)
        self._tick_timer.start()
        self._work_timer = QTimer(self, interval=WORK_CHECK_MS, timeout=self._check_working)
        self._work_timer.start()
        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_POS_MS, timeout=self._save_pos)
        self._frame_timer = QTimer(self, interval=FRAME_MS, timeout=self._frame)
        self._frame_timer.setTimerType(Qt.PreciseTimer)

    # ------------------------------------------------------------ geometry --

    def _screen(self):
        screen = None
        if self._anchor is not None:
            # Just above the feet: the feet themselves are a pixel past the
            # bottom of the area, outside a screen with no taskbar there.
            screen = QApplication.screenAt(QPoint(self._anchor[0], self._anchor[1] - 1))
        if screen is None:
            host_window = getattr(self.owner.host, "windowHandle", None)
            handle = host_window() if host_window else None
            screen = handle.screen() if handle is not None else None
        return screen or QApplication.primaryScreen()

    def _area(self):
        g = self._screen().availableGeometry()
        return g.left(), g.top(), g.right(), g.bottom()

    def _screens(self):
        """Every screen (physics.Screens): where a thrown pet may fly, in
        desk coordinates. On Windows Qt keeps a screen's corner in real
        pixels and its size in its own, so screens scaled differently have
        gaps between them that the desk closes; elsewhere there are none."""
        screens = QApplication.screens() or [self._screen()]
        rects = []
        for screen in screens:
            g, a = screen.geometry(), screen.availableGeometry()
            rects.append(((g.left(), g.top(), g.right(), g.bottom()), (a.left(), a.top(), a.right(), a.bottom()),
                          screen.devicePixelRatio() if sys.platform == "win32" else 1.0))
        primary = QApplication.primaryScreen()
        return physics.Screens(rects, screens.index(primary) if primary in screens else 0)

    def _in_mid_air(self):
        """Whether there's nothing under the pet's feet: left standing in
        mid-air by an older Buddy (which let it be dragged anywhere), moved
        by something else, or the floor gone from under it (a taskbar
        moved, a screen changed)."""
        pet_w = window_frame(self.values["size"], False)[0]
        screens = self._screens()
        x, y = screens.to_desk(*self._anchor)
        return y < screens.desk().up_down(x - pet_w / 2, x + pet_w / 2, y)[1] - physics.TOUCH

    def _start_anchor(self):
        pos = self.values.get("pos")
        if pos:
            return (pos["x"], pos["y"])
        left, _top, right, bottom = self._area()
        return (right - 220 if right - left > 500 else (left + right) // 2, bottom + 1)

    def _place(self):
        """The window around the pet's feet, the pet inside its screen; the
        pet's walking room is that screen's width."""
        area = self._area()
        # Always with the room above for the bubble and the pill: a window
        # resized under the mouse showed its old picture top-aligned for a
        # frame (Chromium's), so the pet jumped - and the hover that grew it
        # ended, shrinking it again. The room is see-through and, except
        # while something's up in it, lets clicks through (_set_clickable).
        frame = window_frame(self.values["size"], True)
        pet_w = window_frame(self.values["size"], False)[0]
        x, y, ax, ay = place_in(area, self._anchor, frame, pet_w, room_below(self.values["size"]))
        self._anchor = (ax, ay)
        self.behaviour.set_width(max(0, area[2] - area[0] + 1 - pet_w))
        self._set_geometry(x, y, frame)
        shift = room_shift(area, x, frame[0])
        if shift != self._shift:
            self._shift = shift
            self.emit("room", {"shift": shift})

    def _set_geometry(self, x, y, frame=None):
        """Buddy's own move (moveEvent leaves it alone)."""
        self._placed_at = (x, y)
        self._placing = True
        try:
            if frame is None:
                self.move(x, y)
            else:
                self.setMinimumSize(*frame)
                self.setMaximumSize(*frame)
                self.setGeometry(x, y, *frame)
        finally:
            self._placing = False

    def _walk_from_here(self):
        """Where it walks from (Behaviour.x, along its screen): where its feet are."""
        left = self._area()[0]
        pet_w = window_frame(self.values["size"], False)[0]
        self.behaviour.set_x(self._anchor[0] - pet_w // 2 - left)

    def _room(self) -> bool:
        """Whether something is up in the room above the pet - the bubble or the pill."""
        return self._bubble or self._pill

    def _set_clickable(self, on):
        """Whether the mouse reaches this window at all. Off, clicks go
        through to whatever is behind the see-through room around the pet;
        on while the mouse is over the pet, or the pill or bubble is up and
        the mouse isn't in the room under the pet (over the taskbar).
        Windows: the native window's transparent-for-input style, toggled
        without Qt remaking the window; a Mac: its NSWindow's
        ignoresMouseEvents, the same way - otherwise the room under the pet
        would take the clicks meant for the Dock. Only on a real screen (a
        test's offscreen window has no native window behind its id)."""
        if on == getattr(self, "_clickable", True):
            return
        self._clickable = on
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = int(self.winId())
                style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                style = (style & ~WS_EX_TRANSPARENT) if on else (style | WS_EX_TRANSPARENT)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            except (AttributeError, OSError, ValueError):
                pass
        elif sys.platform == "darwin" and QGuiApplication.platformName() == "cocoa":
            try:
                import ctypes
                import objc
                view = objc.objc_object(c_void_p=ctypes.c_void_p(int(self.winId())))     # Qt's NSView
                view.window().setIgnoresMouseEvents_(not on)
            except Exception:  # noqa: BLE001 - no PyObjC, or no native window yet: it stays clickable
                pass

    def _pet_rect(self):
        """Where the pet itself is on screen (the window's middle, the room for its swing below it)."""
        pet_w, pet_h = window_frame(self.values["size"], False)
        below = room_below(self.values["size"])
        return QRect(self.x() + (self.width() - pet_w) // 2, self.y() + self.height() - below - pet_h, pet_w, pet_h)

    def _upper_rect(self):
        """The window down to the pet's feet - without the room under it."""
        return self.geometry().adjusted(0, 0, 0, -room_below(self.values["size"]))

    def _check_hover(self):
        """The hover pill, from where the mouse is on screen - not from the
        page's enter and leave events, which the window's own growing and
        shrinking under the mouse would fire again and again. It goes a
        moment after the mouse leaves the pet (or, with the pill up, the
        window above the pet's feet), so a move up to the pill doesn't lose
        it. The room under the feet hangs over the taskbar: clicks there
        always go through."""
        mouse = QCursor.pos()
        over = (self._upper_rect() if self._room() else self._pet_rect()).contains(mouse)
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
        self._set_clickable(self._room() and (over or not self.geometry().contains(mouse)))

    def moveEvent(self, event):
        super().moveEvent(event)
        if (self._placing or self._anchor is None or self._motion is not None
                or (self.x(), self.y()) == self._placed_at):
            return          # Buddy's own move (some systems tell of it late)
        # Moved by something else - the system, a screen gone: the feet are
        # where the window now is (in mid-air, it falls on the next tick).
        w, h = self.width(), self.height()
        self._anchor = (self.x() + w // 2, self.y() + h - room_below(self.values["size"]))
        self._walk_from_here()
        self._save_timer.start()

    def _save_pos(self):
        if self._anchor is not None:
            self.owner.remember_pos({"x": self._anchor[0], "y": self._anchor[1]})

    # ---------------------------------------------------------------- life --

    def apply(self, values):
        """The tab's settings changed (held or in the air, it's set down first)."""
        self.values = dict(values)
        self.behaviour.walks = values["walk"]
        self.behaviour.sleep_after_s = values["sleep_minutes"] * 60
        self._stop_motion()
        self._place()
        self._push_pet()

    def web_ready(self):
        self._push_pet()
        self.emit("room", {"shift": self._shift})

    def _push_pet(self):
        skins = self.values.get("skins") if isinstance(self.values.get("skins"), dict) else {}
        colours = self.values.get("colours") if isinstance(self.values.get("colours"), dict) else {}
        self.emit("pet", {"species": self.values["species"], "name": self.values["name"],
                          "skin": skins.get(self.values["species"], "classic"),
                          "colour": colours.get(self.values["species"], "#7c5cff"),
                          "size": self.values["size"], "px": SIZES[self.values["size"]],
                          "below": room_below(self.values["size"]),
                          "state": self.behaviour.state, "facing": self.behaviour.facing,
                          "ask": self.values["ask"], "physics": self._physics()})

    def _physics(self) -> bool:
        """Picked up and thrown (the Pets setting) - or, off, just moved
        where a drag puts it, and left at that height."""
        return bool(self.values.get("physics", True))

    def _check_working(self):
        working = self.owner.working()
        if working and not self._working:
            self.behaviour.activity()
        self._working = working

    def _tick(self):
        now = time.monotonic()
        dt, self._last_tick = now - self._last_tick, now
        if self._motion is not None:
            return          # held or in the air: the frames (_frame) have it, and its life waits
        self._check_hover()
        if self._room():
            self.behaviour.stand_still()        # not out from under the bubble or the pill
        elif self._physics() and self._in_mid_air():
            self._fall()
            return
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

    # ------------------------------------------------- held and thrown --

    def _hand(self):
        """Where the mouse is, in desk coordinates."""
        return self._map.to_desk(*_xy(QCursor.pos()))

    def _start_motion(self):
        """A Motion from where the pet stands, over the screens as they are now."""
        self._map = self._screens()
        self._motion = physics.Motion(self._map.desk(), window_frame(self.values["size"], False),
                                      self._map.to_desk(*self._anchor))

    def _grab(self, scruff):
        """Picked up - or caught in the air - by the mouse."""
        now = time.perf_counter()
        if self._motion is None:
            self._start_motion()
        self._motion.set_scruff(scruff)
        self._motion.grab(self._hand(), now)
        self._last_frame = now
        self._save_timer.stop()
        self.behaviour.pick_up()
        self._push_pet()
        if self._pill:
            self._pill, self._hover_lost = False, None
            self.emit("hover", {"on": False})
        self._set_clickable(True)
        self._frame_timer.start()

    def _let_go(self, now):
        """Let go: thrown at the speed the hand had, or just dropped."""
        motion = self._motion
        if motion is None or motion.mode != "held":
            return
        motion.hold(self._hand(), now)
        motion.let_go(now)
        self._last_frame = now
        self.behaviour.let_go()
        self._push_pet()

    def _fall(self):
        """Nothing under its feet (_in_mid_air): it drops from where it
        stands, and lands as a thrown pet does."""
        self._start_motion()
        self._motion.fall()
        self._last_frame = time.perf_counter()
        self._save_timer.stop()
        self.behaviour.let_go()
        self._push_pet()
        self._frame_timer.start()

    def _frame(self):
        """A frame held or in the air (FRAME_MS, only then): the pet moved
        on, the window after it, the page told of its pose."""
        motion = self._motion
        if motion is None:
            self._frame_timer.stop()
            return
        now = time.perf_counter()
        dt, self._last_frame = now - self._last_frame, now
        if motion.mode == "held":
            if _primary_button_down():
                motion.hold(self._hand(), now)
            else:
                self._let_go(now)           # let go where the page never heard it
        else:
            for name, speed in motion.step(dt):
                if name == "land":
                    self.emit("land", {"hard": speed >= physics.HARD_LANDING,
                                       "depth": round(min(1.0, speed / physics.HARD_LANDING), 3)})
                elif name == "rest":
                    self._come_to_rest(speed >= physics.HARD_LANDING)
                    return
        self._follow(motion)
        if motion.mode == "fly":
            self._set_clickable(self._pet_rect().contains(QCursor.pos()))     # so it can be caught

    def _follow(self, motion):
        """The window to the pet's feet - the room above it may go off the
        screen now - and its pose to the page, when it's changed enough to
        see."""
        w, h = window_frame(self.values["size"], True)
        ax, ay = self._feet(motion)
        self._anchor = (ax, ay)
        self._set_geometry(ax - w // 2, ay - (h - room_below(self.values["size"])))
        deg, sx, sy = motion.pose()
        last = self._sent_pose
        if (last is None or abs(deg - last[0]) >= POSE_DEG
                or abs(sx - last[1]) >= POSE_SCALE or abs(sy - last[2]) >= POSE_SCALE):
            self._sent_pose = (deg, sx, sy)
            self.emit("pose", {"deg": round(deg, 2), "sx": round(sx, 4), "sy": round(sy, 4)})

    def _come_to_rest(self, hard):
        """Landed and still: dizzy after a hard landing, then its usual life
        along this floor."""
        motion, self._motion = self._motion, None
        self._frame_timer.stop()
        self._anchor = self._feet(motion)
        self.behaviour.land(hard)
        self._settle()

    def _stop_motion(self):
        """Held or in the air when the settings change or the pet is put
        away: set down on the floor below it, and back to its usual life."""
        motion, self._motion = self._motion, None
        if motion is None:
            return
        self._frame_timer.stop()
        motion.set_down()
        self._anchor = self._feet(motion)
        self.behaviour.land(False)
        self._settle()

    def _feet(self, motion):
        """Where the pet's feet are now, as Qt places them (the motion's are in desk coordinates)."""
        x, y = self._map.to_screen(motion.x, motion.y)
        return int(round(x)), int(round(y))

    def _settle(self):
        """Where it came to rest: the window placed, the walking room that
        screen's, the page's pose let go, and the place remembered."""
        self._place()
        self._walk_from_here()
        self._sent_pose = None
        self.emit("pose", None)
        self._push_pet()
        self._save_pos()

    # ------------------------------------------------------ from the view --

    def on_click(self, _payload=None):
        self.pat()

    def on_grab(self, payload=None):
        """The press has moved (or caught it in the air): picked up, by the
        scruff the page names - {fx, fy}, fractions of the pet's box."""
        if not self._physics():
            return self.on_drag()
        payload = payload if isinstance(payload, dict) else {}
        self._grab((_fraction(payload.get("fx"), physics.SCRUFF[0]),
                    _fraction(payload.get("fy"), physics.SCRUFF[1])))

    def on_drag(self, _payload=None):
        """Physics off: the press has moved, and the system moves the window
        (moveEvent follows it) - no pointerup reaches the page."""
        handle = self.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def on_drop(self, _payload=None):
        # Heard while the system still has the button down (Chromium let go
        # of the pointer on its own), it isn't a release: the frames let go
        # when the button really comes up.
        if _system_button_down():
            return
        self._let_go(time.perf_counter())

    def on_bubble(self, payload):
        """The bubble opened or closed: while it's up, the room above the pet takes the mouse."""
        self._bubble = bool((payload or {}).get("open"))
        self._set_clickable(self._room() or self._motion is not None)

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

    def hideEvent(self, event):
        self._stop_motion()
        super().hideEvent(event)

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
        self._stop_motion()
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_pos()
        super().closeEvent(event)

