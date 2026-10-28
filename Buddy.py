#!/usr/bin/env python3
r"""
Launcher stub for DaVinci Resolve's Workspace > Scripts menu.

Runs Buddy straight out of "buddy.zip", which must sit next to this file -
so the whole toolkit is just these two files, and moving/copying them
together (to another machine, another Scripts folder, etc.) is enough, no
dev folder required. The pieces below deal with Resolve running a menu
script through exec() without __file__, with there being no console to
show errors in, and with keying the extraction cache on the zip's
size+mtime.

One app holds every tool, so Resolve's Scripts menu gets a single "Buddy"
entry instead of one per tool.

Buddy connects to Resolve's scripting API in-process (see
core/resolve_bridge.py, which probes reachability in a disposable
subprocess first).
"""

import hashlib
import inspect
import os
import re
import shutil
import sys
import tempfile
import traceback
import zipfile

APP_NAME = "Buddy"
ZIP_NAME = "buddy.zip"
PACKAGE_DIR_NAME = "buddy"

LOG_PATH = os.path.join(tempfile.gettempdir(), "buddy_launch.log")

# Last-resort fallback if even the frame/co_filename trick below comes up
# empty - the well-known location this launcher gets deployed to.
_FALLBACK_SCRIPT_PATH = os.path.expandvars(
    r"%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts"
    r"\Utility\Buddy.py"
)


def _log(message):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass


def _show_fatal_error(error_text):
    _log("FATAL:\n" + error_text)
    message = error_text + f"\n\n(Also written to: {LOG_PATH})"
    if sys.platform == "darwin":
        # Resolve shows a script's output nowhere, so without this a failed
        # launch just looks like nothing happened. Popen, not run: the
        # alert stays up on its own while this script exits. The texts go
        # in as arguments, never spliced into the AppleScript itself.
        try:
            import subprocess

            subprocess.Popen([
                "/usr/bin/osascript",
                "-e", "on run argv",
                "-e", "display alert (item 1 of argv) message (item 2 of argv) as critical",
                "-e", "end run",
                f"{APP_NAME} - Error", message,
            ])
            return
        except Exception:
            pass
    try:
        import ctypes

        MB_ICONERROR = 0x10
        ctypes.windll.user32.MessageBoxW(
            0, message, f"{APP_NAME} - Error", MB_ICONERROR
        )
    except Exception:
        print(message)


def _this_script_path():
    try:
        return os.path.abspath(__file__)
    except NameError:
        pass

    try:
        frame = inspect.currentframe()
        path = frame.f_code.co_filename
        if path and os.path.exists(path):
            return os.path.abspath(path)
    except Exception:
        pass

    if os.path.exists(_FALLBACK_SCRIPT_PATH):
        return _FALLBACK_SCRIPT_PATH

    raise RuntimeError(
        "Could not determine this script's own file path (no __file__, no "
        "usable frame filename, and the well-known fallback location doesn't "
        "exist either)."
    )


