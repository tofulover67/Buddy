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
