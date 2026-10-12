#!/usr/bin/env python3
"""
Buddy's own llama.cpp: models run on this machine without Ollama or any
other app. Ask Buddy's semantic search uses it for an embedding model
dropped in the models folder (embedder.py); the same server can run chat
models later.

No Qt in here - the Settings window runs install() on a thread, and search
calls LlamaServer.ensure() from the agent's worker thread.

  Runtime   One pinned llama.cpp release, the CPU build for this machine
            (Apple's builds use Metal too), from ggml-org's GitHub releases.
            Checked against its SHA-256 before it's unpacked, unpacked into
            a staging folder and renamed into place, so a failed or
            cancelled download never leaves half a runtime. A release an
            earlier Buddy installed keeps running EmbeddingGemma until Set
            up fetches this one (server_exe); only media search, whose
            model older releases can't read, waits for it (runtime_current).
            On a Windows PC with a graphics card of its own, media search
            can run on the card instead: the same release built for Vulkan
            (GPU_RUNTIMES), kept beside the CPU build, which stays what
            Ask Buddy's search runs on and what media search goes back to
            if the card can't run it.
  Models    GGUF files in MODELS_DIR - put there by hand, or the downloads
            offered: EmbeddingGemma, which the manual bundle's vectors are
            built with, and EmbeddingGemma 2 with its picture-and-sound
            half, which media search (core/media_search) runs.
  Server    llama-server, started on demand on 127.0.0.1 at a free port,
            with a random API key so nothing else on the machine (a web
            page included - it allows any origin) can use it. One per
            model; stopped after IDLE_SECONDS unused, when Buddy quits,
            and - on Windows - by the system if Buddy dies, through a job
            object that kills it with Buddy.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core import defender, model_folders

from .ask_folder import ASK_BUDDY_DIR

RELEASE = "b11559"
RELEASE_URL = "https://github.com/ggml-org/llama.cpp/releases/download/" + RELEASE + "/"

ROOT = Path(ASK_BUDDY_DIR)
RUNTIME_DIR = ROOT / "llama.cpp" / RELEASE
MODELS_DIR = model_folders.folder("manual_chat")    # ROOT/models, or one picked in Settings
LOG_PATH = ROOT / "llama.cpp" / "server.log"

IDLE_SECONDS = 15 * 60
START_TIMEOUT = 90          # a big model from a slow disk takes a while to load
CHUNK = 1 << 20

# Progress: (done bytes, total bytes, message).
Progress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class Download:
    url: str
    name: str               # the file's name where it's saved
    size: int               # bytes, as published
    sha256: str
    label: str = ""

    @property
    def mb(self) -> int:
        return round(self.size / 1e6)


# (platform.system(), platform.machine()) -> the release's archive for it.
RUNTIMES = {
    ("Windows", "AMD64"): Download(RELEASE_URL + f"llama-{RELEASE}-bin-win-cpu-x64.zip", f"llama-{RELEASE}-bin-win-cpu-x64.zip",
                                   19501338, "1228f133868c02a424bcf663a688e66d8c7d5499077649f305188f756f584377", "llama.cpp"),
    ("Windows", "ARM64"): Download(RELEASE_URL + f"llama-{RELEASE}-bin-win-cpu-arm64.zip", f"llama-{RELEASE}-bin-win-cpu-arm64.zip",
                                   12371743, "37fc76d2e63bf97adb710c43ef0b985159282a830909f67009ab983d28ada487", "llama.cpp"),
    ("Darwin", "arm64"): Download(RELEASE_URL + f"llama-{RELEASE}-bin-macos-arm64.tar.gz", f"llama-{RELEASE}-bin-macos-arm64.tar.gz",
                                  12105699, "c6cfeaa709b8d2e47e8a50f6833d22071f7ee7079b0b1a6d704e482afe6ee2c6", "llama.cpp"),
    ("Darwin", "x86_64"): Download(RELEASE_URL + f"llama-{RELEASE}-bin-macos-x64.tar.gz", f"llama-{RELEASE}-bin-macos-x64.tar.gz",
                                   11597115, "630d5ab669876748b4f715def6b1cbbc2a8cf836ca8310cb9e4585d5f61fe8d4", "llama.cpp"),
}

# The same release built for Vulkan, which runs a model on the graphics card
# whoever made it. Only media search uses it (gpu_server_exe): 13 times the
# CPU build's frames a second on an RTX 5070 Ti, the same vectors to three
# decimal places (measured 2026-10-12). x64 only - the one it's been run on.
GPU_RUNTIMES = {
    ("Windows", "AMD64"): Download(RELEASE_URL + f"llama-{RELEASE}-bin-win-vulkan-x64.zip",
                                   f"llama-{RELEASE}-bin-win-vulkan-x64.zip", 32084119,
                                   "bf2fb2a7fc5d7669ccabbb6de3f6dd6b54058f213b32e731abc62b590f8566dc",
                                   "llama.cpp for the graphics card"),
}
GPU_RUNTIME_DIR = ROOT / "llama.cpp" / (RELEASE + "-vulkan")
# A card of its own, by what its name says: the graphics built into a processor
# reports the PC's whole memory as its own and is no faster than the processor.
_OWN_CARDS = ("nvidia", "geforce", "rtx", "gtx", "quadro", "radeon rx", "radeon pro", "intel(r) arc", "arc(tm)")

# The embedding model the manual bundle is built with, as llama.cpp's own
# conversion (Ollama's copy is a GGUF llama.cpp can't load). Q8_0 matches the
# bundle's vectors at cosine 0.9996 - as close as int8 storage allows.
EMBEDDING_GEMMA = Download(
    "https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/resolve/main/embeddinggemma-300M-Q8_0.gguf",
    "embeddinggemma-300M-Q8_0.gguf", 333590944,
    "b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63", "EmbeddingGemma")

# Media search's model (core/media_search): text, pictures and sound in one
# vector space - not the manual's, so never what Ask Buddy's search is guessed
# to use (embedder.chosen_model_file). Its text half, and the half that reads
# pictures and sound, which llama.cpp loads beside it (--mmproj). Pinned to one
# revision of the repo, so the files can't change under their checksums.
_GEMMA_2_URL = "https://huggingface.co/ggml-org/embeddinggemma-2-GGUF/resolve/bfcd298762cc34d0357ece5ebdd31791a3a374d8/"
EMBEDDING_GEMMA_2 = Download(
    _GEMMA_2_URL + "embeddinggemma-2-Q8_0.gguf", "embeddinggemma-2-Q8_0.gguf", 309855456,
    "2188ac1deca4b77dffefd603c2776a9d76d9d74ec01841392982ebb840b09135", "EmbeddingGemma 2")
EMBEDDING_GEMMA_2_MEDIA = Download(
    _GEMMA_2_URL + "mmproj-embeddinggemma-2-Q8_0.gguf", "mmproj-embeddinggemma-2-Q8_0.gguf", 554821024,
    "c4a8a52691ecef40618438928bdf9e68379b854e24166f292592353db0aab64f", "EmbeddingGemma 2's picture and sound half")
MEDIA_SEARCH_FILES = (EMBEDDING_GEMMA_2, EMBEDDING_GEMMA_2_MEDIA)


class LlamaError(Exception):
    """Something the person can act on, worded for them."""


class Cancelled(Exception):
    pass


def _machine() -> tuple[str, str]:
    """("Windows", "AMD64" | "ARM64") or ("Darwin", "arm64" | "x86_64").
    An x64 Python on an ARM PC says AMD64 - and the x64 build suits it, as
    that's what it runs under."""
    system, machine = platform.system(), platform.machine()
    return system, machine.upper() if system == "Windows" else machine


