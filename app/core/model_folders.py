#!/usr/bin/env python3
"""
Where each tool keeps the models it downloads: its own folder in ~/.buddy
by default, or one picked in Settings > AI > Model library - a bigger
drive, or the folder other apps keep their models in, so one copy serves
them all.

No Qt: the tools' setup modules take MODELS_DIR from here when they're
imported (pages/transcribe/env_setup.py, pages/generative/engine_setup.py,
pages/manual_chat/local_llama.py), and so does a test or a worker run from
a terminal. Saved in ~/.buddy/model_folders.json, apart from settings.json,
which the shell writes whole from what it read when it started.

set_folder() puts the new folder into every loaded copy of that tool's
module straight away. move() takes models along - only the ones each module
calls its own (own_models()), never another app's files in a shared folder.
A move between drives copies first and deletes the old copy after, so a
move cut short leaves the models where they were.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable

from core import atomic_io, tool_dirs

BUDDY_DIR = Path(tool_dirs.buddy_dir())     # ~/.buddy, or under BUDDY_HOME
CONFIG = BUDDY_DIR / "model_folders.json"
MOVING = ".buddy-moving"            # a copy being made, beside where it goes

# tool -> its name in Settings, its own folder, and the module holding its
# MODELS_DIR (Transcribe's is also imported as plain env_setup by its tests).
TOOLS = {
    "transcribe": {"label": "Subtitles", "what": "Transcription and translation models",
                   "default": BUDDY_DIR / "transcribe" / "models",
                   "modules": ("pages.transcribe.env_setup", "env_setup")},
    "generative": {"label": "Generative", "what": "Removal, cut-out and tracking models",
                   "default": BUDDY_DIR / "generative" / "models",
                   "modules": ("pages.generative.engine_setup",)},
    "manual_chat": {"label": "Ask Buddy", "what": "Search models (GGUF files)",
                    "default": BUDDY_DIR / "ask_buddy" / "models",
                    "modules": ("pages.manual_chat.local_llama",)},
}

Progress = Callable[[str, int], None]       # (text, percent)


def _saved() -> dict:
    try:
        data = atomic_io.read_json(str(CONFIG), default={}, expected_type=dict)
    except atomic_io.CorruptFileError:
        return {}
    return data if isinstance(data, dict) else {}


def folder(tool: str) -> Path:
    """The folder the tool's models go in now."""
    picked = _saved().get(tool)
    if isinstance(picked, str) and picked.strip() and os.path.isabs(picked):
        return Path(picked)
    return TOOLS[tool]["default"]


def is_custom(tool: str) -> bool:
    return _same(folder(tool), TOOLS[tool]["default"]) is False


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def set_folder(tool: str, path: str | Path | None) -> Path:
    """Keeps `path` as the tool's models folder (None or its own folder:
    back to the default) and applies it. Returns the folder now in use."""
    data = _saved()
    default = TOOLS[tool]["default"]
    if not path or _same(Path(path), default):
        data.pop(tool, None)
    else:
        data[tool] = str(Path(path))
    atomic_io.write_json(str(CONFIG), data, indent=2)
    apply(tool)
    return folder(tool)


def apply(tool: str) -> None:
    """The saved folder into each loaded copy of the tool's module."""
    now = folder(tool)
    for name in TOOLS[tool]["modules"]:
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "MODELS_DIR"):
            module.MODELS_DIR = now


def module(tool: str):
    """The tool's setup module (imported if it isn't yet)."""
    return importlib.import_module(TOOLS[tool]["modules"][0])


def own_models(tool: str, where: Path | None = None) -> list[Path]:
    """Buddy's models in `where` (default: the tool's folder now) - what a
    move takes along."""
    where = Path(where or folder(tool))
    if not where.is_dir():
        return []
    return [p for p in module(tool).own_models(where, _same(where, TOOLS[tool]["default"])) if p.exists()]


def size(path: Path) -> int:
    """Bytes in a file, or under a folder (0 if it isn't there)."""
    try:
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def check_folder(tool: str, path: str | Path) -> str:
    """"" if the tool's models can go in `path`, else why not."""
    path = Path(path)
    if not path.is_absolute():
        return "Pick a whole folder path."
    if _same(path, folder(tool)):
        return "The models are already kept there."
    # Inside an engine (a venv) or Buddy's running copy: removed and rebuilt with it.
    root = os.path.normcase(os.path.abspath(path))
    for engine in (BUDDY_DIR / "transcribe" / "venv", BUDDY_DIR / "generative" / "venv",
                   BUDDY_DIR / "ask_buddy" / "llama.cpp", Path(tempfile.gettempdir()) / "Buddy"):
        e = os.path.normcase(os.path.abspath(engine))
        if root == e or root.startswith(e + os.sep):
            return "That folder belongs to one of Buddy's engines, which are replaced when they update – pick another."
    old = os.path.normcase(os.path.abspath(folder(tool)))
    if root.startswith(old + os.sep):
        return "Pick a folder outside the one the models are in now."
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".buddy-", dir=path)
        os.close(fd)
        os.remove(probe)
    except OSError as exc:
        return f"Buddy can't write to that folder ({exc.strerror or exc})."
    return ""


def move(items: list[Path], dest: Path, progress: Progress | None = None) -> list[str]:
    """Moves each of `items` into `dest`, by the same name. On one drive a
    rename; between drives a copy, then the old one deleted. Returns what
    didn't go as planned, a line each ([] when all of it did)."""
    progress = progress or (lambda _text, _pct: None)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    sizes = {p: size(p) for p in items}
    total = sum(sizes.values()) or 1
    done = 0
    problems = []
    for src in items:
        target = dest / src.name
        pct = lambda extra=0: int(100 * min(done + extra, total) / total)    # noqa: E731
        progress(f"Moving {src.name} …", pct())
        if target.exists():
            problems.append(f"{src.name} is in the new folder already – that copy is used, and the old one "
                            f"was left in {src.parent}.")
            done += sizes[src]
            continue
        try:
            if _one_drive(src, dest):
                os.rename(src, target)
            else:
                _copy(src, target, lambda n: progress(f"Moving {src.name} …", pct(n)))
                try:
                    shutil.rmtree(src) if src.is_dir() else src.unlink()
                except OSError as exc:
                    problems.append(f"{src.name} was copied, but the old copy couldn't be deleted "
                                    f"({exc.strerror or exc}) – delete it from {src.parent} yourself.")
        except OSError as exc:
            problems.append(f"Couldn't move {src.name} ({exc.strerror or exc}) – it's still in {src.parent}.")
        done += sizes[src]
    progress("Done.", 100)
    return problems


def _one_drive(src: Path, dest: Path) -> bool:
    try:
        return os.stat(src).st_dev == os.stat(dest).st_dev
    except OSError:
        return False


def _copy(src: Path, target: Path, copied: Callable[[int], None]) -> None:
    """src (a file or a folder) to target, through target + MOVING - so
    target only ever appears whole. copied(bytes so far)."""
    temp = target.with_name(target.name + MOVING)
    if temp.exists():
        shutil.rmtree(temp) if temp.is_dir() else temp.unlink()
    so_far = [0]

    def one(a, b):
        with open(a, "rb") as fa, open(b, "wb") as fb:
            while True:
                block = fa.read(8 << 20)
                if not block:
                    break
                fb.write(block)
                so_far[0] += len(block)
                copied(so_far[0])
        shutil.copystat(a, b)

    try:
        if src.is_dir():
            shutil.copytree(src, temp, copy_function=one)
        else:
            one(src, temp)
        os.rename(temp, target)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True) if temp.is_dir() else temp.unlink(missing_ok=True)
        raise
