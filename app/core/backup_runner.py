#!/usr/bin/env python3
"""
Backups while Buddy runs (core/backup.py does the work): Settings' "Back up
now…", and the automatic one - when it's on, a backup into the folder picked
once a day or once a week, the oldest past the number kept deleted. Checked a
couple of minutes after Buddy starts and every half hour after, so a Buddy
left running still backs up; each one is written on a thread.

Settings (the shared settings, saved by the shell's Settings window):
    auto_backup           on / off (off until it's turned on)
    auto_backup_every     "daily" or "weekly"
    auto_backup_folder    where they go ("" - Documents/Buddy backups)
    auto_backup_keep      how many automatic backups are kept
    last_auto_backup      "YYYY-MM-DD HH:MM" of the last one that worked
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

if sys.platform == "darwin":
    from core import backup
else:
    from core import backup, crash_log   # the crash trail is kept on Windows only
from core.app_version import buddy_version

from core.backup import EVERY, KEEP_CHOICES, default_folder

FIRST_CHECK_MS = 2 * 60 * 1000
CHECK_MS = 30 * 60 * 1000


class BackupRunner(QObject):
    changed = Signal()              # one started, moved on or finished: Settings redraws
    _finished = Signal(object)      # from the thread: (result, error, done)
    _progressed = Signal(str, int)

    def __init__(self, shared, tool_ids, parent=None):
        super().__init__(parent)
        self.shared = shared
        self.tool_ids = list(tool_ids)
        self.running = None         # {"text", "pct", "auto"} while one is written
        self.error = ""             # why the last automatic one failed ("" - it didn't)
        self._finished.connect(self._done)
        self._progressed.connect(self._progress)
        self._timer = QTimer(self, interval=CHECK_MS, timeout=self.check)

    def start(self):
        self._timer.start()
        QTimer.singleShot(FIRST_CHECK_MS, self.check)

    # ------------------------------------------------------------ settings --

    def enabled(self) -> bool:
        return bool(self.shared.get("auto_backup", False))

    def every(self) -> str:
        every = self.shared.get("auto_backup_every", "daily")
        return every if every in EVERY else "daily"

    def folder(self) -> Path:
        picked = self.shared.get("auto_backup_folder") or ""
        return Path(picked) if picked else default_folder()

    def keep(self) -> int:
        keep = self.shared.get("auto_backup_keep", 10)
        return keep if keep in KEEP_CHOICES else 10

    def last(self) -> str:
        return self.shared.get("last_auto_backup") or ""

    # ------------------------------------------------------------- backing up --

    @Slot()
    def check(self):
        """An automatic backup, if one's on and due."""
        if self.enabled() and self.running is None and backup.due(self.last(), EVERY[self.every()]):
            self.back_up(None)

    def back_up(self, dest, done=None) -> bool:
        """Writes a backup on a thread: to dest, or (None) the next
        automatic one. done(result, error) after, on this thread. False if
        one is already being written."""
        if self.running is not None:
            return False
        auto = dest is None
        if auto:
            folder = self.folder()
            dest = folder / backup.auto_name()
            n = 2
            while dest.exists():
                dest = folder / backup.auto_name().replace(".zip", f" ({n}).zip")
                n += 1
        self.running = {"text": "Backing up…", "pct": 0, "auto": auto}
        self.changed.emit()
        shown = [0.0]

        def progress(text, pct):
            now = time.monotonic()
            if now - shown[0] > 0.25:
                shown[0] = now
                self._progressed.emit(text, pct)

        def work():
            try:
                result = backup.make(dest, self.tool_ids, buddy_version(), progress)
                result["path"] = str(dest)
                self._finished.emit((result, "", done, auto))
            except Exception as exc:  # noqa: BLE001 - shown in Settings
                self._finished.emit((None, f"Couldn't back up to {dest.parent}: {getattr(exc, 'strerror', None) or exc}",
                                     done, auto))

        threading.Thread(target=work, name="buddy-backup", daemon=True).start()
        return True

    @Slot(str, int)
    def _progress(self, text, pct):
        if self.running is not None:
            self.running.update(pct=pct)          # each file's name would only flicker past
            self.changed.emit()

    @Slot(object)
    def _done(self, outcome):
        result, error, done, auto = outcome
        self.running = None
        if auto:
            self.error = error
            if result is not None:
                self.shared["last_auto_backup"] = result["made"]
                self.shared["last_auto_backup_file"] = result["path"]
                self.shared.save()
                backup.prune(self.folder(), self.keep())
        if sys.platform != "darwin":
            crash_log.trail("backup", error or f"{'automatic' if auto else 'manual'} backup: {result['files']} files, "
                                               f"{result['bytes'] // 1024} KB")
        if done is not None:
            done(result, error)
        self.changed.emit()
