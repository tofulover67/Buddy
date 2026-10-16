#!/usr/bin/env python3
"""
Where each tool keeps its settings and data: ~/.buddy/tools/<name>/.

Until 1.15 every tool had a folder of its own in the home directory -
~/.command_center, ~/.games - and the ones that began life as standalone
scripts kept the names they had then: ~/.davinci_time_tracker (entries),
~/.color_palette_manager (palettes), ~/.resolve_bin_generator (Project
Setup), ~/.figma_fusion_importer_settings.json (SVG Importer, one file),
~/.buddy_web and ~/.animation_previews (settings buckets). That left a
dozen hidden folders beside ~/.buddy. They all live in ~/.buddy/tools now,
each under the name it had (so nothing about what's inside changes), and
the first start of a Buddy that knows this moves each one there
(migrate_all, from main.py before anything reads its settings; ToolSettings
moves its own bucket too, for a tool main.py didn't know).

A folder that can't be moved - held open, on another drive - stays where
it is and is read from there: path() answers the older place for as long
as the new one doesn't exist. Backups (core/backup.py) read both places
and put an older backup's folders in the new one (relocated).

home() is where everything Buddy keeps is found - ~/.buddy, the tools'
older places, Documents/Buddy backups - and every module that builds a
path to Buddy's own data asks it (or buddy_dir()). BUDDY_HOME=<folder>
puts all of that under that folder instead of the home directory, so a
build being tried, or the test suite (tests/run_suite.py sets it), never
touches the person's own settings and time entries. Places that aren't
Buddy's - Downloads and Documents for file dialogs, Resolve's Scripts
folder, the fonts - stay on the real home.

No Qt (and nothing from core, which imports this); tests patch home().
"""

from __future__ import annotations

import os
import shutil

TOOLS = "tools"
SETTINGS_FILE = "settings.json"

# A lone file in home, kept as its tool's settings.json in the tool's folder.
FILES = {"svg_importer": ".figma_fusion_importer_settings.json"}

# Every name Buddy has kept in home: each tool's id (its ToolSettings
# bucket), the settings buckets with names of their own, and the standalone
# names the older tools kept for their data.
BUCKETS = (
    "asset_manager", "audio_assistant", "audit", "automation", "batch_clip_renamer", "buddy_network",
    "color_palette", "command_center", "dailies", "daw", "essentials", "footage_search", "games", "generative",
    "image_importer", "manual_chat", "marker_manager", "media_manager", "media_relink", "project_setup",
    "stills_exporter", "svg_importer", "text_animator", "time_tracker", "transcribe", "web", "youtube_chapters",
    "animation_previews", "buddy_web", "davinci_time_tracker", "color_palette_manager", "resolve_bin_generator",
)


def home() -> str:
    """BUDDY_HOME when it's set (~ and %VAR% expanded), else the home directory."""
    given = os.environ.get("BUDDY_HOME", "").strip()
    if given:
        return os.path.abspath(os.path.expanduser(os.path.expandvars(given)))
    return os.path.expanduser("~")


def buddy_dir() -> str:
    """~/.buddy - Buddy's own folder."""
    return os.path.join(home(), ".buddy")


def tools_dir() -> str:
    return os.path.join(buddy_dir(), TOOLS)


def legacy(bucket: str) -> str:
    """The place a bucket had in home before 1.15."""
    return os.path.join(home(), FILES.get(bucket, "." + bucket))


def path(bucket: str) -> str:
    """A tool's folder: ~/.buddy/tools/<bucket> - or its older place, for as
    long as that's there and the new one isn't (not moved yet, or couldn't
    be: what's there is what counts). Never makes anything."""
    new = os.path.join(tools_dir(), bucket)
    if bucket not in FILES and not os.path.exists(new) and os.path.isdir(legacy(bucket)):
        return legacy(bucket)
    return new


def file_path(bucket: str, name: str = SETTINGS_FILE) -> str:
    """A tool's settings file: in its folder - or, for a tool that kept a lone
    file in home (FILES), that file for as long as it hasn't been moved."""
    new = os.path.join(tools_dir(), bucket, name)
    if bucket in FILES and name == SETTINGS_FILE and not os.path.exists(new) and os.path.isfile(legacy(bucket)):
        return legacy(bucket)
    return new


def migrate(bucket: str) -> str:
    """Moves a bucket's older place into ~/.buddy/tools, once: "moved", ""
    when there's nothing to move (or it's there already), else what went
    wrong - the older place is then read from where it is (path)."""
    old = legacy(bucket)
    if bucket in FILES:
        target = os.path.join(tools_dir(), bucket, SETTINGS_FILE)
        if os.path.exists(target) or not os.path.isfile(old):
            return ""
    else:
        target = os.path.join(tools_dir(), bucket)
        if os.path.exists(target) or not os.path.isdir(old):
            return ""
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(old, target)        # a rename on the same drive; a copy across drives
    except OSError as exc:
        return f"{old}: {exc}"
    return "moved"


def migrate_all() -> dict[str, str]:
    """Every known bucket moved (BUCKETS): {bucket: "moved" or the error};
    nothing for those with nothing to move."""
    out = {}
    for bucket in BUCKETS:
        result = migrate(bucket)
        if result:
            out[bucket] = result
    return out


def relocated(name: str, tops=None) -> str:
    """A backup's file name (from home, forward slashes) where it goes now:
    ".plain/x.json" -> ".buddy/tools/plain/x.json", the lone settings file
    -> its tool's settings.json, ".buddy/..." as it is. Only Buddy's own
    older folders move (tops: backup.allowed(), when given); any other name
    is left as it is, for the restore to refuse."""
    top, _slash, rest = name.partition("/")
    if top == ".buddy" or not top.startswith(".") or (tops is not None and top not in tops):
        return name
    for bucket, file in FILES.items():
        if top == file and not rest:
            return f".buddy/{TOOLS}/{bucket}/{SETTINGS_FILE}"
    return f".buddy/{TOOLS}/{top[1:]}/{rest}" if rest else name
