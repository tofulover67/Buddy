"""A design as a Fusion comp - the text of a .comp file (core/fusion_comp.py). Pure.

Every layer is a few tools named after it (a layer called "Badge", the third in the
design, is Badge3Mask, Badge3Fill, Badge3On ...), merged in order onto a clear canvas,
so the flow in Fusion reads like the layers list and each piece can be changed there:

    a shape      a mask (its outline) on a Background (its fill: one colour or a linear
                 gradient); a second mask drawn as a line on another Background (its
                 stroke); a soft copy of its outline, moved, on a third (its shadow)
    a boolean    its children's masks in a chain, each combined with the ones before by
                 its PaintMode, on one Background
    text         a Text+ - retyped and restyled in Fusion as any other
    an image     a Loader reading the file (written as text: adding one from a script
                 opens a dialog in Resolve), merged at its place and size
    a group      its layers merged onto a canvas of their own when the group is
                 see-through, else straight onto the design's
    a frame      its background, written as a rectangle's tools (Card3Mask, Card3Fill,
                 Card3On), with its children over it - like a group, put together on
                 a canvas of its own (Card3Canvas) and faded on (Card3Fade) when it is
                 see-through. Where the children are was worked out by the view.
    a component  (a main) as a group
    an instance  its main's layers carried into its box (model.resolve), as a group, their
                 tools named after the instance and then the layer: Button5_Label2Text

A layer's opacity is the Blend of the Merge that puts it on; a blurred or see-through
layer with more than one piece is put together on a canvas of its own first, so it
fades and blurs as one.

A flipped layer (flipx / flipy, geometry.flipped): an outline is mirrored point by
point; a RectangleMask or EllipseMask is the same mirrored, so only its Angle (the
layer's rot, already turned the other way by the flip) and a gradient's Start and End
change; a picture is mirrored by a Transform between its Loader and its Merge; text by
turning its Text+ half round about Y (across) or X (up and down).

Tools are kept flat: a GroupOperator's output reads empty in an imported comp
(docs/RESOLVE_API_GUIDE.md, section 5, where what's relied on here was measured).
"""

import math

from core import fusion_comp as fc
from pages.data_graphics import text_look
from pages.interactions.comp import _loader

from . import geometry, model

TAG = "BuddyDesign"                 # CustomData key on the comp: the design's id
JOIN = {"bevel": 0, "round": 1, "miter": 2}
CAP = {"butt": 0, "round": 1, "square": 2}
MITER_LIMIT = 10                    # the canvas's own (a 2D context's default)
# How a boolean's child joins the ones before it: a mask's PaintMode.
PAINT = {"union": "Merge", "subtract": "Subtract", "intersect": "Multiply", "exclude": "Invert"}
ALIGN = {"left": -1, "center": 0, "right": 1}
# A blur on the canvas is a Gaussian of sigma = half the number typed (as CSS and Figma
# count it). Measured on Studio 21.1.2 against the canvas, on 1920 and 3840 px wide
# timelines: a mask's SoftEdge is 2.15 x sigma as a share of the frame's width; a Blur
# tool's XBlurSize is 1670 x sigma as a share of the frame's width (0.87 per px of sigma
# on a 1920 px frame - the same number blurs twice as many px on one twice as wide); a
# Text+ shadow's Softness is 120 x sigma as a share of the letter size.
SOFT_EDGE_PER_SIGMA = 2.15
BLUR_SIZE_PER_SIGMA = 1670.0
TEXT_SOFT_PER_SIGMA = 120.0
COLUMN, ROW = 110, 33
# A flipped picture's Transform, and a flipped text's Text+ turn (geometry.flips):
# to be measured in Resolve - the Transform's flip inputs' names, and whether Text+
# turns about Y or X before its AngleZ, about its Center (so a turned, flipped text
# lands as the canvas draws it).
FLIP_INPUTS = ("FlipHoriz", "FlipVert")
TEXT_FLIP_ANGLES = ("AngleY", "AngleX")
# Rectangles and ellipses as Fusion's own RectangleMask and EllipseMask, so they stay a
# rectangle and an ellipse there; off, every outline is a PolylineMask.
PLAIN = True


