#!/usr/bin/env python3
"""
The Resolve reader's process (core/resolve_reader.py starts it): connects to
Resolve once, then answers one read at a time, a JSON line in for a JSON
line out:

    in   {"id": 7, "do": "zoom.summary", "args": {}}
    out  {"id": 7, "ok": true, "result": {...}}
         {"id": 7, "ok": false, "error": "...", "type": "SelectionUnavailable"}

First it says whether it reached Resolve: {"ready": true} or {"ready":
false, "error": "..."} (and then it ends).

Why a process of its own: while the timeline plays, Resolve holds every
scripting call until playback stops, and fusionscript keeps Python's GIL
the whole time it waits (measured on 21.1: a second thread made no
progress at all during a 500 ms call) - so a read made inside Buddy, even on
a worker thread, froze every Buddy window for as long as the timeline
played. Here only this process waits; Buddy waits on a pipe, GIL free.

Only jobs named in JOBS run - Buddy's Qt-free modules, nothing else. Most
are reads. The writes, each only when the person presses its button: Dailies'
apply (the review notes), Audit's Go to (the playhead) and its fixes,
Generative's swap and add and Footage Search's place (a still, a cut-out or a
clip into the Media Pool or onto the timeline), Text Edit's playhead and
In/Out - and the long jobs Buddy runs behind its busy overlay
(core/busy_job.py), so it keeps drawing meanwhile: Podcast's builds (NEW
timelines), Project Setup's steps (bins, imports, metadata, NEW timelines),
Subtitles' placing and its NEW timeline, Stills Exporter's grab and export,
Media Relink's relink, the Batch Clip Renamer's rename and its Undo, Image
Importer's and Asset Manager's imports, and the Text+ tabs' edits.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The reads this process makes, by name: (module, function). Each is called
# with the connected controller and the request's args.
JOBS = {
    "animation.summary": ("pages.text_animator.motion_resolve", "summary"),
    "highlight.summary": ("pages.text_animator.highlight_resolve", "summary"),
    "zoom.summary": ("pages.text_animator.zoom_resolve", "summary"),
    "transition.summary": ("pages.text_animator.transition_resolve", "summary"),
    # The Text+ tabs' long edits (pages/text_animator/text_plus.py): each only when the person
    # presses its button, on the clips its "Apply to" names - Center, Size, styling and this tab's
    # animation presets set in their Text+ comps; Stack makes a NEW timeline. "textplus.read" only
    # reads (Space words and Bounding measure in Buddy between the read and the write).
    "textplus.read": ("pages.text_animator.text_plus_resolve", "read_here"),
    "textplus.space": ("pages.text_animator.text_plus_resolve", "space_here"),
    "textplus.bounding": ("pages.text_animator.text_plus_resolve", "bounding_here"),
    "textplus.position": ("pages.text_animator.text_plus_resolve", "position_here"),
    "textplus.animate": ("pages.text_animator.text_plus_resolve", "animate_here"),
    "textplus.unanimate": ("pages.text_animator.text_plus_resolve", "unanimate_here"),
    "textplus.style": ("pages.text_animator.text_plus_resolve", "style_here"),
    "textplus.stack": ("pages.text_animator.text_plus_resolve", "stack_here"),
    "dailies.location": ("pages.dailies.resolve_ext", "current_location"),
    "dailies.scan": ("pages.dailies.resolve_ext", "scan_rows"),
    # Dailies' notes are written from here too: the clips are the ones this
    # process's scan found, and the write is just as held while Resolve plays.
    "dailies.apply": ("pages.dailies.resolve_ext", "apply_by_id"),
    "dailies.reveal": ("pages.dailies.resolve_ext", "reveal_by_id"),
    "footage.scan": ("pages.footage_search.resolve_ext", "scan"),
    "footage.reveal": ("pages.footage_search.resolve_ext", "reveal"),
    # Footage Search's Place: one clip onto a track above V1, only when the person presses it.
    "footage.place": ("pages.footage_search.resolve_ext", "place"),
    "resolve.where": ("core.resolve_where", "where"),
    # Project Setup's steps behind its overlay, only when the person presses one:
    # NEW bins, imported media, the selected clips' metadata, a NEW timeline.
    "project_setup.metadata": ("pages.project_setup.resolve_jobs", "apply_metadata"),
    "project_setup.bins": ("pages.project_setup.resolve_jobs", "create_bins"),
    "project_setup.import_folder": ("pages.project_setup.resolve_jobs", "import_folder"),
    "project_setup.populate": ("pages.project_setup.resolve_jobs", "add_to_timeline"),
    "project_setup.expand": ("pages.project_setup.resolve_jobs", "expand"),
    "project_setup.align": ("pages.project_setup.resolve_jobs", "align"),
    "project_setup.shift": ("pages.project_setup.resolve_jobs", "shift"),
    # A Sync job's export to a temp file (its worker reads it) and the import of the file it writes.
    "project_setup.read_timeline": ("pages.project_setup.resolve_jobs", "read_timeline"),
    "project_setup.import_timeline": ("pages.project_setup.resolve_jobs", "import_timeline"),
    "stills.markers": ("pages.stills_exporter.resolve_ext", "timeline_markers"),
    "chapters.markers": ("pages.youtube_chapters.resolve_ext", "read_current"),
    # Stills Exporter's Grab and Export, only when the person presses them: the
    # stills stay in this process, which grabbed them (resolve_ext.grab_here).
    "stills.grab": ("pages.stills_exporter.resolve_ext", "grab_here"),
    "stills.export": ("pages.stills_exporter.resolve_ext", "export_here"),
    # Subtitles: the SRT put on subtitle track 1, track 1's subtitles read to
    # translate them, and a NEW timeline with a track per language - when asked.
    "transcribe.place": ("pages.transcribe.resolve_ext", "place_here"),
    "transcribe.subtitles": ("pages.transcribe.resolve_ext", "track_subtitles_here"),
    "transcribe.timeline": ("pages.transcribe.resolve_ext", "timeline_with_subtitles_here"),
    "renamer.signature": ("pages.batch_clip_renamer.resolve_ext", "target_signature"),
    "renamer.preview": ("pages.batch_clip_renamer.resolve_ext", "preview"),
    # Batch Clip Renamer: a rename of more than 20 clips, and its Undo.
    "renamer.rename": ("pages.batch_clip_renamer.resolve_ext", "rename"),
    "renamer.undo": ("pages.batch_clip_renamer.resolve_ext", "undo"),
    # Media Relink: the scan, the relink (only when the person presses Relink) and the check after it.
    "relink.scan": ("pages.media_relink.resolve_ext", "scan_rows"),
    "relink.replace": ("pages.media_relink.resolve_ext", "relink_by_id"),
    "relink.check": ("pages.media_relink.resolve_ext", "current_paths"),
    # Image Importer / Asset Manager: the import into the Media Pool, when the person presses Import.
    "importer.import": ("pages.image_importer.resolve_ext", "import_here"),
    "assets.import": ("pages.asset_manager.resolve_ext", "import_selection"),
    "audio.full": ("pages.audio_assistant.resolve_ext", "read_timeline_here"),
    "audio.live": ("pages.audio_assistant.resolve_ext", "read_live_here"),
    # Podcast's Auto Switch: the mics (Audio Assistant's read) and the cameras.
    # Podcast Sync: the recordings read (a temporary timeline made, exported and
    # deleted, for Media Pool clips), and the synced timeline imported as a NEW one.
    "podsync.read": ("pages.podcast.sync.resolve_ext", "read_here"),
    "podsync.build": ("pages.podcast.sync.resolve_ext", "build_here"),
    "podswitch.read": ("pages.podcast.switch.resolve_ext", "read_here"),
    # ...and its Build: a NEW timeline only, when the person presses Build.
    "podswitch.build": ("pages.podcast.switch.resolve_ext", "build_here"),
    "audit.scan": ("pages.audit.resolve_ext", "scan"),
    # Audit's Go to: the playhead only, on the timeline that was checked.
    "audit.goto": ("pages.audit.resolve_ext", "goto"),
    # Audit's fixes: only the one the person pressed, on the timeline checked.
    "audit.fix": ("pages.audit.resolve_ext", "fix"),
    "generative.find": ("pages.generative.resolve_ext", "find"),
    # Generative's writes: only when the person presses Swap in / Add to Media Pool.
    "generative.swap": ("pages.generative.resolve_ext", "swap"),
    "generative.add": ("pages.generative.resolve_ext", "add"),
    "generative.add_sequence": ("pages.generative.resolve_ext", "add_sequence"),
    # The achievements' look at Resolve (core/render_watch.py): render jobs and the open timeline.
    "stats.renders": ("core.resolve_stats", "render_jobs"),
    "stats.timeline": ("core.resolve_stats", "timeline_facts"),
    # Podcast's Text Edit: the open timeline and its playhead; a click on a word
    # moves the playhead, a selection sets In/Out - nothing else is written here.
    "podcast_text.read": ("pages.podcast.text_edit.resolve_ext", "read_here"),
    "podcast_text.live": ("pages.podcast.text_edit.resolve_ext", "live_here"),
    "podcast_text.seek": ("pages.podcast.text_edit.resolve_ext", "seek_here"),
    "podcast_text.marks": ("pages.podcast.text_edit.resolve_ext", "marks_here"),
    # ...and its Build: a NEW timeline from the struck words, when the person presses Build.
    "podcast_text.build": ("pages.podcast.text_edit.resolve_ext", "build_here"),
}
# Reads failing this many times in a row, with Resolve itself not answering
# (_alive): the connection's gone (Resolve closed or restarted) - this
# process ends, and Buddy starts a fresh one. A read failing on what's open
# ("no timeline", nothing selected) doesn't count.
GIVE_UP_AFTER = 3


def _plain(value):
    """What json can't write as it is: a Fraction or numpy number as a float,
    a set as a list, anything else as its text."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return list(value) if isinstance(value, (set, frozenset)) else str(value)


