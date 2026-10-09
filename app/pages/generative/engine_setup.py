#!/usr/bin/env python3
"""
Generative's engine: a private Python environment with NumPy, OpenCV and
ONNX Runtime, and the models its tabs use. No Qt here - the page runs
install() on a thread.

Like Transcribe's (pages/transcribe/env_setup.py, whose helpers this
uses), the environment lives apart from the Python running Buddy, under
~/.buddy/generative/venv, built from that same Python, and Buddy talks to
it only through worker.py run there.

Models, each downloaded when a tab first needs it:
  lama            Clean Up's removal (fill.py). 93 MB.
  birefnet-lite   Cut Out and Separate's subject finder (matte.py),
                  Standard: 115 MB, ~3 s a picture on a desktop CPU.
  birefnet        the same, Best: 490 MB, ~7 s, finer hair.

Everything downloaded is pinned and checked (tools/pin_generative.py
writes the package pins and checks these):
  Packages  locks/engine.txt: exact versions with every wheel's SHA-256,
            installed with --require-hashes --only-binary :all: --no-deps,
            then scanned by Windows Defender (a flagged one is deleted).
  Models    MODELS below: one file from one commit of its repo, its size
            and SHA-256. Downloaded beside where it goes, checked, and only
            then moved in.

Everything runs on the CPU unless an add-on says otherwise: the graphics
card add-on (TensorRT for RTX, for the ONNX models) and the High quality
add-on (PyTorch, for FLUX.2 klein and ROSE) - both NVIDIA RTX on Windows.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

from core import model_folders, tool_dirs
from pages.transcribe import env_setup as tes
from pages.transcribe.env_setup import SetupCancelled, SetupError  # noqa: F401 - the page catches these

ROOT = Path(tool_dirs.buddy_dir()) / "generative"
VENV_DIR = ROOT / "venv"
MODELS_DIR = model_folders.folder("generative")     # ROOT/models, or one picked in Settings
WORK_DIR = ROOT / "work"
WORKER = Path(__file__).with_name("worker.py")
LOCK = Path(__file__).with_name("locks") / "engine.txt"
STAMP_NAME = "buddy-lock.json"
PACKAGES = ["numpy", "opencv-python-headless", "onnxruntime"]
ENV_MB = 70                     # the packages, about

MODELS = {
    # LaMa (Samsung AI Center, Apache 2.0), as OpenCV's model zoo publishes
    # it: int8 weights, the same fills as the 208 MB float export (checked
    # side by side), half the download.
    "lama": {
        "label": "LaMa (OpenCV zoo, int8)", "use": "Clean Up, Separate", "license": "Apache 2.0",
        "repo": "opencv/inpainting_lama", "revision": "aee6d22f0a13e5e35af1c9a1c3afd62841fc6f3f",
        "file": "inpainting_lama_2025jan.onnx", "name": "inpainting_lama_2025jan.onnx",
        "size": 92591623, "sha256": "7df918ac3921d3daf0aae1d219776cf0dc4e4935f035af81841b40adcf74fdf2",
    },
    # BiRefNet (Zheng Peng et al., MIT), onnx-community's half-precision
    # exports - the same mattes as their full-precision ones, half the size.
    "birefnet-lite": {
        "label": "BiRefNet lite", "use": "Cut Out, Separate – Standard", "license": "MIT",
        "repo": "onnx-community/BiRefNet_lite-ONNX", "revision": "de15b22ba131738a16dff04aab8bdf8dc32e3ac1",
        "file": "onnx/model_fp16.onnx", "name": "birefnet_lite_fp16.onnx",
        "size": 114538221, "sha256": "d39b897ceb16ae654c1731f3dba0cf9b368d9cae74b5a57459b455cc8bfec402",
    },
    "birefnet": {
        "label": "BiRefNet", "use": "Cut Out, Separate – Best", "license": "MIT",
        "repo": "onnx-community/BiRefNet-ONNX", "revision": "534d3c82d3bb8b2f0867db6dfbc3a525b8e42f67",
        "file": "onnx/model_fp16.onnx", "name": "birefnet_fp16.onnx",
        "size": 489666272, "sha256": "3654c741eb80bd926ada8fed1713b506ccf8d30eb1f6487e87eb9f234f33df09",
    },
    # SAM 2.1 Hiera-Tiny (Meta, Apache 2.0) as a video tracker: square-zero-labs'
    # export of transformers' Sam2VideoModel - its memory encoder, memory
    # attention and object pointers too, not just the image model - checked
    # by them against PyTorch (worst frame IoU 0.9967). Several files: a
    # folder of its own (track_sam.py runs it).
    "sam2-tiny": {
        "label": "SAM 2.1 Tiny (video)", "use": "Track, on footage", "license": "Apache 2.0",
        "repo": "square-zero-labs/sam2.1-tiny-video-onnx", "revision": "3b2984dd865f6e9d2cc6aed0be6a5a5c2eb352ce",
        "name": "sam2.1-tiny-video",
        "parts": [
            {"file": "onnx/vision_encoder.onnx", "size": 134335567,
             "sha256": "aa7a8542942f042e235a993a1ab0ccf5f049918500577802a7f10ec1b39bb873"},
            {"file": "onnx/mask_decoder.onnx", "size": 17794355,
             "sha256": "0461896de3db00936fe1643506f129d71cb6d5d2ae15754811756b7ea1b070c6"},
            {"file": "onnx/memory_encoder.onnx", "size": 5616496,
             "sha256": "580d246c109de88838f600ba7c1c0d03d1fe267f7641b06f8c88c5f0dc5834cd"},
            {"file": "onnx/memory_attention.onnx", "size": 32259165,
             "sha256": "791e648ce8f5ef91ad00ba06e83066ff261ae5a645f0b042b4f46a89fd054baf"},
            {"file": "onnx/pointer_tpos.onnx", "size": 67289,
             "sha256": "7e71df1d75dba09bc18dd4ae745c2a2cceebdd14d84eadea2fe65d0205f101fc"},
            {"file": "constants.json", "size": 9922,
             "sha256": "172edc70e892aab0aef6caedf97b8e4091fbf6c5d26dac5fd9ea69d093d927d3"},
        ],
    },
    # The High quality add-on's models (install_hq: PyTorch first). Parts from
    # more than one repo, kept in folders as diffusers wants them ("to").
    # FLUX.2 [klein] 4B (Black Forest Labs, Apache 2.0) - the 4-step distilled
    # model, without its 8 GB text encoder (the one prompt's embedding ships
    # in data/) - and fal's object-removal LoRA (Apache 2.0): the ComfyUI
    # export, as the other file in that repo is cut short (its header asks
    # for 76 MB, the file holds 58).
    "flux2-klein": {
        "label": "FLUX.2 klein 4B + removal LoRA", "use": "Remove – High quality", "license": "Apache 2.0",
        "repo": "black-forest-labs/FLUX.2-klein-4B", "revision": "e7b7dc27f91deacad38e78976d1f2b499d76a294",
        "name": "flux2-klein-4b", "hq": True,
        "parts": [
            {"file": "transformer/config.json", "to": "transformer/config.json", "size": 541,
             "sha256": "09733c74a3da6d17dd0a0472a091a8950c7c6935889c32c16cc800ede05029de"},
            {"file": "transformer/diffusion_pytorch_model.safetensors", "size": 7751109744,
             "to": "transformer/diffusion_pytorch_model.safetensors",
             "sha256": "9f29f9edcfdae452a653ffb51a534ca4decd389952c225724ff3b94042612a6e"},
            {"file": "vae/config.json", "to": "vae/config.json", "size": 821,
             "sha256": "0d6dfb69ae95a5e2ac9836284bbb63d8b38ce67b25ba2dff380752b2a10ab948"},
            {"file": "vae/diffusion_pytorch_model.safetensors", "to": "vae/diffusion_pytorch_model.safetensors",
             "size": 168120878, "sha256": "ca70d2202afe6415bdbcb8793ba8cd99fd159cfe6192381504d6c4d3036e0f04"},
            {"file": "scheduler/scheduler_config.json", "to": "scheduler/scheduler_config.json", "size": 486,
             "sha256": "067afb012cef64553a763447d1efd93daeffcc0123ca7e25b09f8de20b90762e"},
            {"repo": "fal/flux-2-klein-4B-object-remove-lora", "revision": "0e3f58790356bf1319b263fc56b333c294b42ff7",
             "file": "kDEkt5q7tDLKOpQJIVMPx_pytorch_lora_weights_comfy_converted.safetensors",
             "to": "lora/flux-2-klein-4b-object-remove-lora.safetensors", "size": 76038936,
             "sha256": "dc197de62e174863f83fc4052465603f4389de7c3a78e17f3d41a1f6f11488ec"},
        ],
    },
    # ROSE (Kunbyte, Apache 2.0): Wan 2.1 Fun 1.3B InP trained to remove
    # things from video with their shadows and reflections; the Wan 2.1 VAE
    # from Alibaba PAI's base model (Apache 2.0). Its umT5 (11 GB) and CLIP
    # (4.7 GB) aren't needed: empty prompt (data/), no reference image.
    "rose": {
        "label": "ROSE (video removal)", "use": "Remove on footage – High quality", "license": "Apache 2.0",
        "repo": "Kunbyte/ROSE", "revision": "8a5e57c9f25e73a4b62d324719cdf3367c13df59", "name": "rose", "hq": True,
        "parts": [
            {"file": "diffusion_pytorch_model.safetensors", "to": "rose_transformer.safetensors", "size": 3128957992,
             "sha256": "f4461e70bd9e32447126ebc0d85a71226ac6bae2e49d3ae110095913655bc9fe"},
            {"repo": "alibaba-pai/Wan2.1-Fun-1.3B-InP", "revision": "9bcbbcf673c0e6a996df6f3f25bee596ffa36d47",
             "file": "Wan2.1_VAE.pth", "size": 507609880,
             "sha256": "38071ab59bd94681c686fa51d75a1968f64e470262043be31f7a094e442fd981"},
        ],
    },
}
HQ_MODELS = [m for m, v in MODELS.items() if v.get("hq")]
for _m in MODELS.values():                          # a model in parts: its size is theirs together
    if "parts" in _m:
        _m["size"] = sum(p["size"] for p in _m["parts"])
MATTE_MODELS = ["birefnet", "birefnet-lite"]       # best first

Progress = Callable[..., None]          # progress(text, pct=None)


def venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def model_path(model_id: str) -> Path:
    """The model's file - or, for one in parts, its folder."""
    return MODELS_DIR / MODELS[model_id]["name"]


