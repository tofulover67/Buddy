#!/usr/bin/env python3
"""
Podcast Sync's engine: every camera and recorder of one recording lined up,
then laid out as a stacked timeline - a video track per camera, an audio
track per mic, the cameras' own sound below the mics.

No Qt and no Resolve in here. It works on an OTIO document - the export of
a timeline holding the clips (page.py makes one) - and hands back a new
document for page.py to import, so the clips and timelines the person
already has are never changed.

It stands on Project Setup's sync (pages/project_setup/):

    otio_engine     reading the export (logical_clips: a camera clip's
                    picture and sound together), timecode offsets, the
                    sound-matching groups (build_sync_groups) and
                    build_document, which writes the tracks.
    audio_sync      decoding a stretch of a file (load_window).

What a podcast adds on top:

    Devices.        Clips are grouped by the camera or recorder they came
                    from (their folder, else the start of their file name),
                    and each device gets its own track - its split files
                    (a camera starts a new file every ~30 minutes / 4 GB)
                    one after another on it. A multitrack recorder's files
                    (ZOOM0001_Tr1, _Tr2) are lanes: a track each.
    Fine sync and drift.
                    Sound matching places a clip to about a frame, and a
                    timecode only as well as it was jammed. So every clip is
                    then checked against the main recorder (the master
                    clock) at several points along it, to the millisecond:
                    placed by the middle of what it shares, and how far its
                    clock runs from the master's is measured and reported
                    (ms per hour). Nothing is retimed - Resolve's speed
                    change isn't something to apply behind someone's back -
                    but a recording that drifts by a frame or more says so.
"""

import os
import re

from pages.project_setup import otio_engine as oe
from pages.project_setup.otio_engine import OtioError

METHODS = ("timecode", "waveform")
CAMERA_AUDIO = ("tracks", "muted", "none")       # the cameras' own sound: kept, kept but off, left out

CAMERA = "camera"
MIC = "mic"

# The fine pass. Windows of the clip, matched against the master around
# where the first pass put them.
REFINE_RATE = 2000            # samples a second: half a millisecond
REFINE_WINDOW_S = 20.0        # how much of the clip each check matches
REFINE_MARGIN_S = 0.5         # how far either way it looks, after sound matching (good to ~20 ms)
REFINE_MARGIN_TC_S = 2.0      # ... and after timecode (only as good as the jam)
REFINE_MIN_STANDOUT = 3.0     # the match against what else the window holds
MAX_PROBE_DISAGREEMENT_S = 0.010   # a check this far from the fitted line is an outlier

# Drift worth a word: the ends of a recording placed by its middle are out
# by half what it drifts over its length.
DRIFT_FRAMES_WARN = 1.0

UNSYNCED_GAP_S = oe.SOLO_GAP_SECONDS        # unplaced clips go after the rest, this far apart


class SyncCancelled(Exception):
    """Raised by report() through the engine when the run is stopped."""


# ---------------------------------------------------------------- devices --

# Folder names that say nothing about the device ("D:\Footage\A001.MXF").
_GENERIC_FOLDERS = {"", "media", "footage", "clips", "audio", "video", "card", "cards", "dcim", "private",
                    "clip", "contents", "100media", "101media", "sound", "recordings", "podcast", "raw",
                    "m4root", "xdroot", "avchd", "bdmv", "stream", "mp_root", "100msdcf"}
# Where a person keeps things, not a device: looking any higher is guessing.
_HOME_FOLDERS = {"movies", "videos", "documents", "desktop", "downloads", "music", "pictures", "users", "home",
                 "volumes", "mnt"}

# The track of a multitrack recorder's file: ZOOM0001_Tr1, Take 3_MIC2, BWF-ch1, ..._LR.
_LANE = re.compile(r"(?:^|[ _\-.])(tr\s*\d+|tr[lr]+|lr|ch\s*\d+|mic\s*\d+|in\s*\d+|track\s*\d+)$", re.I)


# A clip counter in a file's name, not the device: "0001", "C0001", "A001",
# "GH010023", "ZOOM0002", "150" - three digits or more after up to four
# letters, or any digits alone. ("Cam1" is a device.)
_COUNTER = re.compile(r"[A-Za-z]{0,4}\d{3,}[A-Za-z]?|\d+")


