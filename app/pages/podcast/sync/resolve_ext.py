#!/usr/bin/env python3
"""
Podcast Sync's calls to Resolve - the two that take a while. No Qt: both
run in the Resolve reader's process (core/resolve_reader_child.py JOBS,
"podsync.read" and "podsync.build"; core/busy_job.py), so Buddy keeps
drawing while Resolve works, and here only where there's no reader.

    read_here(controller, source)
        the recordings as an OTIO document, written to a file:
        {"path", "workdir", "base", "what", "warnings"}. source "timeline":
        the open timeline's export; "selected" / "bin": the Media Pool's
        selected clips or the current bin's, put on a temporary timeline to
        export (Resolve exports timelines only), which is deleted again and
        the timeline and bin that were open opened again.
    build_here(controller, path, workdir, base, tracks)
        the synced document at `path` imported as a NEW timeline "<base>
        (Podcast Sync)", its tracks named (and turned off) as `tracks` says:
        {"timeline": its name, "warnings"}.

    move_here(controller, moves, name)
        the other way to finish, when asked to sync the OPEN timeline where
        it is ("podsync.move"): each item moved along its own track to where
        `moves` says (engine.moves_in_place) - nothing cut, nothing deleted,
        and nothing moved at all unless every item can go where it belongs.
        {"timeline", "moved", "undo": the moves that put them back}.

Documents go by file, not through the reader's pipe: an hour of podcast is
a lot of JSON. controller: the shell's connection (or the reader's), which
Project Setup's controller wraps here.
"""

import json
import os

from pages.project_setup import otio_engine as oe

SUFFIX = "(Podcast Sync)"
TEMP_NAME = "Buddy Podcast Sync (temporary)"
MEDIA_TYPES = ("Video", "Audio", "Video + Audio")
SOURCES = ("selected", "bin", "timeline")


def _call(obj, name, *args, default=None):
    """A Resolve call that may not exist on this version - default if not."""
    try:
        fn = getattr(obj, name, None)
        if fn is None:
            return default
        out = fn(*args)
        return default if out is None else out
    except Exception:  # noqa: BLE001
        return default


def _wrapped(controller):
    from pages.project_setup.resolve_ext import ProjectSetupController
    return ProjectSetupController(controller)


def media_clips(controller, source):
    """The Media Pool clips to read, what to call them in the log, and the
    new timeline's base name. controller: Project Setup's."""
    project = controller.get_project()
    pool = project.GetMediaPool()
    if source == "selected":
        picked = _call(pool, "GetSelectedClips", default=[]) or []
        if isinstance(picked, dict):
            picked = list(picked.values())
        clips = [c for c in picked if _call(c, "GetClipProperty", "Type", default="") in MEDIA_TYPES]
        if not clips:
            raise oe.OtioError("Select the podcast's clips in the Media Pool first - every camera and "
                               "recorder - or choose Current bin.")
        return clips, {"kind": "selected", "name": ""}, "Podcast"
    folder = controller.get_current_bin()
    clips = [c for c in controller.get_bin_clips(folder)
             if _call(c, "GetClipProperty", "Type", default="") in MEDIA_TYPES]
    name = _call(folder, "GetName", default="") or "Podcast"
    if not clips:
        raise oe.OtioError(f'The bin "{name}" has no video or audio clips in it.')
    return clips, {"kind": "bin", "name": name}, name


def _save(document, workdir):
    path = os.path.join(workdir, "source.otio")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    return path


def read_here(controller, source="selected"):
    controller = _wrapped(controller)
    if source == "timeline":
        timeline = controller.get_current_timeline() if hasattr(controller, "get_current_timeline") \
            else controller.get_project().GetCurrentTimeline()
        if not timeline:
            raise oe.OtioError("No timeline is open in Resolve.")
        document, _clips, workdir = oe.read_current_timeline(controller)
        name = timeline.GetName()
        return {"path": _save(document, workdir), "workdir": workdir, "base": name,
                "what": {"kind": "timeline", "name": name}, "warnings": []}
    clips, what, base = media_clips(controller, source)
    project = controller.get_project()
    pool = project.GetMediaPool()
    before = project.GetCurrentTimeline()
    folder = _call(pool, "GetCurrentFolder")
    temp = pool.CreateTimelineFromClips(oe.unique_timeline_name(project, TEMP_NAME), clips)
    if not temp:
        raise oe.OtioError("Resolve wouldn't put those clips on a timeline to read them.")
    workdir = oe._fresh_workdir()
    warnings = []
    try:
        path = os.path.join(workdir, "source.otio")
        if not controller.export_timeline_otio(temp, path):
            raise oe.OtioError("Resolve couldn't export the clips to read them.")
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        oe._correct_from(temp, document)
    finally:
        if not _call(pool, "DeleteTimelines", [temp], default=False):
            warnings.append(f'Resolve kept the temporary timeline "{TEMP_NAME}" - delete it from the Media Pool.')
        if before:
            _call(project, "SetCurrentTimeline", before)
        if folder is not None:
            _call(pool, "SetCurrentFolder", folder)
    return {"path": _save(document, workdir), "workdir": workdir, "base": base, "what": what, "warnings": warnings}


