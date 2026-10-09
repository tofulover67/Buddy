"""The one clock - when each word of a title comes in and goes out, and how. The
preview draws from it and the Text+ keyframes are sampled from it frame by frame, so
what plays in Buddy is what plays in Resolve; the clip's length comes from it too.

    timing(doc, layout, duration) -> {"words": [...], "intro": s, "exit": s}
    state(word, t) -> {"alpha", "scale", "dx", "dy", "rot", "blur", "spread", "pulse"}

Each entrance is a set of curves over its progress p (0..1) that starts unseen and lands
exactly at rest - the settled frame is what's held. A curve is Bezier keys (CURVES, or
the person's own: doc "curves" for the title, over.curves for a word), one per channel:
alpha, scale, move (how far from its place the word still is, a share of the frame's
height, against its direction), blur (a share of the letter size) and rot (degrees).
dx and dy are shares of the frame's height (y down), so a move is the same on any
timeline. A direction is the way the word MOVES: "u" comes up from below. The Titles
tab's page plays the same curves (web/titles_motion.js); tests keep the two equal.

Starting points from motion design at 30 fps, tuned by eye:
  pop    8 frames  scale 0.6 -> 1.08 -> 1, opacity in over the first third
  slam   6 frames  scale 2.1 -> 0.94 -> 1, fast opacity, motion blur at the start
  slide  9 frames  from ~15% of the frame height away, ease-out cubic, blur fading
  blur   9 frames  heavy blur -> sharp with a small drift, ease-out cubic
  type   1 frame   appears on its time
  fade   8 frames  opacity only

A word's copies (echo, extrude, long shadow) can move too: with "anim": "spread" in a
copies group they wait behind the word until it's half in, then fan out to their places
one after another, nearest first, and fold back in as the word leaves. "spread" is how
far that has got (0 all behind the word, 1 out); copy_at() gives each copy's share of
its distance and opacity from it.
  spread 9 frames a copy, the next 2 frames later (a group's "travel" / "lag", seconds)
With the title's "pulse" on, the copies then breathe while the word is held: in toward
it a little and dimmer, and out again ("pulse" 0..1), slow whole breaths fitted so the
last ends just as the exit (or the title) does - nothing jumps.
  pulse  ~1.6 s a breath, 15% of the way in, 30% dimmer (a group's "pulseIn" / "pulseDim")
"""

import math

from . import kinetic
from . import looks as L

KINDS = ("pop", "slam", "slide", "blur", "type", "fade")
FRAMES_AT_30 = {"pop": 8, "slam": 6, "slide": 9, "blur": 9, "type": 1, "fade": 8}
DIRECTIONS = {"u": (0, -1), "d": (0, 1), "l": (-1, 0), "r": (1, 0),
              "ul": (-0.7071, -0.7071), "ur": (0.7071, -0.7071), "dl": (-0.7071, 0.7071), "dr": (0.7071, 0.7071)}
OPPOSITE = {"u": "d", "d": "u", "l": "r", "r": "l", "ul": "dr", "dr": "ul", "ur": "dl", "dl": "ur"}
SLIDE = 0.148               # slide distance: 160 px of a 1080 frame
KEY_BEAT = 2 / 30           # the key word lands this much after its turn - a beat
LINE_STEP = 3.0             # plays by line: lines this many word-staggers apart
MIN_SCALE = 0.001           # never 0: a Text+ at scale 0 can render the frame black
COPY_TRAVEL = 9 / 30       # one copy's way out
COPY_LAG = 2 / 30          # the next copy leaves this much later
COPY_START = 0.5           # the copies leave once the word's entrance is this far in
PULSE_PERIOD = 1.6         # one breath of a held word's copies, about
PULSE_MIN = 0.8            # a hold shorter than this doesn't pulse
PULSE_IN = 0.15            # how far in a breath takes the copies (share of their distance)
PULSE_DIM = 0.3            # and how much dimmer
REST = {"alpha": 1.0, "scale": 1.0, "dx": 0.0, "dy": 0.0, "rot": 0.0, "blur": 0.0, "spread": 1.0, "pulse": 0.0}


def _clamp(p):
    return 0.0 if p <= 0 else 1.0 if p >= 1 else p


def ease_out(p):
    return 1 - (1 - p) ** 3


def ease_in(p):
    return p ** 3


def ease_in_out(p):
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


