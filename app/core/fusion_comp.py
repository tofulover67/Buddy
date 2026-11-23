"""A Fusion comp written as the text of a .comp file - the pieces every comp Buddy
generates is made of: inputs, tools, keyed splines, paths, merges. A clip takes the
text with ImportFusionComp, and the Edit page renders an imported comp straight away
(docs/RESOLVE_API_GUIDE.md, "A whole title as one generated comp").

Pure text: no Resolve and no Qt, so it loads in the Resolve reader's process and is
tested on its own. Animation's titles (pages/text_animator/titles/textplus_comp.py)
and Data Graphics (pages/data_graphics/) both write their comps with it.
"""

import json

# Where a Text+'s baseline sits below its Center, as a share of the letter size, when
# the face hasn't been measured in Resolve (canvas_math.BASELINE_FALLBACK).
TEXT_BASELINE = 0.375
# A Text+'s letter size in px per (Size x frame width) for a face nobody measured:
# Arial's (0.8 / (ascent + descent)).
EM_PER_SIZE = 0.716
# An outline reaches this many px per (Thickness x Size x width), whatever the font.
OUTLINE_REACH = 0.716


def rgba(value, fallback="#ffffff"):
    """(r, g, b, a) in 0..1 from "#rrggbb", "#rrggbbaa" or "rgba(r, g, b, a)"."""
    text = str(value or fallback).strip()
    if text.startswith("rgb"):
        parts = [float(p) for p in text[text.index("(") + 1:text.index(")")].split(",")]
        return parts[0] / 255, parts[1] / 255, parts[2] / 255, parts[3] if len(parts) > 3 else 1.0
    text = text.lstrip("#")
    if len(text) not in (6, 8):
        return rgba(fallback, "#ffffff")
    try:
        rgb = [int(text[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        return (*rgb, int(text[6:8], 16) / 255 if len(text) == 8 else 1.0)
    except ValueError:
        return rgba(fallback, "#ffffff")


def num(v):
    return f"{v:.6g}"


class Lua(str):
    """A value already written as Lua (a Gradient table): never quoted."""


def input_(name, value):
    if isinstance(value, Lua):
        return f"{name} = Input {{ Value = {value}, }},"
    if isinstance(value, str):
        # The text as it is: Lua reads a JSON string's \" \\ \n, but not \uXXXX.
        return f"{name} = Input {{ Value = {json.dumps(value, ensure_ascii=False)}, }},"
    if isinstance(value, (tuple, list)):
        return f"{name} = Input {{ Value = {{ {', '.join(num(v) for v in value)} }}, }},"
    return f"{name} = Input {{ Value = {num(value)}, }},"


def polyline(points, closed=False):
    """A Polyline value of straight-sided points, (x, y) each an offset from the frame's
    middle as a share of its width and height, y up - what a PolylineMask's Polyline
    takes (measured on Studio 21.1.2). Open, with a BorderWidth, it is a stroke."""
    rows = ", ".join(f"{{ Linear = true, X = {num(x)}, Y = {num(y)} }}" for x, y in points)
    return Lua(f"Polyline {{ {'Closed = true, ' if closed else ''}Points = {{ {rows} }} }}")


def fuid(name):
    """A choice written by name, as Fusion saves one: PaintMode "Multiply"."""
    return Lua(f"FuID {{ {json.dumps(name)} }}")


def spline(name, points):
    """A BezierSpline keyed at (frame, value) points, straight between them (handles a
    third of the way along, Linear) - so Fusion plays exactly the sampled values."""
    rows = []
    for n, (f, v) in enumerate(points):
        parts = [num(v)]
        if n > 0:
            pf, pv = points[n - 1]
            parts.append(f"LH = {{ {num(f - (f - pf) / 3)}, {num(v - (v - pv) / 3)} }}")
        if n < len(points) - 1:
            nf, nv = points[n + 1]
            parts.append(f"RH = {{ {num(f + (nf - f) / 3)}, {num(v + (nv - v) / 3)} }}")
        parts.append("Flags = { Linear = true }")
        rows.append(f"\t\t\t\t[{int(f)}] = {{ {', '.join(parts)} }},")
    return (f"\t\t{name} = BezierSpline {{\n\t\t\tSplineColor = {{ Red = 255, Green = 128, Blue = 0 }},\n"
            f"\t\t\tNameSet = true,\n\t\t\tKeyFrames = {{\n" + "\n".join(rows) + "\n\t\t\t}\n\t\t},")


def text_spline(name, keys):
    """A text input's keys, [(frame, text)]: the text changes on each key's frame and
    holds until the next. Wire it with wired("StyledText", name). (Measured on Studio
    21.1.2: 300 keys imported in 13 ms and drew the right text on every frame asked.)"""
    rows = [f"\t\t\t\t[{int(f)}] = {{ {n}, Flags = {{ Linear = true, LockedY = true }}, "
            f"Value = Text {{ Value = {json.dumps(str(text), ensure_ascii=False)} }} }},"
            for n, (f, text) in enumerate(keys)]
    return (f"\t\t{name} = BezierSpline {{\n\t\t\tSplineColor = {{ Red = 237, Green = 142, Blue = 243 }},\n"
            f"\t\t\tNameSet = true,\n\t\t\tKeyFrames = {{\n" + "\n".join(rows) + "\n\t\t\t}\n\t\t},")


def wired(name, source):
    """An input keyed by another tool (a spline or a path) instead of set."""
    return f"{name} = Input {{ SourceOp = \"{source}\", Source = \"Value\", }},"


def block(name, kind, inputs, pos, extra=""):
    body = "\n".join(f"\t\t\t\t{x}" for x in inputs)
    return (f"\t\t{name} = {kind} {{\n{extra}\t\t\tInputs = {{\n{body}\n\t\t\t}},\n"
            f"\t\t\tViewInfo = OperatorInfo {{ Pos = {{ {pos[0]}, {pos[1]} }} }},\n\t\t}},")


def feed(name, source):
    return f"{name} = Input {{ SourceOp = \"{source}\", Source = \"Output\", }},"


def mask(source):
    """A tool's picture kept to a mask tool's shape."""
    return f"EffectMask = Input {{ SourceOp = \"{source}\", Source = \"Mask\", }},"


def clear(name, frame, comp_frames, pos):
    """A transparent Background the size of the frame: a canvas to merge onto."""
    fw, fh = frame
    return block(name, "Background", [input_("GlobalOut", comp_frames - 1), input_("Width", fw), input_("Height", fh),
                                      input_("UseFrameFormatSettings", 1), input_("TopLeftAlpha", 0)], pos)


def solid(name, colour, frame, comp_frames, pos, extra=()):
    """A Background of one colour the size of the frame (mask it to make a shape). A
    see-through colour ("#rrggbbaa") is written premultiplied, as a Merge expects it:
    left as it was, a 20% white line came out as bright as a solid one (measured on
    Studio 21.1.2)."""
    fw, fh = frame
    r, g, b, a = rgba(colour, "#ffffff")
    r, g, b = r * a, g * a, b * a
    return block(name, "Background", [input_("GlobalOut", comp_frames - 1), input_("Width", fw), input_("Height", fh),
                                      input_("UseFrameFormatSettings", 1), input_("TopLeftRed", r),
                                      input_("TopLeftGreen", g), input_("TopLeftBlue", b), input_("TopLeftAlpha", a),
                                      *extra], pos)


def merge(name, background, foreground, pos, extra=()):
    return block(name, "Merge", [feed("Background", background), feed("Foreground", foreground), *extra,
                                 input_("PerformDepthMerge", 0)], pos)


def path(name, xs, ys, pos):
    """An XYPath keyed from (frame, value) points for x and y - a Point input's motion."""
    return [spline(f"{name}X", xs), spline(f"{name}Y", ys),
            block(name, "XYPath", [wired("X", f"{name}X"), wired("Y", f"{name}Y")], pos,
                  "\t\t\tShowKeyPoints = false,\n\t\t\tDrawMode = \"ModifyOnly\",\n")]


def straight(points):
    """(frame, value) keys less those in the middle of a straight run."""
    if len(points) < 3:
        return list(points)
    kept = [points[0]]
    for prev, cur, nxt in zip(points, points[1:], points[2:]):
        if abs((cur[1] - prev[1]) - (nxt[1] - cur[1])) > 1e-7 or cur[0] - prev[0] != nxt[0] - cur[0]:
            kept.append(cur)
    kept.append(points[-1])
    return kept


def media_out(source, pos):
    """The comp's way out to the timeline: the last tool."""
    inputs = [input_("Index", "0")] + ([feed("Input", source)] if source else [])
    return block("MediaOut1", "Saver", inputs, pos).rstrip(",")


def composition(tools, comp_frames, data):
    """The whole comp: its tools (media_out last) and its CustomData - {key: text}, how
    Buddy knows a comp for its own again (comp.GetData(key))."""
    end = comp_frames - 1
    custom = ", ".join(["TEMPLATE_ID = \"Text+\""] + [f"{k} = {json.dumps(str(v))}" for k, v in data.items()])
    return (f"Composition {{\n\tCurrentTime = 0,\n\tRenderRange = {{ 0, {end} }},\n\tGlobalRange = {{ 0, {end} }},\n"
            f"\tCustomData = {{ {custom} }},\n"
            f"\tTools = {{\n" + "\n".join(tools) + "\n\t},\n}\n")
