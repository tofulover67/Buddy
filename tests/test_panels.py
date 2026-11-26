"""Panels (pages/panels/), the page: that the tool is wired in as a tab of Design Studio
and translated, and the page itself - the state it sends the view, each action the view
sends back, the steps Undo walks, and Add through the (fake) Resolve reader. The panel
(model, solve, styles, clock) is test_panels_model.py's, the view's drawing
test_panels_view.py's, the comp and the job test_panels_comp.py's."""

import json
import os
import re
import tempfile
import unittest
from html.parser import HTMLParser
from unittest import mock

import _paths  # noqa: F401
from _child_reader import ChildReader
from _fake_graphics import studio

from core import i18n, more_tools, tool_dirs
from core.resolve_bridge import ResolveConnectionError
from pages.design.comp import face_key
from pages.panels import catalogue, comp, model as M, styles

ROOT = _paths.APP / "pages" / "panels"
JOBS = {"data_graphics.read": ("pages.data_graphics.place", "read"),
        "panels.add": ("pages.panels.place", "add")}
# What the first state says, every key the view reads.
STATE_KEYS = ("connected", "timeline", "problem", "frame", "fps", "spec", "showing", "solved", "drawn", "chrome",
              "clock", "shelf", "face", "bold_face", "accents", "cursors", "cursor_dip", "limits", "replace",
              "adding", "can_undo", "can_redo", "is_template", "images", "animations")
# Names of other things, the same in every language.
PROPER = ("Mac", "Windows")

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAVE_WEB = True
except ImportError:
    HAVE_WEB = False


class _Said(HTMLParser):
    """What the page says: its text, titles and labels that aren't marked translate="no"."""

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


def shelf_names():
    """Every name the shelf and the looks row show."""
    return [*catalogue.GROUPS, *(made["name"] for made in catalogue.LAYOUTS.values()),
            *(made["name"] for made in catalogue.COMPONENTS.values()),
            *catalogue.KIND_NAMES.values(), *(n for n in catalogue.STYLE_NAMES.values() if n not in PROPER)]


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web" / "panels.js").read_text(encoding="utf-8")

    def test_its_a_tab_of_design_studio_with_a_settings_folder_of_its_own(self):
        source = (_paths.APP / "registry.py").read_text(encoding="utf-8")
        # The rail has Design Studio, and not Panels by itself.
        self.assertIn('"pages.design_studio.page", "DesignStudioPage"', source)
        self.assertNotIn('"PanelsPage"', source)
        self.assertIn('data-tab="panels" aria-selected="true"', self.html)
        self.assertIn("panels", tool_dirs.BUCKETS)

    def test_the_job_is_named_in_the_readers_process(self):
        from core import resolve_reader_child
        self.assertEqual(resolve_reader_child.JOBS["panels.add"], ("pages.panels.place", "add"))

    def test_what_the_page_says_is_translated(self):
        parser = _Said()
        parser.feed(self.html)
        said = ["Panels", "Design Studio", "Design Studio tools", more_tools.BLURBS["design_studio"],
                "Panel added on V2.", "Panel replaced on V1.", "Couldn't add the panel", "Adding the panel…",
                "Adding a panel", f"A panel holds up to {M.MAX_PAGES} pages.", f"A panel holds up to {M.MAX_RULES} rules.",
                "No project is open in Resolve.", "Open a timeline in Resolve first.",
                'The image "logo.png" isn\'t where it was. Put it back, or take it off its image box.',
                "There's no image box for a picture here. Add one from + Add first.",
                # What the script writes that the pattern below doesn't find.
                "Goes into Sign in.", "+ Page", "On a click on", "Starts", "Rename", "click", "after", "at", "a rule", "not reached",
                "counts up", "Click on", "When its time is up", "How long this page shows. Click to change it.", "Cancel", "Done",
                "Seconds", "Stop recording", "Move earlier", "Move later", "Remove", "Move up", "More", "Nothing", "Window", "Click on", "When", "is drawn past 60 % →",
                "At 1.5 s →", "Remove this rule", "Percent", "With", "with", "is drawn past", "%, go to", "Page",
                "Chart or progress bar", "Cursor", "A click", "A click on Get started", "A rule", "Pressed", "Hovered",
                "drawn on", "Play", "Pause", "Stop", "Nothing was recorded.", "Back to the template.", "Undo",
                "Fade", "Slide", "Match",
                *shelf_names(), *parser.texts,
                *re.findall(r'(?:textContent =|toast\(|[?:]) "([A-Z][^"]{8,})"', self.script)]
        self.assertGreater(len(said), 150)
        for text in ("Record the cursor", "Try it", "Add to timeline", "Click one to make the page in view that layout.",
                     "Click a thing to change it, and again for what's in it. Drag it to move it.",
                     "Click a thing to change it. Drag it to move it; drag the window itself to place it in the frame."):
            self.assertIn(text, said)
        for text in said:
            if text in PROPER:
                continue
            with self.subTest(text):
                self.assertNotEqual(i18n.translate(text, "日本語"), text)

    def test_the_sample_rows_keep_their_lines_in_every_language(self):
        hint = re.search(r'id="chart-rows"[^>]*placeholder="([^"]*)"', self.html).group(1)
        hint = hint.replace("&#10;", "\n")
        for language in i18n.LANGUAGES[1:]:
            with self.subTest(language):
                said = i18n.translate(hint, language)
                self.assertNotEqual(said, hint)
                self.assertEqual(said.count("\n"), hint.count("\n"))
                self.assertEqual(said.count("\t"), hint.count("\t"))

    def test_the_translations_are_lf(self):
        for name in ("panels.json", "design_studio.json"):
            raw = (_paths.APP / "core" / "translations" / name).read_bytes()
            self.assertNotIn(b"\r\n", raw, name)


