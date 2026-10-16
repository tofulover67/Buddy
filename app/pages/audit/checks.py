#!/usr/bin/env python3
"""
What Audit looks for - plain functions over resolve_ext.scan()'s data, no
Resolve and no Qt, so every rule is tested on made-up timelines.

run(scan, fonts, off) -> {"groups": [...], "passed": [...], "counts": {...}}
A group is one check that found something: its rows, worst first, each with
the timecode and track to go to. Three severities: "problem" (it will come
out wrong), "warning" (very likely a mistake) and "note" (worth a look,
often on purpose).
"""

import os
import re

from pages.stills_exporter.resolve_ext import frames_to_timecode

from .audio import DEFAULT_TARGET, TARGET_BY_ID, TOLERANCE_LU

PROBLEM, WARNING, NOTE = "problem", "warning", "note"
SEVERITIES = (PROBLEM, WARNING, NOTE)

# A clip this many frames long, or shorter, is a flash frame.
FLASH_FRAMES = 2
# Black for up to this many seconds is a slip (a warning); longer, perhaps meant (a note).
SHORT_BLACK_S = 1.0
# Subtitles: the widely used limits (Netflix's, the BBC's are close).
SUB_LINE_CHARS = 42
SUB_LINES = 2
SUB_CPS = 20.0
SUB_MIN_S = 5 / 6
# More rows than this in one check aren't listed (the count still says how many).
MAX_ROWS = 200

TODO_RE = re.compile(r"\b(?:to ?do|fix(?:me)?|tbd|temp|placeholder|replace|wip)\b|\?\?|xxx", re.I)
# Text a title is made with, or nothing at all: never meant to be delivered.
DEFAULT_TITLES = {"", "custom title", "title", "text", "your text here", "sample text", "enter text"}

CHECKS = [
    {"id": "offline", "title": "Missing media", "severity": PROBLEM,
     "about": "The file behind the clip isn't where Resolve expects it. It renders as a red 'Media Offline' frame."},
    {"id": "fonts", "title": "Fonts not installed", "severity": PROBLEM,
     "about": "A Text+ title uses a font (or a style of one) this computer doesn't have. Resolve draws it in another."},
    {"id": "black", "title": "Black gaps", "severity": WARNING,
     "about": "Nothing is on any video track here, so the export shows black."},
    {"id": "flash", "title": "Flash frames", "severity": WARNING,
     "about": "A clip only one or two frames long – usually left behind by a trim or a cut."},
    {"id": "tracks_off", "title": "Tracks turned off", "severity": WARNING,
     "about": "A track that's turned off (or muted) isn't in the export, clips and all."},
    {"id": "clips_off", "title": "Clips turned off", "severity": WARNING,
     "about": "A disabled clip isn't in the export."},
    {"id": "title_text", "title": "Unfinished titles", "severity": WARNING,
     "about": "A Text+ title that's empty or still has its starting text."},
    {"id": "todo", "title": "To-do markers", "severity": WARNING,
     "about": "A marker whose name or note says there's still something to do (fix, todo, TBD, temp, ??)."},
    {"id": "sub_length", "title": "Long subtitles", "severity": WARNING,
     "about": f"Subtitles read best as at most {SUB_LINES} lines of {SUB_LINE_CHARS} characters."},
    {"id": "sub_speed", "title": "Fast subtitles", "severity": WARNING,
     "about": f"More than {SUB_CPS:g} characters a second, or on screen for under {SUB_MIN_S:.2g} s, is hard to read."},
    {"id": "rate", "title": "Frame rate differs", "severity": NOTE,
     "about": "Footage shot at another frame rate than the timeline's plays with repeated or skipped frames "
              "– fine when it's slow motion on purpose."},
    {"id": "low_res", "title": "Enlarged pictures", "severity": NOTE,
     "about": "Smaller than the timeline, so it's scaled up to fill the frame and may look soft."},
    # The sound checks: made on the rendered mix (audio.py), with Check sound.
    {"id": "clipping", "title": "Clipping", "severity": PROBLEM, "sound": True,
     "about": "The mix goes over full scale here and is cut off – it crackles or distorts."},
    {"id": "peaks", "title": "Peaks too high", "severity": WARNING, "sound": True,
     "about": "The mix peaks above the ceiling of the target you picked – it may distort once it's encoded."},
    {"id": "loudness", "title": "Loudness off target", "severity": NOTE, "sound": True,
     "about": "The whole mix's loudness (BS.1770), against the target you picked."},
    # The recordings: each clip's own stretch of its file (sources.py), also with Check sound.
    {"id": "rec_clipped", "title": "Clipped recordings", "severity": WARNING, "sound": True, "recording": True,
     "about": "The recording itself is cut off here – it was too loud when it was recorded. "
              "Turning the clip down doesn't fix it; another take, or a de-clipper, can."},
    {"id": "dropouts", "title": "Dropouts", "severity": WARNING, "sound": True, "recording": True,
     "about": "A gap of digital silence in the middle of the sound – the recorder lost some audio. "
              "It's heard as a click or a stutter."},
    {"id": "one_side", "title": "Sound on one side", "severity": WARNING, "sound": True, "recording": True,
     "about": "A stereo clip with sound on one side only – often a mic recorded on one channel. "
              "It plays from one speaker: in Clip Attributes, make it mono from the channel with the sound."},
    {"id": "phase", "title": "Out of phase", "severity": WARNING, "sound": True, "recording": True,
     "about": "Left and right cancel each other out: played in mono – a phone, a TV – the clip nearly disappears. "
              "Usually one side's polarity is flipped."},
    {"id": "rumble", "title": "Pops and wind", "severity": NOTE, "sound": True, "recording": True,
     "about": "Low thumps in speech – a 'p' too close to the mic, the mic knocked – or wind. "
              "Audio Assistant's Rumble filter takes most of it out."},
]
BY_ID = {c["id"]: c for c in CHECKS}

