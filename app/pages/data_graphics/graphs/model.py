"""A graph - bars, a line or a pie - as arithmetic: the rows typed or pasted (parse_rows), what
was asked for made valid (clean), where everything sits in a frame (layout), how far
each bar has grown on every frame (growth, from each bar's timing: the graph's one
curve, or a curve, start and length of the bar's own) or how much of the line is drawn
(drawn, from the line's timing; it reaches each point at reached()) or how far round
a pie has swept (drawn too; pie_of has its slices), how every other
part arrives (arrivals) and the dock's lanes for them all (lanes) - the one clock the
preview in Buddy and the keys in Resolve are both made from. No Qt, no Resolve.

A line is drawn on by its length in pixels, as Fusion's WriteLength draws a
PolylineMask (docs/RESOLVE_API_GUIDE.md, "Lines and arcs") - so how far along it is
depends on the frame's shape, and the functions that say when it reaches a point take
the frame."""

import hashlib
import json
import math
import re

from .. import easing, figures, parts
from ..easing import DEFAULT as EASE, clean_curve, colour, ease, named, number
from ..text_look import CAP as TEXT_CAP, LOOK_DEFAULTS, clean_look

MAX_ROWS = 12
MAX_LABEL = 40
MAX_TITLE = 80
MIN_STEPS, MAX_STEPS = 2, 10
LIMIT = 1e12
MIN_SECONDS, MAX_SECONDS = 0.2, 20.0
MAX_HOLD = 60.0
CORNER = 0.5                # rounded bars: the corners' radius, as a share of half the bar's width
GROW_SHARE = 0.6            # one after another: each bar grows for this share of the whole build
PALETTE = ("#4f8cff", "#ff7a59", "#35c48d", "#ffc94d", "#b07cff", "#ff6fa5", "#3fd0e0", "#9aa7b8")
COLOUR_MODES = ("many", "one")
SHAPES = ("bars", "line", "pie")
HOLE = 0.55                 # a donut's hole, as a share of its radius
FRAME = (1920, 1080)        # the frame a line's timing is worked out for until a timeline is read
LINE_W = 1 / 180            # a line's thickness, as a share of the frame's height (6 px at 1080)
DOT = 3.0                   # a point's dot: its radius in line thicknesses (smaller, a steep line out of it looked like a lump)
DOT_GROW = 0.15             # seconds a dot takes to pop in, from when the line reaches it
DOT_FROM = 0.5              # ... from this share of its size, fading in (from nothing, it was a blob on the line)
SPOT_WIDTH = 0.6            # a character's width, about, as a share of the letter size (Arial Bold's digits: 0.56)
OUT_OF_ROOM = 1e6           # what a spot outside the room for numbers counts as, in px of line crossed
FILL_ALPHA = "40"           # the fill under a line: its colour this see-through
SAMPLES = 400               # steps a curve is read in to find when the line reaches a point
SAMPLE_ROWS = [{"label": "Mon", "value": 12.0}, {"label": "Tue", "value": 19.0}, {"label": "Wed", "value": 8.0},
               {"label": "Thu", "value": 24.0}, {"label": "Fri", "value": 16.0}]
DEFAULTS = {
    "rows": SAMPLE_ROWS, "shape": "bars", "title": "", "subtitle": "", "x_title": "", "y_title": "",
    # The value axis: numbers up the side, lines across, about how many steps, and the
    # value at its top ("" is: worked out from the tallest bar).
    "scale": False, "grid": False, "steps": 4, "top": "",
    "seconds": 2.0, "hold": 2.0, "stagger": True,
    "easing": EASE, "curve": named(EASE),
    "values": True, "labels": True, "decimals": 0, "separator": "comma", "prefix": "", "suffix": "",
    "colours": "many", "bar_colour": PALETTE[0], "rounded": False,
    "dots": True, "fill": False,                # a line's: a dot at each point, the room under it filled
    "hole": False, "percent": True,             # a pie's: a hole in the middle, its numbers as shares of the whole
    "draw_timing": None,    # the line's (or the pie's sweep's) own timing, once it's given one in the dock
    "arrivals": {},         # {part: its own arrival} for the parts in TEXT_PARTS that were given one
    **LOOK_DEFAULTS,
}