# ------------------------------------------------------------- curves --
# A key: {"t": progress, "v": value, "hi": [dt, dv], "ho": [dt, dv]} - its handles, in
# and out, relative to it - and "step": hold the value until the next key. Between two
# keys the curve is the cubic Bezier through them and their handles; with the handles
# a third of the way along in time it's a plain cubic of the progress, which is how
# every built-in entrance below is made: exactly its easing, nothing fitted.

CHANNELS = ("alpha", "scale", "move", "blur", "rot")
CHANNEL_REST = {"alpha": 1.0, "scale": 1.0, "move": 0.0, "blur": 0.0, "rot": 0.0}
MAX_KEYS = 16               # a channel's keys, at most
_SHAPES = {                 # a segment's easing: the Bernstein weights of its two middle points
    "lin": (1 / 3, 2 / 3), "in": (0.0, 0.0), "out": (1.0, 1.0),
}


def _keys(first, *segments):
    """Keys from a start (t, v) and segments (shape, t, v): "lin", "in" (cubic ease-in),
    "out" (cubic ease-out) or "step" (hold, then jump)."""
    keys = [{"t": float(first[0]), "v": float(first[1]), "hi": [0.0, 0.0], "ho": [0.0, 0.0]}]
    for shape, t, v in segments:
        a = keys[-1]
        b = {"t": float(t), "v": float(v), "hi": [0.0, 0.0], "ho": [0.0, 0.0]}
        if shape == "step":
            a["step"] = True
        else:
            w1, w2 = _SHAPES[shape]
            third = (b["t"] - a["t"]) / 3
            a["ho"] = [third, (b["v"] - a["v"]) * w1]
            b["hi"] = [-third, (b["v"] - a["v"]) * (w2 - 1)]
        keys.append(b)
    return keys


def _ease_in_out(a, b, t0=0.0, t1=1.0):
    """ease_in_out as two segments: a cubic ease-in to halfway, then a cubic ease-out."""
    return [("in", (t0 + t1) / 2, (a + b) / 2), ("out", t1, b)]


CURVES = {kind: {"ch": ch} for kind, ch in {
    "pop": {"alpha": _keys((0, 0), ("lin", 0.33, 1), ("lin", 1, 1)),
            "scale": _keys((0, 0.6), ("out", 0.6, 1.08), *_ease_in_out(1.08, 1.0, 0.6, 1.0))},
    "slam": {"alpha": _keys((0, 0), ("lin", 0.25, 1), ("lin", 1, 1)),
             "scale": _keys((0, 2.1), ("in", 0.65, 0.94), ("out", 1, 1)),
             "blur": _keys((0, 0.12), ("lin", 0.5, 0), ("lin", 1, 0))},
    "slide": {"alpha": _keys((0, 0), ("lin", 0.4, 1), ("lin", 1, 1)),
              "move": _keys((0, SLIDE), ("out", 1, 0)),
              "blur": _keys((0, 0.06), ("out", 1, 0))},
    "blur": {"alpha": _keys((0, 0), ("lin", 0.5, 1), ("lin", 1, 1)),
             "move": _keys((0, 0.02), ("out", 1, 0)),
             "blur": _keys((0, 0.35), ("out", 1, 0))},
    "type": {"alpha": _keys((0, 0), ("step", 1, 1))},
    "fade": {"alpha": _keys((0, 0), *_ease_in_out(0.0, 1.0))},
}.items()}


def _bezier(p0, p1, p2, p3, s):
    u = 1 - s
    return u * u * u * p0 + 3 * u * u * s * p1 + 3 * u * s * s * p2 + s * s * s * p3


def eval_keys(keys, t):
    """A channel's value at progress t. Handles a third of the way along in time make
    the progress the Bezier's own parameter; otherwise it's found by bisection (a key's
    handles never reach past its neighbours, so time only goes forward)."""
    if not keys:
        return 0.0
    if t <= keys[0]["t"]:
        return keys[0]["v"]
    if t >= keys[-1]["t"]:
        return keys[-1]["v"]
    i = 0
    while t > keys[i + 1]["t"]:
        i += 1
    a, b = keys[i], keys[i + 1]
    if a.get("step"):
        return a["v"]
    t0, t1 = a["t"], b["t"]
    x1, x2 = t0 + a["ho"][0], t1 + b["hi"][0]
    span = t1 - t0
    if abs(x1 - (t0 + span / 3)) < 1e-9 and abs(x2 - (t1 - span / 3)) < 1e-9:
        s = (t - t0) / span
    else:
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if _bezier(t0, x1, x2, t1, mid) < t:
                lo = mid
            else:
                hi = mid
        s = (lo + hi) / 2
    return _bezier(a["v"], a["v"] + a["ho"][1], b["v"] + b["hi"][1], b["v"], s)