# What a row's fix does (resolve_ext.fix). "ask": the view says what will
# happen and waits for a yes first - those take something off the timeline.
# The rest only switch on what was switched off, and are as easily undone.
FIXES = {
    "track_on": {"label": "Turn on", "ask": False},
    "clip_on": {"label": "Turn on", "ask": False},
    "delete_clip": {"label": "Delete", "ask": True},
    "close_gap": {"label": "Close gap", "ask": True},
    "delete_marker": {"label": "Remove marker", "ask": True},
}
# Checks whose rows can all be fixed in one go, with the group's button.
FIX_ALL = {"tracks_off": "Turn all on", "clips_off": "Turn all on"}
# Fonts can only be checked with the list of installed ones.
NEEDS_FONTS = {"fonts"}
# Checked only once the mix has been measured.
SOUND = {c["id"] for c in CHECKS if c.get("sound")}
# ... and these once the recordings have been listened to.
RECORDING = {c["id"] for c in CHECKS if c.get("recording")}
# Peaks or clipping closer together than this are one place.
EVENT_GAP_S = 0.5
# A place longer than this says how long.
LONG_EVENT_S = 1.0


def _where(track):
    return "VAS"[("video", "audio", "subtitle").index(track["kind"])] + str(track["index"])


def _row(scan, severity, frame, where, clip, detail, times=1, fix=None, own=False):
    """One finding. `detail` is one of the sentences in the module's
    translations (with its numbers); `clip` is the user's own text - or,
    with `own`, Buddy's words, to be translated; `times`: how many uses of
    the same thing the row stands for; `fix`: what resolve_ext.fix() is
    asked to do about it, or None (see FIXES)."""
    return {"severity": severity, "frame": int(frame), "where": where, "clip": clip, "detail": detail,
            "times": times, "tc": frames_to_timecode(int(frame), scan["fps"], scan.get("drop", False)),
            "fix": fix, "own": own}


def _seconds(frames, fps):
    s = frames / (fps or 24.0)
    return f"{s:.1f}" if s < 10 else f"{round(s)}"


def _tracks(scan, kind):
    return [t for t in scan["tracks"] if t["kind"] == kind]


def _ref(track, clip):
    """How resolve_ext.fix() finds a clip again: its track and where it sits."""
    return {"kind": track["kind"], "index": track["index"], "start": clip["start"], "end": clip["end"],
            "name": clip["name"]}


def _spans(scan, leave=()):
    """(start, end) of every clip on every track, on or off - but the ones
    in `leave` (_ref()s)."""
    gone = {(r["kind"], r["index"], r["start"], r["end"]) for r in leave}
    return [(c["start"], c["end"]) for t in scan["tracks"] for c in t["items"]
            if (t["kind"], t["index"], c["start"], c["end"]) not in gone and c["end"] > c["start"]]


def _empty(spans, start, end):
    """Nothing in `spans` overlaps start..end."""
    return not any(a < end and b > start for a, b in spans)


def _pictures(scan):
    """(track, clip) for every clip on a video track."""
    return [(t, c) for t in _tracks(scan, "video") for c in t["items"]]


