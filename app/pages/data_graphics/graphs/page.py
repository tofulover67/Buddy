#!/usr/bin/env python3
"""
Graphs - one of the Data Graphics tab's sub-tabs (see pages/data_graphics/page.py):
a graph from numbers typed in or pasted from a spreadsheet - bars growing one after
another, a line drawing itself on left to right, or a pie sweeping round - added to the timeline as a new
clip at the playhead.

The view (web/) has the rows, the titles and axis, how the bars grow and the look, and
plays the graph as it will play in Resolve: where everything sits is model.layout(),
how far each bar has grown model.growth() and how each other part arrives
model.arrivals(), the same ones the comp's keys are made from (comp.py). The dock
under the preview has a lane for every part (model.lanes). Add to timeline is
place.bars (bars or a line): a new clip, nothing already there changed
(../graphic_page.py). A pie (model.pie_of) sweeps round clockwise from 12 o'clock.

Protocol:
    to the view    state, fonts, toast, alert
    from the view  set, part, rows, paste, bar_colour, option, add, refresh, switch_tab, undo, redo, reset
"""

from .. import easing, figures, place
from ..graphic_page import GraphicPage, web_dir
from . import model as M

ROW_KEEPS = ("timing", "name_in", "value_in", "colour")      # what a row keeps of its own when the table is edited


class GraphsPage(GraphicPage):
    tool_id = "data_graphs"
    display_name = "Graphs"
    web_dir = web_dir(__file__)
    MODEL = M
    JOB = "data_graphics.bars"
    WHAT = "graph"
    ADDING = "Adding the graph…"

    def preview(self):
        fps = self.preview_fps()
        build, total = M.build_frames(self.spec, fps), M.frames(self.spec, fps)
        grown = M.growth(self.spec, fps, build)             # after the build every bar is whole
        plan = M.layout(self.spec, self.frame)
        out = {"frame": list(self.frame), "fps": fps, "total": total, "build": build,
               "layout": plan, "grown": grown,
               "values": [[list(k) for k in figures.runs(texts)] for texts in M.value_texts(self.spec, grown)],
               "arrivals": self.arrivals(M.arrivals(self.spec, self.frame), fps, total), "max_rows": M.MAX_ROWS}
        if plan["pie"]:
            out["drawn"] = M.drawn(self.spec, fps, total)        # how far round it has swept, frame by frame
        if plan["line"]:
            # How much of the line is drawn and how big each dot is, frame by frame.
            out.update(points=[list(p) for p in M.points(plan)], drawn=M.drawn(self.spec, fps, total),
                       dots=[[list(s) for s in dot] for dot in M.dot_states(self.spec, fps, total, self.frame)])
        return out

    def _set_rows(self, rows, merge=None):
        self.change_to(M.clean({**self.spec, "rows": rows}), merge)

    def on_rows(self, payload):
        """The table as the view has it now: [{"label", "value"}] - a value still being
        typed that isn't a number yet counts as 0."""
        if self.adding:
            return
        rows, had = [], self.spec["rows"]
        for row in (payload or {}).get("rows") or []:
            if isinstance(row, dict):
                value = figures.parse(row.get("value"))
                rows.append({"label": row.get("label"), "value": max(0.0, value) if value is not None else 0.0})
                # "was": the row it was before this edit - its own timings and colour stay with it.
                was = row.get("was")
                if isinstance(was, int) and not isinstance(was, bool) and 0 <= was < len(had):
                    rows[-1].update({k: had[was][k] for k in ROW_KEEPS if had[was].get(k)})
        # Typing in the table is one step a run; a row added or taken out is a step of its own.
        self._set_rows(rows, "rows" if len(rows) == len(had) else None)

    def on_bar_colour(self, payload):
        """A colour picked for one bar ("#rrggbb"), or None: it takes the graph's again."""
        index, value = (payload or {}).get("index"), (payload or {}).get("value")
        rows = [dict(row) for row in self.spec["rows"]]
        if self.adding or not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(rows):
            return
        if value is not None:
            value = easing.colour(value, None)
            if value is None:
                return                                  # not a colour: the bar keeps the one it has
        rows[index].pop("colour", None)
        if value:
            rows[index]["colour"] = value
        self._set_rows(rows, f"colour:{index}")

    def on_paste(self, payload):
        """Rows pasted from a spreadsheet: they take the table's place."""
        if self.adding:
            return
        rows, skipped, over = M.parse_rows((payload or {}).get("text"))
        if not rows:
            return self.emit("toast", {"text": "No numbers were found in what was pasted."})
        self._set_rows(rows)
        if over:
            self.emit("toast", {"text": f"Only the first {M.MAX_ROWS} rows were taken."})
        elif skipped:
            self.emit("toast", {"text": (f"{len(skipped)} lines without a number were left out." if len(skipped) > 1
                                         else "1 line without a number was left out.")})

    def ready_to_add(self):
        return "" if self.spec["rows"] else "Add a row with a number first."

    def here(self, controller, **args):
        return place.bars(controller, **args)

    def added_text(self, result):
        return (f"Graph replaced on V{result['track']}." if result.get("replaced")
                else f"Graph added on V{result['track']}.")
