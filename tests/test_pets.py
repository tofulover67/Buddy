"""Pets: what the pet does (pages/pets/behaviour.py - no Qt), which species
achievements earn, picking it up and throwing it (physics.py - no Qt,
plain numbers), the window's geometry sums (pet_window.py's pure parts),
and the pet itself (page.py Pets - its Settings page, its settings, the
window) with an in-memory host. The window is only opened where Qt can
show one."""

import math
import os
import random
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from core.achievements import Achievements
from pages.pets import behaviour, physics
from pages.pets.behaviour import Behaviour, clean_name, unlocked_species
from pages.pets.physics import Desk, Motion, throw_velocity

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

    def test_held_and_in_the_air_its_life_waits(self):
        b = self.make(sleep_after_s=60)
        b.pick_up()
        self.assertEqual(b.state, "held")
        self.assertFalse(self.pass_time(b, 300, working=True))         # no work, no sleep, nothing new
        self.assertEqual((b.state, b.x), ("held", 200))
        b.poke()
        b.cheer()
        b.activity()
        b.stand_still()
        self.assertEqual(b.state, "held")
        b.let_go()
        self.assertEqual(b.state, "fly")
        self.assertFalse(self.pass_time(b, 30))
        self.assertEqual(b.state, "fly")
        b.land(False)
        self.assertEqual(b.state, "idle")                               # a soft landing: standing...
        self.pass_time(b, 4)
        self.assertIn(b.state, ("idle", "sit", "walk"))                 # ...then its usual life

    def test_a_hard_landing_leaves_it_dizzy_for_a_moment(self):
        b = self.make()
        b.pick_up()
        b.let_go()
        b.land(True)
        self.assertEqual(b.state, "dizzy")
        b.tick(1.0, working=True)
        self.assertEqual(b.state, "dizzy")                              # work waits for it to come round
        self.pass_time(b, behaviour.DIZZY_S, working=True)
        self.assertEqual(b.state, "work")
        b.tick(0.1)
        b.land(True)
        self.pass_time(b, behaviour.DIZZY_S + 0.1)
        self.assertIn(b.state, ("idle", "sit", "walk"))


SCREEN = (0, 0, 1919, 1039)         # a 1920 x 1080 screen, its taskbar's 40 px below
FLOOR = 1040                        # where feet stand on it
PET = (166, 128)                    # a medium pet's box
HALF = PET[0] / 2


def frames(motion, hand, start, seconds, fps=60):
    """Holds `motion` for `seconds` from time `start`, the mouse at
    hand(t) (t from 0); returns the time it ends at."""
    n = int(round(seconds * fps))
    for i in range(1, n + 1):
        motion.hold(hand(i / fps), start + i / fps)
    return start + n / fps


def fly(motion, seconds, fps=60):
    """The pet in the air for `seconds` (or until it rests): what happened."""
    events = []
    for _ in range(int(round(seconds * fps))):
        events += motion.step(1 / fps)
        if motion.mode != "fly":
            break
    return events


def thrown(desk, feet, velocity):
    """A pet just let go at `feet`, going at `velocity`."""
    m = Motion(desk, PET, feet)
    m.grab(feet, 0.0)
    m.let_go(0.0)
    m.vx, m.vy = velocity
    return m


