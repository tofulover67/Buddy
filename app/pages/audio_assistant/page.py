#!/usr/bin/env python3
"""
Audio Assistant - an easier alternative to Resolve's Fairlight page, for a clip's
volume, fades, a volume line to keyframe, noise reduction and voice clean-up. A web
page (core/web_page.py): the view is web/index.html + timeline.js; every Resolve call
is resolve_ext.py, the waveforms and loudness are peaks.py, and what a control asks of
Resolve is levels.py.

Its first tab, Timeline, mirrors the open timeline's audio tracks the way Resolve draws
them: each clip where it sits, with its file's waveform in grey and - over it, in blue -
the same wave through the clip's volume and fades. Click a clip (or select it in
Resolve: Buddy can read Resolve's selection but not set it) and the panel under the
tracks changes it:

    Levels     clip volume (set to, or change by, for several), pan, fades - also by
               dragging the selected clip's volume line and fade handles on the canvas
    Loudness   integrated LUFS, measured by Buddy from the files (BS.1770, peaks.py),
               and Match: each clip's volume set by those figures to hit a target (not
               Resolve's NormalizeAudioLevel, which ignores its target - see levels.py)
    Clean-up   Voice Isolation and the Dialogue Leveler, where Resolve has them for the
               clip (they read None on some media, and only work on the active timeline)
    Cuts       an audio crossfade on each cut between selected clips
    Ducking    a volume curve dipping the clip under the speech on the tracks ticked
               (ducking.py) - a draft on one clip, applied straight away on several
    Riding     a volume curve evening a voice out phrase by phrase (riding.py)
    Beats      timeline markers on a music clip's beats or bars (beats.py), Buddy's
               own (custom data "buddy-beat") so Clear never takes anyone else's
    Fit        a music clip made to end on its own ending at a length (fit.py):
               whole bars cut out or played again where it sounds the same, heard
               first around the cut, then a WAV on a Buddy track (resolve_ext.apply_fit)
    Effects    Buddy's own processing (dsp.py: rumble, hum, noise, de-esser, clarity,
               gate), heard first with Preview (before / after, played in the page),
               then baked by Apply into a WAV beside the media (render.py) that goes
               in the clip's place on a Buddy track - the clip turned off, as for a
               curve (resolve_ext.apply_effects). A processed clip is made again from
               the recording, never from its WAV (fx.py).
    Curve      volume keys drawn on the clip's line - double-click to add one - and
               Apply: real Resolve keyframes, in a one-clip timeline placed as a nested
               clip where the clip was, the clip itself turned off (keys.py says why,
               resolve_ext.apply_curve how). A clip keyed in Resolve shows its keys.

Every change but a crossfade can be undone (Undo puts back what the clips had, and
takes a curve off or puts the one before back). Buddy can't remove a transition
(DeleteClips refuses one), so the view asks first.

What Resolve 21.1's scripting can reach (tried on a duplicate timeline): a clip's
volume, pan and pitch (SetProperties), fades in frames (SetFades), audio transitions
(AddTransition, category "audio"), loudness normalisation to a target
(Timeline.NormalizeAudioLevel - on the active timeline only: on another it returns True
and changes nothing), a track's Voice Isolation, and a clip's Voice Isolation and
Dialogue Leveler - on the active timeline, and not for every clip: a camera MXF's read
None and refused, an MP4's and MP3's worked. Volume keyframes have no call, but go in
and out through timeline files (keys.py). Not reachable: Fairlight FX (de-esser, EQ,
noise reduction), so those would be Buddy's own processing, baked into a new file.

Reading runs in the Resolve reader's process (core/resolve_reader.py ResolveCalls;
on the ResolveWorker when there's none): a live read of the playhead, Resolve's
selection and the selected clips' levels every LIVE_MS, and a full read of the tracks
every few seconds - more seldom on a big timeline. Resolve holds every call while the
timeline plays, and a read made inside Buddy - even on the worker - froze all of Buddy
until it stopped. The writes that are quick (SetProperties, SetFades, AddTransition:
0-2 ms, measured) run on the worker; each finds its clips afresh by id. Moving the playhead runs in a child process (resolve_child.py):
SetCurrentTimecode holds the GIL for ~500 ms, which froze all of Buddy on every click of
the ruler. While the child runs, Buddy makes no Resolve call of its own: Resolve answers
nothing else until it's done, and Buddy's call would wait for it holding the GIL - which
froze Buddy for all 15 s of a NormalizeAudioLevel when it was tried there.

On macOS a child process can only reach Resolve Studio with external scripting allowed
(Preferences > System > General): the free Resolve gives a script only the connection it
hands Buddy.py (core/resolve_bridge.py). When the child can't connect there, the seek is
made here instead, on the worker - it holds the GIL for its ~500 ms, so only once a click or
drag on the ruler is let go (the view says "final"), never on every step of a drag.

Protocol:
    to the view    state, timeline, levels, playhead, selection, peaks, peaks_progress,
                   options, undo, curve, toast, alert, tidy, tidy_done,
                   fx_busy, fx_preview, fx_done, draft, mix_plan, mix_done,
                   automix_plan, fit_plans, fit_preview
    from the view  refresh, select, seek, set_levels, match, crossfade, curve,
                   remove_curve, undo, tidy_scan, tidy_remove,
                   fx_preview, fx_stop, fx_apply, fx_remove, duck, ride, beats, clear_beats,
                   mix_measure, mix_apply, automix, fit_find, fit_preview, fit_apply,
                   fit_remove

Mix to target (the bar's button): the whole timeline rendered and measured, then one
gain for every clip - to a loudness target, held under the true-peak ceiling.

Automix (the bar's other button): a mic on each track ticked, each up only while its
person talks (automix.py) - a curve on each clip that changes, as Ducking's are.

Tidy up (the header's button, tidy.js): what Buddy made that no timeline uses any
more - curve timelines left in the "Buddy Audio" bin when a curve clip was deleted in
Resolve, processed WAVs likewise, and this project's WAVs on disk gone from its media
pool (resolve_ext.tidy_scan). Removing deletes the timelines from the project and
takes the WAVs out of the media pool; a WAV's file goes to the Recycle Bin once no
project is left on its sidecar (render.release, core/recycle.py). Asked first, as
there's no Undo for it.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEngineSettings

from core import marker_colors, recycle
from core.python_exe import standalone_python
from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveCalls
from core.resolve_worker import ResolveWorker
from core.web_page import WebToolPage

from pages.stills_exporter.resolve_ext import frames_to_timecode

from . import beats as beat_finder
from . import automix, dsp, ducking, fit, fx, riding
from . import keys as K
from . import levels as mixer
from . import resolve_ext
from .peaks import FLOOR_DB, LOUD_BLOCK_S, PEAK_RATE, PeakLoader
from .peaks import decode_range as peaks_decode_range
from .peaks import fold as peaks_fold

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolve_child.py")
_SCRIPT_HOSTS = ("fuscript.exe", "resolve.exe", "fuscript")


def child_python():
    """The interpreter to run resolve_child.py with on Windows, or None. Not
    sys.executable: started from Resolve's Scripts menu that's fuscript.exe,
    Resolve's script host, which runs a .py with no __file__ - the child died
    on its first line, and every click on the ruler said "Resolve didn't
    answer." The python of the install Buddy runs on is the one with the
    same packages (startup_manager.running_python_exe). On a Mac the child
    runs on core.python_exe's standalone_python() instead."""
    from core.startup_manager import running_python_exe
    found = running_python_exe()
    if found:
        return found
    if os.path.basename(sys.executable).lower() not in _SCRIPT_HOSTS:
        return sys.executable
    return None
CHILD_POLL_MS = 40
# Normalising a long clip takes Resolve a while; a child still going after this is stopped.
CHILD_TIMEOUT_S = 180
# After a seek, Buddy's playhead stands this long before a read of Resolve's may move it:
# a read made while the seek was on its way still has the old frame.
SEEK_SETTLE_S = 1.0
LIVE_MS = 500
# A full read at most this often, and never more than 1/FULL_SHARE of the time.
FULL_EVERY_S = 3.0
FULL_SHARE = 10
# Resolve holds calls while the timeline plays: a read still waiting after this long is said.
BUSY_AFTER_S = 4
# A write waits this long for a read in progress before saying Resolve is busy.
WRITE_WAIT_S = 1.5
UNDO_LIMIT = 30
BEAT_COLOR = "Cyan"
BUSY_TITLE = "Resolve isn't answering"
BUSY_TEXT = "Resolve is holding Buddy's requests – usually because the timeline is playing. Stop playback and try again."


