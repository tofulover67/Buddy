#!/usr/bin/env python3
"""
Idle detection for Time Tracker - reads the system-wide "last input"
timestamp (keyboard/mouse, any application) - Windows' GetLastInputInfo,
macOS's CGEventSourceSecondsSinceLastEventType (which needs no permission) - so
tracking can be auto-paused when nobody's actually at the machine even
though Resolve is still open (or a manual session is still running).
"""

import ctypes
import ctypes.util
import sys


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def get_idle_seconds():
    """Seconds since the last keyboard/mouse input anywhere on the system.
    Returns 0.0 (i.e. "not idle") if the platform call fails for any reason
    - a broken idle check should never itself be the thing that stops
    tracking."""
    if sys.platform == "darwin":
        return _mac_idle_seconds()
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick_count = ctypes.windll.kernel32.GetTickCount()
        # Both are 32-bit millisecond counters that wrap around every ~49.7
        # days; using the difference modulo 2**32 keeps this correct across
        # that wraparound instead of producing a huge bogus idle time.
        millis_idle = (tick_count - info.dwTime) & 0xFFFFFFFF
        return max(0.0, millis_idle / 1000.0)
    except Exception:
        return 0.0


_CG_COMBINED_SESSION_STATE = 0    # kCGEventSourceStateCombinedSessionState
_CG_ANY_INPUT_EVENT = 0xFFFFFFFF  # kCGAnyInputEventType
_cg_seconds_since = None


def _mac_idle_seconds():
    global _cg_seconds_since
    try:
        if _cg_seconds_since is None:
            cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
            fn = cg.CGEventSourceSecondsSinceLastEventType
            fn.restype = ctypes.c_double
            fn.argtypes = [ctypes.c_int32, ctypes.c_uint32]
            _cg_seconds_since = fn
        return max(0.0, float(_cg_seconds_since(_CG_COMBINED_SESSION_STATE, _CG_ANY_INPUT_EVENT)))
    except Exception:
        return 0.0