def _extracted_app_dir(zip_path):
    if not os.path.exists(zip_path):
        raise RuntimeError(
            f"Could not find {ZIP_NAME} next to this script.\n"
            f"Expected it at: {zip_path}\n"
            f"Make sure the zip and '{APP_NAME}.py' are copied into the same "
            f"folder."
        )

    stat = os.stat(zip_path)
    cache_key = hashlib.md5(
        f"{stat.st_size}-{stat.st_mtime_ns}".encode()
    ).hexdigest()[:16]
    cache_dir = os.path.join(tempfile.gettempdir(), APP_NAME, cache_key)
    done_marker = os.path.join(cache_dir, ".extracted")

    # Keyed on the zip's size+mtime, so shipping a new zip extracts fresh
    # while relaunching the same one costs nothing.
    if not os.path.exists(done_marker):
        os.makedirs(cache_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(cache_dir)
        with open(done_marker, "w", encoding="utf-8") as handle:
            handle.write("ok")

    _hold(cache_dir)
    _remove_old_versions(cache_dir)
    return os.path.join(cache_dir, PACKAGE_DIR_NAME)


# Old extractions pile up otherwise - one folder per zip ever launched, ~3 MB
# each. A running Buddy keeps this file open in its own folder for as long as
# it runs. Windows won't rename a folder with a file open inside it: so an
# old folder is renamed out of the way first, and only deleted if that
# worked. A Mac renames and deletes open files without a word, so there the
# file is also locked (flock) while Buddy runs, and a folder whose lock is
# taken is left. A copy of Buddy still running from an older version (in the
# tray or menu bar while its zip was replaced) keeps its folder - it imports
# pages and runs Transcribe's worker from there long after starting.
_IN_USE_NAME = ".in_use"
_in_use_handle = None
_VERSION_DIR = re.compile(r"[0-9a-f]{16}(\.deleting)?")


def _hold(cache_dir):
    global _in_use_handle
    try:
        _in_use_handle = open(os.path.join(cache_dir, _IN_USE_NAME), "a")
    except OSError:
        return
    if os.name != "nt":
        import fcntl
        try:
            fcntl.flock(_in_use_handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            pass


def _locked(folder):
    """True if a running Buddy holds `folder`'s lock (macOS: see above).
    Windows' own refusal to rename does this there."""
    if os.name == "nt":
        return False
    import fcntl
    try:
        handle = open(os.path.join(folder, _IN_USE_NAME), "a")
    except OSError:
        return False     # none (nobody ever ran it) or unreadable: the rename decides
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        handle.close()   # closing lets the lock go
    return False


def _remove_old_versions(keep_dir):
    """Deletes the other extracted versions nobody is running. Only folders
    named like this stub's own cache keys are touched."""
    parent = os.path.dirname(keep_dir)
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in names:
        path = os.path.join(parent, name)
        if path == keep_dir or not _VERSION_DIR.fullmatch(name) or not os.path.isdir(path):
            continue
        if _locked(path):
            continue       # a running Buddy's (on a Mac, where the rename below would work)
        doomed = path if name.endswith(".deleting") else path + ".deleting"
        if doomed != path:
            try:
                os.rename(path, doomed)
            except OSError:
                continue   # in use by a running Buddy (or otherwise locked): left alone
        shutil.rmtree(doomed, ignore_errors=True)


# Resolve runs this script with the newest Python on the PC (on Windows,
# whatever "py -3" finds), so installing a newer Python - which can happen
# without the user noticing - leaves Buddy on one with none of its packages.
# Instead of failing on the first missing import, the launcher checks the
# list the zip carries (installer/requirements.txt, put in by
# build_buddy_zip.py) and offers to install what's missing into that Python,
# the way the installer does: -I, since Resolve runs Python isolated and
# ignores per-user packages.
REQUIREMENTS_NAME = "requirements.txt"


def _marker_applies(marker):
    """Only sys_platform markers are written in requirements.txt."""
    found = re.search(r"""sys_platform\s*(==|!=)\s*["']([^"']+)["']""", marker or "")
    if not found:
        return True
    return (sys.platform == found.group(2)) == (found.group(1) == "==")


def _missing_packages(requirements_path, version_of=None):
    """The requirements ("numpy>=1.26") this Python has no copy of at all. A
    copy that's too old still runs Buddy - the installer and the updater
    deal with those."""
    if version_of is None:
        from importlib import metadata

        def version_of(name):
            try:
                return metadata.version(name)
            except metadata.PackageNotFoundError:
                return None
    with open(requirements_path, encoding="utf-8") as handle:
        text = handle.read()
    missing = []
    for raw in text.splitlines():
        spec, _, marker = raw.split("#", 1)[0].strip().partition(";")
        name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec.strip())
        if name and _marker_applies(marker) and version_of(name.group()) is None:
            missing.append(spec.replace(" ", ""))
    return missing


def _real_python():
    """The Python Buddy is running on, as a program pip can run from -
    sys.executable is Resolve's script host (fuscript), which can't. None
    if it can't be found."""
    try:
        from core.python_exe import standalone_python

        python = standalone_python()
    except Exception:
        python = sys.executable
    if os.path.basename(python).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(python), "python.exe")
        python = console if os.path.isfile(console) else python
    return python if os.path.basename(python).lower().startswith("python") else None


