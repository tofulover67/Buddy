#!/usr/bin/env python3
"""
Counters - one of the Data Graphics tab's sub-tabs (see pages/data_graphics/page.py):
a number that runs up or down to where it's set - followers, money, a percentage, a
timer - added to the timeline as a new Text+ clip at the playhead.

The view (web/) has the number's start and end, how long it runs and holds, how it's
written (decimals, separator, what goes before and after, or a clock) and its look,
and plays it as it will play in Resolve: the texts it shows are model.texts(), the
same ones the comp's keys are made from (comp.py). The dock under the preview has a
lane for the count and one for its arriving (model.lanes). Add to timeline is
place.counter: a new clip, nothing already there changed (../graphic_page.py).

Protocol:
    to the view    state, fonts, toast, alert
    from the view  set, part, preset, option, add, refresh, switch_tab, undo, redo, reset
"""

from .. import figures, parts, place
from ..graphic_page import GraphicPage, web_dir
from . import model as M


class CountersPage(GraphicPage):
    tool_id = "data_counters"
    display_name = "Counters"
    web_dir = web_dir(__file__)
    MODEL = M
    JOB = "data_graphics.counter"
    WHAT = "counter"
    ADDING = "Adding the counter…"

    def preview(self):
        fps = self.preview_fps()
        total = M.frames(self.spec, fps)
        return {"frame": list(self.frame), "fps": fps, "total": total,
                "keys": [list(k) for k in figures.runs(M.texts(self.spec, fps))],
                "place": M.place_of(self.spec, self.frame), "align": self.spec["align"], "presets": list(M.PRESETS),
                "arrivals": self.arrivals({"arrive": M.arrival(self.spec)}, fps, total)}

    def on_preset(self, payload):
        """A way of writing it (Money, Percent, a clock...): the numbers typed stay as typed."""
        spec = M.with_preset(self.spec, (payload or {}).get("name"))
        if spec is None or self.adding:
            return
        self.change_to(spec, None)

    def here(self, controller, **args):
        return place.counter(controller, **args)

    def added_text(self, result):
        return (f"Counter replaced on V{result['track']}." if result.get("replaced")
                else f"Counter added on V{result['track']}.")
