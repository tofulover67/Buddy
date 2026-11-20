"""Data Graphics in Resolve: what the pages read (the open timeline's size and rate,
the fonts Text+ can draw) and the two things they add - a counter and a bar graph,
each a NEW clip at the playhead on a track free of anyone else's clips
(core/fusion_place.py). Nothing already on the timeline is changed; only Buddy's own
earlier graphic of the same kind on that very spot is replaced.

No Qt: these run in the Resolve reader's process (core/resolve_reader_child.py JOBS),
and in Buddy itself where that process can't.
"""

from core import fusion_place
from core.resolve_bridge import ResolveConnectionError

from .counters import comp as counter_comp
from .counters import model as counter_model
from .graphs import comp as graph_comp
from .graphs import model as graph_model

MAX_FACES = 4000


def _open(controller):
    project = controller.current_project()
    if project is None:
        raise ResolveConnectionError("No project is open in Resolve.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise ResolveConnectionError("Open a timeline in Resolve first.")
    return project, timeline


def _fonts(controller):
    """{family: [styles]} - Fusion's own list, the exact names a Text+ takes."""
    try:
        listed = controller.resolve.Fusion().FontManager.GetFontList() or {}
    except Exception:
        return {}
    fonts, faces = {}, 0
    for family in sorted(listed, key=str.lower):
        styles = listed[family]
        if not isinstance(styles, dict) or not styles:
            continue
        fonts[str(family)] = sorted(str(s) for s in styles)
        faces += len(styles)
        if faces > MAX_FACES:
            break
    return fonts


def read(controller, fonts=True):
    """{"timeline", "frame", "fps", "fonts"}: the open timeline (name "" when there's
    none - the fonts are still listed) and, when asked, the fonts."""
    out = {"timeline": "", "frame": [1920, 1080], "fps": 24.0}
    project = controller.current_project()
    timeline = project.GetCurrentTimeline() if project is not None else None
    if timeline is not None:
        frame, fps, _drop = fusion_place.timeline_info(timeline)
        out.update(timeline=str(timeline.GetName() or ""), frame=list(frame), fps=fps)
    if fonts:
        out["fonts"] = _fonts(controller)
    return out


def _placed(timeline, done, fps, frame):
    return {**done, "timeline": str(timeline.GetName() or ""), "fps": fps, "frame": list(frame),
            "seconds": (done["end"] - done["start"]) / fps}


def counter(controller, spec, metrics=None, replace=True):
    """A counter at the playhead. Returns where it went; raises PlaceError."""
    project, timeline = _open(controller)
    spec = counter_model.clean(spec)
    frame, fps, _drop = fusion_place.timeline_info(timeline)
    done = fusion_place.place_comp(
        project, timeline, lambda n: counter_comp.comp_text(spec, frame, fps, n, metrics),
        counter_comp.TAG, counter_model.frames(spec, fps), what="counter", replace=bool(replace))
    return _placed(timeline, done, fps, frame)


def bars(controller, spec, metrics=None, replace=True):
    """A bar graph at the playhead. Returns where it went; raises PlaceError."""
    project, timeline = _open(controller)
    spec = graph_model.clean(spec)
    if not spec["rows"]:
        raise fusion_place.PlaceError("There are no numbers to draw yet.")
    frame, fps, _drop = fusion_place.timeline_info(timeline)
    done = fusion_place.place_comp(
        project, timeline, lambda n: graph_comp.comp_text(spec, frame, fps, n, metrics),
        graph_comp.TAG, graph_model.frames(spec, fps), what="graph", replace=bool(replace))
    return _placed(timeline, done, fps, frame)
