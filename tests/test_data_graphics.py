"""Data Graphics (pages/data_graphics/): the tab holding Counters and Graphs - that
it's wired in, that every string is translated, what Ask Buddy is told, and the two
pages themselves: the graphic kept as it's changed, the preview made from the same
clock as the comp, and Add going through the Resolve reader to a (fake) timeline."""

import ast
import os
import re
import tempfile
import unittest
from html.parser import HTMLParser
from unittest import mock

import _paths  # noqa: F401
from _child_reader import ChildReader
from _fake_graphics import Controller, Project, studio

from core import i18n, more_tools, tool_dirs
from core.resolve_bridge import ResolveConnectionError
from core.tools_kb import get_tool
from pages.data_graphics import easing, figures, parts
from pages.data_graphics.counters import model as counters
from pages.data_graphics.graphs import model as graphs

ROOT = _paths.APP / "pages" / "data_graphics"
PAGES = (("counters", "Counters"), ("graphs", "Graphs"))
JOBS = {"data_graphics.read": ("pages.data_graphics.place", "read"),
        "data_graphics.counter": ("pages.data_graphics.place", "counter"),
        "data_graphics.bars": ("pages.data_graphics.place", "bars")}

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAVE_WEB = True
except ImportError:
    HAVE_WEB = False


class _Said(HTMLParser):
    """What a page says: its text, titles, labels and placeholders that aren't marked
    translate="no"."""

    def __init__(self):
        super().__init__()
        self.texts, self._off = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in ("meta", "link", "input", "br"):
            self._off.append(attrs.get("translate") == "no" or tag in ("script", "title", "style")
                             or (self._off[-1] if self._off else False))
        for key in ("title", "aria-label"):
            if attrs.get(key) and attrs.get("translate") != "no":
                self.texts.append(attrs[key])

    def handle_endtag(self, tag):
        if tag not in ("meta", "link", "input", "br") and self._off:
            self._off.pop()

    def handle_data(self, data):
        if data.strip() and not (self._off and self._off[-1]):
            self.texts.append(i18n.normalize(data))


def said_by(folder):
    parser = _Said()
    parser.feed((ROOT / folder / "web" / "index.html").read_text(encoding="utf-8"))
    return parser.texts


