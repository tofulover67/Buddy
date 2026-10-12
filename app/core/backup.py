#!/usr/bin/env python3
"""
Backing Buddy up and putting a backup back: one .zip of Buddy's settings
and what's been made in it - the shell's settings, every tool's own folder
(~/.<tool id>), Ask Buddy's chats and instructions, transcripts and
subtitles, palettes, time entries, Web's bookmarks and history.

Left out, on purpose:
  - what's downloaded again: models, AI engines, the manual, filter lists
  - web sign-ins and browsing data (cookies, cache) - anyone holding the
    file could use those sessions - and Buddy Network's identity and keys
    (Account > Move to another PC moves them, password-locked)
  - caches, logs, this PC's models folders, half-written files
API keys stay as settings keep them (core/secrets_store.py): on Windows
locked to this Windows account, so on another PC they're typed in again;
on a Mac as typed (there's no DPAPI), so a backup is to be kept private.

No Qt. A restore can't overwrite files the running Buddy holds in memory
(it would save over them), so stage_restore() unpacks the backup into
~/.buddy/restore-pending and Buddy restarts; apply_pending(), first thing
at the next start (app/main.py), backs up what's there now
(~/.buddy/before-restore.zip) and copies the backup over it. Files the
backup doesn't have are left alone.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Callable

from core import tool_dirs

MANIFEST = "buddy-backup.json"
FORMAT = 1
PREFIX = "Buddy backup "            # automatic backups' names: "Buddy backup 2026-10-08 2105.zip"
PENDING = "restore-pending"
BEFORE = "before-restore.zip"

# In ~/.buddy (paths relative to it): downloaded again, this PC's, or private.
SKIP_IN_BUDDY = {
    "web", "web-profiles",                          # browsing data and sign-ins
    "web-filters", "web-dictionaries", "manual",    # downloaded again
    "transcribe/venv", "transcribe/models", "transcribe/renders", "transcribe/verified.json",
    "generative/venv", "generative/models", "generative/gpu", "generative/work",
    "ask_buddy/llama.cpp", "ask_buddy/models",
    "audio_assistant/peaks", "audio_assistant/renders", "audit/renders",
    "media_search",                                 # the search index: made again from the files
    "model_folders.json",                           # drives this PC has
    "crash.log", "crash_trail.log", PENDING, BEFORE,
    # In the tools' folders (core/tool_dirs.py): an account's keys, and backups of their own.
    "tools/buddy_network/identity.json", "tools/buddy_network/keys.json",
    "tools/davinci_time_tracker/auto_backups",
}
# The same, in the folders tools had in home before 1.15 (still read, for
# one that couldn't be moved - core/tool_dirs.py).
SKIP_IN_TOOLS = {
    ".buddy_network/identity.json", ".buddy_network/keys.json",
    ".davinci_time_tracker/auto_backups",
}
# The standalone names older tools kept for their data in home (before 1.15).
OWN_NAMES = [".color_palette_manager", ".davinci_time_tracker", ".resolve_bin_generator",
             ".figma_fusion_importer_settings.json", ".buddy_web"]
_JUNK = re.compile(r"(\.bak|\.tmp|\.corrupt-\d+|\.downloading|\.buddy-moving|-journal|\.log\.\d+|\.part)$", re.I)

Progress = Callable[[str, int], None]        # (text, percent)

# Automatic backups (core/backup_runner.py, Settings > General > Backup).
EVERY = {"daily": 1, "weekly": 7}
KEEP_CHOICES = [5, 10, 20, 30]


def default_folder() -> Path:
    # home(), not the real Documents: under BUDDY_HOME a test run's
    # automatic backups stay out of the person's own folder.
    return home() / "Documents" / "Buddy backups"


class BackupError(Exception):
    """Something the person should read: not a backup, a file that won't write."""


def tool_ids(registry) -> list[str]:
    """Every tool's id, from registry.py's [(category, page class)]."""
    return [getattr(cls, "tool_id", "") for _category, cls in registry]


