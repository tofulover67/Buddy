#!/usr/bin/env python3
"""
Achievements - what's been done with Buddy, counted, and the badges earned
for it. The Achievements tab (pages/achievements/) shows them; the pet
(pages/pets/) celebrates them and grows with them.

Two kinds of number (STATS): a count that only goes up as things are done -
proxies rendered, presets applied, questions asked - and a peak, the largest
seen so far - the longest render, the most clips on one timeline. A tool
adds to a count with ToolPage.achieve("proxies", 12) (pages/base.py) and
sets a peak with achieve_peak(); what Resolve does on its own - renders
finishing, clips being graded - is read by core/resolve_stats.py and
core/render_watch.py. Two numbers are derived rather than kept: how many
different days Buddy has been used on, and how many achievements are
unlocked (so unlocking can unlock more).

An achievement (ACHIEVEMENTS) is one stat reaching one goal, in families
of tiers - "First proxy" at 1, "Proxy pipeline" at 100, "Proxy factory" at
1000 - each worth points by its tier, and the points make a level. Each
family has one text, with the goal filled in ("Render {n} proxies with
Project Setup"), so translations cover every tier at once.

Saved in ~/.buddy/tools/achievements/achievements.json (core/atomic_io.py:
a damaged file is recovered from its .bak or set aside, never read as
empty and saved over). No Qt here, so it's unit-tested as it is
(tests/test_achievements.py); whoever owns it (the shell) passes
callbacks for an unlock and for any change.
"""

from __future__ import annotations

import datetime
import os

from core import atomic_io, tool_dirs

BUCKET = "achievements"
FILE = "achievements.json"
VERSION = 1

COUNT = "count"
PEAK = "peak"
DERIVED = "derived"

MAX_VALUE = 10 ** 9
MAX_DAYS = 2000             # the days used kept - well past the last achievement for them
MAX_MEMO = 64 * 1024        # the watcher's notes, as JSON text

# Points for the first, second, third and later tiers of a family.
TIER_POINTS = (10, 25, 50, 100)
LEVEL_POINTS = 100          # a level every this many points

# id -> kind. A count (as "proxies rendered") or a peak (the largest seen);
# time is in seconds. DERIVED ones are worked out, never recorded.
STATS = {
    # Renders
    "proxies": COUNT,           # proxies rendered by Project Setup
    "renders": COUNT,           # render jobs finished in Resolve's Deliver page, while Buddy was open
    "render_seconds": COUNT,    # time those renders took, together
    "render_longest": PEAK,     # the one that took longest
    # Colour and the timeline (read from Resolve)
    "graded": COUNT,            # clips with a grade on them, over every timeline seen
    "timeline_clips": PEAK,     # most clips on one timeline
    "timeline_seconds": PEAK,   # longest timeline
    # Editing
    "presets": COUNT,           # clips given an Animation preset
    "zooms": COUNT,             # clips given zooms
    "transitions": COUNT,       # cuts given a transition
    "transcripts": COUNT,       # timelines transcribed
    "audio_fx": COUNT,          # Audio Assistant effects baked
    "chapters": COUNT,          # chapter lists copied
    "stills": COUNT,            # stills exported
    # Media
    "bins": COUNT,
    "imported": COUNT,
    "renamed": COUNT,
    "relinked": COUNT,
    "svgs": COUNT,
    "images": COUNT,
    "cleaned": COUNT,           # Generative: pictures cleaned, cut out or separated and saved
    "placed": COUNT,            # Footage Search: shots placed on the timeline
    # Workflow
    "audits": COUNT,
    "automations": COUNT,       # runs that reached the end
    "hotkeys": COUNT,
    "syncs": COUNT,
    # Buddy
    "questions": COUNT,
    "palettes": COUNT,
    "messages": COUNT,
    "tracked_seconds": PEAK,    # Time Tracker's total, as it stands
    "downloads": COUNT,
    "pats": COUNT,              # the pet, patted
    "games_won": COUNT,
    "days_used": DERIVED,
    "achievements": DERIVED,
}

# How a stat's numbers read: a plain count, or seconds shown as time.
TIME_STATS = {"render_seconds", "render_longest", "timeline_seconds", "tracked_seconds"}

GROUPS = ("Renders", "Colour", "Editing", "Media", "Workflow", "Buddy", "Games")


def _family(group, stat, tiers, text_many, text_one=None, in_minutes=False, in_hours=False):
    """One family of achievements: tiers [(id, title, goal)]. The text
    names the goal as {n} - in seconds' worth of minutes or hours for a
    time stat - and text_one is the wording when the goal is 1."""
    out = []
    for tier, (ach_id, title, goal) in enumerate(tiers):
        n = goal // 3600 if in_hours else goal // 60 if in_minutes else goal
        text = text_one if (goal == 1 and text_one) else text_many
        out.append({"id": ach_id, "group": group, "stat": stat, "goal": goal, "title": title,
                    "text": text.replace("{n}", f"{n:,}"), "points": TIER_POINTS[min(tier, len(TIER_POINTS) - 1)]})
    return out