def _say(data, out):
    out.write(json.dumps(data, default=_plain) + "\n")
    out.flush()


def answer(request, controller):
    """{"id", "ok", "result" | "error", "type"} for one request."""
    rid = request.get("id")
    name = request.get("do")
    if name not in JOBS:
        return {"id": rid, "ok": False, "error": f"unknown read {name!r}", "type": "ValueError"}
    module_name, function_name = JOBS[name]
    try:
        module = __import__(module_name, fromlist=[function_name])
        result = getattr(module, function_name)(controller, **(request.get("args") or {}))
        return {"id": rid, "ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001 - handed back to Buddy
        return {"id": rid, "ok": False, "error": str(exc), "type": type(exc).__name__}


def _alive(controller):
    """Resolve itself still answers this connection."""
    try:
        return bool(controller.resolve.GetVersionString())
    except Exception:  # noqa: BLE001 - gone, or never had one
        return False


def main(inp=sys.stdin, out=sys.stdout, connect=None):
    try:
        if connect is None:
            from core.resolve_bridge import ResolveController
            connect = ResolveController
        controller = connect()
    except Exception as exc:  # noqa: BLE001 - reported to Buddy
        _say({"ready": False, "error": f"Couldn't reach Resolve: {exc}"}, out)
        return 1
    _say({"ready": True}, out)
    failures = 0
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        reply = answer(request, controller)
        _say(reply, out)
        failures = (0 if reply["ok"] or reply.get("type") == "SelectionUnavailable" or _alive(controller)
                    else failures + 1)
        if failures >= GIVE_UP_AFTER:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
