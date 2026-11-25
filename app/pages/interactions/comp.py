"""An interaction as a Fusion comp - the text of a .comp file (core/fusion_comp.py). Pure.

One controller, `Cursor` (a Custom tool, drawing nothing), holds the cursor and the
interaction's answers - how every shape without states of its own looks hovered and
pressed (model.py) - each a pair, X hovered and Y pressed:

    PointIn1    where the cursor is - keyed (the path recorded in Buddy)
    PointIn2    how much a shape grows, a share of its size (0.08 is 8 % bigger, -0.06 6 % smaller)
    PointIn3    how far it lifts, a share of the frame's height, up
    PointIn4    how strong its glow, 0..1
    NumberIn1   its button: 1 while down - keyed
    NumberIn2   the frames a hover takes       NumberIn3   the frames a press takes
    NumberIn4   how strong its outline, hovered NumberIn5   and pressed
    NumberIn7   how far the cursor's own button is down

Each shape that answers has a `<Name>State` (a Custom too) whose expressions look back
over the cursor's last few frames: NumberIn1 the share of them it was on the shape,
NumberIn2 that eased (the hover), NumberIn3 the share it was on it with the button down
(the press). Its PointIn1..4 are the shape's answers, in the same pairs - grow, lift,
glow, outline: a shape that follows the interaction's reads them off the Cursor
(`Cursor.PointIn2`, ..., `Point(Cursor.NumberIn4, Cursor.NumberIn5)`), so changing the
Cursor's changes every such shape; one with states of its own has its numbers written
there instead, so they can be changed on that shape alone. Its NumberIn4..7 add them
up, as model.look() does: NumberIn4 the size, NumberIn5 the lift, NumberIn6 the glow
and NumberIn7 the outline (those two kept to 0..1). The shape's Transform (Size, and
Center lifted from the middle), its glow and its outline read those - the glow,
outline and label are inside the Transform, so they lift with it. Nothing is keyed but
the cursor, so in Fusion the cursor's path can be redrawn - or a shape's mask moved -
and the shapes still answer. Measured on Studio 21.1.2 (docs/RESOLVE_API_GUIDE.md): a
`:`-block expression, Tool:GetValue("Input", time - k) for the look-back, a tool's own
inputs read by bare name, a Point input given another's whole or one built.

The picture is what model.looks() says, frame for frame: the preview in Buddy is made
from that.

An image shape is the same with a picture for its fill: a Loader reading the file,
merged at its own middle onto a clear canvas at the size that fits its box, and kept to
its mask (so its corners can be rounded). Measured on Studio 21.1.2: a Loader written
this way holds a still for the whole clip, and a Merge's Size 1 is the picture's own px.
"""

import json
import os

from core import fusion_comp as fc
from pages.data_graphics import text_look

from . import model

TAG = "BuddyInteraction"            # CustomData key on the comp: the interaction's id
GLOW_REACH = 0.012                  # how far a glow reaches past its shape, a share of the frame's width
GLOW_SOFT = 0.015
GLOW_LIFT = 0.4                     # a glow is its shape's colour, this far towards white
CURSOR_DIP = 0.15                   # the cursor is this much smaller with its button down
# How each cursor is drawn (model.CURSORS): a pointer's outline in its own height, from
# its tip, across and down; its two colours; its edge, the whole width of the line, a
# share of its height. Windows' pointer is white edged in black, a Mac's black edged in
# white and a little slimmer. A dot's width is a share of the cursor's size.
CURSOR_LOOKS = {
    "windows": {"points": ((0, 0), (0, 0.8), (0.19, 0.63), (0.33, 0.95), (0.45, 0.9), (0.31, 0.58), (0.56, 0.58)),
                "fill": "#ffffff", "edge": "#000000", "edge_w": 0.06},
    "mac": {"points": ((0, 0), (0, 0.76), (0.17, 0.61), (0.3, 0.93), (0.43, 0.87), (0.3, 0.56), (0.51, 0.56)),
            "fill": "#000000", "edge": "#ffffff", "edge_w": 0.09},
    "dot": {"width": 0.5, "fill": "#ffffff", "edge": "#101216"},
}


# How a Loader names the file types an image shape takes (model.IMAGE_TYPES).
FORMATS = {".png": "PNGFormat", ".jpg": "JpegFormat", ".jpeg": "JpegFormat"}


def expr(text):
    return f"Input {{ Expression = {json.dumps(text)}, }},"


def name_of(el):
    """A shape's tools are named by its kind and its place in the pile: Button2."""
    return f"{el['kind'].title()}{el['id'][1:]}"


