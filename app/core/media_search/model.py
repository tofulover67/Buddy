#!/usr/bin/env python3
"""
Media search's model: EmbeddingGemma 2 in Buddy's own llama.cpp.

What has to be on disk (missing, install) and the three calls that make
vectors - of descriptions, of pictures, of sounds - all in one space, so a
description is compared with pictures and sounds directly.

A description gets the model's search prefix; pictures and sounds go as they
are (its card: prefixes are for text only). Pictures are sent PICTURE_SIDE
pixels on their longer side - 81 of the model's tokens each, a third of a
second on a desktop CPU, and found as well as at twice the size (measured
2026-10-12) - and sound as 16 kHz mono WAV, 25 tokens a second.

On a Windows PC with a graphics card of its own it runs on the card, through
llama.cpp's Vulkan build (local_llama.GPU_RUNTIMES) - an extra, 32 MB
download: wanted, not needed (extras). 13 times the frames a second there,
and vectors that agree with the processor's to three decimal places, so an
index made on one is searched from the other. If the card can't run it - no
driver, no memory left beside Resolve - it goes back to the processor for the
rest of the session and says so (gpu_note). Settings can keep it off the
card (use_gpu).

No Qt. Every call here can take seconds (the first starts llama.cpp and
loads the model): never on the UI thread.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from core import ai_card, atomic_io, tool_dirs

NAME = "embeddinggemma-2-Q8_0"          # in the index: another model's vectors aren't these
QUERY_PREFIX = "task: search result | query: "
PICTURE_SIDE = 384
SOUND_RATE = 16000
BATCH = 4                               # inputs a request: llama.cpp's four slots
TIMEOUT = 300


class SearchError(Exception):
    """Worded for the person: what's missing or stopped, and what to do."""


_GPU_FAILED = [""]      # why the graphics card couldn't run it, this session; "" while it hasn't failed


def settings_path() -> str:
    return os.path.join(tool_dirs.buddy_dir(), "media_search", "settings.json")


def _settings() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def use_gpu() -> bool:
    """Whether the graphics card may be used (Settings; on unless switched off)."""
    return _settings().get("gpu", True) is not False


def set_use_gpu(on: bool) -> None:
    data = dict(_settings(), gpu=bool(on))
    os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
    atomic_io.write_json(settings_path(), data, indent=2)
    _GPU_FAILED[0] = ""                         # asked for again: tried again
    stop()


def stop() -> None:
    """Stops the model's servers; the next call starts whichever is wanted now."""
    _llama().stop_model(files()[0])


def _llama():
    from pages.manual_chat import local_llama
    return local_llama


def files() -> tuple[Path, Path]:
    """(the model, its picture-and-sound half) where they belong, there or not."""
    ll = _llama()
    return ll.MODELS_DIR / ll.EMBEDDING_GEMMA_2.name, ll.MODELS_DIR / ll.EMBEDDING_GEMMA_2_MEDIA.name


def extras() -> list:
    """Downloads that make it faster but aren't needed: llama.cpp's build
    for the graphics card, where there is one for this machine and Settings
    hasn't switched the card off."""
    ll = _llama()
    build = ll.gpu_runtime_download()
    return [build] if build is not None and use_gpu() and ll.gpu_server_exe() is None else []


def gpu() -> str:
    """The graphics card it runs on now, by name - "" on the processor."""
    if not use_gpu() or _GPU_FAILED[0]:
        return ""
    device = _llama().gpu_device(ai_card.vulkan_card())
    return device[1] if device else ""


def gpu_note() -> str:
    """Why it's on the processor although a card was wanted, or ""."""
    return _GPU_FAILED[0]


def missing() -> list:
    """The downloads still needed, as local_llama.Downloads: llama.cpp's
    release for this machine if what's installed is older (or nothing is),
    and whichever of the model's two files isn't there whole."""
    ll = _llama()
    out = []
    if not ll.runtime_current():
        build = ll.runtime_download()
        if build is not None:
            out.append(build)
    for item, path in zip(ll.MEDIA_SEARCH_FILES, files()):
        if ll.pinned_copy_known(path, item) is False:       # not there, another size, or checked and wrong
            out.append(item)
    return out


def supported() -> bool:
    """Whether llama.cpp has a build for this computer at all."""
    return _llama().runtime_download() is not None


def ready() -> tuple[bool, str]:
    """Whether it can embed now, asked cheaply - nothing is started."""
    if not supported():
        return False, "There's no llama.cpp build for this computer, so searching by description can't run here."
    if missing():
        return False, "Searching by description isn't set up yet."
    return True, ""


def install(progress=None, cancelled=None) -> str:
    """Downloads what's missing() and the extras(), each checked against its
    pinned checksum (llama.cpp scanned by Windows Defender too). Returns
    Defender's note, if any. Raises local_llama's LlamaError or Cancelled."""
    ll = _llama()
    note = ""
    if not ll.runtime_current():
        _exe, note = ll.install_runtime(progress, cancelled)
    for item in missing():
        if item in ll.MEDIA_SEARCH_FILES:
            ll.download(item, ll.ensure_models_dir() / item.name, progress, cancelled)
    if extras():
        _exe, gpu_note_ = ll.install_gpu_runtime(progress, cancelled)
        note = note or gpu_note_
    return note


def _embed(inputs: list, timeout: float) -> list[list[float]]:
    ok, why = ready()
    if not ok:
        raise SearchError(why)
    ll = _llama()
    model, media = files()
    chosen = ai_card.vulkan_card()          # Settings > AI's card, if one was chosen
    vectors = []
    for at in range(0, len(inputs), BATCH):
        batch = inputs[at:at + BATCH]
        on_card = bool(gpu())
        try:
            server = ll.server_for(model, mmproj=media, gpu=on_card, card=chosen)
            payload = server.post("/embeddings", {"input": batch}, timeout)
        except ll.LlamaError as exc:
            if not on_card:
                raise SearchError(str(exc)) from exc
            # The card couldn't: the processor does this batch and the rest of the session's.
            _GPU_FAILED[0] = str(exc)
            try:
                ll.server_for(model, mmproj=media, gpu=True, card=chosen).stop()
                payload = ll.server_for(model, mmproj=media).post("/embeddings", {"input": batch}, timeout)
            except ll.LlamaError as again:
                raise SearchError(str(again)) from again
        rows = sorted(payload.get("data") or [], key=lambda d: d.get("index", 0))
        got = [d.get("embedding") for d in rows]
        if len(got) != len(batch) or not all(isinstance(v, list) and v for v in got):
            raise SearchError(f"llama.cpp returned {len(got)} embeddings for {len(batch)} inputs.")
        vectors += got
    return vectors


def embed_texts(texts: list[str], timeout: float = TIMEOUT) -> list[list[float]]:
    """Vectors of descriptions - what's typed to search with."""
    return _embed([QUERY_PREFIX + " ".join(str(t).split()) for t in texts], timeout)


def embed_pictures(jpegs: list[bytes], timeout: float = TIMEOUT) -> list[list[float]]:
    """Vectors of pictures, each given as a JPEG's (or PNG's) bytes."""
    return _embed([{"content": [{"type": "image_url", "image_url": {
        "url": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")}}]} for data in jpegs], timeout)


def embed_sounds(wavs: list[bytes], timeout: float = TIMEOUT) -> list[list[float]]:
    """Vectors of sounds, each given as a WAV file's bytes (16 kHz mono)."""
    return _embed([{"content": [{"type": "input_audio", "input_audio": {
        "data": base64.b64encode(data).decode("ascii"), "format": "wav"}}]} for data in wavs], timeout)
