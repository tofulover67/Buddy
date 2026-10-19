#!/usr/bin/env python3
"""
The DAW's project as it's kept between sessions (the tool's settings,
~/.buddy/tools/daw): what the page sends, cleaned to what a project can
hold before it's written - so a bad or huge one is never kept.

A project (the page's web/engine.js makes and reads it):

    bpm, tonic (0-11, C = 0), mode ("major" | "minor"), follow (the song's
    key and tempo when one's been heard), swing (0-0.5), snap (steps a beat:
    0 off, 1, 2, 3, 4, 8), length (bars), cursor (beats),
    loop {on, start, end} (beats), metronome, count_in, pre_roll (play from
    a beat before the playhead), octave, velocity,
    voicing ("triad" | "seventh" | "spread"), view ("arrange" | "console" |
    "perform"), master (its fader, 0-1.25), song (the clip id the song lane
    is), song_level, song_mute, follow_resolve (the cursor follows Resolve's
    playhead while stopped), edit_audio (the timeline's audio shown in Arrange), align
    ("song" | "timeline": what the bars line up with, web/engine.js), snap_clips (Arrange's
    magnet: clips snap to the grid and the song's beats),
    sel {track, clip}, key_chord {type (web/chords.js: a
    short name, "off" for one note), voicing, inversion (0-3)} - what one key plays
    grids {a song's file: {bpm, first}} - its tempo and where its bar 1 is
             (seconds into the file), set by hand over what Buddy heard
    anchor {song, at, origin} | None - the song the beats were last lined up
             with (its file), the beat its bar 1 was on, and where beat 0 was
             (seconds from the timeline's first frame); None in a project kept
             before beat 0 was the timeline's start (web/engine.js reanchor)
    tracks [{id, name, kind ("inst" | "drums"), color (0-9), sound (an
             instrument for "inst", a kit for "drums"), values {name: number},
             level (0-1.25 on the fader), pan (-1..1), mute, solo, off (disabled),
             macros [4 x 0-1], clips [{id, name, start, len (beats), off (disabled; only when true),
             notes [[midi, start, length (beats, in the clip), velocity]]}]}]

Notes on a drum track are its kit's sounds by number (engine.js DRUM_ROWS).
"""

import re

INSTRUMENTS = ("pad", "pluck", "keys", "bass", "bell", "lead", "choir", "sub")
KITS = ("tight", "boom", "trailer")
VIEWS = ("arrange", "console", "perform")
VOICINGS = ("triad", "seventh", "spread")
KEY_VOICINGS = ("close", "open", "drop2", "bass")
SNAPS = (0, 1, 2, 3, 4, 8)

MOST_TRACKS = 16
MOST_CLIPS = 64              # a track's
MOST_NOTES = 4000            # a clip's
MOST_ALL_NOTES = 40000       # the project's
MOST_BARS = 256
MOST_VALUES = 24
MOST_NAME = 40
MOST_GRIDS = 32              # songs whose tempo was set by hand (the latest kept)
MOST_PATH = 1024
MOST_EDIT_BARS = 1024        # the timeline the DAW spans (web/engine.js MOST_BEATS)
_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


