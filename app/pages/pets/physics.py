#!/usr/bin/env python3
"""
The pet picked up and thrown - no Qt, plain numbers, so it's tested as it
is (tests/test_pets.py). The window (pet_window.py) runs a Motion about
sixty times a second while the pet is in the hand or in the air, moves
itself to where the pet now is, and hands the page its pose: how far it
has swung and how much it's stretched.

Screen pixels, y pointing down. Where the pet is, is its feet - the bottom
middle of its box - as everywhere else.

In the hand (Motion.grab, .hold): the feet follow the mouse, easing in a
moment from where they were when it was picked up to hanging by the scruff
(just below the top of its head - the page says where). Below the scruff
the body swings, a damped pendulum pushed by the hand: moved right, it
lags to the left, swings back and settles. It stretches a little with
speed.

Let go (Motion.let_go, .step): it leaves the hand at the speed the hand
had in the last moment, as far as the feet could follow it (a hand that
had stopped just drops it), falls, and bounces off the edges of the
screens' available areas (Desk) - the walls, the ceiling, the floor above
the taskbar or the Dock - losing some speed at each, until it slides to a
stop on a floor, upright. An edge with another screen beyond it is no
wall: it flies on across. A pet standing with nothing under it falls the
same way (Motion.fall). All of it in desk coordinates (Screens): Qt's,
with the gaps closed that Windows leaves between screens scaled
differently.
"""

from __future__ import annotations

import math

GRAVITY = 2600.0            # px/s², down
AIR_DRAG = 0.35             # in the air it loses this much of its speed a second (a light drag)
WALL_BOUNCE = 0.55          # what a wall or the ceiling gives back of the speed into it...
FLOOR_BOUNCE = 0.35         # ...and a floor
FLOOR_GRIP = 0.8            # the sideways speed kept at each floor hit
SLIDE_FRICTION = 1600.0     # px/s² of slowing while it slides along a floor
STOP_BOUNCE = 120.0         # px/s: hitting a floor slower than this, it stops bouncing
HARD_LANDING = 1500.0       # px/s: hitting a floor faster than this leaves it dizzy
SCOOT = 160.0               # px/s: come to a stop across two screens, it shuffles onto one
MAX_THROW = 4000.0          # px/s
THROW_SAMPLE_S = 0.08       # a throw's speed is the feet's over this long before letting go
HOLD_EASE_S = 0.15          # picked up, it eases into hanging by the scruff over this long
SCRUFF = (0.5, 0.28)        # where it hangs from, as fractions of its box, when the page hasn't said
BODY_MIDDLE = 0.58          # the body's middle, down its box (between the head's top and the feet)
MIN_LENGTH = 0.35           # the pendulum is at least this much of the pet's height
SWING_DAMPING = 3.5         # /s
MAX_SWING = math.radians(50)
MAX_HAND_ACCEL = 25000.0    # px/s²: a jerk of the mouse counts as no more than this
HAND_PUSH = 0.5             # how much of the hand's push the body feels (all of it, any brisk drag hits MAX_SWING)
SMOOTH_S = 0.05             # the hand's speed, and how it changes, smoothed over about this long
AIR_SPRING = 28.0           # /s²: in the air it rights itself, slowly...
AIR_DAMPING = 2.5           # /s
MAX_AIR_SWING = math.radians(60)
FLOOR_SPRING = 160.0        # ...and on a floor, at once
FLOOR_DAMPING = 22.0
WOBBLE = 0.004              # rad/s of wobble for each px/s it hits a wall at...
MAX_WOBBLE = 6.0            # ...up to this
STRETCH = 0.08              # it stretches (scaleY) by up to this...
STRETCH_SPEED = 2000.0      # ...at this speed, px/s...
STRETCH_S = 0.06            # ...easing there over about this long
SUBSTEP_S = 0.008           # the sums move on in steps no longer than this
MAX_FRAME_S = 0.05          # a late frame counts as no longer than this
MAX_FLIGHT_S = 20.0         # however it bounces, it's set down after this long
TOUCH = 2.0                 # px: edges this close meet
REAL_TOUCH = 2.0            # real pixels: screens whose edges are this close touch (Screens; Qt rounds sizes)


