#!/usr/bin/env python3
"""
What the pet does, moment to moment - no Qt, no window, so it's tested as
it is (tests/test_pets.py). The window (pet_window.py) ticks it a few
times a second and draws what it says: a state, which way it faces, and
where it stands along the bottom of the screen.

States (STATES): "idle" (standing, breathing, blinking), "walk" (along
the screen, turning at the edges), "sit", "sleep" (left alone for a while),
"work" (a tool is busy - it types along), "happy" (patted), "cheer" (an
achievement), "held" (picked up, dangling), "fly" (let go - thrown or
dropped, in the air), "dizzy" (after a hard landing). What it chooses
between on its own - idle, walk, sit - is random, for a random while, so
two pets don't march in step; a seeded Random makes a test's pet
predictable.

Held and in the air it's the window's (physics.py moves it): its own life
waits - nothing ticks on, and a pat, a cheer or something happening in
Buddy leaves it as it is - until it lands (land()), dizzy for a moment
after a hard one, and carries on from there.

Species (SPECIES) and what unlocks them: a pet is earned with
achievements (core/achievements.py), by how many are unlocked.
"""

from __future__ import annotations

import random

STATES = ("idle", "walk", "sit", "sleep", "work", "happy", "cheer", "held", "fly", "dizzy")
CARRIED = ("held", "fly")     # the window's, not its own: nothing changes them but landing

# id -> (name, how many achievements unlock it)
SPECIES = {
    "blob": ("Blob", 0),
    "robot": ("Robot", 3),
    "shroomy": ("Shroomy", 5),
    "poof": ("Poof", 7),
    "blaze": ("Blaze", 9),
    "spike": ("Spike", 11),
    "cheddar": ("Cheddar", 13),
    "crumb": ("Crumb", 15),
    "nutty": ("Nutty", 17),
    "pebble": ("Pebble", 19),
    "snappy": ("Snappy", 21),
}
DEFAULT_SPECIES = "blob"

# Pet heights in pixels (its box is this high and a little wider).
SIZES = {"small": 96, "medium": 128, "large": 176}
DEFAULT_SIZE = "medium"

SPEED_PX_S = 38             # walking
HAPPY_S = 2.5               # a pat's glow
CHEER_S = 6.0               # an achievement's celebration
DIZZY_S = 2.5               # seeing stars after a hard landing
MAX_NAME = 24


def unlocked_species(achievements_count: int) -> list:
    """The species earned with this many achievements, in SPECIES order."""
    return [s for s, (_name, need) in SPECIES.items() if achievements_count >= need]


def clean_name(value) -> str:
    """A pet's name as typed, trimmed and no longer than MAX_NAME; "" for none."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:MAX_NAME]


class Behaviour:
    """One pet's mind. x is where it stands (its left edge) between 0 and
    `width`; tick(dt, ...) moves time on by dt seconds."""

    def __init__(self, width=1000.0, x=None, rng=None, walks=True, sleep_after_s=15 * 60):
        self.rng = rng or random.Random()
        self.width = max(0.0, float(width))
        self.x = float(x) if x is not None else self.width / 2
        self.facing = 1                 # 1 right, -1 left
        self.walks = walks
        self.sleep_after_s = sleep_after_s
        self.state = "idle"
        self._left = 0.0                # seconds left in the current state
        self._quiet = 0.0               # seconds since anything happened
        self._before_work = None        # the state to go back to after work
        self._pick()

    # ---------------------------------------------------------- outside --

    def set_width(self, width):
        self.width = max(0.0, float(width))
        self.x = min(self.x, self.width)

    def set_x(self, x):
        """Put down somewhere (landed after a throw): it carries on from there."""
        self.x = max(0.0, min(float(x), self.width))

    def poke(self):
        """Patted: happy for a moment, and wide awake."""
        self._quiet = 0.0
        if self.state not in CARRIED:
            self.state, self._left = "happy", HAPPY_S

    def cheer(self):
        """An achievement: a celebration, and wide awake."""
        self._quiet = 0.0
        if self.state not in CARRIED:
            self.state, self._left = "cheer", CHEER_S

    def pick_up(self):
        """Picked up: it dangles until it's let go."""
        self._quiet = 0.0
        self._before_work = None
        self.state, self._left = "held", 1e9

    def let_go(self):
        """Let go: in the air until it lands."""
        self.state, self._left = "fly", 1e9

    def land(self, hard=False):
        """Come to rest after a throw or a drop: dizzy for a moment after a
        hard landing, otherwise standing for a moment - then its usual life."""
        self._quiet = 0.0
        if hard:
            self.state, self._left = "dizzy", DIZZY_S
        else:
            self.state, self._left = "idle", self.rng.uniform(1.0, 3.0)

    def stand_still(self, seconds=3.0):
        """Mid-walk, stop for a moment (something's open over it)."""
        if self.state == "walk":
            self.state, self._left = "idle", max(self._left, seconds)

    def activity(self):
        """Something happened in Buddy: not sleepy any more."""
        self._quiet = 0.0
        if self.state == "sleep":
            self.state, self._left = "idle", 1.0

    def tick(self, dt, working=False) -> bool:
        """dt seconds pass; `working` while a tool is busy. True when what's
        drawn (state or facing) changed - the position changes on its own."""
        if self.state in CARRIED:
            return False                # the window has it until it lands
        dt = max(0.0, min(float(dt), 2.0))
        before = (self.state, self.facing)
        self._quiet += dt
        if working and self.state not in ("happy", "cheer", "dizzy", "work"):
            self._before_work = self.state if self.state != "sleep" else "idle"
            self.state, self._left = "work", 1e9
        elif not working and self.state == "work":
            self.state, self._left = (self._before_work or "idle"), 0.0
            self._before_work = None
        if self.state == "walk":
            self._step(dt)
        self._left -= dt
        if self._left <= 0:
            if self.state in ("happy", "cheer", "dizzy") and working:
                self.state, self._left = "work", 1e9
            elif self.sleep_after_s and self._quiet >= self.sleep_after_s and self.state != "work":
                self.state, self._left = "sleep", 1e9
            else:
                self._pick()
        return (self.state, self.facing) != before

    # ------------------------------------------------------------ inside --

    def _pick(self):
        """Something to do next: idle, walk or sit, for a while."""
        choices = [("idle", 3), ("sit", 2)] + ([("walk", 4)] if self.walks and self.width > 0 else [])
        total = sum(w for _s, w in choices)
        roll = self.rng.random() * total
        for state, weight in choices:
            roll -= weight
            if roll <= 0:
                break
        self.state = state
        self._left = {"idle": self.rng.uniform(3, 9), "sit": self.rng.uniform(6, 16),
                      "walk": self.rng.uniform(3, 10)}[state]
        if state == "walk" and self.rng.random() < 0.35:
            self.facing = -self.facing

    def _step(self, dt):
        self.x += self.facing * SPEED_PX_S * dt
        if self.x <= 0:
            self.x, self.facing = 0.0, 1
        elif self.x >= self.width:
            self.x, self.facing = self.width, -1