def build_here(controller, path, workdir, base, tracks):
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    timeline = oe.import_rebuilt(_wrapped(controller), document, workdir, base, SUFFIX, "podcast_sync.otio")
    warnings, unnamed = [], 0
    for track in tracks:
        kind = track["kind"].lower()
        if not _call(timeline, "SetTrackName", kind, track["number"], track["name"], default=False):
            unnamed += 1
        if not track["enabled"] and not _call(timeline, "SetTrackEnable", kind, track["number"], False,
                                              default=False):
            warnings.append(f'Turn off the track "{track["name"]}" yourself - Resolve didn\'t let Buddy.')
    if unnamed:
        warnings.append("Resolve didn't take some of the track names.")
    return {"timeline": _call(timeline, "GetName", default="") or f"{base} {SUFFIX}", "warnings": warnings}


# Where the items wait, past the end of everything (as it is and as it will
# be), while the others move. Resolve's SetPosition doesn't refuse a place
# that's taken - it OVERWRITES what's there and still answers True (measured
# on 21.1.2: a clip moved onto another left 2 clips of 3 on the track). So
# nothing is ever moved onto a frame another item holds: every item first
# goes out to its own place past the end, and only then to where it belongs
# - checked beforehand to be free.
PARK_FRAMES = 2000


def _label(kind, track):
    return f"{'V' if kind == 'video' else 'A'}{track}"


def move_here(controller, moves, name=""):
    """Sync in place: every item in `moves` ([{"kind", "track", "start",
    "to"}], frames from the timeline's start) moved along its own track.
    Checked first - the timeline still the one read, every item found, no
    track locked, nothing landing on top of anything - and nothing is moved
    unless all of it can be; a move Resolve refuses part-way puts back what
    had moved. Needs Resolve 20's SetPosition."""
    project = _wrapped(controller).get_project()
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise oe.OtioError("No timeline is open in Resolve.")
    current = _call(timeline, "GetName", default="") or ""
    if name and current != name:
        raise oe.OtioError(f'Another timeline is open now ("{current}") - nothing was moved.')
    if not moves:
        return {"timeline": current, "moved": 0, "undo": [], "warnings": []}
    origin = int(timeline.GetStartFrame())
    live = {}
    for kind in ("video", "audio"):
        for number in range(1, int(_call(timeline, "GetTrackCount", kind, default=0) or 0) + 1):
            live[(kind, number)] = [[int(item.GetStart()) - origin, int(item.GetEnd()) - origin, item, None]
                                    for item in _call(timeline, "GetItemListInTrack", kind, number, default=[]) or []]
    plan = []
    for move in moves:
        key = (str(move["kind"]).lower(), int(move["track"]))
        row = next((r for r in live.get(key, []) if r[0] == int(move["start"]) and r[3] is None), None)
        if row is None:
            raise oe.OtioError("The timeline has changed since Buddy read it - nothing was moved. Sync again.")
        if _call(timeline, "GetIsTrackLocked", key[0], key[1], default=False):
            raise oe.OtioError(f"{_label(*key)} is locked - unlock it, then sync again. Nothing was moved.")
        row[3] = int(move["to"])
        plan.append((key, row))
    crowded = []
    for key, rows in live.items():
        after = sorted((r[0] if r[3] is None else r[3], (r[0] if r[3] is None else r[3]) + r[1] - r[0]) for r in rows)
        if any(b[0] < a[1] for a, b in zip(after, after[1:])) or (after and after[0][0] < 0):
            crowded.append(_label(*key))
    if crowded:
        raise oe.OtioError("In sync, clips on " + ", ".join(crowded) + " would sit on top of each other, so nothing "
                           "was moved. Put each camera and recorder on a track of its own, or build a new timeline.")
    if _call(plan[0][1][2], "GetPosition", default=None) is None:
        raise oe.OtioError("Syncing the open timeline where it is needs a newer DaVinci Resolve - build a new "
                           "timeline instead. Nothing was moved.")
    park = max(max(r[1], (r[3] if r[3] is not None else r[0]) + r[1] - r[0])
               for rows in live.values() for r in rows) + PARK_FRAMES
    done = []                           # (item, where it was) - to put back if Resolve refuses one

    def put(item, frame):
        return bool(_call(item, "SetPosition", {"startFrame": origin + int(frame)}, False, default=False))

    try:
        for _key, (start, _end, item, _to) in plan:
            if not put(item, park + start):
                raise oe.OtioError("Resolve wouldn't move a clip.")
            done.append((item, start))
        for _key, (_start, _end, item, to) in plan:
            if not put(item, to):
                raise oe.OtioError("Resolve wouldn't move a clip into place.")
    except oe.OtioError as exc:
        # Back the same careful way: all of them out past the end again, then home.
        for item, start in done:
            put(item, park + start)
        stuck = sum(0 if put(item, start) else 1 for item, start in done)
        raise oe.OtioError(f"{exc} " + ("The clips are back where they were." if not stuck else
                                        f"{stuck} clips couldn't be put back - press Ctrl+Z in Resolve.")) from None
    undo = [{"kind": key[0], "track": key[1], "start": to, "to": start} for key, (start, _end, _item, to) in plan]
    return {"timeline": current, "moved": len(plan), "undo": undo, "warnings": []}