ACHIEVEMENTS = [
    # Renders
    *_family("Renders", "proxies", [("proxy_1", "First proxy", 1), ("proxy_100", "Proxy pipeline", 100),
                                    ("proxy_1000", "Proxy factory", 1000)],
             "Render {n} proxies with Project Setup", "Render a proxy with Project Setup"),
    *_family("Renders", "renders", [("render_1", "Delivered", 1), ("render_25", "Regular delivery", 25),
                                    ("render_100", "Hundred deliveries", 100)],
             "Finish {n} renders in Resolve's Deliver page while Buddy is open",
             "Finish a render in Resolve's Deliver page while Buddy is open"),
    *_family("Renders", "render_longest", [("render_long", "Long haul", 30 * 60), ("render_overnight", "Overnight", 180 * 60)],
             "Finish a render that takes {n} minutes or longer", in_minutes=True),
    *_family("Renders", "render_seconds", [("render_hours_10", "Render hours", 10 * 3600),
                                           ("render_hours_100", "Render farm", 100 * 3600)],
             "Spend {n} hours rendering, all together", in_hours=True),
    # Colour
    *_family("Colour", "graded", [("grade_1", "First grade", 1), ("grade_100", "Hundred grades", 100),
                                  ("grade_1000", "Colourist", 1000)],
             "Grade {n} clips while Buddy is open", "Grade a clip while Buddy is open"),
    *_family("Colour", "timeline_clips", [("clips_500", "Big timeline", 500), ("clips_2000", "Epic", 2000)],
             "Have a timeline with {n} clips or more open"),
    *_family("Colour", "timeline_seconds", [("feature", "Feature length", 90 * 60)],
             "Have a timeline {n} minutes long or more open", in_minutes=True),
    # Editing
    *_family("Editing", "presets", [("anim_1", "Animator", 1), ("anim_50", "In motion", 50),
                                    ("anim_500", "Motion graphics", 500)],
             "Put Animation presets on {n} clips", "Put an Animation preset on a clip"),
    *_family("Editing", "zooms", [("zoom_25", "Punch in", 25)], "Put zooms on {n} clips"),
    *_family("Editing", "transitions", [("trans_25", "Smooth cut", 25)], "Put Buddy's transitions on {n} cuts"),
    *_family("Editing", "transcripts", [("sub_1", "Subtitled", 1), ("sub_25", "Caption crew", 25)],
             "Transcribe {n} timelines with Subtitles", "Transcribe a timeline with Subtitles"),
    *_family("Editing", "audio_fx", [("audio_10", "Sound work", 10)], "Bake {n} audio effects with Audio Assistant"),
    *_family("Editing", "chapters", [("chap_5", "Chaptered", 5)], "Copy {n} YouTube chapter lists"),
    *_family("Editing", "stills", [("stills_50", "Stills", 50)], "Export {n} stills with Marker Manager"),
    # Media
    *_family("Media", "bins", [("bins_50", "Organised", 50)], "Create {n} bins with Project Setup"),
    *_family("Media", "imported", [("import_500", "Importer", 500)], "Import {n} clips with Project Setup"),
    *_family("Media", "renamed", [("rename_250", "Renamer", 250)], "Rename {n} clips with Media Manager"),
    *_family("Media", "relinked", [("relink_100", "Relinker", 100)], "Relink {n} clips with Media Manager"),
    *_family("Media", "svgs", [("svg_25", "Vector", 25)], "Convert {n} SVG or Lottie files"),
    *_family("Media", "images", [("img_100", "Pictures", 100)], "Import {n} images with Image Importer"),
    *_family("Media", "cleaned", [("clean_10", "Clean plate", 10)], "Clean {n} pictures with Generative"),
    *_family("Media", "placed", [("found_10", "Found it", 10)], "Place {n} shots found with Footage Search"),
    # Workflow
    *_family("Workflow", "audits", [("audit_10", "Checked", 10), ("audit_100", "Quality control", 100)],
             "Check {n} timelines with Audit"),
    *_family("Workflow", "automations", [("auto_1", "Automated", 1), ("auto_25", "Hands off", 25)],
             "Run {n} automations to the end", "Run an automation to the end"),
    *_family("Workflow", "hotkeys", [("keys_100", "Hot keys", 100)], "Press Command Center hot keys {n} times"),
    *_family("Workflow", "syncs", [("sync_10", "In sync", 10)], "Sync {n} multicam timelines with Project Setup"),
    # Buddy
    *_family("Buddy", "questions", [("ask_10", "Curious", 10), ("ask_100", "Inquisitive", 100)],
             "Ask Buddy {n} questions"),
    *_family("Buddy", "palettes", [("pal_10", "Palette maker", 10)], "Make {n} palettes with Color Palette Manager"),
    *_family("Buddy", "messages", [("msg_50", "Sociable", 50)], "Send {n} messages on Buddy Network"),
    *_family("Buddy", "tracked_seconds", [("track_100", "Clocked in", 100 * 3600),
                                          ("track_1000", "Thousand hours", 1000 * 3600)],
             "Track {n} hours with Time Tracker", in_hours=True),
    *_family("Buddy", "days_used", [("days_7", "Regular", 7), ("days_30", "Month in", 30), ("days_100", "Hundred days", 100)],
             "Use Buddy on {n} different days"),
    *_family("Buddy", "downloads", [("dl_25", "Downloader", 25)], "Download {n} files with Web"),
    *_family("Buddy", "pats", [("pat_50", "Good pet", 50), ("pat_500", "Best friend", 500)], "Pat your pet {n} times"),
    *_family("Buddy", "achievements", [("coll_10", "Collector", 10), ("coll_25", "Trophy shelf", 25),
                                       ("coll_50", "Completionist", 50)],
             "Unlock {n} achievements"),
    # Games
    *_family("Games", "games_won", [("win_1", "Winner", 1), ("win_25", "Champion", 25), ("win_100", "Grand master", 100)],
             "Win {n} games in Games", "Win a game in Games"),
]
BY_ID = {a["id"]: a for a in ACHIEVEMENTS}
TOTAL_POINTS = sum(a["points"] for a in ACHIEVEMENTS)