def runtime_download() -> Download | None:
    """The llama.cpp archive for this machine, or None where there's none."""
    return RUNTIMES.get(_machine())


def _exe_in(folder: Path) -> Path | None:
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    if not folder.is_dir():
        return None
    for path in (folder / name, *folder.glob(f"*/{name}")):
        if path.is_file():
            return path
    return None


def _older_runtimes() -> list[Path]:
    """Release folders (b11312) an earlier Buddy left beside this one's, newest first."""
    try:
        found = [p for p in RUNTIME_DIR.parent.iterdir()
                 if p.is_dir() and p != RUNTIME_DIR and re.fullmatch(r"b\d+", p.name)]
    except OSError:
        return []
    return sorted(found, key=lambda p: int(p.name[1:]), reverse=True)


def server_exe() -> Path | None:
    """llama-server, if a runtime is installed: this release's, else the one
    an earlier Buddy installed - an update never takes semantic search away."""
    for folder in (RUNTIME_DIR, *_older_runtimes()):
        exe = _exe_in(folder)
        if exe:
            return exe
    return None


def gpu_runtime_download() -> Download | None:
    """The Vulkan build's archive for this machine, or None where there's none."""
    return GPU_RUNTIMES.get(_machine())


def gpu_server_exe() -> Path | None:
    """The Vulkan build's llama-server, if it's installed."""
    return _exe_in(GPU_RUNTIME_DIR)


