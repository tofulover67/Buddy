#!/usr/bin/env python3
"""
DAW - a small music studio inside Buddy: instrument and drum tracks played,
recorded and edited over the music on the timeline, in its key and tempo, and
rendered onto the timeline as a new clip. Everything is Buddy's own sound,
synthesized in the page (Web Audio): no samples, nothing to download.

It grew out of Audio Assistant's Keys tab and shares its instruments
(pages/audio_assistant/web/instruments.js), its song listening (song.py) and
its way of placing a sound (sounds.py, resolve_ext.place_sound) - a new clip
on a Buddy track under the music, never a change to a clip that's there.

One project, three layouts - switched in the title bar, the project and the
transport carrying on across the switch (web/app.js):

    Arrange   tracks over the song lane, a browser of sounds, the selected
              track's instrument as knobs, and a mixer (web/arrange.js)
    Console   one track at a time: a faceplate of knobs with a live scope,
              a piano roll to edit, and the keyboard (web/console.js)
    Perform   chord pads in the key, macro knobs and an XY pad, a drum step
              sequencer and the keyboard (web/perform.js)

Here: the timeline read lightly (its music clips, its markers and cuts for
the picture lane, and the playhead - the reads in the Resolve reader's
process, as Audio Assistant's), the song
listened to and written once as a WAV for the page to play along with (and
its waveform), a render placed (and Undo), and the project kept for next
time (project.py cleans it).

Protocol:
    to the view    state, timeline, playhead, song, placed, undo, project, toast, alert
    from the view  refresh, listen, place, undo, project
"""

import hashlib
import os
import time

import numpy as np
from PySide6.QtCore import QTimer, QUrl

from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveCalls
from core.resolve_worker import ResolveWorker
from core.web_page import WebToolPage

from pages.audio_assistant import fx, resolve_ext, sounds
from pages.audio_assistant import song as song_ear
from pages.audio_assistant.peaks import decode_range as peaks_decode_range

from . import project as P

POLL_MS = 1000
FULL_EVERY_S = 4.0            # the timeline's clips; the playhead in between
BUSY_AFTER_S = 4
WRITE_WAIT_S = 1.5
UNDO_LIMIT = 30
SONG_MOST_S = 10 * 60         # the most of a song listened to
PLACE_MOST_S = 150.0          # the longest render placed (64 bars at 102 BPM)
WAVE_PER_S = 50               # the song lane's waveform: points a second
WAVE_MOST = 30000
MOST_MARKERS = 500            # the edit as the picture lane shows it
MOST_CUTS = 3000
PLAY_DIR = os.path.join(os.path.dirname(os.path.dirname(fx.PREVIEW_DIR)), "daw", "play_along")
BUSY_TITLE = "Resolve isn't answering"
BUSY_TEXT = "Resolve is holding Buddy's requests – usually because the timeline is playing. Stop playback and try again."


def song_clips(timeline):
    """The clips the song lane can be: every audio clip with a file to listen
    to - {id, name, track, track_name, start, end}, the timeline's order."""
    out = []
    for track in (timeline or {}).get("tracks", []):
        for clip in track["clips"]:
            if ((clip.get("media") or {}).get("path")) and not clip.get("curve"):
                out.append({"id": clip["id"], "name": clip["name"], "track": track["index"],
                            "track_name": track.get("name", ""), "start": clip["start"], "end": clip["end"]})
    return out


