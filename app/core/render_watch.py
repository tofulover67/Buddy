#!/usr/bin/env python3
"""
Watches Resolve for the achievements (core/achievements.py): render jobs
finishing in the Deliver page, and the open timeline's size and grades -
what Buddy doesn't do itself but can see.

Every so often, while Buddy is connected, it asks core/resolve_stats.py's
reads through the Resolve reader's process (core/resolve_reader.py
ResolveCalls), so a timeline playing never holds Buddy up; the answers
become stats. The watcher's own notes - which render jobs it has seen in
which state, how many graded clips each timeline had - live in the
achievements file (Achievements.memo), so a render that finished while
Buddy was closed isn't counted, and a timeline's grades count once.

A render counts when a job the watcher saw queued or rendering is complete
the next time it looks. The watcher only ever reads; no Qt but a timer.
"""

from PySide6.QtCore import QObject, QTimer

from core import resolve_stats
from core.resolve_reader import ResolveCalls

RENDERS_EVERY_S = 20
TIMELINE_EVERY_S = 180
MAX_NOTED_JOBS = 400
MAX_NOTED_TIMELINES = 300
COMPLETE = "Complete"
WAITING = ("Ready", "Rendering")


def render_changes(noted, jobs):
    """What a fresh list of render jobs means against the notes:
    (new notes, finished) - the jobs now complete that were seen waiting
    or rendering before, as [{id, ms, timeline}]. No Qt; tested as it is."""
    seen = {j["id"]: j for j in jobs if isinstance(j, dict) and j.get("id")}
    finished = []
    for job_id, job in seen.items():
        before = noted.get(job_id)
        if job["status"] == COMPLETE and before in WAITING:
            finished.append({"id": job_id, "ms": int(job.get("ms") or 0), "timeline": job.get("timeline", "")})
    # Jobs still in the list keep their latest status; gone ones are forgotten.
    fresh = {job_id: job["status"] for job_id, job in seen.items()}
    if len(fresh) > MAX_NOTED_JOBS:
        fresh = dict(list(fresh.items())[-MAX_NOTED_JOBS:])
    return fresh, finished


def grade_changes(noted, facts):
    """(new notes, more) - how many more graded clips this timeline has than
    the most it was seen with, from a timeline_facts() answer."""
    tl_id = str(facts.get("id") or "")
    if not tl_id:
        return noted, 0
    graded = max(0, int(facts.get("graded") or 0))
    before = int(noted.get(tl_id) or 0)
    more = max(0, graded - before)
    fresh = dict(noted)
    if more:
        fresh[tl_id] = graded
    if len(fresh) > MAX_NOTED_TIMELINES:
        fresh = dict(list(fresh.items())[-MAX_NOTED_TIMELINES:])
    return fresh, more


class RenderWatch(QObject):
    """Owned by the shell (`host`: connected, controller, achievements)."""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self._calls = ResolveCalls(self)
        self._render_timer = QTimer(self, interval=RENDERS_EVERY_S * 1000, timeout=self._ask_renders)
        self._timeline_timer = QTimer(self, interval=TIMELINE_EVERY_S * 1000, timeout=self._ask_timeline)

    def start(self):
        self._render_timer.start()
        self._timeline_timer.start()
        QTimer.singleShot(5000, self._ask_renders)
        QTimer.singleShot(15000, self._ask_timeline)

    def stop(self):
        self._render_timer.stop()
        self._timeline_timer.stop()

    def _ready(self):
        return bool(getattr(self.host, "connected", False)) and self.host.controller is not None and not self._calls.busy()

    def _ask_renders(self):
        if not self._ready():
            return
        controller = self.host.controller
        self._calls.call("stats.renders", {}, lambda: resolve_stats.render_jobs(controller), self._on_renders)

    def _ask_timeline(self):
        if not self._ready():
            return
        controller = self.host.controller
        self._calls.call("stats.timeline", {}, lambda: resolve_stats.timeline_facts(controller), self._on_timeline)

    def _on_renders(self, jobs, error):
        if error is not None or not isinstance(jobs, list):
            return
        ach = self.host.achievements
        before = ach.memo.get("render_jobs") or {}
        noted, finished = render_changes(before, jobs)
        if noted != before:
            ach.note("render_jobs", noted)
        for job in finished:
            seconds = job["ms"] // 1000
            ach.record("renders", 1)
            if seconds:
                ach.record("render_seconds", seconds)
                ach.peak("render_longest", seconds)

    def _on_timeline(self, facts, error):
        if error is not None or not isinstance(facts, dict) or not facts:
            return
        ach = self.host.achievements
        ach.peak("timeline_clips", facts.get("clips") or 0)
        ach.peak("timeline_seconds", facts.get("seconds") or 0)
        noted, more = grade_changes(ach.memo.get("graded") or {}, facts)
        if more:
            ach.note("graded", noted)
            ach.record("graded", more)