_GPU_DEVICE = {}        # the Vulkan build's exe -> (device, its name), asked once


def pick_device(listing: str) -> tuple[str, str] | None:
    """(device, name) of the graphics card to run on, from llama-server
    --list-devices' output ("  Vulkan0: NVIDIA GeForce RTX 5070 Ti (15995
    MiB, 15227 MiB free)") - the first that's a card of its own; None when
    there's only the processor's built-in graphics."""
    for line in listing.splitlines():
        found = re.match(r"\s*(Vulkan\d+):\s*(.+?)\s*\(\d+ MiB", line)
        if found and any(word in found.group(2).lower() for word in _OWN_CARDS):
            return found.group(1), found.group(2)
    return None


def gpu_device() -> tuple[str, str] | None:
    """(device, name) the Vulkan build would run on here, or None: not
    installed, or no graphics card of its own. Asked of it once (a second or so)."""
    exe = gpu_server_exe()
    if exe is None:
        return None
    if str(exe) not in _GPU_DEVICE:
        try:
            flags = 0x08000000 if os.name == "nt" else 0               # CREATE_NO_WINDOW
            done = subprocess.run([str(exe), "--list-devices"], cwd=str(exe.parent), capture_output=True, timeout=10,
                                  stdin=subprocess.DEVNULL, creationflags=flags)
            _GPU_DEVICE[str(exe)] = pick_device((done.stdout + done.stderr).decode("utf-8", "replace"))
        except (OSError, subprocess.SubprocessError):
            _GPU_DEVICE[str(exe)] = None
    return _GPU_DEVICE[str(exe)]


def runtime_dir() -> Path | None:
    """The release folder server_exe() runs from, or None."""
    return next((folder for folder in (RUNTIME_DIR, *_older_runtimes()) if _exe_in(folder)), None)


def runtime_current() -> bool:
    """Whether the installed runtime is this release - what media search's
    model needs; an older one can't read it."""
    return _exe_in(RUNTIME_DIR) is not None


def model_files() -> list[Path]:
    """The GGUF files in the models folder, by name - in it, or up to two
    folders down, as another app shares its models (LM Studio's are in
    publisher/model/). Split models' later parts (-00002-of-00003.gguf)
    aren't listed - llama.cpp reads them through the first."""
    if not MODELS_DIR.is_dir():
        return []
    files = []
    for path in (p for pattern in ("*.gguf", "*/*.gguf", "*/*/*.gguf") for p in MODELS_DIR.glob(pattern)):
        part = re.search(r"-(\d{5})-of-\d{5}$", path.stem)
        # A vision model's projector (mmproj-...) isn't a model to run on its own.
        if not (part and part.group(1) != "00001") and not path.name.lower().startswith("mmproj") and is_gguf(path):
            files.append(path)
    return sorted(files, key=lambda p: p.name.lower())


def is_gguf(path: Path) -> bool:
    """A GGUF file by its content, not just its name: one starts with the
    bytes GGUF. GGUF holds weights as data - unlike PyTorch's .bin/.pt
    pickles, loading one runs no code from it - so only GGUF is ever run."""
    try:
        with open(path, "rb") as f:
            return path.is_file() and f.read(4) == b"GGUF"
    except OSError:
        return False


def own_models(folder: Path, own_folder: bool) -> list[Path]:
    """What a change of models folder moves (core/model_folders.py): from
    Buddy's own folder every model file in it - each was put there for
    Buddy - and from one picked to share with other apps only Buddy's
    download, leaving theirs."""
    names = {EMBEDDING_GEMMA.name, *(item.name for item in MEDIA_SEARCH_FILES)}
    out = [p for p in folder.glob("*.gguf") if own_folder or p.name in names]
    return out + [p for p in (folder / VERIFIED_FILE,) if own_folder and p.exists()]


def ensure_models_dir() -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return MODELS_DIR


# (path, size, mtime, sha256) -> whether that file is the download with that SHA-256. Kept
# between runs in a small file beside the models, so a model is hashed once - not each time
# Buddy starts (a 334 MB EmbeddingGemma takes a second or so, cold).
_VERIFIED = {}
VERIFIED_FILE = ".verified.json"
_LOADED = set()     # folders whose VERIFIED_FILE has been read


