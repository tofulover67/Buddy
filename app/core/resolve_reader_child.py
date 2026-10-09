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

Only jobs named in JOBS run - Buddy's Qt-free modules, nothing else. All
are reads but Dailies' apply, which writes the review notes, Audit's
Go to, which moves the playhead, and its fixes, and Generative's swap and
add, which put a cleaned still or a cut-out in the Media Pool.
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
    "dailies.location": ("pages.dailies.resolve_ext", "current_location"),
    "dailies.scan": ("pages.dailies.resolve_ext", "scan_rows"),
    # Dailies' notes are written from here too: the clips are the ones this
    # process's scan found, and the write is just as held while Resolve plays.
    "dailies.apply": ("pages.dailies.resolve_ext", "apply_by_id"),
    "dailies.reveal": ("pages.dailies.resolve_ext", "reveal_by_id"),
    "resolve.where": ("core.resolve_where", "where"),
    "stills.markers": ("pages.stills_exporter.resolve_ext", "timeline_markers"),
    "chapters.markers": ("pages.youtube_chapters.resolve_ext", "read_current"),
    "renamer.signature": ("pages.batch_clip_renamer.resolve_ext", "target_signature"),
    "renamer.preview": ("pages.batch_clip_renamer.resolve_ext", "preview"),
    "audio.full": ("pages.audio_assistant.resolve_ext", "read_timeline_here"),
    "audio.live": ("pages.audio_assistant.resolve_ext", "read_live_here"),
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
