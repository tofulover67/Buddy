"""A bar graph as a Fusion comp - the text of a .comp file (core/fusion_comp.py). Pure.

Each bar is a Background of its colour kept to a RectangleMask whose Height and Center
are keyed, so it grows from the floor line (measured on Studio 21.1.2: a mask's Width
and Height are shares of the frame's width and height, its Center 0..1 with y up, and
a keyed Height of 0 draws nothing; CornerRadius 0.5 rounds a bar's corners by a
quarter of its width). The labels, values and titles are ordinary Text+: a value's
text is keyed as it counts up and its Center rides the top of its bar.
The value axis is more of the same: a thin masked Background per line across, a Text+
per number up the side; the title up the side is a Text+ turned a quarter (AngleZ 90).
Every part that arrives (model.arrivals: its own start, length and curve) does so by
a Transform after it and the Blend of the Merge that puts it on, keyed frame by frame
(text_look.arriving). Everything is merged in order onto a clear canvas into MediaOut1.
"""

from core import fusion_comp as fc

from .. import figures, parts, text_look
from . import model

TAG = "BuddyGraph"                  # CustomData key on the comp: the graph's id


def _keys(values):
    return fc.straight(list(enumerate(values)))


def comp_text(spec, frame, fps, comp_frames, metrics=None):
    """(the comp's text, the graph's id) for a clip of comp_frames frames."""
    metrics = text_look.clean_metrics(metrics)
    fw, fh = frame
    gid = model.graph_id(spec, frame, fps)
    plan = model.layout(spec, frame)
    grown = model.growth(spec, fps, comp_frames)
    said = model.value_texts(spec, grown)
    arriving = {key: parts.states(timing, fps, comp_frames) for key, timing in model.arrivals(spec).items()}
    tools = [fc.clear("Canvas", frame, comp_frames, (0, 99))]
    last = "Canvas"

    def on(name, tool, pos, extra=()):
        nonlocal last
        tools.append(fc.merge(name, last, tool, pos, extra))
        last = name

    def arrive(name, part, size, pivot, pos):
        """A text (or a canvas of them) put on, arriving as its part does."""
        top, extra = name, []
        states = arriving.get(part)
        if states:
            made, top, alphas = text_look.arriving(f"{name}Arrive", name, frame, states, size, pivot, pos)
            tools.extend(made)
            if alphas:
                tools.append(fc.spline(f"{name}Blend", alphas))
                extra.append(fc.wired("Blend", f"{name}Blend"))
        on(f"{name}On", top, (pos[0] + 110, pos[1]), extra)

    def middle(t):
        return t["x"], t["baseline"] - text_look.CAP * t["size"] / 2

    axis = plan["axis"]
    tools.append(fc.block("FloorMask", "RectangleMask", [
        fc.input_("Center", ((axis["x0"] + axis["x1"]) / 2 / fw, 1 - axis["y"] / fh)),
        fc.input_("Width", (axis["x1"] - axis["x0"]) / fw), fc.input_("Height", axis["h"] / fh)], (-110, 66)))
    tools.append(fc.solid("Floor", spec["colour"] + "66", frame, comp_frames, (0, 66), [fc.mask("FloorMask")]))
    on("FloorOn", "Floor", (110, 99))

    # The value axis: a faint line across at each mark above the floor, the marks' numbers up the side.
    if plan["grid"]:
        fade = []
        if arriving.get("grid"):
            tools.append(fc.spline("GridBlend", _keys([a for a, _dy, _s in arriving["grid"]])))
            fade = [fc.wired("Blend", "GridBlend")]
        for k, mark in enumerate(plan["marks"][1:], 1):
            tools.append(fc.block(f"Line{k}Mask", "RectangleMask", [
                fc.input_("Center", ((axis["x0"] + axis["x1"]) / 2 / fw, 1 - mark["y"] / fh)),
                fc.input_("Width", (axis["x1"] - axis["x0"]) / fw), fc.input_("Height", axis["h"] / fh)], (-110, -66 * k)))
            tools.append(fc.solid(f"Line{k}", spec["colour"] + "33", frame, comp_frames, (0, -66 * k), [fc.mask(f"Line{k}Mask")]))
            on(f"Line{k}On", f"Line{k}", (110, -66 * k), fade)
    if plan["tick"]:
        # The numbers together on a canvas of their own, so they arrive as one.
        tick = plan["tick"]
        tools.append(fc.clear("MarksCanvas", frame, comp_frames, (-330, 0)))
        marks = "MarksCanvas"
        for k, mark in enumerate(plan["marks"]):
            tools.append(text_look.text_plus(f"Mark{k}", frame, comp_frames, spec, metrics, tick["size"], tick["x"],
                                             mark["y"] + tick["drop"], (-220, -66 * k), text=mark["text"], align="right"))
            tools.append(fc.merge(f"Mark{k}On", marks, f"Mark{k}", (-330, -66 * k - 33)))
            marks = f"Mark{k}On"
        tools.append(fc.block("Marks", "Transform", [fc.feed("Input", marks)], (-440, 0)))       # the group, by one name
        arrive("Marks", "scale", tick["size"], model.marks_middle(plan), (-440, 33))

    for k, (bar, grow) in enumerate(zip(plan["bars"], grown), 1):
        x = 110 * (k + 1)
        heights = [bar["h"] * p for p in grow]
        tools.append(fc.spline(f"Bar{k}Height", _keys([h / fh for h in heights])))
        tools += fc.path(f"Bar{k}Path", [(0, bar["cx"] / fw), (comp_frames - 1, bar["cx"] / fw)],
                         _keys([1 - (bar["base"] - h / 2) / fh for h in heights]), (x - 55, -33))
        mask = [fc.wired("Center", f"Bar{k}Path"), fc.input_("Width", bar["w"] / fw), fc.wired("Height", f"Bar{k}Height")]
        if spec["rounded"]:
            mask.append(fc.input_("CornerRadius", model.CORNER))
        tools.append(fc.block(f"Bar{k}Mask", "RectangleMask", mask, (x - 55, 0)))
        tools.append(fc.solid(f"Bar{k}", bar["colour"], frame, comp_frames, (x, 0), [fc.mask(f"Bar{k}Mask")]))
        on(f"Bar{k}On", f"Bar{k}", (x, 99))

    if plan["label"]:
        size, baseline = plan["label"]["size"], plan["label"]["baseline"]
        for k, bar in enumerate(plan["bars"], 1):
            if not bar["label"]:
                continue
            tools.append(text_look.text_plus(f"Label{k}", frame, comp_frames, spec, metrics, size,
                                             bar["cx"], baseline, (110 * (k + 1), 165), text=bar["label"]))
            arrive(f"Label{k}", f"name:{k - 1}", size, (bar["cx"], baseline - text_look.CAP * size / 2), (110 * (k + 1), 198))

    if plan["value"]:
        size, gap = plan["value"]["size"], plan["value"]["gap"]
        for k, (bar, grow, texts) in enumerate(zip(plan["bars"], grown, said), 1):
            x = 110 * (k + 1)
            points = [text_look.center_point(frame, metrics, size, bar["cx"], bar["base"] - bar["h"] * p - gap) for p in grow]
            tools.append(fc.text_spline(f"Value{k}Text", figures.runs(texts)))
            tools += fc.path(f"Value{k}Path", [(0, points[0][0]), (comp_frames - 1, points[0][0])],
                             _keys([y for _x, y in points]), (x - 55, 231))
            tools.append(text_look.text_plus(f"Value{k}", frame, comp_frames, spec, metrics, size, bar["cx"], 0,
                                             (x, 264), keyed=f"Value{k}Text", center=f"Value{k}Path"))
            # A number fades in as its part says - by default, as its bar starts to grow.
            alphas = [a for a, _dy, _s in arriving.get(f"value:{k - 1}", [])][:comp_frames]
            tools.append(fc.spline(f"Value{k}Blend", _keys(alphas + [1.0] * (comp_frames - len(alphas)))))
            on(f"Value{k}On", f"Value{k}", (x, 297), [fc.wired("Blend", f"Value{k}Blend")])

    for name, key in (("Title", "title"), ("Subtitle", "subtitle"), ("AlongTitle", "x_title")):
        t = plan[key]
        if t:
            tools.append(text_look.text_plus(name, frame, comp_frames, spec, metrics, t["size"], t["x"], t["baseline"],
                                             (0, 330), text=t["text"]))
            arrive(name, key, t["size"], middle(t), (110, 330))
    if plan["y_title"]:
        t = plan["y_title"]
        tools.append(text_look.text_plus("SideTitle", frame, comp_frames, spec, metrics, t["size"], t["x"], t["y"],
                                         (550, 330), text=t["text"], turned=True))
        arrive("SideTitle", "y_title", t["size"], (t["x"] - text_look.CAP * t["size"] / 2, t["y"]), (660, 330))

    tools.append(fc.media_out(last, (110 * (len(plan["bars"]) + 3), 99)))
    return fc.composition(tools, comp_frames, {TAG: gid}), gid