# ------------------------------------------------------------------ checks --

def check_offline(scan):
    seen, rows = set(), []
    for track in scan["tracks"]:
        for clip in track["items"]:
            key = (clip.get("path"), clip["start"])
            if clip.get("online", True) or key in seen:
                continue
            seen.add(key)
            rows.append(_row(scan, PROBLEM, clip["start"], _where(track), clip["name"], clip.get("path") or ""))
    return rows


def check_tracks_off(scan):
    rows = []
    for track in scan["tracks"]:
        if track["enabled"] or not track["items"]:
            continue
        n = len(track["items"])
        rows.append(_row(scan, WARNING, track["items"][0]["start"], _where(track), track["name"] or _where(track),
                         "Turned off – 1 clip on it" if n == 1 else f"Turned off – {n} clips on it",
                         fix={"do": "track_on", "kind": track["kind"], "index": track["index"]}))
    return rows


CURVE_SUFFIX = " (Buddy curve)"
BUDDY_WAV = " - Buddy "


def stood_in(scan):
    """{(index, start, end): "curve" | "wav"} for the audio clips Audio
    Assistant turned off on purpose, as something of Buddy's plays in their
    place: a curve clip (a timeline named for the clip, over the same
    frames), or a processed or fitted WAV ("<the file's name> - Buddy
    <tag>.wav", from the same frame). Turning one on would play it twice."""
    tracks = [t for t in _tracks(scan, "audio") if t["enabled"]]
    on = [c for t in tracks for c in t["items"] if c["enabled"]]
    curves = {(c["name"], c["start"], c["end"]) for c in on if c["name"].endswith(CURVE_SUFFIX)}
    wavs = set()
    for c in on:
        name = os.path.basename(c.get("path") or "")
        if BUDDY_WAV in name and name.lower().endswith(".wav"):
            wavs.add((name.split(BUDDY_WAV)[0], c["start"]))
    out = {}
    for t in tracks:
        for c in t["items"]:
            if c["enabled"]:
                continue
            stem = os.path.splitext(os.path.basename(c.get("path") or ""))[0][:60]
            if stem and (stem, c["start"]) in wavs:
                out[(t["index"], c["start"], c["end"])] = "wav"
            elif (c["name"] + CURVE_SUFFIX, c["start"], c["end"]) in curves:
                out[(t["index"], c["start"], c["end"])] = "curve"
    return out


def check_clips_off(scan):
    buddy = stood_in(scan)
    return [_row(scan, WARNING, c["start"], _where(t), c["name"], "Disabled", fix={"do": "clip_on", **_ref(t, c)})
            for t in scan["tracks"] if t["enabled"] for c in t["items"] if not c["enabled"]
            and not (t["kind"] == "audio" and (t["index"], c["start"], c["end"]) in buddy)]


def _covered(scan):
    """[(start, end)] where some enabled clip on an enabled video track has
    a picture, merged and in order. An adjustment clip has none of its own."""
    spans = sorted((c["start"], c["end"]) for t in _tracks(scan, "video") if t["enabled"] for c in t["items"]
                   if c["enabled"] and c["end"] > c["start"] and c["name"] != "Adjustment Clip")
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def check_black(scan):
    """Stretches with no picture between the timeline's start and its last
    picture - including before the first clip."""
    covered = _covered(scan)
    if not covered:
        return []
    rows, at = [], scan["start"]
    short = round(SHORT_BLACK_S * scan["fps"])
    spans = _spans(scan)
    first = min(a for a, _b in spans)
    for start, end in covered:
        if start > at:
            n = start - at
            where = "Before the first clip" if at == scan["start"] else ""
            # Closed only where nothing at all is on the timeline - no sound,
            # no subtitle - and never before the first clip (a countdown,
            # perhaps): Command Center's Remove gaps' rule, and its way.
            fix = ({"do": "close_gap", "start": at, "end": start}
                   if at > first and _empty(spans, at, start) else None)
            if n > short:
                rows.append(_row(scan, NOTE, at, "", where, f"{_seconds(n, scan['fps'])} s of black", fix=fix,
                                 own=True))
            else:
                rows.append(_row(scan, WARNING, at, "", where, "1 frame of black" if n == 1 else f"{n} frames of black",
                                 fix=fix, own=True))
        at = max(at, end)
    return rows