class WiringTests(unittest.TestCase):
    def test_its_in_the_sidebar_under_editing_tools(self):
        source = (_paths.APP / "registry.py").read_text(encoding="utf-8")
        rows = [ast.literal_eval(f"({m})") for m in re.findall(r'_tool\(("data_graphics".*?"DataGraphicsPage")', source)]
        self.assertEqual(rows, [("data_graphics", "Data Graphics", "Editing Tools",
                                 "pages.data_graphics.page", "DataGraphicsPage")])

    def test_every_page_draws_the_same_two_tabs(self):
        for folder, _name in PAGES:
            html = (ROOT / folder / "web" / "index.html").read_text(encoding="utf-8")
            tabs = re.findall(r'data-tab="(\w+)" aria-selected="(\w+)">([^<]+)<', html)
            self.assertEqual([(t, label) for t, _s, label in tabs], list(PAGES), folder)
            self.assertEqual([t for t, selected, _l in tabs if selected == "true"], [folder], folder)

    def test_each_page_has_a_folder_for_its_settings(self):
        for name in ("data_graphics", "data_counters", "data_graphs"):
            self.assertIn(name, tool_dirs.BUCKETS)

    def test_what_the_pages_say_is_translated(self):
        said = ["Data Graphics", more_tools.BLURBS["data_graphics"], *counters.PRESETS,
                "Counter added on V2.", "Counter replaced on V2.", "Graph added on V3.", "Graph replaced on V1.",
                "Couldn't add the counter", "Couldn't add the graph", "Open a timeline in Resolve to add this to.",
                "No numbers were found in what was pasted.", "Only the first 12 rows were taken.",
                "3 lines without a number were left out.", "1 line without a number was left out.",
                "Add a row with a number first.", "Auto", "Name", "Number", *graphs.LANE_NAMES.values(),
                *(lane["label"] for lane in counters.lanes(counters.clean({}))),
                "Double-click to choose this bar's colour. Right-click to go back to its automatic colour.", "Adding the counter…", "Adding the graph…",
                "Adding a counter", "Adding a graph", "Play", "Pause", "Name", "Remove this row", "Rows",
                "No rows yet. Add one, or paste them from a spreadsheet.", "Use these rows",
                "Paste two columns – a name and a number – or type a row on each line. "
                "They take the place of the rows you have."]
        for folder, _name in PAGES:
            said += said_by(folder)
        self.assertGreater(len(said), 120)
        for text in said:
            with self.subTest(text):
                self.assertNotEqual(i18n.translate(text, "日本語"), text)

    def test_every_field_in_a_page_is_a_field_of_its_graphic(self):
        for folder, model in (("counters", counters), ("graphs", graphs)):
            html = (ROOT / folder / "web" / "index.html").read_text(encoding="utf-8")
            keys = set(re.findall(r'data-key="(\w+)"', html))
            self.assertGreater(len(keys), 10, folder)
            self.assertLessEqual(keys, set(model.DEFAULTS), folder)
            # The page's own timing select and the dock's offer the same curves.
            selects = re.findall(r'id="(?:part-)?easing"[^>]*>(.*?)</select>', html, re.S)
            self.assertEqual(len(selects), 2, folder)
            for options in selects:
                self.assertEqual(re.findall(r'value="(\w+)"', options), list(easing.EASINGS), folder)

    def test_the_axis_options_are_in_a_panel_of_their_own(self):
        html = (ROOT / "graphs" / "web" / "index.html").read_text(encoding="utf-8")
        page, panel = html.split('id="axes-pop"')
        self.assertIn('id="edit-axes"', page)
        for key in ("subtitle", "x_title", "y_title", "scale", "grid", "steps", "top"):
            self.assertIn(f'data-key="{key}"', panel)
            self.assertNotIn(f'data-key="{key}"', page)
        self.assertEqual(html.count('class="dg-pop"'), html.count("dg-pop-close"))

    def test_each_page_has_the_timing_dock_and_no_timing_panel(self):
        for folder, _name in PAGES:
            html = (ROOT / folder / "web" / "index.html").read_text(encoding="utf-8")
            for part in ('id="dock"', 'id="dock-fold"', 'id="lanes"', 'id="part-kind"', 'id="part-start"',
                         'id="part-length"', 'id="part-easing"', 'id="part-reset"', 'id="curve"'):
                self.assertEqual(html.count(part), 1, f"{folder}: {part}")
            self.assertNotIn("timing-pop", html)
            self.assertNotIn("edit-timing", html)
            self.assertEqual(re.findall(r'<select class="field" id="part-kind">(.*?)</select>', html, re.S)[0].count("<option"),
                             len(parts.KINDS))
    def test_ask_buddy_is_told_what_it_makes_and_what_it_doesnt(self):
        tool = get_tool("data_graphics")
        self.assertTrue(tool.needs_resolve)
        told = " ".join([tool.summary, *tool.capabilities, tool.notes])
        for word in ("Counters", "bar graph", "timing dock", "a lane for every part", "new clip",
                     "Line and pie charts are not built yet"):
            self.assertIn(word, told)
        self.assertNotIn("Neither is built yet", told)


class Mem(dict):
    def save(self):
        self.saved = dict(self)


