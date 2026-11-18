#!/usr/bin/env python3
"""
The desktop layout's bookkeeping (a theme with "layout": "desktop"): which
tool windows are open, where, how big, minimised or maximised, and which is
in front. core/desktop_window.py draws it; this decides it.

No Qt - tests/test_desktop_layout.py runs on plain Python. Geometry is
(x, y, width, height) in the desk's own pixels, the window's shadow
included.

Saved in the shared settings under "desktop" as
    {"windows": {tool_id: {"geom": [x, y, w, h], "open": bool,
                           "min": bool, "max": bool, "color": int}},
     "order": [tool_id, ...],          back to front
     "preset": "grid",                 the arrangement the Tile button applies
     "slots": [tool_id, ...] | None}   while the windows are arranged: which
                                       window is in which place of the preset
"""

import math

SETTINGS_KEY = "desktop"

# Room kept clear round the edge of the desk, and between tiled windows.
MARGIN = 14
# How far each new window steps down and right from the last.
CASCADE_STEP = 30
# Smallest a window can be made: a title bar and a little of the page.
MIN_WIDTH, MIN_HEIGHT = 280, 160
# The share of the desk a window opens at when the page has no opinion.
DEFAULT_SHARE = 0.72
# Title-bar colours a palette has (theme.desktop_colors "windows").
WINDOW_COLORS = 4
# A desk smaller than this hasn't been laid out yet: don't place by it.
MIN_DESK = (400, 300)
# A window dragged past the desk's edge keeps this much of its title bar on
# the desk (across, down): enough to see what it is and take hold of it again.
KEEP = (120, 40)


def clamp(geom, area, minimum=(MIN_WIDTH, MIN_HEIGHT)):
    """`geom` kept wholly on a desk of `area` (w, h), no smaller than
    `minimum` - unless the desk itself is smaller, when it fills it."""
    x, y, w, h = (int(round(v)) for v in geom)
    area_w, area_h = max(1, int(area[0])), max(1, int(area[1]))
    w = max(min(w, area_w), min(minimum[0], area_w))
    h = max(min(h, area_h), min(minimum[1], area_h))
    x = min(max(x, 0), area_w - w)
    y = min(max(y, 0), area_h - h)
    return (x, y, w, h)


def reach(geom, area, minimum=(MIN_WIDTH, MIN_HEIGHT)):
    """Where a window may be DRAGGED to: sized as clamp() sizes it, but free
    to go past the desk's left, right and bottom edges as long as KEEP of
    its title bar stays on the desk (never past the top: the title bar is
    the handle). A window as big as the desk - tiled, or just large - can
    still be moved aside; kept wholly on the desk, it couldn't move at all."""
    x, y, w, h = clamp((0, 0, geom[2], geom[3]), area, minimum)
    x, y = int(round(geom[0])), int(round(geom[1]))
    area_w, area_h = max(1, int(area[0])), max(1, int(area[1]))
    keep_x, keep_y = min(KEEP[0], w, area_w), min(KEEP[1], area_h)
    x = min(max(x, keep_x - w), area_w - keep_x)
    y = min(max(y, 0), area_h - keep_y)
    return (x, y, w, h)


def edges_at(x, y, frame, grip, corner):
    """Which edges a press at (x, y) resizes, for a window frame (left, top,
    right, bottom): within `grip` of an edge, or within `corner` of a
    corner along either edge - corners are bigger targets than the edges,
    since a corner is what people aim for."""
    left, top, right, bottom = frame
    near = {"left": x <= left + grip, "right": x >= right - grip,
            "top": y <= top + grip, "bottom": y >= bottom - grip}
    horizontal = "left" if x <= left + corner else "right" if x >= right - corner else None
    vertical = "top" if y <= top + corner else "bottom" if y >= bottom - corner else None
    on_edge = [e for e, hit in near.items() if hit]
    if horizontal and vertical and on_edge:
        return frozenset({horizontal, vertical})
    return frozenset(on_edge)


def resize(geom, edges, dx, dy, area, minimum=(MIN_WIDTH, MIN_HEIGHT)):
    """`geom` with `edges` dragged by (dx, dy). The opposite edges stay
    where they are, and a dragged edge stops at the desk's edge and at the
    minimum size - it never pushes the window along instead."""
    x, y, w, h = geom
    area_w, area_h = area
    if "right" in edges:
        w = min(max(minimum[0], w + dx), area_w - x)
    if "bottom" in edges:
        h = min(max(minimum[1], h + dy), area_h - y)
    if "left" in edges:
        right = x + w
        x = max(0, min(x + dx, right - minimum[0]))
        w = right - x
    if "top" in edges:
        bottom = y + h
        y = max(0, min(y + dy, bottom - minimum[1]))
        h = bottom - y
    return reach((x, y, w, h), area, minimum)      # a window partly off the desk stays where it is