def _num(raw, lo, hi, default):
    """A finite number in [lo, hi], or `default`."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        return default
    return min(hi, max(lo, value))


def _int(raw, lo, hi, default):
    value = _num(raw, lo, hi, None)
    return default if value is None else int(round(value))


def _bool(raw, default=False):
    return raw if isinstance(raw, bool) else default


def _name(raw, default):
    text = re.sub(r"\s+", " ", str(raw if isinstance(raw, str) else "")).strip()[:MOST_NAME]
    return text or default


def _id(raw, fallback):
    return raw if isinstance(raw, str) and _ID.match(raw) else fallback


def _values(raw):
    """An instrument's or kit's settings: {short name: number}."""
    out = {}
    for key, value in list(raw.items() if isinstance(raw, dict) else [])[:MOST_VALUES]:
        if isinstance(key, str) and _KEY.match(key):
            number = _num(value, -1000.0, 1000.0, None)
            if number is not None:
                out[key] = round(number, 4)
    return out


def _notes(raw, length, budget):
    """[[midi, start, length, velocity]] inside a clip `length` beats long."""
    out = []
    for note in raw if isinstance(raw, list) else []:
        if len(out) >= min(MOST_NOTES, budget):
            break
        if not isinstance(note, list) or len(note) != 4:
            continue
        midi = _int(note[0], 0, 127, None)
        start = _num(note[1], 0.0, length, None)
        dur = _num(note[2], 1 / 64, length, None)
        vel = _num(note[3], 0.05, 1.0, None)
        if None in (midi, start, dur, vel) or start >= length:
            continue
        out.append([midi, round(start, 4), round(min(dur, length - start), 4), round(vel, 3)])
    return out


def _clips(raw, budget, most_beats):
    out, ids = [], set()
    for i, clip in enumerate(raw if isinstance(raw, list) else []):
        if len(out) >= MOST_CLIPS:
            break
        if not isinstance(clip, dict):
            continue
        uid = _id(clip.get("id"), f"c{i}")
        if uid in ids:
            continue
        start = _num(clip.get("start"), 0.0, most_beats - 0.25, None)
        length = _num(clip.get("len"), 0.25, most_beats, None)
        if start is None or length is None:
            continue
        notes = _notes(clip.get("notes"), length, budget[0])
        budget[0] -= len(notes)
        ids.add(uid)
        kept = {"id": uid, "name": _name(clip.get("name"), "Clip"), "start": round(start, 4),
                "len": round(min(length, most_beats - start), 4), "notes": notes}
        if clip.get("off") is True:
            kept["off"] = True                   # disabled: not played or placed
        out.append(kept)
    return out


def _grids(raw):
    """Songs' grids set by hand: {file: {"bpm", "first" (seconds into the file)}},
    the last MOST_GRIDS (the page adds the latest last)."""
    out = {}
    for path, grid in list(raw.items() if isinstance(raw, dict) else [])[-MOST_GRIDS:]:
        if not isinstance(path, str) or not 0 < len(path) <= MOST_PATH or not isinstance(grid, dict):
            continue
        bpm = _num(grid.get("bpm"), 30.0, 300.0, None)
        first = _num(grid.get("first"), -60.0, 36000.0, None)
        if bpm is not None and first is not None:
            out[path] = {"bpm": round(bpm, 3), "first": round(first, 4)}
    return out


def _anchor(raw, most_beats):
    if not isinstance(raw, dict) or not isinstance(raw.get("song"), str) or len(raw["song"]) > MOST_PATH:
        return None
    at = _num(raw.get("at"), -most_beats, most_beats, None)       # lined up with the timeline: any beat
    origin = _num(raw.get("origin"), -36000.0, 36000.0, None)
    if at is None or origin is None:
        return None
    return {"song": raw["song"], "at": round(at, 4), "origin": round(origin, 4)}


def _track(raw, i, budget, most_beats):
    kind = raw.get("kind") if raw.get("kind") in ("inst", "drums") else "inst"
    sounds = KITS if kind == "drums" else INSTRUMENTS
    macros = raw.get("macros") if isinstance(raw.get("macros"), list) else []
    return {
        "id": _id(raw.get("id"), f"t{i}"),
        "name": _name(raw.get("name"), "Drums" if kind == "drums" else "Track"),
        "kind": kind,
        "color": _int(raw.get("color"), 0, 9, i % 10),
        "sound": raw.get("sound") if raw.get("sound") in sounds else sounds[0],
        "values": _values(raw.get("values")),
        "level": round(_num(raw.get("level"), 0.0, 1.25, 0.8), 4),
        "pan": round(_num(raw.get("pan"), -1.0, 1.0, 0.0), 4),
        "mute": _bool(raw.get("mute")),
        "off": _bool(raw.get("off")),            # disabled: not played or placed
        "solo": _bool(raw.get("solo")),
        "macros": [round(_num(macros[k] if k < len(macros) else None, 0.0, 1.0, 0.5), 4) for k in range(4)],
        "clips": _clips(raw.get("clips"), budget, most_beats),
    }


def clean(raw):
    """The project as it can be kept, or None for anything that isn't one."""
    if not isinstance(raw, dict) or not isinstance(raw.get("tracks"), list):
        return None
    length = _int(raw.get("length"), 1, MOST_BARS, 16)
    most_beats = MOST_BARS * 4.0
    budget = [MOST_ALL_NOTES]
    tracks, ids = [], set()
    for i, track in enumerate(raw["tracks"]):
        if len(tracks) >= MOST_TRACKS:
            break
        if not isinstance(track, dict):
            continue
        cleaned = _track(track, i, budget, most_beats)
        if cleaned["id"] in ids:
            continue
        ids.add(cleaned["id"])
        tracks.append(cleaned)
    loop = raw.get("loop") if isinstance(raw.get("loop"), dict) else {}
    start = _num(loop.get("start"), 0.0, most_beats - 1, 0.0)
    end = _num(loop.get("end"), start + 0.25, most_beats, start + 16)    # any length, a sixteenth at the least
    sel = raw.get("sel") if isinstance(raw.get("sel"), dict) else {}
    chord = raw.get("key_chord") if isinstance(raw.get("key_chord"), dict) else {}
    snap = _int(raw.get("snap"), 0, 8, 4)
    return {
        "bpm": round(_num(raw.get("bpm"), 40.0, 240.0, 120.0), 3),
        "tonic": _int(raw.get("tonic"), 0, 11, 9),
        "mode": raw.get("mode") if raw.get("mode") in ("major", "minor") else "minor",
        "follow": _bool(raw.get("follow"), True),
        "swing": round(_num(raw.get("swing"), 0.0, 0.5, 0.0), 3),
        "snap": snap if snap in SNAPS else 4,
        "length": length,
        "cursor": round(_num(raw.get("cursor"), 0.0, most_beats, 0.0), 4),
        "loop": {"on": _bool(loop.get("on"), True), "start": round(start, 4), "end": round(end, 4)},
        "metronome": _bool(raw.get("metronome"), True),
        "count_in": _bool(raw.get("count_in"), True),
        "pre_roll": _bool(raw.get("pre_roll")),
        "octave": _int(raw.get("octave"), 1, 7, 4),
        "velocity": round(_num(raw.get("velocity"), 0.1, 1.0, 0.8), 3),
        "voicing": raw.get("voicing") if raw.get("voicing") in VOICINGS else "triad",
        "view": raw.get("view") if raw.get("view") in VIEWS else "arrange",
        "master": round(_num(raw.get("master"), 0.0, 1.25, 0.8), 4),
        "song_level": round(_num(raw.get("song_level"), 0.0, 1.25, 0.7), 4),
        "song_mute": _bool(raw.get("song_mute")),
        "follow_resolve": _bool(raw.get("follow_resolve"), True),
        "edit_audio": _bool(raw.get("edit_audio"), True),
        "align": raw.get("align") if raw.get("align") in ("song", "timeline") else "song",
        "snap_clips": _bool(raw.get("snap_clips"), True),
        "song": raw.get("song") if isinstance(raw.get("song"), str) and len(raw["song"]) <= 80 else "",
        "sel": {"track": _id(sel.get("track"), ""), "clip": _id(sel.get("clip"), "")},
        "key_chord": {"type": chord.get("type") if isinstance(chord.get("type"), str) and _KEY.match(chord["type"]) else "off",
                      "voicing": chord.get("voicing") if chord.get("voicing") in KEY_VOICINGS else "close",
                      "inversion": _int(chord.get("inversion"), 0, 3, 0)},
        "grids": _grids(raw.get("grids")),
        "anchor": _anchor(raw.get("anchor"), 4 * MOST_EDIT_BARS),
        "tracks": tracks,
    }