def home() -> Path:
    return Path(tool_dirs.home())


def buddy_dir() -> Path:
    return home() / ".buddy"


def _skipped(rel: str) -> bool:
    """rel: a path from home, with forward slashes."""
    name = rel.rsplit("/", 1)[-1]
    if _JUNK.search(name) or name.startswith(".buddy-"):
        return True
    if rel.startswith(".buddy/"):
        inner = rel[len(".buddy/"):]
        return any(inner == s or inner.startswith(s + "/") for s in SKIP_IN_BUDDY)
    return any(rel == s or rel.startswith(s + ".") or rel.startswith(s + "/") for s in SKIP_IN_TOOLS)


def allowed(tool_ids) -> list[str]:
    """The top-level names in home that are Buddy's: ~/.buddy (the tools'
    folders are in it now), and - for one not moved there yet - each tool's
    ~/.<tool id> and the standalone names: all a backup reads. A restore
    writes only into ~/.buddy (relocated)."""
    names = [".buddy", *(f".{t}" for t in tool_ids if t and re.fullmatch(r"[a-z0-9_]+", t)), *OWN_NAMES]
    return list(dict.fromkeys(names))


def roots(tool_ids) -> list[str]:
    """allowed() - those that exist."""
    return [n for n in allowed(tool_ids) if (home() / n).exists()]


def files(tool_ids, leave_out: Path | None = None) -> list[tuple[Path, str]]:
    """(file, its name in the backup) for everything a backup holds.
    leave_out: a folder never taken from (where backups are written, if
    it's somewhere a backup reads)."""
    out = []
    skip = os.path.normcase(os.path.abspath(leave_out)) if leave_out else None
    for name in roots(tool_ids):
        top = home() / name
        if top.is_file():
            if not _skipped(name):
                out.append((top, name))
            continue
        for folder, dirs, names in os.walk(top):
            rel_dir = Path(folder).relative_to(home()).as_posix()
            dirs[:] = [d for d in dirs if not _skipped(f"{rel_dir}/{d}")
                       and not (skip and os.path.normcase(os.path.abspath(os.path.join(folder, d))) == skip)]
            for n in names:
                rel = f"{rel_dir}/{n}"
                if not _skipped(rel):
                    out.append((Path(folder) / n, rel))
    return out


def make(dest: str | Path, tool_ids, version: str = "", progress: Progress | None = None) -> dict:
    """Writes the backup to dest (through dest.part, so a backup is only
    ever whole). Returns its manifest, with "bytes" (the zip's size)."""
    progress = progress or (lambda _t, _p: None)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    todo = files(tool_ids, leave_out=dest.parent)
    manifest = {"app": "Buddy", "format": FORMAT, "version": version, "made": time.strftime("%Y-%m-%d %H:%M"),
                "computer": platform.node(), "files": 0}
    part = dest.with_name(dest.name + ".part")
    total = sum(_size(p) for p, _ in todo) or 1
    done = 0
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6,
                             strict_timestamps=False) as z:    # a file dated before 1980 goes in as 1980
            for path, name in todo:
                try:
                    z.write(path, name)
                    manifest["files"] += 1
                except (OSError, ValueError):
                    continue            # gone, held open without sharing, or odd: the rest still go
                done += _size(path)
                progress(f"Backing up {name}", int(100 * done / total))
            z.writestr(MANIFEST, json.dumps(manifest, indent=1))
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    manifest["bytes"] = dest.stat().st_size
    progress("Done.", 100)
    return manifest


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ------------------------------------------------------------- automatic --


def auto_name(now: float | None = None) -> str:
    return PREFIX + time.strftime("%Y-%m-%d %H%M", time.localtime(now)) + ".zip"


