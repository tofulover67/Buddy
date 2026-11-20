#!/usr/bin/env python3
"""
Graphs - one of the Data Graphics tab's sub-tabs (see pages/data_graphics/page.py):
a bar graph from numbers typed in or pasted from a spreadsheet, its bars growing one
after another, added to the timeline as a new clip at the playhead.

The view (web/) has the rows, the titles and axis, how the bars grow and the look, and
plays the graph as it will play in Resolve: where everything sits is model.layout(),
how far each bar has grown model.growth() and how each other part arrives
model.arrivals(), the same ones the comp's keys are made from (comp.py). The dock
under the preview has a lane for every part (model.lanes). Add to timeline is
place.bars: a new clip, nothing already there changed (../graphic_page.py). Bars only
so far - lines and pies are in docs/DATA_GRAPHICS_PLAN.md.

Protocol:
    to the view    state, fonts, toast, alert
    from the view  set, part, rows, paste, bar_colour, option, add, refresh, switch_tab
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
        return {"frame": list(self.frame), "fps": fps, "total": total, "build": build,
                "layout": M.layout(self.spec, self.frame), "grown": grown,
                "values": [[list(k) for k in figures.runs(texts)] for texts in M.value_texts(self.spec, grown)],
                "arrivals": self.arrivals(M.arrivals(self.spec), fps, total), "max_rows": M.MAX_ROWS}

    def _set_rows(self, rows):
        self.spec = M.clean({**self.spec, "rows": rows})
        self._save()
        self._push_state()

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
        self._set_rows(rows)

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
        self._set_rows(rows)

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