class AudioAssistantPage(WebToolPage):
    tool_id = "audio_assistant"
    display_name = "Audio Assistant"
    category = "Editing Tools"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def build_state(self):
        self.timeline = None         # resolve_ext.read_timeline's data
        self.items = set()           # the clip ids the last full read found
        self._items_here = {}        # clip id -> TimelineItem, when the worker read them (no reader)
        self._fresh_media = False    # the next full read forgets what it knew of the files
        self.selected = []           # clip ids selected in Buddy
        self.from_resolve = False    # ... because Resolve selected them
        self.resolve_selection = None
        self.playhead = None
        self.problem = ""
        self.busy = False
        # [{"label", "timeline", and "before" (apply()'s) or "curve" ({"do": "remove", "id"}
        # | {"do": "apply", "id", "keys", "volume", "outer"})}], newest last
        self.undo_stack = []
        self._select_next = None     # a clip id to select once a full read has it (a new curve clip)
        self._media_cache = {}
        # file key -> peaks.decode()'s (peaks, loudness, channels), every channel apart | None (no waveform)
        self._peaks = {}
        self._peak_errors = {}
        self._shown = set()          # clip["peaks"] keys (a file's, for a channel set) the view has been sent
        self._full_due = 0.0         # time.monotonic() the next full read is due
        self._full_took = 0.0
        self._seek = None            # the latest ruler frame not yet sent to Resolve
        self._seek_final = False     # ... and whether the click or drag it came from is over (macOS)
        self._child_connects = None  # False once a child process couldn't reach Resolve (macOS)
        self._seek_quiet_until = 0.0
        self._child = None           # {"proc", "result", "done", "started"} running now
        self._python = None          # child_python()'s answer, once asked ("" = none found; Windows)
        self._audio = {}             # clip["peaks"] key -> those channels' peaks and loudness as arrays, for Match
        self._fx_job = None          # the Effects panel's fx.Job running now
        self._duck_queue = []        # [(clip id, keys as heard, label)] still to get their curve (ducking, riding)
        self._fx_cache = None        # the stretch Preview decoded last, to play again with other settings
        self._fit = None             # Fit's last Find: {"id", "key", "samples", "rate", "found", "plans", "frames"}
        self._child_timer = QTimer(self)
        self._child_timer.setInterval(CHILD_POLL_MS)
        self._child_timer.timeout.connect(self._check_child)
        self._worker = ResolveWorker(self)
        self._calls = ResolveCalls(self, self._worker)      # the reads
        self._loader = PeakLoader(self)
        self._loader.ready.connect(self._on_peaks)
        self._loader.progress.connect(lambda key, f: self.emit("peaks_progress", {"key": key, "progress": f,
                                                                                  "left": self._loader.pending()}))
        self._tick = QTimer(self)
        self._tick.setInterval(LIVE_MS)
        self._tick.timeout.connect(self._poll)
        self._tick.start()

    def working_on(self):
        """Effects being rendered or applied, or a change going into Resolve
        (not a playhead move)."""
        job = self._fx_job
        if job is not None:
            return "Making a preview" if job.kind == "preview" else "Applying effects"
        child = self._child
        if child is not None and child.get("do") != "seek":
            return "Changing the timeline"
        return None

    def web_ready(self):
        # Preview's <audio> starts when its files are ready - a moment after the
        # click on Preview, too late to count as one for Chromium's rule. The
        # page only ever plays what Preview was pressed for.
        view = getattr(self, "view", None)
        if view is not None:
            view.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self.emit("options", {
            "presets": [{"id": p["id"], "label": p["label"]} for p in mixer.PRESETS],
            "crossfades": resolve_ext.CROSSFADES,
            "leveler_modes": mixer.LEVELER_MODES,
            "volume_range": resolve_ext.VOLUME_RANGE,
            "loud_block": LOUD_BLOCK_S,
            "ducking": ducking.DEFAULTS,
            "riding": riding.DEFAULTS,
            "automix": automix.DEFAULTS,
            "marker_colors": [{"name": n, "hex": marker_colors.HEX[n]} for n in marker_colors.NAMES],
            "beat_color": BEAT_COLOR,
            "effects": [{"id": key, "name": e["name"], "about": e["about"], "default": e["default"]}
                        for key, e in dsp.EFFECTS.items()],
        })
        self._push_state()
        if self.timeline:
            self.emit("timeline", self.timeline)
            self._shown = set()
            self._send_waveforms()
        self._push_selection()
        self._push_undo()
        self.emit("playhead", {"frame": self.playhead})

    def on_shown(self):
        self._full_due = 0.0
        self._poll()

    def on_connection_changed(self, connected):
        if not connected:
            self._forget_timeline()
        self._full_due = 0.0
        self._push_state()

    def on_app_quitting(self):
        if self._fx_job is not None:
            self._fx_job.stop_for_quit()
        self._tick.stop()
        self._child_timer.stop()
        self._loader.shutdown()

    # ------------------------------------------------------------ Resolve --

    def _controller(self, connect=False):
        if connect:
            try:
                return self.host.ensure_connected()
            except ResolveConnectionError as exc:
                self.problem = str(exc)
                return None
        return self.host.controller if getattr(self.host, "connected", False) else None

    def _forget_timeline(self):
        if self.timeline is not None:
            self.timeline, self.items, self._items_here = None, set(), {}
            self.emit("timeline", None)

    def _clips(self):
        return {c["id"]: c for t in (self.timeline or {}).get("tracks", []) for c in t["clips"]}

    def _poll(self, connect=False):
        if not self.isVisible() and not connect:
            return
        controller = self._controller(connect)
        if controller is None:
            self._forget_timeline()
            return self._push_state()
        if self._calls.busy():
            if not self.busy and self._calls.waiting_for() >= BUSY_AFTER_S:
                self.busy = True
                self._push_state()
            return
        if self._child is not None:
            # Resolve is busy with the child's call, and a call made now waits for it
            # holding the GIL (a live read during a 15 s NormalizeAudioLevel froze Buddy for all 15).
            return
        if sys.platform == "darwin" and self._seek is not None and self._child_connects is False:
            return self._seek_here(controller)
        if self.timeline is None or time.monotonic() >= self._full_due:
            cache = self._media_cache
            fresh, self._fresh_media = self._fresh_media, False
            started = time.monotonic()
            self._calls.call("audio.full", {"fresh": fresh}, lambda: resolve_ext.read_timeline(controller, cache),
                             lambda result, error: self._on_full(result, error, started))
        else:
            fps = self.timeline["fps"]
            ids = [uid for uid in self.selected if uid in self.items]
            chosen = {uid: self._items_here[uid] for uid in ids if uid in self._items_here}
            self._calls.call("audio.live", {"fps": fps, "ids": ids},
                             lambda: resolve_ext.read_live(controller, fps, chosen), self._on_live)

    def _failed(self, error):
        """A read went wrong: the reason goes in the header, the tracks go."""
        self.busy = False
        self.problem = str(error) if isinstance(error, ResolveConnectionError) else f"Couldn't read the timeline: {error}"
        self._forget_timeline()
        self._full_due = time.monotonic() + FULL_EVERY_S
        self._push_state()

    def _on_full(self, result, error, started):
        if error is not None:
            return self._failed(error)
        self._full_took = time.monotonic() - started
        self._full_due = time.monotonic() + max(FULL_EVERY_S, self._full_took * FULL_SHARE)
        # From the reader's process, the data; from the worker, (data, items).
        data, self._items_here = result if isinstance(result, tuple) else (result, {})
        self.items = {clip["id"] for track in data["tracks"] for clip in track["clips"]}
        changed = repr(data) != repr(self.timeline)
        if self.timeline is None or data["id"] != self.timeline["id"]:
            self.selected, self.from_resolve, self.resolve_selection = [], False, None
            self._push_selection()
            self._push_undo(data["id"])
        self.timeline, self.problem, self.busy = data, "", False
        self._push_state()
        if changed:
            self.emit("timeline", data)
        if self._select_next and self._select_next in self.items:
            self.selected, self.from_resolve, self._select_next = [self._select_next], False, None
            self._push_selection()
        for track in data["tracks"]:
            for clip in track["clips"]:
                media = clip.get("media")
                if media and media["key"] not in self._peaks:
                    self._loader.want(media["key"], media["path"])
        self._send_waveforms()           # a file already decoded, for channels no clip played before

    def _on_live(self, result, error):
        if error is not None:
            return self._failed(error)
        if self.busy:
            self.busy = False
            self._push_state()
        if self.timeline is None or result["id"] != self.timeline["id"]:
            self._full_due = 0.0             # another timeline: read it on the next tick
            return
        seeking = self._seek is not None or self._child_doing("seek") or time.monotonic() < self._seek_quiet_until
        if result["playhead"] != self.playhead and not seeking:
            self.playhead = result["playhead"]
            self.emit("playhead", {"frame": self.playhead})
        # Resolve's selection wins whenever it changes; a click here stands until then.
        theirs = [uid for uid in result["resolve_selection"] if uid in self.items]
        if theirs != self.resolve_selection:
            first = self.resolve_selection is None
            self.resolve_selection = theirs
            if not first or theirs:
                self.selected, self.from_resolve = theirs, bool(theirs)
                self._push_selection()
        self._merge(result["levels"])

    def _merge(self, fresh, wrote=False):
        """Resolve's levels for some clips into the page's copy, and to the view.
        After a write (wrote) every one goes, changed or not: the view has been
        showing its preview, and Resolve's answer is what it must show now."""
        changed = {}
        for uid, clip in self._clips().items():
            new = fresh.get(uid)
            if new and (wrote or any(clip.get(k) != v for k, v in new.items())):
                clip.update(new)
                changed[uid] = new
        if changed:
            self.emit("levels", {"clips": changed, "wrote": wrote})

    # ----------------------------------------------------- child process --

    def _child_doing(self, do):
        return self._child is not None and self._child["do"] == do

    def _run_child(self, command, done):
        """Runs resolve_child.py with command; done(result, error) when it ends.
        One at a time: the caller checks self._child first."""
        if sys.platform != "darwin":
            if self._python is None:
                self._python = child_python() or ""
            if not self._python:
                return done(None, ResolveConnectionError(
                    "Buddy couldn't find the Python it runs on, so it can't move Resolve's playhead."))
        fd, result_path = tempfile.mkstemp(prefix="buddy_audio_", suffix=".json")
        os.close(fd)
        try:
            proc = subprocess.Popen(
                [standalone_python() if sys.platform == "darwin" else self._python, CHILD, result_path],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            proc.stdin.write(json.dumps(command).encode("utf-8"))
            proc.stdin.close()
        except OSError as exc:
            os.remove(result_path)
            return done(None, exc)
        self._child = {"proc": proc, "result": result_path, "done": done, "do": command["do"],
                       "command": command, "started": time.monotonic()}
        self._child_timer.start()

    def _check_child(self):
        child = self._child
        if child is None:
            return self._child_timer.stop()
        proc = child["proc"]
        if proc.poll() is None:
            if time.monotonic() - child["started"] < CHILD_TIMEOUT_S:
                return
            proc.kill()
            proc.wait(2)
        self._child = None
        self._child_timer.stop()
        try:
            with open(child["result"], encoding="utf-8") as f:
                out = json.load(f)
        except (OSError, ValueError):
            out = {"ok": False, "error": "Resolve didn't answer in time." if proc.returncode is None or proc.returncode < 0
                   else "Resolve didn't answer."}
        finally:
            try:
                os.remove(child["result"])
            except OSError:
                pass
        if sys.platform == "darwin":
            if proc.returncode == 1 and child["do"] == "seek":
                # It couldn't reach Resolve (the free Resolve, on macOS): seeks happen here from now on.
                self._child_connects = False
                if self._seek is None:
                    self._seek, self._seek_final = child["frame"], True
                return self._start_seek()
            self._child_connects = True
        if out.get("ok"):
            child["done"](out.get("result"), None)
        else:
            child["done"](None, ResolveConnectionError(out.get("error") or "Resolve didn't answer."))
        self._start_seek()             # a ruler frame that came in meanwhile

    def _start_seek(self):
        """Sends the latest ruler frame to Resolve, unless the child process is
        busy - then it goes when that's done."""
        if self._child is not None or self._seek is None or self.timeline is None:
            return
        if sys.platform == "darwin" and self._child_connects is False:
            controller = self._controller()
            if controller is not None and not self._worker.busy():
                self._seek_here(controller)
            return                      # else the next _poll makes it
        frame, self._seek = self._seek, None
        tl = self.timeline
        self._run_child({"do": "seek", "timecode": frames_to_timecode(frame, tl["fps"], tl["drop_frame"])},
                        self._on_seek)
        if sys.platform == "darwin":
            self._child["frame"] = frame

    def _seek_here(self, controller):
        """The seek in Buddy's own process, when no child can reach Resolve:
        only a finished click or drag's, as it freezes Buddy for ~500 ms."""
        if not self._seek_final or self._worker.busy():
            return
        frame, self._seek = self._seek, None
        tl = self.timeline
        timecode = frames_to_timecode(frame, tl["fps"], tl["drop_frame"])
        self._worker.start(lambda: resolve_ext.seek(controller, timecode), self._on_seek)

    def _on_seek(self, _result, error):
        self._seek_quiet_until = time.monotonic() + SEEK_SETTLE_S
        if error is not None and self._seek is None:
            self.emit("toast", {"text": str(error)})

    # -------------------------------------------------------------- peaks --

    def _on_peaks(self, key, result, error):
        self._peaks[key] = result
        if error:
            self._peak_errors[key] = error
        # peaks keys are "<file key>" or "<file key>:<channels>" (resolve_ext.peaks_key).
        self._shown = {k for k in self._shown if k.split(":")[0] != key}
        self._audio = {k: v for k, v in self._audio.items() if k.split(":")[0] != key}
        self._send_waveforms()

    def _send_waveforms(self):
        """Each clip's waveform the view hasn't had - its file's, for the channels
        it plays (peaks.fold) - once that file is decoded."""
        for track in (self.timeline or {}).get("tracks", []):
            for clip in track["clips"]:
                media, pk = clip.get("media"), clip.get("peaks")
                if media and pk and pk not in self._shown and media["key"] in self._peaks:
                    self._shown.add(pk)
                    self._emit_peaks(pk, media["key"], clip.get("channels"))

    def _folded(self, file_key, channels):
        """(peak bytes, loudness bytes) for those channels of the file, or (None, why)."""
        result = self._peaks.get(file_key)
        if not result:
            return None, self._peak_errors.get(file_key, "") or "No waveform"
        return peaks_fold(result, channels)

    def _emit_peaks(self, pk, file_key, channels):
        folded, why = self._folded(file_key, channels)
        codes, loud = folded if folded else (None, None)
        self.emit("peaks", {
            "key": pk, "rate": PEAK_RATE, "floor": FLOOR_DB, "left": self._loader.pending(),
            "data": base64.b64encode(codes).decode("ascii") if codes else None,
            "loud": base64.b64encode(loud).decode("ascii") if loud else None,
            "error": "" if folded else why,
        })

    # --------------------------------------------------------------- view --

    def _state(self):
        return {
            "connected": self._controller() is not None,
            "timeline": (self.timeline or {}).get("name", ""),
            "problem": self.problem,
            "busy": self.busy,
        }

    def _push_state(self):
        self.emit("state", self._state())

    def _push_selection(self):
        self.emit("selection", {"ids": self.selected, "from_resolve": self.from_resolve})

    def _push_undo(self, timeline_id=None):
        """The newest undo for this timeline (another timeline's are kept for when it's back)."""
        tid = timeline_id or (self.timeline or {}).get("id")
        mine = [u for u in self.undo_stack if u["timeline"] == tid]
        self.emit("undo", {"label": mine[-1]["label"], "count": len(mine)} if mine else None)

    # ------------------------------------------------------------ writing --

    def _writable(self, need_timeline=True):
        """The controller, once no read is waiting on Resolve - or None, having said
        why. need_timeline False: for the whole project (Tidy up), no timeline needed."""
        controller = self._controller(connect=True)
        if controller is None or (need_timeline and self.timeline is None):
            self.emit("alert", {"title": "Not connected", "text": self.problem or "Open a timeline in Resolve first."})
            return None
        # A call while the child has Resolve busy would freeze Buddy until it's done
        # (see _poll): a seek is over in half a second, so it's waited for.
        if self._child is not None:
            try:
                self._child["proc"].wait(WRITE_WAIT_S)
            except subprocess.TimeoutExpired:
                self.emit("alert", {"title": BUSY_TITLE, "text": BUSY_TEXT})
                return None
        if not self._calls.wait_idle(WRITE_WAIT_S):
            self.emit("alert", {"title": BUSY_TITLE, "text": BUSY_TEXT})
            return None
        return controller

    def _written(self, label, result, count):
        """A write's result: the fresh levels shown, the undo kept, and what happened said."""
        self._merge(result["after"], wrote=True)
        before = {uid: old for uid, old in result["before"].items() if old}
        if before:
            self.undo_stack.append({"label": label, "timeline": self.timeline["id"], "before": before})
            del self.undo_stack[:-UNDO_LIMIT]
            self._push_undo()
        failed = len(result.get("failed") or [])
        done = count - failed
        if failed and not done:
            self.emit("alert", {"title": "Resolve refused", "text": "Resolve didn't take the change for those clips."})
        elif failed:
            self.emit("toast", {"text": f"Changed {done} of {count} clips – Resolve refused the rest"})

    # ------------------------------------------------------------ actions --

    def on_refresh(self, _payload=None):
        """Read everything again - not only the tracks. What Buddy remembers of
        the files goes too: a clip relinked in Resolve keeps its media pool item
        (and so the old path, from the cache), and a file changed on disk kept its
        old waveform for the rest of the session. Unchanged files come back from
        the waveform cache on disk at once."""
        self._full_due = 0.0
        self.problem = ""
        self._media_cache.clear()
        self._fresh_media = True     # the reader's process keeps a cache of its own
        self._peaks, self._peak_errors, self._audio, self._shown = {}, {}, {}, set()
        self._loader.forget()
        self._poll(connect=True)

    def on_select(self, payload):
        ids = [str(i) for i in ((payload or {}).get("ids") or []) if str(i) in self.items]
        self.selected, self.from_resolve = ids, False
        self._push_selection()

    def on_seek(self, payload):
        """A click or drag on the ruler: Resolve's playhead follows, latest first."""
        try:
            frame = int((payload or {}).get("frame"))
        except (TypeError, ValueError):
            return
        if self.timeline is None:
            return
        frame = max(self.timeline["start"], min(self.timeline["end"] - 1, frame))
        self._seek, self.playhead = frame, frame
        if sys.platform == "darwin":
            self._seek_final = bool((payload or {}).get("final"))
        self._start_seek()

    def on_set_levels(self, payload):
        """Volume, pan, fades, Voice Isolation or the Dialogue Leveler on clips
        (levels.changes() says what that asks of each)."""
        payload = payload or {}
        change = mixer.changes(self._clips(), payload)
        if not change:
            return self._push_levels_back(payload)
        controller = self._writable()
        if controller is None:
            return self._push_levels_back(payload)
        label = str(payload.get("label") or "Change")
        self._worker.start(lambda: resolve_ext.apply(controller, change),
                           lambda result, error: self._on_set(label, len(change), result, error, payload))

    def _on_set(self, label, count, result, error, payload):
        if error is not None:
            self.emit("alert", {"title": "Couldn't change the clips", "text": str(error)})
            return self._push_levels_back(payload)
        self._written(label, result, count)

    def _push_levels_back(self, payload):
        """The view previews a change as it's made: when Resolve doesn't take
        it, the clips' real levels go back."""
        clips = self._clips()
        self.emit("levels", {"wrote": True, "clips": {
            uid: {k: clips[uid].get(k) for k in ("volume", "pan", "fade_in", "fade_out", "isolation", "leveler")}
            for uid in (payload.get("ids") or []) if uid in clips}})

    def on_match(self, payload):
        """Each clip's volume moved by what takes it to the preset's target, by
        Buddy's own measurement (levels.match) - together, or each on its own."""
        payload = payload or {}
        preset = mixer.PRESET_IDS.get(payload.get("preset"))
        ids = [uid for uid in (payload.get("ids") or []) if uid in self.items]
        if preset is None or not ids or self.timeline is None:
            return
        change, report = mixer.match(self._clips(), ids, self._audio_of, self.timeline["fps"], preset,
                                     independent=bool(payload.get("independent")))
        if report["keyed"] and not change:
            return self.emit("alert", {"title": "Those clips have volume keyframes", "text": (
                "Their keyframes set their volume in Resolve, so a clip volume wouldn't be heard. "
                "Change the line on the clip instead, then Apply.")})
        if report["missing"] and not change:
            return self.emit("alert", {"title": "Nothing to measure yet", "text": (
                "Buddy is still reading those clips' audio, or they have no file it can read. "
                "Try again once their waveforms show.")})
        if not change:
            return self.emit("alert", {"title": "Nothing to match", "text": "Those clips are silent."})
        controller = self._writable()
        if controller is None:
            return
        self._worker.start(lambda: resolve_ext.apply(controller, change),
                           lambda result, error: self._on_matched(preset, len(change), report, result, error))

    def _on_matched(self, preset, count, report, result, error):
        if error is not None:
            return self.emit("alert", {"title": "Couldn't match the loudness", "text": str(error)})
        self._written("Match loudness", result, count)
        skipped = len(report["missing"]) + len(report["silent"]) + len(report["keyed"])
        if report["clamped"]:
            self.emit("alert", {"title": "Some clips couldn't get there", "text": (
                "+30 dB is as loud as Resolve's clip volume goes, and some clips needed more. "
                "They're at +30 dB.")})
        elif skipped:
            self.emit("toast", {"text": f"Matched to {preset['label']} – {skipped} left out, with volume keyframes or no audio to measure yet"})
        else:
            self.emit("toast", {"text": f"Matched to {preset['label']}"})

    def _audio_of(self, clip):
        """A clip's file's decoded peaks and loudness, for levels.match - or None."""
        media, pk = clip.get("media"), clip.get("peaks")
        if not media or not pk:
            return None
        if pk not in self._audio:
            folded, _why = self._folded(media["key"], clip.get("channels"))
            if folded is None:
                return None          # not decoded yet, or a channel Buddy can't read: Match skips it
            self._audio[pk] = {"codes": np.frombuffer(folded[0], dtype=np.uint8),
                               "loud": np.frombuffer(folded[1], dtype=np.float32),
                               "rate": PEAK_RATE, "floor": FLOOR_DB, "block": LOUD_BLOCK_S}
        return self._audio[pk]

    def on_crossfade(self, payload):
        """A crossfade on every cut between the selected clips. The view has
        asked first: Buddy can't take these off again."""
        payload = payload or {}
        kind = payload.get("kind") if payload.get("kind") in resolve_ext.CROSSFADES else resolve_ext.CROSSFADES[0]
        try:
            frames = max(2, min(240, int(payload.get("frames") or 12)))
        except (TypeError, ValueError):
            frames = 12
        left = mixer.cuts(self.timeline["tracks"] if self.timeline else [], payload.get("ids") or [])
        if not left:
            return self.emit("toast", {"text": "No cuts between the selected clips."})
        controller = self._writable()
        if controller is None:
            return
        self._worker.start(lambda: resolve_ext.crossfade(controller, left, kind, frames),
                           lambda result, error: self._on_crossfaded(len(left), result, error))

    def _on_crossfaded(self, count, result, error):
        self._full_due = 0.0                 # the new transitions show on the next read
        if error is not None:
            return self.emit("alert", {"title": "Couldn't add the crossfades", "text": str(error)})
        added, failed = result
        if failed:
            self.emit("alert", {"title": f"Added {added} of {count} crossfades", "text": (
                "Resolve refused the rest – a crossfade needs media to spare past the cut on both clips.")})
        else:
            self.emit("toast", {"text": "Added 1 crossfade" if added == 1 else f"Added {added} crossfades"})

    # -------------------------------------------------------------- curves --

    def on_curve(self, payload):
        """Apply: the keys the view drew on a clip (as heard) go to Resolve as its
        curve - a new curve clip, or one in place of the clip's Buddy curve."""
        payload = payload or {}
        uid = str(payload.get("id") or "")
        clip = self._clips().get(uid)
        heard = K.clean(payload.get("keys"))
        if clip is None or self.timeline is None:
            return self.emit("curve", {"id": uid, "done": False})
        if not heard:
            if clip.get("curve"):
                return self.on_remove_curve({"id": uid})
            return self.emit("curve", {"id": uid, "done": True, "new": uid})
        self._apply_heard(clip, heard, "Volume curve")

    def _apply_heard(self, clip, heard, label):
        """Keys as heard onto a clip as its curve, with the undo for it."""
        # A keyed clip's own volume isn't heard, so its curve clip starts at 0 dB;
        # any other clip's volume comes with it, and the keys are what's on top.
        volume = 0.0 if mixer.keyed_in_resolve(clip) else float(clip.get("volume") or 0.0)
        undo = ({"do": "apply", "keys": clip["keys"], "volume": volume} if clip.get("curve")
                else {"do": "remove"})
        self._put_curve(clip["id"], K.shifted(heard, -volume), volume, None, label, undo)

    def _put_curve(self, uid, inner, volume, outer, label, undo):
        """apply_curve on the worker; `undo` (less its "id": the new clip's) kept for it."""
        controller = self._writable()
        if controller is None:
            return self.emit("curve", {"id": uid, "done": False})
        fps = self.timeline["fps"]
        self._worker.start(lambda: resolve_ext.apply_curve(controller, uid, inner, fps, volume, outer),
                           lambda result, error: self._on_curve(uid, label, undo, result, error))

    def _on_curve(self, uid, label, undo, result, error):
        self._full_due = 0.0                     # the new clip shows on the next read
        if error is not None:
            self._duck_queue = []
            self.emit("curve", {"id": uid, "done": False})
            return self.emit("alert", {"title": "Couldn't apply the curve", "text": str(error)})
        if self._duck_queue:
            QTimer.singleShot(0, self._next_duck)
        new = result["id"]
        if result.get("replaced"):
            self._remap(result["replaced"], new)
        self._keep_undo(label, dict(undo, id=new))
        self._select_next = new
        self.emit("curve", {"id": uid, "done": True, "new": new})
        if not result.get("replaced"):
            self.emit("toast", {"text": f"Curve on A{result['track']} – the clip under it is turned off"})

    def on_remove_curve(self, payload):
        """A Buddy curve clip taken off, and the clip it stood in for back on."""
        uid = str((payload or {}).get("id") or "")
        clip = self._clips().get(uid)
        if clip is None or not clip.get("curve"):
            return
        controller = self._writable()
        if controller is None:
            return self.emit("curve", {"id": uid, "done": False})
        self._worker.start(lambda: resolve_ext.remove_curve(controller, uid),
                           lambda result, error: self._on_removed(uid, result, error))

    def _on_removed(self, uid, result, error):
        self._full_due = 0.0
        if error is not None:
            self.emit("curve", {"id": uid, "done": False})
            return self.emit("alert", {"title": "Couldn't take the curve off", "text": str(error)})
        if result["original"]:
            self._keep_undo("Remove curve", {"do": "apply", "id": result["original"], "keys": result["keys"],
                                             "volume": result["volume"], "outer": result["outer"]})
            self._select_next = result["original"]
        self.emit("curve", {"id": uid, "done": True, "new": result["original"] or None})

    # ------------------------------------------------------------ the mix --
    #
    # Mix to target: the whole timeline rendered (Transcribe's audio render,
    # the Deliver settings put back) and measured (Audit's sound check), then
    # one gain for every clip - the mix moves as one, its balance kept - to the
    # target's loudness, held so its true peak stays under the ceiling.

    MIX_CEILING = {"youtube": -1.0, "podcast": -1.0, "ebu": -1.0, "atsc": -2.0, "peak": -1.0}

    def on_mix_measure(self, payload):
        preset = mixer.PRESET_IDS.get((payload or {}).get("preset"))
        if preset is None or self.timeline is None:
            return
        controller = self._writable()
        if controller is None:
            return
        fps, tid = self.timeline["fps"], self.timeline["id"]

        def work(progress, cancelled):
            from pages.audit import audio as sound
            from pages.transcribe.resolve_ext import RenderCancelled, TranscribeController
            try:
                path = TranscribeController(controller).render_timeline_audio(
                    fx.PREVIEW_DIR, progress=lambda pct: progress(pct / 100 * 0.6), cancelled=cancelled,
                    prefix="buddy_mix")
            except RenderCancelled:
                return None, "Stopped."
            try:
                return sound.measure(path, fps, lambda f: progress(0.6 + f * 0.4), cancelled)
            finally:
                sound.remove(path)

        def done(result, error):
            if error is not None:
                if error != "Stopped.":
                    self.emit("mix_plan", {"error": str(error)})
                return
            if self.timeline is None or self.timeline["id"] != tid:
                return self.emit("mix_plan", {"error": "A different timeline is open now – measure again."})
            self.emit("mix_plan", self._mix_plan(preset, result))

        if not self._start_fx("mix", "", work, done):
            self.emit("toast", {"text": "Buddy is still processing a clip – try again when it's done."})

    def _mix_clips(self):
        """(clips a volume moves, clips keyed in Resolve - whose volume isn't heard)."""
        movable, keyed = [], []
        for track in self.timeline["tracks"]:
            if not track.get("enabled", True):
                continue
            for clip in track["clips"]:
                if clip.get("transition") or not clip.get("enabled", True):
                    continue
                (keyed if mixer.keyed_in_resolve(clip) else movable).append(clip)
        return movable, keyed

    def _mix_plan(self, preset, measured):
        lufs, peak = measured.get("lufs"), measured.get("peak_db")
        ceiling = self.MIX_CEILING.get(preset["id"], -1.0)
        if lufs is None or peak is None:
            return {"error": "The mix is silent – there's nothing to measure."}
        wanted = (preset["loudness"] - lufs) if "loudness" in preset else (ceiling - peak)
        gain = min(wanted, ceiling - peak)
        movable, keyed = self._mix_clips()
        lo, hi = resolve_ext.VOLUME_RANGE
        clamped = sum(1 for c in movable if not lo <= (c.get("volume") or 0.0) + gain <= hi)
        return {"preset": preset["id"], "label": preset["label"], "lufs": round(lufs, 1), "peak": round(peak, 1),
                "target": preset.get("loudness"), "ceiling": ceiling, "gain": round(gain, 1),
                "wanted": round(wanted, 1), "limited": wanted - gain > 0.05,
                "clips": len(movable), "keyed": len(keyed), "clamped": clamped}

    def on_mix_apply(self, payload):
        """Every clip's volume moved by the plan's gain (one Undo for them all)."""
        try:
            gain = float((payload or {}).get("gain"))
        except (TypeError, ValueError):
            return
        if self.timeline is None or not np.isfinite(gain) or abs(gain) < 0.05 or abs(gain) > 60:
            return
        movable, _keyed = self._mix_clips()
        lo, hi = resolve_ext.VOLUME_RANGE
        change = {c["id"]: {"props": {"AudioVolume": round(min(hi, max(lo, (c.get("volume") or 0.0) + gain)), 1)}}
                  for c in movable}
        if not change:
            return
        controller = self._writable()
        if controller is None:
            return
        self._worker.start(lambda: resolve_ext.apply(controller, change),
                           lambda result, error: self._on_mixed(gain, len(change), result, error))

    def _on_mixed(self, gain, count, result, error):
        if error is not None:
            return self.emit("alert", {"title": "Couldn't change the mix", "text": str(error)})
        self._written("Mix to target", result, count)
        self.emit("mix_done", {"gain": gain})
        self.emit("toast", {"text": f"Every clip up {gain:.1f} dB – measure again to check" if gain > 0
                            else f"Every clip down {-gain:.1f} dB – measure again to check"})

    # --------------------------------------------------------------- beats --

    def on_beats(self, payload):
        """Timeline markers on the beats (or each bar's first beat) of a clip:
        its stretch decoded and listened to (beats.py) on a thread, the
        markers added on the worker."""
        payload = payload or {}
        uid = str(payload.get("id") or "")
        clip = self._clips().get(uid)
        media = (clip or {}).get("media") or {}
        if clip is None or not media.get("path") or self.timeline is None:
            return self.emit("toast", {"text": "That clip has no audio file Buddy can listen to."})
        fps, tl_start = self.timeline["fps"], self.timeline["start"]
        every = "bar" if payload.get("every") == "bar" else "beat"
        tempo = payload.get("tempo") if payload.get("tempo") in ("double", "half") else "auto"
        color = payload.get("color") if payload.get("color") in marker_colors.NAMES else BEAT_COLOR
        start_s = float(clip.get("offset") or 0.0)
        end_s = start_s + (clip["end"] - clip["start"]) / fps
        channels = clip.get("channels")

        def work(progress, cancelled):
            got, error = peaks_decode_range(media["path"], start_s, end_s, progress, cancelled, channels=channels)
            if error:
                return None, error
            found = beat_finder.find(*got)
            return (beat_finder.scaled(found, tempo) if found else None), None

        def done(found, error):
            if error is not None:
                if error != "Stopped.":
                    self.emit("alert", {"title": "Couldn't listen to the clip", "text": str(error)})
                return
            if not found:
                return self.emit("alert", {"title": "No beat found", "text": (
                    "Buddy didn't hear a steady beat in this clip – it works on music with drums or a clear pulse.")})
            marks = []
            for i, sec in enumerate(found["beats"]):
                frame = clip["start"] + int(round(sec * fps))
                if not clip["start"] <= frame < clip["end"]:
                    continue
                bar = sum(1 for b in found["bars"] if b <= i)
                first = i in found["bars"]
                if every == "bar" and not first:
                    continue
                beat_in_bar = i - max([b for b in found["bars"] if b <= i], default=i) + 1
                marks.append({"frame": frame - tl_start, "color": color,
                              "name": f"Bar {bar}" if first else f"{bar}.{beat_in_bar}" if bar else "Beat",
                              "note": f"{found['bpm']:g} BPM"})
            self._mark_beats(marks, found["bpm"])

        if not self._start_fx("beats", uid, work, done):
            self.emit("toast", {"text": "Buddy is still processing another clip."})

    def _mark_beats(self, marks, bpm=None, label="Beat markers"):
        controller = self._writable()
        if controller is None:
            return
        self._worker.start(lambda: resolve_ext.add_beat_markers(controller, marks),
                           lambda added, error: self._on_marked(marks, bpm, label, added, error))

    def _on_marked(self, marks, bpm, label, added, error):
        self._full_due = 0.0                       # the markers show on the next read
        if error is not None:
            return self.emit("alert", {"title": "Couldn't add the markers", "text": str(error)})
        if added:
            self.undo_stack.append({"label": label, "timeline": self.timeline["id"],
                                    "markers": {"do": "remove", "frames": added}})
            del self.undo_stack[:-UNDO_LIMIT]
            self._push_undo()
        skipped = len(marks) - len(added)
        if bpm is None:
            text = f"Undone: {label}"
        elif skipped:
            text = f"Marked {len(added)} beats at {bpm:g} BPM – {skipped} frames had a marker already"
        else:
            text = f"Marked {len(added)} beats at {bpm:g} BPM"
        self.emit("toast", {"text": text})

    def on_clear_beats(self, _payload=None):
        """Buddy's beat markers off the whole timeline (the view has asked)."""
        controller = self._writable()
        if controller is None:
            return
        self._worker.start(lambda: resolve_ext.remove_beat_markers(controller), self._on_cleared)

    def _on_cleared(self, removed, error, undo=True):
        self._full_due = 0.0
        if error is not None:
            return self.emit("alert", {"title": "Couldn't take the markers off", "text": str(error)})
        if removed and undo:
            self.undo_stack.append({"label": "Clear beat markers", "timeline": self.timeline["id"],
                                    "markers": {"do": "add", "marks": removed}})
            del self.undo_stack[:-UNDO_LIMIT]
            self._push_undo()
        self.emit("toast", {"text": f"Took off {len(removed)} beat markers" if undo else "Undone: Beat markers"})

    # ----------------------------------------------------------------- fit --
    #
    # Fit to length (fit.py): Find decodes the song from the clip's start to
    # its file's end, finds its beats and the ways to fit it, and keeps the
    # decoded song (self._fit) for Preview and Apply, which build from it.
    # Apply's WAV goes on a Buddy track (resolve_ext.apply_fit), the clip
    # turned off; Undo takes it off. Undo of a take-off places it again.

    FIT_MOST_S = 10 * 60          # the most of a song decoded (and kept, for Preview and Apply)

    def _fit_target(self, uid):
        """(clip, its file, where it starts, its channels) or (None, why)."""
        clip = self._clips().get(uid)
        if clip is None or self.timeline is None:
            return None, "That clip isn't on the timeline any more."
        if clip.get("curve"):
            return None, "Take the curve off first – the music is fitted from the clip itself."
        if clip.get("fit"):
            return None, "This music is fitted already – take the edit off to fit it again."
        media = clip.get("media") or {}
        if not media.get("path"):
            return None, "That clip has no audio file Buddy can listen to."
        return (clip, media["path"], float(clip.get("offset") or 0.0), clip.get("channels")), None

    def on_fit_find(self, payload):
        payload = payload or {}
        uid = str(payload.get("id") or "")
        target, why = self._fit_target(uid)
        if target is None:
            return self.emit("fit_plans", {"id": uid, "error": why})
        clip, path, start_s, channels = target
        fps = self.timeline["fps"]
        try:
            frames = int(payload.get("frames"))
        except (TypeError, ValueError):
            return
        if frames < fps * 2:
            return self.emit("fit_plans", {"id": uid, "error": "That's too short to fit a song into."})
        want_s = frames / fps
        key = (path, round(start_s, 4), tuple(channels or ()))
        cached = self._fit if self._fit and self._fit["key"] == key else None

        def work(progress, cancelled):
            if cached:
                samples, rate, found = cached["samples"], cached["rate"], cached["found"]
            else:
                got, error = peaks_decode_range(path, start_s, start_s + self.FIT_MOST_S, lambda f: progress(f * 0.8),
                                                cancelled, channels=channels)
                if error:
                    return None, error
                samples, rate = got
                found = beat_finder.find(samples, rate)
            if not found:
                return {"samples": samples, "rate": rate, "found": None, "plans": []}, None
            ways = fit.plans(samples, rate, want_s, found)
            progress(1.0)
            return {"samples": samples, "rate": rate, "found": found, "plans": ways}, None

        def done(result, error):
            if error is not None:
                if error != "Stopped.":
                    self.emit("fit_plans", {"id": uid, "error": str(error)})
                return
            self._fit = dict(result, key=key, id=uid, frames=frames)
            if not result["found"]:
                return self.emit("fit_plans", {"id": uid, "error": (
                    "Buddy didn't hear a steady beat in this clip – fitting works on music with drums or a clear pulse.")})
            if not result["plans"]:
                return self.emit("fit_plans", {"id": uid, "error": (
                    "Buddy couldn't fit the song into that length – it needs at least a bar at its start and its end.")})
            self.emit("fit_plans", {"id": uid, "frames": frames, "bpm": result["found"]["bpm"],
                                    "plans": [dict(p, at=round(fit.jump_at(p), 3)) for p in result["plans"]]})

        if not self._start_fx("fit", uid, work, done):
            self.emit("toast", {"text": "Buddy is still processing another clip."})

    def _fit_plan(self, uid, index):
        """The plan the view picked, from the last Find of this clip - or None."""
        got = self._fit
        if not got or got["id"] != uid or not got.get("plans"):
            return None
        try:
            return got["plans"][int(index)]
        except (TypeError, ValueError, IndexError):
            return None

    def on_fit_preview(self, payload):
        """The fitted song around its cut - PREVIEW_AROUND_S either side - to play here."""
        payload = payload or {}
        uid = str(payload.get("id") or "")
        plan = self._fit_plan(uid, payload.get("plan"))
        if plan is None:
            return self.emit("fit_preview", {"id": uid, "error": "Find the edits again first."})
        samples, rate = self._fit["samples"], self._fit["rate"]
        around = self.PREVIEW_AROUND_S

        def work(progress, cancelled):
            whole = fit.build(samples, rate, plan)
            at = fit.jump_at(plan)
            first = max(0.0, at - around)
            last = min(len(whole) / rate, at + around)
            os.makedirs(fx.PREVIEW_DIR, exist_ok=True)
            fx.clear_previews()
            out = os.path.join(fx.PREVIEW_DIR, f"fit-{os.urandom(4).hex()}.wav")
            fx.write_preview(out, whole[int(first * rate):int(last * rate)], rate)
            return {"path": out, "cut": at - first}, None

        def done(result, error):
            if error is not None:
                if error != "Stopped.":
                    self.emit("fit_preview", {"id": uid, "error": str(error)})
                return
            self.emit("fit_preview", {"id": uid, "plan": payload.get("plan"), "cut": round(result["cut"], 3),
                                      "url": QUrl.fromLocalFile(result["path"]).toString()})

        self._start_fx("fit_preview", uid, work, done)

    PREVIEW_AROUND_S = 6.0

    def on_fit_apply(self, payload):
        payload = payload or {}
        uid = str(payload.get("id") or "")
        plan = self._fit_plan(uid, payload.get("plan"))
        target, why = self._fit_target(uid)
        if target is None:
            return self.emit("alert", {"title": "Can't fit this clip", "text": why})
        if plan is None:
            return self.emit("toast", {"text": "Find the edits again first."})
        _clip, path, start_s, channels = target
        got = self._fit
        if got["key"] != (path, round(start_s, 4), tuple(channels or ())):
            return self.emit("toast", {"text": "The clip has changed – find the edits again."})
        samples, rate, frames = got["samples"], got["rate"], got["frames"]
        project = self.timeline.get("project", "")

        def work(progress, cancelled):
            return fit.save(path, start_s, channels, plan, samples, rate, project)

        def done(result, error):
            if error is not None:
                if error != "Stopped.":
                    self.emit("alert", {"title": "Couldn't fit the music", "text": str(error)})
                return self.emit("fx_done", {"id": uid, "new": None})
            self._fit = None           # the song's no longer needed: its memory goes
            self._place_fit(uid, result, frames, "Fit to length", {"do": "remove", "original": uid})

        if not self._start_fx("fit_apply", uid, work, done):
            self.emit("toast", {"text": "Buddy is still processing another clip."})

    def _place_fit(self, uid, wav, frames, label, undo):
        controller = self._writable()
        if controller is None:
            return self.emit("fx_done", {"id": uid, "new": None})
        self._fx_busy(uid, "place", 1.0)

        def done(placed, error):
            self._full_due = 0.0
            self._fx_busy()
            if error is not None:
                self.emit("fx_done", {"id": uid, "new": None})
                return self.emit("alert", {"title": "Couldn't put the fitted music in", "text": str(error)})
            if undo is not None:
                self.undo_stack.append({"label": label, "timeline": self.timeline["id"],
                                        "fit": dict(undo, id=placed["id"])})
                del self.undo_stack[:-UNDO_LIMIT]
                self._push_undo()
            self._select_next = placed["id"]
            self.emit("fx_done", {"id": uid, "new": placed["id"]})
            self.emit("toast", {"text": (f"Fitted – on A{placed['track']}, saved beside the media; the clip under it is turned off"
                                         if wav.get("beside") else
                                         f"Fitted – on A{placed['track']}, saved in Buddy's own folder; the clip under it is turned off")})

        self._worker.start(lambda: resolve_ext.apply_fit(controller, uid, wav, frames), done)

    def on_fit_remove(self, payload):
        """A fitted clip taken off: the song it was made from back on."""
        uid = str((payload or {}).get("id") or "")
        clip = self._clips().get(uid)
        if clip is None or not clip.get("fit"):
            return
        controller = self._writable()
        if controller is None:
            return
        original = clip["fit"].get("original")
        wav = {"path": clip["media"]["path"], "source": (clip["media"].get("fx") or {}).get("source", ""),
               "beside": True}
        frames = clip["end"] - clip["start"]
        self._worker.start(lambda: resolve_ext.remove_effects(controller, uid, original),
                           lambda result, error: self._on_fit_removed(uid, wav, frames, result, error))

    def _on_fit_removed(self, uid, wav, frames, result, error, undo=True):
        self._full_due = 0.0
        if error is not None:
            self.emit("fx_done", {"id": uid, "new": None})
            return self.emit("alert", {"title": "Couldn't take the edit off", "text": str(error)})
        if undo:
            self.undo_stack.append({"label": "Remove the fit", "timeline": self.timeline["id"],
                                    "fit": {"do": "place", "id": result["original"], "wav": wav, "frames": frames}})
            del self.undo_stack[:-UNDO_LIMIT]
            self._push_undo()
        self._select_next = result["original"]
        self.emit("fx_done", {"id": uid, "new": result["original"]})

    def _undo_fit(self, entry, controller):
        step = entry["fit"]

        def failed(error):
            self.undo_stack.append(entry)
            self._push_undo()
            self.emit("alert", {"title": "Couldn't undo", "text": str(error)})

        if step["do"] == "remove":
            def removed(result, error):
                if error is not None:
                    return failed(error)
                self._on_fit_removed(step["id"], None, 0, result, None, undo=False)
                self.emit("toast", {"text": f"Undone: {entry['label']}"})
            return self._worker.start(lambda: resolve_ext.remove_effects(controller, step["id"], step["original"]),
                                      removed)
        if not os.path.isfile(step["wav"]["path"]):
            return failed("The fitted music's file isn't there any more.")
        self._place_fit(step["id"], step["wav"], step["frames"], entry["label"], None)
        self.emit("toast", {"text": f"Undone: {entry['label']}"})

    # ------------------------------------------------------------- ducking --

    def on_duck(self, payload):
        """Ducking curves for the selected clips under the speech on the tracks
        ticked (ducking.py). One clip: its curve goes to the view as a draft, to
        see and change before Apply. Several (the view has asked): each gets its
        curve now, one after another - each one an Undo of its own."""
        payload = payload or {}
        if self.timeline is None:
            return
        fps = self.timeline["fps"]
        clips = self._clips()
        ids = [uid for uid in (payload.get("ids") or []) if uid in clips and not clips[uid].get("transition")]
        under = {int(i) for i in (payload.get("under") or []) if str(i).lstrip("-").isdigit()}
        if not ids:
            return
        if not under:
            return self.emit("alert", {"title": "Nothing to duck under", "text": "Tick the tracks with the speech on them."})
        voices = [c for t in self.timeline["tracks"] if t["index"] in under and t.get("enabled", True)
                  for c in t["clips"] if c["id"] not in ids]
        spans, missing = ducking.speech(voices, self._audio_of, fps)
        if not spans:
            return self.emit("alert", ({"title": "Still reading the audio", "text": (
                "Buddy hasn't read the audio on those tracks yet. Try again once their waveforms show.")}
                if missing else {"title": "No speech found", "text": "Buddy didn't hear anyone talking on those tracks."}))
        setting = ducking.settings(payload)
        curves = [(uid, ducking.duck_keys(clips[uid], spans, fps, **setting)) for uid in ids]
        curves = [(uid, keys) for uid, keys in curves if keys]
        if not curves:
            return self.emit("toast", {"text": "No speech under the selected clips."})
        if len(ids) == 1:
            uid, keys = curves[0]
            return self.emit("draft", {"id": uid, "keys": keys, "note": (
                "Ducking curve drawn – some clips' audio wasn't read yet, so they were left out" if missing
                else "Ducking curve drawn – Apply puts it in Resolve")})
        self._duck_queue = [(uid, keys, "Ducking") for uid, keys in curves]
        self._next_duck()

    def on_ride(self, payload):
        """Each selected clip evened out phrase by phrase (riding.py): one
        clip's curve as a draft, several applied one after another."""
        payload = payload or {}
        if self.timeline is None:
            return
        fps = self.timeline["fps"]
        clips = self._clips()
        ids = [uid for uid in (payload.get("ids") or []) if uid in clips and not clips[uid].get("transition")]
        setting = riding.settings(payload)
        curves, missing = [], 0
        for uid in ids:
            audio = self._audio_of(clips[uid])
            if audio is None:
                missing += 1
                continue
            keys = riding.ride_keys(clips[uid], audio, fps, **setting)
            if keys:
                curves.append((uid, keys, "Riding"))
        if not curves:
            return self.emit("alert", ({"title": "Still reading the audio", "text": (
                "Buddy hasn't read that clip's audio yet. Try again once its waveform shows.")} if missing else
                {"title": "Nothing to even out", "text": "Buddy found fewer than two phrases of speech in it."}))
        if len(ids) == 1:
            uid, keys, _label = curves[0]
            return self.emit("draft", {"id": uid, "keys": keys,
                                       "note": "Riding curve drawn – Apply puts it in Resolve"})
        self._duck_queue = curves
        self._next_duck()

    def on_automix(self, payload):
        """The automixer (automix.py) on the tracks ticked, one mic each:
        what it would do goes to the view ("automix_plan") - and with
        "apply" (the view has asked), each of their clips that changes gets
        its curve, one after another, each one an Undo of its own."""
        payload = payload or {}
        if self.timeline is None:
            return
        fps = self.timeline["fps"]
        wanted = {int(i) for i in (payload.get("tracks") or []) if str(i).lstrip("-").isdigit()}
        mics = [t for t in self.timeline["tracks"] if t["index"] in wanted and t.get("enabled", True)]
        if len(mics) < 2:
            return self.emit("automix_plan", {"error": "Tick two or more tracks – one for each mic."})
        setting = automix.settings(payload)
        found = automix.plan(mics, self._audio_of, fps, **setting)
        if found["missing"]:
            return self.emit("automix_plan", {"error": (
                "Buddy hasn't read the audio on those tracks yet. Try again once their waveforms show.")})
        if not found["lines"]:
            return self.emit("automix_plan", {"error": "Buddy didn't hear anyone talking on those tracks."})
        curves = []
        for track in mics:
            for clip in track["clips"]:
                if clip.get("transition") or not clip.get("enabled", True):
                    continue
                keys = automix.keys(clip, found["lines"][track["index"]], fps)
                if keys:
                    curves.append((clip["id"], keys, "Automix"))
        if not payload.get("apply"):
            return self.emit("automix_plan", {"tracks": found["tracks"], "together": found["together"],
                                              "clips": len(curves)})
        if not curves:
            return self.emit("toast", {"text": "Nothing to change – every mic is up all the way through."})
        self._duck_queue = curves
        self._next_duck()
        self.emit("automix_plan", {"done": True})

    def _next_duck(self):
        if not self._duck_queue:
            return
        if self._worker.busy():
            return QTimer.singleShot(100, self._next_duck)
        uid, keys, label = self._duck_queue.pop(0)
        clip = self._clips().get(uid)
        if clip is None:
            return self._next_duck()
        self._apply_heard(clip, keys, label)

    # ------------------------------------------------------------- effects --
    #
    # Preview and Apply's processing run as fx.Job threads, one at a time;
    # placing the WAV is resolve_ext.apply_effects on the worker. Undo keeps
    # {"fx": {"do": "remove", "id", "original"}} (take the processed clip off)
    # or {"fx": {"do": "apply", "id", "chain"}} (process it with that chain).

    def _fx_target(self, uid):
        """(clip, its fx.source()) for a clip Effects can work on, or (None, why)."""
        clip = self._clips().get(uid)
        if clip is None or self.timeline is None:
            return None, "That clip isn't on the timeline any more."
        if clip.get("curve"):
            return None, "Take the curve off first – effects go on the clip itself."
        if mixer.keyed_in_resolve(clip):
            return None, "This clip has volume keyframes from Resolve, which processing would lose."
        src = fx.source(clip, self.timeline["fps"])
        if src is None:
            return None, "That clip has no audio file Buddy can process."
        return (clip, src), None

    def _fx_busy(self, uid=None, doing=None, progress=0.0):
        self.emit("fx_busy", {"id": uid, "doing": doing, "progress": round(float(progress), 3)})

    def _start_fx(self, kind, uid, work, done):
        """A Job of `kind` ("preview" | "apply") for a clip; a preview going
        is stopped for it - an Apply isn't (False then)."""
        job = self._fx_job
        if job is not None:
            if job.kind in ("apply", "fit_apply"):
                return False
            job.cancel()
            try:
                job.done.disconnect()
            except (RuntimeError, TypeError):
                pass
        job = fx.Job(work, kind, uid, self)
        job.progressed.connect(lambda f: self._fx_busy(uid, kind, f))
        job.done.connect(lambda result, error: self._fx_finished(job, done, result, error))
        job.finished.connect(job.deleteLater)
        self._fx_job = job
        self._fx_busy(uid, kind, 0.0)
        job.start()
        return True

    def _fx_finished(self, job, done, result, error):
        if self._fx_job is job:
            self._fx_job = None
            self._fx_busy()
        done(result, error)

    def on_fx_preview(self, payload):
        """Before and after of the chain on a stretch of the clip, to play here."""
        payload = payload or {}
        uid = str(payload.get("id") or "")
        target, why = self._fx_target(uid)
        if target is None:
            return self.emit("toast", {"text": why})
        clip, src = target
        span = fx.preview_span(clip, src, self.timeline["fps"], self.playhead)
        chain = dsp.clean_chain(payload.get("chain"))
        work = fx.preview_work(src, span, chain, self._fx_cache)
        self._start_fx("preview", uid, work, lambda result, error: self._on_previewed(uid, span, result, error))

    def _on_previewed(self, uid, span, result, error):
        if error is not None:
            if error != "Stopped.":
                self.emit("fx_preview", {"id": uid, "error": str(error)})
            return
        self._fx_cache = result["decoded"]
        self.emit("fx_preview", {"id": uid, "before": QUrl.fromLocalFile(result["before"]).toString(),
                                 "after": QUrl.fromLocalFile(result["after"]).toString(),
                                 "frame": span[2], "seconds": round(span[1] - span[0], 3), "hum": result["hum"]})

    def on_fx_stop(self, _payload=None):
        if self._fx_job is not None:
            self._fx_job.cancel()

    def on_fx_apply(self, payload):
        """The chain baked into a WAV and put in the clip's place - or, with no
        effect on, a processed clip's effects taken off."""
        payload = payload or {}
        uid = str(payload.get("id") or "")
        chain = dsp.clean_chain(payload.get("chain"))
        target, why = self._fx_target(uid)
        if target is None:
            return self.emit("alert", {"title": "Can't process this clip", "text": why})
        clip, src = target
        if not dsp.active(chain):
            if clip.get("fx"):
                return self.on_fx_remove({"id": uid})
            return self.emit("toast", {"text": "Turn an effect on first."})
        undo = ({"do": "apply", "chain": src["chain"]} if clip.get("fx")
                else {"do": "remove", "original": uid})
        self._process(uid, src, chain, "Effects", undo)

    def _process(self, uid, src, chain, label, undo):
        """Render, then place: `undo` (less the new clip's id) kept for it - or
        None, when this is an undo being done."""
        fps, project = self.timeline["fps"], self.timeline.get("project", "")
        started = self._start_fx("apply", uid, fx.render_work(src, chain, fps, project),
                                 lambda result, error: self._on_rendered(uid, label, undo, result, error))
        if not started:
            self.emit("toast", {"text": "Buddy is still processing another clip."})

    def _on_rendered(self, uid, label, undo, result, error):
        if error is not None:
            if error != "Stopped.":
                self.emit("alert", {"title": "Couldn't process the audio", "text": str(error)})
            return self.emit("fx_done", {"id": uid, "new": None})
        controller = self._writable()
        if controller is None:
            return self.emit("fx_done", {"id": uid, "new": None})
        self._fx_busy(uid, "place", 1.0)
        self._worker.start(lambda: resolve_ext.apply_effects(controller, uid, result),
                           lambda placed, err: self._on_placed(uid, label, undo, result, placed, err))

    def _on_placed(self, uid, label, undo, rendered, placed, error):
        self._full_due = 0.0
        self._fx_busy()
        if error is not None:
            self.emit("fx_done", {"id": uid, "new": None})
            return self.emit("alert", {"title": "Couldn't put the processed audio in", "text": str(error)})
        new = placed["id"]
        self.achieve("audio_fx", 1)
        if placed.get("replaced"):
            self._remap(placed["replaced"], new)
        if undo is not None:
            self._keep_fx_undo(label, dict(undo, id=new))
        self._select_next = new
        self.emit("fx_done", {"id": uid, "new": new})
        if rendered.get("clipped"):
            self.emit("toast", {"text": "Some of the processed audio went over full scale and was clipped – try less strength, or turn the clip down first"})
        elif not placed.get("replaced"):
            self.emit("toast", {"text": (f"Processed – on A{placed['track']}, saved beside the media; the clip under it is turned off"
                                         if rendered.get("beside") else
                                         f"Processed – on A{placed['track']}, saved in Buddy's own folder; the clip under it is turned off")})

    def on_fx_remove(self, payload):
        uid = str((payload or {}).get("id") or "")
        clip = self._clips().get(uid)
        if clip is None or not clip.get("fx"):
            return
        controller = self._writable()
        if controller is None:
            return
        original = clip["fx"].get("original")
        self._worker.start(lambda: resolve_ext.remove_effects(controller, uid, original),
                           lambda result, error: self._on_fx_removed(uid, result, error))

    def _on_fx_removed(self, uid, result, error, undo=True):
        self._full_due = 0.0
        if error is not None:
            self.emit("fx_done", {"id": uid, "new": None})
            return self.emit("alert", {"title": "Couldn't take the effects off", "text": str(error)})
        if undo:
            self._keep_fx_undo("Remove effects", {"do": "apply", "id": result["original"], "chain": result["chain"]})
        self._select_next = result["original"]
        self.emit("fx_done", {"id": uid, "new": result["original"]})

    def _keep_fx_undo(self, label, entry):
        self.undo_stack.append({"label": label, "timeline": self.timeline["id"], "fx": entry})
        del self.undo_stack[:-UNDO_LIMIT]
        self._push_undo()

    def _undo_fx(self, entry, controller):
        step = entry["fx"]
        if step["do"] == "remove":
            def done(result, error):
                self._on_fx_removed(step["id"], result, error, undo=False)
                if error is None:
                    self.emit("toast", {"text": f"Undone: {entry['label']}"})
                else:
                    self.undo_stack.append(entry)
                    self._push_undo()
            return self._worker.start(lambda: resolve_ext.remove_effects(controller, step["id"], step["original"]), done)
        target, why = self._fx_target(step["id"])
        if target is None:
            self.undo_stack.append(entry)
            self._push_undo()
            return self.emit("alert", {"title": "Couldn't undo", "text": why})
        self._process(step["id"], target[1], step["chain"], entry["label"], None)
        self.emit("toast", {"text": f"Undoing: {entry['label']}"})

    # ------------------------------------------------------------- Tidy up --

    def on_tidy_scan(self, _payload=None):
        """What Buddy made that nothing uses any more, for the view to list
        (resolve_ext.tidy_scan - it only reads)."""
        controller = self._writable(need_timeline=False)
        if controller is None:
            return
        self._worker.start(lambda: resolve_ext.tidy_scan(controller), self._on_tidy_scan)

    def _on_tidy_scan(self, result, error):
        if error is not None:
            return self.emit("alert", {"title": "Couldn't look for unused audio", "text": str(error)})
        self.emit("tidy", result)

    def on_tidy_remove(self, payload):
        """The ones the view kept ticked: timelines deleted from the project, WAVs
        out of the media pool and - if no other project has them - to the
        Recycle Bin. There's no Undo for this, so the view asks first."""
        ids = [str(i) for i in ((payload or {}).get("ids") or [])]
        if not ids:
            return
        controller = self._writable(need_timeline=False)
        if controller is None:
            return
        self._worker.start(lambda: resolve_ext.tidy_remove(controller, ids), self._on_tidy_removed)

    def _on_tidy_removed(self, result, error):
        self._full_due = 0.0
        if error is not None:
            return self.emit("alert", {"title": "Couldn't tidy up", "text": str(error)})
        note = ""
        try:
            recycle.to_recycle_bin(result["recycle"])     # here, not on the worker: Windows may ask first
        except OSError as exc:
            note = f"The audio files stayed where they are: {exc}"
        self.emit("tidy_done", {"removed": len(result["removed"]), "failed": len(result["failed"]), "note": note})

    def _keep_undo(self, label, curve):
        self.undo_stack.append({"label": label, "timeline": self.timeline["id"], "curve": curve})
        del self.undo_stack[:-UNDO_LIMIT]
        self._push_undo()

    def _remap(self, old, new):
        """A clip replaced by another (a curve clip by its new one): the undos follow."""
        for entry in self.undo_stack:
            for kind in ("curve", "fx"):
                if entry.get(kind, {}).get("id") == old:
                    entry[kind]["id"] = new
            if old in entry.get("before", {}):
                entry["before"][new] = entry["before"].pop(old)

    # --------------------------------------------------------------- undo --

    def on_undo(self, _payload=None):
        tid = (self.timeline or {}).get("id")
        mine = [i for i, u in enumerate(self.undo_stack) if u["timeline"] == tid]
        if not mine:
            return
        controller = self._writable()
        if controller is None:
            return
        entry = self.undo_stack.pop(mine[-1])
        self._push_undo()
        if entry.get("fx"):
            return self._undo_fx(entry, controller)
        if entry.get("fit"):
            return self._undo_fit(entry, controller)
        marks = entry.get("markers")
        if marks and marks["do"] == "remove":
            return self._worker.start(lambda: resolve_ext.remove_beat_markers(controller, marks["frames"]),
                                      lambda removed, error: self._on_cleared(removed, error, undo=False))
        if marks:
            return self._worker.start(lambda: resolve_ext.add_beat_markers(controller, marks["marks"]),
                                      lambda added, error: self._on_marked(marks["marks"], None, entry["label"], [], error))
        curve = entry.get("curve")
        if curve and curve["do"] == "remove":
            return self._worker.start(lambda: resolve_ext.remove_curve(controller, curve["id"]),
                                      lambda result, error: self._on_curve_undone(entry, result, error))
        if curve:
            fps = self.timeline["fps"]
            return self._worker.start(
                lambda: resolve_ext.apply_curve(controller, curve["id"], curve["keys"] or [], fps, curve["volume"],
                                                curve.get("outer")),
                lambda result, error: self._on_curve_undone(entry, result, error))
        self._worker.start(lambda: resolve_ext.apply(controller, entry["before"]),
                           lambda result, error: self._on_undone(entry, result, error))

    def _on_curve_undone(self, entry, result, error):
        self._full_due = 0.0
        if error is not None:
            self.undo_stack.append(entry)          # nothing lost: it can be tried again
            self._push_undo()
            return self.emit("alert", {"title": "Couldn't undo", "text": str(error)})
        curve = entry["curve"]
        new = result.get("original") if curve["do"] == "remove" else result["id"]
        if curve["do"] == "apply" and result.get("replaced"):
            self._remap(result["replaced"], new)
        if new:
            self._select_next = new
        self.emit("curve", {"id": curve["id"], "done": True, "new": new or None})
        self.emit("toast", {"text": f"Undone: {entry['label']}"})

    def _on_undone(self, entry, result, error):
        if error is not None or result["failed"]:
            self.undo_stack.append(entry)          # nothing lost: it can be tried again
            self._push_undo()
            return self.emit("alert", {"title": "Couldn't undo", "text": str(error or "Resolve refused some clips.")})
        self._merge(result["after"], wrote=True)
        self.emit("toast", {"text": f"Undone: {entry['label']}"})