class PhysicsTests(unittest.TestCase):
    def test_held_it_follows_the_mouse_and_eases_to_hanging_by_its_scruff(self):
        m = Motion(Desk([SCREEN]), PET, (800, FLOOR), scruff=(0.5, 0.25))
        m.grab((830, 980), 0.0)                                         # pressed on its body, right of the middle
        m.hold((830, 980), 0.0)
        self.assertEqual((m.x, m.y), (800, FLOOR))                     # where it was, to begin with
        m.hold((830, 600), physics.HOLD_EASE_S / 2)                    # halfway into hanging
        self.assertAlmostEqual(m.x, 830 + (-30 + 0) / 2)
        self.assertAlmostEqual(m.y, 600 + (60 + 96) / 2)
        frames(m, lambda t: (830 + 100 * t, 600), physics.HOLD_EASE_S / 2, 0.5)
        self.assertAlmostEqual(m.x, 880)                                # hanging: the mouse at its scruff,
        self.assertAlmostEqual(m.y, 600 + 0.75 * 128)                  # the feet three quarters of it below
        m.hold((5, 5), 1.0)
        self.assertEqual((m.x - HALF, m.y - PET[1]), (0, 0))           # its box stays on the screen
        m.hold((900, 2000), 1.1)
        self.assertEqual((m.x, m.y), (900, FLOOR))                     # and above the taskbar

    def test_the_body_swings_against_the_hand_then_settles(self):
        m = Motion(Desk([SCREEN]), PET, (800, 700), scruff=(0.5, 0.25))
        m.grab((800, 604), 0.0)                                         # by the scruff: nothing to ease
        t = frames(m, lambda s: (800, 604), 0.0, 1.0)
        self.assertEqual(m.pose(), (0, 1, 1))

        def speeding(way):
            """The hand off sideways, up to 600 px/s over a tenth of a second."""
            return lambda s: (800 + way * (3000 * s * s if s < 0.1 else 30 + 600 * (s - 0.1)), 604)

        def turned(deg, below=50):
            """Where a point `below` px under the scruff ends up sideways, turned as CSS turns it (y down)."""
            return -below * math.sin(math.radians(deg))

        t = frames(m, speeding(1), t, 0.2)
        deg, sx, sy = m.pose()
        self.assertLess(m.angle, 0)                                     # the body lags to the left of the scruff,
        self.assertGreater(deg, 5)                                      # which the page turns clockwise -
        self.assertLess(turned(deg), 0)                                 # what hangs below goes left
        self.assertGreater(sy, 1)                                       # stretched a little with the speed,
        self.assertAlmostEqual(sx, 1 / math.sqrt(sy))                   # thinner as it does
        self.assertLessEqual(sy, 1 + physics.STRETCH)
        x = m.x
        frames(m, lambda s: (x, 604), t, 4.0)                           # the hand stops: it swings, and settles
        deg, sx, sy = m.pose()
        self.assertLess(abs(deg), 0.5)
        self.assertAlmostEqual(sy, 1, places=3)
        m = Motion(Desk([SCREEN]), PET, (800, 700), scruff=(0.5, 0.25))
        m.grab((800, 604), 0.0)
        t = frames(m, lambda s: (800, 604), 0.0, 1.0)
        frames(m, speeding(-1), t, 0.2)
        self.assertGreater(m.angle, 0)                                  # off to the left: it lags to the right
        self.assertGreater(turned(m.pose()[0]), 0)
        self.assertLessEqual(abs(m.angle), physics.MAX_SWING)

    def test_a_throw_takes_the_speed_of_the_last_moment(self):
        samples = [(0.0, 0, 0), (0.02, 10, 0), (0.04, 20, -10), (0.06, 30, -20), (0.10, 50, -40)]
        vx, vy = throw_velocity(samples, 0.10)                          # over the last 80 ms
        self.assertAlmostEqual(vx, 500)
        self.assertAlmostEqual(vy, -500)
        still = [(0.0, 0, 0), (0.05, 50, 0), (0.1, 50, 0), (0.15, 50, 0), (0.2, 50, 0)]
        self.assertEqual(throw_velocity(still, 0.2), (0, 0))           # a hand that had stopped
        self.assertEqual(throw_velocity([(0.0, 0, 0)], 0.0), (0, 0))
        vx, vy = throw_velocity([(0.0, 0, 0), (0.01, 300, 400)], 0.01)
        self.assertAlmostEqual(math.hypot(vx, vy), physics.MAX_THROW)
        self.assertAlmostEqual(vx / vy, 300 / 400)
        m = Motion(Desk([SCREEN]), PET, (800, 700))
        m.grab((800, 640), 0.0)
        t = frames(m, lambda s: (800 + 900 * s, 640), 0.0, 0.5)
        m.let_go(t)
        self.assertAlmostEqual(m.vx, 900, delta=10)
        self.assertAlmostEqual(m.vy, 0, delta=10)
        self.assertEqual(m.mode, "fly")
        m = Motion(Desk([SCREEN]), PET, (800, 700))
        m.grab((800, 640), 0.0)
        t = frames(m, lambda s: (800 + 900 * min(s, 0.3), 640), 0.0, 0.6)   # moved, then held still
        x = m.x
        m.let_go(t)
        self.assertEqual((m.vx, m.vy), (0, 0))
        fly(m, 0.2)
        self.assertEqual(m.x, x)                                        # it just drops
        self.assertGreater(m.y, 700)

    def test_a_quick_toss_leaves_at_the_hands_speed_while_it_eases_into_hanging(self):
        def toss(grab_at, velocity, seconds=0.1):
            """Picked up off the floor and flung at once - let go inside the ease into hanging."""
            m = Motion(Desk([SCREEN]), PET, (800, FLOOR), scruff=(0.5, 0.26))
            m.grab(grab_at, 0.0)
            t = frames(m, lambda s: (grab_at[0] + velocity[0] * s, grab_at[1] + velocity[1] * s), 0.0, seconds)
            self.assertLess(t, physics.HOLD_EASE_S)
            m.let_go(t)
            return m.vx, m.vy

        for grab_at in ((800, FLOOR - 5), (800, FLOOR - 60)):          # by its feet, by its middle
            with self.subTest(grab_at=grab_at):
                vx, vy = toss(grab_at, (0, -1500))
                self.assertAlmostEqual(vx, 0, delta=1)
                self.assertAlmostEqual(vy, -1500, delta=15)             # up as fast as the hand, not slowed by the ease
                vx, vy = toss(grab_at, (1000, -1000), 0.12)
                self.assertAlmostEqual(vx, 1000, delta=10)              # a toss up and to the side isn't flattened
                self.assertAlmostEqual(vy, -1000, delta=10)
        vx, vy = toss((830, FLOOR - 60), (0, 0), 0.05)                  # pressed right of the middle, the hand still:
        self.assertAlmostEqual(vx, 0, delta=1)                          # the ease sliding it under the hand is no throw
        self.assertAlmostEqual(vy, 0, delta=1)

    def test_it_falls_to_the_floor_bounces_and_comes_to_rest(self):
        m = thrown(Desk([SCREEN]), (800, 700), (0, 0))
        events = fly(m, 4.0)
        self.assertEqual(m.mode, "rest")
        self.assertEqual(events[-1][0], "rest")
        lands = [speed for name, speed in events if name == "land"]
        self.assertGreater(len(lands), 1)                               # it bounced
        self.assertEqual(lands, sorted(lands, reverse=True))           # lower each time
        self.assertTrue(all(speed >= physics.STOP_BOUNCE for speed in lands[1:]))
        self.assertLess(lands[0], physics.HARD_LANDING)
        self.assertLess(events[-1][1], physics.HARD_LANDING)           # not hard: it isn't dizzy
        self.assertEqual((m.x, m.y), (800, FLOOR))
        self.assertEqual(m.pose(), (0, 1, 1))
        m = thrown(Desk([SCREEN]), (800, 300), (0, 3000))               # thrown down hard
        events = fly(m, 4.0)
        self.assertEqual(m.mode, "rest")
        self.assertGreaterEqual(events[0][1], physics.HARD_LANDING)
        self.assertGreaterEqual(events[-1], ("rest", physics.HARD_LANDING))
        m = thrown(Desk([SCREEN]), (300, 500), (1500, -800))            # a long throw slides to a stop
        events = fly(m, 5.0)
        self.assertEqual((m.mode, m.y, m.vx), ("rest", FLOOR, 0))
        self.assertGreater(m.x, 1000)

    def test_a_wall_turns_it_round_with_a_loss(self):
        m = thrown(Desk([SCREEN]), (1700, 600), (1200, 0))
        before = None
        for _ in range(60):
            before = m.vx
            m.step(1 / 60)
            self.assertLessEqual(m.x + HALF, 1920)                     # its own box, not the window, meets the edge
            if m.vx < 0:
                break
        self.assertLess(m.vx, 0)
        self.assertTrue(0.4 < -m.vx / before < 0.7)                     # back the other way, a lot slower
        self.assertAlmostEqual(-m.vx / before, physics.WALL_BOUNCE, delta=0.01)
        self.assertNotEqual(m.spin, 0)                                  # and it wobbles
        m = thrown(Desk([SCREEN]), (200, 600), (-1200, 0))
        fly(m, 0.5)
        self.assertGreater(m.vx, 0)
        self.assertGreaterEqual(m.x - HALF, 0)

    def test_another_screen_beyond_an_edge_is_no_wall(self):
        two = Desk([SCREEN, (1920, 0, 3839, 1039)])
        m = thrown(two, (1700, 600), (1200, 0))
        fly(m, 0.5)
        self.assertGreater(m.vx, 0)                                     # never turned
        self.assertGreater(m.x - HALF, 1920)                            # across, on the next screen
        # A screen beside it that's lower and taller: open where both are, a wall below the first's floor.
        tall = Desk([SCREEN, (1920, -360, 4479, 1399)])
        m = thrown(tall, (2300, 1300), (-1500, 0))
        fly(m, 0.4)
        self.assertGreater(m.vx, 0)
        self.assertGreaterEqual(m.x - HALF, 1920)
        m = thrown(tall, (2300, 600), (-1500, 0))
        fly(m, 0.4)
        self.assertLess(m.x + HALF, 1920)                               # across onto the first
        m = thrown(two, (1910, FLOOR), (0, 0))                          # come to rest across the two
        fly(m, 3.0)
        self.assertEqual(m.mode, "rest")
        self.assertTrue(m.x + HALF <= 1920 or m.x - HALF >= 1920)       # it shuffles onto one of them
        m = thrown(tall, (1950, FLOOR), (0, 0))                         # half over the first's floor, the feet past it:
        fly(m, 5.0)
        self.assertEqual(m.mode, "rest")
        self.assertGreaterEqual(m.x - HALF, 1920)                       # off the ledge,
        self.assertEqual(m.y, 1400)                                     # down onto the lower one's floor

    def test_the_ceiling(self):
        m = thrown(Desk([SCREEN]), (800, FLOOR), (0, -4000))
        top, fell = FLOOR, False
        for _ in range(300):
            m.step(1 / 60)
            top = min(top, m.y - PET[1])
            fell = fell or m.vy > 0
            if m.mode != "fly":
                break
        self.assertEqual(top, 0)                                        # up to the top of the screen, no further
        self.assertTrue(fell)
        self.assertEqual((m.mode, m.y), ("rest", FLOOR))
        stacked = Desk([SCREEN, (0, -1080, 1919, -1)])                  # a screen above: up through onto its floor
        m = thrown(stacked, (800, FLOOR), (0, -4000))
        fly(m, 0.6)
        self.assertLess(m.y - PET[1], 0)

    def test_caught_in_the_air_it_swings_on_the_way_it_was_flying(self):
        m = thrown(Desk([SCREEN]), (800, 500), (1200, 0))
        fly(m, 0.1)
        x, y = m.x, m.y
        hand = (x, y - (1 - physics.SCRUFF[1]) * PET[1])                # caught by the scruff
        m.grab(hand, 5.0)
        self.assertEqual((m.mode, m.vx, m.vy), ("held", 0, 0))
        frames(m, lambda s: hand, 5.0, 0.15)
        self.assertAlmostEqual(m.x, x)                                  # stopped where it was caught,
        self.assertGreater(m.angle, 0.1)                                # the body swinging on to the right

    def test_with_nothing_under_it_it_falls(self):
        m = Motion(Desk([SCREEN]), PET, (800, 300))
        m.fall()
        self.assertEqual((m.mode, m.vx, m.vy), ("fly", 0, 0))
        events = fly(m, 4.0)
        self.assertEqual(events[0][0], "land")
        self.assertEqual((m.mode, m.x, m.y), ("rest", 800, FLOOR))

    def test_screens_scaled_differently_on_windows_have_no_gap_between_them(self):
        # Three screens side by side, as Qt places them on Windows: one at
        # 100% on the left, then two at 150% - the right one's corner kept
        # in real pixels (3840 along), its size in its own: 1280 px of
        # nothing between the two that touch.
        left = ((-1920, 0, -1, 1079), (-1920, 0, -1, 1031), 1.0)
        middle = ((0, 0, 2559, 1439), (0, 0, 2559, 1391), 1.5)
        right = ((3840, 0, 6399, 1439), (3840, 0, 6399, 1391), 1.5)
        screens = physics.Screens([middle, right, left], primary=0)
        self.assertEqual(screens.shifts, [(0, 0), (-1280, 0), (0, 0)])     # only the gap closed
        self.assertEqual(screens.to_desk(3840, 500), (2560, 500))
        self.assertEqual(screens.to_screen(2560, 500), (3840, 500))
        self.assertEqual(screens.to_desk(-100, 1032), (-100, 1032))         # feet on the left one's floor
        for point in ((100, 1392), (5000, 1392), (-1900, 1032), (2559, 700)):
            self.assertEqual(screens.to_screen(*screens.to_desk(*point)), point)
        m = thrown(screens.desk(), (2300, 900), (1500, 0))                  # thrown right, off the middle one
        fly(m, 5.0)
        self.assertEqual(m.mode, "rest")
        self.assertGreaterEqual(m.x - HALF, 2560)                           # over the gap,
        x, y = screens.to_screen(m.x, m.y)
        self.assertGreater(x - HALF, 3840)                                  # onto the right one
        self.assertEqual(y, 1392)
        # Where Qt's own coordinates run on without gaps (a Mac, or screens at 100%), nothing moves.
        mac = physics.Screens([((0, 0, 1439, 899), (0, 25, 1439, 899), 1.0),
                               ((1440, -200, 3359, 879), (1440, -200, 3359, 879), 1.0)])
        self.assertEqual(mac.shifts, [(0, 0), (0, 0)])
        both = physics.Screens([((0, 0, 1919, 1079), (0, 0, 1919, 1039), 1.25),     # both at 125%: the
                                ((2400, 0, 4319, 1079), (2400, 0, 4319, 1039), 1.25)])  # second 2400 real px along
        self.assertEqual(both.shifts[1], (-480, 0))
        self.assertEqual(both.to_desk(2480, 500), (2000, 500))
        self.assertEqual(both.to_screen(2000, 500), (2480, 500))

    def test_set_down_puts_it_on_the_floor_below_on_one_screen(self):
        m = thrown(Desk([SCREEN, (1920, 0, 3839, 1039)]), (1915, 400), (900, -300))
        m.angle = 0.4
        m.set_down()
        self.assertEqual((m.mode, m.y, m.vx, m.vy, m.angle), ("rest", FLOOR, 0, 0, 0))
        self.assertTrue(m.x + HALF <= 1920 or m.x - HALF >= 1920)


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
        self.assertEqual(pet_window.room_below("medium"), 44)                # and for its swing, under and beside it
        self.assertEqual(pet_window.window_frame("medium", True), (pet_window.BUBBLE_W, 128 + pet_window.BUBBLE_H + 44))
        self.assertEqual(pet_window.window_frame("large", True), (228 + 2 * 61, 176 + pet_window.BUBBLE_H + 61))
        self.assertEqual(pet_window.window_frame("nonsense", False), (166, 128))

    def test_the_feet_stay_where_they_are_and_the_pet_inside_the_screen(self):
        area = (0, 0, 1919, 1079)
        frame = (300, 256)                                                          # the window, with the room above
        x, y, ax, ay = pet_window.place_in(area, (1700, 1080), frame, 166)
        self.assertEqual((x, y, ax, ay), (1550, 824, 1700, 1080))
        x2, y2, ax2, ay2 = pet_window.place_in(area, (1700, 1080), (300, 300), 166)  # taller: same feet
        self.assertEqual((ax2, ay2), (1700, 1080))
        self.assertEqual((x2 + 150, y2 + 300), (1700, 1080))
        x, y, ax, ay = pet_window.place_in(area, (10, 1080), frame, 166)           # at the left edge:
        self.assertEqual(ax - 83, 0)                                                # the pet reaches it,
        self.assertEqual(x, -67)                                                    # the see-through side hangs off
        x, y, ax, ay = pet_window.place_in(area, (-50, 50), frame, 166)            # off the left and above
        self.assertEqual((ax - 83, y), (0, 0))
        x, y, ax, ay = pet_window.place_in(area, (5000, 5000), frame, 166)         # off the right and below
        self.assertEqual((ax + 83, y + 256), (1920, 1080))
        x, y, ax, ay = pet_window.place_in(area, (-50, 50), (166, 128))            # no pet width: the whole window
        self.assertEqual((x, y), (0, 0))
        x, y, ax, ay = pet_window.place_in(area, (1700, 5000), (300, 300), 166, 44)  # with room under the feet:
        self.assertEqual((ay, y + 300 - 44), (1080, 1080))                          # it hangs past the bottom
        x, y, ax, ay = pet_window.place_in(area, (1700, 50), (300, 300), 166, 44)
        self.assertEqual((y, ay), (0, 256))                                         # the room above stays on it

    def test_what_is_above_the_pet_slides_back_onto_the_screen(self):
        area = (0, 0, 1919, 1079)
        self.assertEqual(pet_window.room_shift(area, 500, 300), 0)
        self.assertEqual(pet_window.room_shift(area, -67, 300), 67)                 # hanging off the left: right
        self.assertEqual(pet_window.room_shift(area, 1700, 300), -80)               # off the right: left
        self.assertEqual(pet_window.room_shift((1920, 0, 3839, 1079), 1900, 300), 20)


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
        # The room under its feet (for its swing) hangs over the taskbar: clicks there go through.
        under = QPoint(pet.center().x(), pet.bottom() + 10)
        self.assertTrue(window.geometry().contains(under))
        with mock.patch.object(pet_window.QCursor, "pos", return_value=under):
            window._check_hover()
        self.assertEqual(clicks[-1], False)

    def window_to_watch(self, saved=None):
        """The pet on the desktop (the offscreen screen: 800 x 800), what it
        sends the page written down, and its clock and mouse in the test's
        hands: self.clock[0] is the time, self.mouse what QCursor.pos()
        gives, self.button whether the button is down."""
        pets = self.make(saved)
        pets.show(True)
        window = pets.window
        self.sent = []
        window.emit = lambda name, payload=None: self.sent.append((name, payload))
        window._set_clickable = lambda on: None
        self.clock = [1000.0]
        self.mouse = mock.Mock(return_value=window._pet_rect().center())
        self.button = mock.Mock(return_value=True)
        for patch in (mock.patch.object(pet_window.time, "monotonic", side_effect=lambda: self.clock[0]),
                      mock.patch.object(pet_window.time, "perf_counter", side_effect=lambda: self.clock[0]),
                      mock.patch.object(pet_window.QCursor, "pos", self.mouse),
                      mock.patch.object(pet_window, "_primary_button_down", self.button),
                      mock.patch.object(pet_window, "_system_button_down", self.button)):
            patch.start()
            self.addCleanup(patch.stop)
        return pets, window

    def run_frames(self, window, seconds):
        for _ in range(int(round(seconds * 60))):
            self.clock[0] += 1 / 60
            window._frame()
            if window._motion is None:
                break

    def test_picked_up_it_dangles_from_the_mouse_and_a_lost_release_still_throws_it(self):
        pets, window = self.window_to_watch()
        floor = window._area()[3] + 1
        pet_h = pet_window.window_frame("medium", False)[1]
        window._pill = True                                                  # the pill was up
        window.on_grab({"fx": 0.5, "fy": 0.25})
        self.assertEqual(window.behaviour.state, "held")
        self.assertIn(("hover", {"on": False}), self.sent)
        self.assertEqual(self.sent[-2][1]["state"], "held")
        self.assertTrue(window._frame_timer.isActive())
        self.mouse.return_value = QPoint(400, 450)
        self.run_frames(window, 2.0)
        self.assertEqual(window._anchor, (400, 450 + 96))                   # hanging by its scruff from the mouse
        self.assertEqual(window._pet_rect().topLeft(), QPoint(400 - 83, 450 + 96 - pet_h))
        self.assertIn("pose", [name for name, _p in self.sent])
        self.run_frames(window, 3.0)                                         # the swing from that jump dies down
        before = len(self.sent)
        window._tick()                                                       # its usual tick leaves it be
        self.run_frames(window, 1.0)
        self.assertEqual(self.sent[before:], [])                             # held still: nothing more to say
        self.assertEqual(window.behaviour.state, "held")
        self.assertFalse(window._save_timer.isActive())                     # Buddy's own moves aren't a drag
        self.button.return_value = False                                     # let go where the page never heard
        self.run_frames(window, 1 / 60)
        self.assertEqual(window.behaviour.state, "fly")
        self.assertEqual(window._motion.mode, "fly")
        self.run_frames(window, 5.0)
        self.assertIsNone(window._motion)                                    # landed, and still
        self.assertFalse(window._frame_timer.isActive())
        self.assertEqual(window.behaviour.state, "idle")                     # a soft landing
        self.assertEqual(window._anchor, (400, floor))
        names = [name for name, _p in self.sent]
        self.assertIn("land", names)
        self.assertFalse(next(p for name, p in self.sent if name == "land")["hard"])
        self.assertIn(("pose", None), self.sent)
        self.assertEqual(self.host.tools["pets"]["pos"], {"x": 400, "y": floor})
        left = window._area()[0]
        self.assertEqual(window.behaviour.x, 400 - 83 - left)               # it walks on from there
        window.on_drop()                                                     # a late "drop" changes nothing
        self.assertIsNone(window._motion)

    def test_thrown_it_flies_and_lands_and_it_can_be_caught(self):
        pets, window = self.window_to_watch()
        window.on_grab({"fx": 0.5, "fy": 0.25})
        self.mouse.return_value = QPoint(300, 300)
        self.run_frames(window, 1.0)
        for x in range(300, 420, 15):                                        # a flick to the right
            self.mouse.return_value = QPoint(x, 300)
            self.run_frames(window, 1 / 60)
        window.on_drop()                                                     # the button still down: not a release
        self.assertEqual(window.behaviour.state, "held")
        self.button.return_value = False
        window.on_drop()
        self.assertEqual(window.behaviour.state, "fly")
        self.assertGreater(window._motion.vx, 500)
        self.run_frames(window, 0.1)
        self.assertGreater(window._anchor[0], 420)                           # flying on
        self.mouse.return_value = window._pet_rect().center()
        self.button.return_value = True
        window.on_grab({})                                                   # caught in the air
        self.assertEqual((window.behaviour.state, window._motion.mode), ("held", "held"))
        self.button.return_value = False
        window.on_drop()
        self.run_frames(window, 5.0)
        self.assertIsNone(window._motion)
        self.assertEqual(window._anchor[1], window._area()[3] + 1)

    def test_left_standing_in_mid_air_it_falls_to_the_floor(self):
        # Saved where an older Buddy let it be dragged to - anywhere, at any height.
        pets, window = self.window_to_watch({"pos": {"x": 400, "y": 300}})
        floor = window._area()[3] + 1
        self.assertEqual(window._anchor, (400, 300))
        self.mouse.return_value = QPoint(5, 5)                               # the mouse is elsewhere
        window._tick()
        self.assertEqual((window.behaviour.state, window._motion.mode), ("fly", "fly"))
        self.assertTrue(window._frame_timer.isActive())
        self.run_frames(window, 5.0)
        self.assertIsNone(window._motion)                                    # landed, and still
        self.assertEqual(window._anchor, (400, floor))
        self.assertIn("land", [name for name, _p in self.sent])
        self.assertEqual(self.host.tools["pets"]["pos"], {"x": 400, "y": floor})
        window._tick()
        self.assertIsNone(window._motion)                                    # on the floor, it stays there

    def test_the_settings_changing_mid_air_set_it_down(self):
        pets, window = self.window_to_watch()
        floor = window._area()[3] + 1
        window.on_grab({"fx": 0.5, "fy": 0.25})
        self.mouse.return_value = QPoint(400, 200)
        self.run_frames(window, 0.5)
        self.button.return_value = False
        window.on_drop()
        self.run_frames(window, 0.05)
        self.assertIsNotNone(window._motion)
        pets.set_value("size", "large")                                      # mid-air
        self.assertIsNone(window._motion)
        self.assertFalse(window._frame_timer.isActive())
        self.assertEqual(window.behaviour.state, "idle")
        self.assertEqual(window._anchor[1], floor)                           # set down on the floor below
        self.assertEqual(self.sent[-1][0], "pet")
        self.assertIn(("pose", None), self.sent)
        self.assertEqual(window.height(), pet_window.window_frame("large", True)[1])
        self.button.return_value = True
        window.on_grab({})
        self.run_frames(window, 0.3)
        pets.hide_pet()                                                      # put away in the hand
        self.assertIsNone(pets.window)
        self.assertEqual(self.host.tools["pets"]["pos"]["y"], floor)

    def test_held_and_thrown_it_goes_by_the_desk_and_lands_where_qt_has_the_screen(self):
        # The test's screen (800 x 800) with another at 200% left of it, which
        # puts it 800 px along the desk from where Qt has it.
        pets, window = self.window_to_watch()
        other = ((-1600, 0, -801, 799), (-1600, 0, -801, 799), 2.0)
        window._screens = lambda: physics.Screens([other, ((0, 0, 799, 799), (0, 0, 799, 799), 1.0)], primary=0)
        floor = window._area()[3] + 1
        window.on_grab({"fx": 0.5, "fy": 0.25})
        self.mouse.return_value = QPoint(400, 450)
        self.run_frames(window, 1.0)
        self.assertAlmostEqual(window._motion.x, 400 - 800)                  # the sums on the desk...
        self.assertEqual(window._anchor, (400, 450 + 96))                   # ...the window where Qt has it
        self.button.return_value = False
        window.on_drop()
        self.run_frames(window, 5.0)
        self.assertIsNone(window._motion)
        self.assertEqual(window._anchor, (400, floor))
        self.assertEqual(self.host.tools["pets"]["pos"], {"x": 400, "y": floor})

    def test_it_walks_on_from_where_it_was_left(self):
        # Left near the screen's left edge (on the floor), not in its middle -
        # whatever size the test's screen is.
        left = QApplication.primaryScreen().availableGeometry().left()
        pets, window = self.window_to_watch({"pos": {"x": left + 150, "y": 99_999}})
        pet_w = pet_window.window_frame("medium", False)[0]
        self.assertEqual(window._anchor[0], left + 150)
        self.assertEqual(window.behaviour.x, 150 - pet_w // 2)                # where it walks from: where it is
        window.behaviour.state, window.behaviour.facing = "walk", 1
        self.mouse.return_value = QPoint(left + 1000, 5)
        window._last_tick = self.clock[0] - 0.1
        window._tick()
        self.assertTrue(left + 150 <= window._anchor[0] <= left + 160)      # a step on, no jump

    def test_with_physics_off_a_drag_just_moves_it_and_it_stays_up(self):
        pets, window = self.window_to_watch({"physics": False, "walk": False, "pos": {"x": 400, "y": 300}})
        self.assertFalse(self.field(pets, "physics")["value"])
        self.mouse.return_value = QPoint(5, 5)
        window._tick()
        self.assertIsNone(window._motion)                                    # high up, and it stays there
        self.assertEqual(window._anchor, (400, 300))
        moved = []
        with mock.patch.object(window, "windowHandle", return_value=mock.Mock(startSystemMove=lambda: moved.append(1))):
            window.on_grab({"fx": 0.5, "fy": 0.25})                          # the page's grab, as if it were on
            window.on_drag()
        self.assertEqual(moved, [1, 1])                                      # the system moves it instead
        self.assertIsNone(window._motion)
        window._push_pet()
        self.assertFalse(self.sent[-1][1]["physics"])
        pets.set_value("physics", True)                                      # on again: it falls
        self.assertTrue(self.host.tools["pets"]["physics"])
        window._tick()
        self.assertEqual(window.behaviour.state, "fly")
        self.run_frames(window, 5.0)
        self.assertEqual(window._anchor[1], window._area()[3] + 1)

    def test_the_gallery_shows_its_own_life_not_the_hand_or_the_air(self):
        # Settings drawn mid-throw would keep a dangling or flying card: it never sees the landing.
        pets, window = self.window_to_watch()

        def drawn():
            return self.field(pets, "species")["state"]
        window.behaviour.state = "sit"
        self.assertEqual(drawn(), "sit")
        window.on_grab({"fx": 0.5, "fy": 0.26})
        self.assertEqual(window.behaviour.state, "held")
        self.assertEqual(drawn(), "idle")
        self.button.return_value = False
        window.on_drop()
        self.assertEqual(window.behaviour.state, "fly")
        self.assertEqual(drawn(), "idle")
        window.behaviour.land(hard=True)
        self.assertEqual(drawn(), "dizzy")

    def test_at_the_screens_edge_the_pet_reaches_it_and_the_bubble_slides_in(self):
        pets, window = self.window_to_watch()
        left, _top, right, bottom = window._area()
        window._anchor = (left + 10, bottom + 1)
        window._place()
        self.assertEqual(window._pet_rect().left(), left)                    # the pet's own box at the edge,
        self.assertLess(window.x(), left)                                    # the window's see-through side off it
        self.assertEqual(self.sent[-1], ("room", {"shift": left - window.x()}))
        window._anchor = (right - 5, bottom + 1)
        window._place()
        self.assertEqual(window._pet_rect().right(), right)
        self.assertEqual(self.sent[-1], ("room", {"shift": right - (window.x() + window.width() - 1)}))
        window._anchor = ((left + right) // 2, bottom + 1)
        window._place()
        self.assertEqual(self.sent[-1], ("room", {"shift": 0}))
        n = len(self.sent)
        window._place()
        self.assertEqual(len(self.sent), n)                                  # told once, not again and again


if __name__ == "__main__":
    unittest.main()
