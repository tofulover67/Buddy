#!/usr/bin/env python3
"""
Graphs - one of the Data Graphics tab's sub-tabs (see pages/data_graphics/page.py):
bar, line and pie charts from typed or pasted numbers, drawing themselves on the timeline.

Not built yet. The view (web/) says what it will do and that it's coming;
docs/DATA_GRAPHICS_PLAN.md is how it's meant to be built.

Protocol:
    from the view  switch_tab
"""

import os

from core.web_page import WebToolPage


class GraphsPage(WebToolPage):
    tool_id = "data_graphs"
    display_name = "Graphs"
    category = "Editing Tools"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def on_switch_tab(self, payload):
        self._graphics.show_tab((payload or {}).get("tab"))