def folder_device(path) -> str:
    """The folder a file is in, as a device name - unless that says nothing
    (Footage, DCIM, a card's CLIP folder - then the one above); "" when no
    folder below the home ones says anything."""
    path = str(path or "").replace("\\", "/")
    parts = [p for p in path.split("/")[:-1] if p]
    for folder in reversed(parts[-4:]):
        low = folder.lower()
        if low in _HOME_FOLDERS or re.fullmatch(r"[a-z]:", low):
            break
        if low not in _GENERIC_FOLDERS and not re.fullmatch(r"\d{3}[a-z]*", low):
            return folder
    return ""


def name_device(path) -> str:
    """The device a file's own name says: the name without its clip counters
    and its recorder track ("A001_C003_TESS" -> "TESS", "Ep 12 Cam A" ->
    "Ep Cam A", "ZOOM0002_Tr1" -> "ZOOM"); a name that's all counters, the
    letters it starts with ("C0001" -> "C", "A001_C003" -> "A")."""
    name = os.path.splitext(os.path.basename(str(path or "").replace("\\", "/")))[0]
    name = _LANE.sub("", name)
    tokens = [t for t in re.split(r"[ _\-.]+", name) if t]
    kept = [t for t in tokens if not _COUNTER.fullmatch(t)]
    if kept:
        return " ".join(kept)
    letters = re.match(r"[A-Za-z]+", tokens[0]) if tokens else None
    return letters.group(0) if letters else (name or "Unknown")


def device_of(path) -> str:
    """Which camera or recorder a file on its own came from: its folder if
    that says anything, else its name. devices_for() decides for a set of
    recordings, where a folder can turn out to hold several devices."""
    return folder_device(path) or name_device(path)


def devices_for(clips) -> dict:
    """{path: device} for a set of recordings. A folder names a device only
    where the recordings are sorted into folders (more than one that says
    anything), it holds one kind (all cameras or all mics) and its files'
    names agree on one device; otherwise - everything of an episode in one
    folder ("Ep 12"), cameras and mics together - each file goes by its
    name, so the folder doesn't become every recording's device."""
    folder = {c.path: folder_device(c.path) for c in clips}
    named = {c.path: name_device(c.path) for c in clips}
    sorted_into = len({f for f in folder.values() if f}) > 1
    usable = {}
    for f in {f for f in folder.values() if f}:
        inside = [c for c in clips if folder[c.path] == f]
        usable[f] = sorted_into and len({c.has_video for c in inside}) == 1 \
            and len({named[c.path] for c in inside}) == 1
    return {c.path: folder[c.path] if usable.get(folder[c.path]) else named[c.path] for c in clips}


def lane_of(path) -> str:
    """A multitrack recorder's track in a file's name ("Tr1", "MIC2"), or ""."""
    name = os.path.splitext(os.path.basename(str(path or "").replace("\\", "/")))[0]
    match = _LANE.search(name)
    return re.sub(r"\s+", "", match.group(1)).upper() if match else ""


class PodClip:
    """One recording: a camera clip (picture and its sound) or an audio file."""

    def __init__(self, logical):
        self.logical = logical
        self.id = logical.clip_id
        self.name = logical.name
        self.path = logical.path
        self.has_video = logical.has_video
        self.has_audio = logical.has_audio
        first = logical.entries[0]
        self.rate = float(first["rate"] or 24.0)            # the timeline's frames per second
        self.seconds = max(oe._duration(e["clip"]) for e in logical.entries) / self.rate
        sound = self._audio_entry() or first
        self.source_s = oe.source_offset_seconds(sound["clip"])
        # Where its sound starts after the clip itself (a camera clip's
        # picture and sound normally start together: 0).
        self.audio_lead_s = (sound["start"] - logical.record_frame) / self.rate
        # How far into its file the clip starts: timecode is the FILE's start.
        lead = min(logical.entries, key=lambda e: e["start"])
        self.trim_s = oe.source_offset_seconds(lead["clip"])
        self.timecode = logical.start_tc_seconds if oe.has_usable_timecode(logical) else None
        self.device = device_of(self.path)
        self.lane = lane_of(self.path)

    def _audio_entry(self):
        return next((e for e in self.logical.entries if e["kind"] == "Audio"), None)

    def audio_path(self):
        entry = self._audio_entry()
        return oe._media_path(entry["clip"]) if entry else self.path


def read_clips(document):
    """Every recording in an export, in timeline order."""
    oe.refuse_unsupported(document)
    return [PodClip(c) for c in oe.logical_clips(document)]