def curve_of(kind):
    """A built-in entrance's curves (pop's if there's no such kind)."""
    return CURVES.get(kind) or CURVES["pop"]


def apply_curve(curve, p, direction="u"):
    """The word's state at progress p (0..1) of an entrance drawn by curve."""
    p = _clamp(p)
    ch = (curve or {}).get("ch") or {}
    v = {name: eval_keys(ch[name], p) if ch.get(name) else CHANNEL_REST[name] for name in CHANNELS}
    s = dict(REST)
    s["alpha"] = min(1.0, max(0.0, v["alpha"]))
    s["scale"] = max(v["scale"], MIN_SCALE)
    if v["move"]:
        vx, vy = DIRECTIONS.get(direction, DIRECTIONS["u"])
        s["dx"], s["dy"] = -vx * v["move"], -vy * v["move"]
    s["blur"] = max(0.0, v["blur"])
    s["rot"] = v["rot"]
    return s


def entrance(kind, p, direction="u"):
    """The word's state at progress p (0..1) of a built-in entrance."""
    return apply_curve(curve_of(kind), p, direction)


def clean_curve(curve):
    """A curve set the person drew, made safe - or None. {"len": seconds (optional),
    "ch": {channel: keys}}: each channel 2..MAX_KEYS keys from t 0 to t 1 in order, a
    handle no further in time than the next key."""
    if not isinstance(curve, dict) or not isinstance(curve.get("ch"), dict):
        return None
    out = {"ch": {}}
    length = curve.get("len")
    if isinstance(length, (int, float)) and not isinstance(length, bool) and math.isfinite(length):
        out["len"] = min(max(float(length), 1 / 60), 4.0)
    for name in CHANNELS:
        keys = curve["ch"].get(name)
        if not isinstance(keys, list) or not 2 <= len(keys) <= MAX_KEYS:
            continue
        try:
            made = []
            for k in keys:
                hi, ho = k.get("hi") or [0, 0], k.get("ho") or [0, 0]
                key = {"t": float(k["t"]), "v": float(k["v"]),
                       "hi": [float(hi[0]), float(hi[1])], "ho": [float(ho[0]), float(ho[1])]}
                if k.get("step"):
                    key["step"] = True
                if not all(math.isfinite(x) for x in (key["t"], key["v"], *key["hi"], *key["ho"])):
                    raise ValueError
                made.append(key)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            continue
        made[0]["t"], made[-1]["t"] = 0.0, 1.0
        if any(b["t"] <= a["t"] for a, b in zip(made, made[1:])):
            continue
        for n, key in enumerate(made):
            before = made[n - 1]["t"] - key["t"] if n else 0.0
            after = made[n + 1]["t"] - key["t"] if n < len(made) - 1 else 0.0
            key["hi"][0] = min(0.0, max(before, key["hi"][0]))
            key["ho"][0] = max(0.0, min(after, key["ho"][0]))
        out["ch"][name] = made
    return out if out["ch"] else None


def _copy_timing(group):
    n = max(1, int(group.get("n", 3)))
    return n, float(group.get("travel", COPY_TRAVEL)), float(group.get("lag", COPY_LAG))


def spread_len(style):
    """How long a style's copies take to fan out, seconds (0: they don't move)."""
    out = 0.0
    for group in style.get("copies") or []:
        if group.get("anim") == "spread":
            n, travel, lag = _copy_timing(group)
            out = max(out, travel + (n - 1) * lag)
    return out


def copy_at(group, k, spread, pulse=0.0):
    """Copy k of a copies group (1: the nearest) with its word's copies `spread` of the
    way out and `pulse` into a breath: (share of its distance, share of its opacity)."""
    if group.get("anim") != "spread":
        return 1.0, 1.0
    out, shown = 1.0, 1.0
    if spread < 1:
        n, travel, lag = _copy_timing(group)
        q = _clamp((spread * (travel + (n - 1) * lag) - (k - 1) * lag) / travel) if travel > 0 else 1.0
        out, shown = ease_out(q), _clamp(q / 0.35)
    if pulse > 0:
        out *= 1 - float(group.get("pulseIn", PULSE_IN)) * pulse
        shown *= 1 - float(group.get("pulseDim", PULSE_DIM)) * pulse
    return out, shown


