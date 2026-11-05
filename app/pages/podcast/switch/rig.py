#!/usr/bin/env python3
"""
Auto Switch's camera setups ("rigs"): the cameras a studio has, what each
is called, which mics give it its sound, and when it's used. Saved by
name and reused episode after episode, so nothing here knows about one
timeline: a camera remembers the name of the video track it was on and of
the mics it's linked to, and place() finds them again on whichever
timeline is open. Pure Python: no Qt, no Resolve, no numpy.

    clean_setups(raw)                 -> {id: setup}, every field made safe
    from_timeline(video, mics, name, legacy) -> a first setup for a timeline
    new_camera(setup, name)           -> a camera added to it
    place(setup, video, mics, mem)    -> each camera's track and linked mics here

A camera's role says when the cut plan (cutplan.py) shows it:

    speaker   a person's camera: shows the voices pinned to it while they
              talk. With `reaction` on, it's also a listener's reaction
              shot: when someone else talks longer than `react_after` s,
              it's cut to for `react_len` s, and back.
    angle     a second angle of a speaker camera (`of`): in that camera's
              shots, the two take turns every `every` s.
    wide      the wide / group shot: for crosstalk (`crosstalk`), long
              silences (`quiet`), the first and last `ends` s, and a
              cutaway of `cut_len` s every `cutaway` s. Several wides take
              turns.
    cutaway   a detail, audience or extra cam: cut to at random every
              `gap_min`-`gap_max` s, for `length` s; `weight` says how
              often it's picked when there are several.
    off       not used: razored nowhere, never on screen (its clips are
              turned off in the new timeline).

Those settings are the setup's `rules`, one set for every camera of a
role (Cut settings); `person` is who a speaker's camera shows, so a voice
on it starts with that name next episode.

Audio links: a camera with mics linked (`audio`) is heard through them -
its own sound is cut at the same points as its picture and turned off in
the new timeline (otio_split), and the voices on those mics are pinned to
it by default.
"""

ROLES = ("speaker", "angle", "wide", "cutaway", "off")
MAX_SETUPS = 30
MAX_CAMERAS = 16
MAX_NAME = 60
WIDE_WORDS = ("wide", "master", "two shot", "2 shot", "2-shot", "two-shot", "group", "all")

# Each role's settings: (default, lowest, highest) - or a bool / a camera id.
NUMBERS = {
    "react_after": (20.0, 3.0, 600.0),
    "react_len": (2.5, 0.5, 20.0),
    "every": (12.0, 2.0, 600.0),
    "ends": (0.0, 0.0, 60.0),
    "cutaway": (0.0, 0.0, 600.0),
    "cut_len": (3.0, 0.5, 20.0),
    "gap_min": (30.0, 3.0, 1800.0),
    "gap_max": (60.0, 3.0, 1800.0),
    "length": (3.0, 0.5, 20.0),
    "weight": (1.0, 0.1, 10.0),
}
FLAGS = {"reaction": False, "crosstalk": True, "quiet": False}


def _text(value, limit=MAX_NAME):
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def _number(value, key):
    default, lo, hi = NUMBERS[key]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if value != value:          # NaN
        return default
    return round(min(hi, max(lo, value)), 2)


def words(text):
    """The words of a name, lowercased - "Tess Cam (2)" -> {"tess", "cam"}."""
    return {w for w in "".join(ch.lower() if ch.isalnum() else " " for ch in str(text)).split() if len(w) > 1}


def looks_wide(name):
    """A track named like a wide shot: "Wide", "Master", "Two shot"..."""
    low = str(name).lower()
    return bool(words(name) & {w for w in WIDE_WORDS if " " not in w and "-" not in w}) or \
        any(w in low for w in WIDE_WORDS if " " in w or "-" in w)


def clean_camera(raw, index=1):
    raw = raw if isinstance(raw, dict) else {}
    cam = {
        "id": _text(raw.get("id"), 20) or f"cam{index}",
        "name": _text(raw.get("name")) or f"Camera {index}",
        "role": raw.get("role") if raw.get("role") in ROLES else "speaker",
        "track_name": _text(raw.get("track_name"), 120),
        "audio_names": [_text(n, 120) for n in raw.get("audio_names", []) if _text(n, 120)][:8]
        if isinstance(raw.get("audio_names"), list) else [],
        "of": _text(raw.get("of"), 20) or None,
        "person": _text(raw.get("person")),
    }
    cam.update(clean_rules(raw))
    return cam


def clean_rules(raw):
    """The role settings (NUMBERS, FLAGS), every one in range."""
    raw = raw if isinstance(raw, dict) else {}
    out = {key: _number(raw.get(key, NUMBERS[key][0]), key) for key in NUMBERS}
    for key, default in FLAGS.items():
        out[key] = raw[key] if isinstance(raw.get(key), bool) else default
    if out["gap_max"] < out["gap_min"]:
        out["gap_max"] = out["gap_min"]
    return out


# Which role each setting is for - where an older setup's own cameras held them.
RULE_ROLES = {"speaker": ("reaction", "react_after", "react_len"), "angle": ("every",),
              "wide": ("crosstalk", "quiet", "ends", "cutaway", "cut_len"),
              "cutaway": ("gap_min", "gap_max", "length", "weight")}