def clean_rows(raw):
    rows = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        value = number(row.get("value"), None, 0.0, LIMIT)
        if value is None:
            continue
        rows.append({"label": str(row.get("label") or "").strip()[:MAX_LABEL], "value": value})
        # What the dock gave this bar of its own: its growth's timing, its name's and its number's arrival.
        for field, kinds, kind in (("timing", None, None), ("name_in", parts.KINDS, "none"), ("value_in", VALUE_KINDS, "fade")):
            own = parts.clean_timing(row.get(field), kinds, kind)
            if own:
                rows[-1][field] = own
        picked = colour(row.get("colour"), None)        # a colour picked for this bar alone
        if picked:
            rows[-1]["colour"] = picked
        if len(rows) == MAX_ROWS:
            break
    return rows


def _top(value):
    """The top of the scale as typed: a number above 0, or "" - worked out by itself."""
    top = figures.parse(value) if isinstance(value, str) else number(value, None)
    return min(LIMIT, top) if top is not None and top > 0 else ""


def clean(raw):
    """A whole, valid graph from whatever was saved or sent."""
    raw = raw if isinstance(raw, dict) else {}
    spec = {
        "rows": clean_rows(raw.get("rows", DEFAULTS["rows"])),
        "shape": raw.get("shape") if raw.get("shape") in SHAPES else DEFAULTS["shape"],
        "title": str(raw.get("title") or "").strip()[:MAX_TITLE],
        "subtitle": str(raw.get("subtitle") or "").strip()[:MAX_TITLE],
        "x_title": str(raw.get("x_title") or "").strip()[:MAX_TITLE],
        "y_title": str(raw.get("y_title") or "").strip()[:MAX_TITLE],
        "scale": bool(raw.get("scale", DEFAULTS["scale"])),
        "grid": bool(raw.get("grid", DEFAULTS["grid"])),
        "steps": int(number(raw.get("steps"), DEFAULTS["steps"], MIN_STEPS, MAX_STEPS)),
        "top": _top(raw.get("top")),
        "seconds": number(raw.get("seconds"), DEFAULTS["seconds"], MIN_SECONDS, MAX_SECONDS),
        "hold": number(raw.get("hold"), DEFAULTS["hold"], 0.0, MAX_HOLD),
        "stagger": bool(raw.get("stagger", DEFAULTS["stagger"])),
        "values": bool(raw.get("values", DEFAULTS["values"])),
        "labels": bool(raw.get("labels", DEFAULTS["labels"])),
        "colours": raw.get("colours") if raw.get("colours") in COLOUR_MODES else DEFAULTS["colours"],
        "bar_colour": colour(raw.get("bar_colour"), DEFAULTS["bar_colour"]),
        "rounded": bool(raw.get("rounded", DEFAULTS["rounded"])),
        "dots": bool(raw.get("dots", DEFAULTS["dots"])),
        "fill": bool(raw.get("fill", DEFAULTS["fill"])),
        "hole": bool(raw.get("hole", DEFAULTS["hole"])),
        "percent": bool(raw.get("percent", DEFAULTS["percent"])),
        "draw_timing": parts.clean_timing(raw.get("draw_timing")),
    }
    fmt = figures.clean_format(raw, {**DEFAULTS, "form": "number"})
    fmt.pop("form")                     # a bar's value is always a number
    spec.update(clean_curve(raw, DEFAULTS))
    given = raw.get("arrivals") if isinstance(raw.get("arrivals"), dict) else {}
    spec["arrivals"] = {key: own for key, own in ((key, parts.clean_timing(given.get(key), kinds, "none"))
                                                  for key, kinds in TEXT_PARTS.items()) if own}
    spec.update(fmt)
    spec.update(clean_look(raw, DEFAULTS))
    return spec


_SPLIT = re.compile(r"\t|;")


