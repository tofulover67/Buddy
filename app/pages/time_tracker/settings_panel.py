#!/usr/bin/env python3
"""
Time Tracker's section of the shell's Settings window, as fields (see
core/settings_form.py) - tracking mode, polling, idle detection, the global
shortcut, goals, the current project's rate, and backup/export/import of
all data. The page provides self.data_mgr, self.engine and
_apply_settings(), which re-reads everything saved here.

The project rate is two fields here, for whichever project Resolve has
open. Graph colours: the theme's (its emphasis fill), or one picked here;
a project's own is picked on Reports (page.py on_set_project_color).
"""

import re
import sys

from PySide6.QtWidgets import QFileDialog

from core import settings_form as sf
from core.settings_dialog import refresh_open
from core.web_theme import web_theme
from core.i18n import tr, tr_filter

if sys.platform == "darwin":
    from . import mac_hotkey
from .currencies import CURRENCIES, CURRENCY_CODES, DEFAULT_CURRENCY

POLL_INTERVAL_OPTIONS = [2, 5, 10, 30]
IDLE_THRESHOLD_OPTIONS = [2, 5, 10, 15, 30]
TOGGLES = ("idle_detection_enabled", "manual_tracking", "global_hotkey_enabled", "auto_backup_enabled",
           "remember_export_folder")
GOALS = {"weekly_goal_hours": 999.0, "monthly_goal_hours": 9999.0}
FALLBACK_CHART_COLOR = "#9d6bff"
RATE_MAX = 1_000_000.0


def _closest(value, options):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return options[1]
    return min(options, key=lambda o: abs(o - value))