def _look_back(frames, test):
    """An expression: the share of the last `frames` frames (a Cursor input) that
    `test` held on - p is where the cursor was, d whether its button was down."""
    return (f":local n = {frames}; if n < 1 then n = 1 end; local s, c = 0, 0; "
            f"for k = 0, n - 1 do local p = Cursor:GetValue(\"PointIn1\", time - k); "
            f"local d = Cursor:GetValue(\"NumberIn1\", time - k) > 0.5; "
            f"if {test} then s = s + 1 end; c = c + 1 end; return s / c")


def _on(el, mask, frame):
    """Lua: whether p is on the shape - read off its mask, so a mask moved or resized in
    Fusion is still the shape's own edge."""
    if el["kind"] == "circle":
        aspect = frame[0] / frame[1]
        # An ellipse's Width is a share of the frame's width; p is a share of each side.
        return (f"((p.X - {mask}.Center.X) * (p.X - {mask}.Center.X) + (p.Y - {mask}.Center.Y) * "
                f"(p.Y - {mask}.Center.Y) / {fc.num(aspect * aspect)} < {mask}.Width * {mask}.Width / 4)")
    return (f"(p.X > {mask}.Center.X - {mask}.Width / 2 and p.X < {mask}.Center.X + {mask}.Width / 2 and "
            f"p.Y > {mask}.Center.Y - {mask}.Height / 2 and p.Y < {mask}.Center.Y + {mask}.Height / 2)")


def _lifted(hex_colour, by=GLOW_LIFT):
    r, g, b, _a = fc.rgba(hex_colour)
    return "#" + "".join(f"{round((c + (1 - c) * by) * 255):02x}" for c in (r, g, b))


def _mask(name, el, frame, pos, extra=()):
    """A shape's outline: a RectangleMask (Width and Height shares of the frame's width
    and height) or an EllipseMask (both shares of its WIDTH)."""
    centre = (el["x"] / 100, 1 - el["y"] / 100)
    size = [fc.input_("MaskWidth", frame[0]), fc.input_("MaskHeight", frame[1])]
    if el["kind"] == "circle":
        return fc.block(name, "EllipseMask", [fc.input_("Center", centre), fc.input_("Width", el["w"] / 100),
                                              fc.input_("Height", el["w"] / 100), *size, *extra], pos)
    return fc.block(name, "RectangleMask", [fc.input_("Center", centre), fc.input_("Width", el["w"] / 100),
                                            fc.input_("Height", el["h"] / 100),
                                            fc.input_("CornerRadius", el["radius"] / 100), *size, *extra], pos)


def _glow_mask(name, el, frame, pos):
    """The glow behind a shape: its mask a little larger and soft, as strong as the
    shape is hovered - and following the shape's own mask."""
    n = name_of(el)
    reach_h = GLOW_REACH if el["kind"] == "circle" else GLOW_REACH * frame[0] / frame[1]
    inputs = [f"Center = {expr(f'{n}Mask.Center')}", f"Width = {expr(f'{n}Mask.Width + {2 * GLOW_REACH:.4g}')}",
              f"Height = {expr(f'{n}Mask.Height + {2 * reach_h:.4g}')}", fc.input_("SoftEdge", GLOW_SOFT),
              f"Level = {expr(f'{n}State.NumberIn6')}",
              fc.input_("MaskWidth", frame[0]), fc.input_("MaskHeight", frame[1])]
    if el["kind"] != "circle":
        inputs.insert(3, f"CornerRadius = {expr(f'{n}Mask.CornerRadius')}")
    return fc.block(name, "EllipseMask" if el["kind"] == "circle" else "RectangleMask", inputs, pos)


def _loader(name, path, pos):
    """A Loader reading one still, as Fusion writes one itself. A PNG's see-through
    parts come in already multiplied, as a Merge expects."""
    fmt = FORMATS[os.path.splitext(path)[1].lower()]
    post = '\t\t\t\t["Clip1.PNGFormat.PostMultiply"] = Input { Value = 1, },\n' if fmt == "PNGFormat" else ""
    clip = ", ".join(["StartFrame = -1", "LengthSetManually = true", "TrimIn = 0", "TrimOut = 0", "ExtendFirst = 0",
                      "ExtendLast = 0", "Loop = 0", "AspectMode = 0", "Depth = 0", "TimeCode = 0", "GlobalStart = 0",
                      "GlobalEnd = 0"])
    return (f"\t\t{name} = Loader {{\n\t\t\tClips = {{\n\t\t\t\tClip {{ ID = \"Clip1\", "
            f"Filename = {json.dumps(path, ensure_ascii=False)}, FormatID = \"{fmt}\", {clip} }},\n\t\t\t}},\n"
            f"\t\t\tInputs = {{\n{post}\t\t\t}},\n"
            f"\t\t\tViewInfo = OperatorInfo {{ Pos = {{ {pos[0]}, {pos[1]} }} }},\n\t\t}},")


