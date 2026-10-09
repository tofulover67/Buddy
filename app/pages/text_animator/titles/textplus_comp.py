"""A composed title as a Fusion comp - one Text+ per word, merged in order into
MediaOut1 - written as the text of a .comp file. A clip takes it with
ImportFusionComp, and the Edit page renders an imported comp straight away (nodes
built through the API don't render until they've been through an export and import
anyway, motion_resolve.py). Pure: no Resolve, so it's tested on its own.

Where each word goes, from the composer's box (frame px, top-down):
  Size     the letter size over Text+'s px per Size (canvas_math: ascent + descent
           = 0.8 x Size x width, so em px = Size x width x 0.8 / (ascent + descent))
  Center   across: the middle of the word's advance (Text+ centres a line on its
           advance); down: the baseline less the face's baseline offset below the
           Center (canvas_math.baseline, measured in Resolve), flipped to Fusion's
           bottom-up y
  AngleZ   the box's rot negated (Fusion turns counter-clockwise)

How a look's style maps onto Text+'s shading elements (measured on Studio 21.1,
2026-10-05, from rendered bounds - see docs/RESOLVE_API_GUIDE.md):
  fill      element 1, Red1/Green1/Blue1/Alpha1
  gradient  element 1, Type1 = 2 with ShadingGradient1, mapped per word
            (ShadingMappingLevel1 = 3); Fusion's angle runs the other way round
  strokes   outer ones become outlines on elements 2, 5, 6... (OutsideOnly); an
            outline reaches OUTLINE_REACH x Thickness x Size x width px, whatever
            the font
  shadows   element 3: Offset = distance / (Size x width) along the angle (y up),
            softness from the blur
  bg        element 4, the box behind the word

A kinetic title (kinetic.py) is keyed on each Text+ itself: its Size, Center (a path) and
AngleZ on every frame the camera or the word moves - the camera's zoom and turn and the
word's own entrance together - so the letters stay sharp however far the camera zooms.
Text+ scales its outlines, shadows and letter spacing with Size, so the style's inputs
are the same as a still word's. A word's copies are keyed the same way, offset from it.
"""

import json
import math

from .. import canvas_math as cm
from . import kinetic
from . import looks as L

TAG = "BuddyTitle"                  # CustomData key on the comp: the title's id
OUTLINE_REACH = 0.716               # outline px per (Thickness x Size x width), any font
SOFTNESS_PER_BLUR = 55.0            # shadow Softness per blur (share of the letter size) - by eye, 21.1
BLUR_SIZE_PER_PX = 0.5              # Blur's XBlurSize per px of the preview's blur radius - by eye, 21.1
GRADIENT_PER_WORD = 3               # ShadingMappingLevel: 0 frame, 1 text, 2 line, 3 word, 4 character
_OUTLINE_ELEMENTS = (2, 5, 6, 7, 8)