def own_models(folder: Path, _own_folder: bool = True) -> list[Path]:
    """Buddy's downloads in folder - each model's file (and the note that
    it was checked) or folder: what a change of models folder moves
    (core/model_folders.py). A download cut short stays behind."""
    out = []
    for m in MODELS.values():
        path = folder / m["name"]
        out += [p for p in (path, path.with_name(path.name + ".verified.json")) if p.exists()]
    return out


def model_url(model_id: str) -> str:
    m = MODELS[model_id]
    return f"https://huggingface.co/{m['repo']}/resolve/{m['revision']}/{m['file']}"


def model_files(model_id: str) -> list[dict]:
    """[{url, path, size, sha256}] - the one file, or each of its parts
    (in its folder, by the name it has in the repo, folders left out)."""
    m = MODELS[model_id]
    if "parts" not in m:
        return [{"url": model_url(model_id), "path": model_path(model_id), "size": m["size"], "sha256": m["sha256"]}]
    return [{"url": f"https://huggingface.co/{p.get('repo', m['repo'])}/resolve/"
                    f"{p.get('revision', m['revision'])}/{p['file']}",
             "path": model_path(model_id) / p.get("to", p["file"].rsplit("/", 1)[-1]), "size": p["size"],
             "sha256": p["sha256"]}
            for p in m["parts"]]