def _verified_key(path: Path, stat, item: Download) -> tuple:
    return str(path), stat.st_size, stat.st_mtime_ns, item.sha256


def _load_verified(folder: Path):
    if str(folder) in _LOADED:
        return
    _LOADED.add(str(folder))
    try:
        rows = json.loads((folder / VERIFIED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, list) and len(row) == 5:
            _VERIFIED.setdefault(tuple(row[:4]), bool(row[4]))


def _remember_verified(path: Path, item: Download, ok: bool):
    """Notes that path (as it is now) is, or isn't, item's download."""
    try:
        stat = path.stat()
    except OSError:
        return
    folder = path.parent
    _load_verified(folder)
    _VERIFIED[_verified_key(path, stat, item)] = ok
    rows = [[*key, value] for key, value in _VERIFIED.items() if os.path.dirname(key[0]) == str(folder)]
    try:
        (folder / VERIFIED_FILE).write_text(json.dumps(rows), encoding="utf-8")
    except OSError:
        pass                        # it's hashed again next time, that's all


def pinned_copy_known(path: Path, item: Download = None):
    """is_pinned_copy's answer if it needs no hashing - from the size, or a
    check already made of the file as it is now - else None."""
    item = item or EMBEDDING_GEMMA
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size != item.size:
        return False
    _load_verified(path.parent)
    return _VERIFIED.get(_verified_key(path, stat, item))


def is_pinned_copy(path: Path, item: Download = None) -> bool:
    """Whether a file is exactly the pinned download (EmbeddingGemma by
    default): its size, then its SHA-256 - hashed once per change of the
    file, then remembered (pinned_copy_known). Seconds for a big file the
    first time: not on the UI thread."""
    item = item or EMBEDDING_GEMMA
    known = pinned_copy_known(path, item)
    if known is not None:
        return known
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(CHUNK * 8), b""):
                digest.update(block)
    except OSError:
        return False
    ok = digest.hexdigest() == item.sha256
    _remember_verified(path, item, ok)
    return ok