def parse_rows(text):
    """Rows pasted from a spreadsheet or typed, a line each: (rows, the lines that had no
    number, how many rows past MAX_ROWS were left out). A line is "label <tab> number" - or with a semicolon or a comma between
    them, or only a number. The last number on the line is the value; negatives count
    as lines without one (a bar can't go below its floor)."""
    rows, skipped = [], []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        cells = [c.strip() for c in _SPLIT.split(line)] if _SPLIT.search(line) else _comma_cells(line)
        value, at = None, None
        for i in range(len(cells) - 1, -1, -1):
            value = figures.parse(cells[i]) if re.search(r"\d", cells[i]) and not re.search(r"[A-Za-z]{2,}", cells[i]) else None
            if value is not None:
                at = i
                break
        if value is None or value < 0 or value > LIMIT:
            skipped.append(line[:MAX_LABEL])
            continue
        label = " ".join(c for c in cells[:at] if c)
        rows.append({"label": label[:MAX_LABEL], "value": value})
    return rows[:MAX_ROWS], skipped, max(0, len(rows) - MAX_ROWS)


def _comma_cells(line):
    """"Apples, 1,234" -> ["Apples", "1,234"]: the label is up to the first comma that
    isn't inside a number; a line that is only a number stays whole."""
    if figures.parse(line) is not None and not re.search(r"[^\d\s.,%$€£¥+\-−]", line):
        return [line]
    head, mark, tail = line.partition(",")
    while mark and re.search(r"\d$", head) and re.match(r"\d", tail) and not re.search(r"[A-Za-z]", head):
        more, mark, tail = tail.partition(",")
        head += "," + more
    if not mark:
        # No comma to split on: "Apples 12" - the last word is the number.
        left, _space, right = line.rpartition(" ")
        return [left, right] if left else [line]
    return [head.strip(), tail.strip()]


def colours(spec):
    """Each bar's (or point's dot's) colour: the one picked for it, else the graph's - the
    one colour for all, or the palette's next. A line is one colour, so its dots take it."""
    one = spec["colours"] == "one" or spec["shape"] == "line"
    return [row.get("colour") or (spec["bar_colour"] if one else PALETTE[i % len(PALETTE)])
            for i, row in enumerate(spec["rows"])]


def scale_of(spec, peak):
    """The value axis: (the value at the top of the bars' room, the values its marks sit
    on, the decimals they're written with). With no numbers up the side, no lines across
    and no top typed, the tallest bar simply fills the room and there are no marks."""
    steps = spec["steps"]
    typed = spec["top"] if spec["top"] and spec["top"] >= peak else None
    if typed is None and not (spec["scale"] or spec["grid"]):
        return peak, [], spec["decimals"]
    if typed is not None:
        step, count = typed / steps, steps
    else:
        # A round step - 1, 2 or 5 times a power of ten - about `steps` of which reach the tallest bar.
        raw = (peak if peak > 0 else 1.0) / steps
        size = 10 ** math.floor(math.log10(raw))
        step = next(m * size for m in (1, 2, 5, 10) if m * size >= raw * (1 - 1e-9))
        count = max(1, math.ceil(peak / step - 1e-9))
    needed = next((k for k in range(figures.MAX_DECIMALS + 1) if abs(round(step, k) - step) < 1e-9), figures.MAX_DECIMALS)
    return step * count, [step * i for i in range(count + 1)], max(spec["decimals"], needed)


