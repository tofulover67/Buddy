"""A new install's first start (app/core/onboarding.py): the starter sidebar,
More tools (app/core/more_tools.py) and the tour. The layout and step logic
run without Qt; the shell tests build a real ShellWindow offscreen with
stand-in pages and in-memory settings - never the real ~/.buddy, never
Resolve, no tray icon."""

import ast
import os
import tempfile
import time
import unittest
from unittest import mock

import _paths  # noqa: F401

from core import i18n, nav_layout

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    import core.shell_window as shell_window
    from core import more_tools, onboarding, settings_form as sf, settings_store
    from pages.base import ToolPage
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
# Seconds a wait for a web page may take: a slow CI Mac loading the first
# pages of a test process (a passing wait returns as soon as it's done).
SLOW = 90


def registry_ids():
    """The real registry's tool ids, read without importing every page."""
    with open(os.path.join(APP, "registry.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    ids = [node.args[0].value for node in ast.walk(tree)
           if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_tool"]
    return ids + ["buddy_network"]


class _Cls:
    def __init__(self, tool_id, placeholder=False):
        self.tool_id = tool_id
        self.is_placeholder = placeholder


REGISTRY = [("Ask", _Cls("manual_chat")), ("Audit", _Cls("audit")),
            ("Media", _Cls("asset_manager")), ("Media", _Cls("media_manager")),
            ("Editing", _Cls("dailies", placeholder=True)), ("Editing", _Cls("transcribe")),
            ("", _Cls("buddy_network"))]


class LayoutTests(unittest.TestCase):
    def test_the_starter_layout_shows_only_the_starter_tools(self):
        layout = nav_layout.starter_layout(REGISTRY, ("manual_chat", "media_manager", "dailies", "buddy_network"))
        self.assertEqual(nav_layout.visible_tool_ids(layout), ["manual_chat", "media_manager", "buddy_network"])
        # Every tool is still in it, hidden - a placeholder never starts out showing.
        self.assertEqual(sorted(nav_layout.hidden_tool_ids(layout)), ["asset_manager", "audit", "dailies", "transcribe"])
        self.assertEqual(nav_layout.reconcile(layout, REGISTRY), layout)

    def test_a_starter_set_naming_nothing_real_still_shows_a_tool(self):
        layout = nav_layout.starter_layout(REGISTRY, ("nope",))
        self.assertEqual(nav_layout.visible_tool_ids(layout), ["manual_chat"])

    def test_the_rail_drops_headings_over_hidden_tools_only(self):
        layout = nav_layout.starter_layout(REGISTRY, ("manual_chat", "media_manager", "buddy_network"))
        rows = nav_layout.rail_rows(layout)
        self.assertEqual([r.get("label", r.get("id")) for r in rows],
                         ["Ask", "manual_chat", "Media", "media_manager", "", "buddy_network"])

    def test_a_run_of_dividers_shows_whole_over_a_visible_tool(self):
        d = lambda label: {"type": "divider", "label": label}  # noqa: E731
        t = lambda tid, on: {"type": "tool", "id": tid, "visible": on}  # noqa: E731
        layout = [t("a", True), d("One"), t("b", False), d("Two"), d(""), t("c", False), t("e", True), d("End")]
        self.assertEqual([r.get("label", r.get("id")) for r in nav_layout.rail_rows(layout)],
                         ["a", "Two", "", "e"])

    def test_show_tool_puts_it_back_where_it_was(self):
        layout = nav_layout.starter_layout(REGISTRY, ("manual_chat",))
        self.assertEqual(nav_layout.show_tool(layout, "audit"), "")
        self.assertEqual(nav_layout.visible_tool_ids(layout), ["manual_chat", "audit"])
        self.assertEqual(nav_layout.show_tool(layout, "nope"), "nothing")

    def test_the_real_starter_tools_are_real_tools(self):
        if not HAVE_QT:
            self.skipTest("PySide6 not installed")
        self.assertEqual(set(onboarding.STARTER_TOOLS) - set(registry_ids()), set())


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class NewInstallTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="buddy-onboarding-")
        path = os.path.join(self.dir, "settings.json")
        for p in (mock.patch.object(settings_store, "BUDDY_DIR", self.dir),
                  mock.patch.object(settings_store, "SHARED_SETTINGS_PATH", path)):
            p.start()
            self.addCleanup(p.stop)
        self.path = path

    def test_no_settings_file_is_a_new_install(self):
        shared = settings_store.SharedSettings()
        self.assertTrue(shared.is_new)
        self.assertTrue(onboarding.is_new_install(shared, [os.path.join(self.dir, "days.json")]))

    def test_a_saved_settings_file_is_not(self):
        settings_store.SharedSettings().save()
        self.assertFalse(settings_store.SharedSettings().is_new)

    def test_a_damaged_settings_file_is_not(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        shared = settings_store.SharedSettings()
        self.assertFalse(shared.is_new)
        self.assertFalse(onboarding.is_new_install(shared))

    def test_an_earlier_run_s_files_say_it_is_not(self):
        shared = settings_store.SharedSettings()
        days = os.path.join(self.dir, "achievements.json")
        with open(days + ".bak", "w", encoding="utf-8") as fh:
            fh.write("{}")
        self.assertFalse(onboarding.is_new_install(shared, [days]))

    def test_a_settings_object_without_the_flag_is_an_old_install(self):
        self.assertFalse(onboarding.is_new_install({}))

    def test_setting_up_saves_the_starter_sidebar_and_the_tour(self):
        shared = settings_store.SharedSettings()
        onboarding.set_up_new_install(shared, REGISTRY)
        again = settings_store.SharedSettings()
        self.assertFalse(again.is_new)
        self.assertTrue(again.get(onboarding.TOUR_KEY))
        self.assertEqual(nav_layout.visible_tool_ids(nav_layout.load(again, REGISTRY)), ["manual_chat", "media_manager", "transcribe",
                                                                                         "buddy_network"])


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class StepTests(unittest.TestCase):
    TARGETS = {None, "rail", "more", "connection", "pane", "tool:manual_chat", "split", "bug", "settings",
               "settings:theme", "settings:language"}

    def ids(self, **kw):
        return [s["id"] for s in onboarding.tour_steps(**kw)]

    def test_welcome_first_and_done_last(self):
        steps = onboarding.tour_steps(more=4, tray="win")
        self.assertEqual((steps[0]["id"], steps[-1]["id"]), ("welcome", "done"))
        self.assertLessEqual({s["target"] for s in steps}, self.TARGETS)

    def test_theme_then_language_follow_settings(self):
        ids = self.ids(more=4)
        self.assertEqual(ids[ids.index("settings"):], ["settings", "theme", "language", "done"])
        for step_id, page in (("theme", "appearance"), ("language", "general")):
            target = next(s["target"] for s in onboarding.tour_steps() if s["id"] == step_id)
            self.assertEqual(onboarding.SETTINGS_TARGETS[target][0], page)

    def test_steps_that_have_nothing_to_show_are_left_out(self):
        self.assertNotIn("more", self.ids(more=0))
        self.assertNotIn("ask", self.ids(ask=False, more=3))
        self.assertIn("ask", self.ids(more=3))

    def test_one_more_tool_is_said_as_one(self):
        more = next(s for s in onboarding.tour_steps(more=1) if s["id"] == "more")
        self.assertIn("One more is ready", more["text"][0])
        more = next(s for s in onboarding.tour_steps(more=15) if s["id"] == "more")
        self.assertIn("15 more are ready", more["text"][0])

    def test_the_last_card_says_where_buddy_keeps_running(self):
        done = lambda tray: onboarding.tour_steps(tray=tray)[-1]["text"]  # noqa: E731
        self.assertEqual(len(done(None)), 1)
        self.assertIn("system tray", done("win")[1])
        self.assertIn("menu bar", done("mac")[1])

    def test_every_step_is_translated(self):
        for kw in (dict(more=1, tray="win"), dict(more=15, tray="mac")):
            for step in onboarding.tour_steps(**kw):
                for text in [step["title"], *step["text"]]:
                    with self.subTest(text):
                        self.assertNotEqual(i18n.translate(text, "Deutsch"), text)

    def test_more_tools_has_a_line_on_every_tool_in_every_language(self):
        self.assertEqual(set(registry_ids()) - set(more_tools.BLURBS), set())
        for text in more_tools.BLURBS.values():
            with self.subTest(text):
                self.assertNotEqual(i18n.translate(text, "日本語"), text)

    def test_settings_offers_the_tour_except_under_the_desktop_layout(self):
        actions = lambda shared: [b["action"] for f in sf.tour_fields(shared) for b in f.get("items", [])]  # noqa: E731
        self.assertEqual(actions({}), ["tour"])
        self.assertEqual(actions({"theme": "Desktop"}), [])


class _Settings(dict):
    """In-memory settings - a new install's, with is_new."""
    is_new = False

    def save(self):
        pass


def _page(tool_id):
    class Page(ToolPage):
        def build_ui(self):
            layout = QVBoxLayout(self)
            layout.addWidget(QWidget())

    Page.tool_id = tool_id
    Page.display_name = tool_id.replace("_", " ").title()
    Page.category = "Tools"
    return Page


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ShellTests(unittest.TestCase):
    def build(self, new):
        self.app = QApplication.instance() or QApplication([])
        self.settings = _Settings()
        self.settings.is_new = new
        self.achievements = os.path.join(tempfile.mkdtemp(prefix="buddy-onboarding-"), "achievements.json")
        for p in (mock.patch.object(shell_window, "SharedSettings", lambda: self.settings),
                  mock.patch.object(shell_window, "resolve_connect", side_effect=RuntimeError("no Resolve")),
                  mock.patch.object(shell_window.AnnouncementChecker, "start", lambda self: None),
                  mock.patch.object(shell_window.QSystemTrayIcon, "isSystemTrayAvailable", return_value=False),
                  mock.patch.object(shell_window.Achievements, "__init__",
                                    self._achievements_init(self.achievements))):
            p.start()
            self.addCleanup(p.stop)
        registry = [("Ask", _page("manual_chat")), ("Audit", _page("audit")), ("Business", _page("time_tracker"))]
        self.win = shell_window.ShellWindow(self.app, registry)
        self.addCleanup(self.win.deleteLater)
        self.addCleanup(self.win._shutdown_connect_worker)
        self.addCleanup(self.win.close_tour)

    @staticmethod
    def _achievements_init(path):
        real = shell_window.Achievements.__init__

        def init(self, *_args, **_kw):
            real(self, path=path)
        return init

    def wait_until(self, check, seconds=20):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            if check():
                return True
            time.sleep(0.02)
        return False

    def test_an_old_install_keeps_every_tool_and_gets_no_tour(self):
        self.build(new=False)
        self.assertEqual(nav_layout.visible_tool_ids(self.win.nav_layout), ["manual_chat", "audit", "time_tracker"])
        self.assertFalse(self.settings.get(onboarding.TOUR_KEY, False))
        self.assertEqual(self.win.more_tool_ids(), [])

    def test_a_new_install_starts_with_the_starter_tools(self):
        self.build(new=True)
        self.assertEqual(nav_layout.visible_tool_ids(self.win.nav_layout), ["manual_chat", "time_tracker"])
        self.assertEqual(self.win.more_tool_ids(), ["audit"])
        self.assertTrue(self.settings[onboarding.TOUR_KEY])
        shown = []
        self.win.rail.show_items = lambda items, more=0: shown.append((items, more))
        self.win.push_rail()
        items, more = shown[-1]
        self.assertEqual(more, 1)
        self.assertNotIn("Audit", [i.get("label") for i in items])         # nor its heading
        self.win.add_to_rail("audit")
        self.assertEqual(self.win.more_tool_ids(), [])
        self.assertIn("audit", nav_layout.visible_tool_ids(self.settings[nav_layout.SETTINGS_KEY]))

    def show_with_tour(self):
        """Shows the window with the tour offered at once; the steps this
        window's tour sends - to its own view, or to the one over Settings
        ("in_settings") - go into the list returned."""
        sent = []
        real_emit = onboarding.TourView.emit

        def emit(view, name, payload=None):
            if name == "step" and getattr(view, "tour", view).shell is self.win:
                sent.append(dict(payload, in_settings=isinstance(view, onboarding._SettingsLens)))
            real_emit(view, name, payload)

        for p in (mock.patch.object(onboarding, "TOUR_DELAY_MS", 0),
                  mock.patch.object(onboarding.TourView, "emit", emit),
                  mock.patch.object(onboarding._SettingsLens, "emit", emit)):
            p.start()
            self.addCleanup(p.stop)
        # The first pages a test process loads can take a slow CI Mac well
        # over 10 s: what's timed here is the tour, not the web engine starting.
        self.assertTrue(self.wait_until(lambda: self.win.header._ready and self.win.rail._ready, SLOW),
                        "the header and rail never loaded")
        self.win.resize(1300, 800)
        self.win.show()
        self.assertTrue(self.wait_until(lambda: sent, SLOW), "the tour never came up")
        return sent

    def test_on_screen_it_waits_however_long_the_pages_take(self):
        self.build(new=True)
        self.assertTrue(self.wait_until(lambda: self.win.header._ready and self.win.rail._ready, SLOW))
        self.win.header._ready = False                         # as if still loading
        with mock.patch.object(onboarding, "TOUR_DELAY_MS", 0), mock.patch.object(onboarding, "READY_WAIT_MS", 300):
            self.win.resize(1300, 800)
            self.win.show()
            self.wait_until(lambda: False, 1.0)                # well past READY_WAIT_MS
            self.assertIsNone(self.win._tour)
            self.win.header._ready = True
            self.assertTrue(self.wait_until(lambda: self.win._tour is not None, 10), "it gave up waiting")

    def test_the_tour_skip_and_dont_show_again(self):
        self.build(new=True)
        sent = self.show_with_tour()
        self.assertTrue(sent[-1]["welcome"])
        # Skip without the tick: still offered next time.
        self.win._tour.on_skip({"never": False})
        self.assertIsNone(self.win._tour)
        self.assertTrue(self.settings[onboarding.TOUR_KEY])
        # Don't show this again.
        self.win.start_tour(welcome=True)
        self.assertTrue(self.wait_until(lambda: self.win._tour is not None and self.win._tour._ready, SLOW))
        self.win._tour.on_skip({"never": True})
        self.assertFalse(self.settings[onboarding.TOUR_KEY])

    def test_taken_from_settings_it_starts_past_the_welcome(self):
        self.build(new=True)
        self.show_with_tour()
        self.win._tour.on_skip({"never": False})
        self.win.start_tour()
        self.assertEqual(self.win._tour.index, 1)
        self.assertFalse(self.settings[onboarding.TOUR_KEY])      # taken: no welcome card at the next start

    def card(self, sent, step_id, seconds=SLOW):
        """The card a step shows - wherever it shows it (not the main
        window's dim while Settings has it)."""
        found = lambda: next((p for p in reversed(sent) if p["id"] == step_id and not p["elsewhere"]), None)  # noqa: E731
        self.assertTrue(self.wait_until(lambda: found() is not None, seconds), f"{step_id} never showed its card")
        return found()

    def settings_open(self):
        from core.settings_dialog import SettingsDialog
        return [d for d in SettingsDialog.on_screen() if d.isVisible()]

    def test_taking_the_tour_lights_up_each_part(self):
        self.build(new=True)
        sent = self.show_with_tour()
        tour = self.win._tour
        tour.on_start()
        self.assertFalse(self.settings[onboarding.TOUR_KEY])               # taken: not offered again
        lit, where = {}, {}
        for step in tour.steps[1:]:
            card = self.card(sent, step["id"])
            lit[step["id"]], where[step["id"]] = card["rects"], card["in_settings"]
            if step["id"] in ("theme", "language"):
                self.assertTrue(self.settings_open(), f"Settings isn't open for {step['id']}")
            tour.on_next()
        self.assertIsNone(self.win._tour)
        for step_id in ("rail", "more", "connection", "pane", "ask", "split", "bug", "settings", "theme", "language"):
            with self.subTest(step_id):
                self.assertTrue(lit.get(step_id), f"{step_id} lit nothing")
        self.assertEqual(len(lit["connection"]), 2)                         # the status and its button, apart
        self.assertEqual(lit["done"], [])
        self.assertEqual({k for k, v in where.items() if v}, {"theme", "language"})
        self.assertTrue(self.wait_until(lambda: not self.settings_open(), 5), "the tour left Settings open")

    def test_back_from_the_theme_closes_settings(self):
        self.build(new=True)
        sent = self.show_with_tour()
        tour = self.win._tour
        tour.on_start()
        tour._go([s["id"] for s in tour.steps].index("theme"))
        self.card(sent, "theme")
        tour.on_back()
        self.assertEqual(tour.steps[tour.index]["id"], "settings")
        self.assertFalse(self.card(sent, "settings")["in_settings"])
        self.assertTrue(self.wait_until(lambda: not self.settings_open(), 5), "Settings stayed open")

    def test_closing_settings_under_the_tour_ends_it(self):
        self.build(new=True)
        sent = self.show_with_tour()
        tour = self.win._tour
        tour.on_start()
        tour._go([s["id"] for s in tour.steps].index("language"))
        self.card(sent, "language")
        self.settings_open()[0].reject()                                    # its own close button
        self.assertTrue(self.wait_until(lambda: self.win._tour is None, 5))
        self.assertFalse(self.settings[onboarding.TOUR_KEY])


if __name__ == "__main__":
    unittest.main()