def size_mb(model_id: str) -> int:
    return round(MODELS[model_id]["size"] / 1e6)


# ------------------------------------------------------------- status --


def _lock_hash() -> str:
    """The lock file's SHA-256, line endings aside: a git checkout on
    Windows writes it with CRLF, a deployed copy may have LF - the same
    lock either way."""
    import hashlib
    return hashlib.sha256(LOCK.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _stamp_current() -> bool:
    try:
        stamp = json.loads((VENV_DIR / STAMP_NAME).read_text(encoding="utf-8"))
        return stamp.get("lock") == _lock_hash()
    except (OSError, ValueError, AttributeError):
        return False


def env_ready(timeout: int = 60) -> tuple[bool, str]:
    """(ready, why not). Asks the environment's own Python, so a half-built
    one reads as not ready."""
    py = venv_python()
    if not py.exists():
        return False, "Not installed yet."
    if not _stamp_current():
        return False, "Installed from older packages – install again to update it."
    try:
        out = subprocess.run([str(py), "-c", "import numpy, cv2, onnxruntime"], capture_output=True,
                             text=True, timeout=timeout, **tes._no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"The engine can't start: {exc}"
    if out.returncode != 0:
        last = (out.stderr.strip().splitlines() or ["unknown error"])[-1]
        return False, f"The engine is incomplete: {last}"
    return True, ""


def model_ready(model_id: str) -> bool:
    """The model's file (every part of it) is there and is the pinned one -
    hashed once, then remembered while it's unchanged."""
    return all(_file_ready(f) for f in model_files(model_id))


def _file_ready(f: dict) -> bool:
    path = f["path"]
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_size != f["size"]:
        return False
    stamp = path.with_name(path.name + ".verified.json")
    sig = {"sha256": f["sha256"], "size": st.st_size, "mtime": st.st_mtime_ns}
    try:
        if json.loads(stamp.read_text(encoding="utf-8")) == sig:
            return True
    except (OSError, ValueError):
        pass
    if tes._sha256(path) != f["sha256"]:
        return False
    try:
        stamp.write_text(json.dumps(sig), encoding="utf-8")
    except OSError:
        pass
    return True


def status() -> dict:
    """{"env": bool, "detail": why not, "models": {id: bool}, "gpu": gpu_status(),
    "hq": hq_status()} - seconds the first time (the environment is started
    once, each model hashed once)."""
    env, why = env_ready()
    return {"env": env, "detail": why, "models": {mid: model_ready(mid) for mid in MODELS},
            "gpu": gpu_status(env), "hq": hq_status(env)}


# ------------------------------------------------------- graphics card --
#
# NVIDIA's TensorRT for RTX plugin (gpu.py runs the models on it): one
# pinned wheel, offered only where there's an RTX 30 or later on Windows.
# On an RTX 5070 Ti it took LaMa from 1 s to 37 ms, BiRefNet from 8.3 s to
# 41 ms and SAM 2 from 1.3 s to 60 ms a frame.

GPU_LOCK = Path(__file__).with_name("locks") / "gpu-nvidia.txt"
GPU_PACKAGE = "onnxruntime_ep_nv_tensorrt_rtx"
GPU_MB = 105
GPU_CACHE = ROOT / "gpu"                      # the models compiled for the card (gpu.CACHE)
MIN_COMPUTE = 8.0                             # Ampere (RTX 30) and later: TensorRT for RTX's


def nvidia_card() -> dict | None:
    """{"name", "memory_mb", "compute"} of the first NVIDIA card nvidia-smi
    knows - None without one (or without its driver)."""
    if os.name != "nt":
        return None
    exe = shutil.which("nvidia-smi") or os.path.expandvars(r"%SystemRoot%\System32\nvidia-smi.exe")
    if not os.path.isfile(exe):
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15, **tes._no_window())
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            try:
                return {"name": parts[0], "memory_mb": int(float(parts[1])), "compute": float(parts[2])}
            except ValueError:
                continue
    return None


def gpu_installed() -> bool:
    py = venv_python()
    if not py.exists():
        return False
    try:
        out = subprocess.run([str(py), "-c", f"import {GPU_PACKAGE}"], capture_output=True, text=True,
                             timeout=60, **tes._no_window())
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def gpu_status(env: bool | None = None) -> dict:
    """{"card": name or "", "supported": an RTX it can use, "installed": the add-on is}."""
    card = nvidia_card()
    supported = bool(card and card["compute"] >= MIN_COMPUTE)
    return {"card": card["name"] if card else "", "memory_mb": card["memory_mb"] if card else 0,
            "supported": supported, "installed": bool(supported and (env if env is not None else True)
                                                      and gpu_installed())}


def install_gpu(progress: Progress = print, cancelled=lambda: False) -> None:
    """The graphics card add-on into the engine (which has to be there)."""
    if not env_ready()[0]:
        raise SetupError("Install Generative's engine first.")
    progress(f"Downloading the graphics card add-on ({GPU_MB} MB) – checked against its published hash …", 5)
    args = [str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off",
            "--require-hashes", "--only-binary", ":all:", "--no-deps", "-r", str(GPU_LOCK)]
    tes._run_streaming(args, lambda line: progress(line), cancelled, "Installing the graphics card add-on")
    from core import defender
    if os.name == "nt":
        progress("Checking it with Windows Defender …", 90)
    verdict, note = defender.scan(VENV_DIR, "Generative's graphics card add-on")
    if verdict == defender.THREAT:
        remove_gpu()
        raise SetupError("Windows Defender flagged the graphics card add-on, so it was removed and won't be used.")
    if note:
        progress(note)
    if not gpu_installed():
        raise SetupError("The graphics card add-on didn't install properly.")
    progress("Ready.", 100)


def remove_gpu() -> None:
    """The add-on out of the engine, and the models compiled for the card."""
    py = venv_python()
    if py.exists():
        subprocess.run([str(py), "-m", "pip", "uninstall", "-y", "--disable-pip-version-check",
                        "onnxruntime-ep-nv-tensorrt-rtx-cu13"], capture_output=True, text=True, timeout=300,
                       **tes._no_window())
    shutil.rmtree(GPU_CACHE, ignore_errors=True)


# ------------------------------------------------------- High quality --
#
# PyTorch and diffusers in the engine, for FLUX.2 klein (pictures,
# klein_fill.py) and ROSE (footage, rose_fill.py) - models LaMa can't touch
# for what they put back where something was (measured on the user's FX3
# footage, 2026-10-07). NVIDIA RTX 30 or later with 8 GB or more, on
# Windows; CUDA 13's PyTorch wants a driver from 580 on. Both models are
# moved onto the card a few blocks at a time as they run, so they need 3-5
# GB of graphics memory, beside Resolve.

HQ_LOCK = Path(__file__).with_name("locks") / "hq-nvidia.txt"
HQ_MB = 2100                                  # the packages (PyTorch is 2.0 GB of it)
HQ_MIN_MEMORY_MB = 7500                       # an 8 GB card
HQ_MIN_DRIVER = 580                           # CUDA 13


def nvidia_driver() -> float:
    exe = shutil.which("nvidia-smi") or os.path.expandvars(r"%SystemRoot%\System32\nvidia-smi.exe")
    try:
        out = subprocess.run([exe, "--query-gpu=driver_version", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=15, **tes._no_window())
        return float(out.stdout.strip().splitlines()[0].split(".")[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return 0.0


def hq_packages() -> list[str]:
    """The add-on's packages that the engine doesn't have itself (what
    remove_hq takes out)."""
    import re

    def names(path):
        return {re.split(r"[=<>; ]", l.strip(), maxsplit=1)[0].lower()
                for l in path.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.lstrip().startswith(("#", "--"))}
    return sorted(names(HQ_LOCK) - names(LOCK))


def hq_installed() -> bool:
    py = venv_python()
    if not py.exists():
        return False
    try:
        out = subprocess.run([str(py), "-c", "import torch, diffusers, safetensors, accelerate; "
                              "raise SystemExit(0 if torch.cuda.is_available() else 3)"],
                             capture_output=True, text=True, timeout=120, **tes._no_window())
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def hq_status(env: bool | None = None) -> dict:
    """{"card", "supported", "why" (not supported), "installed",
    "models": {id: bool}}."""
    card = nvidia_card()
    why = ""
    if not card:
        why = "It needs an NVIDIA RTX graphics card."
    elif card["compute"] < MIN_COMPUTE:
        why = f"{card['name']} is older than the RTX 30 series it needs."
    elif card["memory_mb"] < HQ_MIN_MEMORY_MB:
        why = f"{card['name']} has {card['memory_mb'] / 1024:.0f} GB of graphics memory – it needs 8 GB."
    elif nvidia_driver() < HQ_MIN_DRIVER:
        why = f"The NVIDIA driver is too old – update it to version {HQ_MIN_DRIVER} or later."
    supported = not why
    installed = bool(supported and (env if env is not None else True) and hq_installed())
    return {"card": card["name"] if card else "", "supported": supported, "why": why, "installed": installed,
            "models": {mid: model_ready(mid) for mid in HQ_MODELS}}


def hq_mb(status: dict) -> int:
    """What install_hq() would download, in MB, by hq_status() - which knows
    what's there (asking again starts the engine's Python)."""
    have = status.get("models") or {}
    return (0 if status.get("installed") else HQ_MB) + sum(size_mb(m) for m in HQ_MODELS if not have.get(m))


def install_hq(models=None, progress: Progress = print, cancelled=lambda: False) -> None:
    """PyTorch and diffusers into the engine (which has to be there), then
    each of `models` (all of the add-on's by default) - whatever's missing."""
    models = list(HQ_MODELS if models is None else models)
    if not env_ready()[0]:
        raise SetupError("Install Generative's engine first.")
    wanted = [m for m in models if not model_ready(m)]
    total = sum(MODELS[m]["size"] for m in wanted) or 1
    start = 0
    if not hq_installed():
        start = 25
        progress(f"Downloading PyTorch and diffusers ({HQ_MB / 1000:.1f} GB) – every file checked against its "
                 "published hash …", 1)
        args = [str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off",
                "--require-hashes", "--only-binary", ":all:", "--no-deps", "-r", str(HQ_LOCK)]
        tes._run_streaming(args, lambda line: progress(line), cancelled, "Installing PyTorch")
        from core import defender
        if os.name == "nt":
            progress("Checking it with Windows Defender …", 20)
        verdict, note = defender.scan(VENV_DIR, "Generative's High quality add-on")
        if verdict == defender.THREAT:
            remove_hq(keep_models=True)
            raise SetupError("Windows Defender flagged the High quality add-on, so it was removed and won't be used.")
        if note:
            progress(note)
        if not hq_installed():
            raise SetupError("PyTorch installed, but can't use the graphics card – update the NVIDIA driver "
                             f"(version {HQ_MIN_DRIVER} or later) and try again.")
    done = 0
    for mid in wanted:
        lo = start + (100 - start) * done / total
        hi = start + (100 - start) * (done + MODELS[mid]["size"]) / total
        _download_model(mid, progress, cancelled, lo, hi)
        done += MODELS[mid]["size"]
    progress("Ready.", 100)


def remove_hq(keep_models: bool = False) -> None:
    """The add-on's packages out of the engine - and its models, unless kept."""
    py = venv_python()
    if py.exists():
        subprocess.run([str(py), "-m", "pip", "uninstall", "-y", "--disable-pip-version-check", *hq_packages()],
                       capture_output=True, text=True, timeout=600, **tes._no_window())
    if not keep_models:
        for mid in HQ_MODELS:
            remove(mid)


def gpu_cache_size() -> int:
    return tes.folder_size(GPU_CACHE)


def disk_size() -> dict:
    """Bytes on disk: {"env": ..., model id: ...}."""
    out = {"env": tes.folder_size(VENV_DIR)}
    for mid in MODELS:
        out[mid] = 0
        for f in model_files(mid):
            try:
                out[mid] += f["path"].stat().st_size
            except OSError:
                pass
    return out


def install_mb(models) -> int:
    """What install(models) would download, in MB, from what's here now."""
    total = 0 if env_ready()[0] else ENV_MB
    return total + sum(size_mb(m) for m in models if not model_ready(m))


# ------------------------------------------------------------ install --


def install(models=("lama",), progress: Progress = print, cancelled=lambda: False) -> None:
    """Builds whatever's missing: the environment (from scratch if it's
    from older locks), then each of `models`. Safe to run again."""
    ROOT.mkdir(parents=True, exist_ok=True)
    wanted = [m for m in models if not model_ready(m)]
    env = env_ready()[0]
    # Share of the bar: the environment a third when it's built, the rest by size.
    total = sum(MODELS[m]["size"] for m in wanted) or 1
    start = 0 if env else 40
    if not env:
        _install_env(progress, cancelled)
    done = 0
    for mid in wanted:
        lo = start + (100 - start) * done / total
        hi = start + (100 - start) * (done + MODELS[mid]["size"]) / total
        _download_model(mid, progress, cancelled, lo, hi)
        done += MODELS[mid]["size"]
    progress("Ready.", 100)


def _install_env(progress, cancelled):
    if VENV_DIR.exists():
        progress("Removing the old engine (the models are kept) …")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    python = tes.base_python()
    progress(f"Creating the engine's environment with {python} …", 2)
    tes._run_streaming([python, "-m", "venv", str(VENV_DIR)], lambda line: progress(line),
                       cancelled, "Creating the environment")
    progress("Installing NumPy, OpenCV and ONNX Runtime – every file checked against its published hash …", 8)
    args = [str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off",
            "--require-hashes", "--only-binary", ":all:", "--no-deps", "-r", str(LOCK)]
    tes._run_streaming(args, lambda line: progress(line), cancelled, "Installing packages")
    check = subprocess.run([str(venv_python()), "-m", "pip", "check", "--disable-pip-version-check"],
                           capture_output=True, text=True, **tes._no_window())
    if check.returncode != 0:
        raise SetupError("The locked packages don't fit together:\n" + check.stdout.strip()[-1500:])
    from core import defender
    if os.name == "nt":
        progress("Checking the engine with Windows Defender …", 30)
    verdict, note = defender.scan(VENV_DIR, "Generative's engine")
    if verdict == defender.THREAT:
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        raise SetupError("Windows Defender flagged Generative's engine download, so it was deleted and won't be "
                         "used. Nothing was run.")
    if note:
        progress(note)
    (VENV_DIR / STAMP_NAME).write_text(json.dumps({"lock": _lock_hash()}), encoding="utf-8")
    ok, why = env_ready()
    if not ok:
        raise SetupError(why)


def _download_model(model_id, progress, cancelled, lo, hi):
    """Each of the model's files not there yet, its share of lo .. hi."""
    total = MODELS[model_id]["size"]
    done = 0
    for f in model_files(model_id):
        a = lo + (hi - lo) * done / total
        b = lo + (hi - lo) * (done + f["size"]) / total
        if not _file_ready(f):
            _download_file(model_id, f, progress, cancelled, a, b)
        done += f["size"]


def _download_file(model_id, f, progress, cancelled, lo, hi):
    m = MODELS[model_id]
    target = f["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".downloading")
    size = f["size"]
    progress(f"Downloading {m['label']} ({m['size'] / 1e6:.0f} MB) …", int(lo))
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    request = urllib.request.Request(f["url"], headers={"User-Agent": "Buddy"})
    if have:
        request.add_header("Range", f"bytes={have}-")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if have and response.status != 206:
                have = 0                       # the server sent it all again
            shown = 0.0
            with open(part, "ab" if have else "wb") as out:
                while True:
                    if cancelled():
                        raise SetupCancelled()
                    block = response.read(1 << 20)
                    if not block:
                        break
                    out.write(block)
                    have += len(block)
                    now = time.monotonic()
                    if now - shown > 0.25:
                        shown = now
                        progress(f"Downloading {m['label']} – {have / 1e6:.0f} of {size / 1e6:.0f} MB"
                                 + (f" ({target.name})" if "parts" in m else ""),
                                 int(lo + (hi - lo) * min(have, size) / size))
    except OSError as exc:
        raise SetupError(f"Couldn't download {m['label']}: {exc}. Check the internet connection and try "
                         "again – it carries on where it stopped.") from exc
    progress(f"Checking {m['label']} …", int(hi))
    if part.stat().st_size != size or tes._sha256(part) != f["sha256"]:
        part.unlink(missing_ok=True)
        raise SetupError(f"The {m['label']} download didn't match what Buddy expects, so it was deleted and "
                         "won't be used. Try again – if it keeps happening, the file on the server has changed.")
    os.replace(part, target)
    _file_ready(f)                             # remembered: not hashed again at the next check


def remove(model_id: str | None = None) -> None:
    """Deletes one model - or, with None, the engine and every model."""
    if model_id is None:
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        for mid in MODELS:          # only Buddy's: the folder may be shared with other apps
            remove(mid)
        return
    if "parts" in MODELS[model_id]:
        shutil.rmtree(model_path(model_id), ignore_errors=True)
        return
    path = model_path(model_id)
    for p in (path, path.with_name(path.name + ".verified.json")):
        try:
            p.unlink()
        except OSError:
            pass