def layout(spec, frame):
    """Where everything sits in a frame, px (y down): the bars at full height, the
    labels under them, the values over them, the title and subtitle, the floor line -
    and, when asked for, the value axis (its marks' numbers up the side and lines
    across) and a title along each axis. A pie ("pie") has no axes: its titles sit over
    it and the disc fills the rest (pie_of)."""
    fw, fh = frame
    if spec["shape"] == "pie":
        spec = {**spec, "scale": False, "grid": False, "x_title": "", "y_title": ""}
    rows = spec["rows"]
    n = max(1, len(rows))
    peak = max([r["value"] for r in rows] + [0.0])
    top_value, marks, mark_decimals = scale_of(spec, peak)
    mark_texts = [figures.shown(v, {**spec, "decimals": mark_decimals}) for v in marks]

    left, right = 0.10 * fw, 0.90 * fw
    side = None
    if spec["y_title"]:
        size = min(0.04 * fh, 0.7 * fh / (0.55 * max(8, len(spec["y_title"]))))
        side = {"text": spec["y_title"], "size": size, "x": 0.035 * fw + size}      # x: its baseline, facing the bars
        left = side["x"] + size * 0.9
    tick = None
    if spec["scale"] and marks:
        size = 0.03 * fh
        left = max(left, 0.05 * fw) + 0.6 * size * max(len(t) for t in mark_texts) + 0.012 * fw
        tick = {"size": size, "x": left - 0.012 * fw, "drop": TEXT_CAP * size / 2}  # x: where the numbers end
    left = min(left, 0.35 * fw)

    slot = (right - left) / n
    longest = max([len(r["label"]) for r in rows] + [1])
    text = max(0.018 * fh, min(0.042 * fh, slot / (0.62 * longest)))      # labels fit their slots, about
    shown = [figures.shown(r["value"], spec) for r in rows]
    value_size = max(0.018 * fh, min(0.046 * fh, slot / (0.62 * max([len(s) for s in shown] + [1]))))
    middle = (left + right) / 2
    title = subtitle = None
    top = 0.12 * fh
    if spec["title"]:
        size = min(0.07 * fh, (right - left) / (0.55 * max(8, len(spec["title"]))))
        title = {"text": spec["title"], "size": size, "x": middle, "baseline": top + size * 0.75}
        top += size * 1.6
    if spec["subtitle"]:
        size = min(0.04 * fh, (right - left) / (0.55 * max(8, len(spec["subtitle"]))))
        if title:
            top -= title["size"] * 0.35
        subtitle = {"text": spec["subtitle"], "size": size, "x": middle, "baseline": top + size * 0.75}
        top += size * 1.7
    base = (0.84 if spec["labels"] else 0.90) * fh
    below = None
    if spec["x_title"]:
        size = min(0.04 * fh, (right - left) / (0.55 * max(8, len(spec["x_title"]))))
        base -= size * 1.2
        under = text * 1.85 if spec["labels"] else size * 0.3
        below = {"text": spec["x_title"], "size": size, "x": middle, "baseline": base + under + size}
    room = value_size * 1.7 if spec["values"] else 0.0
    line_w = LINE_W * fh
    if spec["shape"] == "line":
        room += line_w * DOT if spec["dots"] else line_w / 2       # a number sits clear of its point's dot
    tallest = max(1.0, base - top - room)
    width = slot * 0.62
    bars = [{"cx": left + slot * (i + 0.5), "w": width, "base": base,
             "h": tallest * (r["value"] / top_value) if top_value > 0 else 0.0,
             "colour": c, "label": r["label"], "value": r["value"], "shown": s}
            for i, (r, c, s) in enumerate(zip(rows, colours(spec), shown))]
    if side:
        side["y"] = base - tallest / 2                                      # the middle of the bars' room
    line = None
    if spec["shape"] == "line":
        lift = line_w * DOT if spec["dots"] else line_w / 2
        line = {"w": line_w, "dot": line_w * DOT if spec["dots"] else 0.0, "colour": spec["bar_colour"],
                "fill": spec["bar_colour"] + FILL_ALPHA if spec["fill"] else None, "lift": lift}
        if spec["values"]:
            line["spots"] = value_spots([(b["cx"], b["base"] - b["h"]) for b in bars], [b["shown"] for b in bars],
                                        value_size, value_size * 0.45 + lift, line_w,
                                        (0.02 * fh, base - max(2.0, fh / 540), 0.0, fw))
    return {"frame": [fw, fh], "bars": bars, "title": title, "subtitle": subtitle,
            "label": {"size": text, "baseline": base + text * 1.45} if spec["labels"] else None,
            "value": {"size": value_size, "gap": value_size * 0.45} if spec["values"] else None,
            "axis": {"x0": left, "x1": right, "y": base, "h": max(2.0, fh / 540)},
            "marks": [{"y": base - tallest * (v / top_value) if top_value > 0 else base, "text": t}
                      for v, t in zip(marks, mark_texts)],
            "tick": tick, "grid": bool(spec["grid"] and marks), "x_title": below, "y_title": side,
            "radius": CORNER * width / 2 if spec["rounded"] else 0.0, "line": line,
            "pie": pie_of(spec, frame, top) if spec["shape"] == "pie" else None}


