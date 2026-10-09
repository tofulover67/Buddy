#!/usr/bin/env python3
"""
Time Tracker's global Pause/Resume shortcut on macOS: Control+Option+P,
the Mac spelling of Windows' Ctrl+Alt+P (page.py registers that one).

Carbon's RegisterEventHotKey - old, still supported, and what Mac apps use
for a system-wide shortcut. It needs no permission: it hears only this one
key combination, unlike a keyboard listener (Input Monitoring). Since
macOS 15 a shortcut made of Option (+ Shift) alone is refused; this one
includes Control.

The pressed shortcut arrives through the app's own event loop, on the main
thread, so the callback can touch Qt directly.
"""

import ctypes
import ctypes.util

LABEL = "Control+Option+P"

_KVK_ANSI_P = 0x23
_CONTROL_KEY = 0x1000
_OPTION_KEY = 0x0800
_EVENT_HOTKEY_PRESSED = 5


def _fourcc(code):
    return int.from_bytes(code.encode("ascii"), "big")


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

_carbon = None
# While registered: (the C callback - kept referenced, or Python frees it
# while Carbon can still call it - handler ref, hotkey ref).
_registered = None


def _load():
    global _carbon
    if _carbon is None:
        carbon = ctypes.CDLL(ctypes.util.find_library("Carbon"))
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p, _HANDLER, ctypes.c_ulong, ctypes.POINTER(_EventTypeSpec),
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.InstallEventHandler.restype = ctypes.c_int32
        carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.RegisterEventHotKey.restype = ctypes.c_int32
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        carbon.UnregisterEventHotKey.restype = ctypes.c_int32
        carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        carbon.RemoveEventHandler.restype = ctypes.c_int32
        _carbon = carbon
    return _carbon


def register(callback):
    """Registers Control+Option+P to call callback(). True on success,
    False if it couldn't be (e.g. another app already has it)."""
    global _registered
    if _registered is not None:
        return True
    try:
        carbon = _load()

        def on_event(_call_ref, _event, _user_data):
            try:
                callback()
            except Exception:
                pass   # an exception must never unwind into Carbon
            return 0

        c_callback = _HANDLER(on_event)
        target = carbon.GetApplicationEventTarget()
        spec = _EventTypeSpec(_fourcc("keyb"), _EVENT_HOTKEY_PRESSED)
        handler_ref = ctypes.c_void_p()
        if carbon.InstallEventHandler(target, c_callback, 1, ctypes.byref(spec), None,
                                      ctypes.byref(handler_ref)) != 0:
            return False
        hotkey_ref = ctypes.c_void_p()
        if carbon.RegisterEventHotKey(_KVK_ANSI_P, _CONTROL_KEY | _OPTION_KEY,
                                      _EventHotKeyID(_fourcc("Bddy"), 1), target, 0,
                                      ctypes.byref(hotkey_ref)) != 0:
            carbon.RemoveEventHandler(handler_ref)
            return False
        _registered = (c_callback, handler_ref, hotkey_ref)
        return True
    except Exception:
        return False


def unregister():
    global _registered
    if _registered is None:
        return
    _c_callback, handler_ref, hotkey_ref = _registered
    try:
        _carbon.UnregisterEventHotKey(hotkey_ref)
        _carbon.RemoveEventHandler(handler_ref)
    except Exception:
        pass
    _registered = None
