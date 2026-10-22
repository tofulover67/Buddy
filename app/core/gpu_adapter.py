#!/usr/bin/env python3
"""
Which graphics card Buddy's web pages draw on, on a PC with more than one.

Qt draws on DXGI's first adapter unless QT_D3D_ADAPTER_INDEX says
otherwise, and hands the same card to Chromium (--use-adapter-luid). The
first adapter is the one driving the main monitor - on a desktop with a
card and integrated graphics, the card Resolve is busy with. There, ANGLE's
Direct3D 11 on 12 (core/web_flags.py) went down with the card's driver:
dragging Buddy onto a monitor plugged into the integrated graphics, or
quitting Resolve, reset the NVIDIA driver (nvlddmkm event 153) and Buddy
crashed with it. Drawing on the low-power adapter - the integrated
graphics - survived all of that, at the same 60 frames a second: web pages
ask very little of a GPU, and the card is left to Resolve.

So with two or more hardware adapters, QT_D3D_ADAPTER_INDEX is set to the
one Windows calls minimum-power (IDXGIFactory6::EnumAdapterByGpuPreference).
With one adapter nothing is set. Settings > Window > "Graphics card for
Buddy's window" picks one by name instead (SharedSettings web_adapter: "auto"
or a card's label, see labels) - a card that's gone since falls back to the
low-power one. BUDDY_WEB_ADAPTER=<index> picks one by hand, =default leaves
Qt's choice; both win over the setting, and a QT_D3D_ADAPTER_INDEX already
set wins over everything.

The pick is kept (picked) for what depends on which card it is: AMD's
integrated graphics draws some rounded corners wrong on the GPU, so web
pages are painted on the CPU there (amd_integrated, core/web_flags.py).

No Qt here; nothing raises - without DXGI (not Windows) it's a no-op.
"""

import ctypes
import sys
import uuid

from core import ai_card

VENDOR_AMD = 0x1002
DXGI_ADAPTER_FLAG_SOFTWARE = 2
DXGI_GPU_PREFERENCE_MINIMUM_POWER = 1
_IID_FACTORY1 = uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387")
_IID_FACTORY6 = uuid.UUID("c1b6694f-ff09-44a9-b03c-77900a0a1d17")
_IID_ADAPTER1 = uuid.UUID("29038f61-3839-4626-91fd-086879011a05")

# The adapter apply() picked when there were two or more to pick from
# ({"name", "luid", "vendor", "software", "low_power"}), None when it picked
# nothing - one card, a choice made in the environment, no DXGI.
picked = None
# DXGI's adapters and the low-power one's LUID, as apply() found them as
# Buddy started - what Settings lists (choices); None until they're asked for.
_found = None

AUTO = "auto"


def amd_integrated():
    """Is Buddy drawing on AMD's integrated graphics - the low-power card of
    two, picked for it or chosen in Settings? There, Chromium paints rounded
    corners wrong on the GPU (core/web_flags.py)."""
    return bool(picked) and picked.get("vendor") == VENDOR_AMD and bool(picked.get("low_power"))


def labels(adapters):
    """{index: label} for each hardware adapter - its name, and #2, #3 after
    a name another card before it has (two of the same card)."""
    hardware = [i for i, a in enumerate(adapters) if not a["software"]]
    return dict(zip(hardware, ai_card.numbered([adapters[i]["name"] for i in hardware])))


def choose(adapters, low_power_luid, wanted=AUTO):
    """The index to draw on, or None for Qt's own choice. adapters: DXGI's
    order, [{"name", "luid", "software"}]; low_power_luid: Windows'
    minimum-power adapter; wanted: the setting - a label (see labels), or
    AUTO for the low-power one. Only when there's more than one real card to
    choose from."""
    named = labels(adapters)
    if len(named) < 2:
        return None
    for i, label in named.items():
        if wanted != AUTO and label == wanted:
            return i
    if low_power_luid is None:
        return None
    for i in named:
        if adapters[i]["luid"] == low_power_luid:
            return i
    return None


def _adapters():
    """(adapters, low-power LUID), asked of DXGI once."""
    global _found
    if _found is None:
        _found = _enumerate()
    return _found


def cards():
    """This PC's hardware adapters' labels (see labels), in DXGI's order - []
    without DXGI. What Settings lists, here and for the AI (core/ai_card.py)."""
    try:
        return list(labels(_adapters()[0]).values())
    except Exception:  # noqa: BLE001 - no DXGI: nothing to choose
        return []


