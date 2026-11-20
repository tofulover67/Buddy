"""A counter as a Fusion comp: one Text+ whose text is keyed on every frame the number
changes, into MediaOut1 - the text of a .comp file (core/fusion_comp.py). Pure.

Why keyed text and not an expression: both import and render (measured on Studio
21.1.2, docs/RESOLVE_API_GUIDE.md), but keyed text is exactly what Buddy's preview
showed - any separator, the clock form, the easing - with no formatting written twice
in Lua. The Text+ is an ordinary one: its font, size, colour and place stay editable.
Its arriving (parts.py) is a Transform and a Merge after it, keyed the same way.
"""

from core import fusion_comp as fc

from .. import figures, parts, text_look
from . import model

TAG = "BuddyCounter"                # CustomData key on the comp: the counter's id


def comp_text(spec, frame, fps, comp_frames, metrics=None):
    """(the comp's text, the counter's id) for a clip of comp_frames frames."""
    metrics = text_look.clean_metrics(metrics)
    gid = model.counter_id(spec, frame, fps)
    at = model.place_of(spec, frame)
    keys = figures.runs(model.texts(spec, fps, comp_frames))
    tools = [fc.text_spline("CounterText", keys),
             text_look.text_plus("Counter", frame, comp_frames, spec, metrics, at["size"], at["x"], at["baseline"],
                                 (0, 0), keyed="CounterText", align=spec["align"])]
    last = "Counter"
    states = parts.states(model.arrival(spec), fps, comp_frames)
    if states:
        # Arriving: scaled and moved by a Transform, faded by the Merge that puts it on a clear canvas.
        pivot = (at["x"], at["baseline"] - text_look.CAP * at["size"] / 2)
        made, top, alphas = text_look.arriving("Arrive", "Counter", frame, states, at["size"], pivot, (110, 0))
        tools += made
        extra = []
        if alphas:
            tools.append(fc.spline("ArriveBlend", alphas))
            extra.append(fc.wired("Blend", "ArriveBlend"))
        tools += [fc.clear("Canvas", frame, comp_frames, (110, 99)), fc.merge("CounterOn", "Canvas", top, (220, 99), extra)]
        last = "CounterOn"
    tools.append(fc.media_out(last, (330, 0)))
    return fc.composition(tools, comp_frames, {TAG: gid}), gid