def check_flash(scan):
    rows = []
    for track, clip in _pictures(scan):
        n = clip["end"] - clip["start"]
        if 0 < n <= FLASH_FRAMES and clip["enabled"]:
            rows.append(_row(scan, WARNING, clip["start"], _where(track), clip["name"],
                             "1 frame long" if n == 1 else f"{n} frames long", fix=_flash_fix(scan, track, clip)))
    return rows


def linked_sound(scan, clip):
    """The audio clips that came in with a picture clip: the same file over
    the same frames - deleted with it, as Resolve's own Delete does."""
    path = clip.get("path")
    if not path:
        return []
    return [_ref(t, c) for t in _tracks(scan, "audio") for c in t["items"]
            if c.get("path") == path and (c["start"], c["end"]) == (clip["start"], clip["end"])]


def _flash_fix(scan, track, clip):
    """Delete the clip and its sound. "then" says what's left where it was:
    "close" - nothing on any track, so the delete ripples the rest up over
    it; "black" - no other picture shows there; "" - another one does."""
    sound = linked_sound(scan, clip)
    if _empty(_spans(scan, [_ref(track, clip), *sound]), clip["start"], clip["end"]):
        then = "close"
    else:
        rest = {**scan, "tracks": [{**t, "items": [c for c in t["items"] if c is not clip]} for t in scan["tracks"]]}
        shown = any(a <= clip["start"] and b >= clip["end"] for a, b in _covered(rest))
        then = "" if shown else "black"
    return {"do": "delete_clip", **_ref(track, clip), "sound": sound, "then": then}


def check_rate(scan):
    """One row per file, at its first use: the rate is the footage's own."""
    uses = {}
    for track, clip in _pictures(scan):
        fps = clip.get("fps") or 0
        if clip.get("type") in ("Video", "Video + Audio") and fps and abs(fps - scan["fps"]) > 0.01:
            uses.setdefault(clip.get("path") or clip["name"], []).append((clip["start"], track, clip))
    rows = []
    for found in uses.values():
        start, track, clip = min(found, key=lambda f: f[0])
        rows.append(_row(scan, NOTE, start, _where(track), clip["name"],
                         f"{clip['fps']:g} fps on a {scan['fps']:g} fps timeline", len(found)))
    return rows


def check_low_res(scan):
    if not scan.get("width") or not scan.get("height"):
        return []
    uses = {}
    for track, clip in _pictures(scan):
        res = clip.get("res")
        if res and res[0] < scan["width"] and res[1] < scan["height"]:
            uses.setdefault(clip.get("path") or clip["name"], []).append((clip["start"], track, clip))
    rows = []
    for found in uses.values():
        start, track, clip = min(found, key=lambda f: f[0])
        w, h = clip["res"]
        rows.append(_row(scan, NOTE, start, _where(track), clip["name"],
                         f"{w}×{h} on a {scan['width']}×{scan['height']} timeline", len(found)))
    return rows


def _todo(marker):
    return bool(TODO_RE.search(marker["name"]) or TODO_RE.search(marker["note"]))


def _marker_text(marker):
    return " – ".join(p for p in (marker["name"], marker["note"]) if p and p != "Marker")


def _seen(marker):
    """The marker as the check saw it: Remove takes it off only if it's still
    this one (resolve_ext), not one edited or put there since."""
    return {"marker": marker["name"], "note": marker["note"], "color": marker["color"]}


def check_todo(scan):
    """A clip's marker goes to the clip's start: where on the clip it sits
    is counted from the media's own start, which Audit doesn't read."""
    rows = [_row(scan, WARNING, scan["start"] + m["frame"], "", _marker_text(m), "Timeline marker",
                 fix={"do": "delete_marker", "frame": m["frame"], **_seen(m)})
            for m in scan["markers"] if _todo(m)]
    for track in scan["tracks"]:
        for clip in track["items"]:
            rows += [_row(scan, WARNING, clip["start"], _where(track), _marker_text(m), "Marker on a clip",
                          fix={"do": "delete_marker", "frame": m["frame"], **_seen(m),
                               "clip": _ref(track, clip)})
                     for m in clip.get("markers", ()) if _todo(m)]
    return rows


def _norm(name):
    return re.sub(r"[\s_-]+", "", str(name or "")).casefold()


