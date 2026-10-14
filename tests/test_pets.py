"""Pets: what the pet does (pages/pets/behaviour.py - no Qt), which species
achievements earn, the window's geometry sums (pet_window.py's pure parts),
and the pet itself (page.py Pets - its Settings page, its settings, the
window) with an in-memory host. The window is only opened where Qt can
show one."""

import os
import random
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from core.achievements import Achievements
from pages.pets import behaviour
from pages.pets.behaviour import Behaviour, clean_name, unlocked_species

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject, QPoint
    from PySide6.QtWidgets import QApplication
    from pages.pets import page as page_mod
    from pages.pets import pet_window
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class SpeciesTests(unittest.TestCase):
    def test_the_blob_comes_first_and_the_rest_are_earned(self):
        self.assertEqual(unlocked_species(0), ["blob"])
        self.assertEqual(unlocked_species(2), ["blob"])
        self.assertEqual(unlocked_species(3), ["blob", "robot"])
        self.assertEqual(unlocked_species(5), ["blob", "robot", "shroomy"])
        self.assertEqual(unlocked_species(21), list(behaviour.SPECIES))
        self.assertEqual(unlocked_species(100), list(behaviour.SPECIES))
        needs = [need for _name, need in behaviour.SPECIES.values()]
        self.assertEqual(needs[1:], list(range(3, 3 + 2 * (len(needs) - 1), 2)))      # two apart, from the robot's 3
        self.assertEqual(behaviour.DEFAULT_SPECIES, "blob")

    def test_names(self):
        self.assertEqual(clean_name("  Olive   the  Cat "), "Olive the Cat")
        self.assertEqual(clean_name("x" * 40), "x" * behaviour.MAX_NAME)
        self.assertEqual(clean_name(None), "")
        self.assertEqual(clean_name(12), "")

    def test_every_species_is_drawn(self):
        sprites = (_paths.APP / "pages" / "pets" / "web" / "sprites.js").read_text(encoding="utf-8")
        for species in behaviour.SPECIES:
            with self.subTest(species):
                self.assertIn(f"{species}(rig)", sprites)
                self.assertIn(f"{species}: ", sprites)        # its name, for the gallery

    def test_it_is_not_a_tab_but_ask_buddy_knows_it(self):
        from core.tools_kb import get_tool
        from pages.games.page import GamesPage
        registry = [("Games", GamesPage)]
        for tool_id in ("pets", "achievements"):
            with self.subTest(tool_id):
                info = get_tool(tool_id, registry)
                self.assertIsNotNone(info)
                self.assertTrue(info.is_available)           # built in, though no page in the registry
                self.assertFalse(info.needs_resolve)
                self.assertIn("Settings > General", info.summary)