def auto_backups(folder: str | Path) -> list[Path]:
    """The automatic backups in folder, oldest first (their names sort by date)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    pattern = re.compile(re.escape(PREFIX) + r"\d{4}-\d{2}-\d{2} \d{4}( \(\d+\))?\.zip$")
    return sorted(p for p in folder.iterdir() if p.is_file() and pattern.match(p.name))


def prune(folder: str | Path, keep: int) -> list[Path]:
    """Deletes the oldest automatic backups past `keep` - only ever files
    named as Buddy names them. Returns what went."""
    gone = []
    for old in auto_backups(folder)[:-keep] if keep > 0 else []:
        try:
            old.unlink()
            gone.append(old)
        except OSError:
            pass
    return gone


def due(last: str | None, every_days: int, now: float | None = None) -> bool:
    """Whether an automatic backup is due: last ("YYYY-MM-DD HH:MM", or
    None for never) is every_days or more ago."""
    if not last:
        return True
    try:
        then = time.mktime(time.strptime(last, "%Y-%m-%d %H:%M"))
    except (ValueError, OverflowError):
        return True
    return (now or time.time()) - then >= every_days * 86400 - 3600     # an hour's grace: same time each day


# --------------------------------------------------------------- restore --


def read_manifest(path: str | Path) -> dict:
    """The backup's manifest - BackupError if it isn't a Buddy backup."""
    try:
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise BackupError("That isn't a Buddy backup.") from exc
    if not isinstance(manifest, dict) or manifest.get("app") != "Buddy":
        raise BackupError("That isn't a Buddy backup.")
    if not isinstance(manifest.get("format"), int) or manifest["format"] > FORMAT:
        raise BackupError("That backup was made by a newer Buddy – update Buddy, then restore it.")
    return manifest


def _safe_name(name: str, tops) -> bool:
    """A name a restore may write: under home, in one of Buddy's own
    folders (tops: allowed()), nothing a backup leaves out."""
    if not name or name.startswith(("/", "\\")) or ":" in name or "\\" in name:
        return False
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts) or parts[0] not in tops:
        return False
    return not _skipped(name)


def stage_restore(path: str | Path, tool_ids) -> dict:
    """Unpacks the backup into ~/.buddy/restore-pending, for
    apply_pending() at the next start - only what goes in Buddy's own
    folders. Returns its manifest."""
    manifest = read_manifest(path)
    tops = set(allowed(tool_ids))
    pending = buddy_dir() / PENDING
    shutil.rmtree(pending, ignore_errors=True)
    staging = pending.with_name(PENDING + ".part")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                # An older backup's ~/.<tool> folders go where the tools keep
                # things now (core/tool_dirs.py).
                name = tool_dirs.relocated(info.filename, tops)
                if info.is_dir() or info.filename == MANIFEST or not _safe_name(name, tops):
                    continue
                target = staging / "home" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        (staging / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        os.replace(staging, pending)
    except (OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise BackupError(f"Couldn't unpack the backup: {exc}") from exc
    return manifest


def pending() -> dict | None:
    """The manifest of a restore waiting for the next start, or None."""
    try:
        return json.loads((buddy_dir() / PENDING / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cancel_pending() -> None:
    shutil.rmtree(buddy_dir() / PENDING, ignore_errors=True)


def apply_pending(tool_ids, version: str = "") -> dict | None:
    """At start, before anything reads its settings: a staged restore put
    in place, after what's there now is kept in ~/.buddy/before-restore.zip.
    Returns {"manifest", "files", "failed": [names]} - or None if there was
    none."""
    manifest = pending()
    if manifest is None:
        return None
    staged = buddy_dir() / PENDING / "home"
    names = [p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()] if staged.is_dir() else []
    tops = set(allowed(tool_ids))
    try:
        make(buddy_dir() / BEFORE, tool_ids, version)
    except Exception:  # noqa: BLE001 - the restore is what they asked for; it goes ahead
        pass
    failed = []
    for name in names:
        if not _safe_name(name, tops):
            continue
        target = home() / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged / name, target)
        except OSError:
            failed.append(name)
    cancel_pending()
    return {"manifest": manifest, "files": len(names) - len(failed), "failed": failed}