def check_fonts(scan, fonts):
    """One row per font (or font style), at its first use. `fonts`: the
    installed families -> their styles (an empty list: styles unknown)."""
    families = {_norm(family): {_norm(s) for s in styles} for family, styles in fonts.items()}
    uses = {}
    for track, clip in _pictures(scan):
        for title in clip.get("titles", ()):
            family = _norm(title["font"])
            if not family:
                continue
            styles = families.get(family)
            if styles is None:
                missing = title["font"]
            elif styles and title["style"] and _norm(title["style"]) not in styles:
                missing = f"{title['font']} {title['style']}"
            else:
                continue
            uses.setdefault(missing, []).append((clip["start"], track, clip))
    rows = []
    for font, found in uses.items():
        start, track, clip = min(found, key=lambda f: f[0])
        rows.append(_row(scan, PROBLEM, start, _where(track), font, "Not installed", len(found)))
    return rows


def check_title_text(scan):
    rows = []
    for track, clip in _pictures(scan):
        for title in clip.get("titles", ()):
            text = " ".join(title["text"].split())
            if text.casefold() in DEFAULT_TITLES or text.casefold().startswith("lorem ipsum"):
                rows.append(_row(scan, WARNING, clip["start"], _where(track), clip["name"],
                                 "Empty" if not text else f"Still says “{text}”"))
                break
    return rows


def _lines(text):
    return [line.strip() for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]


def check_sub_length(scan):
    rows = []
    for track in _tracks(scan, "subtitle"):
        for clip in track["items"]:
            lines = _lines(clip["name"])
            longest = max((len(line) for line in lines), default=0)
            many, wide = len(lines) > SUB_LINES, longest > SUB_LINE_CHARS
            if many and wide:
                why = f"{len(lines)} lines, the longest {longest} characters"
            elif many:
                why = f"{len(lines)} lines"
            elif wide:
                why = f"A line of {longest} characters"
            else:
                continue
            rows.append(_row(scan, WARNING, clip["start"], _where(track), " / ".join(lines), why))
    return rows


def check_sub_speed(scan):
    rows = []
    for track in _tracks(scan, "subtitle"):
        for clip in track["items"]:
            lines = _lines(clip["name"])
            seconds = (clip["end"] - clip["start"]) / (scan["fps"] or 24.0)
            if not lines or seconds <= 0:
                continue
            chars = len(" ".join(lines))
            cps = chars / seconds
            if cps > SUB_CPS:
                rows.append(_row(scan, WARNING, clip["start"], _where(track), " / ".join(lines),
                                 f"{cps:.0f} characters a second"))
            elif seconds < SUB_MIN_S:
                rows.append(_row(scan, WARNING, clip["start"], _where(track), " / ".join(lines),
                                 f"On screen for {seconds:.2f} s"))
    return rows


# ----------------------------------------------------------- the sound --

def _events(frames, gap):
    """[(first, last)] of sorted frames, those `gap` or fewer apart as one."""
    out = []
    for frame in frames:
        if out and frame - out[-1][1] <= gap:
            out[-1][1] = frame
        else:
            out.append([frame, frame])
    return [tuple(e) for e in out]


def _length(scan, first, last):
    """" for {s} s" for a place longer than LONG_EVENT_S, else ""."""
    n = last - first + 1
    return f" for {_seconds(n, scan['fps'])} s" if n > LONG_EVENT_S * scan["fps"] else ""


def check_clipping(scan, sound, target):
    gap = round(EVENT_GAP_S * scan["fps"])
    return [_row(scan, PROBLEM, scan["start"] + first, "", "Mix", "Hits full scale" + _length(scan, first, last),
                 own=True)
            for first, last in _events(sound.get("clipped") or (), gap)]


def check_peaks(scan, sound, target):
    clipped = set(sound.get("clipped") or ())
    over = {f: db for f, db in sound.get("peaks") or () if db > target["ceiling"] and f not in clipped}
    gap = round(EVENT_GAP_S * scan["fps"])
    rows = []
    for first, last in _events(sorted(over), gap):
        top = max(db for f, db in over.items() if first <= f <= last)
        rows.append(_row(scan, WARNING, scan["start"] + first, "", "Mix",
                         f"Peaks at {top:+.1f} dBTP{_length(scan, first, last)}", own=True))
    return rows


def check_loudness(scan, sound, target):
    lufs = sound.get("lufs")
    if lufs is None:
        has_sound = any(t["items"] for t in _tracks(scan, "audio"))
        return [_row(scan, WARNING, scan["start"], "", "Mix", "No sound at all", own=True)] if has_sound else []
    off = lufs - target["lufs"]
    if abs(off) <= TOLERANCE_LU:
        return []
    side = "louder" if off > 0 else "quieter"
    return [_row(scan, WARNING if target["strict"] else NOTE, scan["start"], "", "Mix",
                 f"{lufs:.1f} LUFS – {abs(off):.1f} LU {side} than the {target['lufs']:g} LUFS target", own=True)]