class BehaviourTests(unittest.TestCase):
    def make(self, **kw):
        kw.setdefault("rng", random.Random(7))
        return Behaviour(width=400, x=200, **kw)

    @staticmethod
    def pass_time(b, seconds, **kw):
        """Seconds in one-second ticks (a tick is clamped to 2 s, as a
        computer waking from sleep must not jump the pet)."""
        changed = False
        while seconds > 0:
            changed = b.tick(min(1.0, seconds), **kw) or changed
            seconds -= 1.0
        return changed

    def test_it_chooses_between_idle_sit_and_walk(self):
        b = self.make(sleep_after_s=0)
        seen = set()
        for _ in range(3000):
            b.tick(0.5)
            seen.add(b.state)
        self.assertEqual(seen, {"idle", "sit", "walk"})
        self.assertTrue(0 <= b.x <= 400)

    def test_without_walking_it_stays_put(self):
        b = self.make(walks=False)
        for _ in range(2000):
            b.tick(0.5)
            self.assertNotEqual(b.state, "walk")
        self.assertEqual(b.x, 200)

    def test_walking_turns_at_the_edges(self):
        b = self.make()
        b.state, b._left, b.facing, b.x = "walk", 1e9, 1, 399
        b.tick(1.0)
        self.assertEqual((b.x, b.facing), (400.0, -1))
        b.x = 1
        b.tick(1.0)
        self.assertEqual((b.x, b.facing), (0.0, 1))

    def test_a_pat_and_a_cheer_pass(self):
        b = self.make()
        b.poke()
        self.assertEqual(b.state, "happy")
        self.assertTrue(self.pass_time(b, behaviour.HAPPY_S + 0.1))      # changed: back to something else
        self.assertNotEqual(b.state, "happy")
        b.cheer()
        self.assertEqual(b.state, "cheer")
        b.tick(1.0)
        self.assertEqual(b.state, "cheer")
        self.pass_time(b, behaviour.CHEER_S)
        self.assertNotEqual(b.state, "cheer")
        b.tick(10.0)                                                     # a huge tick (the computer slept) is clamped
        self.assertTrue(0 <= b.x <= 400)

    def test_work_interrupts_and_hands_back(self):
        b = self.make()
        self.assertTrue(b.tick(0.1, working=True))
        self.assertEqual(b.state, "work")
        for _ in range(100):
            b.tick(1.0, working=True)
        self.assertEqual(b.state, "work")
        b.tick(0.1, working=False)
        self.assertIn(b.state, ("idle", "sit", "walk"))
        b.poke()
        b.tick(0.1, working=True)
        self.assertEqual(b.state, "happy")                       # a pat isn't cut short by work
        self.pass_time(b, behaviour.HAPPY_S, working=True)
        self.assertEqual(b.state, "work")

    def test_it_falls_asleep_when_nothing_happens_and_wakes(self):
        b = self.make(sleep_after_s=60)
        for _ in range(130):
            b.tick(1.0)
        self.assertEqual(b.state, "sleep")
        b.activity()
        self.assertEqual(b.state, "idle")
        b = self.make(sleep_after_s=0)
        for _ in range(1000):
            b.tick(1.0)
        self.assertNotEqual(b.state, "sleep")
        b = self.make(sleep_after_s=60)
        for _ in range(130):
            b.tick(1.0, working=True)
        self.assertEqual(b.state, "work")                        # work keeps it up

    def test_put_down_somewhere_and_made_to_stand_still(self):
        b = self.make()
        b.set_x(900)
        self.assertEqual(b.x, 400)
        b.set_width(300)
        self.assertEqual(b.x, 300)
        b.set_x(-5)
        self.assertEqual(b.x, 0)
        b.state, b._left = "walk", 1.0
        b.stand_still()
        self.assertEqual(b.state, "idle")
        self.assertGreaterEqual(b._left, 3.0)
        b.state = "sit"
        b.stand_still()
        self.assertEqual(b.state, "sit")


class SettingsTests(unittest.TestCase):
    def test_saved_settings_are_made_safe(self):
        from pages.pets.page import DEFAULTS, clean_settings
        self.assertEqual(clean_settings(None), DEFAULTS)
        got = clean_settings({"species": "unicorn", "name": 5, "size": "huge", "shown": "yes", "walk": False,
                              "sleep_minutes": 7, "ask": True, "pos": {"x": 10.5, "y": "2"}})
        self.assertEqual(got, {**DEFAULTS, "walk": False, "ask": True})
        got = clean_settings({"species": "robot", "name": " Sam ", "size": "large", "shown": True,
                              "sleep_minutes": 0, "pos": {"x": 10.5, "y": -20}})
        self.assertEqual((got["species"], got["name"], got["size"], got["shown"], got["sleep_minutes"], got["pos"]),
                         ("robot", "Sam", "large", True, 0, {"x": 10, "y": -20}))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class WindowSumsTests(unittest.TestCase):
    def test_the_frame_has_room_for_the_bubble(self):
        self.assertEqual(pet_window.window_frame("medium", False), (166, 128))
        self.assertEqual(pet_window.window_frame("medium", True), (pet_window.BUBBLE_W, 128 + pet_window.BUBBLE_H))
        self.assertEqual(pet_window.window_frame("nonsense", False), (166, 128))

    def test_the_feet_stay_where_they_are_and_inside_the_screen(self):
        area = (0, 0, 1919, 1079)
        x, y, ax, ay = pet_window.place_in(area, (1700, 1080), (166, 128))
        self.assertEqual((x, y, ax, ay), (1617, 952, 1700, 1080))
        x2, y2, ax2, ay2 = pet_window.place_in(area, (1700, 1080), (300, 256))     # taller: same feet
        self.assertEqual((ax2, ay2), (1700, 1080))
        self.assertEqual((x2 + 150, y2 + 256), (1700, 1080))
        x, y, ax, ay = pet_window.place_in(area, (-50, 50), (166, 128))            # off the left and above
        self.assertEqual((x, y), (0, 0))
        x, y, ax, ay = pet_window.place_in(area, (5000, 5000), (166, 128))         # off the right and below
        self.assertEqual((x + 166, y + 128), (1920, 1080))