def _fill(el, frame, comp_frames, pos):
    """[tool text] making `<Name>Fill`, what a shape is filled with, kept to its mask: its
    colour - or, an image, its picture as large as fits its box, following the mask when
    that is moved or resized in Fusion."""
    n = name_of(el)
    x, y = pos
    if el["kind"] != "image":
        return [fc.solid(f"{n}Fill", el["colour"], frame, comp_frames, pos, extra=[fc.mask(f"{n}Mask")])]
    wide, high = el["px"]
    width, _height = model.fit(el, frame)
    # Size 1 is the picture's own px. Whichever side of the box holds the picture in sets it.
    if abs(width - el["w"] / 100 * frame[0]) < 0.5:
        size = f"{n}Mask.Width * {fc.num(frame[0] / wide)}"
    else:
        size = f"{n}Mask.Height * {fc.num(frame[1] / high)}"
    return [_loader(f"{n}Image", el["path"], (x - 55, y - 16)),
            fc.clear(f"{n}Back", frame, comp_frames, (x - 55, y + 16)),
            fc.merge(f"{n}Fill", f"{n}Back", f"{n}Image", pos,
                     extra=[f"Center = {expr(f'{n}Mask.Center')}", f"Size = {expr(size)}", fc.mask(f"{n}Mask")])]


def _edge_mask(name, el, spec, frame, pos):
    """The outline traced round a shape: its mask's own edge as a line (not solid, with
    a border half inside it and half out), as strong as the shape is hovered - and
    following the shape's own mask."""
    n = name_of(el)
    inputs = [f"Center = {expr(f'{n}Mask.Center')}", f"Width = {expr(f'{n}Mask.Width')}",
              f"Height = {expr(f'{n}Mask.Height')}", fc.input_("Solid", 0),
              fc.input_("BorderWidth", spec["outline_width"] / 100 * frame[1] / frame[0]),
              f"Level = {expr(f'{n}State.NumberIn7')}",
              fc.input_("MaskWidth", frame[0]), fc.input_("MaskHeight", frame[1])]
    if el["kind"] != "circle":
        inputs.insert(3, f"CornerRadius = {expr(f'{n}Mask.CornerRadius')}")
    return fc.block(name, "EllipseMask" if el["kind"] == "circle" else "RectangleMask", inputs, pos)


def _label(el, spec, frame, comp_frames, metrics, pos):
    """A shape's label: a Text+ on its middle, moving with its mask."""
    n = name_of(el)
    size = spec["label_size"] / 100 * frame[1]
    x, baseline = el["x"] / 100 * frame[0], el["y"] / 100 * frame[1] + text_look.CAP * size / 2
    look = {"font": spec["font"], "style": spec["style"], "colour": spec["label_colour"]}
    text = text_look.text_plus(f"{n}Label", frame, comp_frames, look, metrics, size, x, baseline, pos, text=el["label"])
    # Its Center, as far above or below the mask's as it was placed.
    centre = text_look.center_point(frame, metrics, size, x, baseline)
    rise = centre[1] - (1 - el["y"] / 100)
    return text.replace(fc.input_("Center", centre),
                        f"Center = {expr(f'Point({n}Mask.Center.X, {n}Mask.Center.Y + {rise:.6g})')}")


# A State's answers - (its input, what of model's states it holds, where the Cursor has
# the interaction's): PointIn1..4, each X hovered and Y pressed.
STATE_POINTS = (("PointIn1", "grow", "Cursor.PointIn2"), ("PointIn2", "lift", "Cursor.PointIn3"),
                ("PointIn3", "glow", "Cursor.PointIn4"), ("PointIn4", "outline", "Point(Cursor.NumberIn4, Cursor.NumberIn5)"))


def _pair(answers, key):
    """(hovered, pressed) of one of a state's numbers, as the comp takes it: a share."""
    return (answers["hover"][key] / 100, answers["press"][key] / 100)


def _kept(point):
    """A block: a State's sum for one of its pairs, kept to 0..1."""
    return (f":local v = {point}.X * NumberIn2 + {point}.Y * NumberIn3; if v < 0 then v = 0 end; "
            f"if v > 1 then v = 1 end; return v")


