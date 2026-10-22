#!/usr/bin/env python3
"""
Generative's models on the graphics card, when there's one to use - else
the CPU, as before. Runs in the engine's environment.

NVIDIA RTX (RTX 30 and later) through NVIDIA's TensorRT for RTX plugin for
ONNX Runtime (onnxruntime-ep-nv-tensorrt-rtx, engine_setup's GPU add-on).
Measured on an RTX 5070 Ti against a Ryzen 9 9900X (2026-10-07):

    LaMa (removal)       1.0 s  ->  37 ms    BiRefNet Best   8.3 s  -> 41 ms
    SAM 2 a frame        1.3 s  ->  60 ms    graphics memory: 3.4 GB for all of them

TensorRT compiles a model for the card the first time it's used - LaMa 85
s, BiRefNet ~190 s, SAM 2 a few - so the compiled model (ONNX Runtime's "EP
context": a small .onnx and its .engine) is kept in CACHE and loaded in
seconds after. It's TensorRT for RTX's portable kind, not tied to one card;
one that won't load (a newer plugin) is compiled again.

Anything that goes wrong - no plugin, no RTX, not enough graphics memory
beside Resolve, a model TensorRT can't take - falls back to the CPU for that
model, and says why (why()).

BUDDY_GENERATIVE_GPU=0 in the environment keeps everything on the CPU (the
page's "Use the graphics card" off). Which card: the one CUDA_VISIBLE_DEVICES
shows, which Settings > AI can set (core/ai_card.py cuda_env).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading

# core/tool_dirs.py home(), written out: this runs in the engine's own
# Python, without Buddy's core, and BUDDY_HOME comes down in the environment.
_HOME = (os.path.abspath(os.path.expanduser(os.path.expandvars(os.environ["BUDDY_HOME"].strip())))
         if os.environ.get("BUDDY_HOME", "").strip() else os.path.expanduser("~"))
CACHE = os.path.join(_HOME, ".buddy", "generative", "gpu")
_lock = threading.Lock()
_state = {"tried": False, "devices": None, "why": "", "ep": ""}
_failed = {}                    # model path -> why it's on the CPU


def wanted() -> bool:
    return os.environ.get("BUDDY_GENERATIVE_GPU", "1") != "0"


def devices():
    """The RTX the plugin found (ONNX Runtime's EP devices), or None."""
    if not wanted():
        return None
    with _lock:
        if not _state["tried"]:
            _state["tried"] = True
            try:
                import onnxruntime as ort
                import onnxruntime_ep_nv_tensorrt_rtx as trt
                ort.register_execution_provider_library(trt.get_ep_name(), trt.get_library_path())
                found = [d for d in ort.get_ep_devices() if d.ep_name == trt.get_ep_name()]
                _state["devices"] = found or None
                _state["ep"] = trt.get_ep_name()
                _state["version"] = getattr(trt, "__version__", "")
                if not found and os.environ.get("CUDA_VISIBLE_DEVICES") == "-1" and os.environ.get("BUDDY_AI_CARD"):
                    # Settings > AI chose another kind of card (core/ai_card.py cuda_env).
                    _state["why"] = f"Settings > AI runs AI on {os.environ['BUDDY_AI_CARD']}, not an NVIDIA RTX."
                elif not found:
                    _state["why"] = "No NVIDIA RTX graphics card was found."
            except ImportError:
                _state["why"] = "The graphics card add-on isn't installed."
            except Exception as exc:  # noqa: BLE001 - a broken install runs on the CPU
                _state["why"] = f"The graphics card add-on didn't start: {exc}"
    return _state["devices"]


def name() -> str:
    """The card in use ("NVIDIA GeForce RTX 5070 Ti"), or ""."""
    d = devices()
    if not d:
        return ""
    meta = getattr(d[0].device, "metadata", {}) or {}
    return str(meta.get("Description", "") or "NVIDIA RTX")


def why() -> str:
    return _state["why"]


def on_gpu(path: str) -> bool:
    return bool(devices()) and path not in _failed


def _key(path: str) -> str:
    st = os.stat(path)
    h = hashlib.sha256(f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{_state.get('version', '')}".encode())
    return f"{os.path.splitext(os.path.basename(path))[0]}-{h.hexdigest()[:16]}"


def compiled(path: str) -> bool:
    """Whether the model at `path` is ready compiled for the card (so
    session() won't take a minute or two) - False with no card in use."""
    return bool(devices()) and path not in _failed and os.path.isfile(
        os.path.join(CACHE, _key(path), "model_ctx.onnx"))


def preparing_text(label: str) -> dict:
    """What the busy card says while `label` is compiled for the card."""
    return {"stage": "Preparing the graphics card",
            "detail": f"{label} – only the first time, a minute or two. It's kept after."}


def _cpu(path, threads=0):
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    if threads:
        opts.intra_op_num_threads = threads
    return ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])


def _gpu_options(ort, devs, extra=None):
    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.add_provider_for_devices(devs, {"nv_runtime_cache_path": os.path.join(CACHE, "kernels")})
    for k, v in (extra or {}).items():
        so.add_session_config_entry(k, v)
    return so


def _compiled(path: str, devs, preparing) -> str:
    """The model compiled for the card (its EP context .onnx) - compiled now
    if it isn't yet: into a folder of its own, moved into place whole, so a
    second process compiling the same model at once doesn't leave half a one."""
    import onnxruntime as ort
    final = os.path.join(CACHE, _key(path))
    ctx = os.path.join(final, "model_ctx.onnx")
    if os.path.isfile(ctx):
        return ctx
    if preparing:
        preparing()
    building = f"{final}.building-{os.getpid()}"
    shutil.rmtree(building, ignore_errors=True)
    os.makedirs(building, exist_ok=True)
    os.makedirs(os.path.join(CACHE, "kernels"), exist_ok=True)
    so = _gpu_options(ort, devs, {"ep.context_enable": "1", "ep.context_embed_mode": "0",
                                  "ep.context_file_path": os.path.join(building, "model_ctx.onnx")})
    ort.InferenceSession(path, sess_options=so)
    try:
        os.replace(building, final)
    except OSError:                       # someone else's got there first: theirs is used
        shutil.rmtree(building, ignore_errors=True)
    return ctx


def session(path: str, threads: int = 0, preparing=None):
    """An InferenceSession for the ONNX model at `path`: on the RTX when
    there is one (compiled for it the first time - preparing() is said
    first, as that takes a minute or two), else on the CPU."""
    devs = devices()
    if devs and path not in _failed:
        import onnxruntime as ort
        try:
            ctx = _compiled(path, devs, preparing)
            try:
                return ort.InferenceSession(ctx, sess_options=_gpu_options(ort, devs))
            except Exception:  # noqa: BLE001 - a compiled model that won't load: compiled again, once
                shutil.rmtree(os.path.dirname(ctx), ignore_errors=True)
                ctx = _compiled(path, devs, preparing)
                return ort.InferenceSession(ctx, sess_options=_gpu_options(ort, devs))
        except Exception as exc:  # noqa: BLE001 - this model runs on the CPU then
            _failed[path] = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
    return _cpu(path, threads)
