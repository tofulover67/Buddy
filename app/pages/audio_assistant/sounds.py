#!/usr/bin/env python3
"""
Sound Design's sounds on disk: a riser, an impact or a whoosh the page made
(web/synth.js renders it - the same sound its preview played), or a render
from the DAW (pages/daw, which places its renders the same way), written as a
WAV where Buddy's other audio goes - a "Buddy Audio" folder beside the music
it was made for (render.output_folder), or Buddy's own folder - with a
sidecar holding what it was made from. No Qt, no Resolve.

The sidecar has no "source": the timeline never takes the sound for a
processed clip (resolve_ext._fx_of), and Tidy up finds it like any of
Buddy's WAVs (" - Buddy " in its name, this project on its list). It has
"sound": the recipe it was made from.

Also the Sound Design library kept in the tool's settings (the page's
sd_*): sounds saved by name, and the settings last used. What the page
sends is cleaned here before it's kept - names, settings and counts in
bounds - since it's written to the settings file as it comes.

    decode(channels_b64, rate, most_s) -> samples (frames, channels), or ValueError
    save(samples, rate, recipe, label, beside_path, project) -> {"path", "seconds", "beside", "reused"}
    clean_values(raw), clean_saved(raw), clean_last(raw)
"""

import base64
import hashlib
import json
import os
import re
from datetime import datetime

import numpy as np

from core.atomic_io import write_json

from . import render

KINDS = ("riser", "impact", "whoosh")
RATES = (44100, 48000)
MOST_S = 60.0                 # the longest sound taken (an 8-bar riser at 40 BPM is 48 s)
MOST_RECIPE = 4000            # characters of recipe kept in the sidecar
MOST_SAVED = 300              # sounds in the library
MOST_NAME = 60
MOST_KEYS = 120               # settings a sound has (synth.js has ~45 at the most)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def decode(channels_b64, rate, most_s=MOST_S):
    """The page's channels (base64 of little-endian float32 each) as one
    (frames, channels) array, `most_s` seconds at the most. ValueError for
    anything that isn't a sound."""
    if rate not in RATES:
        raise ValueError("Unexpected sample rate.")
    if not isinstance(channels_b64, list) or not 1 <= len(channels_b64) <= 2:
        raise ValueError("A sound is one or two channels.")
    chans = []
    for data in channels_b64:
        raw = base64.b64decode(str(data), validate=True)
        if len(raw) % 4:
            raise ValueError("A channel isn't whole samples.")
        chans.append(np.frombuffer(raw, dtype="<f4"))
    frames = len(chans[0])
    if not frames or any(len(c) != frames for c in chans):
        raise ValueError("The channels aren't the same length.")
    if frames > most_s * rate:
        raise ValueError("That sound is too long.")
    samples = np.stack(chans, axis=1).astype(np.float64)
    if not np.isfinite(samples).all():
        raise ValueError("The sound has gaps in it.")
    return samples


def file_label(label):
    """A file name's worth of the sound's name: what Windows and macOS both allow."""
    text = str(label or "Sound").replace("♯", "#").replace("♭", "b")
    text = re.sub(r"\s+", " ", _UNSAFE.sub("", text)).strip(" .")
    return text[:80] or "Sound"


def save(samples, rate, recipe, label, beside_path=None, project=None,
         fallback=render.FALLBACK_DIR, writable=render._writable):
    """The sound as a 24-bit WAV - beside the music in "Buddy Audio" when
    beside_path's folder can be written, Buddy's own folder otherwise - and
    its sidecar. Named by what it sounds like, so the same sound made again
    is the same file (reused, this project added to its list).
    Returns {"path", "seconds", "beside", "reused"}; OSError if it can't be
    written."""
    if beside_path:
        folder, beside = render.output_folder(beside_path, fallback, writable)
    else:
        folder, beside = fallback, False
    tag = hashlib.sha1(np.ascontiguousarray(samples, dtype="<f4").tobytes()
                       + str(int(rate)).encode()).hexdigest()[:8]
    path = os.path.join(folder, f"{file_label(label)} - Buddy {tag}.wav")
    seconds = round(len(samples) / rate, 4)
    known = render.read_sidecar(path) if os.path.isfile(path) else None
    if known and known.get("sound"):
        render._claim(path, known, project)
        return {"path": path, "seconds": seconds, "beside": beside, "reused": True}
    os.makedirs(folder, exist_ok=True)
    clipped = render.write_wav(path, samples, rate)
    kept = json.dumps(recipe if isinstance(recipe, dict) else {}, ensure_ascii=False)
    info = {"version": render.SIDECAR_VERSION, "sound": json.loads(kept) if len(kept) <= MOST_RECIPE else {},
            "label": str(label or "")[:120], "rate": int(rate), "frames": int(len(samples)),
            "channels": int(samples.shape[1]), "clipped": clipped,
            "projects": [project] if project else [], "made": datetime.now().isoformat(timespec="seconds")}
    try:
        write_json(render.sidecar_path(path), info)
    except OSError:
        pass
    return {"path": path, "seconds": seconds, "beside": beside, "reused": False}


# ---------------------------------------------------------------- library --

def clean_values(raw):
    """A sound's settings as the page keeps them: {name: number | short text
    | on/off}, nothing else - or {} for anything that isn't."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in list(raw.items())[:MOST_KEYS]:
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", key):
            continue
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)) and np.isfinite(value):
            out[key] = round(float(value), 4) if not float(value).is_integer() else int(value)
        elif isinstance(value, str) and len(value) <= 24:
            out[key] = value
    return out


def _seed(raw):
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    return min(9999, max(1, n))


def clean_saved(raw):
    """The saved sounds: [{"id", "name", "kind", "values", "seed", "made"}],
    each whole or left out, ids unique, MOST_SAVED at the most."""
    out, ids = [], set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            continue
        uid = str(item.get("id") or "")
        name = re.sub(r"\s+", " ", str(item.get("name") or "")).strip()[:MOST_NAME]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", uid) or uid in ids or not name:
            continue
        ids.add(uid)
        out.append({"id": uid, "name": name, "kind": item["kind"], "values": clean_values(item.get("values")),
                    "seed": _seed(item.get("seed")), "made": str(item.get("made") or "")[:20]})
        if len(out) >= MOST_SAVED:
            break
    return out


def clean_last(raw):
    """The settings last used: {"kind", "values": {kind: settings}, "seed": {kind: n}}."""
    raw = raw if isinstance(raw, dict) else {}
    values = raw.get("values") if isinstance(raw.get("values"), dict) else {}
    seeds = raw.get("seed") if isinstance(raw.get("seed"), dict) else {}
    return {"kind": raw.get("kind") if raw.get("kind") in KINDS else "riser",
            "values": {k: clean_values(values.get(k)) for k in KINDS if isinstance(values.get(k), dict)},
            "seed": {k: _seed(seeds.get(k)) for k in KINDS if k in seeds}}