def _state(el, frame, pos):
    """A shape's `<Name>State`: how far it is hovered and pressed, its answers - the
    Cursor's, or its own written in - and what they add up to."""
    n = name_of(el)
    on = _on(el, f"{n}Mask", frame)
    own = el.get("states")
    answers = [fc.input_(point, _pair(own, key)) if own else f"{point} = {expr(cursor)}"
               for point, key, cursor in STATE_POINTS]
    return fc.block(f"{n}State", "Custom", [
        f"NumberIn1 = {expr(_look_back('Cursor.NumberIn2', on))}",
        f"NumberIn2 = {expr('NumberIn1 * NumberIn1 * (3 - 2 * NumberIn1)')}",
        f"NumberIn3 = {expr(_look_back('Cursor.NumberIn3', f'{on} and d'))}",
        *answers,
        f"NumberIn4 = {expr('1 + PointIn1.X * NumberIn2 + PointIn1.Y * NumberIn3')}",
        f"NumberIn5 = {expr('PointIn2.X * NumberIn2 + PointIn2.Y * NumberIn3')}",
        f"NumberIn6 = {expr(_kept('PointIn3'))}",
        f"NumberIn7 = {expr(_kept('PointIn4'))}"], pos)


def _shape(el, spec, frame, comp_frames, metrics, under, col):
    """([tool text], the tool now on top) for one shape merged over `under`."""
    n = name_of(el)
    x = 110 * col
    tools = [_mask(f"{n}Mask", el, frame, (x, 33)), *_fill(el, frame, comp_frames, (x, 66))]
    if not el["reacts"]:
        tools.append(fc.merge(f"{n}On", under, f"{n}Fill", (x, 198)))
        if el["label"]:
            tools += [_label(el, spec, frame, comp_frames, metrics, (x, 99)),
                      fc.merge(f"{n}LabelOn", f"{n}On", f"{n}Label", (x, 231))]
            return tools, f"{n}LabelOn"
        return tools, f"{n}On"
    # One that answers is built on a canvas of its own - glow, fill, label - so its
    # Transform sizes it alone, about its middle.
    answers = model.answers_of(spec, el)
    tools += [_state(el, frame, (x, -33)),
              _glow_mask(f"{n}GlowMask", el, frame, (x, 0)),
              fc.solid(f"{n}Glow", _lifted(el["colour"]), frame, comp_frames, (x + 55, 0), extra=[fc.mask(f"{n}GlowMask")]),
              fc.clear(f"{n}Canvas", frame, comp_frames, (x + 55, 33)),
              fc.merge(f"{n}GlowOn", f"{n}Canvas", f"{n}Glow", (x + 55, 66)),
              fc.merge(f"{n}FillOn", f"{n}GlowOn", f"{n}Fill", (x + 55, 99))]
    top = f"{n}FillOn"
    if answers["hover"]["outline"] > 0 or answers["press"]["outline"] > 0:
        # Only when its answers ask for one: two more tools a shape that most comps don't need.
        tools += [_edge_mask(f"{n}EdgeMask", el, spec, frame, (x - 55, 99)),
                  fc.solid(f"{n}Edge", spec["outline_colour"], frame, comp_frames, (x - 55, 132),
                           extra=[fc.mask(f"{n}EdgeMask")]),
                  fc.merge(f"{n}EdgeOn", top, f"{n}Edge", (x + 55, 116))]
        top = f"{n}EdgeOn"
    if el["label"]:
        tools += [_label(el, spec, frame, comp_frames, metrics, (x, 132)),
                  fc.merge(f"{n}LabelOn", top, f"{n}Label", (x + 55, 132))]
        top = f"{n}LabelOn"
    # Sized about its mask's middle, and lifted: a Transform's Center at 0.5, 0.5 moves nothing.
    tools += [fc.block(n, "Transform", [fc.feed("Input", top), f"Center = {expr(f'Point(0.5, 0.5 + {n}State.NumberIn5)')}",
                                        f"Pivot = {expr(f'{n}Mask.Center')}",
                                        f"Size = {expr(f'{n}State.NumberIn4')}"], (x + 55, 165)),
              fc.merge(f"{n}On", under, n, (x, 198))]
    return tools, f"{n}On"