# -------------------------------------------------------------- the pie --

def pie_of(spec, frame, top):
    """A pie's disc and slices, px (y down): {"cx", "cy", "r", "inner" (the hole's
    radius, 0 for a pie), "slices": [{"row", "a", "b" (where it starts and ends, shares of the
    way round clockwise from 12 o'clock), "colour", "label", "shown", "value_at" (x,
    baseline), "label_at" (x, baseline), "align"}]}. Slices of 0 are left out; with
    nothing to share there are none."""
    fw, fh = frame
    rows = spec["rows"]
    total = sum(r["value"] for r in rows)
    bottom = 0.93 * fh
    size = 0.034 * fh
    room = size * 2.2 if spec["labels"] else size * 0.4
    r = max(1.0, min((bottom - top) / 2 - room, 0.24 * fw))
    cx, cy = fw / 2, (top + bottom) / 2
    inner = r * HOLE if spec["hole"] else 0.0
    slices, a = [], 0.0
    for i, (row, c) in enumerate(zip(rows, colours(spec))):
        if total <= 0 or row["value"] <= 0:
            continue
        share = row["value"] / total
        b = min(1.0, a + share)
        mid = (a + b) / 2 * 2 * math.pi
        sx, sy = math.sin(mid), -math.cos(mid)                 # towards the slice's middle, y down
        shown = (figures.shown(share * 100, {**spec, "prefix": "", "suffix": "%"}) if spec["percent"]
                 else figures.shown(row["value"], spec))
        middle = (r + inner) / 2 if inner else r * 0.62
        out = r + size * 0.6
        slices.append({"row": i, "a": round(a, 6), "b": round(b, 6), "colour": c, "label": row["label"], "shown": shown,
                       "value_at": (cx + sx * middle, cy + sy * middle + TEXT_CAP * size / 2),
                       "label_at": (cx + sx * out, cy + sy * out + TEXT_CAP * size / 2 + max(0.0, sy) * size * 0.6),
                       "align": "left" if sx > 0.2 else "right" if sx < -0.2 else "center"})
        a = b
    return {"cx": cx, "cy": cy, "r": r, "inner": inner, "size": size, "slices": slices}


# ------------------------------------------------------------- the line --

def _crossing(p0, p1, box):
    """How much of the line from p0 to p1 lies inside box (x0, y0, x1, y1), px."""
    (ax, ay), (bx, by) = p0, p1
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - box[0]), (dx, box[2] - ax), (-dy, ay - box[1]), (dy, box[3] - ay)):
        if p == 0:
            if q < 0:
                return 0.0
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    return max(0.0, t1 - t0) * math.hypot(dx, dy)


def value_spots(pts, texts, size, gap, line_w, bounds):
    """Where each point's number goes: (x, baseline), the text centred on x. Over its
    point, unless the line runs through it there - then under it, or beside it, wherever
    the line crosses it least (in that order when it's a tie). gap: from the point to the
    text; bounds: (top, bottom, left, right) the text must stay within, px."""
    top, bottom, left, right = bounds
    lines = list(zip(pts, pts[1:]))
    cap = TEXT_CAP * size
    pad = size * 0.15 + line_w / 2
    out = []
    for (x, y), text in zip(pts, texts):
        w = SPOT_WIDTH * size * max(1, len(text))
        beside = gap + w / 2
        best = score = None
        for sx, base in ((x, y - gap), (x, y + gap + cap), (x + beside, y + cap / 2), (x - beside, y + cap / 2)):
            box = (sx - w / 2 - pad, base - cap - pad, sx + w / 2 + pad, base + pad)
            crossed = sum(_crossing(a, b, box) for a, b in lines)
            if box[1] < top or box[3] > bottom or box[0] < left or box[2] > right:
                crossed += OUT_OF_ROOM
            if best is None or crossed < score - 1e-6:
                best, score = (round(sx, 3), round(base, 3)), crossed
        out.append(best)
    return out


