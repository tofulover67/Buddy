#!/usr/bin/env python3
"""
Animation > Transitions in Resolve: the cuts in the selection, and Buddy's
transitions (transition.py) put on them or taken off. Runs on the Animation
page's ResolveWorker (summary also in the Resolve reader's process).

How a Buddy transition gets onto a cut (measured on Studio 21.1):
TimelineItem.AddTransition({"type", "category": "fusion", ...}) places any
of Resolve's own Fusion transitions; one is a timeline item with a comp of
its own - MediaIn1 the outgoing clip, MediaIn2 the incoming, at the
timeline's size and rate, frames 0..length-1. Buddy places Resolve's Fusion
Cross Dissolve as a carrier and rebuilds its comp:

    MediaIn1 -> BuddyTrA (Transform, edges mirrored) -> BuddyTrBlurA (Directional Blur) -+
    MediaIn2 -> BuddyTrB (Transform, edges mirrored) -> BuddyTrBlurB (Directional Blur) -+-> Dissolve1 (Mix: a cut, mid-way)

keyed every frame from transition.plan. The light group instead:

    MediaIn -> BuddyTrSoft (Blur) -> BuddyTrA/B (Brightness Contrast: Gain) -> BuddyTrLitA/B (Merge, Screen) -+-> Dissolve1 (Mix: a crossfade)
                                                       BuddyTrLight (FastNoise, or a solid Background) --^

The wipes, glitches, paper and split-screen ones build their own chains
and feed Dissolve1's Background, its Mix held at 0. A split screen's
pieces are each a layer of their own - a card of colour and the picture
inside it, cut out by Rectangle masks (Width and Height: shares of the
frame's width and height), motion blurred - merged over the clip they
cover. The 3D ones turn each picture in a DVE (its own perspective
camera; see transition.turn_rig) over a colour or the picture behind. A transition comp edited from
outside renders as it is (no export / import round trip, unlike a clip's).
What it is stays on BuddyTrA as tool data (TAG), for Update and Remove.

Facts it leans on: a transition needs the clips' unused frames past the cut
(handles) - AddTransition returns None without them; a cut takes one
transition (a second is refused), so a new one replaces the old; and
DeleteClips removes a video transition. On a track's item list a
transition is an item that overlaps its neighbours (clips never overlap).
"""

import json
import math

from . import motion_resolve as mr
from . import transition as tr

TAG = "BuddyTransition"
CARRIER = "Cross Dissolve"          # Resolve's Fusion transition Buddy builds on
NAME_A, NAME_B = "BuddyTrA", "BuddyTrB"
MIRROR, DUPLICATE = 3, 2            # Transform Edges: Canvas, Wrap, Duplicate, Mirror
MIN_FRAMES = 4


def _fps(timeline):
    try:
        fps = float(timeline.GetSetting("timelineFrameRate") or 24)
    except (TypeError, ValueError):
        fps = 24.0
    return fps if fps > 0 else 24.0


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ cuts --

def _overlaps(item, others):
    a, b = item.GetStart(), item.GetEnd()
    return any(o is not item and o.GetStart() < b and a < o.GetEnd() for o in others)


def track_layout(timeline, track):
    """(clips, transitions) on a video track, each in time order - a
    transition being an item that overlaps another."""
    items = list(timeline.GetItemListInTrack("video", track) or [])
    transitions = [i for i in items if i.GetMediaPoolItem() is None and _overlaps(i, items)]
    clips = [i for i in items if all(i is not t for t in transitions)]
    return sorted(clips, key=lambda i: i.GetStart()), sorted(transitions, key=lambda i: i.GetStart())


def handles(item, fps, end):
    """Timeline frames of the clip's file past its end (end=True) or before
    its start: what a transition there can use. A still or generated clip:
    as many as wanted (None)."""
    pool = item.GetMediaPoolItem()
    if pool is None:
        return None
    try:
        props = pool.GetClipProperty() or {}
    except Exception:
        return None
    if str(props.get("Type") or "") == "Still":
        return None
    source_fps = _num(props.get("FPS"), fps) or fps
    try:
        speed = _num((item.GetSpeed() or {}).get("Percentage"), 100.0) / 100.0 or 1.0
    except Exception:
        speed = 1.0
    if end:
        total = int(_num(props.get("Frames"), 0))
        spare = total - 1 - int(_num(item.GetSourceEndFrame(), total - 1)) if total else 0
    else:
        spare = int(_num(item.GetSourceStartFrame(), 0))
    return max(0, math.floor(spare * fps / source_fps / abs(speed)))


def _buddy(transition):
    """A transition's Buddy settings (TAG), or None for anyone else's."""
    try:
        comp = transition.GetFusionCompByIndex(1)
        tool = comp.FindTool(NAME_A) if comp is not None else None
        raw = tool.GetData(TAG) if tool is not None else None
        return json.loads(raw) if raw else None
    except Exception:
        return None


def cuts(timeline):
    """The cuts the selection asks about: between selected clips that meet
    on a track - or, where that's none, every cut at a selected clip's ends.
    Each {"track", "frame", "left", "right", "id" ("track:frame"), "items":
    (left, right, transition or None), "existing": its name, "buddy": its
    Buddy settings, "room": (left's handles, right's)}."""
    fps = _fps(timeline)
    picked = [(item, kind) for item, kind in mr.selected_items(timeline) if kind == "video"]
    by_track = {}
    for item, _kind in picked:
        try:
            _type, track = item.GetTrackTypeAndIndex()
        except Exception:
            continue
        by_track.setdefault(int(track), []).append(item)
    between, around = [], []
    for track, chosen in sorted(by_track.items()):
        clips, transitions = track_layout(timeline, track)
        ids = {c.GetUniqueId() for c in chosen}
        for left, right in zip(clips, clips[1:]):
            if left.GetEnd() != right.GetStart():
                continue
            ends = (left.GetUniqueId() in ids, right.GetUniqueId() in ids)
            if not any(ends):
                continue
            frame = left.GetEnd()
            on = next((t for t in transitions if t.GetStart() <= frame <= t.GetEnd()), None)
            cut = {"track": track, "frame": frame, "id": f"{track}:{frame}",
                   "left": left.GetName(), "right": right.GetName(), "items": (left, right, on),
                   "existing": on.GetName() if on is not None else "", "buddy": _buddy(on) if on is not None else None,
                   "room": (handles(left, fps, True), handles(right, fps, False))}
            (between if all(ends) else around).append(cut)
    return between or around