def _base(layer):
    """A layer's name as its tools' names start: its letters and digits, never a digit first."""
    base = "".join(ch for ch in layer["name"] if ch.isascii() and ch.isalnum())
    if not base or base[0].isdigit():
        base = model.TITLES[layer["kind"]] + base
    return base[:40]


class _Comp:
    """What every layer's tools need to know, and the tools so far."""

    def __init__(self, design, frame, comp_frames, metrics):
        self.frame, self.comp_frames, self.metrics = frame, comp_frames, metrics or {}
        self.k, self.dx, self.dy = geometry.fit(design["size"], frame)
        self.tools = []
        self.col = 0
        self.names = {}
        self.mains = model.mains(design)
        for n, layer in enumerate(model.every(design["layers"]), 1):
            self.names[layer["id"]] = f"{_base(layer)}{n}"

    # A place on the canvas, in the frame's px - and as Fusion counts it.
    def px(self, x, y):
        return x * self.k + self.dx, y * self.k + self.dy

    def point(self, x, y):
        """0..1 across and up: a Center, a gradient's Start."""
        fx, fy = self.px(x, y)
        return fx / self.frame[0], 1 - fy / self.frame[1]

    def wide(self, length):
        """A length as a share of the frame's width: a BorderWidth, a SoftEdge."""
        return length * self.k / self.frame[0]

    def high(self, length):
        return length * self.k / self.frame[1]

    def column(self):
        self.col += 1
        return COLUMN * self.col

    def sized(self):
        return [fc.input_("MaskWidth", self.frame[0]), fc.input_("MaskHeight", self.frame[1]),
                fc.Lua("PixelAspect = Input { Value = { 1, 1 }, },")]


def _polyline(comp, outline):
    """A Polyline value with curves. All four handles are always written: left out,
    Fusion makes one up on the closing side and a straight edge bulges."""
    fw, fh = comp.frame
    rows = []
    for x, y, ix, iy, ox, oy in outline["points"]:
        fx, fy = comp.px(x, y)
        fields = [f"X = {fc.num(fx / fw - 0.5)}", f"Y = {fc.num(0.5 - fy / fh)}",
                  f"LX = {fc.num(comp.wide(ix))}", f"LY = {fc.num(0.0 - comp.high(iy))}",
                  f"RX = {fc.num(comp.wide(ox))}", f"RY = {fc.num(0.0 - comp.high(oy))}"]
        if not (ix or iy or ox or oy):
            fields.append("Linear = true")
        rows.append("{ " + ", ".join(fields) + " }")
    return fc.Lua(f"Polyline {{ Closed = {'true' if outline['closed'] else 'false'}, Points = {{ {', '.join(rows)} }} }}")


def _stroke_inputs(layer):
    stroke = layer.get("stroke") or model.STROKE
    return [fc.input_("JoinStyle", JOIN[stroke["join"]]), fc.input_("MiterLimit", MITER_LIMIT),
            fc.input_("CapStyle", CAP[stroke["cap"]])]


