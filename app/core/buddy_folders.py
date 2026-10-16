#!/usr/bin/env python3
"""
Everything Buddy keeps on this computer, for Settings' ⓘ ("Where Buddy
keeps things"): the app itself, its settings and logs, each tool's models
and engines, and what the tools save - each with what it is.

No Qt: places() lists them (what exists - and the models folders, which
are a setting); sizes() measures them, slowly (an engine is thousands of
files), so Settings runs it on a thread.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from core import model_folders, tool_dirs

BUDDY_DIR = model_folders.BUDDY_DIR
SCRIPTS_DIR = (Path.home() / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
               if sys.platform == "darwin" else
               Path(os.path.expandvars(r"%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility")))
APP_DIR = Path(__file__).resolve().parents[1]       # buddy/ - unpacked from buddy.zip, or the source folder

# Files in Buddy's folder, and what they are.
FILES = {
    "settings.json": ("Settings", "Appearance, window, language and the rest of General"),
    "model_folders.json": ("Models folders", "Where each tool's models go, when one's been changed"),
    "crash.log": ("Crash log", "What Buddy was doing when it last closed unexpectedly"),
    "crash_trail.log": ("Activity log", "A running record of what Buddy did, sent with a bug report"),
    "before-restore.zip": ("Before the last restore", "A backup of what was here when a backup was restored"),
}
# Folders in Buddy's folder (and the AI engines in them).
FOLDERS = {
    "transcribe": ("Subtitles", "Transcripts, saved subtitles and its engine"),
    "generative": ("Generative", "Work files, its engine and models prepared for the graphics card"),
    "ask_buddy": ("Ask Buddy", "Saved chats, your instructions and files, and llama.cpp"),
    "manual": ("Resolve manual", "The manual Ask Buddy answers from"),
    "audio_assistant": ("Audio Assistant", "Waveforms and renders"),
    "audit": ("Audit", "Renders it checks"),
    "daw": ("DAW", "The song it plays along with"),
    "dailies": ("Dailies", "Transcripts"),
    "media_search": ("Search by description", "The index of the pictures, footage and sound Buddy has looked at"),
    "web": ("Web", "Browsing data: cookies, sign-ins, cache"),
    "web-profiles": ("Web profiles", "Each other profile's browsing data"),
    "web-history": ("Web history", "The pages visited, by profile"),
    "web-filters": ("Web ad blocking", "Filter lists"),
    "web-dictionaries": ("Web spelling", "Spell-check dictionaries"),
}
ENGINES = [
    ("Subtitles' engine", "faster-whisper and Parakeet, in a Python of their own", BUDDY_DIR / "transcribe" / "venv"),
    ("Generative's engine", "NumPy, OpenCV and ONNX Runtime (and PyTorch, with High quality)",
     BUDDY_DIR / "generative" / "venv"),
    ("Generative's graphics card cache", "Models prepared for this graphics card", BUDDY_DIR / "generative" / "gpu"),
    ("llama.cpp", "Runs Ask Buddy's search model on this computer", BUDDY_DIR / "ask_buddy" / "llama.cpp"),
]
# Tools whose folder in ~/.buddy/tools keeps the standalone name they had
# (core/tool_dirs.py): {folder: (tool, what)}. The rest are named by tool id.
OWN_NAMES = {
    "color_palette_manager": ("Color Palette", "Your palettes and its settings"),
    "davinci_time_tracker": ("Time Tracker", "Your time entries and its settings"),
    "resolve_bin_generator": ("Project Setup", "Its bin lists and settings"),
    "svg_importer": ("SVG Importer", "Its settings"),
    "animation_previews": ("Animation", "Its presets, favourites and settings"),
    "buddy_web": ("Web", "Its settings"),
}
SKIP = (".bak", ".tmp", ".1", ".corrupt")


def _row(label, what, path, **extra):
    path = Path(path)
    return {"id": "", "label": label, "what": what, "path": str(path), "file": path.is_file(), **extra}


def places(tools=(), backups=None) -> list[dict]:
    """[{title, rows: [{id, label, what, path, file, [chip]}]}] - each place
    that's there. tools: [(tool id, name)], for each tool's own folder
    (~/.<tool id>). backups: where automatic backups go, when they're on."""
    home = Path(tool_dirs.home())      # where the tools' older places are (BUDDY_HOME follows)
    groups = []

    buddy = [_row("Buddy's folder", "Settings, logs and what the tools keep", BUDDY_DIR)]
    if (SCRIPTS_DIR / "Buddy.py").is_file():
        buddy.append(_row("Installed in Resolve", "Buddy.py and buddy.zip – what Workspace > Scripts runs", SCRIPTS_DIR))
    buddy.append(_row("Running from", "Unpacked from buddy.zip when it changes; old copies are cleared away"
                      if Path(tempfile.gettempdir()) in APP_DIR.parents else "Buddy's source folder", APP_DIR.parent))
    helper = Path(os.path.expandvars(r"%LOCALAPPDATA%\Buddy")) if os.name == "nt" else None
    if helper and helper.is_dir():
        buddy.append(_row("Start-up helper", "Starts Buddy with Resolve, and its shortcuts' launcher", helper))
    groups.append({"title": "Buddy", "rows": buddy})

    groups.append({"title": "Settings and logs", "rows": [
        _row(label, what, BUDDY_DIR / name) for name, (label, what) in FILES.items() if (BUDDY_DIR / name).is_file()]})
    if backups:
        groups[-1]["rows"].append(_row("Automatic backups", "Settings > General > Backup", backups))

    models = []
    for tool, t in model_folders.TOOLS.items():
        custom = model_folders.is_custom(tool)
        models.append(_row(f"{t['label']} models", t["what"], model_folders.folder(tool),
                           chip="Your folder" if custom else ""))
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"   # not Buddy's: the real home
    if hub.is_dir():
        models.append(_row("Hugging Face cache", "Not Buddy's – Subtitles uses models other apps downloaded here",
                           hub))
    groups.append({"title": "Models", "rows": models})

    engines = [_row(label, what, path) for label, what, path in ENGINES if path.is_dir()]
    if engines:
        groups.append({"title": "AI engines", "rows": engines})

    data = []
    if BUDDY_DIR.is_dir():
        for child in sorted(BUDDY_DIR.iterdir(), key=lambda p: p.name.lower()):
            if child.name in FILES or child.name.endswith(SKIP) or ".corrupt" in child.name:
                continue
            if child.name == tool_dirs.TOOLS:
                continue                            # each tool's, listed below
            if child.is_dir():
                label, what = FOLDERS.get(child.name, (child.name, ""))
                data.append(_row(label, what, child))
    if data:
        groups.append({"title": "In Buddy's folder", "rows": data})

    # Each tool's folder in ~/.buddy/tools - and one still at its older place
    # in home, when it couldn't be moved (core/tool_dirs.py).
    names = dict(tools)
    own = []
    tools_dir = BUDDY_DIR / tool_dirs.TOOLS
    if tools_dir.is_dir():
        for child in sorted(tools_dir.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.endswith(SKIP):
                continue
            label, what = OWN_NAMES.get(child.name, (names.get(child.name, child.name),
                                                     "Its settings and what it saves"))
            own.append(_row(label, what, child))
    for bucket in (*names, *OWN_NAMES):
        old = home / tool_dirs.FILES.get(bucket, "." + bucket)
        if old.exists() and not (tools_dir / bucket).exists():
            label, what = OWN_NAMES.get(bucket, (names.get(bucket, bucket), "Its settings and what it saves"))
            own.append(_row(label, what, old, chip="Older place"))
    if own:
        groups.append({"title": "Each tool's settings and data", "rows": own})

    for g in groups:
        for i, row in enumerate(g["rows"]):
            row["id"] = f"{g['title']}:{i}"
    return [g for g in groups if g["rows"]]


def sizes(groups) -> dict[str, int]:
    """path -> bytes, for each place in places() (the Hugging Face cache
    and Buddy's folder as a whole too)."""
    return {row["path"]: model_folders.size(Path(row["path"])) for g in groups for row in g["rows"]}