def _cursor(spec, frame, comp_frames, under, col):
    """The cursor itself, on top of everything: a pointer whose tip, or a dot whose
    middle, is where the controller says - a pointer a little smaller while its button
    is down."""
    look = CURSOR_LOOKS[spec["cursor"]]
    fw, fh = frame
    size = spec["cursor_size"] / 100 * fh
    x = 110 * col
    dip = f"Size = {expr(f'1 - {CURSOR_DIP} * Cursor.NumberIn7')}"
    where = fc.wired("Center", "CursorPath")
    frame_size = [fc.input_("MaskWidth", fw), fc.input_("MaskHeight", fh)]
    if spec["cursor"] == "dot":
        width = look["width"] * size / fw
        shapes = [fc.block("CursorEdgeMask", "EllipseMask", [where, fc.input_("Width", width * 1.3),
                                                           fc.input_("Height", width * 1.3), *frame_size], (x, 0)),
                  fc.block("CursorMask", "EllipseMask", [where, fc.input_("Width", width), fc.input_("Height", width),
                                                       *frame_size], (x, 66))]
        # An ellipse has no Size of its own to dip: the dot stays as it is.
    else:
        outline = fc.polyline([(ax * size / fw, -ay * size / fh) for ax, ay in look["points"]], closed=True)
        shapes = [fc.block("CursorEdgeMask", "PolylineMask", [where, fc.input_("Polyline", outline), dip,
                                                            fc.input_("BorderWidth", look["edge_w"] * size / fw),
                                                            *frame_size], (x, 0)),
                  fc.block("CursorMask", "PolylineMask", [where, fc.input_("Polyline", outline), dip, *frame_size],
                           (x, 66))]
    return shapes + [
        fc.solid("CursorEdge", look["edge"], frame, comp_frames, (x, 33), extra=[fc.mask("CursorEdgeMask")]),
        fc.solid("CursorFill", look["fill"], frame, comp_frames, (x, 99), extra=[fc.mask("CursorMask")]),
        fc.merge("CursorEdgeOn", under, "CursorEdge", (x, 198)),
        fc.merge("CursorOn", "CursorEdgeOn", "CursorFill", (x + 55, 198))], "CursorOn"


def controller(spec, fps, comp_frames):
    """The Cursor tool and what keys it: [tool text]."""
    keys = model.cursor_keys(spec, fps, comp_frames)
    held = [(f, 1.0 if d else 0.0) for f, d in enumerate(model.down(spec, fps, comp_frames))]
    own = (":local n = NumberIn3; if n < 1 then n = 1 end; local s, c = 0, 0; for k = 0, n - 1 do "
           "if Cursor:GetValue(\"NumberIn1\", time - k) > 0.5 then s = s + 1 end; c = c + 1 end; return s / c")
    tools = fc.path("CursorPath", [(f, x / 100) for f, x, _y in keys], [(f, 1 - y / 100) for f, _x, y in keys], (0, -99))
    tools.append(fc.spline("CursorClick", fc.straight(held)))
    answers = spec["answers"]
    edge = _pair(answers, "outline")
    tools.append(fc.block("Cursor", "Custom", [
        fc.wired("PointIn1", "CursorPath"),
        fc.input_("PointIn2", _pair(answers, "grow")), fc.input_("PointIn3", _pair(answers, "lift")),
        fc.input_("PointIn4", _pair(answers, "glow")),
        fc.wired("NumberIn1", "CursorClick"),
        fc.input_("NumberIn2", model.look_back(spec["hover_seconds"], fps)),
        fc.input_("NumberIn3", model.look_back(spec["press_seconds"], fps)),
        fc.input_("NumberIn4", edge[0]), fc.input_("NumberIn5", edge[1]),
        f"NumberIn7 = {expr(own)}"], (0, -33)))
    return tools


def comp_text(spec, frame, fps, comp_frames, metrics=None):
    """(the comp's text, the interaction's id) for a clip of comp_frames frames."""
    metrics = text_look.clean_metrics(metrics)
    gid = model.interaction_id(spec, frame, fps)
    tools = controller(spec, fps, comp_frames)
    tools.append(fc.clear("Canvas", frame, comp_frames, (0, 198)))
    last = "Canvas"
    for col, el in enumerate(spec["elements"], 1):
        made, last = _shape(el, spec, frame, comp_frames, metrics, last, col * 2)
        tools += made
    col = (len(spec["elements"]) + 1) * 2
    if spec["cursor"] != "none":
        made, last = _cursor(spec, frame, comp_frames, last, col)
        tools += made
    tools.append(fc.media_out(last, (110 * (col + 2), 198)))
    return fc.composition(tools, comp_frames, {TAG: gid}), gid