def _rec_rows(scan, sound, kind, severity, detail):
    """A row for each place a recording check found (sources.listen()'s
    `kind`): detail(first, last) - seconds into the clip - says what."""
    rows = []
    fps = scan["fps"] or 24.0
    for clip in sound.get("sources") or ():
        for first, last in clip["found"].get(kind) or ():
            frame = min(clip["end"] - 1, clip["start"] + int(first * fps))
            rows.append(_row(scan, severity, frame, clip["where"], clip["clip"], detail(first, last)))
    return rows


def _for(seconds):
    """" for {s} s" for a place longer than LONG_EVENT_S, else ""."""
    return f" for {seconds:.1f} s" if seconds > LONG_EVENT_S else ""


def check_rec_clipped(scan, sound, target):
    return _rec_rows(scan, sound, "clipped", WARNING, lambda a, b: "Cut off in the recording" + _for(b - a))


def check_dropouts(scan, sound, target):
    return _rec_rows(scan, sound, "dropouts", WARNING,
                     lambda a, b: f"Drops out for {max(1, round((b - a) * 1000))} ms")


def check_one_side(scan, sound, target):
    return [_row(scan, WARNING, clip["start"], clip["where"], clip["clip"],
                 "Sound on the left only" if clip["found"]["side"] == "left" else "Sound on the right only")
            for clip in sound.get("sources") or () if clip["found"].get("side")]


def check_phase(scan, sound, target):
    return [_row(scan, WARNING, clip["start"], clip["where"], clip["clip"], "Left and right cancel out in mono")
            for clip in sound.get("sources") or () if clip["found"].get("phase") is not None]


def check_rumble(scan, sound, target):
    return (_rec_rows(scan, sound, "pops", NOTE, lambda a, b: "A pop")
            + _rec_rows(scan, sound, "wind", NOTE, lambda a, b: "Wind or handling noise" + _for(b - a)))


SOUND_RUNNERS = {"clipping": check_clipping, "peaks": check_peaks, "loudness": check_loudness,
                 "rec_clipped": check_rec_clipped, "dropouts": check_dropouts, "one_side": check_one_side,
                 "phase": check_phase, "rumble": check_rumble}


RUNNERS = {
    "offline": check_offline, "black": check_black, "flash": check_flash, "tracks_off": check_tracks_off,
    "clips_off": check_clips_off, "title_text": check_title_text, "todo": check_todo,
    "sub_length": check_sub_length, "sub_speed": check_sub_speed, "rate": check_rate, "low_res": check_low_res,
}


def run(scan, fonts=None, off=(), sound=None, target=None):
    """The checks not in `off`, on one scan. Fonts are checked only with
    `fonts` ({installed family: [its styles]}); the sound only with `sound`
    (audio.measure()'s, of this timeline), against `target` (one of
    audio.TARGETS)."""
    groups, passed, skipped = [], [], []
    counts = dict.fromkeys(SEVERITIES, 0)
    for check in CHECKS:
        cid = check["id"]
        if cid in off or (cid in NEEDS_FONTS and fonts is None) or (cid in SOUND and sound is None) \
                or (cid in RECORDING and sound.get("sources") is None) \
                or (cid in SOUND - RECORDING and not sound.get("mixed", True)):
            skipped.append(cid)
            continue
        if cid in SOUND:
            rows = SOUND_RUNNERS[cid](scan, sound, target or TARGET_BY_ID[DEFAULT_TARGET])
        else:
            rows = check_fonts(scan, fonts) if cid == "fonts" else RUNNERS[cid](scan)
        if not rows:
            passed.append(cid)
            continue
        rows.sort(key=lambda r: (SEVERITIES.index(r["severity"]), r["frame"]))
        for r in rows:
            counts[r["severity"]] += 1
        worst = rows[0]["severity"]
        fixes = [r["fix"] for r in rows if r["fix"]]
        groups.append({"id": cid, "title": check["title"], "about": check["about"], "severity": worst,
                       "count": len(rows), "rows": rows[:MAX_ROWS],
                       "fix_all": ({"label": FIX_ALL[cid], "fixes": fixes}
                                   if cid in FIX_ALL and len(fixes) > 1 else None)})
    groups.sort(key=lambda g: SEVERITIES.index(g["severity"]))
    return {"groups": groups, "passed": passed, "skipped": skipped, "counts": counts}