def _mask(comp, name, layer, pos, grow=0.0, line=0.0, move=(0.0, 0.0), soft=0.0, extra=()):
    """A layer's outline as a mask tool. grow: px its edge is pushed out all round;
    line: drawn as a line this wide along its edge instead of filled; move: px it is
    shifted by; soft: px of Gaussian sigma its edge is softened by."""
    kind = layer["kind"]
    more = list(extra)
    if soft > 0:
        more.append(fc.input_("SoftEdge", comp.wide(soft) * SOFT_EDGE_PER_SIGMA))
    # A box whose corners differ has no RectangleMask that draws it: it is a polyline.
    if PLAIN and kind in ("rect", "ellipse", "image") and not layer.get("radii"):
        w, h = layer["w"] + 2 * grow, layer["h"] + 2 * grow
        inputs = [fc.input_("Center", comp.point(layer["x"] + move[0], layer["y"] + move[1])),
                  fc.input_("Width", comp.wide(w)),
                  # An ellipse's Height is a share of the frame's WIDTH; a rectangle's, of its height.
                  fc.input_("Height", comp.wide(h) if kind == "ellipse" else comp.high(h))]
        if kind != "ellipse":
            radius = geometry.radius_of(layer)
            radius = radius + grow if radius > 0 else 0.0
            inputs.append(fc.input_("CornerRadius", min(1.0, radius / (min(w, h) / 2))))
        if layer["rot"]:
            inputs.append(fc.input_("Angle", -layer["rot"]))
        if line > 0:
            inputs += [fc.input_("Solid", 0), fc.input_("BorderWidth", comp.wide(line))]
        tool = "EllipseMask" if kind == "ellipse" else "RectangleMask"
        return fc.block(name, tool, [*inputs, *comp.sized(), *more], pos)
    inputs = [*_stroke_inputs(layer), fc.input_("Polyline", _polyline(comp, geometry.outline(layer)))]
    if move[0] or move[1]:
        inputs.append(fc.input_("Center", (0.5 + comp.wide(move[0]), 0.5 - comp.high(move[1]))))
    if line > 0:
        inputs += [fc.input_("Solid", 0), fc.input_("BorderWidth", comp.wide(line))]
    elif grow > 0:
        # A filled outline with a border is the shape grown by half the border all round.
        inputs.append(fc.input_("BorderWidth", comp.wide(2 * grow)))
    return fc.block(name, "PolylineMask", [*inputs, *comp.sized(), *more], pos, '\t\t\tDrawMode = "ModifyOnly",\n')


def _paint(comp, name, fill, box, rot, mask, pos, flip=(1.0, 1.0)):
    """A Background of a fill ({"type", "colour", "stops", "angle"}), kept to a mask."""
    if fill["type"] != "linear":
        return fc.solid(name, fill["colour"], comp.frame, comp.comp_frames, pos, extra=[fc.mask(mask)])
    start, end = geometry.gradient_line(box, rot, fill["angle"], flip)
    stops = ", ".join(f"[{fc.num(stop['at'] / 100)}] = {{ {', '.join(fc.num(c) for c in fc.rgba(stop['colour']))} }}"
                      for stop in fill["stops"])
    fw, fh = comp.frame
    return fc.block(name, "Background", [
        fc.input_("GlobalOut", comp.comp_frames - 1), fc.input_("Width", fw), fc.input_("Height", fh),
        fc.input_("UseFrameFormatSettings", 1), fc.input_("Type", fc.fuid("Gradient")),
        fc.input_("GradientType", fc.fuid("Linear")), fc.input_("Start", comp.point(*start)),
        fc.input_("End", comp.point(*end)), fc.input_("Gradient", fc.Lua(f"Gradient {{ Colors = {{ {stops} }} }}")),
        fc.mask(mask)], pos)


def _shade(comp, name, shadow, mask, pos):
    """A shadow's Background: its colour, as strong as its opacity, kept to its mask."""
    r, g, b, _a = fc.rgba(shadow["colour"])
    alpha = shadow["opacity"] / 100
    colour = "#" + "".join(f"{round(c * 255):02x}" for c in (r, g, b, alpha))
    return fc.solid(name, colour, comp.frame, comp.comp_frames, pos, extra=[fc.mask(mask)])


def _put(comp, n, pictures, under, layer, x):
    """The layer's pictures (bottom first) on top of `under`: the tool now on top."""
    opacity, blur = layer["opacity"] / 100, layer.get("blur") or 0.0
    blend = [fc.input_("Blend", opacity)] if opacity < 1 else []
    if blur <= 0 and (opacity >= 1 or len(pictures) == 1):
        for i, picture in enumerate(pictures):
            last = i == len(pictures) - 1
            name = f"{n}On" if last else f"{n}On{i + 1}"
            comp.tools.append(fc.merge(name, under, picture, (x + (0 if last else -55), 330 + (0 if last else -ROW)),
                                       extra=blend))
            under = name
        return under
    # Put together on a canvas of its own, so it fades and blurs as one.
    comp.tools.append(fc.clear(f"{n}Canvas", comp.frame, comp.comp_frames, (x + 55, 198)))
    top = f"{n}Canvas"
    for i, picture in enumerate(pictures):
        comp.tools.append(fc.merge(f"{n}Part{i + 1}", top, picture, (x + 55, 231 + 11 * i)))
        top = f"{n}Part{i + 1}"
    if blur > 0:
        size = comp.wide(blur / 2) * BLUR_SIZE_PER_SIGMA
        comp.tools.append(fc.block(f"{n}Blur", "Blur", [fc.feed("Input", top), fc.input_("XBlurSize", size)],
                                   (x + 55, 297)))
        top = f"{n}Blur"
    comp.tools.append(fc.merge(f"{n}On", under, top, (x, 330), extra=blend))
    return f"{n}On"