def throw_velocity(samples, now, over=THROW_SAMPLE_S, cap=MAX_THROW):
    """How fast the feet were going, (vx, vy) px/s, from (time, x, y)
    samples: from the first to the last of them in the `over` seconds up to
    `now`, no faster than `cap`. A hand that had stopped gives (0, 0)."""
    recent = [s for s in samples if now - over <= s[0] <= now]
    if len(recent) < 2 or recent[-1][0] - recent[0][0] < 1e-3:
        return 0.0, 0.0
    (t0, x0, y0), (t1, x1, y1) = recent[0], recent[-1]
    vx, vy = (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)
    speed = math.hypot(vx, vy)
    if speed > cap:
        vx, vy = vx * cap / speed, vy * cap / speed
    return vx, vy


def _ease(k):
    """0..1 eased in and out (smoothstep)."""
    k = min(1.0, max(0.0, k))
    return k * k * (3 - 2 * k)


def _toward(value, target, dt, over):
    """value moved towards target, smoothed over about `over` seconds."""
    return value + (target - value) * (1 - math.exp(-dt / over))


class Desk:
    """The screens' available areas, as the pet meets them. rects: one or
    more (left, top, right, bottom), right and bottom inclusive as Qt gives
    them. A box stays inside them; an edge with another area just beyond
    it - all along the box's side of it - is no edge, so two screens side
    by side are one wide floor to fly across."""

    def __init__(self, rects):
        self.areas = [(float(left), float(top), float(right) + 1, float(bottom) + 1)
                      for left, top, right, bottom in rects if right >= left and bottom >= top]
        if not self.areas:
            raise ValueError("a desk needs a screen")

    def home(self, x, y):
        """The area the feet at (x, y) stand in - the one holding the point
        just above them - or else the nearest."""
        px, py = x, y - 0.5
        for a in self.areas:
            if a[0] <= px < a[2] and a[1] <= py < a[3]:
                return a
        return min(self.areas, key=lambda a: math.hypot(max(a[0] - px, 0, px - a[2]), max(a[1] - py, 0, py - a[3])))

    def across(self, x, top, bottom):
        """(left, right): how far sideways a box from `top` to `bottom`,
        its middle at x, may go - the edges of the area it's in, carried on
        across any area beside it that covers the whole of that height."""
        def covers(a):
            return a[1] <= top + TOUCH and a[3] >= bottom - TOUCH

        here = next((a for a in self.areas if a[0] <= x < a[2] and covers(a)), None) or self.home(x, bottom)
        left, right = here[0], here[2]
        grew = True
        while grew:
            grew = False
            for a in self.areas:
                if covers(a) and a[0] < left <= a[2] + TOUCH:
                    left, grew = a[0], True
                if covers(a) and a[0] - TOUCH <= right < a[2]:
                    right, grew = a[2], True
        return left, right

    def up_down(self, left, right, y):
        """(ceiling, floor) for a box from `left` to `right` with its feet at
        y: the lowest ceiling and the highest floor of the areas it's over
        at that height (half over a ledge, it still stands on it), carried
        on through any area above or below that covers the box's width."""
        def over(a):
            return a[0] < right and a[2] > left if right > left else a[0] <= left < a[2]

        holding = [a for a in self.areas if over(a) and a[1] < y <= a[3] + TOUCH]
        if holding:
            ceiling, floor = max(a[1] for a in holding), min(a[3] for a in holding)
        else:
            _l, ceiling, _r, floor = self.home((left + right) / 2, y)
        grew = True
        while grew:
            grew = False
            for a in self.areas:
                if a[0] <= left + TOUCH and a[2] >= right - TOUCH:
                    if a[1] <= floor + TOUCH and a[3] > floor:
                        floor, grew = a[3], True
                    if a[3] >= ceiling - TOUCH and a[1] < ceiling:
                        ceiling, grew = a[1], True
        return ceiling, floor

    def clamp(self, x, y, half_w, h):
        """Feet at (x, y) moved as little as it takes for a box `half_w`
        either side of them and `h` high to be on the desk."""
        l, t, r, b = self.home(x, y)
        x = min(max(x, l), r - 0.5)                              # over the area it's nearest,
        ceiling, floor = self.up_down(x, x, min(max(y, t + 0.5), b))
        y = min(max(y, ceiling + h), floor)                      # inside it up and down,
        left, right = self.across(x, y - h, y)
        x = min(max(x, left + half_w), right - half_w)           # then sideways as far as the areas reach,
        ceiling, floor = self.up_down(x - half_w, x + half_w, y)
        return x, min(max(y, ceiling + h), floor)                # and up and down over all of its width


