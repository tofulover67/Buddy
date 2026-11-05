#!/usr/bin/env python3
"""
"Cut the open timeline live": Resolve's own keys, pressed for the person.

The scripting API can set In and Out but can't ripple-delete them, so each
live cut is In/Out by the API, then the person's own Ripple Delete key
pressed in Resolve - so Resolve makes the cut, and grades, effects and links
stay. Keys go through pynput (a key Resolve hears like any other), to
Resolve's window, brought to the front first.

Shortcuts are written the Windows way ("Ctrl+Shift+A"); on a Mac Ctrl is
the Command key, as everywhere in Buddy. A Mac only lets an app press keys
for another once it's allowed to, under Privacy & Security >
Accessibility.
"""

import os
import subprocess
import sys
import time

IS_MAC = sys.platform == "darwin"

COMMANDS = ("deselect", "mark_in", "mark_out", "clear_marks", "ripple_delete", "undo", "redo")
LABELS = {
    "deselect": "Deselect all", "mark_in": "Mark In", "mark_out": "Mark Out",
    "clear_marks": "Clear In and Out", "ripple_delete": "Ripple delete", "undo": "Undo", "redo": "Redo",
}
PRESETS = {
    "resolve": {"label": "DaVinci Resolve (default)", "keys": {
        "deselect": "Ctrl+Shift+A", "mark_in": "I", "mark_out": "O", "clear_marks": "Alt+X",
        "ripple_delete": "Shift+Backspace", "undo": "Ctrl+Z", "redo": "Ctrl+Shift+Z"}},
    "premiere": {"label": "Adobe Premiere Pro layout", "keys": {
        "deselect": "Ctrl+Shift+A", "mark_in": "I", "mark_out": "O", "clear_marks": "Ctrl+Shift+X",
        "ripple_delete": "Shift+Backspace" if IS_MAC else "Shift+Delete", "undo": "Ctrl+Z",
        "redo": "Ctrl+Shift+Z"}},
}
MODIFIERS = ("Ctrl", "Alt", "Shift")
NAMED = ("Backspace", "Delete", "Space", "Enter", "Esc", "Tab", "Left", "Right", "Up", "Down",
         "Home", "End", "PageUp", "PageDown", *(f"F{n}" for n in range(1, 13)))
PUNCTUATION = "[];',./\\-=`"
SETTLE_S = 0.09


def canonical(combo):
    """A shortcut spelled one way ("ctrl + shift + a" -> "Ctrl+Shift+A"), or
    "" if it isn't one: any modifiers, then exactly one key."""
    parts = [p.strip() for p in str(combo or "").replace(" ", "").split("+")]
    mods, key = set(), None
    aliases = {"control": "Ctrl", "ctl": "Ctrl", "cmd": "Ctrl", "command": "Ctrl", "⌘": "Ctrl",
               "option": "Alt", "opt": "Alt", "⌥": "Alt", "shift": "Shift", "⇧": "Shift", "ctrl": "Ctrl",
               "alt": "Alt", "del": "Delete", "escape": "Esc", "return": "Enter", "pgup": "PageUp",
               "pgdn": "PageDown"}
    for raw in parts:
        if not raw:
            return ""
        name = aliases.get(raw.lower(), raw)
        if name in MODIFIERS:
            mods.add(name)
            continue
        if key is not None:
            return ""
        match = next((n for n in NAMED if n.lower() == name.lower()), None)
        if match:
            key = match
        elif len(name) == 1 and (name.isalnum() or name in PUNCTUATION):
            key = name.upper()
        else:
            return ""
    if key is None:
        return ""
    return "+".join([m for m in MODIFIERS if m in mods] + [key])


def load(saved):
    """{command: combo} from what's saved ({"preset"} or {"keys"}), defaults filled in."""
    saved = saved if isinstance(saved, dict) else {}
    preset = PRESETS.get(saved.get("preset")) or PRESETS["resolve"]
    keys = dict(preset["keys"])
    for command, combo in (saved.get("keys") or {}).items():
        if command in COMMANDS and canonical(combo):
            keys[command] = canonical(combo)
    return keys


def preset_of(keys):
    """The preset these keys are, or "" (custom)."""
    for name, preset in PRESETS.items():
        if all(canonical(keys.get(c)) == preset["keys"][c] for c in COMMANDS):
            return name
    return ""