def _shape(comp, layer, under):
    n, x = comp.names[layer["id"]], comp.column()
    fill, stroke, shadow = layer["fill"], layer["stroke"], layer["shadow"]
    filled, lined = fill["type"] != "none", stroke["width"] > 0
    if not filled and not lined:
        return under
    pictures = []
    if shadow["on"] and shadow["opacity"] > 0:
        # The shadow of all of it: the outline grown by the half of the stroke outside
        # it - or, with no fill, of the line alone.
        look = {"grow": stroke["width"] / 2 if lined else 0.0} if filled else {"line": stroke["width"]}
        comp.tools += [_mask(comp, f"{n}ShadowMask", layer, (x, 0), move=(shadow["dx"], shadow["dy"]),
                             soft=shadow["blur"] / 2, **look),
                       _shade(comp, f"{n}Shadow", shadow, f"{n}ShadowMask", (x, ROW))]
        pictures.append(f"{n}Shadow")
    if filled:
        box = (layer["x"], layer["y"], layer["w"], layer["h"])
        comp.tools += [_mask(comp, f"{n}Mask", layer, (x, 2 * ROW)),
                       _paint(comp, f"{n}Fill", fill, box, layer["rot"], f"{n}Mask", (x, 3 * ROW),
                              geometry.flips(layer))]
        pictures.append(f"{n}Fill")
    if lined:
        comp.tools += [_mask(comp, f"{n}StrokeMask", layer, (x, 4 * ROW), line=stroke["width"]),
                       fc.solid(f"{n}Stroke", stroke["colour"], comp.frame, comp.comp_frames, (x, 5 * ROW),
                                extra=[fc.mask(f"{n}StrokeMask")])]
        pictures.append(f"{n}Stroke")
    return _put(comp, n, pictures, under, layer, x)


def _chain(comp, n, layer, tag, pos, move=(0.0, 0.0)):
    """A boolean's children as masks, each combined with the ones before: the last one's name."""
    last = None
    for i, child in enumerate(layer["children"], 1):
        name = f"{n}{tag}{i}"
        extra = [] if last is None else [fc.input_("PaintMode", fc.fuid(PAINT[layer["op"]])), fc.mask(last)]
        comp.tools.append(_mask(comp, name, child, (pos[0] + 55 * (i - 1), pos[1]), move=move, extra=extra))
        last = name
    return last


def _boolean(comp, layer, under):
    n, x = comp.names[layer["id"]], comp.column()
    comp.col += len(layer["children"]) // 2
    fill, shadow = layer["fill"], layer["shadow"]
    if fill["type"] == "none":
        return under
    pictures = []
    if shadow["on"] and shadow["opacity"] > 0:
        last = _chain(comp, n, layer, "ShadowMask", (x, 0), move=(shadow["dx"], shadow["dy"]))
        comp.tools.append(_shade(comp, f"{n}ShadowFill", shadow, last, (x, ROW)))
        top = f"{n}ShadowFill"
        if shadow["blur"] > 0:
            size = comp.wide(shadow["blur"] / 2) * BLUR_SIZE_PER_SIGMA
            comp.tools.append(fc.block(f"{n}Shadow", "Blur", [fc.feed("Input", top), fc.input_("XBlurSize", size)],
                                       (x, 2 * ROW - 11)))
            top = f"{n}Shadow"
        pictures.append(top)
    left, top_, right, bottom = geometry.bounds(layer["children"])
    box = ((left + right) / 2, (top_ + bottom) / 2, right - left, bottom - top_)
    last = _chain(comp, n, layer, "Mask", (x, 2 * ROW))
    comp.tools.append(_paint(comp, f"{n}Fill", fill, box, 0.0, last, (x, 3 * ROW)))
    pictures.append(f"{n}Fill")
    return _put(comp, n, pictures, under, layer, x)