def length(kind, scale=None):
    """An entrance's length in seconds (scale: the person's own length, seconds)."""
    base = FRAMES_AT_30.get(kind, 8) / 30.0
    return float(scale) if scale else base


def kind_of(word_id, role, line_first, doc, look):
    """A word's entrance: the word's own, else its line's, else its role's, else the
    title's (whose "look" is the look's)."""
    over = (doc.get("over") or {}).get("anim") or {}
    for value in (over.get(word_id), (doc.get("lineAnim") or {}).get(line_first),
                  (doc.get("roleAnim") or {}).get(role), doc.get("anim")):
        if value in KINDS:
            return value
    return look.get("anim") if look.get("anim") in KINDS else "pop"


def curve_for(word_id, role, line_first, doc, look):
    """A word's entrance as (kind, curve, whose): curve is the person's own drawing (the
    word's, else the title's - unless the word, its line or its role has a built-in
    entrance of its own) or None for the built-in kind's; kind is "custom" for a
    drawing, and whose says which: "word", "title" or None."""
    own = clean_curve(((doc.get("over") or {}).get("curves") or {}).get(word_id))
    if own:
        return "custom", own, "word"
    over = (doc.get("over") or {}).get("anim") or {}
    if not any(v in KINDS for v in (over.get(word_id), (doc.get("lineAnim") or {}).get(line_first),
                                    (doc.get("roleAnim") or {}).get(role))):
        title = clean_curve(doc.get("curves"))
        if title:
            return "custom", title, "title"
        return kind_of(word_id, role, line_first, doc, look), None, "title"
    return kind_of(word_id, role, line_first, doc, look), None, None


EXIT_STYLES = ("reverse",) + tuple(k for k in KINDS if k != "type")


def _drawn(curve):
    return isinstance(curve, dict) and bool(curve.get("ch"))