class TrackerSettingsMixin:
    def settings_fields(self):
        s = self.data_mgr.settings
        fields = [
            sf.heading("Time Tracker"),
            sf.select("poll_interval_seconds", "Check Resolve every",
                      _closest(s.get("poll_interval_seconds", 5), POLL_INTERVAL_OPTIONS),
                      [(n, f"{n} sec") for n in POLL_INTERVAL_OPTIONS]),
            sf.check("idle_detection_enabled", "Pause tracking when idle", s.get("idle_detection_enabled", True),
                     tooltip="Automatically stop the running entry (trimming off the idle time) after no "
                             "keyboard/mouse activity anywhere on this machine for the chosen duration. In manual "
                             "tracking mode you'll need to press Start again afterward; in auto-follow mode it "
                             "resumes on its own as soon as you're back."),
            sf.select("idle_threshold_minutes", "Idle threshold",
                      _closest(s.get("idle_threshold_minutes", 5), IDLE_THRESHOLD_OPTIONS),
                      [(n, f"{n} min") for n in IDLE_THRESHOLD_OPTIONS], indent=True),
            sf.check("manual_tracking", "Manual tracking", s.get("manual_tracking", False),
                     tooltip="Track time yourself with a Start/Pause/Stop button instead of auto-following "
                             "whatever project is open in DaVinci Resolve."),
        ]
        if sys.platform in ("win32", "darwin"):
            shortcut = mac_hotkey.LABEL if sys.platform == "darwin" else "Ctrl+Alt+P"
            fields.append(sf.check(
                "global_hotkey_enabled", f"Global pause/resume shortcut ({shortcut})",
                s.get("global_hotkey_enabled", False),
                tooltip="Works even while DaVinci Resolve (or any other app) has focus – cycles Start (if manual "
                        "tracking is idle) -> Pause -> Resume. Off by default since a system-wide shortcut could "
                        "conflict with another app already using the same combination."))
        weekly, monthly = s.get("weekly_goal_hours", 0.0), s.get("monthly_goal_hours", 0.0)
        fields += [
            sf.line(),
            sf.heading("Goals & billing"),
            sf.number("weekly_goal_hours", "Weekly goal", f"{weekly:g}" if weekly else None, 0, 999, 1,
                      placeholder="10", suffix="hours (0 to disable)"),
            sf.number("monthly_goal_hours", "Monthly goal", f"{monthly:g}" if monthly else None, 0, 9999, 1,
                      placeholder="40", suffix="hours (0 to disable)"),
            *self._rate_fields(),
            *self._color_fields(),
            sf.line(),
            sf.heading("Data management"),
            sf.check("auto_backup_enabled", "Automatic weekly backups", s.get("auto_backup_enabled", True),
                     tooltip="Silently saves a full backup (same format as Export all data) once a week to "
                             "~/.davinci_time_tracker/auto_backups – keeps the most recent 8, purely a safety net "
                             "in case entries.json is ever lost or corrupted."),
            sf.check("remember_export_folder", "Remember last export folder", s.get("remember_export_folder", False)),
            sf.hint("Moving to a new machine? Export or import your Time Tracker data."),
            sf.buttons(("Export all data…", "export_all"), ("Import data…", "import_all")),
        ]
        return fields

    def _color_fields(self):
        s = self.data_mgr.settings
        theme = s.get("chart_theme_color", True)
        chart = s.get("chart_color") or self._theme_chart_color()
        return [
            sf.line(),
            sf.heading("Graph colours"),
            sf.check("chart_theme_color", "Use the theme's colour", theme,
                     tooltip="Graphs, goal bars and the project total follow the colour of the theme picked in "
                             "Settings > Appearance."),
            {**sf.color("chart_color", "Graph colour", chart, enabled=not theme), "indent": True},
            sf.hint("A project can have a colour of its own: click the square beside it on Reports > By project."),
        ]

    def _theme_chart_color(self):
        """Where Graph colour starts when the theme's is turned off: the
        bars' colour in the theme now (its emphasis fill)."""
        try:
            shared = getattr(self.host, "shared_settings", None) or {}
            tokens = self.host.theme_tokens()
            variables = web_theme(shared.get("theme", "Default"), shared.get("subtheme"), tokens)["vars"]
            color = variables.get("emphasis-fill")
            if isinstance(color, str) and color.startswith("var(--primary"):
                color = tokens["primary"]
        except Exception:  # noqa: BLE001 - no shell (tests)
            return FALLBACK_CHART_COLOR
        return color.lower() if isinstance(color, str) and re.match(r"^#[0-9a-fA-F]{6}$", color) else FALLBACK_CHART_COLOR

    def _rate_fields(self):
        project = self.engine.detected_project_name
        if not project:
            return [sf.info("Project rate", "No project is currently detected in DaVinci Resolve. Open a project "
                                            "there, then come back here to set its rate.")]
        info = self.data_mgr.get_project_rate_info(project)
        rate = info.get("rate") or 0.0
        currency = info.get("currency") if info.get("currency") in CURRENCY_CODES else DEFAULT_CURRENCY
        return [
            sf.info("Project rate", project, raw=True),
            sf.number("project_rate", "Rate per hour", f"{rate:.2f}" if rate > 0 else None, 0, RATE_MAX, 2,
                      placeholder="0.00"),
            sf.select("project_currency", "Currency", currency, [(c, f"{c} ({sym})") for c, sym, _n in CURRENCIES]),
            sf.hint("Leave the rate at 0 to hide earnings for this project on Reports."),
        ]

    def on_setting(self, key, value, ui):
        s = self.data_mgr.settings
        if key == "poll_interval_seconds" and value in POLL_INTERVAL_OPTIONS:
            s[key] = value
        elif key == "idle_threshold_minutes" and value in IDLE_THRESHOLD_OPTIONS:
            s[key] = value
        elif key in TOGGLES:
            if key == "global_hotkey_enabled" and sys.platform not in ("win32", "darwin"):
                return
            s[key] = bool(value)
        elif key in GOALS:
            # Blank or not a number means off.
            s[key] = sf.parse_number(value, 0.0, GOALS[key]) or 0.0
        elif key == "chart_theme_color":
            s[key] = bool(value)
            if not value and not s.get("chart_color"):
                s["chart_color"] = self._theme_chart_color()
            self.data_mgr.save_settings()
            self._apply_settings()
            refresh_open(self.tool_id)          # Graph colour turns on or off
            return
        elif key == "chart_color":
            if not isinstance(value, str) or not re.match(r"^#[0-9a-fA-F]{6}$", value):
                return
            s[key] = value.lower()
        elif key in ("project_rate", "project_currency"):
            project = self.engine.detected_project_name
            if not project:
                return
            info = self.data_mgr.get_project_rate_info(project)
            rate, currency = info.get("rate") or 0.0, info.get("currency") or DEFAULT_CURRENCY
            if key == "project_rate":
                rate = sf.parse_number(value, 0.0, RATE_MAX) or 0.0
            elif value in CURRENCY_CODES:
                currency = value
            self.data_mgr.set_project_rate(project, rate, currency)
            self._apply_settings()   # earnings figures follow the rate
            return
        else:
            return
        self.data_mgr.save_settings()
        self._apply_settings()

    def on_settings_action(self, action, ui):
        if action == "export_all":
            self._export_all_data(ui)
        elif action == "import_all":
            self._import_all_data(ui)

    def _export_all_data(self, ui):
        default_path = self.data_mgr.default_export_path("time_tracker_backup.json")
        path, _ = QFileDialog.getSaveFileName(ui.parent, tr("Export all data"), default_path, tr_filter("JSON files (*.json)"))
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            self.data_mgr.export_backup(path)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            return ui.alert("Export failed", f"Could not export data:\n{exc}")
        self.data_mgr.remember_export_folder(path)
        ui.alert("Export complete",
                 f"Exported {len(self.data_mgr.entries)} entries and your settings to:\n{path}\n\n"
                 "Copy this file to the new machine and use Import data there.")

    def _import_all_data(self, ui):
        start_dir = self.data_mgr.get_export_start_dir()
        path, _ = QFileDialog.getOpenFileName(ui.parent, tr("Import data"), start_dir, tr_filter("JSON files (*.json)"))
        if not path:
            return
        if not ui.confirm("Import data",
                          f"Import time entries and settings from:\n{path}\n\n"
                          "Entries already present (matched by id) are left alone – nothing already here gets "
                          "duplicated or overwritten. Your tracking mode, poll interval, and other saved Time "
                          "Tracker settings will be replaced with the ones from this file. Continue?", "Import"):
            return
        try:
            added, skipped = self.data_mgr.import_backup(path)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            return ui.alert("Import failed", f"Could not import data:\n{exc}")
        self._apply_settings()
        message = "Imported 1 new entry." if added == 1 else f"Imported {added} new entries."
        if skipped:
            message += f" ({skipped} already present, skipped.)"
        ui.alert("Import complete", message)