def face_key(family, style):
    return f"{family}|{style}"


def _text(comp, layer, under):
    n, x = comp.names[layer["id"]], comp.column()
    fw, fh = comp.frame
    em = text_look.clean_metrics(comp.metrics.get(face_key(layer["font"], layer["style"])))["em"]
    size_px = layer["size"] * comp.k
    share = size_px / em                            # Size x the frame's width, px
    r, g, b, _a = fc.rgba(layer["colour"])
    inputs = [fc.input_("GlobalOut", comp.comp_frames - 1), fc.input_("Width", fw), fc.input_("Height", fh),
              fc.input_("UseFrameFormatSettings", 1), fc.input_("StyledText", layer["text"]),
              fc.input_("Font", layer["font"]), fc.input_("Style", layer["style"]), fc.input_("Size", share / fw),
              fc.input_("Center", comp.point(layer["x"], layer["y"])),
              fc.input_("HorizontalLeftCenterRight", ALIGN[layer["align"]]),
              *([fc.input_("AngleZ", -layer["rot"])] if layer["rot"] else []),
              *[fc.input_(angle, 180) for angle, f in zip(TEXT_FLIP_ANGLES, geometry.flips(layer)) if f < 0],
              fc.input_("Red1", r), fc.input_("Green1", g), fc.input_("Blue1", b), fc.input_("Alpha1", 1)]
    shadow = layer["shadow"]
    if shadow["on"] and shadow["opacity"] > 0:
        r, g, b, _a = fc.rgba(shadow["colour"])
        soft = shadow["blur"] / 2 * comp.k / size_px * TEXT_SOFT_PER_SIGMA
        dx, dy = _text_shadow(layer, shadow)
        inputs += [fc.input_("Enabled3", 1),
                   fc.input_("Offset3", (dx * comp.k / share, 0.0 - dy * comp.k / share)),
                   fc.input_("SoftnessX3", soft), fc.input_("SoftnessY3", soft), fc.input_("Red3", r),
                   fc.input_("Green3", g), fc.input_("Blue3", b), fc.input_("Alpha3", shadow["opacity"] / 100)]
    comp.tools.append(fc.block(f"{n}Text", "TextPlus", inputs, (x, 3 * ROW)))
    return _put(comp, n, [f"{n}Text"], under, layer, x)


def _text_shadow(layer, shadow):
    """(dx, dy) to write as a Text+ shadow's Offset, px down and across the letters:
    Fusion carries the offset round with the text - turned by its AngleZ, mirrored by its
    AngleX/AngleY (measured on Studio 21.1.2, 2026-11-25: a shadow 40 px to the right of
    text turned 90 degrees came out 40 px below it) - while the canvas keeps a shadow
    where it says on screen. So the screen offset is taken back through the text's turn,
    then its flips (the text is flipped first, then turned)."""
    turn = math.radians(-layer["rot"])
    c, s = math.cos(turn), math.sin(turn)
    x, y = shadow["dx"] * c - shadow["dy"] * s, shadow["dx"] * s + shadow["dy"] * c
    fx, fy = geometry.flips(layer)
    return x * fx, y * fy