def _rgba(value, fallback="#ffffff"):
    """(r, g, b, a) in 0..1 from "#rrggbb", "#rrggbbaa" or "rgba(r, g, b, a)"."""
    text = str(value or fallback).strip()
    if text.startswith("rgb"):
        parts = [float(p) for p in text[text.index("(") + 1:text.index(")")].split(",")]
        return parts[0] / 255, parts[1] / 255, parts[2] / 255, parts[3] if len(parts) > 3 else 1.0
    text = text.lstrip("#")
    if len(text) not in (6, 8):
        return _rgba(fallback, "#ffffff")
    rgb = [int(text[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return (*rgb, int(text[6:8], 16) / 255 if len(text) == 8 else 1.0)


def _num(v):
    return f"{v:.6g}"


class _Lua(str):
    """A value already written as Lua (a Gradient table): never quoted."""


def _input(name, value):
    if isinstance(value, _Lua):
        return f"{name} = Input {{ Value = {value}, }},"
    if isinstance(value, str):
        # The text as it is: Lua reads a JSON string's \" \\ \n, but not \uXXXX.
        return f"{name} = Input {{ Value = {json.dumps(value, ensure_ascii=False)}, }},"
    if isinstance(value, (tuple, list)):
        return f"{name} = Input {{ Value = {{ {', '.join(_num(v) for v in value)} }}, }},"
    return f"{name} = Input {{ Value = {_num(value)}, }},"


def text_plus_size(box, frame_w):
    """The Text+ Size that draws the box's letters at its size."""
    family, style = box["font"]
    return box["size"] / (cm._px_per_size(family, style) * frame_w)


def text_plus_center(box, frame):
    """The Text+ Center (Fusion's: 0..1, y up) that puts the word where the box says."""
    fw, fh = frame
    family, style = box["font"]
    cx = (box["x"] + box["w"] / 2) / fw
    cy = (box["baseline"] - cm.baseline(family, style) * box["size"]) / fh
    return cx, 1 - cy


def _colour(n, value, fallback="#ffffff"):
    r, g, b, a = _rgba(value, fallback)
    return [(f"Red{n}", r), (f"Green{n}", g), (f"Blue{n}", b), (f"Alpha{n}", a)]


def _gradient(gradient):
    stops = ", ".join(f"[{float(at):.6g}] = {{ {', '.join(_num(c) for c in _rgba(col))} }}"
                      for at, col in gradient.get("stops") or [])
    return _Lua(f"Gradient {{ Colors = {{ {stops} }} }}")


def shading(style, em_px, size_share):
    """The Text+ inputs for a role style: [(name, value)]. em_px: the word's letter
    size; size_share: its Size x frame width - the px Text+'s outline widths and
    shadow offsets scale by."""
    inputs = []
    gradient = style.get("gradient")
    if gradient and gradient.get("stops"):
        inputs += [("Type1", 2), ("ShadingGradient1", _gradient(gradient)),
                   ("ShadingMappingAngle1", (-float(gradient.get("angle", 90))) % 360),
                   ("ShadingMappingLevel1", GRADIENT_PER_WORD)]
    elif style.get("fill", "#ffffff") == "none":
        inputs.append(("Enabled1", 0))                 # a hollow word: its strokes only
    else:
        inputs += _colour(1, style.get("fill"), "#ffffff")
    free = list(_OUTLINE_ELEMENTS)
    # Outer strokes stack outward, each adding its reach (a share of the letter size);
    # Text+ draws them outside the letters only.
    reach = 0.0
    for stroke in [s for s in style.get("strokes") or [] if s.get("pos", "outer") == "outer"]:
        if not free:
            break
        n = free.pop(0)
        reach += float(stroke.get("w", 0.0))
        thickness = reach * em_px / (OUTLINE_REACH * size_share)
        inputs += [(f"Enabled{n}", 1), (f"ElementShape{n}", 1), (f"OutsideOnly{n}", 1),
                   (f"Thickness{n}", thickness)] + _colour(n, stroke.get("colour"), "#000000")
    # A centre stroke straddles the edge: Text+'s outline without OutsideOnly reaches
    # as far in as out, so it's half the stroke's width each way.
    for stroke in [s for s in style.get("strokes") or [] if s.get("pos") == "center"]:
        if not free:
            break
        n = free.pop(0)
        thickness = float(stroke.get("w", 0.0)) / 2 * em_px / (OUTLINE_REACH * size_share)
        inputs += [(f"Enabled{n}", 1), (f"ElementShape{n}", 1), (f"OutsideOnly{n}", 0),
                   (f"Thickness{n}", thickness)] + _colour(n, stroke.get("colour"), "#ffffff")
    shadows = style.get("shadows") or []
    for shadow in shadows[:1]:
        angle = math.radians(float(shadow.get("angle", 180)))
        dist = float(shadow.get("dist", 0.05)) * em_px / size_share
        soft = float(shadow.get("blur", 0.1)) * SOFTNESS_PER_BLUR
        inputs += [("Enabled3", 1), ("Offset3", (math.sin(angle) * dist, math.cos(angle) * dist)),
                   ("SoftnessX3", soft), ("SoftnessY3", soft)] + _colour(3, shadow.get("colour"), "#000000")
    if not shadows:
        # A glow is element 3 with no offset: a soft copy of the letters behind them.
        for glow in (style.get("glows") or [])[:1]:
            soft = float(glow.get("blur", 0.3)) * SOFTNESS_PER_BLUR
            r, g, b, _a = _rgba(glow.get("colour"), "#ffffff")
            inputs += [("Enabled3", 1), ("Offset3", (0.0, 0.0)), ("SoftnessX3", soft), ("SoftnessY3", soft),
                       ("Red3", r), ("Green3", g), ("Blue3", b), ("Alpha3", min(1.0, float(glow.get("strength", 1.0))))]
    return inputs


def _tool(name, box, style, frame, pos, comp_frames, wired=None):
    """A word's Text+. wired: {input: tool} - inputs keyed by another tool (a spline or
    path) instead of set."""
    fw, fh = frame
    family, font_style = box["font"]
    size = text_plus_size(box, fw)
    inputs = [("GlobalOut", comp_frames - 1), ("Width", fw), ("Height", fh), ("UseFrameFormatSettings", 1),
              ("StyledText", box["text"]), ("Font", family), ("Style", font_style), ("Size", size),
              ("Center", text_plus_center(box, frame)),
              ("VerticalJustificationNew", 3), ("HorizontalJustificationNew", 3)]
    if box.get("rot") or (wired and "AngleZ" in wired):
        inputs.append(("AngleZ", -float(box.get("rot") or 0.0)))
    if box.get("track"):
        # Each step between letters grows by (CharacterSpacing - 1) x Size x width, the
        # same for every letter (measured on Studio 21.1).
        inputs.append(("CharacterSpacing", 1.0 + float(box["track"]) / (size * fw)))
    inputs += shading(style, box["size"], size * fw)
    wired = wired or {}
    body = "\n".join(f"\t\t\t\t{_wired(k, wired[k]) if k in wired else _input(k, v)}" for k, v in inputs)
    return (f"\t\t{name} = TextPlus {{\n\t\t\tInputs = {{\n{body}\n\t\t\t}},\n"
            f"\t\t\tViewInfo = OperatorInfo {{ Pos = {{ {pos[0]}, {pos[1]} }} }},\n\t\t}},")


def _spline(name, points):
    """A BezierSpline keyed at (frame, value) points, straight between them (handles a
    third of the way along, Linear) - so Fusion plays exactly the sampled values."""
    rows = []
    for n, (f, v) in enumerate(points):
        parts = [_num(v)]
        if n > 0:
            pf, pv = points[n - 1]
            parts.append(f"LH = {{ {_num(f - (f - pf) / 3)}, {_num(v - (v - pv) / 3)} }}")
        if n < len(points) - 1:
            nf, nv = points[n + 1]
            parts.append(f"RH = {{ {_num(f + (nf - f) / 3)}, {_num(v + (nv - v) / 3)} }}")
        parts.append("Flags = { Linear = true }")
        rows.append(f"\t\t\t\t[{int(f)}] = {{ {', '.join(parts)} }},")
    return (f"\t\t{name} = BezierSpline {{\n\t\t\tSplineColor = {{ Red = 255, Green = 128, Blue = 0 }},\n"
            f"\t\t\tNameSet = true,\n\t\t\tKeyFrames = {{\n" + "\n".join(rows) + "\n\t\t\t}\n\t\t},")


def _wired(name, source):
    return f"{name} = Input {{ SourceOp = \"{source}\", Source = \"Value\", }},"


def _block(name, kind, inputs, pos, extra=""):
    body = "\n".join(f"\t\t\t\t{x}" for x in inputs)
    return (f"\t\t{name} = {kind} {{\n{extra}\t\t\tInputs = {{\n{body}\n\t\t\t}},\n"
            f"\t\t\tViewInfo = OperatorInfo {{ Pos = {{ {pos[0]}, {pos[1]} }} }},\n\t\t}},")


def _feed(name, source):
    return f"{name} = Input {{ SourceOp = \"{source}\", Source = \"Output\", }},"


def _clear(name, frame, comp_frames, pos):
    """A transparent Background the size of the frame: a canvas to merge onto."""
    fw, fh = frame
    return _block(name, "Background", [_input("GlobalOut", comp_frames - 1), _input("Width", fw), _input("Height", fh),
                                       _input("UseFrameFormatSettings", 1), _input("TopLeftAlpha", 0)], pos)


def _merge(name, background, foreground, pos, extra=()):
    return _block(name, "Merge", [_feed("Background", background), _feed("Foreground", foreground), *extra,
                                  _input("PerformDepthMerge", 0)], pos)


def _path(name, xs, ys, pos):
    """An XYPath keyed from (frame, value) points for x and y - a Point input's motion."""
    return [_spline(f"{name}X", xs), _spline(f"{name}Y", ys),
            _block(name, "XYPath", [_wired("X", f"{name}X"), _wired("Y", f"{name}Y")], pos,
                   "\t\t\tShowKeyPoints = false,\n\t\t\tDrawMode = \"ModifyOnly\",\n")]


def _at(points, f):
    """A keyed channel's value at frame f - straight between its keys, as Fusion plays them."""
    if f <= points[0][0]:
        return points[0][1]
    for (f0, v0), (f1, v1) in zip(points, points[1:]):
        if f <= f1:
            return v0 + (v1 - v0) * (f - f0) / (f1 - f0)
    return points[-1][1]


def _copy_keys(keys):
    """(frame, spread, pulse) on every frame either is keyed (timing.keys), or []."""
    from . import timing
    channels = ("spread", "pulse")
    frames = sorted({f for c in channels for f, _v in keys.get(c, [])})
    return [(f, *(_at(keys[c], f) if c in keys else timing.REST[c] for c in channels)) for f in frames]


def _with_copies(i, box, style, frame, comp_frames, pos, keys=None):
    """A word with copies (echo, extrude, long shadow): the copies merged onto a canvas
    of the word's own - farthest first, each at its offset and fade - then the word on
    top. ([tool text], the last tool). The word's animation then moves them together.
    keys: the word's keys (timing.keys) - copies that fan out (and breathe) have their
    Merge's Center and Blend keyed from its spread and pulse, frame by frame."""
    from . import timing
    fw, fh = frame
    em = box["size"]
    tools = [_clear(f"Word{i}Base", frame, comp_frames, (pos[0], pos[1] - 33))]
    last = f"Word{i}Base"
    moves = _copy_keys(keys or {})
    for g, group in enumerate(style.get("copies") or []):
        copy_style = {"fill": group.get("fill") or "none"}
        if group.get("stroke"):
            copy_style["strokes"] = [{**group["stroke"], "pos": "center"}]
        source = f"Word{i}Copy{g}"
        tools.append(_tool(source, box, copy_style, frame, (pos[0] - 55, pos[1] - 33), comp_frames))
        n = int(group.get("n", 3))
        dx, dy = (float(v) * em for v in group.get("step", (0.0, -0.5)))
        alpha, fade = float(group.get("alpha", 1.0)), float(group.get("fade", 1.0))
        for k in range(n, 0, -1):
            a = alpha * fade ** (k - 1)
            if a <= 0.003:
                continue
            for side, tag in (((1, "a"), (-1, "b")) if group.get("both") else ((1, "a"),)):
                name = f"Word{i}Copy{g}_{k}{tag}"
                if moves and group.get("anim") == "spread":
                    at = [(f, timing.copy_at(group, k, sp, pu)) for f, sp, pu in moves]
                    tools += _path(f"{name}Path", [(f, 0.5 + side * k * dx * o / fw) for f, (o, _s) in at],
                                   [(f, 0.5 - side * k * dy * o / fh) for f, (o, _s) in at], (pos[0] - 55, pos[1] - 50))
                    tools.append(_spline(f"{name}Blend", [(f, a * shown) for f, (_o, shown) in at]))
                    placed = [_wired("Center", f"{name}Path"), _wired("Blend", f"{name}Blend")]
                else:
                    placed = [_input("Center", (0.5 + side * k * dx / fw, 0.5 - side * k * dy / fh)),
                              _input("Blend", a)]
                tools.append(_merge(name, last, source, (pos[0], pos[1] - 33), placed))
                last = name
    tools.append(_merge(f"Word{i}On", last, f"Word{i}", (pos[0], pos[1] - 16)))
    return tools, f"Word{i}On"


def _bar_tools(k, bar, frame, comp_frames, background, blend):
    """A line's bar: a Background in its colour, masked to a rectangle, merged under the
    words - faded with its line's first word (blend: that word's Blend wiring, or None)."""
    fw, fh = frame
    r, g, b, a = _rgba(bar.get("colour"), "#000000")
    center = ((bar["x"] + bar["w"] / 2) / fw, 1 - (bar["y"] + bar["h"] / 2) / fh)
    mask = _block(f"Bar{k}Mask", "RectangleMask", [_input("Center", center), _input("Width", bar["w"] / fw),
                                                   _input("Height", bar["h"] / fh)], (0, 132 + 33 * k))
    fill = _block(f"Bar{k}", "Background", [_input("GlobalOut", comp_frames - 1), _input("Width", fw),
                                            _input("Height", fh), _input("UseFrameFormatSettings", 1),
                                            _input("TopLeftRed", r), _input("TopLeftGreen", g),
                                            _input("TopLeftBlue", b), _input("TopLeftAlpha", a),
                                            f"EffectMask = Input {{ SourceOp = \"Bar{k}Mask\", Source = \"Mask\", }},"],
                  (55, 132 + 33 * k))
    on = _merge(f"Bar{k}On", background, f"Bar{k}", (110, 132 + 33 * k), [blend] if blend else [])
    return [mask, fill, on], f"Bar{k}On"


def _motion(i, box, frame, keys, upstream, pos):
    """The word's animation after its Text+: a Transform (scale about the word's own
    centre, the move along a path) and a Blur when it blurs - ([tool text], the last
    tool). keys: timing.keys - {channel: [(frame, value)]}."""
    fw, fh = frame
    tools, last = [], upstream
    if any(c in keys for c in ("scale", "dx", "dy", "rot")):
        pivot = ((box["x"] + box["w"] / 2) / fw, 1 - (box["y"] + box["h"] / 2) / fh)
        inputs = [_feed("Input", last), _input("Pivot", pivot)]
        if "scale" in keys:
            tools.append(_spline(f"Move{i}Size", keys["scale"]))
            inputs.append(_wired("Size", f"Move{i}Size"))
        if "rot" in keys:
            tools.append(_spline(f"Move{i}Angle", [(f, -v) for f, v in keys["rot"]]))
            inputs.append(_wired("Angle", f"Move{i}Angle"))
        if "dx" in keys or "dy" in keys:
            # Fusion's Center: 0.5 is where the word is; x a share of the width, y up.
            tools += _path(f"Move{i}Path", [(f, 0.5 + v * fh / fw) for f, v in keys.get("dx", [(0, 0.0)])],
                           [(f, 0.5 - v) for f, v in keys.get("dy", [(0, 0.0)])], (pos[0], pos[1] + 20))
            inputs.append(_wired("Center", f"Move{i}Path"))
        tools.append(_block(f"Move{i}", "Transform", inputs, (pos[0], pos[1] + 33)))
        last = f"Move{i}"
    if "blur" in keys:
        tools.append(_spline(f"Blur{i}Size", [(f, v * box["size"] * BLUR_SIZE_PER_PX) for f, v in keys["blur"]]))
        tools.append(_block(f"Blur{i}", "Blur", [_feed("Input", last), _wired("XBlurSize", f"Blur{i}Size")],
                            (pos[0], pos[1] + 50)))
        last = f"Blur{i}"
    return tools, last


# ---------------------------------------------------------------- kinetic --

def _pose(box, state, frame):
    """Where a word's Text+ goes for a box (in the frame's px) and its timing.state:
    (Size, Center x, Center y - Fusion's: 0..1, y up - AngleZ). The state scales the
    word about its own centre, moves it and turns it, as render.py draws it; the Text+
    turns about its own Center, so that point is found turned about the box's centre."""
    fw, fh = frame
    s = float(state.get("scale", 1.0))
    cx = box["x"] + box["w"] / 2 + float(state.get("dx", 0.0)) * fh
    cy = box["y"] + box["h"] / 2 + float(state.get("dy", 0.0)) * fh
    size = box["size"] * s
    family, style = box["font"]
    # The Text+ Center unturned: across, the middle of the advance (the box's centre);
    # down, the baseline less the face's offset.
    ty = cy + (box["baseline"] - (box["y"] + box["h"] / 2)) * s - cm.baseline(family, style) * size
    rot = float(box.get("rot", 0.0)) + float(state.get("rot", 0.0))
    r = math.radians(rot)
    px, py = cx - math.sin(r) * (ty - cy), cy + math.cos(r) * (ty - cy)
    return size / (cm._px_per_size(family, style) * fw), px / fw, 1 - py / fh, -rot


def _straight(points):
    """(frame, value) keys less those in the middle of a straight run."""
    if len(points) < 3:
        return points
    kept = [points[0]]
    for prev, cur, nxt in zip(points, points[1:], points[2:]):
        if abs((cur[1] - prev[1]) - (nxt[1] - cur[1])) > 1e-7 or cur[0] - prev[0] != nxt[0] - cur[0]:
            kept.append(cur)
    kept.append(points[-1])
    return kept


def _kinetic_frames(word, camera, fps, comp_frames):
    """Every frame the word or the camera moves on."""
    from . import timing
    spans = [(0.0, camera[-1]["t"] if camera else 0.0), (word["start"], word["start"] + word["len"])]
    for span in (word.get("spread"), word.get("pulse")):
        if span:
            spans.append((span["start"], span["start"] + span["len"]))
    if word.get("exit"):
        spans.append((word["exit"]["at"], word["exit"]["at"] + word["exit"]["len"]))
    picked = set()
    for a, b in spans:
        first, last = int(math.floor(a * fps)) - 1, int(math.ceil(b * fps)) + 1
        picked.update(f for f in range(first, last + 1) if 0 <= f < comp_frames)
    return sorted(picked) or [0]


def _kinetic_word(i, box, style, frame, comp_frames, pos, word, camera, fps):
    """A kinetic word: its Text+ (and its copies') keyed on every frame it or the camera
    moves, all on a canvas of the word's own - ([tool text], the last tool, its Blend
    keys or None)."""
    from . import timing
    fw, fh = frame
    frames = _kinetic_frames(word, camera, fps, comp_frames)
    samples = []
    for f in frames:
        t = f / fps
        sbox = kinetic.screen_box(box, kinetic.camera_at(camera, t), frame)
        samples.append((f, sbox, timing.state(word, t)))
    tools = [_clear(f"Word{i}Base", frame, comp_frames, (pos[0], pos[1] - 33))]
    last = f"Word{i}Base"

    def keyed(name, poses, offset=None, src_style=None):
        """A Text+ keyed along poses [(frame, (size, x, y, angle))]: (tool texts)."""
        made = [_spline(f"{name}Size", _straight([(f, p[0]) for f, p in poses])),
                _spline(f"{name}Angle", _straight([(f, p[3]) for f, p in poses]))]
        made += _path(f"{name}Path", _straight([(f, p[1]) for f, p in poses]), _straight([(f, p[2]) for f, p in poses]),
                      (pos[0] - 55, pos[1] - 50))
        rest = samples[-1][1]
        made.append(_tool(name, rest, src_style if src_style is not None else style, frame, pos, comp_frames,
                          wired={"Size": f"{name}Size", "AngleZ": f"{name}Angle", "Center": f"{name}Path"}))
        return made

    poses = [(f, _pose(sb, st, frame)) for f, sb, st in samples]
    for g, group in enumerate(style.get("copies") or []):
        copy_style = {"fill": group.get("fill") or "none"}
        if group.get("stroke"):
            copy_style["strokes"] = [{**group["stroke"], "pos": "center"}]
        n = int(group.get("n", 3))
        sx, sy = (float(v) for v in group.get("step", (0.0, -0.5)))
        alpha, fade = float(group.get("alpha", 1.0)), float(group.get("fade", 1.0))
        for k in range(n, 0, -1):
            a = alpha * fade ** (k - 1)
            if a <= 0.003:
                continue
            for side, tag in (((1, "a"), (-1, "b")) if group.get("both") else ((1, "a"),)):
                name = f"Word{i}Copy{g}_{k}{tag}"
                cposes, blends = [], []
                for (f, sb, st), (_f, p) in zip(samples, poses):
                    out, shown = timing.copy_at(group, k, st.get("spread", 1.0), st.get("pulse", 0.0))
                    size = sb["size"] * float(st.get("scale", 1.0))
                    # The copies step in the frame's own directions, a share of the word's size now.
                    cposes.append((f, (p[0], p[1] + side * k * sx * size * out / fw, p[2] - side * k * sy * size * out / fh, p[3])))
                    blends.append((f, a * shown))
                tools += keyed(name, cposes, src_style=copy_style)
                if any(abs(b - blends[0][1]) > 1e-6 for _f, b in blends):      # fanning out: its fade keyed
                    tools.append(_spline(f"{name}Blend", _straight(blends)))
                    placed = [_wired("Blend", f"{name}Blend")]
                else:
                    placed = [_input("Blend", a)]
                tools.append(_merge(f"{name}On", last, name, (pos[0], pos[1] - 33), placed))
                last = f"{name}On"
    tools += keyed(f"Word{i}", poses)
    tools.append(_merge(f"Word{i}On", last, f"Word{i}", (pos[0], pos[1] - 16)))
    last = f"Word{i}On"
    blur = [(f, float(st.get("blur", 0.0)) * sb["size"] * BLUR_SIZE_PER_PX) for f, sb, st in samples]
    if any(v > 1e-6 for _f, v in blur):
        tools.append(_spline(f"Blur{i}Size", _straight(blur)))
        tools.append(_block(f"Blur{i}", "Blur", [_feed("Input", last), _wired("XBlurSize", f"Blur{i}Size")], (pos[0], pos[1] + 50)))
        last = f"Blur{i}"
    alpha = [(f, round(float(st.get("alpha", 1.0)), 6)) for f, _sb, st in samples]
    keyed_alpha = _straight(alpha) if any(abs(v - 1.0) > 1e-6 for _f, v in alpha) else None
    return tools, last, keyed_alpha


def comp_text(layout, title_id, comp_frames, styles=None, clock=None, fps=None):
    """The whole comp for a composed title: Word1..n (Text+), merged in order into
    MediaOut1, tagged with the title's id. styles: one per box (default: its role's
    style in the layout's look). clock (timing.timing) and fps: animate it - each word
    through its own Transform / Blur and onto a clear canvas by a Merge whose Blend is
    its opacity, keyed on every frame the clock moves it. A line's bar goes on that
    canvas first; a word's copies ride on a canvas of the word's own."""
    from . import timing
    frame = layout["frame"]
    if styles is None:
        styles = L.box_styles(layout)
    if layout.get("kinetic") and (clock is None or not clock.get("camera")):
        layout, clock = kinetic.on_screen(layout), None       # a still: where the camera settles
    elif layout.get("kinetic"):
        return _kinetic_comp(layout, title_id, comp_frames, styles, clock, fps)
    boxes, bars = layout["boxes"], layout.get("bars") or []
    keys = [timing.keys(w, fps, comp_frames) for w in clock["words"]] if clock is not None else [{}] * len(boxes)
    # The copies' fan-out is keyed on the copies themselves (_with_copies), not by _motion.
    canvas = clock is not None or bool(bars)
    tools, last = [], None
    if canvas:
        tools.append(_clear("Canvas", frame, comp_frames, (0, 99)))
        last = "Canvas"
        index = {b["id"]: i for i, b in enumerate(boxes, 1)}
        for k, bar in enumerate(bars, 1):
            i = index.get(bar.get("first"))
            blend = _wired("Blend", f"Merge{i}Blend") if i and "alpha" in keys[i - 1] else None
            made, last = _bar_tools(k, bar, frame, comp_frames, last, blend)
            tools += made
    for i, (box, style) in enumerate(zip(boxes, styles), 1):
        name = f"Word{i}"
        pos = (110 * i, 0)
        tools.append(_tool(name, box, style, frame, pos, comp_frames))
        top, extra = name, []
        if style.get("copies"):
            made, top = _with_copies(i, box, style, frame, comp_frames, pos, keys[i - 1])
            tools += made
        if clock is not None:
            motion, top = _motion(i, box, frame, keys[i - 1], top, pos)
            tools += motion
            if "alpha" in keys[i - 1]:
                tools.append(_spline(f"Merge{i}Blend", keys[i - 1]["alpha"]))
                extra.append(_wired("Blend", f"Merge{i}Blend"))
        if last is None:
            last = top
            continue
        merge = f"Merge{i}" if canvas else f"Merge{i - 1}"
        tools.append(_merge(merge, last, top, (110 * i, 99), extra))
        last = merge
    out = [_input("Index", "0")] + ([_feed("Input", last)] if last else [])
    tools.append(_block("MediaOut1", "Saver", out, (110 * (len(boxes) + 2), 99)).rstrip(","))
    end = comp_frames - 1
    return (f"Composition {{\n\tCurrentTime = 0,\n\tRenderRange = {{ 0, {end} }},\n\tGlobalRange = {{ 0, {end} }},\n"
            f"\tCustomData = {{ TEMPLATE_ID = \"Text+\", {TAG} = {json.dumps(title_id)} }},\n"
            f"\tTools = {{\n" + "\n".join(tools) + "\n\t},\n}\n")


def _kinetic_comp(layout, title_id, comp_frames, styles, clock, fps):
    """comp_text for a kinetic title: each word keyed on its own canvas (_kinetic_word),
    merged in order onto a clear one by its opacity."""
    frame = layout["frame"]
    tools = [_clear("Canvas", frame, comp_frames, (0, 99))]
    last = "Canvas"
    for i, (box, style, word) in enumerate(zip(layout["boxes"], styles, clock["words"]), 1):
        pos = (110 * i, 0)
        made, top, alpha = _kinetic_word(i, box, style, frame, comp_frames, pos, word, clock["camera"], fps)
        tools += made
        extra = []
        if alpha:
            tools.append(_spline(f"Merge{i}Blend", alpha))
            extra.append(_wired("Blend", f"Merge{i}Blend"))
        tools.append(_merge(f"Merge{i}", last, top, (110 * i, 99), extra))
        last = f"Merge{i}"
    tools.append(_block("MediaOut1", "Saver", [_input("Index", "0"), _feed("Input", last)],
                        (110 * (len(layout["boxes"]) + 2), 99)).rstrip(","))
    end = comp_frames - 1
    return (f"Composition {{\n\tCurrentTime = 0,\n\tRenderRange = {{ 0, {end} }},\n\tGlobalRange = {{ 0, {end} }},\n"
            f"\tCustomData = {{ TEMPLATE_ID = \"Text+\", {TAG} = {json.dumps(title_id)} }},\n"
            f"\tTools = {{\n" + "\n".join(tools) + "\n\t},\n}\n")
