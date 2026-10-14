#!/usr/bin/env python3
"""
Achievements - the badges earned with Buddy's tools (core/achievements.py
says what they are and keeps the numbers; this shows them). Not a tab: a
page in Settings (General > Achievements - core/settings_dialog.py takes it
from the shell's `extras`), with the level, the points and how many are
unlocked at the top, then a card for each in groups - Renders, Colour,
Editing, Media, Workflow, Buddy, Games - with how far along it is, the day
it was unlocked, and New on the ones unlocked since the page was last
looked at. Its settings: a notification when one unlocks (on by default),
and a reset.
"""

import time

from PySide6.QtCore import QObject

from core.achievements import Achievements
from core.settings_dialog import refresh_open
from core import settings_form as sf

DEFAULTS = {"notify": True}
NEW_FOR_S = 120         # what was new when the page was first built stays marked this long


class AchievementsSettings(QObject):
    """The shell's achievements page (host: the shell - tool_settings, achievements)."""

    tool_id = "achievements"
    display_name = "Achievements"
    is_placeholder = False

    def __init__(self, host):
        super().__init__(host if isinstance(host, QObject) else None)
        self.host = host
        self.settings = host.tool_settings(self.tool_id, dict(DEFAULTS))
        self.ach = getattr(host, "achievements", None) or Achievements()
        self.ach.on_change.append(self._changed)
        self._fresh = set()         # unlocked and not yet seen when the page was built
        self._fresh_at = 0.0

    def _changed(self):
        refresh_open(self.tool_id)

    def items(self) -> list:
        """The badges for the page: what's unlocked since the page was last
        looked at is marked new for a while, and counts as seen now."""
        now = time.monotonic()
        if now - self._fresh_at > NEW_FOR_S:
            self._fresh = set(self.ach.unlocked) - set(self.ach.seen)
            self._fresh_at = now
            self.ach.mark_seen()
        out = self.ach.progress()
        for item in out:
            item["new"] = item["done"] and item["id"] in self._fresh
        return out

    def settings_pages(self):
        summary = self.ach.summary()
        fields = [
            sf.hint("Use the tools, and the badges follow. Resolve's own renders and grades count while Buddy "
                    "is open."),
            sf.info("Level", str(summary["level"])),
            sf.info("Points", f"{summary['points']:,} of {summary['total_points']:,}"),
            sf.info("Unlocked", f"{summary['unlocked']} of {summary['total']}"),
            sf.check("notify", "Show a notification when an achievement unlocks", bool(self.settings.get("notify", True))),
            sf.buttons(("Reset achievements…", "reset", {"kind": "danger"})),
            sf.line(),
            sf.achievements(self.items(), summary),
        ]
        return [{"id": "achievements", "group": "general", "title": "Achievements",
                 "subtitle": "Badges for what's done with Buddy", "fields": fields}]

    def on_app_quitting(self):
        pass

    def on_setting(self, key, value, ui):
        if key == "notify":
            self.settings["notify"] = bool(value)
            self.settings.save()

    def on_settings_action(self, action, ui):
        if action == "reset" and ui.confirm("Reset achievements?", "Every achievement and every count starts over. "
                                            "This can't be undone.", ok="Reset", danger=True):
            self._fresh, self._fresh_at = set(), 0.0
            self.ach.reset()
            ui.status("Achievements reset.")