def _image(comp, layer, under):
    n, x = comp.names[layer["id"]], comp.column()
    shadow = layer["shadow"]
    pictures = []
    if shadow["on"] and shadow["opacity"] > 0:
        comp.tools += [_mask(comp, f"{n}ShadowMask", layer, (x, 0), move=(shadow["dx"], shadow["dy"]),
                             soft=shadow["blur"] / 2),
                       _shade(comp, f"{n}Shadow", shadow, f"{n}ShadowMask", (x, ROW))]
        pictures.append(f"{n}Shadow")
    # A Merge's Size 1 is the picture's own px; its Center places it, its Angle turns it.
    extra = [fc.input_("Center", comp.point(layer["x"], layer["y"])),
             fc.input_("Size", layer["w"] * comp.k / layer["px"][0])]
    if layer["rot"]:
        extra.append(fc.input_("Angle", -layer["rot"]))
    if any(geometry.radii_of(layer)):
        comp.tools.append(_mask(comp, f"{n}Mask", layer, (x - 55, 3 * ROW)))
        extra.append(fc.mask(f"{n}Mask"))
    comp.tools.append(_loader(f"{n}Image", layer["path"], (x - 55, 2 * ROW)))
    picture = f"{n}Image"
    flips = [fc.input_(key, 1) for key, f in zip(FLIP_INPUTS, geometry.flips(layer)) if f < 0]
    if flips:
        # The picture mirrored about its own middle, before the Merge sizes and turns it.
        comp.tools.append(fc.block(f"{n}Flip", "Transform", [fc.feed("Input", picture), *flips], (x - 55, 2 * ROW + 11)))
        picture = f"{n}Flip"
    comp.tools += [fc.clear(f"{n}Back", comp.frame, comp.comp_frames, (x + 55, 2 * ROW)),
                   fc.merge(f"{n}Fill", f"{n}Back", picture, (x, 3 * ROW), extra=extra)]
    pictures.append(f"{n}Fill")
    return _put(comp, n, pictures, under, layer, x)


def _group(comp, layer, under):
    if layer["opacity"] >= 100:
        return _layers(comp, layer["children"], under)
    n, x = comp.names[layer["id"]], comp.column()
    comp.tools.append(fc.clear(f"{n}Canvas", comp.frame, comp.comp_frames, (x, 264)))
    top = _layers(comp, layer["children"], f"{n}Canvas")
    if top == f"{n}Canvas":
        return under
    comp.tools.append(fc.merge(f"{n}On", under, top, (comp.column(), 330),
                               extra=[fc.input_("Blend", layer["opacity"] / 100)]))
    return f"{n}On"


def _frame(comp, layer, under):
    """An auto layout frame: its background as a rectangle, then its children on top."""
    back = {key: value for key, value in layer.items() if key not in ("children", "layout")}
    back.update(kind="rect", opacity=100.0, blur=0.0)
    if layer["opacity"] >= 100:
        return _layers(comp, layer["children"], _shape(comp, back, under))
    n, x = comp.names[layer["id"]], comp.column()
    comp.tools.append(fc.clear(f"{n}Canvas", comp.frame, comp.comp_frames, (x, 264)))
    top = _layers(comp, layer["children"], _shape(comp, back, f"{n}Canvas"))
    if top == f"{n}Canvas":
        return under
    comp.tools.append(fc.merge(f"{n}Fade", under, top, (comp.column(), 330),
                               extra=[fc.input_("Blend", layer["opacity"] / 100)]))
    return f"{n}Fade"


def _instance(comp, layer, under):
    """An instance: its main's layers carried into its box, put on as a group would be,
    each tool named <instance's tool name>_<layer's name and place in the main>."""
    made = model.resolve(layer, comp.mains)
    if made is None:
        return under
    n = comp.names[layer["id"]]
    for m, inner in enumerate(model.every(made["children"]), 1):
        comp.names[inner["id"]] = f"{n}_{_base(inner)}{m}"
    return _group(comp, made, under)


def _layers(comp, layers, under):
    for layer in layers:
        if layer["hidden"] or layer["opacity"] <= 0:
            continue
        kind = layer["kind"]
        if kind == "instance":
            under = _instance(comp, layer, under)
            continue
        under = (_group if kind in ("group", "component") else _frame if kind == "frame" else _boolean if kind == "boolean" else _text if kind == "text"
                 else _image if kind == "image" else _shape)(comp, layer, under)
    return under


def comp_text(design, frame, fps, comp_frames, metrics=None):
    """(the comp's text, the design's id) for a clip of comp_frames frames. metrics:
    {"Family|Style": {"em"}} of the faces the text is set in, measured by the page."""
    comp = _Comp(design, frame, comp_frames, metrics)
    comp.tools.append(fc.clear("Canvas", frame, comp_frames, (0, 330)))
    last = _layers(comp, design["layers"], "Canvas")
    comp.tools.append(fc.media_out(last, (COLUMN * (comp.col + 2), 330)))
    gid = model.design_id(design, frame, fps)
    return fc.composition(comp.tools, comp_frames, {TAG: gid}), gid