class Screens:
    """The screens as Qt places them, and the desk the pet flies over.

    On Windows, Qt keeps each screen's top-left corner in real pixels but
    gives its size in its own scaled pixels: a screen at 150% that starts
    3840 real pixels along - right of another at 150%, 2560 wide as Qt
    counts it - starts at x 3840, not 2560. The two touch, but between them
    lie 1280 px of nothing, and a thrown pet would stop at that edge as at a
    wall. So the pet's sums use desk coordinates: each screen shifted so
    that screens touching in real pixels touch here too (Desk), the primary
    screen where Qt has it. to_desk() takes a point (the mouse) to them,
    to_screen() brings one (the feet) back.

    screens: (geometry, available, scale) for each: rects (left, top,
    right, bottom), right and bottom inclusive as Qt gives them; scale:
    real pixels to Qt's - 1 where Qt's coordinates already run on without
    gaps (macOS), and then nothing is shifted. primary: its index."""

    def __init__(self, screens, primary=0):
        self.geometry = [tuple(float(v) for v in g) for g, _a, _s in screens]
        self.available = [tuple(float(v) for v in a) for _g, a, _s in screens]
        if not self.geometry:
            raise ValueError("a desk needs a screen")
        real = [(l, t, l + (r - l + 1) * s, t + (b - t + 1) * s)      # right and bottom just past it
                for (l, t, r, b), (_g, _a, s) in zip(self.geometry, screens)]
        primary = primary if 0 <= primary < len(screens) else 0
        shifts = {primary: (0.0, 0.0)}
        queue = [primary]
        while queue:
            i = queue.pop(0)
            (l, t, r, b), (rl, rt, rr, rb), (dx, dy) = self.geometry[i], real[i], shifts[i]
            for j, (gl, gt, gr, gb) in enumerate(self.geometry):
                if j in shifts:
                    continue
                jl, jt, jr, jb = real[j]
                side_by_side = min(rb, jb) - max(rt, jt) > 0
                stacked = min(rr, jr) - max(rl, jl) > 0
                if side_by_side and abs(jl - rr) <= REAL_TOUCH:          # to its right
                    shifts[j] = (dx + r + 1 - gl, dy)
                elif side_by_side and abs(rl - jr) <= REAL_TOUCH:        # to its left
                    shifts[j] = (dx + l - gr - 1, dy)
                elif stacked and abs(jt - rb) <= REAL_TOUCH:             # below it
                    shifts[j] = (dx, dy + b + 1 - gt)
                elif stacked and abs(rt - jb) <= REAL_TOUCH:             # above it
                    shifts[j] = (dx, dy + t - gb - 1)
                else:
                    continue
                queue.append(j)
        self.shifts = [shifts.get(i, (0.0, 0.0)) for i in range(len(self.geometry))]
        self.desk_geometry = [(l + dx, t + dy, r + dx, b + dy)
                              for (l, t, r, b), (dx, dy) in zip(self.geometry, self.shifts)]

    def desk(self):
        """The screens' available areas, in desk coordinates."""
        return Desk([(l + dx, t + dy, r + dx, b + dy) for (l, t, r, b), (dx, dy) in zip(self.available, self.shifts)])

    @staticmethod
    def _which(rects, x, y):
        """The screen holding the point just above (x, y) - feet stand a
        pixel past the bottom of a screen with no taskbar - or else the nearest."""
        py = y - 0.5
        for i, (l, t, r, b) in enumerate(rects):
            if l <= x < r + 1 and t <= py < b + 1:
                return i
        return min(range(len(rects)), key=lambda i: math.hypot(max(rects[i][0] - x, 0, x - rects[i][2] - 1),
                                                              max(rects[i][1] - py, 0, py - rects[i][3] - 1)))

    def to_desk(self, x, y):
        """A point Qt gave (the mouse, the feet) in desk coordinates."""
        dx, dy = self.shifts[self._which(self.geometry, x, y)]
        return x + dx, y + dy

    def to_screen(self, x, y):
        """A point in desk coordinates (where the pet's feet now are) as Qt places it."""
        dx, dy = self.shifts[self._which(self.desk_geometry, x, y)]
        return x - dx, y - dy