def summary(controller):
    """{"timeline", "fps", "selected", "cuts": [{"id", "track", "frame",
    "left", "right", "existing", "buddy", "room"}]} for the page."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        return {"timeline": "", "fps": 24.0, "selected": 0, "cuts": [], "reason": "no_timeline"}
    found = cuts(timeline)
    selected = sum(1 for _item, kind in mr.selected_items(timeline) if kind == "video")
    return {"timeline": timeline.GetName(), "fps": _fps(timeline), "selected": selected,
            "cuts": [{k: v for k, v in c.items() if k != "items"} for c in found],
            "reason": "" if found else ("none" if not selected else "no_cuts")}


# ---------------------------------------------------------------- placing --

def fit(frames, room):
    """(frames, alignment) a transition `frames` long can have at a cut with
    `room` = (left's handles, right's): centred on the cut when both have
    half, else wholly after it ("right": uses the left clip's handles) or
    before it ("left": the right's), else as long as fits centred. None
    when even MIN_FRAMES won't fit."""
    left, right = (float("inf") if r is None else r for r in room)
    half = math.ceil(frames / 2)
    if left >= half and right >= half:
        return frames, "center"
    if left >= frames:
        return frames, "right"
    if right >= frames:
        return frames, "left"
    most = int(2 * min(left, right))
    most -= most % 2
    return (most, "center") if most >= MIN_FRAMES else None


def _keys(values):
    """Per-frame values as motion_resolve._key wants them - every frame
    where they change, and the ends."""
    keys = {}
    last = len(values) - 1
    for f, v in enumerate(values):
        if f in (0, last) or v != values[f - 1] or (f < last and v != values[f + 1]):
            keys[f] = {"value": float(v)}
    return keys


def _set(comp, tool, name, values):
    """An input held at one value, or keyed every frame it changes."""
    if max(values) - min(values) < 1e-9:
        tool.SetInput(name, float(values[0]))
    else:
        mr._key(comp, getattr(tool, name), _keys(values))


def _nest(comp, media_in, strength, name):
    """The picture with copies of itself nested inside it (each NEST_STEP
    the size of the last), laid on as strongly as `strength` says."""
    x = media_in
    for i in range(1, tr.NEST_COPIES + 1):
        copy = _tool(comp, "Transform", f"{name}Copy{i}", media_in, Size=tr.NEST_STEP ** i)
        merge = _tool(comp, "Merge", f"{name}Nest{i}", None)
        merge.Background.ConnectTo(x.Output)
        merge.Foreground.ConnectTo(copy.Output)
        _set(comp, merge, "Blend", strength)
        x = merge
    return x


def _chroma(comp, x, chroma, name):
    """`x` with its red channel a little bigger and its blue a little
    smaller (`chroma`, shares of the size, per frame)."""
    red = _tool(comp, "Transform", name + "Red", x, Edges=MIRROR)
    _set(comp, red, "Size", [1 + c for c in chroma])
    blue = _tool(comp, "Transform", name + "Blue", x, Edges=MIRROR)
    _set(comp, blue, "Size", [1 - c for c in chroma])
    x = _channel_from(comp, x, red, 0, name + "SplitR")
    return _channel_from(comp, x, blue, 2, name + "SplitB")


def _side(comp, media_in, values, settings, blur_type, blur_angle, name):
    """MediaIn -> Transform -> Directional Blur, taking over what it fed - a
    tunnel's nested copies before it, a chroma zoom's split or a punch's
    flash after."""
    kind = tr.KINDS[settings["kind"]]
    fed = list((media_in.Output.GetConnectedInputs() or {}).values())
    source = _nest(comp, media_in, values["Nest"], name) if kind.get("nest") else media_in
    tf = comp.AddTool("Transform", mr.FAR, mr.FAR)
    tf.SetAttrs({"TOOLS_Name": name})
    tf.Input.ConnectTo(source.Output)
    tf.SetInput("Edges", MIRROR)
    tf.SetInput("UseSizeAndAspect", 0)
    if kind.get("move") == "roll":
        tf.SetInput("Pivot", {1: 0.5, 2: tr.ROLL_PIVOT})
    _set(comp, tf, "XSize", values["XSize"])
    _set(comp, tf, "YSize", values["YSize"])
    _set(comp, tf, "Angle", values["Angle"])
    tf.AddModifier("Center", "XYPath")
    path = mr._source_tool(tf.Center)
    if path is None:
        raise RuntimeError("couldn't attach a motion path")
    _set(comp, path, "X", values["Center.X"])
    _set(comp, path, "Y", values["Center.Y"])
    out = tf
    if settings["blur"] > 0 and max(values["Length"]) > 0:
        blur = comp.AddTool("DirectionalBlur", mr.FAR, mr.FAR)
        blur.SetAttrs({"TOOLS_Name": name.replace("Tr", "TrBlur")})
        blur.Input.ConnectTo(tf.Output)
        blur.SetInput("Type", blur_type)
        blur.SetInput("Angle", blur_angle)
        _set(comp, blur, "Length", values["Length"])
        out = blur
    if max(values["Chroma"]) > 0:
        out = _chroma(comp, out, values["Chroma"], name)
    if max(values["Flash"]) > 1:
        out = _tool(comp, "BrightnessContrast", name.replace("Tr", "TrFlash"), out)
        _set(comp, out, "Gain", values["Flash"])
    for inp in fed:
        inp.ConnectTo(out.Output)
    return tf


def _light_source(comp, settings, plan):
    """What the light group screens over the clips: churning noise drifting
    across (a leak, a burn) or a solid colour."""
    kind = tr.KINDS[settings["kind"]]
    r, g, b = tr.rgb(settings["color"])
    if kind["light"] == "none":
        return None
    if kind["light"] == "band":
        # A soft slanted band of the colour, crossing the frame.
        band = _tool(comp, "RectangleMask", "BuddyTrBand", None, Width=tr.BAND_WIDTH, Height=3.0,
                     Angle=tr.BAND_ANGLE, SoftEdge=tr.BAND_SOFT)
        _point(comp, band, "Center", plan["band"], [0.5] * len(plan["band"]))
        light = _tool(comp, "Merge", "BuddyTrLight", None)
        light.Background.ConnectTo(_solid(comp, (0, 0, 0), "BuddyTrClear", alpha=0.0).Output)
        light.Foreground.ConnectTo(_solid(comp, (r, g, b), "BuddyTrTint").Output)
        light.EffectMask.ConnectTo(band.Mask)
        return light
    if kind["light"] == "flare":
        # A lens flare's streak (Hot Spot, stretched wide) on black, swept
        # across, tinted the colour (multiplied).
        dark = _solid(comp, (0, 0, 0), "BuddyTrDark")
        spot = _tool(comp, "HotSpot", "BuddyTrFlare", dark, HotSpotSize=FLARE_SIZE, Aspect=FLARE_ASPECT,
                     PrimaryStrength=1.5, SecondaryStrength=0.3)
        _point(comp, spot, "PrimaryCenter", plan["sweep"], [FLARE_Y] * len(plan["sweep"]))
        light = _tool(comp, "Merge", "BuddyTrLight", None, ApplyMode="Multiply")
        light.Background.ConnectTo(spot.Output)
        light.Foreground.ConnectTo(_solid(comp, (r, g, b), "BuddyTrTint").Output)
        return light
    if kind["light"] == "noise":
        light = comp.AddTool("FastNoise", mr.FAR, mr.FAR)
        burn = settings["kind"] == "film_burn"
        dark = tr.burn_dark(settings["color"]) if burn else (0.0, 0.0, 0.0)
        for name, value in (("Type", 0), ("Color1Red", dark[0]), ("Color1Green", dark[1]), ("Color1Blue", dark[2]),
                            ("Color1Alpha", 1.0), ("Color2Red", r), ("Color2Green", g), ("Color2Blue", b),
                            ("Color2Alpha", 1.0), *kind["noise"].items()):
            light.SetInput(name, value)
        _set(comp, light, "Seethe", plan["seethe"])
        light.AddModifier("Center", "XYPath")
        path = mr._source_tool(light.Center)
        if path is None:
            raise RuntimeError("couldn't attach a motion path")
        _set(comp, path, "X", plan["sweep"])
        _set(comp, path, "Y", [0.5] * len(plan["sweep"]))
    else:
        light = comp.AddTool("Background", mr.FAR, mr.FAR)
        for name, value in (("TopLeftRed", r), ("TopLeftGreen", g), ("TopLeftBlue", b), ("TopLeftAlpha", 1.0)):
            light.SetInput(name, value)
    light.SetAttrs({"TOOLS_Name": "BuddyTrLight"})
    return light


FLARE_SIZE, FLARE_ASPECT, FLARE_Y = 0.3, 5.0, 0.55     # a lens flare's Hot Spot: size, stretch, height


def _light_side(comp, media_in, plan, light, name, fx=None, mode="Screen", axis=None):
    """MediaIn -> Blur (or a lens Defocus) -> Brightness Contrast -> its own
    light (a Soft Glow or Rays, `fx`) -> Merge (the light, screened)."""
    fed = list((media_in.Output.GetConnectedInputs() or {}).values())
    if axis in ("zoom", "spin"):            # smeared out from the middle, or round it
        x = soft = _tool(comp, "DirectionalBlur", name.replace("Tr", "TrSoft"), media_in,
                         Type=tr.BLUR_TYPES[axis])
        _set(comp, soft, "Length", [v / tr.SOFT_MAX for v in plan["soft"]])
    else:
        x = soft = _tool(comp, "Blur", name.replace("Tr", "TrSoft"), media_in)
        if axis == "x":                     # smeared across only
            soft.SetInput("LockXY", 0)
            soft.SetInput("YBlurSize", 0.0)
        _set(comp, soft, "XBlurSize", plan["soft"])
    if fx == "bokeh":
        x = _tool(comp, "Defocus", name.replace("Tr", "TrFocus"), x, Filter=1, LensType=3, BloomLevel=1.5,
                  BloomThreshold=0.6)
        _set(comp, x, "XDefocusSize", plan["focus"])
    bright = x = _tool(comp, "BrightnessContrast", name, x)
    _set(comp, bright, "Gain", plan["gain"])
    if fx == "invert":
        # Turned negative as strongly as the swell: the picture's difference from white.
        neg = _tool(comp, "Merge", name.replace("Tr", "TrNegative"), None, ApplyMode="Difference")
        neg.Background.ConnectTo(x.Output)
        neg.Foreground.ConnectTo(_solid(comp, (1.0, 1.0, 1.0), name.replace("Tr", "TrWhite")).Output)
        _set(comp, neg, "Blend", plan["fx"])
        x = neg
    elif fx == "bloom":
        x = _tool(comp, "SoftGlow", name.replace("Tr", "TrBloom"), x, Threshold=0.25, Gain=3.0, XGlowSize=60.0)
        _set(comp, x, "Blend", plan["fx"])
    elif fx == "rays":
        rays = _tool(comp, "Fuse.OCLRays", name.replace("Tr", "TrRays"), None, Threshold=0.5, Decay=0.01,
                     Weight=3.0, Exposure=1.0)
        rays.Image.ConnectTo(x.Output)
        _point(comp, rays, "Center", plan["sweep"], [tr.RAYS_CENTER_Y] * len(plan["sweep"]))
        _set(comp, rays, "Blend", plan["fx"])
        x = rays
    if light is not None:
        lit = _tool(comp, "Merge", name.replace("Tr", "TrLit"), None, ApplyMode=mode)
        lit.Background.ConnectTo(x.Output)
        lit.Foreground.ConnectTo(light.Output)
        _set(comp, lit, "Blend", plan["glow"])
        x = lit
    for inp in fed:
        inp.ConnectTo(x.Output)
    return bright


def _gradient(comp, kind, start, end, repeat=None):
    """A grey gradient, black at `start` to white at `end` (shares of the
    frame, y up): a wipe's map, or part of one."""
    grad = comp.AddTool("Background", mr.FAR, mr.FAR)
    grad.SetInput("Type", "Gradient")
    grad.SetInput("GradientType", kind)
    grad.SetInput("Start", {1: start[0], 2: start[1]})
    grad.SetInput("End", {1: end[0], 2: end[1]})
    if repeat:
        grad.SetInput("Repeat", "Repeat" if repeat is True else repeat)
    return grad


def _mix_in(comp, under, over, share):
    """`over` laid on `under` at `share` opacity (Merge, Normal)."""
    merge = comp.AddTool("Merge", mr.FAR, mr.FAR)
    merge.Background.ConnectTo(under.Output)
    merge.Foreground.ConnectTo(over.Output)
    merge.SetInput("Blend", share)
    return merge


def _wipe_map(comp, settings, plan=None):
    """The wipe's map: black where the reveal starts, white where it ends.
    A kind with noise settings of its own ("grain") gets them, boiling as
    plan["seethe"] says."""
    kind = tr.KINDS[settings["kind"]]
    if kind.get("map") == "radial":
        # From the middle out to past the corners (Fusion measures in widths).
        shape = _gradient(comp, "Radial", (0.5, 0.5), (1.1, 0.5))
    elif kind.get("map") in ("angle", "square", "reflect"):
        # Angle: 0 straight up, rising anticlockwise; Square: a box out to
        # the sides (corners included); Reflect: the middle line out.
        end = (0.5, 1.0) if kind["map"] == "angle" else (1.0, 0.5)
        shape = _gradient(comp, kind["map"].capitalize(), (0.5, 0.5), end)
    elif kind.get("map") == "down":
        shape = _gradient(comp, "Linear", (0.5, 1.0), (0.5, 0.0))
    elif kind.get("map") == "diag":         # the top left corner to the bottom right
        shape = _gradient(comp, "Linear", (0.0, 1.0), (1.0, 0.0))
    elif kind.get("map") == "spiral":
        # A third the clock's turn, two thirds the way out: the edge winds out from the middle.
        turn = _gradient(comp, "Angle", (0.5, 0.5), (0.5, 1.0))
        shape = _mix_in(comp, turn, _gradient(comp, "Radial", (0.5, 0.5), (1.1, 0.5)), 2 / 3)
    elif kind.get("map") == "corner":       # a circle from the top left corner, out past the far one
        shape = _gradient(comp, "Radial", (0.0, 1.0), (1.15, 1.0))
    elif kind.get("map") == "zigzag":
        # Across, its edge toothed by a Ping-Pong gradient down the frame (ZIGZAG teeth).
        teeth = _gradient(comp, "Linear", (0.5, 1.0), (0.5, 1.0 - 0.5 / tr.ZIGZAG), repeat="Ping-Pong")
        shape = _mix_in(comp, _gradient(comp, "Linear", (0.0, 0.5), (1.0, 0.5)), teeth, ZIGZAG_DEPTH)
    elif kind.get("map") == "blinds":
        # A saw down the frame: every slat sweeps top to bottom at once.
        shape = _gradient(comp, "Linear", (0.5, 1.0), (0.5, 1.0 - 1.0 / tr.BLINDS), repeat=True)
    elif kind.get("map") == "halftone":
        # Each square cell its distance out from its middle (the larger of
        # across and down: Ping-Pong gradients, black at the middles,
        # Lighten), with a diagonal sweep mixed in.
        width = _num(comp.GetPrefs("Comp.FrameFormat.Width"), 1920.0) or 1920.0
        height = _num(comp.GetPrefs("Comp.FrameFormat.Height"), 1080.0) or 1080.0
        sx = 0.5 / tr.HALFTONE
        sy = sx * width / height
        across = _gradient(comp, "Linear", (sx, 0.5), (2 * sx, 0.5), repeat="Ping-Pong")
        down = _gradient(comp, "Linear", (0.5, 1 - sy), (0.5, 1 - 2 * sy), repeat="Ping-Pong")
        cells = _tool(comp, "Merge", "BuddyTrCells", None, ApplyMode="Lighten")
        cells.Background.ConnectTo(across.Output)
        cells.Foreground.ConnectTo(down.Output)
        shape = _mix_in(comp, _gradient(comp, "Linear", (0.0, 1.0), (1.0, 0.0)), cells, 0.5)
    elif kind.get("map") == "bars":
        # Diagonal bars, each its own sweep, the far ones a little behind.
        step = 1.0 / tr.BARS
        saw = _gradient(comp, "Linear", (0.0, 1.0), (step, 1.0 - step * 9 / 16), repeat=True)
        across = _gradient(comp, "Linear", (0.0, 1.0), (1.0, 0.0))
        shape = _mix_in(comp, across, saw, 0.6)
    else:
        shape = _gradient(comp, "Linear", (0.0, 0.5), (1.0, 0.5))
    if kind["noise"] <= 0:
        return shape
    noise = comp.AddTool("FastNoise", mr.FAR, mr.FAR)
    if "grain" in kind:
        noise.SetInput("LockXY", 0)
        for name, value in kind["grain"].items():
            noise.SetInput(name, value)
        if plan is not None and "seethe" in plan:
            _set(comp, noise, "Seethe", plan["seethe"])
    else:
        ink = kind.get("dark", False)
        for name, value in (("XScale", 6.0 if ink else 3.0), ("Detail", 5.0 if ink else 2.0),
                            ("Contrast", 1.4 if ink else 1.0), ("Brightness", 0.0)):
            noise.SetInput(name, value)
    return _mix_in(comp, shape, noise, kind["noise"])


ZIGZAG_DEPTH = 0.12                 # how much of a zigzag's map its teeth are


def _masked(comp, under, over, edge, at, soft, name, below=True):
    """`over` on `under` where the map is below (or above) `at` - per-frame
    lists - with a soft edge `soft` wide: a Merge masked by the map's
    brightness."""
    merge = comp.AddTool("Merge", mr.FAR, mr.FAR)
    merge.SetAttrs({"TOOLS_Name": name})
    merge.Background.ConnectTo(under.Output)
    merge.Foreground.ConnectTo(over.Output)
    merge.EffectMask.ConnectTo(edge.Output)
    merge.SetInput("MaskChannel", 5)                    # Luminance
    merge.SetInput("ApplyMaskInverted", 1 if below else 0)
    _set(comp, merge, "MaskLow", [m - h / 2 for m, h in zip(at, soft)])
    _set(comp, merge, "MaskHigh", [m + h / 2 for m, h in zip(at, soft)])
    return merge


def _wipe_stage(comp, under, over, edge, plan, key, name):
    """`over` on `under` where the map is below plan[key] (a soft edge
    plan["soft"] wide)."""
    return _masked(comp, under, over, edge, plan[key], plan["soft"], name)


def _solid(comp, rgb, name, alpha=1.0):
    """A flat colour (premultiplied: a clear one is black, alpha 0)."""
    color = comp.AddTool("Background", mr.FAR, mr.FAR)
    color.SetAttrs({"TOOLS_Name": name})
    for key, value in zip(("TopLeftRed", "TopLeftGreen", "TopLeftBlue"), rgb):
        color.SetInput(key, value * alpha)
    color.SetInput("TopLeftAlpha", alpha)
    return color


def _wipe(comp, mi1, mi2, dissolve, settings, plan):
    """MediaIn1 -> band of colour (Merge over the map) -> MediaIn2 (Merge
    over the map) -> Dissolve1, held on its Background."""
    kind = tr.KINDS[settings["kind"]]
    if kind.get("map") == "star":
        return _iris(comp, mi1, mi2, dissolve, settings, plan)
    if kind.get("map") == "shutter":
        return _shutter(comp, mi1, mi2, dissolve, settings, plan)
    if kind.get("map") == "luma":           # the incoming picture's own brightness
        edge = mi2
    else:
        edge = _wipe_map(comp, settings, plan)
        edge.SetAttrs({"TOOLS_Name": "BuddyTrMap"})
    r, g, b = tr.rgb(settings["color"])
    if kind.get("dark"):
        r, g, b = r * 0.12, g * 0.12, b * 0.12
    color = _solid(comp, (r, g, b), "BuddyTrColor")
    if kind.get("flip"):        # from the map's top down: where it's above 1 - m
        band = mi1
        if kind["band"] > 0:
            band = _masked(comp, mi1, color, edge, [1 - v for v in plan["m1"]], plan["soft"], "BuddyTrBand",
                           below=False)
        reveal = _masked(comp, band, mi2, edge, [1 - v for v in plan["m2"]], plan["soft"], NAME_A, below=False)
    elif kind["band"] <= 0:
        reveal = _wipe_stage(comp, mi1, mi2, edge, plan, "m2", NAME_A)
    else:
        band = _wipe_stage(comp, mi1, color, edge, plan, "m1", "BuddyTrBand")
        reveal = _wipe_stage(comp, band, mi2, edge, plan, "m2", NAME_A)
    if kind.get("glow"):
        # The band alone, on black, blurred and screened on: it glows (as a burn's ember).
        black = _solid(comp, (0, 0, 0), "BuddyTrBlack")
        lit = _wipe_stage(comp, black, color, edge, plan, "m1", "BuddyTrEmber")
        lit = _wipe_stage(comp, lit, black, edge, plan, "m2", "BuddyTrEmberOnly")
        glow = _tool(comp, "Blur", "BuddyTrGlowBlur", lit, XBlurSize=tr.GLOW_SIZE)
        screen = _tool(comp, "Merge", "BuddyTrGlow", None, ApplyMode="Screen", Blend=kind["glow"])
        screen.Background.ConnectTo(reveal.Output)
        screen.Foreground.ConnectTo(glow.Output)
        dissolve.Background.ConnectTo(screen.Output)
        dissolve.Mix.ConnectTo(None)
        dissolve.SetInput("Mix", 0.0)
        return reveal
    dissolve.Background.ConnectTo(reveal.Output)
    dissolve.Mix.ConnectTo(None)
    dissolve.SetInput("Mix", 0.0)
    return reveal


STAR_TURN = 72.0                    # a star turns this much (degrees) as it grows across the frame
HEART_LOBE = math.sqrt(0.125)       # a heart's circles' middles: the middles of its square's upper sides
HEART_DROP = (HEART_LOBE + 0.5 - math.sqrt(0.5)) / 2    # ... and the whole dropped this much: its middle the frame's


def _star(comp, name, sizes, soft, width, kind="star"):
    """A white star (sStar -> sRender), `sizes` wide per frame, its edge
    softened `soft` (map units, per frame) - a mask. A "diamond": sNGon's
    four sides (its tips left and right, Width tip to tip), not turning."""
    if kind == "heart":
        # A square on its point and a circle on each upper side (side 1,
        # frame widths, y up), dropped so the whole is in the middle, then
        # sized as a whole.
        square = _tool(comp, "sRectangle", name + "Square", None, Width=1.0, Height=1.0, Angle=45.0)
        square.SetInput("Translate.Y", -HEART_DROP)
        x = square
        for side in (-1, 1):
            lobe = _tool(comp, "sEllipse", name + ("Left" if side < 0 else "Right"), None, Width=1.0, Height=1.0)
            lobe.SetInput("Translate.X", side * HEART_LOBE)
            lobe.SetInput("Translate.Y", HEART_LOBE - HEART_DROP)
            both = _tool(comp, "sMerge", name + ("Lobe1" if side < 0 else "Lobe2"), None)
            both.Input1.ConnectTo(x.Output)
            both.Input2.ConnectTo(lobe.Output)
            x = both
        shape = _tool(comp, "sTransform", name, x)
        sizes = [max(0.0, m) * tr.HEART_COVER for m in sizes]
        _set(comp, shape, "XSize", sizes)
        _set(comp, shape, "YSize", sizes)
    else:
        if kind == "diamond":
            shape = _tool(comp, "sNGon", name, None, Sides=4)
            widths = [max(0.0, m) * tr.DIAMOND_COVER for m in sizes]
        else:
            shape = _tool(comp, "sStar", name, None, Points=5)
            widths = [max(0.0, m) * tr.STAR_COVER for m in sizes]
            _set(comp, shape, "Angle", [STAR_TURN * max(0.0, m) for m in sizes])
        _set(comp, shape, "Width", widths)
        _set(comp, shape, "Height", widths)
    height = _num(comp.GetPrefs("Comp.FrameFormat.Height"), 1080.0) or 1080.0
    render = _tool(comp, "sRender", name + "Render", None, Width=float(width), Height=float(height))
    render.Input.ConnectTo(shape.Output)
    blur = _tool(comp, "Blur", name + "Soft", render)
    _set(comp, blur, "XBlurSize", [v * width * 0.5 for v in soft])
    return blur


def _shutter(comp, mi1, mi2, dissolve, settings, plan):
    """The clips (a cut at the middle) seen through a six-sided aperture
    (sNGon) closing to nothing and opening again, turning, on black - or
    an iris's round one (an Ellipse mask: Width and Height both frame
    widths, measured)."""
    width = _num(comp.GetPrefs("Comp.FrameFormat.Width"), 1920.0) or 1920.0
    height = _num(comp.GetPrefs("Comp.FrameFormat.Height"), 1080.0) or 1080.0
    cut = _tool(comp, "Dissolve", "BuddyTrCut", None)
    cut.Background.ConnectTo(mi1.Output)
    cut.Foreground.ConnectTo(mi2.Output)
    _set(comp, cut, "Mix", plan["mix"])
    seen = _tool(comp, "Merge", NAME_A, None)
    seen.Background.ConnectTo(_solid(comp, (0, 0, 0), "BuddyTrBlack").Output)
    seen.Foreground.ConnectTo(cut.Output)
    if tr.KINDS[settings["kind"]].get("shape") == "circle":
        hole = _tool(comp, "EllipseMask", "BuddyTrAperture", None)
        sizes = [v * tr.IRIS_COVER for v in plan["open"]]
        _set(comp, hole, "Width", sizes)
        _set(comp, hole, "Height", sizes)
        seen.EffectMask.ConnectTo(hole.Mask)
    else:
        blades = _tool(comp, "sNGon", "BuddyTrBlades", None, Sides=6)
        sizes = [v * tr.HEX_COVER for v in plan["open"]]
        _set(comp, blades, "Width", sizes)
        _set(comp, blades, "Height", sizes)
        _set(comp, blades, "Angle", plan["turn"])
        hole = _tool(comp, "sRender", "BuddyTrAperture", None, Width=float(width), Height=float(height))
        hole.Input.ConnectTo(blades.Output)
        seen.SetInput("MaskChannel", 5)
        seen.EffectMask.ConnectTo(hole.Output)
    dissolve.Background.ConnectTo(seen.Output)
    dissolve.Mix.ConnectTo(None)
    dissolve.SetInput("Mix", 0.0)
    return seen


def _iris(comp, mi1, mi2, dissolve, settings, plan):
    """A shape grown from the middle: the band of colour, then the incoming
    clip, each inside its own star."""
    kind = tr.KINDS[settings["kind"]]
    width = _num(comp.GetPrefs("Comp.FrameFormat.Width"), 1920.0) or 1920.0
    r, g, b = tr.rgb(settings["color"])
    if kind.get("dark"):
        r, g, b = r * 0.12, g * 0.12, b * 0.12
    color = _solid(comp, (r, g, b), "BuddyTrColor")
    x = mi1
    for key, over, name in (("m1", color, "BuddyTrBand"), ("m2", mi2, NAME_A)):
        mask = _star(comp, name + "Star", plan[key], plan["soft"], width, kind.get("shape", "star"))
        merge = _tool(comp, "Merge", name, None, MaskChannel=5)
        merge.Background.ConnectTo(x.Output)
        merge.Foreground.ConnectTo(over.Output)
        merge.EffectMask.ConnectTo(mask.Output)
        x = merge
    dissolve.Background.ConnectTo(x.Output)
    dissolve.Mix.ConnectTo(None)
    dissolve.SetInput("Mix", 0.0)
    return x


DO_NOTHING = 4                      # ChannelBoolean's To...: Red FG, Green FG, Blue FG, Alpha FG, Do Nothing...


def _tool(comp, reg, name, source=None, **inputs):
    tool = comp.AddTool(reg, mr.FAR, mr.FAR)
    tool.SetAttrs({"TOOLS_Name": name})
    if source is not None:
        tool.Input.ConnectTo(source.Output)
    for key, value in inputs.items():
        tool.SetInput(key, value)
    return tool


def _channel_from(comp, under, over, channel, name):
    """`under` with one colour channel (0 red, 2 blue) taken from `over`."""
    cb = comp.AddTool("ChannelBoolean", mr.FAR, mr.FAR)
    cb.SetAttrs({"TOOLS_Name": name})
    cb.Background.ConnectTo(under.Output)
    cb.Foreground.ConnectTo(over.Output)
    cb.SetInput("Operation", 0)                         # Copy
    for i, key in enumerate(("ToRed", "ToGreen", "ToBlue", "ToAlpha")):
        cb.SetInput(key, i if i == channel else DO_NOTHING)
    return cb


def _blocks(comp, x, block, name):
    """`x` in blocks `block` pixels (at 1920 wide) a side, per frame: down
    to a pixel a block (box: each its block's average), back up nearest
    neighbour - BetterResize, which takes pixel sizes (see _glitch)."""
    width, height = comp.GetPrefs("Comp.FrameFormat.Width") or 1920, comp.GetPrefs("Comp.FrameFormat.Height") or 1080
    size = [b * width / 1920 for b in block]
    down = _tool(comp, "BetterResize", name, x, KeepAspect=0, FilterMethod=1)
    _set(comp, down, "Width", [float(max(1, round(width / z))) for z in size])
    _set(comp, down, "Height", [float(max(1, round(height / z))) for z in size])
    return _tool(comp, "BetterResize", name + "Up", down, KeepAspect=0, FilterMethod=0,
                 Width=float(width), Height=float(height))


def _crt(comp, cut, dissolve, plan):
    """The stuttered-to-cut clips squashed to a line, a dot, gone - lit up
    as they go - on black."""
    tf = _tool(comp, "Transform", NAME_A, cut, UseSizeAndAspect=0)
    _set(comp, tf, "XSize", plan["sx"])
    _set(comp, tf, "YSize", plan["sy"])
    bright = _tool(comp, "BrightnessContrast", "BuddyTrTube", tf)
    _set(comp, bright, "Gain", plan["gain"])
    over = _tool(comp, "Merge", "BuddyTrScreen", None)
    over.Background.ConnectTo(_solid(comp, (0, 0, 0), "BuddyTrBlack").Output)
    over.Foreground.ConnectTo(bright.Output)
    dissolve.Background.ConnectTo(over.Output)
    dissolve.Mix.ConnectTo(None)
    dissolve.SetInput("Mix", 0.0)
    return tf


def _glitch(comp, mi1, mi2, dissolve, settings, plan):
    """The clips stuttered (a Dissolve of their own), broken into blocks,
    split into colour channels, torn into bands, flickered and faded, under
    scanlines and grain -> Dissolve1, held on its Background."""
    kind = tr.KINDS[settings["kind"]]
    cut = comp.AddTool("Dissolve", mr.FAR, mr.FAR)
    cut.SetAttrs({"TOOLS_Name": "BuddyTrCut"})
    cut.Background.ConnectTo(mi1.Output)
    cut.Foreground.ConnectTo(mi2.Output)
    _set(comp, cut, "Mix", plan["mix"])
    x = cut
    if kind.get("style") == "crt":
        return _crt(comp, cut, dissolve, plan)
    if kind.get("style") == "pixelate":
        x = _blocks(comp, x, plan["block"], NAME_A)
        dissolve.Background.ConnectTo(x.Output)
        dissolve.Mix.ConnectTo(None)
        dissolve.SetInput("Mix", 0.0)
        return comp.FindTool(NAME_A)
    if max(map(abs, plan["jx"] + plan["jy"])) > 0:
        # The picture jumping about (a chaos), its edges stretched out.
        jump = _tool(comp, "Transform", "BuddyTrJump", x, Edges=DUPLICATE)
        _point(comp, jump, "Center", [0.5 + v for v in plan["jx"]], [0.5 + v for v in plan["jy"]])
        x = jump
    if max(plan["block"]) > 1:
        # Crisp blocks: Resize, which takes pixel sizes - Scale rounded its
        # sizes its own way (1920 x 71/1920 came out 72 wide, the pair 1948
        # wide, the frame refitted) and Transforms set to nearest came out
        # soft in Resolve's render.
        x = _blocks(comp, x, plan["block"], "BuddyTrBlocks")
    if max(map(abs, plan["split"])) > 0:
        # Red pushed one way, blue the other.
        right = _tool(comp, "Transform", "BuddyTrRed", x, Edges=DUPLICATE)
        left = _tool(comp, "Transform", "BuddyTrBlue", x, Edges=DUPLICATE)
        for tf, sign in ((right, 1), (left, -1)):
            tf.AddModifier("Center", "XYPath")
            path = mr._source_tool(tf.Center)
            if path is None:
                raise RuntimeError("couldn't attach a motion path")
            _set(comp, path, "X", [0.5 + sign * v for v in plan["split"]])
            _set(comp, path, "Y", [0.5] * len(plan["split"]))
        x = _channel_from(comp, x, right, 0, "BuddyTrSplitR")
        x = _channel_from(comp, x, left, 2, "BuddyTrSplitB")
    if max(plan["tear"]) > 0:
        # Bands torn sideways: Displace by a noise that only changes down the frame.
        rows = _tool(comp, "FastNoise", "BuddyTrRows", None, LockXY=0, XScale=0.01, YScale=kind["rows"], Detail=1.0,
                     Contrast=2.0, Discontinuous=1 if kind["hard"] else 0)
        _set(comp, rows, "Seethe", plan["seethe"])
        tear = _tool(comp, "Displace", "BuddyTrTear", x, Type=1, XChannel=0, XOffset=0.5, YRefraction=0.0)
        tear.Foreground.ConnectTo(rows.Output)
        _set(comp, tear, "XRefraction", plan["tear"])
        # Displace pulls in nothing past the frame's edge: what a band leaves
        # bare there shows the picture untorn, not black.
        patched = _tool(comp, "Merge", "BuddyTrTorn", None)
        patched.Background.ConnectTo(x.Output)
        patched.Foreground.ConnectTo(tear.Output)
        x = patched
    x = _tool(comp, "BrightnessContrast", NAME_A, x)
    _set(comp, x, "Gain", plan["gain"])
    _set(comp, x, "Saturation", plan["sat"])
    tagged = x
    if max(plan["neg"]) > 0:
        # Frames flashed negative: the picture's difference from white.
        neg = _tool(comp, "Merge", "BuddyTrNegative", None, ApplyMode="Difference")
        neg.Background.ConnectTo(x.Output)
        neg.Foreground.ConnectTo(_solid(comp, (1.0, 1.0, 1.0), "BuddyTrWhite").Output)
        _set(comp, neg, "Blend", plan["neg"])
        x = neg
    if max(plan["scan"]) > 0:
        lines = _gradient(comp, "Linear", (0.5, 0.0), (0.5, 1.0 / 270), repeat=True)
        lines.SetAttrs({"TOOLS_Name": "BuddyTrScanlines"})
        scan = _tool(comp, "Merge", "BuddyTrScan", None, ApplyMode="Multiply")
        scan.Background.ConnectTo(x.Output)
        scan.Foreground.ConnectTo(lines.Output)
        _set(comp, scan, "Blend", plan["scan"])
        x = scan
    if max(plan["grain"]) > 0:
        noise = _tool(comp, "FastNoise", "BuddyTrGrainNoise", None, XScale=300.0, Detail=2.0, Contrast=1.5)
        _set(comp, noise, "Seethe", plan["seethe"])
        grain = _tool(comp, "Merge", "BuddyTrGrain", None, ApplyMode="Overlay")
        grain.Background.ConnectTo(x.Output)
        grain.Foreground.ConnectTo(noise.Output)
        _set(comp, grain, "Blend", plan["grain"])
        x = grain
    dissolve.Background.ConnectTo(x.Output)
    dissolve.Mix.ConnectTo(None)
    dissolve.SetInput("Mix", 0.0)
    return tagged


PAPER = (0.96, 0.94, 0.89)          # a tear's torn edge


def _xy_path(comp, tool, xs, ys):
    tool.AddModifier("Center", "XYPath")
    path = mr._source_tool(tool.Center)
    if path is None:
        raise RuntimeError("couldn't attach a motion path")
    _set(comp, path, "X", xs)
    _set(comp, path, "Y", ys)


def _tear(comp, mi1, mi2, dissolve, settings, plan):
    """MediaIn1 cut in two along the map's middle - each half its torn
    paper edge, its photo, a shadow, moved off - over MediaIn2."""
    edge = _wipe_map(comp, settings)
    edge.SetAttrs({"TOOLS_Name": "BuddyTrMap"})
    clear = _solid(comp, (0, 0, 0), "BuddyTrClear", alpha=0.0)
    paper = _solid(comp, PAPER, "BuddyTrPaper")
    n = len(plan["open"])
    hard = [tr.EDGE_MIN] * n
    line = 0.5
    under, first = mi2, None
    for side in ("l", "r"):
        if side == "l":     # the map below the line - and a little past, under the right's edge
            reach = line + tr.TEAR_OVERLAP
            torn = _masked(comp, clear, paper, edge, [reach] * n, hard, "BuddyTrTornL")
            half = _masked(comp, torn, mi1, edge, [reach - f for f in plan["fringe"]], hard, "BuddyTrHalfL")
        else:
            torn = _masked(comp, clear, paper, edge, [line] * n, hard, "BuddyTrTornR", below=False)
            half = _masked(comp, torn, mi1, edge, [line + f for f in plan["fringe"]], hard, "BuddyTrHalfR",
                           below=False)
        shadow = _tool(comp, "Shadow", "BuddyTrShadow" + side.upper(), half,
                       Softness=4.0 + 20.0 * settings["blur"], Alpha=0.55)
        shadow.SetInput("ShadowOffset", {1: 0.5 + 0.006, 2: 0.5 - 0.01})
        tf = _tool(comp, "Transform", NAME_A if side == "l" else NAME_B, shadow)
        _xy_path(comp, tf, plan[side + "x"], plan[side + "y"])
        _set(comp, tf, "Angle", plan[side + "a"])
        over = _tool(comp, "Merge", "BuddyTrOver" + side.upper(), None)
        over.Background.ConnectTo(under.Output)
        over.Foreground.ConnectTo(tf.Output)
        under, first = over, first or tf
    dissolve.Background.ConnectTo(under.Output)
    dissolve.Mix.ConnectTo(None)
    dissolve.SetInput("Mix", 0.0)
    return first


def _burn(comp, mi1, mi2, dissolve, settings, plan):
    """MediaIn1 charred, then glowing, then gone to MediaIn2, along the map,
    the ember's glow screened over."""
    kind = tr.KINDS[settings["kind"]]
    edge = _wipe_map(comp, settings)
    edge.SetAttrs({"TOOLS_Name": "BuddyTrMap"})
    r, g, b = tr.rgb(settings["color"])
    char = _solid(comp, tr.char(settings["color"]), "BuddyTrChar")
    ember = _solid(comp, (r, g, b), "BuddyTrColor")
    black = _solid(comp, (0, 0, 0), "BuddyTrBlack")
    scorch = [3 * v for v in plan["soft"]]
    x = _masked(comp, mi1, char, edge, plan["m0"], scorch, "BuddyTrCharred")
    x = _masked(comp, x, ember, edge, plan["m1"], plan["soft"], "BuddyTrBand")
    x = _masked(comp, x, mi2, edge, plan["m2"], plan["soft"], NAME_A)
    tagged = x
    # The glow: the ember band alone, on black, blurred, screened on.
    lit = _masked(comp, black, ember, edge, plan["m1"], plan["soft"], "BuddyTrEmber")
    lit = _masked(comp, lit, black, edge, plan["m2"], plan["soft"], "BuddyTrEmberOnly")
    glow = _tool(comp, "Blur", "BuddyTrGlowBlur", lit, XBlurSize=tr.GLOW_SIZE)
    screen = _tool(comp, "Merge", "BuddyTrGlow", None, ApplyMode="Screen", Blend=kind["glow"])
    screen.Background.ConnectTo(x.Output)
    screen.Foreground.ConnectTo(glow.Output)
    dissolve.Background.ConnectTo(screen.Output)
    dissolve.Mix.ConnectTo(None)
    dissolve.SetInput("Mix", 0.0)
    return tagged


# Dust and scratches: specks where a noise is brightest - at Contrast 1,
# measured, FastNoise sits 0.23-0.81 (1st to 99.7th percentile), so these
# thresholds catch a percent or so of the frame.
DUST = (("Dust", (0.05, 0.04, 0.03), {"XScale": 24.0, "YScale": 24.0, "Detail": 3.0, "Contrast": 1.0}, 0.76),
        ("Scratches", (0.85, 0.83, 0.78), {"XScale": 60.0, "YScale": 0.4, "Detail": 1.0, "Contrast": 1.0}, 0.76))


def _grunge(comp, mi1, mi2, dissolve, settings, plan):
    """The wipe on a boiling map, then dust and scratches, then the whole
    picture jittered (edges mirrored)."""
    tagged = _wipe(comp, mi1, mi2, dissolve, settings, plan)
    x = tagged
    if max(plan["dirt"]) > 0:
        # Specks where a noise is brightest: a colour, masked by it.
        for name, color, noise, low in DUST:
            dirt = _tool(comp, "FastNoise", "BuddyTr" + name + "Noise", None, LockXY=0, **noise)
            _set(comp, dirt, "Seethe", [v * 3.1 for v in plan["seethe"]])
            spots = _solid(comp, color, "BuddyTr" + name + "Color")
            merge = _tool(comp, "Merge", "BuddyTr" + name, None, MaskChannel=5, MaskLow=low, MaskHigh=low + 0.02)
            merge.Background.ConnectTo(x.Output)
            merge.Foreground.ConnectTo(spots.Output)
            merge.EffectMask.ConnectTo(dirt.Output)
            _set(comp, merge, "Blend", plan["dirt"])
            x = merge
    if max(map(abs, plan["jx"] + plan["jy"])) > 0:
        tf = _tool(comp, "Transform", "BuddyTrJitter", x, Edges=MIRROR)
        _xy_path(comp, tf, [0.5 + v for v in plan["jx"]], [0.5 + v for v in plan["jy"]])
        x = tf
    dissolve.Background.ConnectTo(x.Output)
    return tagged


DRIP_ACROSS = 3                     # Drip Shape "Horizontal" (0 Circular)
BULGE, PINCH, POLAR, RECT = 2, 0, 0, 1     # Dent Type: the fisheye, the one a pinch pulls; CoordSpace Shape


def _bend(comp, source, settings, plan, side, noise):
    """One side's picture bent as the style says (each tool keyed from the
    plan); returns the last tool and the one carrying the tag."""
    kind = tr.KINDS[settings["kind"]]
    style, name = kind["style"], NAME_A if side == "a" else NAME_B
    if style == "twirl":
        tool = _tool(comp, "Vortex", name, source, Size=tr.TWIRL_SIZE, Power=2.0)
        _set(comp, tool, "Angle", plan["angle_" + side])
        return tool, tool
    if style in ("ripple", "wave"):
        tool = _tool(comp, "Drip", name, source, Shape=0 if style == "ripple" else DRIP_ACROSS,
                     Frequency=tr.RIPPLE_FREQ)
        _set(comp, tool, "Amplitude", plan["amp"])
        _set(comp, tool, "Phase", plan["phase"])
        return tool, tool
    if style == "lens":
        # Bulged, then zoomed: a Dent fed by a Transform with mirrored edges
        # draws nothing (all black, Studio 21.1).
        tool = _tool(comp, "Dent", name.replace("Tr", "TrLens"), source, Type=BULGE, Size=tr.BULGE_SIZE)
        _set(comp, tool, "Strength", plan["strength"])
        zoom = _tool(comp, "Transform", name, tool, Edges=MIRROR)
        _set(comp, zoom, "Size", plan["zoom_" + side])
        return zoom, zoom
    if style in ("bulge", "pinch"):
        # A pinch is Dent's first type pulled the other way (Strength
        # negative): by -0.3 the whole picture is drawn into one point.
        bulge = style == "bulge"
        tool = _tool(comp, "Dent", name, source, Type=BULGE if bulge else PINCH, Size=tr.BULGE_SIZE)
        _set(comp, tool, "Strength", plan["strength"] if bulge else [-v for v in plan["strength"]])
        return tool, tool
    if style == "glass":
        shift = _tool(comp, "Displace", name, source, Type=1, XChannel=0, YChannel=1, XOffset=0.5, YOffset=0.5)
        shift.Foreground.ConnectTo(noise[0].Output)
        _set(comp, shift, "XRefraction", plan["refract"])
        _set(comp, shift, "YRefraction", plan["refract"])
        patched = _tool(comp, "Merge", "BuddyTrGlass" + side.upper(), None)
        patched.Background.ConnectTo(source.Output)
        patched.Foreground.ConnectTo(shift.Output)
        return patched, shift
    if style == "melt":
        # Pulled down in streaks: one Displace, down only, by a noise that
        # changes fast across and slowly down.
        down = _tool(comp, "Displace", name, source, Type=1, YChannel=0, YOffset=0.0, XRefraction=0.0)
        down.Foreground.ConnectTo(noise[0].Output)
        _set(comp, down, "YRefraction", plan["refract"])
        patched = _tool(comp, "Merge", "BuddyTrMelted" + side.upper(), None)
        patched.Background.ConnectTo(source.Output)
        patched.Foreground.ConnectTo(down.Output)
        return patched, down
    if style == "warp":
        # Two Displaces, one across and one down, each by its own noise.
        across = _tool(comp, "Displace", name, source, Type=1, XChannel=0, XOffset=0.5, YRefraction=0.0)
        across.Foreground.ConnectTo(noise[0].Output)
        _set(comp, across, "XRefraction", plan["refract"])
        down = _tool(comp, "Displace", name.replace("Tr", "TrDown"), across, Type=1, YChannel=0, YOffset=0.5,
                     XRefraction=0.0)
        down.Foreground.ConnectTo(noise[1].Output)
        _set(comp, down, "YRefraction", plan["refract"])
        # Displace pulls in nothing past the frame: the plain picture shows there, not black.
        patched = _tool(comp, "Merge", "BuddyTrWarped" + side.upper(), None)
        patched.Background.ConnectTo(source.Output)
        patched.Foreground.ConnectTo(down.Output)
        return patched, across
    # The kaleidoscope: into polar co-ordinates (across = the angle, up =
    # the distance out), one segment's wedge stretched across (flattened,
    # or Fusion would fold the two Transforms into none), squeezed back and
    # mirror-tiled round, turned (shifted across) and zoomed (stretched up),
    # back, and faded in over the plain picture.
    polar = _tool(comp, "CoordSpace", "BuddyTrPolar" + side.upper(), source, Shape=POLAR)
    wedge = _tool(comp, "Transform", "BuddyTrWedge" + side.upper(), polar, UseSizeAndAspect=0,
                  XSize=float(kind["segments"]), FlattenTransform=1)
    fold = _tool(comp, "Transform", "BuddyTrFold" + side.upper(), wedge, UseSizeAndAspect=0, Edges=MIRROR,
                 XSize=1.0 / kind["segments"])
    _set(comp, fold, "YSize", plan["zoom"])
    _xy_path(comp, fold, [0.5 + v for v in plan["spin"]], [0.5] * len(plan["spin"]))
    back = _tool(comp, "CoordSpace", "BuddyTrRect" + side.upper(), fold, Shape=RECT)
    fill = _tool(comp, "Transform", "BuddyTrFill" + side.upper(), back, Size=tr.KALEIDO_OUT)
    merge = _tool(comp, "Merge", name, None)
    merge.Background.ConnectTo(source.Output)
    merge.Foreground.ConnectTo(fill.Output)
    _set(comp, merge, "Blend", plan["kaleido"])
    return merge, merge


def _distort(comp, mi1, mi2, dissolve, settings, plan):
    """Each MediaIn bent (_bend) and softened, crossfaded by Dissolve1."""
    kind = tr.KINDS[settings["kind"]]
    noise = None
    if kind["style"] == "glass":
        # The blocks: a saw across in red, a saw down in green, square cells.
        width = _num(comp.GetPrefs("Comp.FrameFormat.Width"), 1920.0) or 1920.0
        height = _num(comp.GetPrefs("Comp.FrameFormat.Height"), 1080.0) or 1080.0
        across = _gradient(comp, "Linear", (0.0, 0.5), (1.0 / tr.GLASS_CELLS, 0.5), repeat=True)
        down = _gradient(comp, "Linear", (0.5, 1.0), (0.5, 1.0 - width / height / tr.GLASS_CELLS), repeat=True)
        noise = [_channel_from(comp, across, down, 1, "BuddyTrBlocks")]
        noise[0].SetInput("ToGreen", 0)              # green from the down saw's red (it's grey)
    if kind["style"] == "melt":
        n = _tool(comp, "FastNoise", "BuddyTrMeltNoise", None, LockXY=0, XScale=12.0, YScale=0.6, Detail=2.0)
        _set(comp, n, "Seethe", plan["seethe"])
        noise = [n]
    if kind["style"] == "warp":
        noise = []
        for i, center in enumerate(((0.5, 0.5), (0.13, 0.71))):
            n = _tool(comp, "FastNoise", "BuddyTrWarp" + "XY"[i], None, XScale=tr.WARP_SCALE, Detail=2.0)
            n.SetInput("Center", {1: center[0], 2: center[1]})
            _set(comp, n, "Seethe", plan["seethe"])
            noise.append(n)
    tagged = None
    for side, media_in in (("a", mi1), ("b", mi2)):
        fed = list((media_in.Output.GetConnectedInputs() or {}).values())
        out, main = _bend(comp, media_in, settings, plan, side, noise)
        if max(plan["soft"]) > 0:
            soft = _tool(comp, "Blur", "BuddyTrSoft" + side.upper(), out)
            _set(comp, soft, "XBlurSize", plan["soft"])
            out = soft
        for inp in fed:
            inp.ConnectTo(out.Output)
        tagged = tagged or main
    dissolve.Mix.ConnectTo(None)
    _set(comp, dissolve, "Mix", plan["mix"])
    return tagged


def _point(comp, tool, name, xs, ys):
    """A point input held, or moved along an XYPath keyed as xs, ys say."""
    if max(xs) - min(xs) < 1e-9 and max(ys) - min(ys) < 1e-9:
        tool.SetInput(name, {1: float(xs[0]), 2: float(ys[0])})
        return
    tool.AddModifier(name, "XYPath")
    path = mr._source_tool(getattr(tool, name))
    if path is None:
        raise RuntimeError("couldn't attach a motion path")
    # Both axes keyed, even one that holds still: an XYPath's X or Y set
    # with SetInput stayed at the middle (Studio 21.1).
    mr._key(comp, path.X, _keys(xs))
    mr._key(comp, path.Y, _keys(ys))


def _rect(comp, name, xs, ys, widths, heights):
    """A Rectangle mask: middle (y up) and size, shares of the frame, per frame."""
    rect = _tool(comp, "RectangleMask", name, None)
    _point(comp, rect, "Center", xs, ys)
    _set(comp, rect, "Width", widths)
    _set(comp, rect, "Height", heights)
    return rect


def _split(comp, mi1, mi2, dissolve, settings, plan, aspect):
    """Each piece a layer - a colour card, the picture inside it, both cut
    out by Rectangle masks, motion blurred - over the clip it covers (split
    open: the incoming one, pushing in)."""
    kind = tr.KINDS[settings["kind"]]
    under = (0.0, 0.0, 0.0) if kind.get("film") else tr.rgb(settings["color"])         # a film strip's black
    x = {"a": mi2, "b": mi1}.get(kind["src"]) or _solid(comp, under, "BuddyTrUnder")
    if max(plan["push"]) - min(plan["push"]) > 1e-9:
        x = _tool(comp, "Transform", "BuddyTrPush", x, Edges=MIRROR)
        _set(comp, x, "Size", plan["push"])
    color = _solid(comp, tr.rgb(settings["color"]), "BuddyTrColor")
    clear = _solid(comp, (0, 0, 0), "BuddyTrClear", alpha=0.0)
    border = plan["border"]
    tagged = diag = None
    frames = len(border)
    for i, piece in enumerate(plan["pieces"]):
        hx, hy, hw, hh = piece["home"]
        src = mi1 if piece["src"] == "a" else mi2
        if "half" in piece:
            # Cut corner to corner before it moves: a gradient square on to
            # the line (across it in pixels), the half above or below its middle.
            if diag is None:
                k = 0.1
                diag = _gradient(comp, "Linear", (0.5 + k / aspect, 0.5 - k * aspect),
                                 (0.5 - k / aspect, 0.5 + k * aspect))
                diag.SetAttrs({"TOOLS_Name": "BuddyTrDiagonal"})
            src = _masked(comp, clear, src, diag, [0.5] * frames, [0.004] * frames, f"BuddyTrHalf{i + 1}",
                          below=piece["half"] == "lr")
        tf = _tool(comp, "Transform", NAME_A if i == 0 else f"BuddyTrPiece{i + 1}", src, Pivot={1: hx, 2: hy})
        _point(comp, tf, "Center", [0.5 + v - hx for v in piece["x"]], [0.5 + v - hy for v in piece["y"]])
        across = [z * w for z, w in zip(piece["s"], piece["sw"])]
        down = [z * h for z, h in zip(piece["s"], piece["sh"])]
        if across == down:
            _set(comp, tf, "Size", [max(mr.MIN_SIZE, z) for z in piece["s"]])
        else:                               # squashed (a squeeze)
            tf.SetInput("UseSizeAndAspect", 0)
            _set(comp, tf, "XSize", [max(mr.MIN_SIZE, z) for z in across])
            _set(comp, tf, "YSize", [max(mr.MIN_SIZE, z) for z in down])
        widths, heights = [hw * z for z in across], [hh * z for z in down]
        if "half" in piece:
            layer = tf
        else:
            card = _rect(comp, f"BuddyTrCard{i + 1}", piece["x"], piece["y"], widths, heights)
            # The line is as thick across as down: its share of the height is aspect x its share of the width.
            inner = _rect(comp, f"BuddyTrInside{i + 1}", piece["x"], piece["y"],
                          [max(0.0, w - 2 * b) for w, b in zip(widths, border)],
                          [max(0.0, h - 2 * b * aspect) for h, b in zip(heights, border)])
            layer = _tool(comp, "Merge", f"BuddyTrLine{i + 1}", None)
            layer.Background.ConnectTo(clear.Output)
            layer.Foreground.ConnectTo(color.Output)
            layer.EffectMask.ConnectTo(card.Mask)
            pic = _tool(comp, "Merge", f"BuddyTrCut{i + 1}", None)
            pic.Background.ConnectTo(layer.Output)
            pic.Foreground.ConnectTo(tf.Output)
            pic.EffectMask.ConnectTo(inner.Mask)
            layer = pic
        if settings["blur"] > 0 and max(piece["blur"]) > 0:
            grow = kind["move"] == "grow"
            dx, dy = piece["dir"]
            blur = _tool(comp, "DirectionalBlur", f"BuddyTrBlur{i + 1}", layer, Type=tr.BLUR_TYPES["zoom" if grow else "pan"],
                         Angle=math.degrees(math.atan2(dy, dx * aspect)))      # in pixels
            if grow:
                blur.SetInput("Center", {1: hx, 2: hy})
            _set(comp, blur, "Length", piece["blur"])
            layer = blur
        over = _tool(comp, "Merge", f"BuddyTrPieceOver{i + 1}", None)
        over.Background.ConnectTo(x.Output)
        over.Foreground.ConnectTo(layer.Output)
        x, tagged = over, tagged or tf
    if "hole_y" in plan:
        holes = _film_holes(comp, plan, settings["blur"] > 0)
        film = _tool(comp, "Merge", "BuddyTrFilm", None)
        film.Background.ConnectTo(x.Output)
        film.Foreground.ConnectTo(holes.Output)
        x = film
    dissolve.Background.ConnectTo(x.Output)
    dissolve.Mix.ConnectTo(None)
    dissolve.SetInput("Mix", 0.0)
    return tagged


def _film_holes(comp, plan, blur):
    """A film roll's sprocket holes, on clear: a saw down the frame (a
    Repeat gradient, a hole where it's low), cut to a column each side,
    moving and shrinking with the strip - smeared as the frames are."""
    n = len(plan["hole_y"])
    saw = _gradient(comp, "Linear", (0.5, 1.0), (0.5, 0.9), repeat=True)
    saw.SetAttrs({"TOOLS_Name": "BuddyTrPerfs"})
    _point(comp, saw, "Start", [0.5] * n, plan["hole_y"])
    _point(comp, saw, "End", [0.5] * n, [y - h for y, h in zip(plan["hole_y"], plan["hole_p"])])
    clear = _solid(comp, (0, 0, 0), "BuddyTrPerfClear", alpha=0.0)
    rows = _masked(comp, clear, _solid(comp, tr.FILM_HOLE, "BuddyTrPerfLight"), saw, [tr.FILM_DUTY] * n,
                   [0.02] * n, "BuddyTrPerfRows")
    x = clear
    for side, name in ((-1, "Left"), (1, "Right")):
        column = _rect(comp, "BuddyTrPerf" + name, [0.5 + side * v for v in plan["hole_x"]], [0.5] * n,
                       plan["hole_w"], [1.0] * n)
        cut = _tool(comp, "Merge", "BuddyTrPerfCut" + name, None)
        cut.Background.ConnectTo(x.Output)
        cut.Foreground.ConnectTo(rows.Output)
        cut.EffectMask.ConnectTo(column.Mask)
        x = cut
    smear = plan["pieces"][0]["blur"]
    if blur and max(smear) > 0:
        x = _tool(comp, "DirectionalBlur", "BuddyTrPerfBlur", x, Type=tr.BLUR_TYPES["pan"], Angle=90.0)
        _set(comp, x, "Length", smear)
    return x


def _strip(comp, source, y, h, name):
    """A level strip of `source` (middle y, height h: shares, y up), clear elsewhere."""
    mask = _tool(comp, "RectangleMask", name + "Mask", None, Center={1: 0.5, 2: y}, Width=1.0, Height=h)
    cut = _tool(comp, "Merge", name, None)
    cut.Background.ConnectTo(_solid(comp, (0, 0, 0), name + "Clear", alpha=0.0).Output)
    cut.Foreground.ConnectTo(source.Output)
    cut.EffectMask.ConnectTo(mask.Mask)
    return cut


def _louvers(comp, mi1, mi2, dissolve, settings, plan):
    """Slats over the colour: each slat of the outgoing clip flipping over
    on its own level axis, and the incoming clip's on its back."""
    persp = plan["persp"][0]
    x = _solid(comp, tr.rgb(settings["color"]), "BuddyTrColor")
    tagged = None
    h = 1.0 / tr.LOUVERS
    for i in range(tr.LOUVERS):
        y = 1 - (i + 0.5) * h
        for side, media_in in (("a", mi1), ("b", mi2)):
            name = NAME_A if (i, side) == (0, "a") else f"BuddyTrSlat{i + 1}{side.upper()}"
            strip = _strip(comp, media_in, y, h, name + "Cut")
            dve = _tool(comp, "DVE", name, strip, Pivot={1: 0.5, 2: y}, Perspective=persp)
            _set(comp, dve, "XRotation", [row[i] for row in plan["rot_" + side]])
            if settings["blur"] > 0:
                dve.SetInput("MotionBlur", 1)
                dve.SetInput("Quality", 6.0)
                dve.SetInput("ShutterAngle", 360.0 * settings["blur"])
            over = _tool(comp, "Merge", name + "Over", None)
            over.Background.ConnectTo(x.Output)
            over.Foreground.ConnectTo(dve.Output)
            _set(comp, over, "Blend", [row[i] for row in plan["show_" + side]])
            x, tagged = over, tagged or dve
    dissolve.Background.ConnectTo(x.Output)
    dissolve.Mix.ConnectTo(None)
    dissolve.SetInput("Mix", 0.0)
    return tagged


def _turn(comp, mi1, mi2, dissolve, settings, plan, aspect):
    """Each picture turned in a DVE over the colour (a flip, a cube) or
    the outgoing one swung off the incoming one, which brightens as it's
    uncovered (a door, a fall)."""
    kind = tr.KINDS[settings["kind"]]
    persp = plan["persp"][0]
    rig = tr.turn_rig(settings["kind"], persp, aspect)
    axis = "YRotation" if kind["axis"] == "y" else "XRotation"
    if kind["style"] == "louver":
        return _louvers(comp, mi1, mi2, dissolve, settings, plan)
    if kind["style"] == "page":
        # Under the page: the outgoing clip's left half on the incoming one;
        # the page: the outgoing right half, then (its back) the incoming left.
        def half(source, left, name):
            mask = _tool(comp, "RectangleMask", name + "Half", None, Center={1: 0.25 if left else 0.75, 2: 0.5},
                         Width=0.5, Height=1.0)
            cut = _tool(comp, "Merge", name, None)
            cut.Background.ConnectTo(_solid(comp, (0, 0, 0), name + "Clear", alpha=0.0).Output)
            cut.Foreground.ConnectTo(source.Output)
            cut.EffectMask.ConnectTo(mask.Mask)
            return cut
        x = _tool(comp, "Merge", "BuddyTrUnder", None)
        x.Background.ConnectTo(mi2.Output)
        x.Foreground.ConnectTo(half(mi1, True, "BuddyTrLeftA").Output)
        sides = (("a", half(mi1, False, "BuddyTrRightA")), ("b", half(mi2, True, "BuddyTrLeftB")))
    elif kind["style"] == "door":
        x = _tool(comp, "BrightnessContrast", "BuddyTrShade", mi2)
        _set(comp, x, "Gain", plan["shade"])
        sides = (("a", mi1),)
    else:
        x = _solid(comp, tr.rgb(settings["color"]), "BuddyTrColor")
        sides = (("a", mi1), ("b", mi2))
    tagged = None
    for side, media_in in sides:
        dve = _tool(comp, "DVE", NAME_A if side == "a" else NAME_B, media_in,
                    Pivot={1: rig["pivot"][0], 2: rig["pivot"][1]}, ZPivot=rig["zpivot"], ZMove=rig["zmove"],
                    Perspective=persp)
        _set(comp, dve, axis, plan["rot_" + side])
        _set(comp, dve, "ZMove", [rig["zmove"] * v for v in plan["pull"]])
        if settings["blur"] > 0:
            # DVE's own motion blur, from its keys: the shutter opens with Blur.
            dve.SetInput("MotionBlur", 1)
            dve.SetInput("Quality", 6.0)
            dve.SetInput("ShutterAngle", 360.0 * settings["blur"])
        top = dve
        if kind["style"] == "page":
            top = _tool(comp, "BrightnessContrast", "BuddyTrPageShade" + side.upper(), dve)
            _set(comp, top, "Gain", plan["shade"])
        over = _tool(comp, "Merge", "BuddyTrOver" + side.upper(), None)
        over.Background.ConnectTo(x.Output)
        over.Foreground.ConnectTo(top.Output)
        _set(comp, over, "Blend", plan["show_" + side])
        x, tagged = over, tagged or dve
    dissolve.Background.ConnectTo(x.Output)
    dissolve.Mix.ConnectTo(None)
    dissolve.SetInput("Mix", 0.0)
    return tagged


def build(transition, settings):
    """A placed carrier transition's comp rebuilt as Buddy's `settings`."""
    comp = transition.GetFusionCompByIndex(1)
    if comp is None:
        raise RuntimeError("Resolve gave the transition no Fusion composition")
    mi1, mi2, dissolve = comp.FindTool("MediaIn1"), comp.FindTool("MediaIn2"), comp.FindTool("Dissolve1")
    if mi1 is None or mi2 is None or dissolve is None:
        raise RuntimeError("the transition's composition isn't the one Buddy builds on")
    attrs = comp.GetAttrs() or {}
    start, end = attrs.get("COMPN_RenderStart"), attrs.get("COMPN_RenderEnd")
    frames = int(round(end - start)) + 1 if start is not None and end is not None else int(transition.GetDuration())
    settings = tr.clean(settings)
    width = _num(comp.GetPrefs("Comp.FrameFormat.Width"), 1920.0) or 1920.0
    height = _num(comp.GetPrefs("Comp.FrameFormat.Height"), 1080.0) or 1080.0
    plan = tr.plan(settings, frames, width / height)
    comp.Lock()
    comp.StartUndo("Buddy transition")
    try:
        if plan.get("group") == "light":
            light = _light_source(comp, settings, plan)
            fx = tr.KINDS[settings["kind"]].get("fx")
            mode = "Normal" if tr.KINDS[settings["kind"]]["light"] == "dip" else "Screen"     # a dip covers, not lights
            axis = tr.KINDS[settings["kind"]].get("axis")
            a = _light_side(comp, mi1, plan, light, NAME_A, fx, mode, axis)
            _light_side(comp, mi2, plan, light, NAME_B, fx, mode, axis)
            dissolve.Mix.ConnectTo(None)
            _set(comp, dissolve, "Mix", plan["mix"])
            a.SetData(TAG, json.dumps(settings))
            return
        if plan.get("group") == "wipe":
            a = _wipe(comp, mi1, mi2, dissolve, settings, plan)
            a.SetData(TAG, json.dumps(settings))
            return
        if plan.get("group") == "glitch":
            a = _glitch(comp, mi1, mi2, dissolve, settings, plan)
            a.SetData(TAG, json.dumps(settings))
            return
        if plan.get("group") == "paper":
            style = tr.KINDS[settings["kind"]]["style"]
            make = {"tear": _tear, "burn": _burn, "grunge": _grunge}.get(style, _wipe)
            a = make(comp, mi1, mi2, dissolve, settings, plan)
            a.SetData(TAG, json.dumps(settings))
            return
        if plan.get("group") == "distort":
            a = _distort(comp, mi1, mi2, dissolve, settings, plan)
            a.SetData(TAG, json.dumps(settings))
            return
        if plan.get("group") == "split":
            a = _split(comp, mi1, mi2, dissolve, settings, plan, width / height)
            a.SetData(TAG, json.dumps(settings))
            return
        if plan.get("group") == "turn":
            a = _turn(comp, mi1, mi2, dissolve, settings, plan, width / height)
            a.SetData(TAG, json.dumps(settings))
            return
        a = _side(comp, mi1, plan["a"], settings, plan["blur_type"], plan["blur_angle"], NAME_A)
        _side(comp, mi2, plan["b"], settings, plan["blur_type"], plan["blur_angle"], NAME_B)
        dissolve.Mix.ConnectTo(None)
        cut = plan["cut"]
        mr._key(comp, dissolve.Mix, {0: {"value": 0.0}, max(0, cut - 1): {"value": 0.0},
                                    cut: {"value": 1.0}, frames - 1: {"value": 1.0}})
        a.SetData(TAG, json.dumps(tr.clean(settings)))
    finally:
        comp.EndUndo(True)
        comp.Unlock()


def put_on(timeline, cut, settings):
    """Buddy's transition on one cut (cuts()), in place of whatever was
    there. Returns its length in frames. Raises with a reason."""
    left, _right, existing = cut["items"]
    want = max(MIN_FRAMES, int(round(settings["length"] * _fps(timeline))))
    fitted = fit(want, cut["room"])
    if fitted is None:
        raise RuntimeError("the clips have no unused frames past the cut to make a transition from")
    if existing is not None and not timeline.DeleteClips([existing], False):
        raise RuntimeError("Resolve wouldn't take off the transition that's there")
    frames, alignment = fitted
    placed = left.AddTransition({"type": CARRIER, "category": "fusion", "position": "end",
                                 "alignment": alignment, "duration": frames})
    if not placed:
        raise RuntimeError("Resolve wouldn't put a transition there"
                           + (" – the one that was there has been taken off" if existing is not None else ""))
    try:
        build(placed, settings)
    except Exception:
        timeline.DeleteClips([placed], False)
        raise
    return frames


def run_apply(controller, cut_ids, settings):
    """Apply: `settings` on the selection's cuts named in cut_ids. Returns
    {"applied", "shorter" (cuts that got less than asked), "failed":
    [(where, why)]}."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        raise RuntimeError("There's no timeline open in Resolve.")
    settings = tr.clean(settings)
    want = max(MIN_FRAMES, int(round(settings["length"] * _fps(timeline))))
    applied, shorter, failed = 0, 0, []
    for cut in cuts(timeline):
        if cut["id"] not in cut_ids:
            continue
        try:
            frames = put_on(timeline, cut, settings)
            applied += 1
            shorter += frames < want
        except Exception as exc:  # noqa: BLE001 - one cut's trouble is reported, the rest go on
            failed.append((f"{cut['left']} → {cut['right']}", str(exc)))
    return {"applied": applied, "shorter": shorter, "failed": failed}


def run_remove(controller, cut_ids):
    """Takes the transitions off the selection's cuts named in cut_ids.
    Returns how many came off."""
    _project, timeline = mr.current_timeline(controller)
    if timeline is None:
        raise RuntimeError("There's no timeline open in Resolve.")
    gone = [c["items"][2] for c in cuts(timeline) if c["id"] in cut_ids and c["items"][2] is not None]
    if gone and not timeline.DeleteClips(gone, False):
        raise RuntimeError("Resolve wouldn't take the transitions off")
    return len(gone)