class Mem(dict):
    def save(self):
        self.saved = json.loads(json.dumps(self))


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
        self.template = M.clean({})
        self.open()

    def open(self, controller=None):
        from pages.panels.page import PanelsPage
        self.host = Host(controller)
        self.page = PanelsPage(self.host)
        self.addCleanup(self.page.deleteLater)
        self.sent = []
        self.page.emit = lambda name, data=None: self.sent.append((name, data))
        if controller is not None:
            self.reader = self.page._calls.reader = ChildReader(controller, JOBS)

    def last(self, name):
        return next(data for n, data in reversed(self.sent) if n == name)

    def recorded(self):
        """The page's panel with a cursor of its own, as one recorded - not the story's."""
        self.page.spec = M.clean({**self.page.spec, "auto": False})

    def states(self):
        return [data for n, data in self.sent if n == "state"]

    def page_of(self, page_id):
        return M.page_of(self.page.spec, page_id)

    def first(self, role, page_id="p1"):
        """The id of the first box of a role on a page."""
        return next(box["id"] for box in M.every(self.page_of(page_id)["root"]) if box["role"] == role)

    # ------------------------------------------------------------ state --

    def test_its_a_real_page_under_editing_tools(self):
        self.assertFalse(self.page.is_placeholder)
        self.assertEqual((self.page.tool_id, self.page.category), ("panels", "Editing Tools"))

    def test_the_first_state_has_everything_the_view_reads(self):
        self.page.web_ready()
        state = self.last("state")
        for key in STATE_KEYS:
            self.assertIn(key, state)
        json.dumps(state)                                           # it goes to the view as JSON
        self.assertEqual(state["spec"], self.template)
        self.assertEqual((state["showing"], state["connected"], state["is_template"]), ("p1", False, True))
        self.assertEqual((state["can_undo"], state["can_redo"], state["adding"], state["replace"]),
                         (False, False, False, True))
        self.assertEqual(set(state["solved"]), {"p1", "p2", "p3"})
        self.assertEqual(set(state["drawn"]), {"p1", "p2", "p3"})
        self.assertEqual((state["frame"], state["fps"]), ([1920, 1080], 24.0))
        self.assertEqual(state["limits"], {"width": [M.MIN_WIDTH, M.MAX_WIDTH], "seconds": [M.MIN_SECONDS, M.MAX_SECONDS],
                                           "pages": M.MAX_PAGES, "rules": M.MAX_RULES})
        self.assertEqual(state["accents"], list(catalogue.ACCENTS))
        self.assertEqual(state["face"], {"family": "Arial", "weight": 400, "italic": False})
        self.assertEqual(state["bold_face"], {"family": "Arial", "weight": 700, "italic": False})
        shelf = state["shelf"]
        self.assertEqual([t["id"] for t in shelf["layouts"]], list(catalogue.LAYOUTS))
        self.assertEqual([t["id"] for t in shelf["components"]], list(catalogue.COMPONENTS))
        self.assertEqual(shelf["groups"], list(catalogue.GROUPS))

    def test_the_shelf_is_drawn_again_only_when_its_look_changes(self):
        tiles = len(catalogue.LAYOUTS) + len(catalogue.COMPONENTS)
        with mock.patch.object(styles, "tile", wraps=styles.tile) as drawn:
            self.page.shelf()
            self.assertEqual(drawn.call_count, tiles)
            self.page.shelf()
            for key, value in (("width", 800), ("x", 40), ("seconds", 9), ("cursor", "dot")):
                self.page.on_set({"key": key, "value": value})
            self.assertEqual(drawn.call_count, tiles)
            for n, (key, value) in enumerate((("kind", "phone"), ("style", "glass"), ("accent", "#e4572e"),
                                              ("dark", False)), 2):
                self.page.on_set({"key": key, "value": value})
                self.assertEqual(drawn.call_count, tiles * n, key)

    def test_a_new_font_takes_a_plain_and_a_bold_style_it_has(self):
        self.page.fonts = {"Arial": ["Regular", "Bold"], "Inter": ["Light", "Book", "Semibold", "Black"],
                           "Poster": ["Black"]}
        self.page.on_set({"key": "font", "value": "Inter"})
        self.assertEqual([self.page.spec[k] for k in ("font", "face", "bold_face")], ["Inter", "Book", "Semibold"])
        self.page.on_set({"key": "font", "value": "Poster"})        # one style: it's both
        self.assertEqual([self.page.spec[k] for k in ("font", "face", "bold_face")], ["Poster", "Black", "Black"])
        self.page.on_set({"key": "font", "value": "Unknown"})       # none known: the faces stay
        self.assertEqual([self.page.spec[k] for k in ("font", "face", "bold_face")], ["Unknown", "Black", "Black"])
        self.assertEqual(set(self.page.metrics()), {face_key("Unknown", "Black")})      # one face, measured once

    def test_the_metrics_have_both_faces(self):
        metrics = self.page.metrics()
        self.assertEqual(set(metrics), {face_key("Arial", "Regular"), face_key("Arial", "Bold")})
        for measured in metrics.values():
            self.assertEqual(set(measured), {"em", "baseline"})
            self.assertGreater(measured["em"], 0)

    # ----------------------------------------------------------- actions --

    def test_a_field_changed_is_cleaned_kept_and_sent_back(self):
        self.recorded()
        for key, value in (("kind", "phone"), ("style", "glass"), ("accent", "#E4572E"), ("dark", False),
                           ("width", 9999), ("x", 30), ("y", "40"), ("cursor", "dot"), ("seconds", 12),
                           ("build", {"on": False, "stagger": 0.1, "seconds": 0.5}), ("page_keys", [[1.0, "p2"]]),
                           ("nonsense", 1)):
            self.page.on_set({"key": key, "value": value})
        spec = self.last("state")["spec"]
        self.assertEqual([spec[k] for k in ("kind", "style", "accent", "dark", "width", "x", "y", "cursor", "seconds")],
                         ["phone", "glass", "#e4572e", False, M.MAX_WIDTH, 30.0, 40.0, "dot", 12.0])
        self.assertEqual(spec["build"], {"on": False, "stagger": 0.1, "seconds": 0.5})
        self.assertEqual(spec["page_keys"], [[1.0, "p2", spec["go_with"]]])
        self.assertNotIn("nonsense", spec)
        self.assertEqual(self.host.buckets["panels"].saved["spec"], spec)
        again = type(self.page)(self.host)
        self.addCleanup(again.deleteLater)
        self.assertEqual(again.spec, spec)

    def test_set_leaves_the_pages_rules_and_cursor_to_their_own_actions(self):
        for key, value in (("pages", []), ("rules", [{"box": "b1", "page": "p2"}]), ("path", [[0, 1, 1]]),
                           ("clicks", [[0.1, 0.2]]), ("start", "p2")):
            self.page.on_set({"key": key, "value": value})
        self.assertEqual(self.page.spec, self.template)
        self.assertEqual(self.page.undo_steps, [])

    def test_one_thing_about_a_box_is_changed(self):
        self.recorded()
        button = self.first("button")
        for key, value in (("text", "  Go   on "), ("w", 140), ("reacts", False), ("go", {"page": "p3", "with": "fade"})):
            self.page.on_box({"id": button, "key": key, "value": value})
        box = M.find(self.page.spec, button)["box"]
        self.assertEqual((box["text"], box["w"], box["reacts"], box["go"]), ("Go on", 140.0, False, {"page": "p3", "with": "fade"}))
        mine = self.page.spec
        self.page.on_box({"id": "b999", "key": "text", "value": "nobody"})
        self.page.on_box({"id": button, "key": "nonsense", "value": 1})
        self.page.on_box(None)
        self.assertEqual(self.page.spec, mine)

    def test_a_component_goes_last_into_the_page_in_view_and_is_picked(self):
        before = [box["id"] for box in self.page_of("p1")["root"]["children"]]
        self.page.on_insert({"component": "button", "into": "", "at": -1})
        root = self.page_of("p1")["root"]
        self.assertEqual([box["id"] for box in root["children"][:-1]], before)
        new = root["children"][-1]
        self.assertEqual((new["role"], new["text"]), ("button", "Button"))
        self.assertEqual(self.last("picked"), {"id": new["id"]})
        # A component of many boxes: its own box is picked, not one inside it.
        self.page.on_page_show({"id": "p2"})
        self.page.on_insert({"component": "card", "into": "", "at": -1})
        card = self.page_of("p2")["root"]["children"][-1]
        self.assertEqual((card["role"], card["variant"], self.last("picked")), ("section", "card", {"id": card["id"]}))
        # Into a box that holds nothing: after it, in what holds it.
        text = self.first("text", "p2")
        self.page.on_insert({"component": "badge", "into": text, "at": -1})
        parent = M.find(self.page.spec, text)["parent"]
        ids = [box["id"] for box in parent["children"]]
        self.assertEqual(M.find(self.page.spec, ids[ids.index(text) + 1])["box"]["role"], "badge")
        sent = len(self.sent)
        self.page.on_insert({"component": "nothing", "into": ""})
        self.page.on_insert({"component": "button", "into": "b999"})
        self.assertEqual(len(self.sent), sent)

    def test_a_box_is_removed_but_never_a_pages_own(self):
        button = self.first("button")
        self.page.on_remove_box({"id": button})
        self.assertIsNone(M.find(self.page.spec, button))
        mine = self.page.spec
        self.page.on_remove_box({"id": self.page_of("p1")["root"]["id"]})
        self.page.on_remove_box({"id": "b999"})
        self.assertEqual(self.page.spec, mine)

    def test_a_page_becomes_a_layout_from_the_shelf(self):
        self.page.on_layout({"layout": "pricing"})                  # the page in view
        page = self.page_of("p1")
        self.assertEqual((page["layout"], page["name"]), ("pricing", "Pricing"))     # still the template's name: it follows
        self.page.on_page_rename({"id": "p2", "name": "Home"})
        self.page.on_layout({"page": "p2", "layout": "player"})
        self.assertEqual((self.page_of("p2")["layout"], self.page_of("p2")["name"]), ("player", "Home"))   # a name of its own is kept
        self.page.on_layout({"page": "p3", "layout": "chat"})
        page = self.page_of("p3")
        self.assertEqual((page["layout"], page["name"]), ("chat", "Chat"))          # named after its layout, it follows
        mine = self.page.spec
        self.page.on_layout({"layout": "nothing"})
        self.page.on_layout({"page": "p9", "layout": "chat"})
        self.assertEqual(self.page.spec, mine)

    def test_a_page_is_added_shown_renamed_and_removed(self):
        self.page.on_page_add({})
        state = self.last("state")
        self.assertEqual([p["id"] for p in state["spec"]["pages"]], ["p1", "p2", "p3", "p4"])
        self.assertEqual((state["showing"], self.page_of("p4")["name"]), ("p4", "Page 4"))
        self.page.on_page_rename({"id": "p4", "name": "  Home   page "})
        self.assertEqual(self.page_of("p4")["name"], "Home page")
        self.page.on_page_rename({"id": "p4", "name": "   "})
        self.page.on_page_rename({"id": "p9", "name": "Nobody"})
        self.assertEqual(self.page_of("p4")["name"], "Home page")
        self.page.on_page_remove({"id": "p4"})                      # the page in view: the start page is shown
        state = self.last("state")
        self.assertEqual(([p["id"] for p in state["spec"]["pages"]], state["showing"]), (["p1", "p2", "p3"], "p1"))
        for page_id in ("p2", "p3"):
            self.page.on_page_remove({"id": page_id})
        mine = self.page.spec
        self.page.on_page_remove({"id": "p1"})                      # the only page stays
        self.assertEqual(self.page.spec, mine)

    def test_its_said_when_a_panel_has_all_the_pages_it_may(self):
        while len(self.page.spec["pages"]) < M.MAX_PAGES:
            self.page.on_page_add()
        steps = len(self.page.undo_steps)
        self.page.on_page_add()
        self.assertEqual(len(self.page.spec["pages"]), M.MAX_PAGES)
        self.assertEqual(len(self.page.undo_steps), steps)
        self.assertEqual(self.last("toast"), {"text": f"A panel holds up to {M.MAX_PAGES} pages."})

    def test_showing_a_page_is_no_step(self):
        self.page.on_page_show({"id": "p2"})
        self.assertEqual((self.page.showing, self.last("state")["showing"]), ("p2", "p2"))
        self.assertEqual((self.page.undo_steps, self.page.spec), ([], self.template))
        sent = len(self.sent)
        for payload in ({"id": "p2"}, {"id": "p9"}, {}, None):     # already there, or no such page: nothing
            self.page.on_page_show(payload)
        self.assertEqual(len(self.sent), sent)

    def test_how_boxes_answer_the_panels_and_a_boxs_own(self):
        self.page.on_answers({"id": "", "state": "hover", "key": "grow", "value": 10})
        self.page.on_answers({"id": "", "state": "hover", "key": "seconds", "value": "0.5"})
        answers = self.page.spec["answers"]
        self.assertEqual((answers["hover"]["grow"], answers["hover_seconds"]), (10.0, 0.5))
        button = self.first("button")
        self.page.on_answers({"id": button, "state": "press", "key": "lift", "value": 5})
        own = M.find(self.page.spec, button)["box"]["states"]
        self.assertEqual(own["press"]["lift"], 5.0)
        self.assertEqual(own["hover"], self.page.spec["answers"]["hover"])           # it starts from the panel's
        self.page.on_answers({"id": button, "state": "hover", "key": "reset", "value": None})
        self.assertIsNone(M.find(self.page.spec, button)["box"]["states"])
        mine = self.page.spec
        self.page.on_answers({"id": "", "state": "sleep", "key": "grow", "value": 1})
        self.page.on_answers({"id": "b999", "state": "hover", "key": "grow", "value": 1})
        self.assertEqual(self.page.spec, mine)

    def test_a_rule_is_added_changed_and_removed(self):
        self.page.on_rule({"id": "", "key": "", "value": None})
        (rule,) = self.page.spec["rules"]
        self.assertEqual((rule["id"], rule["box"], rule["page"], rule["over"]),
                         ("r1", self.first("chart", "p2"), "p3", 60.0))      # the first chart, the page after its own
        self.page.on_rule({"id": "r1", "key": "over", "value": 80})
        self.page.on_rule({"id": "r1", "key": "with", "value": "fade"})
        self.assertEqual([self.page.spec["rules"][0][k] for k in ("over", "with")], [80.0, "fade"])
        self.page.on_rule({"id": "r1", "key": "page", "value": "p9"})          # a page there isn't
        self.assertEqual(self.page.spec["rules"][0]["page"], "p3")
        self.page.on_rule_remove({"id": "r1"})
        self.page.on_rule_remove({"id": "r9"})
        self.assertEqual(self.page.spec["rules"], [])

    def test_its_said_when_a_panel_has_all_the_rules_it_may(self):
        for _ in range(M.MAX_RULES):
            self.page.on_rule({"id": "", "key": "", "value": None})
        self.assertEqual(len(self.page.spec["rules"]), M.MAX_RULES)
        self.page.on_rule({"id": "", "key": "", "value": None})
        self.assertEqual(len(self.page.spec["rules"]), M.MAX_RULES)
        self.assertEqual(self.last("toast"), {"text": f"A panel holds up to {M.MAX_RULES} rules."})

    def test_a_recording_becomes_the_cursor_and_the_clips_length(self):
        samples = [[i / 20, 10 + i, 40] for i in range(61)]              # three seconds, a straight line
        self.page.on_recorded({"samples": samples, "clicks": [[1.0, 1.2]], "seconds": 3.0})
        state = self.last("state")
        self.assertEqual(state["spec"]["path"], [[0.0, 10.0, 40.0], [3.0, 70.0, 40.0]])
        self.assertEqual((state["spec"]["clicks"], state["spec"]["seconds"]), ([[1.0, 1.2]], 3.0))
        self.assertEqual(state["clock"]["total"], 72)
        self.assertFalse(state["is_template"])
        for bad in (None, {}, {"samples": []}, {"samples": "lots"}):
            self.page.on_recorded(bad)
        self.assertEqual(self.page.spec, state["spec"])
        self.page.on_undo()                                              # one step: the template's cursor is back
        self.assertEqual(self.page.spec, self.template)

    def test_undo_redo_and_reset_walk_the_steps(self):
        self.page.on_set({"key": "kind", "value": "phone"})
        self.page.on_page_add()
        self.page.on_box({"id": self.first("button"), "key": "text", "value": "Enter"})
        self.assertEqual(len(self.page.undo_steps), 3)
        self.assertTrue(self.last("state")["can_undo"])
        mine = self.page.spec
        self.page.on_reset()
        self.assertEqual(self.page.spec, self.template)
        self.page.on_undo()
        self.assertEqual(self.page.spec, mine)
        for _ in range(3):
            self.page.on_undo()
        self.assertEqual(self.page.spec, self.template)
        self.assertEqual(self.last("state")["can_redo"], True)
        self.page.on_undo()                                              # nothing more to take back
        self.assertEqual(self.page.spec, self.template)
        self.page.on_redo()
        self.assertEqual(self.page.spec["kind"], "phone")
        self.assertEqual(len(self.page.spec["pages"]), 3)
        for _ in range(3):
            self.page.on_redo()
        self.assertEqual(self.page.spec, self.template)                  # the reset, again
        self.assertEqual(self.host.buckets["panels"].saved["spec"], self.template)

    def test_a_drag_is_one_step(self):
        from pages.data_graphics import graphic_page
        clock = [100.0]
        with mock.patch.object(graphic_page.time, "monotonic", side_effect=lambda: clock[0]):
            for x in (33, 34, 35, 36):                                   # the window dragged: many sets, one step
                self.page.on_set({"key": "x", "value": x})
                clock[0] += 0.05
            self.assertEqual(len(self.page.undo_steps), 1)
            clock[0] += 5
            self.page.on_set({"key": "x", "value": 20})
            self.assertEqual(len(self.page.undo_steps), 2)

    def test_what_is_said_when_its_added_or_replaced(self):
        self.assertEqual(self.page.added_text({"track": 2, "replaced": False}), "Panel added on V2.")
        self.assertEqual(self.page.added_text({"track": 1, "replaced": True}), "Panel replaced on V1.")
        self.assertIsNone(self.page.working_on())

    def test_nothing_changes_while_its_being_added(self):
        self.page.adding = True
        self.assertEqual(self.page.working_on(), "Adding a panel")
        button, root = self.first("button"), self.page_of("p1")["root"]["id"]
        self.page.on_set({"key": "kind", "value": "phone"})
        self.page.on_box({"id": button, "key": "text", "value": "x"})
        self.page.on_insert({"component": "button", "into": root})
        self.page.on_remove_box({"id": button})
        self.page.on_layout({"layout": "chat"})
        self.page.on_page_add()
        self.page.on_page_remove({"id": "p3"})
        self.page.on_page_rename({"id": "p3", "name": "Elsewhere"})
        self.page.on_answers({"id": "", "state": "hover", "key": "grow", "value": 20})
        self.page.on_rule({"id": "", "key": "", "value": None})
        self.page.on_recorded({"samples": [[0, 1, 1], [1, 2, 2]], "clicks": [], "seconds": 2})
        self.page.on_reset()
        self.page.on_undo()
        self.assertEqual(self.page.spec, self.template)
        self.assertEqual(self.page.undo_steps, [])
        self.assertFalse(any(n == "picked" for n, _d in self.sent))

    # ------------------------------------------------------------ round 2 --

    def picture(self, name="logo.png", size=(400, 200)):
        """A made-up picture in the test's own folder: its path."""
        from PySide6.QtGui import QImage
        path = os.path.join(self.home.name, name)
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(0xffff8a1e)
        self.assertTrue(image.save(path))
        return path

    def image_boxes(self, n=2):
        """n image boxes put last into the page in view: their ids."""
        for _ in range(n):
            self.page.on_insert({"component": "image", "into": "", "at": -1})
        return [box["id"] for box in M.every(self.page_of("p1")["root"]) if box["role"] == "image"]

    def test_the_state_has_the_pictures_and_every_boxs_animations(self):
        state = self.page._state()
        self.assertEqual(state["images"], {})
        self.assertEqual(set(state["animations"]), {box["id"] for box in M.every(self.page_of("p1")["root"])})
        field = self.first("field")
        self.assertEqual([a["id"] for a in state["animations"][field]], ["arrive", "type", "answer"])
        self.page.on_page_show({"id": "p2"})
        self.assertEqual(set(self.last("state")["animations"]), {box["id"] for box in M.every(self.page_of("p2")["root"])})

    def test_a_picture_is_picked_from_a_file_for_an_image_box_and_taken_off(self):
        from pages.panels import page as page_module
        (photo,) = self.image_boxes(1)
        path = self.picture()
        with mock.patch.object(page_module.QFileDialog, "getOpenFileName", return_value=(path, "")) as asked:
            self.page.on_image({"id": photo})
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(asked.call_args[0][1:], ("Choose an image", "", "Images (*.png *.jpg *.jpeg)"))
        self.assertEqual(M.find(self.page.spec, photo)["box"]["image"], {"path": os.path.normpath(path), "px": [400, 200]})
        self.assertEqual(self.last("picked"), {"id": photo})
        state = self.last("state")
        self.assertTrue(state["images"][photo].startswith("file:///") and state["images"][photo].endswith("/logo.png"))
        self.assertIn("image", {p["kind"] for p in state["drawn"]["p1"]})
        self.assertEqual(self.host.buckets["panels"]["image_dir"], os.path.dirname(path))
        # Asked again it starts where the last one was; nothing picked changes nothing.
        steps = len(self.page.undo_steps)
        with mock.patch.object(page_module.QFileDialog, "getOpenFileName", return_value=("", "")) as asked:
            self.page.on_image({"id": photo})
        self.assertEqual(asked.call_args[0][2], os.path.dirname(path))
        self.assertEqual(len(self.page.undo_steps), steps)
        with mock.patch.object(page_module.QFileDialog, "getOpenFileName") as asked:
            self.page.on_image({"id": self.first("button")})                  # not an image box: nothing asked
            self.page.on_image({"id": "b999"})
            self.page.on_image(None)
        self.assertEqual(asked.call_count, 0)
        broken = os.path.join(self.home.name, "broken.png")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("not a picture")
        with mock.patch.object(page_module.QFileDialog, "getOpenFileName", return_value=(broken, "")):
            self.page.on_image({"id": photo})
        self.assertEqual(self.last("toast"), {"text": "Buddy couldn't read that image. PNG and JPEG images can be added."})
        self.assertEqual(M.find(self.page.spec, photo)["box"]["image"]["px"], [400, 200])
        self.page.on_image_clear({"id": photo})
        self.assertIsNone(M.find(self.page.spec, photo)["box"]["image"])
        self.assertEqual(self.last("state")["images"], {})
        self.page.on_undo()
        self.assertIsNotNone(M.find(self.page.spec, photo)["box"]["image"])

    def test_pictures_dropped_go_to_the_picked_image_box_then_the_empty_ones(self):
        self.assertTrue(self.page.file_drops)
        self.assertTrue(self.page.accepts_drops())
        self.page.on_files_dropped([self.picture("a.png")])                  # no image box to take it
        self.assertEqual(self.last("toast"), {"text": "There's no image box for a picture here. Add one from + Add first."})
        one, two = self.image_boxes(2)
        self.page.on_pick({"id": two})
        notes = os.path.join(self.home.name, "notes.txt")
        with open(notes, "w", encoding="utf-8") as fh:
            fh.write("not a picture")
        self.page.on_files_dropped([notes, self.picture("a.png"), self.picture("b.jpg", (300, 300)), self.picture("c.png")])
        said = {bid: os.path.basename((M.find(self.page.spec, bid)["box"]["image"] or {}).get("path", "")) for bid in (one, two)}
        self.assertEqual(said, {two: "a.png", one: "b.jpg"})                 # the picked one first; no box for the third
        self.assertEqual(self.last("toast"), {"text": "Buddy couldn't read that image. PNG and JPEG images can be added."})
        # With nothing picked and every box full there's nowhere for it.
        self.page.on_pick({"id": ""})
        self.page.on_files_dropped([self.picture("d.png")])
        self.assertEqual(self.last("toast"), {"text": "There's no image box for a picture here. Add one from + Add first."})
        # Picked again, a dropped picture replaces its picture.
        self.page.on_pick({"id": one})
        self.page.on_files_dropped([self.picture("d.png")])
        self.assertTrue(M.find(self.page.spec, one)["box"]["image"]["path"].endswith("d.png"))
        self.page.on_pick({"id": "b999"})
        self.assertEqual(self.page.picked, "")
        self.page.adding = True
        self.assertFalse(self.page.accepts_drops())

    def test_a_page_is_made_from_a_layout_copied_moved_and_started_on(self):
        self.recorded()
        self.page.on_page_add({"layout": "chat"})
        state = self.last("state")
        self.assertEqual((state["showing"], self.page_of("p4")["layout"], self.page_of("p4")["name"]), ("p4", "chat", "Chat"))
        self.assertEqual(len(self.page.undo_steps), 1)                  # the page and its layout: one step
        self.page.on_page_copy({"id": "p2"})
        state = self.last("state")
        self.assertEqual([(p["id"], p["name"]) for p in state["spec"]["pages"]],
                         [("p1", "Sign in"), ("p2", "Dashboard"), ("p5", "Dashboard 2"), ("p3", "Settings"), ("p4", "Chat")])
        self.assertEqual(state["showing"], "p5")
        self.page.on_page_move({"id": "p5", "at": 0})
        self.assertEqual([p["id"] for p in self.page.spec["pages"]], ["p5", "p1", "p2", "p3", "p4"])
        self.assertEqual((self.page.spec["start"], self.page.showing), ("p1", "p5"))
        self.page.on_page_start({"id": "p5"})
        self.assertEqual(self.last("state")["spec"]["start"], "p5")
        steps = len(self.page.undo_steps)
        for nothing in (lambda: self.page.on_page_start({"id": "p5"}), lambda: self.page.on_page_start({"id": "p9"}),
                        lambda: self.page.on_page_move({"id": "p5", "at": 0}), lambda: self.page.on_page_copy({"id": "p9"}),
                        lambda: self.page.on_page_move(None), lambda: self.page.on_page_copy(None)):
            nothing()
        self.assertEqual(len(self.page.undo_steps), steps)
        while len(self.page.spec["pages"]) < M.MAX_PAGES:
            self.page.on_page_add()
        self.sent.clear()
        self.page.on_page_copy({"id": "p1"})
        self.assertEqual(len(self.page.spec["pages"]), M.MAX_PAGES)
        self.assertEqual(self.last("toast"), {"text": f"A panel holds up to {M.MAX_PAGES} pages."})

    def test_a_layer_goes_beside_another_and_is_copied(self):
        root = self.page_of("p1")["root"]
        first, second = root["children"][0]["id"], root["children"][1]["id"]
        self.page.on_beside({"id": second, "target": first, "side": "right"})
        row = self.page_of("p1")["root"]["children"][0]
        self.assertEqual((row["dir"], [c["id"] for c in row["children"]]), ("row", [first, second]))
        steps = len(self.page.undo_steps)
        self.page.on_beside({"id": second, "target": first, "side": "right"})     # where it is: nothing changes
        self.page.on_beside({"id": root["id"], "target": first, "side": "left"})
        self.page.on_beside(None)
        self.assertEqual(len(self.page.undo_steps), steps)
        self.sent.clear()
        button = self.first("button")
        self.page.on_copy_box({"id": button})
        kids = M.find(self.page.spec, button)["parent"]["children"]
        at = [k["id"] for k in kids].index(button)
        self.assertEqual((kids[at + 1]["role"], kids[at + 1]["text"]), ("button", kids[at]["text"]))
        self.assertEqual(self.last("picked"), {"id": kids[at + 1]["id"]})          # the new one is picked
        self.assertEqual(self.page.picked, kids[at + 1]["id"])
        steps = len(self.page.undo_steps)
        self.page.on_copy_box({"id": self.page_of("p1")["root"]["id"]})
        self.page.on_copy_box(None)
        self.assertEqual(len(self.page.undo_steps), steps)
        self.page.on_undo()
        self.assertIsNone(M.find(self.page.spec, kids[at + 1]["id"]))

    def test_the_pages_one_after_another(self):
        self.recorded()
        self.page.on_page_add({"layout": "player"})
        self.assertNotIn("p4", [c["page"] for c in self.last("state")["clock"]["pages"]])     # nothing leads to it
        self.page.on_flow({})
        state = self.last("state")
        each = state["spec"]["seconds"] / 4
        self.assertEqual(state["spec"]["page_keys"], [[each, "p2", "slide"], [2 * each, "p3", "slide"], [3 * each, "p4", "slide"]])
        self.assertEqual([c["page"] for c in state["clock"]["pages"]], ["p1", "p2", "p3", "p4"])
        self.page.on_page_start({"id": "p3"})                       # from the page the clip starts on, round the list
        self.page.on_flow()
        self.assertEqual([key[1] for key in self.page.spec["page_keys"]], ["p4", "p1", "p2"])
        steps = len(self.page.undo_steps)
        self.page.on_flow()                                         # the same again: nothing changes
        self.assertEqual(len(self.page.undo_steps), steps)
        for page_id in ("p1", "p2", "p4"):
            self.page.on_page_remove({"id": page_id})
        mine = self.page.spec
        self.page.on_flow()                                         # one page: nowhere to go
        self.assertEqual(self.page.spec, mine)

    def test_the_story_is_sent_and_changed(self):
        self.page.web_ready()
        state = self.last("state")
        self.assertEqual([part["page"] for part in state["story"]], ["p1", "p2", "p3"])
        self.assertTrue(state["spec"]["auto"])
        self.page.on_story({"page": "p3", "key": "secs", "value": 4})
        self.assertEqual((self.last("state")["story"][2]["secs"], self.page.spec["seconds"]), (4.0, 9.0))
        self.page.on_story({"page": "p1", "key": "by", "value": ""})
        self.assertEqual(self.page.spec["page_keys"], [[2.0, "p2", "slide"]])
        steps = len(self.page.undo_steps)
        for nothing in ({"page": "p9", "key": "secs", "value": 2}, {"page": "p1", "key": "by", "value": ""}, None):
            self.page.on_story(nothing)
        self.assertEqual(len(self.page.undo_steps), steps)
        # A click on one box starts another.
        self.page.on_page_add({"layout": "player"})
        boxes = {b["text"]: b["id"] for b in M.every(self.page_of("p4")["root"]) if b["text"]}
        self.page.on_starts({"id": boxes["3:41"], "by": boxes["Next"]})
        self.assertEqual(M.find(self.page.spec, boxes["3:41"])["box"]["run"], {"by": boxes["Next"], "seconds": 3.0})
        steps = len(self.page.undo_steps)
        self.page.on_starts({"id": boxes["Slow Morning"], "by": boxes["Next"]})
        self.page.on_starts(None)
        self.assertEqual(len(self.page.undo_steps), steps)
        # A cursor recorded takes the story's place; "auto" set again gives it back.
        self.page.on_recorded({"samples": [[0, 10, 10], [1, 30, 30], [2, 50, 50]], "clicks": [], "seconds": 2})
        self.assertFalse(self.page.spec["auto"])
        self.page.on_set({"key": "auto", "value": True})
        self.assertTrue(self.page.spec["auto"])
        self.assertEqual(self.page.spec["seconds"], sum(part["secs"] for part in self.last("state")["story"]))

    def test_a_panel_kept_from_before_the_story_gets_one(self):
        old = {k: v for k, v in M.clean({**self.template, "auto": False}).items() if k != "auto"}
        self.host.buckets["panels"]["spec"] = old
        from pages.panels.page import PanelsPage
        page = PanelsPage(self.host)
        self.addCleanup(page.deleteLater)
        self.assertTrue(page.spec["auto"])
        self.assertEqual(page.spec, M.clean(page.spec))

    def test_a_layer_is_moved_and_its_typing_and_arrival_are_set(self):
        root = self.page_of("p1")["root"]
        first, second = root["children"][0]["id"], root["children"][1]["id"]
        self.page.on_move({"id": second, "into": root["id"], "at": 0})
        self.assertEqual([c["id"] for c in self.page_of("p1")["root"]["children"][:2]], [second, first])
        steps = len(self.page.undo_steps)
        self.page.on_move({"id": root["id"], "into": root["id"], "at": 0})    # refused: nothing changes
        self.page.on_move({"id": second, "into": self.first("button"), "at": 0})
        self.assertEqual(len(self.page.undo_steps), steps)
        self.page.on_move({"id": second, "into": root["id"], "at": -1})       # to the end
        self.assertEqual(self.page_of("p1")["root"]["children"][-1]["id"], second)
        field = self.first("field")
        self.page.on_box({"id": field, "key": "type", "value": {"text": "tess@example.com"}})
        self.page.on_box({"id": field, "key": "arrive", "value": {"kind": "pop"}})
        box = M.find(self.page.spec, field)["box"]
        self.assertEqual((box["type"]["text"], box["type"]["start"], box["arrive"]), ("tess@example.com", "click", {"kind": "pop"}))
        state = self.last("state")
        self.assertIn(field, state["clock"]["typed"])
        self.assertEqual({a["id"]: a["on"] for a in state["animations"][field]}["type"], True)
        self.page.on_undo()
        self.assertIsNone(M.find(self.page.spec, field)["box"]["arrive"])

    # ----------------------------------------------------------- Resolve --

    def test_add_puts_it_on_the_timeline_through_the_reader(self):
        controller, timeline = studio()
        self.open(controller)
        self.page.on_set({"key": "kind", "value": "browser"})
        self.page.on_add()
        self.assertTrue(self.page.adding)
        self.assertTrue(self.last("state")["adding"])
        self.page.on_set({"key": "kind", "value": "phone"})              # nothing changes while it's being added
        self.reader.run()
        self.assertFalse(self.page.adding)
        name, args = self.reader.asked[-1]
        self.assertEqual((name, args["spec"]["kind"], args["replace"]), ("panels.add", "browser", True))
        self.assertEqual(set(args["metrics"]), {face_key("Arial", "Regular"), face_key("Arial", "Bold")})
        (item,) = timeline.tracks[1]
        self.assertEqual(item.comp.GetData(comp.TAG), M.panel_id(self.page.spec, (1920, 1080), 24.0))
        self.assertEqual(self.last("toast"), {"text": "Panel added on V1."})
        self.page.on_add()
        self.reader.run()
        self.assertEqual(self.last("toast"), {"text": "Panel replaced on V1."})

    def test_not_connected_is_said_and_nothing_is_added(self):
        self.page.on_add()
        self.assertFalse(self.page.adding)
        self.assertEqual(self.last("alert")["title"], "Not connected")

    def test_a_failure_is_said(self):
        controller, timeline = studio()
        timeline.takes_comps = False
        self.open(controller)
        self.page.on_add()
        self.reader.run()
        self.assertEqual(self.last("alert")["title"], "Couldn't add the panel")
        self.assertEqual(timeline.tracks[1], [])


if __name__ == "__main__":
    unittest.main()
