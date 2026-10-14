#!/usr/bin/env python3
"""
The ONE Settings window for the whole shell: pages on a rail (General,
Look, AI, Tools, About - core/settings_form.py GROUPS). The shell's own
pages are settings_form.shell_pages(); every tool adds its own through
ToolPage.settings_pages() (pages/base.py) - under Tools, and under AI for
what it does with models - whichever tool is open. The AI group opens with
the Model library, every tool's ToolPage.ai_models() in one list and where
each tool's models are kept (core/model_folders.py - changed here, Buddy's
own models moved along on a thread), and ends with Privacy and safety,
their ToolPage.ai_jobs(). The header's ⓘ lists everything Buddy keeps on
this computer (core/buddy_folders.py).

A web window (app/web/shell/settings/): the fields are data, and every
change comes back here to be checked, saved and applied at once - there's
no Apply button. Search is the view's own, over the fields it was sent.

Protocol:
    to the view    settings {groups, pages, [open]}, status,
                   ask {title, text, buttons [{label, action, kind}]},
                   where {intro, groups, [sizes]}
    from the view  set {section, key, value}, action {section, action}
                   - section is "shell", or the tool id whose page it is
"""

import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

if sys.platform == "win32":
    from core import backup, buddy_folders, crash_log, link_bar, model_folders, nav_layout, shortcuts, startup_manager, tool_dirs
else:       # no crash trail (core/crash_log.py) off Windows
    from core import backup, buddy_folders, link_bar, model_folders, nav_layout, shortcuts, startup_manager, tool_dirs
from core import settings_form as sf
from core.i18n import get_i18n, tr
from core.message_dialog import alert, confirm
from core.web_page import WEB_COMMON_DIR, WebDialog

# The AI pages in order, whichever tool gives them; any other comes before Privacy.
AI_ORDER = ["ai_library", "ai_chat", "ai_search", "ai_transcription", "ai_translation"]
BUDDY_DIR = Path(tool_dirs.buddy_dir())     # ~/.buddy, or under BUDDY_HOME
REFRESH_MS = 250

_OPEN = []      # the Settings windows on screen (one, in practice)
_MOVING = {}    # models being moved: {"tool", "text", "pct"} - outlives the window that started it


class _Relay(QObject):
    """Runs a function on the UI thread, handed over from another one."""
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run)

    @Slot(object)
    def _run(self, fn):
        fn()


_RELAY = None


def _relay():
    global _RELAY
    if _RELAY is None:              # made on the UI thread, the first time it's needed
        _RELAY = _Relay()
    return _RELAY


def refresh_open(owner=None):
    """Something a tool shows in Settings changed by itself - a download's
    progress, a check finishing: an open Settings window redraws, at most
    a few times a second. owner: that tool's id (None: everything)."""
    for dialog in list(_OPEN):
        try:
            dialog.refresh_soon(owner)
        except RuntimeError:            # already destroyed
            _OPEN.remove(dialog)


class SettingsUI:
    """What a tool's settings code can do from inside the window (the `ui`
    argument of on_setting / on_settings_action)."""

    def __init__(self, dialog):
        self._dialog = dialog

    @property
    def parent(self):
        """The window, for a file picker to sit over."""
        return self._dialog

    def refresh(self):
        self._dialog.push()

    def alert(self, title, text):
        alert(self._dialog, title, text)

    def confirm(self, title, text, ok="OK", danger=False):
        return confirm(self._dialog, title, text, ok, danger=danger)

    def status(self, text, tone=""):
        """A line at the foot of the window - "Saved.", "Exported..."."""
        self._dialog.emit("status", {"text": text, "tone": tone})

    def close(self):
        self._dialog.reject()


def open_in_files(path):
    """A folder opened in Explorer / Finder; a file shown in its folder."""
    path = str(path)
    if os.path.isfile(path) and os.name == "nt":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif os.path.isfile(path) and sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path if os.path.isdir(path) else os.path.dirname(path)))


def size_text(size):
    if size >= 1e9:
        return f"{size / 1e9:.1f} GB"
    if size >= 1e6:
        return f"{size / 1e6:.0f} MB"
    return f"{max(1, round(size / 1e3))} KB" if size else "Empty"