def points(plan):
    """A line's points, px (y down): over each slot's middle, as high as its bar would be."""
    return [(b["cx"], b["base"] - b["h"]) for b in plan["bars"]]


def along(pts):
    """How far along the line each point is, as a share of its whole length in pixels
    (0 for the first; all 0 when it has no length)."""
    run, out = 0.0, [0.0]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        run += math.hypot(x1 - x0, y1 - y0)
        out.append(run)
    return [d / run if run > 0 else 0.0 for d in out]


def head(pts, share):
    """Where the line's drawn end is when `share` of it is drawn: (x, y) px."""
    if not pts:
        return 0.0, 0.0
    shares = along(pts)
    for (a, p0), (b, p1) in zip(zip(shares, pts), zip(shares[1:], pts[1:])):
        if share <= b and b > a:
            t = max(0.0, (share - a) / (b - a))
            return p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t
    return pts[-1]


def draw_timing(spec):
    """The line's (or the pie's sweep's) timing as it plays: the graph's curve over the
    whole build, or its own from the dock (kept within the build) - with "own"."""
    build, own = spec["seconds"], spec["draw_timing"]
    if own:
        begin = min(own["start"], build - parts.MIN_LENGTH)
        return {**own, "start": round(begin, 4), "length": round(min(own["length"], build - begin), 4), "own": True}
    return {"easing": spec["easing"], "curve": spec["curve"], "start": 0.0, "length": build, "own": False}


def drawn(spec, fps, total=None):
    """How much of the line is drawn, 0..1 of its length (or how far round the pie has
    swept), on each frame of a clip of `total` frames. The build's last frame is exactly
    its end; a curve that goes past can't draw more than the whole."""
    build = build_frames(spec, fps)
    total = frames(spec, fps) if total is None else max(1, int(total))
    t = draw_timing(spec)
    return [round(easing.clamp(ease(t["curve"], (min(1.0, f / (build - 1)) * spec["seconds"] - t["start"]) / t["length"])), 6)
            for f in range(total)]


def _shares(spec, frame):
    """How far through the drawing each row's part is reached: its point along the line,
    or the middle of its slice round the pie (where its slice starts, for a slice of 0)."""
    if spec["shape"] != "pie":
        return along(points(layout(spec, frame)))
    total, a, out = sum(r["value"] for r in spec["rows"]), 0.0, []
    for row in spec["rows"]:
        share = row["value"] / total if total > 0 else 0.0
        out.append(min(1.0, a + share / 2))
        a += share
    return out


def reached(spec, frame=FRAME):
    """The second the line reaches each point, or the pie's sweep the middle of each
    slice (its first crossing, for a curve that goes past and back)."""
    t = draw_timing(spec)
    out = []
    for share in _shares(spec, frame):
        u = next((k / SAMPLES for k in range(SAMPLES + 1) if ease(t["curve"], k / SAMPLES) >= share - 1e-9), 1.0)
        out.append(round(t["start"] + u * t["length"], 4))
    return out


def dot_states(spec, fps, total=None, frame=FRAME):
    """Each dot on each frame, [[(its share of its full size, its opacity), ...] per dot]:
    nothing until the line reaches it, then over DOT_GROW seconds it pops in - fading in
    as it grows from DOT_FROM of its size."""
    total = frames(spec, fps) if total is None else max(1, int(total))
    # The line's own clock (drawn's), run on past the build so the last dot still pops in.
    per_frame = spec["seconds"] / (build_frames(spec, fps) - 1)
    out = []
    for at in reached(spec, frame):
        states = []
        for f in range(total):
            p = easing.clamp((f * per_frame - at) / DOT_GROW)
            states.append((round(DOT_FROM + (1 - DOT_FROM) * p, 6) if p > 0 else 0.0, round(p, 6)))
        out.append(states)
    return out


