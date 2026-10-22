#!/usr/bin/env python3
"""
Which graphics card Buddy's AI runs on, on a PC with more than one -
Settings > AI > Model library > "Graphics card for AI" (SharedSettings
ai_card: "" for Automatic, or a card's label as numbered() gives it).

Automatic leaves each tool to its own choice, as before: search by
description takes the first card of its own llama.cpp's Vulkan build sees;
Transcribe, Dailies' transcripts and Generative take CUDA's first card.

A card chosen here is found by its name in each engine's own list:

  - search by description (core/media_search/model.py): llama.cpp's Vulkan
    devices (pages/manual_chat/local_llama.py gpu_device(vulkan_card())).
  - Transcribe and Dailies' transcripts (pages/transcribe/jobs.py) and
    Generative (engine_client.py, video_runner.py): their engines run in
    processes of their own, started with cuda_env(), which shows CUDA only
    that card (CUDA_VISIBLE_DEVICES=its UUID, from nvidia-smi). ONNX
    Runtime's TensorRT for RTX plugin and PyTorch both list just that one
    then (checked 2026-10-22). These need NVIDIA: with another kind of card
    chosen - the processor's built-in graphics, say, to keep the NVIDIA card
    for Resolve - CUDA is shown no card (-1) and they run on the processor.

Ask Buddy's chat models run in Ollama or LM Studio, which choose their own.

The cards listed are Windows' (core/gpu_adapter.py, DXGI): Windows only - a
Mac has one GPU, or switches between two itself. Two of the same card are
told apart by #2 after the second's name, in each list's own order (DXGI's,
nvidia-smi's PCI order, Vulkan's) - the same card in each, in practice.

Read once a run (chosen): it takes effect when Buddy next starts, as an
engine already running on a card keeps it. No Qt here.
"""

import os
import shutil
import subprocess
import sys

KEY = "ai_card"
AUTO = ""

_chosen = []        # [the setting], read once a run
_nvidia = []        # [nvidia-smi's cards], asked once a run


def numbered(names):
    """Each name as a label: as it is, and #2, #3 after a name one before it
    has (two of the same card)."""
    out, seen = [], {}
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name} #{seen[name]}")
    return out


def cards():
    """The labels of this PC's graphics cards (Windows' list), or [] where
    there's no list to read."""
    if sys.platform != "win32":
        return []
    from core import gpu_adapter
    return gpu_adapter.cards()


def choices():
    """Settings' list, [(value, label)]: Automatic, then each card - or []
    with one card or none to choose from."""
    found = cards()
    if len(found) < 2:
        return []
    return [(AUTO, "Automatic (each tool's own choice)"), *((label, label) for label in found)]


def chosen():
    """The card Settings chose for this run ("" for Automatic, or one that
    isn't here any more)."""
    if not _chosen:
        try:
            from core.settings_store import SharedSettings
            value = SharedSettings().get(KEY, AUTO)
        except Exception:  # noqa: BLE001 - a settings file the shell will complain about itself
            value = AUTO
        value = value if isinstance(value, str) else AUTO
        _chosen.append(value if value and value in cards() else AUTO)
    return _chosen[0]


def vulkan_card():
    """The label llama.cpp's Vulkan build should look for ("" for its own
    choice) - every kind of card is on its list."""
    return chosen()


def _smi():
    exe = shutil.which("nvidia-smi")
    if not exe and os.name == "nt":
        exe = os.path.expandvars(r"%SystemRoot%\System32\nvidia-smi.exe")
    return exe if exe and os.path.isfile(exe) else None


def parse_nvidia(listing):
    """[{"label", "name", "uuid", "memory_mb", "compute"}] from nvidia-smi
    --query-gpu=name,uuid,memory.total,compute_cap --format=csv,noheader,nounits."""
    rows = []
    for line in listing.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 4 and parts[1].startswith("GPU-"):
            try:
                rows.append({"name": parts[0], "uuid": parts[1], "memory_mb": int(float(parts[2])),
                             "compute": float(parts[3])})
            except ValueError:
                continue
    for row, label in zip(rows, numbered([r["name"] for r in rows])):
        row["label"] = label
    return rows


def nvidia():
    """This PC's NVIDIA cards, in nvidia-smi's order - [] without one (or
    without its driver, or on a Mac). Asked once a run."""
    if not _nvidia:
        rows = []
        exe = _smi() if sys.platform != "darwin" else None
        if exe:
            try:
                out = subprocess.run([exe, "--query-gpu=name,uuid,memory.total,compute_cap",
                                      "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                rows = parse_nvidia(out.stdout) if out.returncode == 0 else []
            except (OSError, subprocess.SubprocessError):
                rows = []
        _nvidia.append(rows)
    return _nvidia[0]


def cuda_card():
    """The NVIDIA card the AI runs on: the one chosen, the first with
    Automatic - None with no NVIDIA card, or another kind chosen."""
    found = nvidia()
    card = chosen()
    if card == AUTO:
        return found[0] if found else None
    return next((row for row in found if row["label"] == card), None)


def cuda_env(env):
    """Shows a process started with env (a dict) only the chosen card, if
    one was: its UUID, or none (-1) when it isn't an NVIDIA card. Automatic,
    or no NVIDIA card at all, leaves env as it is. Returns env."""
    card = chosen()
    if card == AUTO or not nvidia():
        return env
    row = cuda_card()
    env["CUDA_VISIBLE_DEVICES"] = row["uuid"] if row else "-1"
    env["BUDDY_AI_CARD"] = card                  # for what the engine says about it (generative/gpu.py)
    return env