def clean_setup(raw, index=1):
    raw = raw if isinstance(raw, dict) else {}
    cams, seen = [], set()
    for i, item in enumerate(raw.get("cameras", []) if isinstance(raw.get("cameras"), list) else []):
        cam = clean_camera(item, i + 1)
        if cam["id"] in seen:
            cam["id"] = _free_id(seen)
        seen.add(cam["id"])
        cams.append(cam)
        if len(cams) >= MAX_CAMERAS:
            break
    for cam in cams:                      # an angle of a camera that's gone (or of itself) is of nothing
        if cam["of"] not in seen or cam["of"] == cam["id"]:
            cam["of"] = None
    rules = raw.get("rules")
    if not isinstance(rules, dict):       # an older setup: each role's first camera's settings
        rules = {}
        for role, keys in RULE_ROLES.items():
            first = next((c for c in raw.get("cameras", []) if isinstance(c, dict) and c.get("role") == role), {})
            rules.update({k: first[k] for k in keys if k in first})
    return {"name": _text(raw.get("name")) or f"Setup {index}", "cameras": cams, "rules": clean_rules(rules)}


def clean_setups(raw):
    out = {}
    for i, (key, value) in enumerate(raw.items() if isinstance(raw, dict) else ()):
        key = _text(key, 20)
        if key and len(out) < MAX_SETUPS:
            out[key] = clean_setup(value, i + 1)
    return out


def _free_id(taken, prefix="cam"):
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def new_setup_id(setups):
    return _free_id(set(setups), "setup")


def new_camera(setup, name=""):
    """A speaker camera added to `setup` (changed in place) - its id."""
    taken = {c["id"] for c in setup["cameras"]}
    cam = clean_camera({"id": _free_id(taken), "name": name or f"Camera {len(setup['cameras']) + 1}"},
                       len(setup["cameras"]) + 1)
    setup["cameras"].append(cam)
    return cam["id"]


def _default_name(name, kind, index):
    return not name or name == f"{'Audio' if kind == 'audio' else 'Video'} {index}"


def from_timeline(video, mics, name="My setup", legacy_wide=None):
    """A first setup from a timeline: a camera per video track with clips,
    named after its track, the one named like a wide (or `legacy_wide`, the
    track an older Auto Switch had as the wide) a wide, the rest speakers;
    a speaker linked to the mic named like it ("Tess cam" - "Tess lav")."""
    setup = {"name": name, "cameras": []}
    for track in video:
        if not track.get("clips"):
            continue
        raw = str(track.get("name") or "")
        new_camera(setup, raw if not _default_name(raw, "video", track["index"]) else "")
        cam = setup["cameras"][-1]
        cam["track_name"] = raw
        if (track["index"] == legacy_wide) if legacy_wide is not None else looks_wide(raw):
            cam["role"] = "wide"
    for cam in setup["cameras"]:
        if cam["role"] != "speaker":
            continue
        mine = words(cam["track_name"]) - {"cam", "camera", "video"}
        for mic in mics:
            if mic.get("camera_sound"):
                continue
            mic_name = str(mic.get("raw_name") or "")
            if mine and not _default_name(mic_name, "audio", mic["index"]) and mine & words(mic_name):
                cam["audio_names"].append(mic_name)
    return clean_setup(setup)


def place(setup, video, mics, mem):
    """{camera id: {"track": video track index or None, "audio": [mic track
    indexes]}} on this timeline. mem: this timeline's own choices,
    {"tracks": {id: track or None}, "audio": {id: [tracks]}} - they win;
    else a camera goes to the track with its track's name, then the one
    with its own name, then the next free one; its mics by name."""
    tracks = [t for t in video if t.get("clips")]
    valid = {t["index"] for t in tracks}
    mic_valid = {m["index"] for m in mics}
    chosen_tracks = mem.get("tracks") if isinstance(mem.get("tracks"), dict) else {}
    chosen_audio = mem.get("audio") if isinstance(mem.get("audio"), dict) else {}
    out, taken = {}, set()
    cams = setup["cameras"]
    for cam in cams:                                       # this timeline's own choice
        if cam["id"] in chosen_tracks:
            track = chosen_tracks[cam["id"]]
            out[cam["id"]] = track if track in valid else None
            if out[cam["id"]] is not None:
                taken.add(out[cam["id"]])
    for match in (lambda c, t: c["track_name"] and str(t.get("name") or "") == c["track_name"],
                  lambda c, t: words(c["name"]) and words(c["name"]) == words(t.get("name") or "")):
        for cam in cams:
            if cam["id"] in out:
                continue
            hit = next((t["index"] for t in tracks if t["index"] not in taken and match(cam, t)), None)
            if hit is not None:
                out[cam["id"]] = hit
                taken.add(hit)
    for cam in cams:                                       # then in order
        if cam["id"] not in out:
            hit = next((t["index"] for t in tracks if t["index"] not in taken), None)
            out[cam["id"]] = hit
            if hit is not None:
                taken.add(hit)
    placed = {}
    for cam in cams:
        if cam["id"] in chosen_audio and isinstance(chosen_audio[cam["id"]], list):
            audio = [a for a in chosen_audio[cam["id"]] if a in mic_valid]
        else:
            audio = [m["index"] for m in mics if str(m.get("raw_name") or "") in cam["audio_names"]]
        placed[cam["id"]] = {"track": out[cam["id"]], "audio": sorted(set(audio))}
    return placed