def build_frames(spec, fps):
    """The frames the bars take to grow - the last one has every bar at its full height."""
    return max(2, int(round(spec["seconds"] * fps)))


def frames(spec, fps):
    """The whole clip's frames: the build, then the hold on the finished graph."""
    return build_frames(spec, fps) + int(round(spec["hold"] * fps))


def spans(spec):
    """Each bar's (start, length) as shares of the build: together, or one after another."""
    n = len(spec["rows"])
    if not spec["stagger"] or n < 2:
        return [(0.0, 1.0)] * n
    step = (1.0 - GROW_SHARE) / (n - 1)
    return [(i * step, GROW_SHARE) for i in range(n)]


def timings(spec):
    """Each bar's timing as it plays: {"start", "length" (seconds, within the build),
    "easing", "curve", "own"}. The graph's one curve and its spans - unless the bar was
    given a timing of its own in the dock ("own")."""
    build, out = spec["seconds"], []
    for row, (start, length) in zip(spec["rows"], spans(spec)):
        own = row.get("timing")
        if own:
            begin = min(own["start"], build - parts.MIN_LENGTH)
            out.append({**own, "start": round(begin, 4), "length": round(min(own["length"], build - begin), 4), "own": True})
        else:
            out.append({"easing": spec["easing"], "curve": spec["curve"], "start": round(start * build, 4),
                        "length": round(length * build, 4), "own": False})
    return out


def growth(spec, fps, total=None):
    """How far each bar has grown, 0..1, on each frame of a clip of `total` frames:
    [[bar 1's frames], [bar 2's], ...]. The build's last frame is exactly its end."""
    build = build_frames(spec, fps)
    total = frames(spec, fps) if total is None else max(1, int(total))
    if spec["shape"] != "bars":
        return [[1.0] * total for _row in spec["rows"]]     # a line's points (a pie's slices) are drawn on instead
    out = []
    for t in timings(spec):
        # A curve that dips below its start can't make a bar shorter than nothing.
        out.append([round(max(0.0, ease(t["curve"], (min(1.0, f / (build - 1)) * spec["seconds"] - t["start"]) / t["length"])), 6)
                    for f in range(total)])
    return out


# The parts that arrive (already there, or fading, rising or popping in), each with a
# start, a length and a curve of its own once it's given one in the dock: {id: the ways
# it may arrive}. The lines across only fade; a bar's number always fades in - by
# itself, as its bar starts to grow.
TEXT_PARTS = {"title": parts.KINDS, "subtitle": parts.KINDS, "y_title": parts.KINDS, "x_title": parts.KINDS,
              "scale": parts.KINDS, "grid": ("none", "fade")}
LANE_NAMES = {"title": "Title", "subtitle": "Subtitle", "y_title": "Up the side", "x_title": "Along the bottom",
              "scale": "Numbers up the side", "grid": "Lines across"}
VALUE_KINDS = ("fade",)
VALUE_FADE = 0.15
THERE = {"kind": "none", "start": 0.0, "length": parts.ARRIVES_IN, "easing": EASE, "curve": named(EASE)}


def _shown(spec):
    """Which of the TEXT_PARTS the graph has (a pie has no axes)."""
    return [key for key in TEXT_PARTS
            if (spec[key] if key in ("scale", "grid") else bool(spec[key]))
            and not (spec["shape"] == "pie" and key not in ("title", "subtitle"))]


def _starts(spec, frame):
    """When each bar starts to grow - or when the line reaches each point, or the pie's
    sweep each slice's middle."""
    return reached(spec, frame) if spec["shape"] != "bars" else [t["start"] for t in timings(spec)]