def _seconds(v, zero=False):
    """A time or length the person set (not a bool; above 0, or from 0 with zero)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and (v >= 0 if zero else v > 0)


def timing(doc, layout, duration, styles=None):
    """When every word starts, how long its entrance takes, and - with the exit on -
    when it goes. duration: the title's length in seconds. styles: one per box (default:
    looks.box_styles with doc's changes) - for copies that fan out."""
    look = L.get(layout["look"])
    boxes = layout["boxes"]
    plays = doc.get("plays") if doc.get("plays") in ("word", "line", "all") else "word"
    stagger = float(doc.get("stagger") if doc.get("stagger") is not None else 0.1)
    direction = doc.get("dir") if doc.get("dir") in DIRECTIONS else "u"
    times = doc.get("times") or {}
    lens = (doc.get("over") or {}).get("len") or {}
    exit_lens = (doc.get("over") or {}).get("exitLen") or {}
    exit_anims = (doc.get("over") or {}).get("exitAnim") or {}
    exit_dirs = (doc.get("over") or {}).get("exitDir") or {}
    exit_curves = (doc.get("over") or {}).get("exitCurves") or {}
    exit_times = doc.get("exitTimes") or {}
    dirs = (doc.get("over") or {}).get("dir") or {}
    firsts = {k: ids[0] for k, ids in enumerate(layout["lines"])}
    kin = layout.get("kinetic")
    within = {}                 # kinetic: how many of a block's words came before this one
    words = []
    for i, b in enumerate(boxes):
        if kin:
            # A block every kinetic.STEP seconds - the camera's move, then a hold - its
            # words one after another (or together, playing by line or all at once).
            j = within.get(b["line"], 0)
            within[b["line"]] = j + 1
            start = b["line"] * kinetic.STEP + (j * stagger if plays == "word" else 0.0)
        elif plays == "line":
            start = b["line"] * stagger * LINE_STEP
        elif plays == "all":
            start = 0.0
        else:
            start = i * stagger
        if b["role"] == "key":
            start += KEY_BEAT
        if b["id"] in times:
            start = max(0.0, float(times[b["id"]]))
        kind, curve, whose = curve_for(b["id"], b["role"], firsts.get(b["line"]), doc, look)
        # Its length: the word's own, else its drawing's, else the title's or the kind's.
        own_len = lens.get(b["id"])
        if isinstance(own_len, (int, float)) and not isinstance(own_len, bool) and own_len > 0:
            span = min(float(own_len), 4.0)
        elif curve and curve.get("len"):
            span = curve["len"]
        else:
            span = length(kind, doc.get("animLen"))
        word = {"id": b["id"], "start": start, "len": span, "kind": kind, "curve": curve, "drawn": whose,
                "dir": (dirs.get(b["id"]) if dirs.get(b["id"]) in DIRECTIONS else direction),
                "exit": None, "spread": None, "pulse": None}
        fan = spread_len(styles[i] if styles else L.word_style(look, b["role"], b["id"], firsts.get(b["line"]), doc))
        if fan > 0:
            word["spread"] = {"start": start + COPY_START * word["len"], "len": fan}
        words.append(word)
    intro = max((max(w["start"] + w["len"], w["spread"]["start"] + w["spread"]["len"] if w["spread"] else 0.0)
                 for w in words), default=0.0)
    camera = None
    if kin and words:
        # The camera moves on as each block's first word comes in - retimed with it.
        starts = [min(w["start"] for w, b in zip(words, boxes) if b["line"] == k) for k in range(len(kin["blocks"]))]
        camera = kinetic.camera_keys(kin["blocks"], starts, kin.get("camera"), layout["frame"])
        intro = max(intro, camera[-1]["t"])
    out = doc.get("exit") or {}
    exit_dur = 0.0
    if out.get("on") and words:
        order = out.get("order") if out.get("order") in ("same", "reverse", "all") else "same"
        style = out.get("style") or "reverse"
        ranked = sorted(words, key=lambda w: w["start"])
        if order == "reverse":
            ranked = ranked[::-1]
        first = min(w["start"] for w in words)
        offs = {}
        for n, w in enumerate(ranked):
            offs[w["id"]] = 0.0 if order == "all" else (n * stagger if order == "reverse" else w["start"] - first)
        title_curve = doc.get("exitCurves") if _drawn(doc.get("exitCurves")) else None
        title_towards = out.get("dir") if out.get("dir") in DIRECTIONS else None
        for w in words:
            # Which exit: the word's own drawing, else its own style, else the title's
            # drawing, else the title's style.
            own_style = exit_anims.get(w["id"]) if exit_anims.get(w["id"]) in EXIT_STYLES else None
            curve, drawn = None, None
            if _drawn(exit_curves.get(w["id"])):
                curve, drawn = exit_curves[w["id"]], "word"
            elif own_style is None and title_curve:
                curve, drawn = title_curve, "title"
            if curve:
                reverse, kind = False, w["kind"]
                span = curve.get("len") or length(w["kind"], doc.get("animLen"))
            else:
                w_style = own_style or style
                reverse = w_style == "reverse" or (w_style not in KINDS and w["curve"] is not None)
                kind = w["kind"] if reverse else (w_style if w_style in KINDS else w["kind"])
                # Reversed, the exit is the word's own entrance backwards - its own length too.
                span = w["len"] if reverse else length(kind, doc.get("animLen"))
            # Which way it leaves ("towards", as the person picks it): its own, the title's,
            # else on the way it came in - or, reversed, back where it came from.
            towards = exit_dirs.get(w["id"]) if exit_dirs.get(w["id"]) in DIRECTIONS else title_towards
            if towards is None:
                towards = OPPOSITE.get(w["dir"], "d") if reverse else w["dir"]
            # dir: what the curve is played with (a move goes against it - so the opposite).
            w["exit"] = {"off": offs[w["id"]], "len": span, "kind": kind, "reverse": reverse,
                         "dir": OPPOSITE.get(towards, "d"), "towards": towards, "curve": curve, "drawn": drawn,
                         "style": "custom" if curve else ("reverse" if reverse else kind)}
        exit_dur = max(w["exit"]["off"] + w["exit"]["len"] for w in words)
        begin = max(intro, duration - exit_dur)
        for w in words:
            ex = w["exit"]
            ex["at"] = begin + ex["off"]
            # The person's own length or time for this one (the others keep theirs): after
            # its entrance, done by the title's end.
            own_len, own_at = exit_lens.get(w["id"]), exit_times.get(w["id"])
            if _seconds(own_len):
                ex["len"] = min(float(own_len), 4.0)
            if _seconds(own_at, zero=True):
                ex["at"] = float(own_at)
            if _seconds(own_len) or _seconds(own_at, zero=True):
                ex["at"] = max(w["start"] + w["len"], min(ex["at"], duration - ex["len"]))
    if doc.get("pulse"):
        for w in words:
            if w["spread"]:
                start = w["spread"]["start"] + w["spread"]["len"]
                hold = (w["exit"]["at"] if w["exit"] else duration) - start
                if hold >= PULSE_MIN:
                    w["pulse"] = {"start": start, "len": hold, "breaths": max(1, round(hold / PULSE_PERIOD))}
    clock = {"words": words, "intro": intro, "exit": exit_dur}
    if camera is not None:
        clock["camera"] = camera
    return clock