def level_for(points: int) -> int:
    """The level `points` make: 1 to start, one more every LEVEL_POINTS."""
    return 1 + max(0, int(points)) // LEVEL_POINTS


def _number(value, top=MAX_VALUE):
    """A whole number from 0 to `top`, or None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        value = int(value)
    if not isinstance(value, int) or value < 0:
        return None
    return min(value, top)


def _clean_day(value):
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _clean_when(value):
    if not isinstance(value, str) or not 10 <= len(value) <= 32:
        return None
    try:
        datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def time_text(seconds) -> str:
    """Seconds as "2 h 10 min", "45 min", "30 s" - the hour and minute
    words are translated by the page."""
    seconds = int(seconds)
    if seconds >= 3600:
        hours, minutes = divmod(seconds // 60, 60)
        return f"{hours:,} h" if not minutes else f"{hours:,} h {minutes} min"
    if seconds >= 60:
        return f"{seconds // 60} min"
    return f"{seconds} s"


class Achievements:
    """The numbers and the badges. on_unlock(list of achievement dicts) and
    on_change() are callback lists the owner appends to."""

    def __init__(self, path=None, today=None):
        self.path = path or tool_dirs.file_path(BUCKET, FILE)
        self._today = today                 # a function giving the date (tests); None: today
        self.stats = {}                     # stat id -> number
        self.unlocked = {}                  # achievement id -> ISO time it was unlocked
        self.seen = set()                   # unlocked ones the Achievements tab has shown
        self.days = []                      # ISO dates Buddy was used on, oldest first
        self.memo = {}                      # the Resolve watcher's notes (core/render_watch.py)
        self.load_warnings = []
        self.on_unlock = []
        self.on_change = []
        self._load()

    # ------------------------------------------------------------ reading --

    def value(self, stat) -> int:
        kind = STATS.get(stat)
        if kind == DERIVED:
            return len(self.days) if stat == "days_used" else len(self.unlocked)
        return self.stats.get(stat, 0)

    def points(self) -> int:
        return sum(BY_ID[a]["points"] for a in self.unlocked if a in BY_ID)

    def level(self) -> int:
        return level_for(self.points())

    def summary(self) -> dict:
        points = self.points()
        return {"unlocked": len(self.unlocked), "total": len(ACHIEVEMENTS), "points": points,
                "total_points": TOTAL_POINTS, "level": level_for(points),
                "level_points": points % LEVEL_POINTS, "next_level": LEVEL_POINTS,
                "new": len(set(self.unlocked) - self.seen)}

    def progress(self) -> list:
        """Every achievement as the page shows it, in ACHIEVEMENTS order:
        {id, group, title, text, points, goal, value, done, when, new,
        value_text, goal_text, pct}."""
        out = []
        for a in ACHIEVEMENTS:
            value = self.value(a["stat"])
            done = a["id"] in self.unlocked
            shown = min(value, a["goal"]) if not done else a["goal"]
            time_stat = a["stat"] in TIME_STATS
            out.append({
                "id": a["id"], "group": a["group"], "title": a["title"], "text": a["text"], "points": a["points"],
                "goal": a["goal"], "value": shown, "done": done, "when": self.unlocked.get(a["id"]),
                "new": done and a["id"] not in self.seen,
                "value_text": time_text(shown) if time_stat else f"{shown:,}",
                "goal_text": time_text(a["goal"]) if time_stat else f"{a['goal']:,}",
                "pct": 100 if done else int(100 * shown / a["goal"]),
            })
        return out

    def recent(self, count=5) -> list:
        """The latest unlocked, newest first: [(achievement, when)]."""
        latest = sorted(self.unlocked.items(), key=lambda kv: kv[1], reverse=True)
        return [(BY_ID[a], when) for a, when in latest if a in BY_ID][:count]

    # ------------------------------------------------------------ writing --

    def record(self, stat, amount=1) -> list:
        """`amount` more of a count stat. The achievements it unlocked, if any."""
        amount = _number(amount)
        if STATS.get(stat) != COUNT or not amount:
            return []
        self.stats[stat] = min(MAX_VALUE, self.stats.get(stat, 0) + amount)
        return self._changed()

    def peak(self, stat, value) -> list:
        """A peak stat's latest reading: kept if it's the largest yet."""
        value = _number(value)
        if STATS.get(stat) != PEAK or value is None or value <= self.stats.get(stat, 0):
            return []
        self.stats[stat] = value
        return self._changed()

    def touch_day(self) -> list:
        """Buddy was used today."""
        today = (self._today() if self._today else datetime.date.today()).isoformat()
        if self.days and self.days[-1] == today:
            return []
        self.days = sorted(set(self.days) | {today})[-MAX_DAYS:]
        return self._changed()

    def note(self, key, value):
        """A watcher's note, kept between runs (see memo)."""
        self.memo[key] = value
        self.save()

    def mark_seen(self):
        """The Achievements tab showed what's unlocked."""
        if set(self.unlocked) - self.seen:
            self.seen = set(self.unlocked)
            self.save()
            self._tell(self.on_change)

    def reset(self):
        """Back to nothing - the numbers, the badges and the watcher's notes."""
        self.stats, self.unlocked, self.seen, self.days, self.memo = {}, {}, set(), [], {}
        self.save()
        self._tell(self.on_change)

    def _changed(self) -> list:
        unlocked = self._unlock()
        self.save()
        if unlocked:
            self._tell(self.on_unlock, unlocked)
        self._tell(self.on_change)
        return unlocked

    def _unlock(self) -> list:
        """Every achievement whose goal is reached - and again, since
        unlocking is itself counted ("Collector")."""
        now = datetime.datetime.now().isoformat(timespec="seconds")
        unlocked = []
        while True:
            fresh = [a for a in ACHIEVEMENTS if a["id"] not in self.unlocked and self.value(a["stat"]) >= a["goal"]]
            if not fresh:
                return unlocked
            for a in fresh:
                self.unlocked[a["id"]] = now
            unlocked.extend(fresh)

    @staticmethod
    def _tell(callbacks, *args):
        for fn in list(callbacks):
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - a listener's bug never loses the record
                pass

    # ------------------------------------------------------------- files --

    def _load(self):
        try:
            saved = atomic_io.read_json(self.path, default=None, warnings=self.load_warnings, expected_type=dict)
        except atomic_io.CorruptFileError:
            name = os.path.basename(self.path)
            try:
                moved = atomic_io.set_aside(self.path)
                self.load_warnings.append(f"{name} was damaged and has been kept as "
                                          f"{os.path.basename(moved or self.path)}. The achievements start over.")
            except OSError as err:
                self.load_warnings.append(f"{name} was damaged and could not be read ({err}).")
            return
        if not saved:
            return
        stats = saved.get("stats") if isinstance(saved.get("stats"), dict) else {}
        self.stats = {k: v for k, v in ((k, _number(v)) for k, v in stats.items())
                      if STATS.get(k) in (COUNT, PEAK) and v}
        unlocked = saved.get("unlocked") if isinstance(saved.get("unlocked"), dict) else {}
        self.unlocked = {k: w for k, w in ((k, _clean_when(v)) for k, v in unlocked.items()) if k in BY_ID and w}
        seen = saved.get("seen") if isinstance(saved.get("seen"), list) else []
        self.seen = {s for s in seen if s in self.unlocked}
        days = saved.get("days") if isinstance(saved.get("days"), list) else []
        self.days = sorted({d for d in (_clean_day(x) for x in days) if d})[-MAX_DAYS:]
        memo = saved.get("memo") if isinstance(saved.get("memo"), dict) else {}
        self.memo = memo if len(str(memo)) <= MAX_MEMO else {}

    def save(self):
        atomic_io.write_json(self.path, {"version": VERSION, "stats": self.stats, "unlocked": self.unlocked,
                                         "seen": sorted(self.seen), "days": self.days, "memo": self.memo})
