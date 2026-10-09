#!/usr/bin/env python3
"""
The Chromium switches Buddy's web views start with on Windows (main.py
applies them there only).

Chromium draws through ANGLE on a GPU thread inside Buddy's own process.
ANGLE's default backend, Direct3D 11, crashes Buddy within a minute or two
of ordinary use, on any card: on Chromium's GPU thread, a write through a
freed pointer while relinking a list in ANGLE's cache of Direct3D state
objects (Qt6WebEngineCore.dll+0xd020d0 in Qt 6.11.2). The more varied the
pages' drawing, the sooner - Buddy's themed pages brought it in ~35 s of
switching tools. Direct3D 11 on 12 never does: 550+ switches without a
fault where Direct3D 11 managed ~75. ANGLE's OpenGL and Vulkan backends
lost their context over and over.

11 on 12 has one weakness of its own: on the card's driver. Dragging Buddy
onto a monitor plugged into another GPU, or quitting Resolve, reset the
NVIDIA driver (nvlddmkm event 153) and took Buddy with it - so on a PC with
two GPUs, Buddy draws on the low-power one (core/gpu_adapter.py), where it
survived all of that.

Drawing in software (--disable-gpu) never crashed, but managed 7 frames a
second on a busy page where the GPU does 60.

11 on 12 on AMD's integrated graphics (the low-power adapter above) draws
some rounded corners wrong: a box's bottom-left curve and border go
missing and a long thin sliver runs from the corner across the box - on
Buddy's own cards and fields (Nova shows it most) and on sites' pages
alike, and redrawn with every frame of whatever animates over it. It's
the multisampling Chromium paints those curves with on the GPU: with it
off (--gpu-rasterization-msaa-sample-count=0) every corner comes out right.

Only on Buddy's own pages, it turned out: sites in the Web tab still drew
those corners wrong with multisampling off (Google's settings menu,
2026-10-09, same AMD adapter).

Painting on the CPU instead (--disable-gpu-rasterization) draws every
corner right, sites' included (confirmed 2026-10-09), but slowly: a
maximized tool on a 4K screen took ~450-650 ms to appear after switching
to it, against ~70-190 ms painted on the GPU (measured from screen
captures, 2026-10-03) - Chromium repaints a page that's been out of sight
from scratch. So pages are painted on the CPU only where the GPU paints
them wrong: on AMD's integrated graphics, picked as the low-power card of
two (core/gpu_adapter.py amd_integrated) - that's Settings > Window > "Draw
web pages on the CPU" at "Only where needed", its default; "Always" and
"Never" are for a machine this doesn't know (main.py reads it before the
engine starts: cpu_paint). BUDDY_WEB_CPU_PAINT=1 or =0 wins over the
setting.

BUDDY_WEB_SOFTWARE=1 draws in software again (a machine where the GPU
still crashes); BUDDY_WEB_GPU=1 adds nothing, leaving Chromium's own
default (for comparing). Whatever QTWEBENGINE_CHROMIUM_FLAGS already holds
is kept, and a --use-angle there wins over Buddy's - as does a
multisampling count of its own.

Every one of Buddy's own pages (all local files: one "site" to Chromium)
shares one renderer process (--process-per-site). By default each got its
own - 25 of them, ~540 MB of private memory and ~1.9 GB of working set
between them, against ~130 MB and ~250 MB shared (measured 2026-10-03).
Switching tools, the Animation tab's 60 fps and startup were unchanged;
none of Buddy's pages ran a task over 50 ms, so sharing the one thread
holds nothing up; and when the shared renderer was killed every page
reloaded within 2 s (core/web_page.py). Sites in the Web tab still get
their own process, one per site. BUDDY_WEB_PROCESS_PER_PAGE=1 goes back to
one per page.
"""

import os

from core import tool_dirs

GPU =("--use-angle=d3d11on12", "--gpu-rasterization-msaa-sample-count=0")
CPU_PAINT = "--disable-gpu-rasterization"
SOFTWARE = ("--disable-gpu", "--disable-gpu-compositing")
SHARED_RENDERER = "--process-per-site"

# Settings > Window > "Draw web pages on the CPU" (SharedSettings
# web_cpu_paint): [(value, label)]. Before 1.15 the setting was a tick:
# True is "on"; False was the default, so it's "auto" (nobody had chosen it).
CPU_PAINT_CHOICES = [("auto", "Only where needed (AMD integrated graphics)"), ("on", "Always"), ("off", "Never")]


def cpu_paint_choice(value):
    """The setting's value as one of CPU_PAINT_CHOICES' - whatever it holds."""
    if value is True:
        return "on"
    return value if value in dict(CPU_PAINT_CHOICES) else "auto"


def cpu_paint(environ, choice, amd_integrated):
    """Sets BUDDY_WEB_CPU_PAINT from the setting (choice: a CPU_PAINT_CHOICES
    value) and the card (amd_integrated: core/gpu_adapter.py, after its
    apply), unless the environment says already. Before chromium_flags;
    returns what the crash trail should say."""
    given = environ.get("BUDDY_WEB_CPU_PAINT")
    if given is not None:
        return f"cpu paint {given} (BUDDY_WEB_CPU_PAINT)"
    if choice == "on":
        environ["BUDDY_WEB_CPU_PAINT"] = "1"
        return "cpu paint on (Settings)"
    if choice == "auto" and amd_integrated:
        environ["BUDDY_WEB_CPU_PAINT"] = "1"
        return "cpu paint on (AMD integrated graphics)"
    return "cpu paint off (Settings)" if choice == "off" else "cpu paint off"


def chromium_flags(env):
    """QTWEBENGINE_CHROMIUM_FLAGS for this run, given the environment."""
    flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    if env.get("BUDDY_WEB_GPU") == "1":
        return " ".join(flags)
    if env.get("BUDDY_WEB_PROCESS_PER_PAGE") != "1" and SHARED_RENDERER not in flags:
        flags.append(SHARED_RENDERER)
    if env.get("BUDDY_WEB_SOFTWARE") == "1":
        flags += [f for f in SOFTWARE if f not in flags]
    elif not any(f.startswith("--use-angle") for f in flags):
        given = {f.split("=")[0] for f in flags}
        flags += [f for f in GPU if f.split("=")[0] not in given]
        if env.get("BUDDY_WEB_CPU_PAINT") == "1" and CPU_PAINT not in flags:
            flags.append(CPU_PAINT)
    return " ".join(flags)


def dictionaries_dir(home=None):
    """Where the Web tab's spelling dictionaries are kept (Chromium's .bdic
    files, fetched when one is chosen - pages/web/spelling.py)."""
    return os.path.join(home or tool_dirs.home(), ".buddy", "web-dictionaries")


def apply(environ):
    """Sets the switches; must run before the QApplication exists. Qt
    WebEngine reads its spelling dictionaries' folder once, as it starts."""
    environ["QTWEBENGINE_CHROMIUM_FLAGS"] = chromium_flags(environ)
    environ.setdefault("QTWEBENGINE_DICTIONARIES_PATH", dictionaries_dir())
    return environ["QTWEBENGINE_CHROMIUM_FLAGS"]