class Device:
    def __init__(self, name):
        self.name = name
        self.clips = []

    @property
    def kind(self):
        return CAMERA if any(c.has_video for c in self.clips) else MIC

    @property
    def seconds(self):
        return sum(c.seconds for c in self.clips)


def group_devices(clips, names=None, order=None):
    """The devices, each with its clips in recording order. names: {path:
    device name} the person changed; order: device names as they put them
    (cameras and mics each keep that order among themselves)."""
    names = names or {}
    found = devices_for(clips)
    devices = {}
    for clip in clips:
        clip.device = names.get(clip.path) or found[clip.path]
        devices.setdefault(clip.device, Device(clip.device)).clips.append(clip)
    for device in devices.values():
        device.clips.sort(key=lambda c: (c.timecode if c.timecode is not None else float("inf"), c.name.lower()))
    rank = {name: i for i, name in enumerate(order or [])}
    return sorted(devices.values(), key=lambda d: (d.kind != CAMERA, rank.get(d.name, len(rank)), d.name.lower()))


def master_device(devices, wanted=None):
    """The clock everything is measured against: the one named, else the
    mic recorder with the most recorded (the podcast's own recorder), else
    the camera with the most."""
    for device in devices:
        if device.name == wanted and any(c.has_audio for c in device.clips):
            return device
    heard = [d for d in devices if any(c.has_audio for c in d.clips)]
    if not heard:
        return None
    return max(heard, key=lambda d: (d.kind == MIC, d.seconds))


# ------------------------------------------------------------ first pass --

def by_timecode(clips):
    """Seconds from the earliest timecode, and why the rest weren't placed."""
    logical = [c.logical for c in clips]
    offsets, warnings = oe.compute_offsets_timecode(logical)
    # Project Setup's own check takes a folder for one device, so a
    # multitrack recorder's files (Tr1, Tr2 - recorded at once, by design)
    # would read as two days of footage. Checked here by device and lane.
    warnings = [w for w in warnings if not w.startswith("WARNING:")]
    spans = {}
    for clip in clips:
        if clip.timecode is not None:
            spans.setdefault((clip.device, clip.lane), []).append((clip.timecode, clip.timecode + clip.seconds))
    clashing = sorted({device for (device, _lane), runs in spans.items()
                       for (_a, end), (start, _b) in zip(sorted(runs), sorted(runs)[1:]) if start < end - 0.5})
    for device in clashing:
        warnings.append(f"Two clips from {device} claim the same time. One camera can't record two things at "
                        "once, so this is probably more than one day of footage - sync one day at a time.")
    unplaced = {c.id: "It has no timecode." for c in clips if c.id not in offsets}
    trim = {c.id: c.trim_s for c in clips}
    return {cid: at + trim[cid] for cid, at in offsets.items()}, warnings, unplaced