def _install_command(python, missing):
    """What the user can run themselves - the packages spelled out, since the
    extracted requirements.txt sits in a temp folder."""
    shell = "Terminal" if sys.platform == "darwin" else "Command Prompt"
    packages = " ".join(f'"{spec}"' for spec in missing)
    return (f"To install them yourself, run this in {shell}:\n"
            f'"{python}" -I -m pip install {packages}')


def _can_write(folder):
    """Whether pip can install into `folder` without admin rights - tried,
    since os.access doesn't read Windows' permissions."""
    try:
        handle, path = tempfile.mkstemp(dir=folder)
        os.close(handle)
        os.remove(path)
        return True
    except OSError:
        return False


def _ask(title, text):
    """A yes/no question before any of Buddy's windows can open (they need
    PySide6). True for "Install"."""
    if sys.platform == "darwin":
        import subprocess

        try:
            answer = subprocess.run([
                "/usr/bin/osascript",
                "-e", "on run argv",
                "-e", 'display dialog (item 2 of argv) with title (item 1 of argv) '
                      'buttons {"Not Now", "Install"} default button "Install" with icon caution',
                "-e", "end run",
                title, text,
            ], capture_output=True, text=True)
        except OSError:
            return False
        return "button returned:Install" in answer.stdout
    import ctypes

    MB_YESNO, MB_ICONWARNING, IDYES = 0x4, 0x30, 6
    return ctypes.windll.user32.MessageBoxW(0, text, title, MB_YESNO | MB_ICONWARNING) == IDYES


def _run_windows(program, args, as_admin):
    """Runs `program` in a console window of its own, so pip's progress shows,
    and waits for it. As admin, Windows asks for permission first. The exit
    code, or None if it didn't start (permission refused)."""
    import ctypes
    import subprocess
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    SEE_MASK_NOCLOSEPROCESS, SW_SHOWNORMAL, INFINITE = 0x40, 1, 0xFFFFFFFF
    # Own instances, so these argtypes don't change anyone else's calls.
    shell32, kernel32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("kernel32")
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    info = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=SEE_MASK_NOCLOSEPROCESS,
                             lpVerb="runas" if as_admin else "open", lpFile=program,
                             lpParameters=subprocess.list2cmdline(args), nShow=SW_SHOWNORMAL)
    if not shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return None
    try:
        kernel32.WaitForSingleObject(info.hProcess, INFINITE)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)


def _run_mac(command):
    """Runs pip with an alert up meanwhile (a menu script has no window to
    show progress in); its output goes to the launch log. The exit code."""
    import subprocess

    notice = None
    try:
        notice = subprocess.Popen([
            "/usr/bin/osascript", "-e", "on run argv",
            "-e", "display alert (item 1 of argv) message (item 2 of argv)",
            "-e", "end run",
            f"{APP_NAME} is installing its packages",
            "This can take a few minutes. Buddy opens when it's done.",
        ])
    except OSError:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as log:
            return subprocess.run(command, stdout=log, stderr=subprocess.STDOUT).returncode
    finally:
        if notice is not None:
            notice.terminate()