# ---------------------------------------------------------------- sending --

class KeysUnavailable(RuntimeError):
    pass


def mac_allowed():
    """True when this Mac lets Buddy press keys for other apps (Accessibility)."""
    if not IS_MAC:
        return True
    try:
        import ctypes
        lib = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception:  # noqa: BLE001 - can't tell: let the press try
        return True


def available():
    """"" when keys can be pressed here, else why not."""
    try:
        import pynput.keyboard  # noqa: F401
    except Exception:  # noqa: BLE001
        return "Pressing keys in Resolve needs the pynput package, which isn't installed."
    if IS_MAC and not mac_allowed():
        return ("To cut the open timeline live, allow Buddy to control your computer: System Settings > "
                "Privacy & Security > Accessibility, then turn on the app Buddy runs in (DaVinci Resolve).")
    return ""


def _key(name):
    from pynput.keyboard import Key, KeyCode
    named = {"Backspace": Key.backspace, "Delete": Key.delete, "Space": Key.space, "Enter": Key.enter,
             "Esc": Key.esc, "Tab": Key.tab, "Left": Key.left, "Right": Key.right, "Up": Key.up,
             "Down": Key.down, "Home": Key.home, "End": Key.end, "PageUp": Key.page_up,
             "PageDown": Key.page_down, "Ctrl": Key.cmd if IS_MAC else Key.ctrl, "Alt": Key.alt,
             "Shift": Key.shift}
    if name in named:
        return named[name]
    if name.startswith("F") and name[1:].isdigit():
        return getattr(Key, name.lower())
    if not IS_MAC and name.isalnum():
        return KeyCode.from_vk(ord(name.upper()))       # a virtual key: Ctrl+letter must be one
    return KeyCode.from_char(name.lower())


class Presser:
    """Presses shortcuts in Resolve. `controller` is pynput's (tests give a fake)."""

    def __init__(self, controller=None, focus=None):
        self._controller = controller
        self._focus = focus if focus is not None else focus_resolve

    def _keyboard(self):
        if self._controller is None:
            why = available()
            if why:
                raise KeysUnavailable(why)
            from pynput.keyboard import Controller
            self._controller = Controller()
        return self._controller

    def press(self, combo):
        combo = canonical(combo)
        if not combo:
            raise KeysUnavailable("That shortcut isn't one Buddy can press.")
        keyboard = self._keyboard()
        names = combo.split("+")
        mods, key = names[:-1], names[-1]
        held = []
        try:
            for m in mods:
                keyboard.press(_key(m))
                held.append(_key(m))
            keyboard.press(_key(key))
            keyboard.release(_key(key))
        finally:
            for k in reversed(held):
                keyboard.release(k)
        time.sleep(SETTLE_S)

    def focus(self):
        return self._focus()


# ------------------------------------------------------------- the window --

def focus_resolve():
    """Resolve's window to the front. True if it got there (or can't tell)."""
    if IS_MAC:
        for script in ('tell application id "com.blackmagic-design.DaVinciResolve" to activate',
                       'tell application "DaVinci Resolve" to activate'):
            try:
                if subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5).returncode == 0:
                    time.sleep(0.15)
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
        return False
    if sys.platform != "win32":
        return False
    hwnd = _resolve_window()
    if not hwnd:
        return False
    return _win_focus(hwnd)


def _resolve_window():
    """The biggest visible window of Resolve.exe."""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return True
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                if os.path.basename(buf.value).lower() == "resolve.exe":
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    found.append(((rect.right - rect.left) * (rect.bottom - rect.top), hwnd))
        finally:
            kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows(each, 0)
    return max(found)[1] if found else None


def _win_focus(hwnd):
    import ctypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                  # SW_RESTORE
    for _ in range(3):
        fg = user32.GetForegroundWindow()
        mine = kernel32.GetCurrentThreadId()
        theirs = user32.GetWindowThreadProcessId(fg, None)
        attached = theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(mine, theirs, False)
        for _ in range(20):
            if user32.GetForegroundWindow() == hwnd:
                time.sleep(0.05)
                return True
            time.sleep(0.025)
    return False