def folder_size(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


# ------------------------------------------------------------- downloads


def download(item: Download, dest: Path, progress: Progress | None = None,
             cancelled: Callable[[], bool] | None = None) -> Path:
    """item into dest (a file path), checked against its SHA-256. Written
    beside it as .part and renamed only once it's whole and right."""
    progress = progress or (lambda *_a: None)
    cancelled = cancelled or (lambda: False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    label = item.label or item.name
    try:
        req = urllib.request.Request(item.url, headers={"User-Agent": "Buddy"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or item.size)
            while True:
                if cancelled():
                    raise Cancelled()
                block = resp.read(CHUNK)
                if not block:
                    break
                out.write(block)
                digest.update(block)
                done += len(block)
                progress(done, total, f"Downloading {label}… {done / 1e6:.0f} of {total / 1e6:.0f} MB")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        part.unlink(missing_ok=True)
        raise LlamaError(f"Couldn't download {label}: {exc}") from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != item.sha256:
        part.unlink(missing_ok=True)
        raise LlamaError(f"The {label} download didn't match its published checksum, so it wasn't used. "
                         "Try again - if it keeps happening, the file on the server has changed.")
    os.replace(part, dest)
    if dest.suffix.lower() == ".gguf":
        _remember_verified(dest, item, True)     # checked on the way in: Settings needn't hash it
    return dest


SCAN_CLEAN, SCAN_THREAT, SCAN_SKIPPED = defender.CLEAN, defender.THREAT, defender.SKIPPED


def defender_scan(folder: Path) -> tuple[str, str]:
    """Windows Defender's verdict on the unpacked runtime (core/defender.py)."""
    return defender.scan(folder, "llama.cpp")


def install_runtime(progress: Progress | None = None, cancelled: Callable[[], bool] | None = None) -> tuple[Path, str]:
    """Downloads, checks and unpacks llama.cpp for this machine: (its
    llama-server, what Windows Defender said of it)."""
    exe = _exe_in(RUNTIME_DIR)
    if exe:
        return exe, ""
    item = runtime_download()
    if item is None:
        raise LlamaError(f"There's no llama.cpp build for this computer ({' '.join(_machine())}).")
    exe, note = _install(item, RUNTIME_DIR, progress, cancelled)
    stop_all()                                  # one still running holds an older release's files open
    for folder in _older_runtimes():
        shutil.rmtree(folder, ignore_errors=True)
    return exe, note


def install_gpu_runtime(progress: Progress | None = None, cancelled: Callable[[], bool] | None = None) -> tuple[Path, str]:
    """The same for the Vulkan build, beside the CPU one."""
    exe = gpu_server_exe()
    if exe:
        return exe, ""
    item = gpu_runtime_download()
    if item is None:
        raise LlamaError(f"There's no llama.cpp build for this computer's graphics card ({' '.join(_machine())}).")
    exe, note = _install(item, GPU_RUNTIME_DIR, progress, cancelled)
    for folder in GPU_RUNTIME_DIR.parent.glob("b*-vulkan"):         # an earlier release's
        if folder != GPU_RUNTIME_DIR:
            shutil.rmtree(folder, ignore_errors=True)
    return exe, note


def _install(item: Download, target: Path, progress: Progress | None, cancelled) -> tuple[Path, str]:
    """A llama.cpp archive downloaded, checked, scanned and unpacked into
    `target`: (its llama-server, what Windows Defender said of it)."""
    progress = progress or (lambda *_a: None)
    archive = download(item, target.parent / item.name, progress, cancelled)
    staging = target.with_name(target.name + ".unpacking")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(staging)
        else:
            with tarfile.open(archive) as t:
                # Its symlinks (libllama.dylib -> libllama.0.5.0.dylib) stay links,
                # and nothing may land outside the folder.
                if hasattr(tarfile, "data_filter"):
                    t.extractall(staging, filter="data")
                else:
                    safe = [m for m in t.getmembers()
                            if not (m.name.startswith(("/", "..")) or "/../" in m.name)]
                    t.extractall(staging, members=safe)
        progress(0, 0, "Checking llama.cpp with Windows Defender…" if os.name == "nt" else "Unpacking llama.cpp…")
        verdict, note = defender_scan(staging)
        if verdict == SCAN_THREAT:
            raise LlamaError("Windows Defender flagged the llama.cpp download, so it was deleted and won't be used. "
                             "Nothing was run.")
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise LlamaError(f"Couldn't unpack llama.cpp: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        archive.unlink(missing_ok=True)
    exe = _exe_in(target)
    if exe is None:
        raise LlamaError("llama.cpp was unpacked, but its llama-server wasn't in it.")
    if os.name != "nt":
        for path in exe.parent.iterdir():
            if path.is_file() and not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o111)
    return exe, note


# ---------------------------------------------------------------- server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _KillWithBuddy:
    """A Windows job object that ends every process in it when Buddy's
    handle to it closes - when Buddy quits or crashes alike."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.OpenProcess.restype = wintypes.HANDLE
            handle = k32.CreateJobObjectW(None, None)
            if not handle:
                return

            class _Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class _Io(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                            "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class _Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            info = _Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if k32.SetInformationJobObject(wintypes.HANDLE(handle), 9, ctypes.byref(info), ctypes.sizeof(info)):
                self.handle, self._k32 = handle, k32
        except (OSError, AttributeError):
            self.handle = None

    def add(self, pid: int):
        if not self.handle:
            return
        from ctypes import wintypes
        process = self._k32.OpenProcess(0x0101, False, pid)    # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if process:
            self._k32.AssignProcessToJobObject(wintypes.HANDLE(self.handle), wintypes.HANDLE(process))
            self._k32.CloseHandle(wintypes.HANDLE(process))


_JOB = None
_LOCK = threading.Lock()
_SERVERS: dict[tuple, "LlamaServer"] = {}


class LlamaServer:
    """llama-server for one model, started when first needed."""

    def __init__(self, model: Path, embedding: bool = True, extra: tuple[str, ...] = (), exe: Path | None = None):
        self.model = Path(model)
        self.embedding = embedding
        self.extra = tuple(extra)
        self.exe = Path(exe) if exe else None       # another build's llama-server (the Vulkan one); None: server_exe()
        self.key = secrets.token_urlsafe(24)
        self.port = 0
        self.process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._used = 0.0
        self._timer: threading.Timer | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def ensure(self, timeout: float = START_TIMEOUT) -> str:
        """Its base URL, starting it (and waiting until the model is loaded)
        if it isn't running."""
        with self._lock:
            self._touch()
            if self.running():
                return self.base_url
            exe = self.exe if self.exe is not None and self.exe.is_file() else server_exe()
            if exe is None:
                raise LlamaError("llama.cpp isn't set up yet – Settings > AI > Manual search > Set up.")
            if not self.model.is_file():
                raise LlamaError(f"The model file isn't there any more: {self.model}")
            if not is_gguf(self.model):
                raise LlamaError(f"{self.model.name} isn't a GGUF model, so it wasn't run.")
            self.port = _free_port()
            args = [str(exe), "-m", str(self.model), "--host", "127.0.0.1", "--port", str(self.port),
                    "--api-key", self.key, "--no-webui", "--offline"]
            if self.embedding:
                # Four inputs at a time, each up to the 2,048 tokens EmbeddingGemma
                # reads; a whole input in one physical batch, as embeddings need.
                args += ["--embedding", "-c", "8192", "-np", "4", "-b", "2048", "-ub", "2048"]
            args += list(self.extra)
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            log = open(LOG_PATH, "ab")
            log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {self.model.name} ---\n".encode())
            log.flush()
            flags = 0x08000000 if os.name == "nt" else 0               # CREATE_NO_WINDOW
            try:
                self.process = subprocess.Popen(args, cwd=str(exe.parent), stdin=subprocess.DEVNULL, stdout=log,
                                                stderr=subprocess.STDOUT, creationflags=flags)
            except OSError as exc:
                raise LlamaError(f"llama.cpp wouldn't start: {exc}") from exc
            finally:
                log.close()
            global _JOB
            if _JOB is None:
                _JOB = _KillWithBuddy()
            _JOB.add(self.process.pid)
            self._wait_ready(timeout)
            return self.base_url

    def _wait_ready(self, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.process = None
                raise LlamaError(f"llama.cpp stopped while loading {self.model.name} – is it a GGUF model it can "
                                 f"read? Its log: {LOG_PATH}")
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{self.port}/health",
                                             headers={"Authorization": f"Bearer {self.key}"})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        return
            except urllib.error.HTTPError as exc:
                if exc.code != 503:                 # 503: still loading the model
                    break
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            time.sleep(0.25)
        self.stop()
        raise LlamaError(f"llama.cpp didn't get {self.model.name} ready in {timeout:.0f} s. Its log: {LOG_PATH}")

    def post(self, path: str, body: dict, timeout: float) -> dict:
        """A request to it, starting it first if need be."""
        base = self.ensure()
        req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LlamaError(f"llama.cpp answered {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            raise LlamaError(f"llama.cpp stopped answering: {exc}") from exc
        finally:
            self._touch()

    def _touch(self):
        """Used just now: it stops IDLE_SECONDS from now unless used again."""
        self._used = time.monotonic()
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(IDLE_SECONDS, self._idle)
        self._timer.daemon = True
        self._timer.start()

    def _idle(self):
        if time.monotonic() - self._used >= IDLE_SECONDS - 1:
            self.stop()

    def stop(self):
        process, self.process = self.process, None
        if self._timer is not None:
            self._timer.cancel()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def server_for(model: Path, embedding: bool = True, mmproj: Path | None = None, gpu: bool = False) -> LlamaServer:
    """The one server for this model, made the first time it's asked for.
    mmproj: the file that lets it read pictures and sound, loaded beside it.
    gpu: run by the Vulkan build on the graphics card gpu_device() names -
    another server than the CPU build's for the same model."""
    key = (str(Path(model).resolve()), embedding, str(Path(mmproj).resolve()) if mmproj else "", bool(gpu))
    with _LOCK:
        server = _SERVERS.get(key)
        if server is None:
            extra = ("--mmproj", str(mmproj)) if mmproj else ()
            exe = None
            if gpu:
                device, exe = gpu_device(), gpu_server_exe()
                if device is None or exe is None:
                    raise LlamaError("There's no graphics card set up to run it on.")
                # Every layer on that one card - not shared out over the processor's graphics too.
                extra += ("-ngl", "99", "--device", device[0])
            server = _SERVERS[key] = LlamaServer(Path(model), embedding, extra, exe)
        return server


def stop_model(model: Path):
    """Stops every server running this model - the CPU build's and the graphics card's."""
    with _LOCK:
        servers = [s for s in _SERVERS.values() if s.model == Path(model)]
    for server in servers:
        server.stop()


def stop_all():
    with _LOCK:
        servers = list(_SERVERS.values())
    for server in servers:
        server.stop()


atexit.register(stop_all)


if __name__ == "__main__":      # py -m pages.manual_chat.local_llama: what's set up
    print("runtime:", server_exe() or "not installed", "| for this machine:", runtime_download())
    print("models:", [p.name for p in model_files()], "in", MODELS_DIR)
    sys.exit(0)