def gigabytes(size):
    return f"{size / 1e9:.1f} GB" if size >= 1e8 else f"{max(1, round(size / 1e6))} MB"


def tool_pages(pages, tool_id):
    """A tool's own pages among Settings' pages, its Tools page first."""
    own = [p for p in pages if p.get("owner") == tool_id]
    return sorted(own, key=lambda p: p.get("group") != "tools")


class SettingsDialog(WebDialog):
    web_dir = os.path.join(WEB_COMMON_DIR, "shell", "settings")

    def __init__(self, parent, shared_settings, on_apply, active_page=None, open_tool=None):
        """open_tool: a tool id to open on that tool's own page (its
        right-click Settings) instead of General."""
        self.shared_settings = shared_settings
        self.on_apply = on_apply
        self.main_window = parent
        self.active_page = active_page
        self.open_tool = open_tool
        self.ui = SettingsUI(self)
        self._built = {}            # tool id -> {"pages", "models", "jobs"}
        self._pending = None        # (tool, folder, models to move) while the view asks what to do with them
        self._places = {}           # the ⓘ list's place ids -> paths
        self._opened = False
        self._dirty = set()
        super().__init__(parent, parent, "Settings", (940, 680))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh_now)
        _OPEN.append(self)

    def done(self, result):
        if self in _OPEN:
            _OPEN.remove(self)
        self._timer.stop()
        super().done(result)

    @staticmethod
    def on_screen() -> list:
        """The Settings windows open right now (one, in practice) - counting
        one still loading its page before it shows. Settings is shown with
        show(), not exec(): a window of its own, so the rest of Buddy - the
        pet - keeps taking the mouse while it's open."""
        out = []
        for dialog in list(_OPEN):
            try:
                dialog.isVisible()
            except RuntimeError:        # already destroyed
                _OPEN.remove(dialog)
                continue
            out.append(dialog)
        return out

    def web_ready(self):
        self.push()

    # ------------------------------------------------------------ tools --

    def tools(self):
        """Every tool page, in the rail's order - and the one on screen."""
        pages = getattr(self.main_window, "pages", None)
        tools = list(pages.values()) if isinstance(pages, dict) else []
        if self.active_page is not None and self.active_page not in tools:
            tools.append(self.active_page)
        # The shell's extras - the pet, the achievements (pages/pets/,
        # pages/achievements/): pages here and nowhere else.
        tools.extend(getattr(self.main_window, "extras", None) or [])
        return tools

    def tool(self, section):
        if section == "tool":            # the tool on screen (how it used to be addressed)
            return self.active_page
        return next((t for t in self.tools() if getattr(t, "tool_id", None) == section), None)

    def _build(self, tool):
        """One tool's pages, models and jobs - nothing, if they fail to
        build (logged): one tool's bug never takes Settings with it."""
        owner = getattr(tool, "tool_id", "tool")
        name = getattr(tool, "display_name", owner)
        built = {"pages": [], "models": [], "jobs": []}
        try:
            for p in getattr(tool, "settings_pages", lambda: [])() or []:
                group = p.get("group") if p.get("group") in sf.GROUP_IDS else "tools"
                fields = [f for f in p.get("fields") or [] if f]
                # The page is titled with the tool's name already.
                if fields and fields[0].get("kind") == "heading" and fields[0].get("text") == p.get("title", name):
                    fields = fields[1:]
                built["pages"].append({"id": p.get("id") or owner, "group": group, "title": p.get("title") or name,
                                       "subtitle": p.get("subtitle") or "", "owner": owner, "fields": fields})
            built["models"] = [dict(r, owner=r.get("owner") or owner)
                               for r in getattr(tool, "ai_models", lambda: [])() or []]
            built["jobs"] = [dict(j, owner=owner) for j in getattr(tool, "ai_jobs", lambda: [])() or []]
        except Exception:  # noqa: BLE001 - shown as nothing, logged
            traceback.print_exc()
            if sys.platform == "win32":
                crash_log.trail("settings", f"{owner}: {traceback.format_exc(limit=1).strip()[-300:]}")
        return built

    # ------------------------------------------------------------ pages --

    def _autostart(self):
        if not startup_manager.IS_WINDOWS:
            return False
        try:
            return startup_manager.is_enabled()
        except OSError:
            return None

    def _start_tools(self):
        """(id, name) of the tools Buddy can open on, in the sidebar's order."""
        shell = self.main_window
        pages = getattr(shell, "pages", {}) or {}
        layout = getattr(shell, "nav_layout", None)
        ids = nav_layout.visible_tool_ids(layout) if layout else list(pages)
        return [(t, pages[t].display_name) for t in ids if t in pages]

    def _shortcuts(self):
        if not shortcuts.available:
            return None
        try:
            return {place: shortcuts.exists(place) for place in shortcuts.PLACES}
        except OSError:
            return None

    def _shell_pages(self):
        pages = sf.shell_pages(self.shared_settings, self._autostart(), startup_manager.IS_WINDOWS,
                               getattr(self.main_window, "updates", None), self._start_tools(), self._shortcuts())
        runner = getattr(self.main_window, "backups", None)
        if runner is not None:
            at = next((i for i, p in enumerate(pages) if p["id"] == "window"), 0) + 1
            pages.insert(at, sf.backup_page(self.shared_settings, {
                "running": runner.running, "error": runner.error, "pending": backup.pending(),
                "folder": str(runner.folder())}))
        return pages

    def pages(self, owners=None):
        """Every page, in the rail's order. owners: rebuild only these
        tools' (the rest as last built)."""
        tools = self.tools()
        for t in tools:
            owner = getattr(t, "tool_id", "tool")
            if owners is None or owner in owners or owner not in self._built:
                self._built[owner] = self._build(t)
        built = [self._built[getattr(t, "tool_id", "tool")] for t in tools]
        shell = [dict(p, owner="shell") for p in self._shell_pages()]
        tool_pages = [p for b in built for p in b["pages"]]
        models = [r for b in built for r in b["models"]]
        jobs = [j for b in built for j in b["jobs"]]
        ai = [p for p in tool_pages if p["group"] == "ai"]
        ai.sort(key=lambda p: AI_ORDER.index(p["id"]) if p["id"] in AI_ORDER else len(AI_ORDER))
        if models or ai:
            ai = [self._library(models), *ai, self._privacy(jobs)]
        out = []
        for group in sf.GROUP_IDS:
            out += [p for p in shell if p["group"] == group]
            out += ai if group == "ai" else [p for p in tool_pages if p["group"] == group]
        seen = set()
        for p in out:                    # ids are what the view goes by: never two alike
            if p["id"] in seen:
                p["id"] = f"{p['owner']}:{p['id']}"
            seen.add(p["id"])
        return out

    def _library(self, rows):
        """Every model Buddy uses, the space they take, and getting them."""
        kinds = {}
        for r in rows:
            if r.get("where") == "local" and r.get("bytes"):
                kinds[r.get("kind", "")] = kinds.get(r.get("kind", ""), 0) + r["bytes"]
        total = sum(kinds.values())
        fields = []
        if total:
            fields.append(sf.storage(f"{gigabytes(total)} on this PC",
                                     [{"label": kind, "size": gigabytes(size), "bytes": size, "tone": kind.lower()}
                                      for kind, size in kinds.items()], str(BUDDY_DIR)))
        # What's here (or in use in the cloud) first, what can be fetched after.
        rows = sorted(rows, key=lambda r: r.get("where") == "missing")
        fields += [
            sf.models(rows, filters=True) if rows else sf.hint("No tool here uses a model."),
            sf.buttons(("Open Buddy's folder", "open_buddy_folder")),
            *self._folder_fields(),
        ]
        return sf.page("ai_library", "ai", "Model library", fields,
                       "Every model Buddy can use, on this PC or in the cloud, and what uses it.") | {"owner": "shell"}

    def _folder_fields(self):
        """Where each tool's models go - Buddy's folder, or one picked."""
        moving = dict(_MOVING)
        rows = []
        for tool, t in model_folders.TOOLS.items():
            custom = model_folders.is_custom(tool)
            busy = bool(moving)
            rows.append({
                "id": f"models-folder-{tool}", "label": t["label"], "sub": [t["what"]],
                "note": str(model_folders.folder(tool)), "where": "local", "owner": "shell",
                "chip": {"text": "Your folder", "tone": "ok", "tip": "Picked here, instead of Buddy's own."} if custom
                else {"text": "Buddy's folder", "tone": ""},
                "actions": [{"label": "Change…", "action": f"models_folder:{tool}", "disabled": busy},
                            {"label": "Open", "action": f"models_open:{tool}"},
                            *([{"label": "Use Buddy's folder", "action": f"models_default:{tool}", "disabled": busy,
                                "tip": str(t["default"])}] if custom else [])],
            })
        fields = [sf.heading("Where models are kept"),
                  sf.hint("Pick a folder to keep a tool's models somewhere else – a bigger drive, or a folder other "
                          "apps share, so one copy serves them all. Buddy offers to move the models it downloaded."),
                  sf.models(rows)]
        if moving:
            fields.append(sf.progress(moving.get("text") or "Moving…", moving.get("pct")))
        return fields

    # ------------------------------------------------- models folders --

    def _change_models_folder(self, tool, default=False):
        if _MOVING:
            return self.ui.status("Models are still moving – wait for them to finish.", "")
        page = self.tool(tool)
        busy = page.models_busy() if page is not None and hasattr(page, "models_busy") else ""
        if busy:
            return self.ui.status(busy, "")
        label = model_folders.TOOLS[tool]["label"]
        if default:
            folder = str(model_folders.TOOLS[tool]["default"])
        else:
            folder = QFileDialog.getExistingDirectory(self, f"Where should {label}'s models be kept?",
                                                      str(model_folders.folder(tool)))
            if not folder:
                return
        problem = model_folders.check_folder(tool, folder)
        if problem:
            return self.alert_soon("Models folder", problem)
        items = model_folders.own_models(tool)
        if not items:
            return self._finish_models_folder(tool, folder, [])
        self._pending = (tool, folder, items)
        size = size_text(sum(model_folders.size(p) for p in items))
        self.emit("ask", {
            "title": "Move the models too?",
            "text": f"{label} has {size} of models in {model_folders.folder(tool)}.\n\n"
                    f"Move them to {os.path.normpath(folder)}? If you don't, they stay where they are, unused – "
                    f"{label} offers to download them again when it needs them.",
            "buttons": [{"label": "Cancel", "action": ""},
                        {"label": "Don't move", "action": "models_move:no"},
                        {"label": f"Move {size}", "action": "models_move:yes", "kind": "accent"}],
        })

    def alert_soon(self, title, text):
        """An alert once the click that asked for it is done with."""
        QTimer.singleShot(0, lambda: alert(self, title, text))

    def _answer_move(self, yes):
        pending, self._pending = self._pending, None
        if pending is None or _MOVING:
            return
        tool, folder, items = pending
        page = self.tool(tool)
        busy = page.models_busy() if page is not None and hasattr(page, "models_busy") else ""
        if busy:                        # it started something while they read the question
            return self.ui.status(busy, "")
        self._finish_models_folder(tool, folder, items if yes else [])

    def _finish_models_folder(self, tool, folder, items):
        """Moves items (Buddy's models, maybe none) to folder on a thread,
        then keeps folder as the tool's models folder."""
        page = self.tool(tool)
        if page is not None and hasattr(page, "models_moving"):
            page.models_moving()
        label = model_folders.TOOLS[tool]["label"]
        _MOVING.update(tool=tool, text=f"Moving {label}'s models …", pct=0 if items else None)
        relay = _relay()
        shown = [0.0]

        def progress(text, pct):
            _MOVING.update(text=text, pct=pct)
            now = time.monotonic()
            if now - shown[0] > 0.25:
                shown[0] = now
                relay.call.emit(lambda: refresh_open("shell"))

        def work():
            try:
                problems = model_folders.move(items, Path(folder), progress) if items else []
            except Exception as exc:  # noqa: BLE001 - reported, and the folder stays as it was
                failed = [f"Couldn't move the models: {exc}"]
                relay.call.emit(lambda: _moved(tool, None, failed, page))
                return
            relay.call.emit(lambda: _moved(tool, folder, problems, page))

        threading.Thread(target=work, name="buddy-move-models", daemon=True).start()
        self.push()

    # ------------------------------------------------------------- backup --

    def _backup_action(self, action):
        runner = getattr(self.main_window, "backups", None)
        if runner is None:
            return
        if action == "backup_now":
            start = str(runner.folder() if runner.folder().is_dir() else Path.home() / "Documents")
            path, _ = QFileDialog.getSaveFileName(self, "Back up Buddy",
                                                  os.path.join(start, backup.auto_name()), "Buddy backup (*.zip)")
            if not path:
                return
            if not path.lower().endswith(".zip"):
                path += ".zip"

            def done(result, error):
                for dialog in list(_OPEN):
                    if error:
                        dialog.alert_soon("Back up", error)
                    else:
                        dialog.ui.status(tr("Backed up {files} files to {path} ({size}).").format(
                            files=result["files"], path=result["path"], size=size_text(result["bytes"])), "success")
                    break

            if not runner.back_up(Path(path), done):
                self.ui.status("A backup is being made already – wait for it to finish.", "")
        elif action == "backup_restore":
            start = str(runner.folder() if runner.folder().is_dir() else Path.home())
            path, _ = QFileDialog.getOpenFileName(self, "Restore from a backup", start, "Buddy backup (*.zip)")
            if path and hasattr(self.main_window, "restore_backup"):
                self.main_window.restore_backup(path, self)
        elif action == "backup_restart" and hasattr(self.main_window, "restart_buddy"):
            self.main_window.restart_buddy(self)
        elif action == "backup_cancel_restore":
            backup.cancel_pending()
            self.ui.status("The backup won't be restored.", "")
        elif action == "backup_browse":
            folder = QFileDialog.getExistingDirectory(self, "Where should automatic backups go?", str(runner.folder()))
            if folder:
                self._set_shell("auto_backup_folder", folder)
        elif action == "backup_open_folder":
            folder = runner.folder()
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return self.ui.status(f"Couldn't open {folder}: {exc.strerror or exc}", "danger")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # --------------------------------------------- where Buddy keeps things --

    def _show_where(self):
        tools = [(getattr(t, "tool_id", ""), getattr(t, "display_name", "")) for t in self.tools()]
        runner = getattr(self.main_window, "backups", None)
        groups = buddy_folders.places([t for t in tools if t[0]],
                                      runner.folder() if runner is not None and runner.enabled() else None)
        self._places = {row["id"]: row["path"] for g in groups for row in g["rows"]}
        data = {"intro": "Everything Buddy keeps on this computer. Removing Buddy leaves these behind – delete "
                         "them by hand to clear it all away.", "groups": groups}
        self.emit("where", data)
        relay = _relay()

        def measure():
            sizes = {path: size_text(n) for path, n in buddy_folders.sizes(groups).items()}
            relay.call.emit(lambda: self._where_sized(dict(data, sizes=sizes)))

        threading.Thread(target=measure, name="buddy-folder-sizes", daemon=True).start()

    def _where_sized(self, data):
        if self in _OPEN:               # still open
            self.emit("where", data)

    def _privacy(self, jobs):
        """Where each AI job's data goes, and how what Buddy downloads is checked."""
        rows = []
        for j in jobs:
            where = j.get("where")
            text = j.get("text") or {"local": "On this PC", "cloud": "Leaves this PC", "off": "Off"}.get(where, "")
            tone = j.get("tone", "ok" if where == "local" else "warn" if where == "cloud" else "")
            row = sf.status(j.get("label", ""), text, tone, j.get("detail"))
            row["page"] = j.get("page")
            rows.append(row)
        return sf.page("ai_privacy", "ai", "Privacy and safety", [
            sf.heading("Where your data goes"),
            *(rows or [sf.hint("No tool here uses AI.")]),
            sf.heading("How downloads are checked"),
            sf.hint("Every model Buddy downloads is pinned to one exact version, and each of its files is checked "
                    "against the SHA-256 checksum Buddy expects before it's used. Model files hold no code."),
            sf.hint("The transcription engine installs only exact package versions, each checked against its "
                    "published hash, and Windows Defender scans it before it's used. llama.cpp, which runs "
                    "the search model, is checked against its published checksum and scanned the same way."
                    if sys.platform == "win32" else
                    "The transcription engine installs only exact package versions, each checked against its "
                    "published hash. llama.cpp, which runs the search model, is checked against its published "
                    "checksum."),
            sf.hint("A copy from somewhere else – a folder you picked, another app's download – shows as Not "
                    "verified when its files aren't exactly the expected ones."),
        ], "What leaves this computer, and what Buddy checks.") | {"owner": "shell"}

    def _first_page(self, pages):
        """Where Settings opens: the tool it was opened for (its Tools page
        first), else General > General, whatever tool is on screen (or,
        should a Buddy ever lack it, the first page)."""
        own = tool_pages(pages, self.open_tool) if self.open_tool else []
        if own:
            return own[0]["id"]
        return "general" if any(p["id"] == "general" for p in pages) else (pages[0]["id"] if pages else "general")

    def push(self, owners=None):
        pages = self.pages(owners)
        data = {"groups": sf.GROUPS, "pages": pages}
        if not self._opened:
            self._opened = True
            data["open"] = self._first_page(pages)
        self.emit("settings", data)

    def open_page(self, tool_id=None):
        """Settings is already on screen and asked for again - for a tool's
        own page, or just Settings: goes there (or stays put) and comes
        to the front."""
        if tool_id:
            self.open_tool = tool_id
            pages = self.pages()
            self.emit("settings", {"groups": sf.GROUPS, "pages": pages, "open": self._first_page(pages), "goto": True})
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh_soon(self, owner=None):
        self._dirty.add(owner)
        if not self._timer.isActive():
            self._timer.start()

    def _refresh_now(self):
        dirty, self._dirty = self._dirty, set()
        self.push(None if None in dirty else dirty)

    # ----------------------------------------------------------- actions --

    def on_set(self, payload):
        payload = payload or {}
        key, value = str(payload.get("key") or ""), payload.get("value")
        section = payload.get("section") or "shell"
        if section in ("shell", "language"):
            self._set_shell(key, value)
        else:
            tool = self.tool(section)
            if tool is not None:
                tool.on_setting(key, value, self.ui)
        self.push()

    def _set_shell(self, key, value):
        effect = sf.apply_shell(self.shared_settings, key, value)
        if effect is None:
            return
        if effect == "announcements":
            shell = self.main_window
            if hasattr(shell, "set_announcements_enabled"):
                shell.set_announcements_enabled(bool(value))   # also stops/starts checking
            else:
                self.shared_settings["announcements_enabled"] = bool(value)
                self.shared_settings.save()
            return
        if effect == "updates":
            shell = self.main_window
            if hasattr(shell, "set_updates_enabled"):
                shell.set_updates_enabled(bool(value))         # also stops/starts checking
            else:
                self.shared_settings["updates_enabled"] = bool(value)
                self.shared_settings.save()
            return
        if effect == "linkbar":
            shell = self.main_window
            if hasattr(shell, "set_link_bar_visible"):
                shell.set_link_bar_visible(bool(value))        # also makes the bar, the first time
            else:
                self.shared_settings[link_bar.SHOW_KEY] = bool(value)
                self.shared_settings.save()
            return
        if effect == "later":
            self.shared_settings.save()                   # takes effect when Buddy next starts
            return
        if effect == "linkbar_style":
            self.shared_settings.save()
            bar = getattr(self.main_window, "link_bar", None)
            if bar is not None:
                bar.on_theme_changed()                         # its colours come with the theme
            return
        if effect == "language":
            self.shared_settings.save()
            # Every open view (this window too) gets the new language's
            # strings and redraws in it - see core/web_page.py.
            get_i18n().language = value
            return
        if effect == "shortcut":
            # A real side effect straight away: the shortcut made or deleted.
            place = sf.SHORTCUT_KEYS[key]
            try:
                shortcuts.make(place) if value else shortcuts.remove(place)
            except OSError as exc:
                alert(self, "Shortcut", (f"Couldn't make the shortcut: {exc.strerror or exc}" if value
                                         else f"Couldn't remove the shortcut: {exc.strerror or exc}"))
            return
        if effect == "backup":
            self.shared_settings.save()
            runner = getattr(self.main_window, "backups", None)
            if runner is not None and key == "auto_backup" and value:
                QTimer.singleShot(1000, runner.check)      # the first one now, not in half an hour
            return
        if effect == "autostart":
            if not startup_manager.IS_WINDOWS:
                return
            # A real side effect straight away (a registry write).
            try:
                startup_manager.set_enabled(bool(value))
            except Exception as exc:  # noqa: BLE001 - reported to the user
                alert(self, "Startup setting failed",
                      f"Could not enable launching at startup:\n{exc}" if value
                      else f"Could not disable launching at startup:\n{exc}")
            return
        self.shared_settings.save()
        if effect in ("theme", "window"):
            self.on_apply()
            if effect == "theme":
                self.on_theme_changed()

    def on_action(self, payload):
        payload = payload or {}
        action = str(payload.get("action") or "")
        section = payload.get("section") or "shell"
        if section not in ("shell", "language"):
            tool = self.tool(section)
            if tool is not None:
                tool.on_settings_action(action, self.ui)
        elif action == "close":
            self.accept()
            return
        elif action == "organize" and hasattr(self.main_window, "open_nav_organizer"):
            self.main_window.open_nav_organizer(self)
        elif action == "check_updates" and hasattr(self.main_window, "check_updates_now"):
            self.main_window.check_updates_now(self)
        elif action == "roll_back_update" and hasattr(self.main_window, "roll_back_update"):
            self.main_window.roll_back_update(self)
        elif action == "open_buddy_folder":
            BUDDY_DIR.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(BUDDY_DIR)))
        elif action == "where":
            self._show_where()
            return
        elif action.startswith("backup_"):
            self._backup_action(action)
        elif action.startswith("open_place:"):
            path = self._places.get(action.split(":", 1)[1])
            if path and os.path.exists(path):
                open_in_files(path)
            elif path:
                self.ui.status("It isn't there any more.", "")
            return
        elif action.startswith(("models_folder:", "models_default:")):
            name, tool = action.split(":", 1)
            if tool in model_folders.TOOLS:
                self._change_models_folder(tool, default=name == "models_default")
        elif action.startswith("models_move:"):
            self._answer_move(action.endswith(":yes"))
        elif action.startswith("models_open:"):
            tool = action.split(":", 1)[1]
            if tool in model_folders.TOOLS:
                folder = model_folders.folder(tool)
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    return self.ui.status(f"Couldn't open {folder}: {exc.strerror or exc}", "danger")
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        elif action == "reset_theme":
            sf.reset_theme(self.shared_settings)
            self.shared_settings.save()
            self.on_apply()
            self.on_theme_changed()
        self.push()


def _moved(tool, folder, problems, page):
    """A move finished (on the UI thread): the new folder kept, the tool told,
    and what didn't go as planned shown."""
    _MOVING.clear()
    label = model_folders.TOOLS[tool]["label"]
    if folder is not None:
        try:
            model_folders.set_folder(tool, folder)
        except OSError as exc:
            problems = [*problems, f"Couldn't save the new folder: {exc.strerror or exc}"]
    try:
        if page is not None and hasattr(page, "models_moved"):
            page.models_moved()
    except RuntimeError:            # the page was closed meanwhile
        pass
    refresh_open()
    for dialog in list(_OPEN):
        if problems:
            dialog.alert_soon("Models folder", "\n\n".join(problems))
        else:
            dialog.ui.status(f"{label}'s models are kept in {model_folders.folder(tool)} now.", "success")
        break
