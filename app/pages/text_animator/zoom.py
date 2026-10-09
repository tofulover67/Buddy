#!/usr/bin/env python3
"""
Zoom - the Animation page's Zoom tab: zooms placed along one clip, each
easing in to a framing of the clip (a point and how far in), holding, and
easing out again - or moving between framings, bouncing, stepping,
pulsing, shaking, tilting. The shapes and the keys here; zoom_resolve.py
puts them on the clip, and web/animation.js plays the very same maths over
the clip's own picture, so what's dragged is what Resolve renders.

A zoom is {"kind", "start", "end", "in", "out", "scale", "x", "y"} - seconds
from the clip's first frame (start, end), how long it takes to get in and
back out (in, out - 0 is a cut), how far in (scale, 1 = the whole frame) and
the middle of what it lands on (x, y: shares of the frame, y down) - plus
what any zoom can have: "tilt" (degrees, anticlockwise, at full zoom),
"shake" (0-1: a jolt that dies away as it lands), "blur" (motion blur
while it moves); and the kinds' own: "steps" (step: how many), "period"
(pulse: seconds a beat), "bounce" (0-1: how far past it goes before
settling), "points" (pan / follow: framings {"at" (s from the zoom's
start), "scale", "x", "y"} it moves through, in place of scale/x/y). The
kinds are starting shapes:

    punch     cuts in, holds, cuts out
    push      eases in and stays
    inout     eases in, holds, eases out
    pullout   starts in close and eases back out
    creep     a slow, gentle push over the whole clip
    crash     a sudden rush in, motion-blurred
    bounce    in past the framing and settling back to it
    step      in by steps - cut, cut
    impact    a hit: in fast, the picture shaking as it lands
    pan       from one framing to another (pan and zoom)
    follow    through several framings, one after another
    pulse     in and back on a beat, again and again
    tilt      eases in turning a little, and back

How it moves: the part of the frame on screen goes from the whole frame to
the framing, as the zoom's amount `f` goes 0 -> 1. The zoom grows evenly to
the eye (scale^f, not a straight line in size, which would rush the end of
a big zoom) and the framing's middle comes along in step, so what's shown
is a blend of the two rectangles - always inside the picture, never an
edge or black. Between two framings (pan, follow) it's the same blend of
theirs. A tilted picture shows less of itself straight: the framing's
middle is kept far enough in for the turned frame to stay inside it (and
the tilt is held down to what the zoom can cover).

No Resolve and no Qt in here: tests/test_zoom.py runs it on plain Python.
"""

import math

KINDS = {
    # label, length (s; None: the rest of the clip), in, out (s; None: the whole length), scale, ease, extras
    "punch": {"label": "Punch in", "len": 1.5, "in": 0.0, "out": 0.0, "scale": 1.3, "ease": "smooth"},
    "push": {"label": "Push in", "len": 2.5, "in": 0.6, "out": 0.0, "scale": 1.4, "ease": "smooth"},
    "inout": {"label": "Zoom in & out", "len": 2.5, "in": 0.5, "out": 0.5, "scale": 1.4, "ease": "smooth"},
    "pullout": {"label": "Pull out", "len": 1.5, "in": 0.0, "out": None, "scale": 1.5, "ease": "smooth"},
    "creep": {"label": "Slow creep", "len": None, "in": None, "out": 0.0, "scale": 1.12, "ease": "gentle"},
    "crash": {"label": "Crash zoom", "len": 1.2, "in": 0.12, "out": 0.0, "scale": 1.8, "ease": "rush", "blur": True},
    "bounce": {"label": "Bounce", "len": 1.5, "in": 0.5, "out": 0.0, "scale": 1.4, "ease": "smooth", "bounce": 0.5},
    "step": {"label": "Step zoom", "len": 2.0, "in": 0.0, "out": 0.0, "scale": 1.6, "ease": "smooth", "steps": 2},
    "impact": {"label": "Impact", "len": 1.0, "in": 0.06, "out": 0.0, "scale": 1.35, "ease": "rush", "shake": 0.6},
    "pan": {"label": "Pan & zoom", "len": 3.0, "in": 0.0, "out": 0.0, "scale": 1.3, "ease": "smooth",
            "points": [(0.0, 1.3, 0.38, 0.5), (1.0, 1.3, 0.62, 0.5)]},
    "follow": {"label": "Follow pan", "len": 4.0, "in": 0.0, "out": 0.0, "scale": 1.4, "ease": "smooth",
               "points": [(0.0, 1.4, 0.36, 0.5), (0.5, 1.4, 0.5, 0.45), (1.0, 1.4, 0.64, 0.5)]},
    "pulse": {"label": "Pulse", "len": 2.0, "in": 0.0, "out": 0.0, "scale": 1.08, "ease": "smooth", "period": 0.5},
    "tilt": {"label": "Tilt zoom", "len": 2.5, "in": 0.6, "out": 0.6, "scale": 1.3, "ease": "smooth", "tilt": 4.0},
}
ORDER = ("punch", "push", "crash", "bounce", "step", "impact", "inout", "pulse", "tilt",
         "pullout", "creep", "pan", "follow")