def _repair_packages(app_dir):
    """Offers to install the packages this Python is missing, then installs
    them. Returns quietly when nothing is missing (or the zip carries no
    list); raises with what to do when they're still missing."""
    requirements_path = os.path.join(app_dir, REQUIREMENTS_NAME)
    if not os.path.isfile(requirements_path):
        return
    missing = _missing_packages(requirements_path)
    if not missing:
        return
    _log("missing packages: " + ", ".join(missing))
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    python = _real_python()
    if python is None:
        raise RuntimeError(
            f"Python {version}, which DaVinci Resolve is running {APP_NAME} with, doesn't have "
            f"these packages {APP_NAME} needs:\n{', '.join(missing)}\n\n"
            "Run the Buddy installer again to install them."
        )

    import sysconfig

    as_admin = sys.platform == "win32" and not _can_write(sysconfig.get_paths()["purelib"])
    if sys.platform == "darwin":
        how = "Buddy opens when it's done."
    elif as_admin:
        how = ("Windows will ask for permission first, then a window shows the "
               "progress. Buddy opens when it's done.")
    else:
        how = "A window shows the progress, and Buddy opens when it's done."
    question = (
        f"DaVinci Resolve is now running {APP_NAME} with Python {version} ({python}), "
        f"which doesn't have these packages {APP_NAME} needs:\n\n"
        f"{', '.join(missing)}\n\n"
        "This usually means a newer Python was installed. Install them now? "
        f"It can take a few minutes. {how}"
    )
    if not _ask(f"{APP_NAME} - Missing packages", question):
        _log("package install declined")
        raise RuntimeError(
            f"{APP_NAME} can't start without these packages for Python {version}:\n"
            f"{', '.join(missing)}\n\n" + _install_command(python, missing)
        )

    args = ["-I", "-m", "pip", "install", "--disable-pip-version-check",
            "--no-warn-script-location", "-r", requirements_path]
    _log(f"installing with {python} (admin: {as_admin})")
    if sys.platform == "darwin":
        code = _run_mac([python] + args + ["--progress-bar", "off"])
    else:
        code = _run_windows(python, args, as_admin)
    _log(f"pip exit code: {code}")

    import importlib

    importlib.invalidate_caches()
    still_missing = _missing_packages(requirements_path)
    if still_missing:
        reason = ("Windows' permission request was turned down." if code is None
                  else f"pip stopped with an error (exit code {code}).")
        raise RuntimeError(
            f"{APP_NAME}'s packages couldn't be installed for Python {version}. {reason}\n\n"
            + _install_command(python, still_missing)
        )


def _run():
    script_path = _this_script_path()
    zip_path = os.path.join(os.path.dirname(script_path), ZIP_NAME)

    _log(
        f"--- launch attempt ---\n"
        f"python: {sys.executable}\n"
        f"version: {sys.version}\n"
        f"script: {script_path}\n"
    )

    app_dir = _extracted_app_dir(zip_path)
    _log(f"app_dir: {app_dir}")
    # Which zip this is - what an update replaces (core/updater.py).
    os.environ["BUDDY_ZIP"] = zip_path
    # Buddy's modules import each other absolutely (core.*, pages.*,
    # registry), so the extracted app folder itself goes on sys.path -
    # not its parent.
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    _repair_packages(app_dir)

    try:
        import PySide6  # noqa: F401
    except ImportError as exc:
        python = _real_python() or sys.executable
        raise RuntimeError(
            "PySide6 is not installed for the Python interpreter DaVinci "
            f"Resolve is using to run this script ({python}).\n\n"
            "Install it for that exact interpreter, e.g.:\n"
            f'"{python}" -I -m pip install PySide6\n\n'
            f"Original error: {exc}"
        ) from exc

    # Launched from Resolve's Scripts menu, Resolve injects a ready
    # `resolve` connection into this script's globals - on macOS the only
    # one the free Resolve allows (see core/resolve_bridge.py). Absent
    # otherwise.
    host_resolve = globals().get("resolve") if sys.platform == "darwin" else None
    if host_resolve is not None:
        from core import resolve_bridge

        resolve_bridge.set_host_resolve(host_resolve)

    import main

    main.main(start_hidden="--start-hidden" in sys.argv[1:])


try:
    _run()
except Exception:
    _show_fatal_error(traceback.format_exc())