class Host:
    def __init__(self, controller=None):
        self.buckets = {}
        self.controller, self.connected = controller, controller is not None
        self.busy = []

    def tool_settings(self, tool_id, defaults=None):
        return self.buckets.setdefault(tool_id, Mem(defaults or {}))

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")

    def ensure_connected(self):
        if self.controller is None:
            raise ResolveConnectionError("Resolve isn't running.")
        return self.controller

    def set_busy(self, on, message=""):
        self.busy.append((on, message))


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class PageTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": self.home.name, "BUDDY_RESOLVE_READER": "off"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.open()

    def open(self, controller=None):
        from pages.data_graphics.page import DataGraphicsPage
        self.host = Host(controller)
        self.page = DataGraphicsPage(self.host)
        self.addCleanup(self.page.deleteLater)
        self.counters, self.graphs = self.page.pages["counters"], self.page.pages["graphs"]
        self.sent = []
        for page in (self.counters, self.graphs):
            page.emit = lambda name, data=None, _page=page: self.sent.append((_page.tool_id, name, data))
            if controller is not None:
                page._calls.reader = ChildReader(controller, JOBS)

    def last(self, name):
        return next(data for _tool, n, data in reversed(self.sent) if n == name)

    # ---------------------------------------------------------- the tab --

    def test_it_opens_on_counters_and_isnt_a_placeholder(self):
        self.assertFalse(self.page.is_placeholder)
        self.assertIs(self.page.current(), self.counters)

    def test_a_tab_asked_for_is_shown_and_remembered(self):
        self.counters.on_switch_tab({"tab": "graphs"})
        self.assertIs(self.page.current(), self.graphs)
        self.assertEqual(self.host.buckets["data_graphics"].saved["tab"], "graphs")
        again = type(self.page)(self.host)
        self.addCleanup(again.deleteLater)
        self.assertIs(again.current(), again.pages["graphs"])

    def test_a_tab_it_doesnt_have_changes_nothing(self):
        self.counters.on_switch_tab({"tab": "maps"})
        self.counters.on_switch_tab(None)
        self.assertIs(self.page.current(), self.counters)

    # --------------------------------------------------------- counters --

    def test_the_state_carries_the_counter_and_its_preview(self):
        self.counters.web_ready()
        state = self.last("state")
        self.assertEqual(state["spec"], counters.clean({}))
        self.assertFalse(state["connected"])
        preview = state["preview"]
        self.assertEqual(preview["total"], counters.frames(state["spec"], 24.0))
        self.assertEqual([tuple(k) for k in preview["keys"]], figures.runs(counters.texts(state["spec"], 24.0)))
        self.assertEqual(preview["presets"], list(counters.PRESETS))
        self.assertEqual(state["look"]["face"], {"family": "Arial", "weight": 700, "italic": False})
        self.assertEqual(state["curve_range"], [easing.Y_MIN, easing.Y_MAX])
        self.assertEqual(state["lanes"], counters.lanes(state["spec"]))
        self.assertEqual((state["dock"], state["cap"], preview["arrivals"]), (True, 0.7, {}))
        self.assertIn("Arial", self.last("fonts")["fonts"])

    def test_a_field_changed_is_cleaned_kept_and_sent_back(self):
        self.counters.on_set({"key": "to", "value": "2,5"})
        self.counters.on_set({"key": "seconds", "value": "-9"})
        self.counters.on_set({"key": "nonsense", "value": 1})
        spec = self.last("state")["spec"]
        self.assertEqual((spec["to"], spec["seconds"]), (2.5, counters.MIN_SECONDS))
        self.assertNotIn("nonsense", spec)
        self.assertEqual(self.host.buckets["data_counters"].saved["spec"], spec)
        again = type(self.counters)(self.host)
        self.addCleanup(again.deleteLater)
        self.assertEqual(again.spec, spec)

    def test_a_dragged_curve_is_its_own_and_a_named_one_takes_over_again(self):
        self.counters.on_part({"id": "count", "key": "curve", "value": [0.2, 1.4, 0.6, 0.9]})
        spec = self.last("state")["spec"]
        self.assertEqual((spec["easing"], spec["curve"]), ("custom", [0.2, 1.4, 0.6, 0.9]))
        self.counters.on_set({"key": "easing", "value": "linear"})          # the page's own dropdown
        spec = self.last("state")["spec"]
        self.assertEqual((spec["easing"], spec["curve"]), ("linear", easing.named("linear")))
        self.assertEqual(self.last("state")["lanes"][0]["curve"], easing.named("linear"))
        self.counters.on_part({"id": "count", "key": "easing", "value": "custom"})   # "Your own curve": from where it is
        spec = self.last("state")["spec"]
        self.assertEqual((spec["easing"], spec["curve"]), ("custom", easing.named("linear")))
    def test_a_lane_dragged_in_the_dock_retimes_its_part(self):
        self.counters.web_ready()
        self.assertEqual(self.last("state")["preview"]["arrivals"], {})
        self.counters.on_set({"key": "in_kind", "value": "rise"})           # the page's Arrives dropdown
        arriving = self.last("state")["preview"]["arrivals"]["arrive"]
        self.assertEqual(len(arriving), 13)
        self.assertEqual((arriving[0], arriving[-1]), ([0.0, parts.RISE, 1.0], [1.0, 0.0, 1.0]))
        total = self.last("state")["preview"]["total"]
        self.counters.on_part({"id": "count", "key": "start", "value": 1.0})
        self.counters.on_part({"id": "arrive", "key": "length", "value": "2"})
        self.counters.on_part({"id": "arrive", "key": "curve", "value": [0.1, 1.3, 0.5, 1.0]})
        state = self.last("state")
        spec = state["spec"]
        self.assertEqual((spec["count_start"], spec["in_seconds"], spec["in_easing"], spec["in_curve"]),
                         (1.0, 2.0, "custom", [0.1, 1.3, 0.5, 1.0]))
        self.assertEqual(spec["easing"], easing.DEFAULT)                    # the count's own is untouched
        self.assertEqual(state["preview"]["total"], total + 24)
        self.assertEqual([(lane["id"], lane["start"], lane["length"]) for lane in state["lanes"]],
                         [("count", 1.0, 3.0), ("arrive", 0.0, 2.0)])
        self.assertEqual(self.host.buckets["data_counters"].saved["spec"], spec)
        for bad in ({"id": "title", "key": "start", "value": 1}, {"id": "count", "key": "colour", "value": 1}, None):
            self.counters.on_part(bad)
        self.assertEqual(self.last("state")["spec"], spec)

    def test_the_dock_folds_and_stays_folded(self):
        self.counters.on_option({"key": "dock", "value": False})
        self.assertFalse(self.last("state")["dock"])
        self.assertEqual(self.host.buckets["data_counters"].saved["dock"], False)
        self.counters.on_option({"key": "nonsense", "value": True})
        self.assertNotIn("nonsense", self.host.buckets["data_counters"])
    def test_a_graphs_parts_arrive_as_the_dock_says(self):
        self.graphs.on_part({"id": "title", "key": "kind", "value": "fade"})     # no title: nothing to time
        self.assertEqual(self.graphs.spec["arrivals"], {})
        self.graphs.on_set({"key": "title", "value": "Sales"})
        self.graphs.on_part({"id": "title", "key": "kind", "value": "fade"})
        self.graphs.on_part({"id": "name:1", "key": "kind", "value": "rise"})
        state = self.last("state")
        arriving = state["preview"]["arrivals"]
        self.assertEqual(arriving["title"][0][0], 0.0)
        self.assertEqual(arriving["name:1"][0][1], parts.RISE)
        self.assertNotIn("name:0", arriving)                                 # already there: nothing to play
        self.assertIn("value:0", arriving)                                   # a number fades in by itself
        self.assertEqual(state["lanes"], graphs.lanes(state["spec"]))
        own = {lane["id"] for lane in state["lanes"] if lane["own"]}
        self.assertEqual(own, {"title", "name:1"})
    def test_a_preset_sets_its_fields_and_leaves_the_look(self):
        self.counters.on_set({"key": "colour", "value": "#ff0000"})
        self.counters.on_set({"key": "to", "value": "4200"})
        self.counters.on_set({"key": "seconds", "value": "7"})
        self.counters.on_preset({"name": "Money"})
        spec = self.last("state")["spec"]
        self.assertEqual((spec["from"], spec["to"], spec["seconds"], spec["prefix"]), (0.0, 4200.0, 7.0, "$"))
        self.counters.on_set({"key": "from", "value": "9000"})             # high to low, as typed
        self.counters.on_preset({"name": "Timer"})
        spec = self.last("state")["spec"]
        self.assertEqual((spec["from"], spec["to"], spec["seconds"], spec["form"], spec["prefix"]),
                         (9000.0, 4200.0, 7.0, "clock", ""))
        self.assertEqual((spec["colour"], spec["easing"]), ("#ff0000", easing.DEFAULT))
        self.counters.on_preset({"name": "Fireworks"})
        self.assertEqual(self.last("state")["spec"], spec)

    def test_a_font_takes_a_style_it_has(self):
        controller, _timeline = studio()
        self.open(controller)
        self.counters._read(connect=True)
        self.counters._calls.reader.run()
        self.assertEqual(self.last("fonts")["fonts"]["Inter"], ["Black"])
        self.counters.on_set({"key": "font", "value": "Inter"})
        spec = self.last("state")["spec"]
        self.assertEqual((spec["font"], spec["style"]), ("Inter", "Black"))
        self.assertEqual(self.last("state")["look"]["face"]["weight"], 900)

    def test_the_timeline_read_sets_the_previews_frame_and_rate(self):
        controller, _timeline = studio(fps=30, size=(1080, 1920), name="Short")
        self.open(controller)
        self.counters.on_refresh()
        self.counters._calls.reader.run()
        state = self.last("state")
        self.assertEqual((state["connected"], state["timeline"], state["problem"]), (True, "Short", ""))
        self.assertEqual((state["preview"]["frame"], state["preview"]["fps"]), ([1080, 1920], 30.0))

    def test_no_timeline_open_is_said(self):
        self.open(Controller(Project(None)))
        self.counters.on_refresh()
        self.counters._calls.reader.run()
        state = self.last("state")
        self.assertFalse(state["connected"])
        self.assertIn("Open a timeline", state["problem"])

    def test_add_without_resolve_says_so_and_adds_nothing(self):
        self.counters.on_add()
        self.assertEqual(self.last("alert"), {"title": "Not connected", "text": "Resolve isn't running."})
        self.assertFalse(self.counters.adding)

    def test_add_puts_the_counter_on_the_timeline_behind_the_overlay(self):
        controller, timeline = studio()
        footage = timeline.put(1, 86400, 90000)
        self.open(controller)
        self.counters.on_set({"key": "to", "value": "500"})
        self.counters.on_add()
        self.assertTrue(self.counters.adding)
        self.assertEqual(self.host.busy, [(True, "Adding the counter…")])
        self.assertEqual(self.counters.working_on(), "Adding a counter")
        self.counters.on_add()                                             # pressed again: once is enough
        reader = self.counters._calls.reader
        self.assertEqual([name for name, _args in reader.asked], ["data_graphics.counter"])
        reader.run()
        self.assertFalse(self.counters.adding)
        self.assertEqual(self.host.busy[-1][0], False)
        self.assertEqual(self.last("toast"), {"text": "Counter added on V2."})
        (item,) = timeline.tracks[2]
        self.assertIn('Value = Text { Value = "500" }', item.comp.text)
        self.assertEqual(timeline.tracks[1], [footage])
        self.counters.on_add()
        reader.run()
        self.assertEqual(self.last("toast"), {"text": "Counter replaced on V2."})
        self.assertEqual(len(timeline.tracks[2]), 1)

    def test_replace_unticked_keeps_the_earlier_one(self):
        controller, timeline = studio()
        self.open(controller)
        self.counters.on_option({"key": "replace", "value": False})
        self.assertFalse(self.last("state")["replace"])
        for _ in range(2):
            self.counters.on_add()
            self.counters._calls.reader.run()
        self.assertEqual((len(timeline.tracks[1]), len(timeline.tracks[2])), (1, 1))

    def test_a_failure_is_said_and_the_page_is_ready_again(self):
        controller, timeline = studio()
        timeline.takes_comps = False
        self.open(controller)
        self.counters.on_add()
        self.counters._calls.reader.run()
        alert = self.last("alert")
        self.assertEqual(alert["title"], "Couldn't add the counter")
        self.assertIn("nothing was added", alert["text"])
        self.assertFalse(self.last("state")["adding"])

    # ----------------------------------------------------------- graphs --

    def test_the_graphs_preview_is_the_models_layout_and_growth(self):
        self.graphs.web_ready()
        state = self.last("state")
        spec, preview = state["spec"], state["preview"]
        self.assertEqual(spec, graphs.clean({}))
        self.assertEqual(preview["layout"], graphs.layout(spec, (1920, 1080)))
        self.assertEqual(preview["build"], graphs.build_frames(spec, 24.0))
        self.assertEqual(preview["grown"], graphs.growth(spec, 24.0, preview["build"]))
        self.assertEqual(len(preview["values"]), 5)
        self.assertEqual(preview["values"][3][-1][1], "24")
        self.assertEqual(preview["max_rows"], graphs.MAX_ROWS)

    def test_the_axis_fields_reach_the_preview(self):
        self.graphs.on_set({"key": "scale", "value": True})
        self.graphs.on_set({"key": "top", "value": "48"})
        self.graphs.on_set({"key": "steps", "value": "6"})
        self.graphs.on_set({"key": "y_title", "value": " Units "})
        state = self.last("state")
        self.assertEqual((state["spec"]["top"], state["spec"]["steps"], state["spec"]["y_title"]), (48.0, 6, "Units"))
        plan = state["preview"]["layout"]
        self.assertEqual([m["text"] for m in plan["marks"]], ["0", "8", "16", "24", "32", "40", "48"])
        self.assertEqual(plan["y_title"]["text"], "Units")
        self.graphs.on_set({"key": "top", "value": ""})                       # cleared: worked out again
        self.assertEqual(self.last("state")["spec"]["top"], "")
        self.assertEqual(self.last("state")["preview"]["layout"]["marks"][-1]["text"], "25")

    def test_rows_typed_are_read_as_numbers(self):
        self.graphs.on_rows({"rows": [{"label": "North", "value": "1,250"}, {"label": "South", "value": "abc"},
                                      {"label": "East", "value": "-3"}, "junk"]})
        self.assertEqual(self.last("state")["spec"]["rows"],
                         [{"label": "North", "value": 1250.0}, {"label": "South", "value": 0.0},
                          {"label": "East", "value": 0.0}])
        self.assertEqual(len(self.host.buckets["data_graphs"].saved["spec"]["rows"]), 3)

    def test_pasted_rows_take_the_tables_place(self):
        self.graphs.on_paste({"text": "North\t1,250\nSouth\t980\nnothing here"})
        self.assertEqual(self.last("state")["spec"]["rows"],
                         [{"label": "North", "value": 1250.0}, {"label": "South", "value": 980.0}])
        self.assertEqual(self.last("toast"), {"text": "1 line without a number was left out."})
        self.graphs.on_paste({"text": "just words"})
        self.assertEqual(self.last("toast"), {"text": "No numbers were found in what was pasted."})
        self.assertEqual(len(self.last("state")["spec"]["rows"]), 2)
        self.graphs.on_paste({"text": "\n".join(f"R{i}\t{i}" for i in range(20))})
        self.assertEqual(self.last("toast"), {"text": "Only the first 12 rows were taken."})

    def test_a_bar_takes_a_timing_of_its_own_only_when_dragged(self):
        self.graphs.web_ready()
        before = self.last("state")
        self.assertEqual({lane["own"] for lane in before["lanes"]}, {False})
        self.graphs.on_part({"id": "bar:3", "key": "curve", "value": [0.2, 1.4, 0.5, 0.9]})
        self.graphs.on_part({"id": "bar:3", "key": "start", "value": 0.0})
        state = self.last("state")
        lane = next(lane for lane in state["lanes"] if lane["id"] == "bar:3")
        was = next(lane for lane in before["lanes"] if lane["id"] == "bar:3")
        self.assertEqual((lane["own"], lane["easing"], lane["curve"], lane["start"]), (True, "custom", [0.2, 1.4, 0.5, 0.9], 0.0))
        self.assertEqual(lane["length"], was["length"])                     # kept from what it played
        self.assertNotEqual(state["preview"]["grown"][3], before["preview"]["grown"][3])
        self.assertEqual(state["preview"]["grown"][2], before["preview"]["grown"][2])
        self.graphs.on_part({"id": "bar:3", "key": "reset", "value": None})
        self.assertEqual(self.last("state")["preview"]["grown"], before["preview"]["grown"])
    def test_a_colour_picked_for_a_bar_is_its_own_and_stays_with_it(self):
        self.graphs.on_bar_colour({"index": 3, "value": "#FF00AA"})
        state = self.last("state")
        self.assertEqual(state["spec"]["rows"][3]["colour"], "#ff00aa")
        self.assertEqual(state["preview"]["layout"]["bars"][3]["colour"], "#ff00aa")
        self.assertEqual(state["preview"]["layout"]["bars"][2]["colour"], graphs.PALETTE[2])
        rows = [{"label": r["label"], "value": str(r["value"]), "was": i} for i, r in enumerate(self.graphs.spec["rows"])]
        del rows[0]
        self.graphs.on_rows({"rows": rows})
        bars = self.last("state")["preview"]["layout"]["bars"]
        self.assertEqual([b["label"] for b in bars], ["Tue", "Wed", "Thu", "Fri"])
        self.assertEqual(bars[2]["colour"], "#ff00aa")                              # Thu kept its colour
        for bad in ({"index": 2, "value": "pink"}, {"index": 99, "value": "#000000"}, None):
            self.graphs.on_bar_colour(bad)
        self.assertEqual(self.last("state")["preview"]["layout"]["bars"][2]["colour"], "#ff00aa")
        self.graphs.on_bar_colour({"index": 2, "value": None})                      # back to the graph's
        self.assertEqual(self.last("state")["preview"]["layout"]["bars"][2]["colour"], graphs.PALETTE[2])

    def test_a_rows_own_timings_stay_with_it_when_rows_are_edited(self):
        self.graphs.on_part({"id": "bar:3", "key": "start", "value": 0.0})
        self.graphs.on_part({"id": "name:3", "key": "kind", "value": "pop"})
        rows = [{"label": r["label"], "value": str(r["value"]), "was": i} for i, r in enumerate(self.graphs.spec["rows"])]
        del rows[1]                                                         # Tue removed: Thu is now the third row
        rows.append({"label": "Sat", "value": "5", "was": -1})
        self.graphs.on_rows({"rows": rows})
        spec = self.last("state")["spec"]
        self.assertEqual([r["label"] for r in spec["rows"]], ["Mon", "Wed", "Thu", "Fri", "Sat"])
        self.assertEqual([("timing" in r, "name_in" in r) for r in spec["rows"]],
                         [(False, False), (False, False), (True, True), (False, False), (False, False)])
    def test_a_graph_with_no_rows_isnt_added(self):
        controller, timeline = studio()
        self.open(controller)
        self.graphs.on_rows({"rows": []})
        self.graphs.on_add()
        self.assertEqual(self.last("toast"), {"text": "Add a row with a number first."})
        self.assertEqual(self.graphs._calls.reader.asked, [])
        self.assertEqual(timeline.tracks[1], [])

    def test_add_puts_the_graph_on_the_timeline(self):
        controller, timeline = studio()
        self.open(controller)
        self.graphs.on_set({"key": "title", "value": "Sales"})
        self.graphs.on_add()
        self.assertEqual(self.host.busy, [(True, "Adding the graph…")])
        self.graphs._calls.reader.run()
        self.assertEqual(self.last("toast"), {"text": "Graph added on V1."})
        (item,) = timeline.tracks[1]
        self.assertIn('StyledText = Input { Value = "Sales", },', item.comp.text)
        self.assertEqual(item.end - item.start, graphs.frames(self.graphs.spec, 24.0))


if __name__ == "__main__":
    unittest.main()