MOVING = ("pan", "follow")      # kinds that move through framings ("points")
MIN_SCALE, MAX_SCALE = 1.02, 4.0
MAX_TILT = 20.0                 # degrees either way
MIN_LEN = 0.1                   # seconds a zoom lasts, at least
MAX_ZOOMS = 100
MAX_POINTS = 12
SHAKE_FOR = 0.6                 # seconds a shake takes to die away
TAG = "BuddyZoom"               # tool data on the clip's zoom Transform: what's on it (stored)
_EXTRAS = ("tilt", "shake", "blur", "steps", "period", "bounce")


def _num(value, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def inside(x, scale):
    """A framing's middle (one axis) kept where the framing stays in the picture."""
    half = 0.5 / scale
    return min(max(x, half), 1 - half)


def new(kind, at, duration, room_end=None):
    """A zoom of `kind` starting at `at` (s) on a clip `duration` long, its
    length cut to fit before `room_end` (the next zoom, or the clip's end).
    None when there's no room."""
    spec = KINDS.get(kind) or KINDS["push"]
    end_room = duration if room_end is None else min(duration, room_end)
    start = max(0.0, min(at, duration))
    length = (end_room - start) if spec["len"] is None else min(spec["len"], end_room - start)
    if length < MIN_LEN:
        return None
    zin = length if spec["in"] is None else min(spec["in"], length)
    zout = length - zin if spec["out"] is None else min(spec["out"], length - zin)
    z = {"kind": kind, "start": start, "end": start + length, "in": zin, "out": zout,
         "scale": spec["scale"], "x": 0.5, "y": 0.5}
    z.update({k: spec[k] for k in _EXTRAS if k in spec})
    if "points" in spec:
        z["points"] = [{"at": share * length, "scale": s, "x": x, "y": y} for share, s, x, y in spec["points"]]
    return clean_one(z, duration)


def clean_one(z, duration):
    """One zoom made safe for a clip `duration` long, or None."""
    if not isinstance(z, dict):
        return None
    kind = z.get("kind") if z.get("kind") in KINDS else "push"
    start = min(max(_num(z.get("start"), 0.0), 0.0), max(0.0, duration - MIN_LEN))
    end = min(max(_num(z.get("end"), start + 1), start + MIN_LEN), duration)
    if end - start < MIN_LEN - 1e-9:
        return None
    length = end - start
    zin = min(max(_num(z.get("in"), 0.0), 0.0), length)
    zout = min(max(_num(z.get("out"), 0.0), 0.0), length - zin)
    scale = min(max(_num(z.get("scale"), 1.3), MIN_SCALE), MAX_SCALE)
    out = {"kind": kind, "start": round(start, 4), "end": round(end, 4), "in": round(zin, 4), "out": round(zout, 4),
           "scale": round(scale, 4), "x": round(inside(_num(z.get("x"), 0.5), scale), 5),
           "y": round(inside(_num(z.get("y"), 0.5), scale), 5)}
    tilt = min(max(_num(z.get("tilt"), 0.0), -MAX_TILT), MAX_TILT)
    if tilt:
        out["tilt"] = round(tilt, 3)
    shake = min(max(_num(z.get("shake"), 0.0), 0.0), 1.0)
    if shake:
        out["shake"] = round(shake, 3)
    if z.get("blur") is True:
        out["blur"] = True
    if kind == "step":
        out["steps"] = int(min(max(round(_num(z.get("steps"), 2)), 2), 5))
    if kind == "pulse":
        out["period"] = round(min(max(_num(z.get("period"), 0.5), 0.15), 4.0), 4)
    if kind == "bounce":
        out["bounce"] = round(min(max(_num(z.get("bounce"), 0.5), 0.0), 1.0), 3)
    if kind in MOVING:
        points = []
        for p in (z.get("points") or [])[:MAX_POINTS]:
            if not isinstance(p, dict):
                continue
            ps = min(max(_num(p.get("scale"), scale), MIN_SCALE), MAX_SCALE)
            points.append({"at": round(min(max(_num(p.get("at"), 0.0), 0.0), length), 4), "scale": round(ps, 4),
                           "x": round(inside(_num(p.get("x"), 0.5), ps), 5), "y": round(inside(_num(p.get("y"), 0.5), ps), 5)})
        points.sort(key=lambda p: p["at"])
        if len(points) < 2:
            points = [{"at": round(share * length, 4), "scale": s, "x": x, "y": y}
                      for share, s, x, y in KINDS[kind]["points"]]
        out["points"] = points
    return out


def clean(zooms, duration):
    """A clip's zooms made safe: each one cleaned, in time order, none
    overlapping the one before (a later one starts where the earlier ends,
    and is dropped if that leaves it too short)."""
    out = []
    items = [z for z in (zooms or [])[:MAX_ZOOMS] if isinstance(z, dict)]
    for z in sorted(items, key=lambda z: _num(z.get("start"), 0.0)):
        z = clean_one(z, duration)
        if z is None:
            continue
        if out and z["start"] < out[-1]["end"]:
            z = clean_one({**z, "start": out[-1]["end"]}, duration)
            # No room left after it (cleaning pulls a start back off the
            # clip's end): dropped, never overlapping.
            if z is None or z["start"] < out[-1]["end"] - 1e-9:
                continue
        out.append(z)
    return out


# ----------------------------------------------------------------- timing

def ease(u, how="smooth"):
    u = min(max(u, 0.0), 1.0)
    if how == "gentle":
        return 0.5 - 0.5 * math.cos(math.pi * u)
    if how == "rush":                   # straight off, braking hard at the end
        return 1 - (1 - u) ** 4
    return 4 * u * u * u if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def overshoot(u, strength):
    """Bounce's way in: past 1 and back, settling on 1 at u = 1."""
    u = min(max(u, 0.0), 1.0)
    c1 = 1.70158 * 2 * strength
    c3 = c1 + 1
    return 1 + c3 * (u - 1) ** 3 + c1 * (u - 1) ** 2


def beat(u):
    """Pulse's shape, over one beat: up fast, then easing back down."""
    if u < 0.12:
        return ease(u / 0.12, "smooth")
    return (1 - (u - 0.12) / 0.88) ** 2


def running(z, t, duration):
    """Whether a zoom has hold of the picture at `t` - one that runs to the
    clip's end holds it there."""
    return z["start"] <= t and (t < z["end"] or z["end"] >= duration - 1e-6)


def _held(z, since, length):
    """The amount between getting in and going out."""
    if z["kind"] == "step":
        steps = z.get("steps", 2)
        span = max(1e-6, (length - z["out"]) / steps)
        return min(1.0, (int(since / span) + 1) / steps)
    if z["kind"] == "pulse":
        period = z.get("period", 0.5)
        return beat((since % period) / period)
    return 1.0


def amount(z, t, duration):
    """How far in a zoom is at `t` (s): 0 (the whole frame) to 1 (its framing)
    - past 1 while a bounce overshoots."""
    if not running(z, t, duration):
        return 0.0
    how = KINDS.get(z["kind"], KINDS["push"])["ease"]
    since, length = t - z["start"], z["end"] - z["start"]
    if z["out"] > 0 and t >= z["end"] - z["out"]:
        level = 1.0 - ease((t - (z["end"] - z["out"])) / z["out"], how)
        return level * _held(z, max(0.0, z["end"] - z["out"] - z["start"] - 1e-6), length)
    if z["in"] > 0 and since < z["in"]:
        u = since / z["in"]
        return overshoot(u, z.get("bounce", 0.5)) if z["kind"] == "bounce" else ease(u, how)
    return _held(z, since, length)


def _shake(z, t):
    """(dx, dy, degrees) a shake moves the picture at `t`: a jolt as the zoom
    lands, dying away over SHAKE_FOR."""
    strength = z.get("shake", 0.0)
    tau = t - (z["start"] + z["in"])
    if not strength or tau < 0 or tau >= SHAKE_FOR:
        return 0.0, 0.0, 0.0
    fade = math.exp(-7 * tau) * (1 - tau / SHAKE_FOR)
    amp = 0.02 * strength * fade
    return (amp * math.sin(2 * math.pi * 13 * tau + 0.3), 0.8 * amp * math.sin(2 * math.pi * 17 * tau + 1.7),
            1.5 * strength * fade * math.sin(2 * math.pi * 11 * tau + 0.9))


# ---------------------------------------------------------------- framing

def _blend(a, b, f):
    """The framing (scale, x, y) `f` of the way from framing a to framing b
    - its size even to the eye, its middle in step, so it's always a blend of
    the two rectangles (inside the picture whenever both are)."""
    wa, wb = 1 / a[0], 1 / b[0]
    w = math.exp(math.log(wa) + (math.log(wb) - math.log(wa)) * f)
    lam = (w - wa) / (wb - wa) if abs(wb - wa) > 1e-9 else f
    return 1 / w, a[1] + (b[1] - a[1]) * lam, a[2] + (b[2] - a[2]) * lam


def target(z, t):
    """The framing (scale, x, y) a zoom is heading for at `t` - its own, or
    for pan / follow, the one along its path."""
    points = z.get("points") if z["kind"] in MOVING else None
    if not points:
        return z["scale"], z["x"], z["y"]
    since = t - z["start"]
    if since <= points[0]["at"]:
        p = points[0]
        return p["scale"], p["x"], p["y"]
    for a, b in zip(points, points[1:]):
        if since < b["at"]:
            f = ease((since - a["at"]) / max(1e-6, b["at"] - a["at"]), "smooth")
            return _blend((a["scale"], a["x"], a["y"]), (b["scale"], b["x"], b["y"]), f)
    p = points[-1]
    return p["scale"], p["x"], p["y"]


def max_turn(scale, aspect):
    """The most a picture zoomed `scale` can turn (degrees) and still cover
    the frame (aspect: width / height)."""
    a = max(aspect, 1 / aspect)
    r = math.hypot(1, a)
    if scale >= r:
        return 90.0
    return max(0.0, math.degrees(math.atan(a) - math.acos(min(1.0, scale / r))))


def place(s, qx, qy, degrees, aspect):
    """(scale, centre x, centre y, degrees) the picture is drawn at to show
    the framing (s, qx, qy) turned `degrees` - centre in shares of the frame,
    y DOWN, where the picture's middle goes. The framing's middle is held in
    so the turned frame stays inside the picture, and the turn to what the
    zoom can cover."""
    limit = max_turn(s, aspect)
    degrees = max(-limit, min(limit, degrees))
    th = math.radians(degrees)
    c, sn = math.cos(th), abs(math.sin(th))
    hx = (c + sn / aspect) / (2 * s)
    hy = (c + sn * aspect) / (2 * s)
    qx = min(max(qx, hx), 1 - hx) if hx < 0.5 else 0.5
    qy = min(max(qy, hy), 1 - hy) if hy < 0.5 else 0.5
    # The picture's middle sits so the framing's middle lands on the frame's:
    # centre = 0.5 - R(turn) * s * (q - 0.5), in pixels, y up.
    vx, vy = (qx - 0.5) * aspect, 0.5 - qy
    rx, ry = vx * math.cos(th) - vy * math.sin(th), vx * math.sin(th) + vy * math.cos(th)
    return s, 0.5 - s * rx / aspect, 0.5 + s * ry, degrees


def at(zooms, t, duration, aspect=16 / 9):
    """(scale, centre x, centre y, degrees) at `t` (s) for a clip's zooms."""
    for z in zooms:
        if running(z, t, duration):
            f = amount(z, t, duration)
            dx, dy, turn = _shake(z, t)
            if f <= 0 and not (dx or dy or turn):
                break
            s, qx, qy = _blend((1.0, 0.5, 0.5), target(z, t), f) if f > 0 else (1.0, 0.5, 0.5)
            return place(s, qx + dx, qy + dy, z.get("tilt", 0.0) * f + turn, aspect)
    return 1.0, 0.5, 0.5, 0.0


def blurred(zooms):
    """Whether any of a clip's zooms asks for motion blur."""
    return any(z.get("blur") for z in zooms)


def keys(zooms, duration, frames, rate, base=0.0, aspect=16 / 9):
    """The Transform's keys for a clip's zooms, on a comp `frames` long from
    `base`, `rate` comp frames a second: {"Size", "Center.X", "Center.Y",
    "Angle"}, motion.plan's key format, y UP (as Fusion's). A key every
    frame while it moves and one at each end of a stretch that doesn't."""
    tracks = {"Size": [], "Center.X": [], "Center.Y": [], "Angle": []}
    for k in range(max(1, int(frames))):
        s, cx, cy, turn = at(zooms, k / rate, duration, aspect)
        for name, value in (("Size", s), ("Center.X", cx), ("Center.Y", 1 - cy), ("Angle", turn)):
            tracks[name].append((k, round(value, 6)))
    out = {}
    for name, points in tracks.items():
        kept = []
        for i, (f, v) in enumerate(points):
            same_before = i > 0 and abs(points[i - 1][1] - v) < 1e-7
            same_after = i + 1 < len(points) and abs(points[i + 1][1] - v) < 1e-7
            if not (same_before and same_after):
                kept.append((f, v))
        out[name] = {base + f: {"value": v} for f, v in kept}
    return out


# ---------------------------------------------------------------- copying

PASTE_MODES = ("same", "stretch")


def adapt(zooms, from_duration, to_duration, mode="stretch"):
    """A clip's zooms for another clip `to_duration` long: at the same times
    ("same" - any past its end are cut or dropped), or stretched to its length
    ("stretch": every time, easing and framing's moment in proportion). The
    framings are shares of the picture, so they carry over as they are."""
    k = to_duration / from_duration if mode == "stretch" and from_duration > 0 else 1.0
    out = []
    for z in zooms or []:
        if not isinstance(z, dict):
            continue
        moved = dict(z)
        for key in ("start", "end", "in", "out"):
            moved[key] = _num(z.get(key), 0.0) * k
        if moved["start"] > to_duration - MIN_LEN:
            continue                    # starts after the clip has ended: not on it at all
        if mode == "stretch" and "period" in z:
            moved["period"] = _num(z.get("period"), 0.5) * k
        if isinstance(z.get("points"), list):
            moved["points"] = [{**p, "at": _num(p.get("at"), 0.0) * k} for p in z["points"] if isinstance(p, dict)]
        out.append(moved)
    return clean(out, to_duration)


# ---------------------------------------------------------------- keeping

def to_comp(zooms, rate, base):
    """Zooms with their times as comp frames (start_f ...) - how they're kept
    on the clip, so they stay on the same picture when the clip is trimmed."""
    out = []
    for z in zooms:
        kept = {"kind": z["kind"], "scale": z["scale"], "x": z["x"], "y": z["y"],
                "start_f": round(base + z["start"] * rate, 3), "end_f": round(base + z["end"] * rate, 3),
                "in_f": round(z["in"] * rate, 3), "out_f": round(z["out"] * rate, 3)}
        kept.update({k: z[k] for k in _EXTRAS if k in z})
        if "points" in z:
            kept["points"] = [{"at_f": round(p["at"] * rate, 3), "scale": p["scale"], "x": p["x"], "y": p["y"]}
                              for p in z["points"]]
        out.append(kept)
    return out


def from_comp(kept, rate, base, duration):
    """Zooms kept on a clip (to_comp) back in seconds from its first frame."""
    zooms = []
    for z in kept or []:
        if not isinstance(z, dict):
            continue
        back = {"kind": z.get("kind"), "scale": z.get("scale"), "x": z.get("x"), "y": z.get("y"),
                "start": (_num(z.get("start_f"), 0.0) - base) / rate, "end": (_num(z.get("end_f"), 0.0) - base) / rate,
                "in": _num(z.get("in_f"), 0.0) / rate, "out": _num(z.get("out_f"), 0.0) / rate}
        back.update({k: z[k] for k in _EXTRAS if k in z})
        if isinstance(z.get("points"), list):
            back["points"] = [{"at": _num(p.get("at_f"), 0.0) / rate, "scale": p.get("scale"), "x": p.get("x"),
                               "y": p.get("y")} for p in z["points"] if isinstance(p, dict)]
        zooms.append(back)
    return clean(zooms, duration)