def waveform(samples, rate, per_s=WAVE_PER_S):
    """The song lane's waveform: the loudest sample in each 1/per_s of a
    second, as 0-255, WAVE_MOST points at the most."""
    x = np.asarray(samples, dtype=np.float64)
    mono = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    if not len(mono):
        return []
    step = max(1, int(rate // per_s), int(np.ceil(len(mono) / WAVE_MOST)))
    n = len(mono) // step
    blocks = mono[:n * step].reshape(n, step).max(axis=1) if n else np.array([mono.max()])
    return [int(v) for v in np.clip(np.round(np.sqrt(np.minimum(blocks, 1.0)) * 255), 0, 255)]


class DawPage(WebToolPage):
    tool_id = "daw"
    display_name = "DAW"
    category = "Editing Tools"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    PLAY_DIR = PLAY_DIR

    def build_state(self):
        self.timeline = None           # resolve_ext.read_timeline's data
        self.playhead = None
        self.problem = ""
        self.busy = False
        self.undo_stack = []           # [{"label", "timeline", "sound": {"id"}}], newest last
        self._songs = {}               # (path, start, end, channels) -> what the view was sent
        self._job = None               # the fx.Job listening to a song
        self._placing = False
        self._full_due = 0.0
        self.settings = self.host.tool_settings(self.tool_id)
        self._worker = ResolveWorker(self)
        self._calls = ResolveCalls(self, self._worker)
        self._tick = QTimer(self)
        self._tick.setInterval(POLL_MS)
        self._tick.timeout.connect(self._poll)
        self._tick.start()

    def working_on(self):
        if self._placing:
            return "Placing on the timeline"
        if self._job is not None:
            return "Listening to the song"
        return None

    def web_ready(self):
        self.emit("project", P.clean(self.settings.get("project")))
        self._push_state()
        self.emit("timeline", self._timeline_for_view())
        self.emit("playhead", {"frame": self.playhead})
        self._push_undo()

    def on_shown(self):
        self._full_due = 0.0
        self._poll()

    def on_connection_changed(self, connected):
        if not connected:
            self._forget_timeline()
        self._full_due = 0.0
        self._push_state()

    def on_app_quitting(self):
        if self._job is not None:
            self._job.stop_for_quit()
        self._tick.stop()

    # ------------------------------------------------------------ Resolve --

    def _controller(self, connect=False):
        if connect:
            try:
                return self.host.ensure_connected()
            except ResolveConnectionError as exc:
                self.problem = str(exc)
                return None
        return self.host.controller if getattr(self.host, "connected", False) else None

    def _push_state(self):
        self.emit("state", {"connected": self.timeline is not None, "problem": self.problem, "busy": self.busy})

    def _timeline_for_view(self):
        t = self.timeline
        if t is None:
            return None
        # The edit, to score to: its markers, its cuts and where the picture ends.
        markers = [{"frame": m["frame"], "color": m["color"], "name": m["name"], "duration": m.get("duration", 1)}
                   for m in t.get("markers", []) if not m.get("beat")][:MOST_MARKERS]
        return {"id": t["id"], "name": t["name"], "fps": t["fps"], "start": t["start"], "end": t["end"],
                "songs": song_clips(t), "markers": markers, "cuts": list(t.get("cuts", []))[:MOST_CUTS],
                "video_end": t.get("video_end")}

    def _forget_timeline(self):
        if self.timeline is not None:
            self.timeline = None
            self.emit("timeline", None)

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
        if self.timeline is None or time.monotonic() >= self._full_due:
            cache = {}
            self._calls.call("audio.full", {"fresh": False}, lambda: resolve_ext.read_timeline(controller, cache),
                             self._on_full)
        else:
            fps = self.timeline["fps"]
            self._calls.call("audio.live", {"fps": fps, "ids": []},
                             lambda: resolve_ext.read_live(controller, fps, {}), self._on_live)

    def _failed(self, error):
        self.busy = False
        self.problem = str(error) if isinstance(error, ResolveConnectionError) else f"Couldn't read the timeline: {error}"
        self._forget_timeline()
        self._full_due = time.monotonic() + FULL_EVERY_S
        self._push_state()

    def _on_full(self, result, error):
        if error is not None:
            return self._failed(error)
        self._full_due = time.monotonic() + FULL_EVERY_S
        # From the reader's process, the data; from the worker, (data, items).
        data = result[0] if isinstance(result, tuple) else result
        before = self._timeline_for_view()
        self.timeline, self.problem, self.busy = data, "", False
        self._push_state()
        now = self._timeline_for_view()
        if now != before:
            self.emit("timeline", now)

    def _on_live(self, result, error):
        if error is not None:
            return self._failed(error)
        if self.busy:
            self.busy = False
            self._push_state()
        if self.timeline is None or result["id"] != self.timeline["id"]:
            self._full_due = 0.0
            return
        if result["playhead"] != self.playhead:
            self.playhead = result["playhead"]
            self.emit("playhead", {"frame": self.playhead})

    def on_refresh(self, _payload=None):
        self._full_due = 0.0
        self._poll(connect=True)

    def _writable(self):
        """The controller, once no read is waiting on Resolve - or None, having said why."""
        controller = self._controller(connect=True)
        if controller is None or self.timeline is None:
            self.emit("alert", {"title": "Not connected", "text": self.problem or "Open a timeline in Resolve first."})
            return None
        if not self._calls.wait_idle(WRITE_WAIT_S):
            self.emit("alert", {"title": BUSY_TITLE, "text": BUSY_TEXT})
            return None
        return controller

    def _clip(self, uid):
        for track in (self.timeline or {}).get("tracks", []):
            for clip in track["clips"]:
                if clip["id"] == uid:
                    return clip, track["index"]
        return None, None

    # --------------------------------------------------------------- song --
    #
    # The song lane: a music clip listened to (its key, tempo and bars - song.py)
    # and its stretch written once as a plain WAV for the page to play along
    # with (PLAY_DIR, one song at a time, never in a backup), with its waveform.

    def on_listen(self, payload):
        uid = str((payload or {}).get("id") or "")
        clip, _track = self._clip(uid)
        media = (clip or {}).get("media") or {}
        if clip is None or not media.get("path") or self.timeline is None:
            return self.emit("song", {"id": uid, "error": "That clip has no audio file Buddy can listen to."})
        fps = self.timeline["fps"]
        start_s = float(clip.get("offset") or 0.0)
        end_s = start_s + min((clip["end"] - clip["start"]) / fps, SONG_MOST_S)
        channels = clip.get("channels")
        key = (os.path.normcase(media["path"]), round(start_s, 4), round(end_s, 4), tuple(channels or ()))
        ident = "|".join(str(k) for k in key)
        out = os.path.join(self.PLAY_DIR, "song-" + hashlib.sha1(ident.encode("utf-8")).hexdigest()[:16] + ".wav")

        def send(found):
            moments = []
            for m in found["moments"]:
                frame = clip["start"] + int(round(m["t"] * fps))
                if clip["start"] <= frame < clip["end"]:
                    moments.append(dict(m, frame=frame))
            self.emit("song", dict(found, id=uid, moments=moments, start=clip["start"], end=clip["end"], fps=fps,
                                   name=clip["name"], url=QUrl.fromLocalFile(out).toString()))

        if key in self._songs and os.path.isfile(out):
            return send(self._songs[key])

        def work(progress, cancelled):
            got, error = peaks_decode_range(media["path"], start_s, end_s, lambda f: progress(f * 0.8), cancelled,
                                            channels=channels)
            if error:
                return None, error
            samples, rate = got
            found = song_ear.analyse(samples, rate)
            os.makedirs(self.PLAY_DIR, exist_ok=True)
            fx.clear_previews(self.PLAY_DIR)              # one song at a time
            fx.write_preview(out, samples, rate)
            found = dict(found, wave=waveform(samples, rate), wave_per_s=WAVE_PER_S)
            found.pop("levels", None)
            return found, None

        def done(found, error):
            if error is not None:
                if error != "Stopped.":
                    self.emit("song", {"id": uid, "error": str(error)})
                return
            self._songs[key] = found
            send(found)

        self._listen(uid, work, done)

    def _listen(self, uid, work, done):
        """One song at a time: a new one stops the one being listened to."""
        job = self._job
        if job is not None:
            job.cancel()
            try:
                job.done.disconnect()
            except (RuntimeError, TypeError):
                pass
        job = fx.Job(work, "listen", uid, self)
        job.progressed.connect(lambda f: self.emit("listening", {"id": uid, "progress": round(float(f), 3)}))
        job.done.connect(lambda result, error: self._listened(job, done, result, error))
        job.finished.connect(job.deleteLater)
        self._job = job
        job.start()

    def _listened(self, job, done, result, error):
        if self._job is job:
            self._job = None
        done(result, error)

    # -------------------------------------------------------------- place --

    def on_place(self, payload):
        """A render the page made, its first sample at timeline frame `frame`:
        saved (sounds.py), then put on a Buddy track under the song's track -
        or under A1 with no song."""
        payload = payload or {}
        if self._placing:
            return self.emit("toast", {"text": "Buddy is still putting the last render in."})
        try:
            rate = int(payload.get("rate") or 0)
            samples = sounds.decode(payload.get("channels"), rate, PLACE_MOST_S)
            frame = int(payload.get("frame"))
        except (TypeError, ValueError) as exc:
            self.emit("placed", {"ok": False})
            return self.emit("alert", {"title": "Couldn't use that render", "text": str(exc)})
        if self.timeline is None:
            self.emit("placed", {"ok": False})
            return self.emit("alert", {"title": "Not connected", "text": self.problem or "Open a timeline in Resolve first."})
        seconds = len(samples) / rate
        clip, below = self._clip(str(payload.get("song") or ""))
        music = ((clip or {}).get("media") or {}).get("path")
        label = str(payload.get("label") or "DAW")[:80]
        try:
            wav = sounds.save(samples, rate, payload.get("recipe"), label, music, self.timeline.get("project", ""))
        except (OSError, ValueError) as exc:
            self.emit("placed", {"ok": False})
            return self.emit("alert", {"title": "Couldn't save the render", "text": str(exc)})
        controller = self._writable()
        if controller is None:
            return self.emit("placed", {"ok": False})
        self._placing = True
        what = str(payload.get("what") or "mix")
        undo_label = {"mix": "Place the mix", "track": "Place a track", "loop": "Place the loop"}.get(what, "Place the mix")

        def done(placed, error):
            self._placing = False
            self._full_due = 0.0
            if error is not None:
                self.emit("placed", {"ok": False})
                return self.emit("alert", {"title": "Couldn't put the render in", "text": str(error)})
            self.undo_stack.append({"label": undo_label, "timeline": self.timeline["id"] if self.timeline else "",
                                    "sound": {"id": placed["id"]}})
            del self.undo_stack[:-UNDO_LIMIT]
            self._push_undo()
            self.emit("placed", {"ok": True, "id": placed["id"], "track": placed["track"]})
            self.emit("toast", {"text": "Placed on A{track}".format(track=placed["track"])})

        self._worker.start(lambda: resolve_ext.place_sound(controller, wav["path"], frame, seconds, below or 1), done)

    def _push_undo(self):
        here = (self.timeline or {}).get("id")
        entries = [e for e in self.undo_stack if e["timeline"] == here]
        self.emit("undo", {"label": entries[-1]["label"] if entries else None})

    def on_undo(self, _payload=None):
        here = (self.timeline or {}).get("id")
        at = next((i for i in range(len(self.undo_stack) - 1, -1, -1) if self.undo_stack[i]["timeline"] == here), None)
        if at is None:
            return
        controller = self._writable()
        if controller is None:
            return
        entry = self.undo_stack.pop(at)
        self._push_undo()

        def done(_result, error):
            self._full_due = 0.0
            if error is not None:
                self.undo_stack.append(entry)          # nothing lost: it can be tried again
                self._push_undo()
                return self.emit("alert", {"title": "Couldn't undo", "text": str(error)})
            self.emit("toast", {"text": f"Undone: {entry['label']}"})

        self._worker.start(lambda: resolve_ext.remove_sound(controller, entry["sound"]["id"]), done)

    # ------------------------------------------------------------ project --

    def on_project(self, payload):
        """The project as it is now, kept for next time (the page sends it a moment after a change)."""
        cleaned = P.clean(payload)
        if cleaned is None:
            return
        self.settings["project"] = cleaned
        self.settings.save()