def choices():
    """Settings' list, [(value, label)]: AUTO, then each card - or [] where
    there's one card or none to choose from (or no DXGI)."""
    found = cards()
    if len(found) < 2:
        return []
    return [(AUTO, "Automatic (the low-power card)"), *((label, label) for label in found)]


def apply(environ, wanted=AUTO):
    """Sets QT_D3D_ADAPTER_INDEX if it should be; returns what the crash
    trail should say about it. wanted: Settings' choice (web_adapter).
    Before the QApplication exists."""
    global picked, _found
    picked = None
    if environ.get("QT_D3D_ADAPTER_INDEX"):
        return f"adapter {environ['QT_D3D_ADAPTER_INDEX']} (QT_D3D_ADAPTER_INDEX)"
    manual = environ.get("BUDDY_WEB_ADAPTER", "").strip().lower()
    if manual == "default":
        return "adapter: Qt's choice (BUDDY_WEB_ADAPTER)"
    if manual.isdigit():
        environ["QT_D3D_ADAPTER_INDEX"] = manual
        return f"adapter {manual} (BUDDY_WEB_ADAPTER)"
    try:
        adapters, low_power = _found = _enumerate()
    except Exception as exc:  # noqa: BLE001 - no DXGI, an old Windows: Qt's choice
        return f"adapter: Qt's choice ({type(exc).__name__})"
    wanted = wanted if isinstance(wanted, str) and wanted else AUTO
    index = choose(adapters, low_power, wanted)
    if index is None:
        return f"adapter: Qt's choice ({len(adapters)} adapters)"
    environ["QT_D3D_ADAPTER_INDEX"] = str(index)
    picked = dict(adapters[index], low_power=adapters[index]["luid"] == low_power)
    if wanted != AUTO and labels(adapters)[index] == wanted:
        return f"adapter {index} ({adapters[index]['name']}, Settings)"
    gone = "" if wanted == AUTO else f"; Settings' {wanted} not found"
    return f"adapter {index} ({adapters[index]['name']}, low-power of {len(adapters)}{gone})"


# ------------------------------------------------------------------ DXGI --

class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint32),
                ("DeviceId", ctypes.c_uint32), ("SubSysId", ctypes.c_uint32), ("Revision", ctypes.c_uint32),
                ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", _LUID), ("Flags", ctypes.c_uint32)]


def _guid(u):
    return (ctypes.c_byte * 16).from_buffer_copy(u.bytes_le)


def _method(obj, index, restype, *argtypes):
    """A COM method: the index-th entry of the object's vtable."""
    vtable = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(obj):
    if obj:
        _method(obj, 2, ctypes.c_ulong)(obj)


def _describe(adapter):
    desc = _DESC1()
    if _method(adapter, 10, ctypes.c_long, ctypes.POINTER(_DESC1))(adapter, ctypes.byref(desc)) < 0:
        raise OSError("GetDesc1 failed")
    luid = (desc.AdapterLuid.HighPart, desc.AdapterLuid.LowPart)
    return {"name": desc.Description, "luid": luid, "vendor": desc.VendorId,
            "software": bool(desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)}


def _enumerate():
    """DXGI's adapters in its order, and the minimum-power one's LUID (None
    if this Windows can't say)."""
    if sys.platform != "win32":
        raise OSError("not Windows")
    dxgi = ctypes.WinDLL("dxgi")
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(_guid(_IID_FACTORY1)), ctypes.byref(factory)) < 0:
        raise OSError("CreateDXGIFactory1 failed")
    try:
        adapters = []
        enum1 = _method(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        for i in range(16):
            adapter = ctypes.c_void_p()
            if enum1(factory, i, ctypes.byref(adapter)) < 0:   # DXGI_ERROR_NOT_FOUND: no more
                break
            try:
                adapters.append(_describe(adapter))
            finally:
                _release(adapter)
        low_power = None
        factory6 = ctypes.c_void_p()
        query = _method(factory, 0, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        if query(factory, ctypes.byref(_guid(_IID_FACTORY6)), ctypes.byref(factory6)) >= 0:
            try:
                adapter = ctypes.c_void_p()
                by_pref = _method(factory6, 29, ctypes.c_long, ctypes.c_uint, ctypes.c_int, ctypes.c_void_p,
                                  ctypes.POINTER(ctypes.c_void_p))
                if by_pref(factory6, 0, DXGI_GPU_PREFERENCE_MINIMUM_POWER, ctypes.byref(_guid(_IID_ADAPTER1)),
                           ctypes.byref(adapter)) >= 0:
                    try:
                        low_power = _describe(adapter)["luid"]
                    finally:
                        _release(adapter)
            finally:
                _release(factory6)
        return adapters, low_power
    finally:
        _release(factory)