def state(word, t):
    """A word's state at t seconds into the title."""
    s = _motion_state(word, t)
    if word.get("spread"):
        s["spread"] = _spread(word, t)
    if word.get("pulse"):
        s["pulse"] = _pulse(word, t)
    return s


def _own(word):
    """The word's entrance curve: its drawing, else its kind's."""
    return word.get("curve") or curve_of(word["kind"])


def _motion_state(word, t):
    out = word.get("exit")
    if out and t >= out["at"]:
        p = (t - out["at"]) / out["len"] if out["len"] > 0 else 1.0
        if out.get("curve"):
            # Drawn as it plays: from at rest (0) to gone (1).
            return apply_curve(out["curve"], p, out["dir"])
        if out["reverse"]:
            return apply_curve(_own(word), 1 - p, out["dir"])
        return entrance(out["kind"], 1 - p, out["dir"])
    if word["len"] <= 0:
        return dict(REST) if t >= word["start"] else apply_curve(_own(word), 0, word["dir"])
    return apply_curve(_own(word), (t - word["start"]) / word["len"], word["dir"])


def _spread(word, t):
    """How far out the word's copies are: fanning out after its entrance, folding back
    in over its exit."""
    out = word.get("exit")
    if out and t >= out["at"]:
        return 1.0 - _clamp((t - out["at"]) / out["len"]) if out["len"] > 0 else 0.0
    fan = word["spread"]
    return _clamp((t - fan["start"]) / fan["len"])


def _pulse(word, t):
    """How far into a breath the word's copies are (0 out, 1 the furthest in)."""
    beat = word["pulse"]
    p = (t - beat["start"]) / beat["len"]
    if p <= 0 or p >= 1:
        return 0.0
    return (1 - math.cos(2 * math.pi * beat["breaths"] * p)) / 2


def min_duration(clock):
    """The shortest the title can be and still play its whole entrance and exit."""
    return clock["intro"] + clock["exit"] + 0.5


def keys(word, fps, frames):
    """The word's state sampled on every frame its entrance or exit moves, as
    {channel: [(frame, value)]} - linear keys between them reproduce state() exactly
    on every frame. A channel that never moves isn't keyed."""
    spans = [(word["start"], word["start"] + word["len"])]
    for span in (word.get("spread"), word.get("pulse")):
        if span:
            spans.append((span["start"], span["start"] + span["len"]))
    if word.get("exit"):
        spans.append((word["exit"]["at"], word["exit"]["at"] + word["exit"]["len"]))
    picked = set()
    for a, b in spans:
        first, last = int(math.floor(a * fps)) - 1, int(math.ceil(b * fps)) + 1
        picked.update(f for f in range(first, last + 1) if 0 <= f < frames)
    if not picked:
        return {}
    samples = [(f, state(word, f / fps)) for f in sorted(picked)]
    out = {}
    for channel in REST:
        values = [(f, round(s[channel], 6)) for f, s in samples]
        if any(abs(v - REST[channel]) > 1e-6 for _f, v in values):
            # Drop keys in the middle of a straight run - fewer keys, same curve.
            kept = [values[0]]
            for prev, cur, nxt in zip(values, values[1:], values[2:]):
                if abs(cur[1] - prev[1]) > 1e-9 or abs(nxt[1] - cur[1]) > 1e-9:
                    kept.append(cur)
            if len(values) > 1:
                kept.append(values[-1])
            out[channel] = kept
    return out