class Motion:
    """One pet in the hand or in the air. desk: a Desk; size: its box (w,
    h); feet: where it stands; scruff: where it hangs from, (x, y) as
    fractions of its box. mode: "held", "fly" or "rest".

    angle is the swing, in radians: 0 hanging straight down from the scruff
    (or, in the air and on a floor, upright); above 0, the body is to the
    right of the scruff. pose() turns it into what the page draws."""

    def __init__(self, desk, size, feet, scruff=SCRUFF):
        self.desk = desk
        self.w, self.h = float(size[0]), float(size[1])
        self.x, self.y = float(feet[0]), float(feet[1])
        self.vx = self.vy = 0.0
        self.angle = 0.0
        self.spin = 0.0                 # how fast the angle changes, rad/s
        self.stretch = 1.0              # scaleY; scaleX keeps the area
        self.mode = "rest"
        self.hardest = 0.0              # the hardest it has hit a floor since it was let go, px/s
        self._t = 0.0                   # the last frame in the hand
        self._grabbed = 0.0
        self._from = self._to = (0.0, 0.0)
        self._samples = []              # (time, x, y) in the hand, of the track a throw goes by...
        self._track = (0.0, 0.0)        # ...the feet's moves without the ease into hanging (hold)
        self._eased = (0.0, 0.0)        # the ease's offset at the last frame
        self._hand_v = (0.0, 0.0)       # the feet's speed in the hand, smoothed...
        self._hand_a = (0.0, 0.0)       # ...and how it changes, smoothed
        self._landed = False
        self._flown = 0.0
        self.set_scruff(scruff)

    def set_scruff(self, scruff):
        fx, fy = (min(1.0, max(0.0, float(v))) for v in scruff)
        self.scruff = (fx, fy)
        # Where the feet hang from the scruff, and how long the pendulum is.
        self._to = ((0.5 - fx) * self.w, (1 - fy) * self.h)
        self.length = max(MIN_LENGTH * self.h, math.hypot((0.5 - fx) * self.w, (BODY_MIDDLE - fy) * self.h))

    def pose(self):
        """(degrees, scale x, scale y) as the page draws them, about the
        scruff. CSS turns clockwise as drawn: a body swung to the right of
        its scruff (angle > 0) is turned anticlockwise, so the degrees are
        the angle's the other way round."""
        return -math.degrees(self.angle), 1 / math.sqrt(self.stretch), self.stretch

    # ------------------------------------------------------- in the hand --

    def grab(self, hand, now):
        """Picked up (or caught) with the mouse at `hand`, at time `now` (s).
        Caught in the air, it's brought up short: the body swings on the
        way it was flying."""
        flying = (self.vx, self.vy) if self.mode == "fly" else (0.0, 0.0)
        self.mode = "held"
        self._from = (self.x - hand[0], self.y - hand[1])
        self._grabbed = self._t = now
        self._samples = [(now, self.x, self.y)]
        self._track, self._eased = (self.x, self.y), self._from
        self._hand_v, self._hand_a = flying, (0.0, 0.0)
        self.vx = self.vy = 0.0

    def hold(self, hand, now):
        """A frame in the hand: the mouse is at `hand` at time `now`. The
        feet follow it, the body swings."""
        if self.mode != "held":
            return
        dt = min(max(now - self._t, 0.0), MAX_FRAME_S)
        self._t = now
        k = _ease((now - self._grabbed) / HOLD_EASE_S)
        ox = self._from[0] + (self._to[0] - self._from[0]) * k
        oy = self._from[1] + (self._to[1] - self._from[1]) * k
        x, y = self.desk.clamp(hand[0] + ox, hand[1] + oy, self.w / 2, self.h)
        if dt > 0:
            # The scruff moves with the feet: its speed and how that
            # changes, smoothed - the mouse moves in whole pixels on
            # frames that don't come quite evenly.
            vx = _toward(self._hand_v[0], (x - self.x) / dt, dt, SMOOTH_S)
            vy = _toward(self._hand_v[1], (y - self.y) / dt, dt, SMOOTH_S)
            ax = _toward(self._hand_a[0], (vx - self._hand_v[0]) / dt, dt, SMOOTH_S)
            ay = _toward(self._hand_a[1], (vy - self._hand_v[1]) / dt, dt, SMOOTH_S)
            size = math.hypot(ax, ay)
            if size > MAX_HAND_ACCEL:
                ax, ay = ax * MAX_HAND_ACCEL / size, ay * MAX_HAND_ACCEL / size
            self._hand_v, self._hand_a = (vx, vy), (ax, ay)
            self._swing_held(ax, ay, dt)
            self.stretch = _toward(self.stretch, 1 + STRETCH * min(1.0, math.hypot(vx, vy) / STRETCH_SPEED),
                                   dt, STRETCH_S)
        # What a throw goes by: the feet's moves, less the ease's part of
        # them wherever the desk's edges didn't stop them - a toss while
        # it's still easing into hanging leaves at the hand's speed, and a
        # pet held against an edge at none.
        free_x, free_y = abs(x - hand[0] - ox) < 1e-6, abs(y - hand[1] - oy) < 1e-6
        tx = self._track[0] + x - self.x - (ox - self._eased[0] if free_x else 0.0)
        ty = self._track[1] + y - self.y - (oy - self._eased[1] if free_y else 0.0)
        self._track, self._eased = (tx, ty), (ox, oy)
        self.x, self.y = x, y
        self._samples.append((now, tx, ty))
        while len(self._samples) > 2 and self._samples[1][0] < now - 2 * THROW_SAMPLE_S:
            del self._samples[0]

    def _swing_held(self, ax, ay, dt):
        """The body below the scruff: a damped pendulum whose pivot speeds
        up at (ax, ay) - angle'' = -((g - ay) / L) sin(angle) - (ax / L)
        cos(angle) - c angle', with a = HAND_PUSH of the hand's. A hand
        speeding up to the right (ax > 0) swings the body left (angle < 0):
        it lags behind. Pulled down faster than it would fall, there's no
        pull either way - it doesn't flip over."""
        pull = max(0.0, GRAVITY - HAND_PUSH * ay) / self.length
        push = HAND_PUSH * ax / self.length
        steps = max(1, math.ceil(dt / SUBSTEP_S))
        h = dt / steps
        for _ in range(steps):
            self.spin += (-pull * math.sin(self.angle) - push * math.cos(self.angle) - SWING_DAMPING * self.spin) * h
            self.angle += self.spin * h
            self._limit(MAX_SWING)

    def _limit(self, most):
        if abs(self.angle) > most:
            self.angle = math.copysign(most, self.angle)
            if self.angle * self.spin > 0:
                self.spin = 0.0

    def let_go(self, now):
        """Let go at time `now`: thrown at the speed the hand had, as far as
        the feet could follow it (held still, it just drops)."""
        if self.mode == "held":
            self.fall(throw_velocity(self._samples, now))

    def fall(self, velocity=(0.0, 0.0)):
        """Off into the air at `velocity` (vx, vy) px/s: let go - or, at
        rest, with nothing under its feet (the floor it stood on gone)."""
        self.vx, self.vy = (float(v) for v in velocity)
        self.mode = "fly"
        self.hardest = 0.0
        self._landed = False
        self._flown = 0.0

    # -------------------------------------------------------- in the air --

    def step(self, dt):
        """dt seconds in the air. What happened, in order: ("land", speed
        px/s) the first time it touched a floor and each time it hit one
        hard enough to bounce, and ("rest", the hardest of those) once it
        has come to a stop."""
        events = []
        if self.mode != "fly":
            return events
        dt = min(max(dt, 0.0), MAX_FRAME_S)
        steps = max(1, math.ceil(dt / SUBSTEP_S))
        for _ in range(steps):
            self._fly(dt / steps, events)
            if self.mode != "fly":
                break
        return events

    def _fly(self, dt, events):
        half, h = self.w / 2, self.h
        self._flown += dt
        self.vy += GRAVITY * dt
        drag = math.exp(-AIR_DRAG * dt)
        self.vx, self.vy = self.vx * drag, self.vy * drag
        # Sideways, at the height it's at: a wall turns it round.
        left, right = self.desk.across(self.x, self.y - h, self.y)
        x = self.x + self.vx * dt
        if x - half < left or x + half > right:
            into_left = x - half < left
            x = left + half if into_left else right - half
            if (self.vx < 0) == into_left and self.vx:
                self.spin -= math.copysign(min(MAX_WOBBLE, abs(self.vx) * WOBBLE), self.vx)
                self.vx = -self.vx * WALL_BOUNCE
        self.x = x
        # Up and down, over where it now is: the ceiling turns it round,
        # a floor bounces it, less and less, until it slides.
        ceiling, floor = self.desk.up_down(x - half, x + half, self.y)
        y = self.y + self.vy * dt
        on_floor = False
        if y >= floor:
            y = floor
            if self.vy > 0:
                impact = self.vy
                bounces = impact >= STOP_BOUNCE
                if bounces or not self._landed:
                    events.append(("land", impact))
                self._landed = True
                self.hardest = max(self.hardest, impact)
                if bounces:
                    self.vy = -impact * FLOOR_BOUNCE
                    self.vx *= FLOOR_GRIP
                else:
                    self.vy = 0.0
            on_floor = self.vy >= 0
        elif y - h < ceiling:
            y = ceiling + h
            if self.vy < 0:
                self.vy = -self.vy * WALL_BOUNCE
        self.y = y
        if on_floor:
            self.vy = 0.0
            self.vx = math.copysign(max(0.0, abs(self.vx) - SLIDE_FRICTION * dt), self.vx)
            l, _t, r, _b = self.desk.home(self.x, self.y)
            if abs(self.vx) < SCOOT and (self.x - half < l - 0.5 or self.x + half > r + 0.5):
                # Slowing to a stop across two screens: it shuffles onto
                # the one its feet are on (off a ledge, it falls).
                self.vx = math.copysign(SCOOT, 1 if self.x - half < l else -1)
        # The swing rights itself - slowly in the air, at once on a floor.
        spring, damping = (FLOOR_SPRING, FLOOR_DAMPING) if on_floor else (AIR_SPRING, AIR_DAMPING)
        self.spin += (-spring * self.angle - damping * self.spin) * dt
        self.angle += self.spin * dt
        self._limit(MAX_AIR_SWING)
        self.stretch = _toward(self.stretch, 1 + (0 if on_floor else STRETCH * min(1.0, abs(self.vy) / STRETCH_SPEED)),
                               dt, STRETCH_S)
        if on_floor and self.vx == 0 and abs(self.angle) < math.radians(1) and abs(self.spin) < 0.5:
            self._rest()
            events.append(("rest", self.hardest))
        elif self._flown > MAX_FLIGHT_S:
            self.set_down()
            events.append(("rest", self.hardest))

    def _rest(self):
        self.vx = self.vy = self.angle = self.spin = 0.0
        self.stretch = 1.0
        self.mode = "rest"

    def set_down(self):
        """Straight down onto the floor below it, still and upright, on one
        screen (stopped in the hand or mid-air: the settings changed, or it
        was put away)."""
        half = self.w / 2
        x, y = self.desk.clamp(self.x, self.y, half, self.h)
        l, _t, r, _b = self.desk.home(x, y)
        x = min(max(x, l + half), r - half)
        self.x, self.y = x, self.desk.up_down(x - half, x + half, y)[1]
        self._rest()