def opening_size(area, preferred=None, minimum=(MIN_WIDTH, MIN_HEIGHT)):
    """The size a window opens at: the page's own preference when it has
    one, else DEFAULT_SHARE of the desk - never past the desk's margins."""
    room_w, room_h = max(1, area[0] - 2 * MARGIN), max(1, area[1] - 2 * MARGIN)
    if preferred and preferred[0] > 0 and preferred[1] > 0:
        w, h = preferred
    else:
        w, h = area[0] * DEFAULT_SHARE, area[1] * DEFAULT_SHARE
    w = max(min(w, room_w), min(minimum[0], room_w))
    h = max(min(h, room_h), min(minimum[1], room_h))
    return (int(w), int(h))


def cascade_position(index, size, area):
    """Where the index-th cascaded window of `size` goes: stepping down and
    right from the top-left, starting over when it would leave the desk."""
    span_x = max(1, area[0] - size[0] - 2 * MARGIN)
    span_y = max(1, area[1] - size[1] - 2 * MARGIN)
    steps = max(1, min(span_x, span_y) // CASCADE_STEP + 1)
    step = index % steps
    return (MARGIN + step * CASCADE_STEP, MARGIN + step * CASCADE_STEP)


def grid_columns(count, area):
    """How many columns a grid of `count` windows gets: an even grid when
    there is one (four windows are two by two, six three by two - never three
    and a wide one left over), and of those the one whose windows are nearest
    a comfortable shape, a little wider than tall."""
    def cost(cols):
        rows = math.ceil(count / cols)
        shape = (area[0] / cols) / max(1.0, area[1] / rows)
        return (0.0 if count % cols == 0 else 1.0) + abs(math.log(max(shape, 1e-6) / 1.25))
    side = math.isqrt(count)
    if side * side == count and 0.4 < area[0] / max(1, area[1]) < 4:
        return side         # four are two by two, nine three by three, on any desk but a sliver
    return min(range(1, count + 1), key=cost)


def tile(count, area):
    """`count` geometries filling the desk in a grid (grid_columns), the
    last row's windows stretched to use the whole width."""
    if count <= 0:
        return []
    cols = grid_columns(count, area)
    rows = math.ceil(count / cols)
    cell_h = (area[1] - MARGIN * (rows + 1)) / rows
    out = []
    for row in range(rows):
        in_row = min(cols, count - row * cols)
        cell_w = (area[0] - MARGIN * (in_row + 1)) / in_row
        for col in range(in_row):
            out.append((int(MARGIN + col * (cell_w + MARGIN)), int(MARGIN + row * (cell_h + MARGIN)),
                        int(cell_w), int(cell_h)))
    return out


# The arrangements the Tile button offers (its right-click), in the menu's
# order: (id, what it's called). Place 0 is the main window's, where there
# is one.
PRESETS = (("grid", "Grid"), ("columns", "Side by side"), ("rows", "Stacked"),
           ("main_left", "Main window on the left"), ("main_right", "Main window on the right"),
           ("main_top", "Main window on top"))
PRESET_IDS = tuple(p for p, _label in PRESETS)
MAIN_SHARE = 0.6        # how much of the desk a preset's main window takes


def _strip(count, x, y, w, h, across):
    """`count` equal cells filling (x, y, w, h), MARGIN apart - side by side (across) or stacked."""
    if count <= 0:
        return []
    out = []
    if across:
        cell = (w - MARGIN * (count - 1)) / count
        for i in range(count):
            out.append((int(x + i * (cell + MARGIN)), int(y), int(cell), int(h)))
    else:
        cell = (h - MARGIN * (count - 1)) / count
        for i in range(count):
            out.append((int(x), int(y + i * (cell + MARGIN)), int(w), int(cell)))
    return out


def cells(preset, count, area):
    """Where `count` windows go in an arrangement: geometries, place 0
    first (the main window's, in the presets that have one)."""
    if count <= 0:
        return []
    if preset not in PRESET_IDS or preset == "grid":
        return tile(count, area)
    x, y = MARGIN, MARGIN
    w, h = max(1, area[0] - 2 * MARGIN), max(1, area[1] - 2 * MARGIN)
    if preset == "columns":
        return _strip(count, x, y, w, h, True)
    if preset == "rows":
        return _strip(count, x, y, w, h, False)
    if count == 1:
        return [(x, y, int(w), int(h))]
    if preset == "main_top":
        main_h = int((h - MARGIN) * MAIN_SHARE)
        return [(x, y, int(w), main_h)] + _strip(count - 1, x, y + main_h + MARGIN, w, h - main_h - MARGIN, True)
    main_w = int((w - MARGIN) * MAIN_SHARE)
    side_w = w - main_w - MARGIN
    if preset == "main_right":
        return [(x + side_w + MARGIN, y, main_w, int(h))] + _strip(count - 1, x, y, side_w, h, False)
    return [(x, y, main_w, int(h))] + _strip(count - 1, x + main_w + MARGIN, y, side_w, h, False)


class DesktopState:
    """Every tool window's state, and the stacking order."""

    def __init__(self, windows=None, order=None, preset="grid", slots=None):
        self.windows = windows or {}
        self.order = order or []
        self.preset = preset if preset in PRESET_IDS else "grid"
        # While the windows are arranged: who is in which place. None once
        # one is moved or resized by hand, opened, closed or minimised.
        self.slots = list(slots) if slots else None
        # When each window last opened (a counter), for the taskbar's order.
        self.opened = {t: i for i, t in enumerate(self.order)}
        self._clock = len(self.order)

    # ------------------------------------------------------------ settings --
    @classmethod
    def from_dict(cls, data, known_ids):
        """Whatever the settings file holds, trusted only as far as it
        checks out: unknown tools and malformed entries are dropped."""
        windows, order = {}, []
        data = data if isinstance(data, dict) else {}
        raw = data.get("windows") if isinstance(data.get("windows"), dict) else {}
        for tool_id, entry in raw.items():
            if tool_id not in known_ids or not isinstance(entry, dict):
                continue
            geom = entry.get("geom")
            if not (isinstance(geom, (list, tuple)) and len(geom) == 4
                    and all(isinstance(v, (int, float)) for v in geom)):
                geom = None
            color = entry.get("color")
            windows[tool_id] = {
                "geom": tuple(int(v) for v in geom) if geom else None,
                "open": bool(entry.get("open")),
                "min": bool(entry.get("min")),
                "max": bool(entry.get("max")),
                "color": color if isinstance(color, int) and 0 <= color < WINDOW_COLORS else 0,
            }
        for tool_id in data.get("order") or []:
            if tool_id in windows and tool_id not in order:
                order.append(tool_id)
        order += [t for t in windows if t not in order]
        slots = data.get("slots")
        shown = sorted(t for t, w in windows.items() if w["open"] and not w["min"])
        if not (isinstance(slots, list) and sorted(str(t) for t in slots) == shown):
            slots = None                # not the windows on show any more: not arranged
        return cls(windows, order, data.get("preset"), slots)

    def to_dict(self):
        return {
            "windows": {t: {"geom": list(w["geom"]) if w["geom"] else None, "open": w["open"],
                            "min": w["min"], "max": w["max"], "color": w["color"]}
                        for t, w in self.windows.items()},
            "order": list(self.order),
            "preset": self.preset,
            "slots": list(self.slots) if self.slots else None,
        }

    # ------------------------------------------------------------- queries --
    def entry(self, tool_id):
        if tool_id not in self.windows:
            self.windows[tool_id] = {"geom": None, "open": False, "min": False, "max": False, "color": 0}
            self.order.append(tool_id)
        return self.windows[tool_id]

    def is_open(self, tool_id):
        return self.windows.get(tool_id, {}).get("open", False)

    def open_ids(self):
        """Open windows, back to front (minimised ones included)."""
        return [t for t in self.order if self.is_open(t)]

    def front(self):
        """The window in front: the topmost open one that isn't minimised."""
        for tool_id in reversed(self.order):
            w = self.windows[tool_id]
            if w["open"] and not w["min"]:
                return tool_id
        return None

    def window_state(self, tool_id):
        """"min", "open" or None - what the rail marks a tool with."""
        w = self.windows.get(tool_id)
        if not w or not w["open"]:
            return None
        return "min" if w["min"] else "open"

    # ------------------------------------------------------------- changes --
    def raise_(self, tool_id):
        self.entry(tool_id)
        self.order.remove(tool_id)
        self.order.append(tool_id)

    def open(self, tool_id):
        """Open (or bring back) a window and put it in front. True when it
        wasn't on show before."""
        w = self.entry(tool_id)
        newly = not w["open"] or w["min"]
        if not w["open"]:
            w["color"] = self._free_color(tool_id)
            self._clock += 1
            self.opened[tool_id] = self._clock
        w["open"], w["min"] = True, False
        self.raise_(tool_id)
        if newly:
            self.slots = None
        return newly

    def _free_color(self, tool_id):
        """A title colour no other open window has - its old one when that's
        free, so a tool tends to keep its colour - else the least used."""
        used = [self.windows[t]["color"] for t in self.open_ids() if t != tool_id]
        own = self.windows[tool_id]["color"]
        if own not in used:
            return own
        return min(range(WINDOW_COLORS), key=lambda c: (used.count(c), c))

    def close(self, tool_id):
        w = self.entry(tool_id)
        w["open"], w["min"], w["max"] = False, False, False
        self.slots = None

    def minimize(self, tool_id):
        self.entry(tool_id)["min"] = True
        self.slots = None

    def toggle_max(self, tool_id):
        w = self.entry(tool_id)
        w["max"] = not w["max"]
        return w["max"]

    def set_geom(self, tool_id, geom):
        """Moved or resized by hand: where it is now - and the windows are
        no longer arranged."""
        w = self.entry(tool_id)
        w["geom"] = tuple(int(v) for v in geom)
        w["max"] = False
        self.slots = None

    def place_new(self, tool_id, area, preferred=None, minimum=(MIN_WIDTH, MIN_HEIGHT)):
        """Give a window that has never been placed a spot: its opening size,
        cascaded from the windows already open."""
        size = opening_size(area, preferred, minimum)
        index = len([t for t in self.open_ids() if t != tool_id])
        x, y = cascade_position(index, size, area)
        self.entry(tool_id)["geom"] = clamp((x, y, *size), area, minimum)

    def cascade(self, area):
        """Every open window restored and stepped down from the top-left, in
        their current order."""
        ids = self.open_ids()
        size = opening_size(area, (area[0] * 0.6, area[1] * 0.7))
        for i, tool_id in enumerate(ids):
            w = self.windows[tool_id]
            w["min"] = w["max"] = False
            x, y = cascade_position(i, size, area)
            w["geom"] = clamp((x, y, *size), area)
        self.slots = None

    def tile(self, area):
        """Every open window restored and laid out in the arrangement last chosen."""
        self.arrange(self.preset, area)

    def arrange(self, preset, area):
        """Every open window restored and laid out as `preset` (PRESETS),
        which the Tile button applies from now on. The window in front takes
        place 0 - the main window's - and the rest follow in the order they
        were opened (the taskbar's)."""
        self.preset = preset if preset in PRESET_IDS else "grid"
        front = self.front()
        ids = sorted(self.open_ids(), key=lambda t: (t != front, self.opened.get(t, 0)))
        self.slots = ids
        self._lay(area)

    def _lay(self, area):
        for tool_id, geom in zip(self.slots, cells(self.preset, len(self.slots), area)):
            w = self.windows[tool_id]
            w["min"] = w["max"] = False
            w["geom"] = geom

    def reflow(self, area):
        """The desk changed size: arranged windows keep their arrangement."""
        if self.slots:
            self._lay(area)

    def slot_at(self, x, y, area):
        """The arranged window whose place holds the point (desk pixels), or None."""
        if not self.slots:
            return None
        for tool_id, (gx, gy, gw, gh) in zip(self.slots, cells(self.preset, len(self.slots), area)):
            if gx <= x < gx + gw and gy <= y < gy + gh:
                return tool_id
        return None

    def swap(self, a, b, area):
        """Two arranged windows trade places. True if they did."""
        if not self.slots or a == b or a not in self.slots or b not in self.slots:
            return False
        i, j = self.slots.index(a), self.slots.index(b)
        self.slots[i], self.slots[j] = b, a
        self._lay(area)
        return True

    def keep_only(self, visible_ids):
        """Close the windows of tools no longer in the rail."""
        for tool_id in self.open_ids():
            if tool_id not in visible_ids:
                self.close(tool_id)


# ------------------------------------------------------------- taskbar --
#
# The desktop's taskbar: pinned tools first, in the order they were pinned,
# then every other open window in the order it opened. Pins are saved in
# the shared settings under PINS_KEY.

PINS_KEY = "desktop_pins"
DEFAULT_PINS = ("manual_chat", "project_setup", "time_tracker")


def load_pins(raw, known_ids):
    """The saved pins, trusted only as far as they check out; the defaults
    for someone who has never pinned anything (an empty list is a choice)."""
    if not isinstance(raw, list):
        raw = list(DEFAULT_PINS)
    pins = []
    for tool_id in raw:
        if tool_id in known_ids and tool_id not in pins:
            pins.append(tool_id)
    return pins


def taskbar_ids(pins, state, visible_ids):
    """[(tool_id, pinned)] in taskbar order - tools out of the rail's list
    (Organize sidebar) left off."""
    pinned = [t for t in pins if t in visible_ids]
    running = [t for t in state.open_ids() if t in visible_ids and t not in pinned]
    running.sort(key=lambda t: state.opened.get(t, 0))
    return [(t, True) for t in pinned] + [(t, False) for t in running]


def task_click(state, tool_id):
    """What clicking a tool's taskbar button does: "minimize" the window in
    front (as Windows does), else "open" - open, restore or bring forward."""
    if state.window_state(tool_id) == "open" and state.front() == tool_id:
        return "minimize"
    return "open"