def by_sound(clips, ffmpeg_path, warnings, report, anchors=None):
    """Positions from the sound the clips share (Project Setup's matching).
    anchors: positions already known (from timecode) - then only the other
    clips are placed, each relative to the anchored ones in its group.

    Without anchors, groups that share nothing with each other (the
    recording stopped and started again with everything off) are laid one
    after another, the biggest first. Returns (positions, unplaced)."""
    heard = [c for c in clips if c.has_audio and c.path]
    groups, _considered = oe.build_sync_groups(clips=[c.logical for c in heard], ffmpeg_path=ffmpeg_path,
                                               warnings=warnings, progress_cb=report)
    groups = [g for g in groups if len(g) > 1]
    placed = {}
    if anchors:
        placed.update(anchors)
        for group in groups:
            known = [cid for cid in group if cid in anchors]
            if not known:
                continue
            for cid in group:
                if cid in placed:
                    continue
                guesses = sorted(anchors[k] + group[cid] - group[k] for k in known)
                placed[cid] = guesses[len(guesses) // 2]
    else:
        cursor = 0.0
        length = {c.id: c.seconds for c in clips}
        for group in sorted(groups, key=lambda g: (len(g), sum(length[c] for c in g)), reverse=True):
            base = min(group.values())
            for cid, at in group.items():
                placed[cid] = cursor + at - base
            cursor = max(placed[cid] + length[cid] for cid in group) + oe.GROUP_GAP_SECONDS
    unplaced = {}
    for clip in clips:
        if clip.id in placed:
            continue
        if not clip.has_audio:
            unplaced[clip.id] = "It has no sound to match."
        else:
            unplaced[clip.id] = "Its sound didn't match any other recording."
    return placed, unplaced


# ------------------------------------------------------------- fine pass --

def best_lag(reference, sample, rate):
    """Where `sample` sits inside the longer `reference`: (seconds from the
    reference's start, standout). The two are differenced first, which
    favours the onsets two different microphones both hear over the room
    and hum they don't share; the peak is placed to a fraction of a sample."""
    import numpy as np
    ref = np.diff(np.asarray(reference, dtype=np.float64))
    smp = np.diff(np.asarray(sample, dtype=np.float64))
    if len(smp) < rate or len(ref) < len(smp):
        return None, 0.0
    size = 1 << int(np.ceil(np.log2(len(ref) + len(smp))))
    cross = np.fft.irfft(np.fft.rfft(ref, size) * np.conj(np.fft.rfft(smp, size)), size)
    valid = cross[:len(ref) - len(smp) + 1]
    index = int(np.argmax(valid))
    peak = float(valid[index])
    if peak <= 0:
        return None, 0.0
    field = float(np.percentile(np.abs(valid), 90))
    shift = 0.0
    if 0 < index < len(valid) - 1:
        left, right = float(valid[index - 1]), float(valid[index + 1])
        bend = left - 2 * peak + right
        if bend < 0:
            shift = 0.5 * (left - right) / bend
    return (index + shift) / rate, (peak / field if field > 0 else float("inf"))


def fit_line(points):
    """(slope, value at t=0) through [(t, value)], robust to one bad point
    (Theil-Sen: the median of every pair's slope). One point: no slope."""
    points = sorted(points)
    if not points:
        return 0.0, 0.0
    if len(points) == 1:
        return 0.0, points[0][1]
    slopes = sorted((b[1] - a[1]) / (b[0] - a[0]) for i, a in enumerate(points) for b in points[i + 1:]
                    if b[0] != a[0])
    slope = slopes[len(slopes) // 2] if slopes else 0.0
    values = sorted(v - slope * t for t, v in points)
    return slope, values[len(values) // 2]


def _probe_times(start, end, window, margin):
    """Where along a shared stretch to check: more checks on a long one."""
    room = end - start - window - 2 * margin
    if room < 0:
        return []
    span = end - start
    count = 5 if span > 600 else 3 if span > 120 else 1
    if count == 1:
        return [start + margin + room / 2]
    return [start + margin + room * i / (count - 1) for i in range(count)]


def refine(clips, devices, positions, ffmpeg_path, report, margin=REFINE_MARGIN_S, master=None, loader=None):
    """The fine pass (see the module docstring). Returns (positions, drift):
    drift {clip id: {"against", "ms_per_hour", "spread_s", "checks"}} for
    each clip measured. loader(path, start, seconds, rate) -> samples, for
    tests; by default ffmpeg decodes the stretch."""
    if loader is None:
        from pages.project_setup import audio_sync

        def loader(path, start, seconds, rate):
            return audio_sync.load_window(ffmpeg_path, path, max(0.0, start), seconds, rate=rate)

    master = master or master_device(devices)
    if master is None:
        return dict(positions), {}
    out = dict(positions)
    drift = {}
    trusted = {c.id for c in master.clips if c.id in out}

    def span(clip):
        return out[clip.id], out[clip.id] + clip.seconds

    def overlap(a, b):
        return max(span(a)[0], span(b)[0]), min(span(a)[1], span(b)[1])

    todo = [c for d in devices if d is not master for c in d.clips if c.id in out and c.has_audio]
    # Clips sharing the most with the master go first; one that shares
    # nothing with it is matched against a clip already checked.
    todo.sort(key=lambda c: -max([0.0] + [overlap(c, m)[1] - overlap(c, m)[0] for m in master.clips
                                          if m.id in out and m.has_audio]))
    total = len(todo)
    for done, clip in enumerate(todo):
        report("Fine-tuning and measuring drift", done, total)
        candidates = [m for m in clips if m.id in trusted and m.has_audio and m.device != clip.device]
        best, best_len = None, 0.0
        for other in candidates:
            lo, hi = overlap(clip, other)
            if hi - lo > best_len:
                best, best_len = other, hi - lo
        if best is None:
            continue
        lo, hi = overlap(clip, best)
        points = []
        for t in _probe_times(lo, hi, REFINE_WINDOW_S, margin):
            try:
                sample = loader(clip.audio_path(), clip.source_s + (t - out[clip.id] - clip.audio_lead_s),
                                REFINE_WINDOW_S, REFINE_RATE)
                ref = loader(best.audio_path(), best.source_s + (t - out[best.id] - best.audio_lead_s) - margin,
                             REFINE_WINDOW_S + 2 * margin, REFINE_RATE)
            except Exception:  # noqa: BLE001 - a stretch that won't read is one check fewer
                continue
            at, standout = best_lag(ref, sample, REFINE_RATE)
            if at is None or standout < REFINE_MIN_STANDOUT:
                continue
            points.append((t + REFINE_WINDOW_S / 2, at - margin))
        if not points:
            continue
        slope, value = fit_line(points)
        good = [(t, v) for t, v in points if abs(v - (value + slope * t)) <= MAX_PROBE_DISAGREEMENT_S]
        if len(good) < max(1, (len(points) + 1) // 2):
            continue                                  # the checks don't agree: keep the first pass
        slope, value = fit_line(good)
        middle = (lo + hi) / 2
        out[clip.id] = out[clip.id] + value + slope * middle
        trusted.add(clip.id)
        if len(good) >= 2:
            drift[clip.id] = {"against": best.device, "ms_per_hour": slope * 3600_000.0,
                              "spread_s": abs(slope) * clip.seconds, "checks": len(good)}
    report("Fine-tuning and measuring drift", total, max(total, 1))
    return out, drift


# ---------------------------------------------------------------- layout --

def _lanes(entries):
    """[(start, end, entry)] onto as few lanes as fit without overlapping."""
    lanes = []
    for start, end, entry in sorted(entries, key=lambda e: e[0]):
        for lane in lanes:
            if lane[-1][1] <= start + 1e-6:
                lane.append((start, end, entry))
                break
        else:
            lanes.append([(start, end, entry)])
    return lanes


def layout(document, clips, devices, positions, camera_audio="tracks"):
    """The new document, and its tracks [{kind, number, name, device, role,
    enabled}]. Each clip is moved as a whole (its picture and sound stay
    together) to its position in whole frames; unplaced clips follow the
    rest, a gap apart, on their own device's tracks."""
    if camera_audio not in CAMERA_AUDIO:
        camera_audio = "tracks"
    rate = clips[0].rate if clips else 24.0
    frames = {}
    for clip in clips:
        if clip.id in positions:
            frames[clip.id] = round(positions[clip.id] * rate)
    if frames:
        low = min(frames[c.id] + min(e["start"] for e in c.logical.entries) - c.logical.record_frame
                  for c in clips if c.id in frames)
        frames = {cid: f - low for cid, f in frames.items()}
        cursor = max(frames[c.id] + c.seconds * rate for c in clips if c.id in frames) + UNSYNCED_GAP_S * rate
    else:
        cursor = 0.0
    for device in devices:              # unplaced ones, device by device after the rest
        for clip in device.clips:
            if clip.id not in frames:
                frames[clip.id] = round(cursor)
                cursor += clip.seconds * rate + UNSYNCED_GAP_S * rate

    groups = []                          # (kind, role, device, label, [(start, end, entry)])
    for device in devices:
        video, audio = {}, {}
        for clip in device.clips:
            shift = frames[clip.id] - clip.logical.record_frame
            sounds = 0
            for entry in clip.logical.entries:
                start = entry["start"] + shift
                item = (start, start + oe._duration(entry["clip"]), entry)
                if entry["kind"] == "Video":
                    video.setdefault("", []).append(item)
                else:
                    audio.setdefault((clip.lane, sounds), []).append(item)
                    sounds += 1
        if video:
            groups.append(("Video", CAMERA, device.name, device.name, video[""]))
        role = MIC if device.kind == MIC else "camera_audio"
        if role == "camera_audio" and camera_audio == "none":
            continue
        several = len({lane for lane, _n in audio}) > 1
        for (lane, number), items in sorted(audio.items()):
            label = device.name + (f" {lane}" if lane and several else "") + (f" {number + 1}" if number else "")
            groups.append(("Audio", role, device.name, label, items))

    # Mics first, then the cameras' own sound - each in the device order.
    groups.sort(key=lambda g: (g[0] != "Video", g[1] == "camera_audio"))
    placements, tracks = [], []
    count = {"Video": 0, "Audio": 0}
    for kind, role, device, label, items in groups:
        lanes = _lanes(items)
        for index, lane in enumerate(lanes):
            count[kind] += 1
            name = label if len(lanes) == 1 else f"{label} ({index + 1})"
            tracks.append({"kind": kind, "number": count[kind], "name": name, "device": device, "role": role,
                           "enabled": not (role == "camera_audio" and camera_audio == "muted")})
            for start, _end, entry in lane:
                placements.append((entry, kind, count[kind], start))
    out = oe.build_document(document, placements)
    numbered = {"Video": 0, "Audio": 0}
    names = {(t["kind"], t["number"]): t for t in tracks}
    for track in out["tracks"]["children"]:
        numbered[track["kind"]] += 1
        info = names.get((track["kind"], numbered[track["kind"]]))
        if info:
            track["name"] = info["name"]
            track["enabled"] = info["enabled"]
    return out, tracks


# ------------------------------------------------------------------- run --

def sync(document, method, ffmpeg_path=None, report=None, names=None, order=None, fine=True,
         camera_audio="tracks", master=None, loader=None):
    """The whole run: (new document, result). result: {devices, tracks,
    placed, unplaced {clip name: why}, drift [...], warnings, master, method}."""
    def say(stage, done=0, total=1):
        if report is not None:
            report(stage, done, total)

    if method not in METHODS:
        raise OtioError("Choose Timecode or Sound to sync by.")
    clips = read_clips(document)
    if not clips:
        raise OtioError("There are no clips to sync.")
    devices = group_devices(clips, names, order)
    by_id = {c.id: c for c in clips}
    warnings = list(oe.rebuild_warnings(document))
    needs_sound = method == "waveform" or fine
    if needs_sound and not ffmpeg_path:
        if method == "waveform":
            raise OtioError("Syncing by sound needs ffmpeg, and Buddy couldn't find it. Install ffmpeg, or say "
                            "where it is in Project Setup's settings.")
        warnings.append("ffmpeg wasn't found, so the clips were placed by timecode alone - no fine-tuning or "
                        "drift check.")
        fine = False

    say("Lining up the recordings")
    if method == "timecode":
        positions, tc_warnings, unplaced = by_timecode(clips)
        warnings.extend(tc_warnings)
        if fine and any(by_id[cid].has_audio for cid in unplaced):
            positions, unplaced = by_sound(clips, ffmpeg_path, warnings, say, anchors=positions)
    else:
        if sum(1 for c in clips if c.has_audio) < 2:
            raise OtioError("Syncing by sound needs at least two recordings with sound.")
        positions, unplaced = by_sound(clips, ffmpeg_path, warnings, say)
        if not positions:
            raise OtioError("None of the recordings share any sound, so there's nothing to line them up by. "
                            "They need to have been rolling at the same time.")

    chosen = master_device(devices, master)
    drift = {}
    if fine and positions and chosen is not None:
        positions, drift = refine(clips, devices, positions, ffmpeg_path, say,
                                  margin=REFINE_MARGIN_S if method == "waveform" else REFINE_MARGIN_TC_S,
                                  master=chosen, loader=loader)

    say("Building the timeline")
    out, tracks = layout(document, clips, devices, positions, camera_audio)
    drift_rows = []
    for cid, info in drift.items():
        clip = by_id[cid]
        frames = info["spread_s"] * clip.rate
        drift_rows.append({"clip": clip.name, "device": clip.device, "against": info["against"],
                           "ms_per_hour": round(info["ms_per_hour"], 1), "frames": round(frames, 2),
                           "warn": frames >= DRIFT_FRAMES_WARN})
    drift_rows.sort(key=lambda r: (-abs(r["ms_per_hour"]), r["clip"]))
    result = {
        "method": method,
        "master": chosen.name if chosen is not None else "",
        "fine": bool(fine),
        "devices": [{"name": d.name, "kind": d.kind, "clips": len(d.clips),
                     "placed": sum(1 for c in d.clips if c.id in positions)} for d in devices],
        "tracks": tracks,
        "placed": len(positions),
        "total": len(clips),
        "unplaced": [{"clip": by_id[cid].name, "device": by_id[cid].device, "why": why}
                     for cid, why in unplaced.items() if cid not in positions],
        "drift": drift_rows,
        "warnings": warnings,
    }
    return out, result


def device_drift(rows):
    """Each device's drift: the median of its clips' {device: ms_per_hour}."""
    by = {}
    for row in rows:
        by.setdefault(row["device"], []).append(row["ms_per_hour"])
    return {name: sorted(values)[len(values) // 2] for name, values in by.items()}
