"""Interactions in Resolve: the one thing it adds - the interaction as a NEW clip at the
playhead, on a track free of anyone else's clips (core/fusion_place.py). Nothing
already on the timeline is changed; only Buddy's own earlier interaction on that very
spot is replaced. What the page reads (the open timeline's size and rate, Resolve's
fonts) is Data Graphics' read (pages/data_graphics/place.py).

No Qt: this runs in the Resolve reader's process (core/resolve_reader_child.py JOBS),
and in Buddy itself where that process can't.
"""

import os

from core import fusion_place
from core.resolve_bridge import ResolveConnectionError

from . import comp, model


def add(controller, spec, metrics=None, replace=True):
    """The interaction at the playhead. Returns where it went; raises PlaceError."""
    project = controller.current_project()
    if project is None:
        raise ResolveConnectionError("No project is open in Resolve.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise ResolveConnectionError("Open a timeline in Resolve first.")
    spec = model.clean(spec)
    for el in spec["elements"]:
        # A Loader with no file renders nothing, and says so only in Fusion.
        if el["kind"] == "image" and not os.path.isfile(el["path"]):
            raise fusion_place.PlaceError(f"The image \"{os.path.basename(el['path'])}\" isn't where it was. "
                                          "Put it back, or remove it from the stage.")
    frame, fps, _drop = fusion_place.timeline_info(timeline)
    done = fusion_place.place_comp(
        project, timeline, lambda n: comp.comp_text(spec, frame, fps, n, metrics),
        comp.TAG, model.frames(spec, fps), what="interaction", replace=bool(replace))
    return {**done, "timeline": str(timeline.GetName() or ""), "fps": fps, "frame": list(frame),
            "seconds": (done["end"] - done["start"]) / fps}