def arrivals(spec, frame=FRAME):
    """{part id: its timing as it plays, with "own"} for every part of the graph that
    arrives: the titles, the scale, the lines, and each bar's (or point's) name
    ("name:0") and number ("value:0") - a number fades in by itself as its bar starts
    to grow, or as the line reaches its point."""
    out = {}
    for key in _shown(spec):
        own = spec["arrivals"].get(key)
        out[key] = {**(own or THERE), "own": bool(own)}
    for i, (row, start) in enumerate(zip(spec["rows"], _starts(spec, frame))):
        auto = {"kind": "fade", "start": start, "length": VALUE_FADE, "easing": "linear", "curve": named("linear")}
        if spec["labels"] and row["label"]:
            # Names under bars and points are like an axis, there from the start; a slice's
            # name would sit beside nothing, so it fades in with its slice.
            name = auto if spec["shape"] == "pie" else THERE
            out[f"name:{i}"] = {**(row.get("name_in") or name), "own": bool(row.get("name_in"))}
        if spec["values"]:
            out[f"value:{i}"] = {**(row.get("value_in") or auto), "own": bool(row.get("value_in"))}
    return out


def marks_middle(plan):
    """The middle of the numbers up the side, px: what they scale about as they arrive."""
    ys = [m["y"] for m in plan["marks"]]
    return plan["tick"]["x"], (min(ys) + max(ys)) / 2


def lanes(spec, frame=FRAME):
    """The dock's lanes, in the order they're read: the titles, the axis, then each bar
    with its name and its number under it - or the line (the pie's sweep: "draw"), then
    each point's (slice's) name and number."""
    arriving, out = arrivals(spec, frame), []
    line = spec["shape"] != "bars"
    for key in _shown(spec):
        out.append(parts.lane(key, LANE_NAMES[key], arriving[key], TEXT_PARTS[key], arriving[key]["own"]))
    if line:
        drawing = draw_timing(spec)
        out.append(parts.lane("draw", "Pie" if spec["shape"] == "pie" else "Line", drawing, None, drawing["own"]))
    for i, (row, bar) in enumerate(zip(spec["rows"], timings(spec))):
        name = row["label"] or str(i + 1)
        if not line:
            out.append(parts.lane(f"bar:{i}", name, bar, None, bar["own"], plain=True))
        for part, sub, kinds in ((f"name:{i}", "name", parts.KINDS), (f"value:{i}", "number", VALUE_KINDS)):
            if part in arriving:
                out.append(parts.lane(part, name, arriving[part], kinds, arriving[part]["own"], sub=sub, plain=True))
    return out


_ROW_FIELDS = {"bar": "timing", "name": "name_in", "value": "value_in"}


def with_part(spec, part, key, value, frame=FRAME):
    """The graph with one thing about a part's timing changed in the dock - or, key
    "reset", the part following the graph again. None for a part or key there isn't.
    A part's first change starts from the timing it was playing."""
    if key != "reset" and key not in parts.KEYS:
        return None
    kind, _colon, index = str(part).partition(":")
    line = spec["shape"] != "bars"
    raw = {**spec, "rows": [dict(row) for row in spec["rows"]], "arrivals": dict(spec["arrivals"])}
    if kind in TEXT_PARTS and not index:
        playing, store, field = arrivals(spec, frame).get(kind), raw["arrivals"], kind
    elif kind == "draw" and not index and line:
        playing, store, field = dict(draw_timing(spec)), raw, "draw_timing"
    elif kind in _ROW_FIELDS and index.isdigit() and int(index) < len(spec["rows"]) and not (line and kind == "bar"):
        i = int(index)
        playing = dict(timings(spec)[i]) if kind == "bar" else arrivals(spec, frame).get(part)
        store, field = raw["rows"][i], _ROW_FIELDS[kind]
    else:
        return None
    if playing is None or (key == "kind" and kind in ("bar", "draw")):
        return None
    if key == "reset":
        store.pop(field, None)
    else:
        store[field] = parts.changed(playing, key, value)
    return clean(raw)


def value_texts(spec, grown):
    """What each bar's value says on each frame as it grows: [[bar 1's], ...]."""
    return [[figures.shown(row["value"] * p, spec) for p in bar] for row, bar in zip(spec["rows"], grown)]


def graph_id(spec, frame, fps):
    raw = json.dumps({"spec": spec, "frame": list(frame), "fps": float(fps)}, sort_keys=True)
    return "bg-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