class Mem(dict):
    def save(self):
        pass


if HAVE_QT:
    class Host(QObject):
        """A shell: settings in memory, real achievements in a temp file,
        and what the pet asks of the shell written down."""

        def __init__(self, path, saved=None):
            super().__init__()
            self.achievements = Achievements(path)
            self.tools = {"pets": Mem(saved)} if saved is not None else {}
            self.opened = []
            self.switched = []
            self.sent = []
            self.front = 0
            self.pages = {"manual_chat": self}

        def tool_settings(self, tool_id, defaults=None):
            return self.tools.setdefault(tool_id, Mem(defaults or {}))

        def record_stat(self, stat, amount=1):
            self.achievements.record(stat, amount)

        def switch_tool(self, tool_id):
            self.switched.append(tool_id)

        def open_settings(self, page_id):
            self.opened.append(page_id)

        def bring_to_front(self):
            self.front += 1

        def on_send(self, payload):           # standing in for Ask Buddy
            self.sent.append(payload)

        def working_tools(self):
            return []


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PetsTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "achievements.json")
        self.addCleanup(self._tmp.cleanup)

    def make(self, saved=None):
        self.host = Host(self.path, saved)
        pets = page_mod.Pets(self.host)
        self.addCleanup(pets._close_window)
        return pets

    @staticmethod
    def field(pets, key):
        return next(f for f in pets.settings_pages()[0]["fields"] if f.get("key") == key)

    def test_its_settings_page_shows_what_was_saved(self):
        pets = self.make({"species": "robot", "name": "Sam", "size": "large", "walk": False})
        pages = pets.settings_pages()
        self.assertEqual((pages[0]["id"], pages[0]["group"]), ("pets", "general"))
        self.assertEqual(self.field(pets, "name")["value"], "Sam")
        self.assertEqual(self.field(pets, "size")["value"], "large")
        self.assertFalse(self.field(pets, "walk")["value"])
        self.assertFalse(self.field(pets, "shown")["value"])
        gallery = self.field(pets, "species")
        self.assertEqual(gallery["kind"], "pets")
        self.assertEqual([g["id"] for g in gallery["items"]], list(behaviour.SPECIES))
        self.assertEqual([g["id"] for g in gallery["items"] if g["unlocked"]], ["blob"])
        self.assertEqual([g["id"] for g in gallery["items"] if g["chosen"]], ["robot"])
        self.assertIsNone(pets.window)

    def test_only_an_earned_species_can_be_chosen(self):
        pets = self.make()
        self.assertFalse(pets.set_value("species", "robot"))
        self.assertEqual(pets.values["species"], "blob")
        for stat in ("proxies", "renders", "graded"):
            self.host.achievements.record(stat, 1)
        self.assertTrue(pets.set_value("species", "robot"))
        self.assertEqual(self.host.tools["pets"]["species"], "robot")
        self.assertEqual([g["id"] for g in self.field(pets, "species")["items"] if g["unlocked"]], ["blob", "robot"])

    def test_settings_are_checked_and_saved(self):
        pets = self.make()
        pets.on_setting("name", "  Tess  ", None)
        pets.on_setting("size", "giant", None)
        pets.on_setting("sleep_minutes", 7, None)
        pets.on_setting("sleep_minutes", "30", None)
        pets.on_setting("ask", 0, None)
        pets.on_setting("pos", {"x": 1, "y": 1}, None)              # not a setting of the page's
        saved = self.host.tools["pets"]
        self.assertEqual((saved["name"], saved["size"], saved["sleep_minutes"], saved["ask"]), ("Tess", "medium", 30, False))
        self.assertIsNone(saved["pos"])

    def test_a_question_goes_to_ask_buddy_in_front(self):
        pets = self.make()
        pets.ask_buddy("  How do I  add a marker? ")
        self.assertEqual(self.host.front, 1)
        self.assertEqual(self.host.switched, ["manual_chat"])
        self.assertEqual(self.host.sent, [{"text": "How do I add a marker?"}])
        pets.ask_buddy("   ")
        self.assertEqual(self.host.front, 1)

    def test_show_opens_the_window_and_hide_closes_it(self):
        pets = self.make()
        pets.on_setting("shown", True, None)
        self.assertIsNotNone(pets.window)
        self.assertTrue(self.host.tools["pets"]["shown"])
        window = pets.window
        self.assertEqual(window.behaviour.walks, True)
        pets.set_value("walk", False)
        self.assertFalse(window.behaviour.walks)
        pets.celebrate([{"id": "proxy_1", "title": "First proxy", "text": ""}])
        self.assertEqual(window.behaviour.state, "cheer")
        window.on_click()
        self.assertEqual(self.host.achievements.value("pats"), 1)
        self.assertEqual(window.behaviour.state, "happy")
        full = pet_window.window_frame("medium", True)[1]
        self.assertEqual(window.height(), full)                 # always with the room above: never resized
        window.on_bubble({"open": True})
        self.assertTrue(window._room())
        self.assertEqual(window.height(), full)
        window.on_bubble({"open": False})
        self.assertFalse(window._room())
        window.on_ask({"text": "What is a flash frame?"})
        self.assertEqual(self.host.sent, [{"text": "What is a flash frame?"}])
        window._open("achievements")
        self.assertEqual(self.host.opened, ["achievements"])
        pets.hide_pet()
        self.assertIsNone(pets.window)
        self.assertFalse(self.host.tools["pets"]["shown"])

    def test_the_hover_pill_follows_the_mouse_on_screen_with_a_grace_period(self):
        pets = self.make()
        pets.show(True)
        window = pets.window
        sent = []
        window.emit = lambda name, payload=None: sent.append((name, payload))
        pet = window._pet_rect()
        height = window.height()
        clicks = []
        window._set_clickable = lambda on: clicks.append(on)
        with mock.patch.object(pet_window.QCursor, "pos", return_value=pet.center()):
            window._check_hover()
            window._check_hover()
        self.assertEqual(sent, [("hover", {"on": True})])
        self.assertEqual(clicks[-1], True)                                   # the mouse reaches it now
        self.assertEqual(window.height(), height)                            # nothing moves or resizes
        self.assertEqual(window._pet_rect(), pet)
        away = QPoint(pet.left() - 500, pet.top() - 500)
        with mock.patch.object(pet_window.QCursor, "pos", return_value=away):
            window._check_hover()
            self.assertTrue(window._pill)                                    # not gone at once
            with mock.patch.object(pet_window.time, "monotonic", return_value=window._hover_lost + 10):
                window._check_hover()
        self.assertEqual(sent[-1], ("hover", {"on": False}))
        self.assertEqual(clicks[-1], False)                                  # clicks go through again
        self.assertEqual(window.height(), height)
        # With the pill up, it stays while the mouse is anywhere in the window (on its way up to the pill).
        with mock.patch.object(pet_window.QCursor, "pos", return_value=pet.center()):
            window._check_hover()
        with mock.patch.object(pet_window.QCursor, "pos", return_value=QPoint(window.x() + 5, window.y() + 5)):
            window._check_hover()
        self.assertTrue(window._pill)
        window.behaviour.state = "walk"
        window._tick()
        self.assertEqual(window.behaviour.state, "idle")                     # it stands still under the pill


if __name__ == "__main__":
    unittest.main()
