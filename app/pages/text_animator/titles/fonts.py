"""The title looks' fonts, installed for the person's own user (no admin rights) so
Fusion's Text+ can draw them: Buddy ships them (assets/fonts/titles, SIL Open Font
License) and the preview loads them straight from there (measure.py), but Text+ only
knows fonts installed on the computer.

Windows: copied into %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts and registered under
HKCU\\...\\Windows NT\\CurrentVersion\\Fonts - Windows' own per-user install.
macOS: copied into ~/Library/Fonts.
Resolve reads its font list when it starts; add_to_fusion() hands it the fonts just
installed (FontManager.AddFont, measured on Studio 21.1: the face is in GetFontList at
once, no restart).
"""

import os
import shutil
import struct
import sys

from .. import canvas_math as cm
from . import measure as M

_REG_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"


def full_name(path):
    """The font's full name (name ID 4, "Montserrat Black") - what Windows registers it
    under - else its file name."""
    try:
        with open(path, "rb") as f:
            table = cm._sfnt_tables(f.read())["name"]
        _fmt, count, strings = struct.unpack(">HHH", table[:6])
        for i in range(count):
            platform, _enc, _lang, nid, length, offset = struct.unpack(">HHHHHH", table[6 + 12 * i:18 + 12 * i])
            if platform == 3 and nid == 4:
                return table[strings + offset:strings + offset + length].decode("utf-16-be")
    except (OSError, KeyError, struct.error, UnicodeDecodeError):
        pass
    return os.path.splitext(os.path.basename(path))[0]


def user_font_dir():
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Microsoft", "Windows", "Fonts")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Fonts")
    return os.path.expanduser("~/.local/share/fonts")


def missing(font_list):
    """The bundled faces Resolve's FontManager doesn't list ({family: {style: file}},
    FontManager.GetFontList()) - [(family, style)]."""
    font_list = font_list or {}
    return sorted(face for face in M.bundled_files() if face[1] not in (font_list.get(face[0]) or {}))


def _register_windows(path, name):
    import ctypes
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
        winreg.SetValueEx(key, f"{name} (TrueType)", 0, winreg.REG_SZ, path)
    gdi32, user32 = ctypes.windll.gdi32, ctypes.windll.user32
    gdi32.AddFontResourceW(path)
    # WM_FONTCHANGE to every window - with a timeout, so a hung window can't hang Buddy.
    result = ctypes.c_ulong()
    user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, 0, 0x0002, 1000, ctypes.byref(result))


def install(faces=None):
    """Install the bundled title fonts (or just these (family, style) faces) for this
    user. Returns {"installed": [names], "already": [names], "failed": [(name, why)]}."""
    done = {"installed": [], "already": [], "failed": []}
    folder = user_font_dir()
    for face, src in sorted(M.bundled_files().items()):
        if faces is not None and face not in faces:
            continue
        name = full_name(src)
        dest = os.path.join(folder, os.path.basename(src))
        try:
            if os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(src):
                done["already"].append(name)
            else:
                os.makedirs(folder, exist_ok=True)
                shutil.copyfile(src, dest)
                done["installed"].append(name)
            if sys.platform == "win32":
                _register_windows(dest, name)
        except Exception as exc:  # noqa: BLE001 - reported per font
            done["failed"].append((name, str(exc)))
    return done


def add_to_fusion(fusion, font_list=None):
    """Hand Resolve's running Fusion the bundled faces its font list lacks (installed
    with install() first). Returns the faces still missing afterwards."""
    manager = fusion.FontManager
    font_list = font_list if font_list is not None else (manager.GetFontList() or {})
    folder = user_font_dir()
    files = M.bundled_files()
    for face in missing(font_list):
        path = os.path.join(folder, os.path.basename(files[face]))
        if os.path.isfile(path):
            try:
                manager.AddFont(path)
            except Exception:  # noqa: BLE001 - it's then still missing, and said so
                pass
    return missing(manager.GetFontList() or {})
