#!/usr/bin/env python3
"""
Podcast Sync - the Podcast tab's first sub-tab (see pages/podcast/page.py).

Lines up every camera and recorder of a podcast recording, by timecode or
by sound, and builds a NEW timeline with a video track per camera and an
audio track per mic - the clips and timelines already there are never
changed. The engine is engine.py; this is its page.

What it reads, the person's choice:
    selected   the clips selected in the Media Pool
    bin        every clip in the Media Pool's open bin
    timeline   the clips on the open timeline

Resolve can only export a timeline, so for clips in the Media Pool a
temporary timeline is made from them, exported and deleted again at once
(the timeline that was open is opened again).

Every Resolve call is made here on the main thread, behind the shell's busy
overlay; the matching runs on a worker (Project Setup's EngineWorker) and
never touches Resolve.

Protocol:
    to the view    state, devices, job, result, log, alert
    from the view  option, scan, rename_device, move_clip, move_device,
                   master, sync, cancel, switch_tab
"""

import json
import os
import shutil
import time

from PySide6.QtCore import QTimer

from core import busy_job
from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveCalls
from core.web_page import WebToolPage
from pages.podcast.sync import engine
from pages.podcast.sync import resolve_ext as sync_ext

SOURCES = ("selected", "bin", "timeline")
DEFAULTS = {"source": "selected", "method": "timecode", "fine": True, "camera_audio": "tracks"}
SUFFIX = sync_ext.SUFFIX
MAX_NAME = 60
MAX_LOG = 200


def took(seconds):
    s = max(0, int(round(seconds)))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s" if m else f"{s}s"


def clean_name(name):
    return " ".join(str(name or "").split())[:MAX_NAME]


class PodcastSyncPage(WebToolPage):
    tool_id = "podcast_sync"
    display_name = "Podcast Sync"
    category = "Podcast"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def on_switch_tab(self, payload):
        self._podcast.show_tab((payload or {}).get("tab"))

    # ------------------------------------------------------------ state --

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id, dict(DEFAULTS))
        self.options = {key: self.settings.get(key, value) for key, value in DEFAULTS.items()}
        if self.options["source"] not in SOURCES:
            self.options["source"] = "selected"
        if self.options["method"] not in engine.METHODS:
            self.options["method"] = "timecode"
        if self.options["camera_audio"] not in engine.CAMERA_AUDIO:
            self.options["camera_audio"] = "tracks"
        self.names = {}          # {file path: device name} the person changed
        self.order = []          # device names, as the person ordered them
        self.master = ""         # the device chosen as the master clock ("" = Buddy picks)
        self.scanned = None      # {"what", "clips": [PodClip]} from the last read
        self.result = None
        self.log = []
        self.job = None          # {"label", "stage", "done", "total", "started"}
        self._worker = None
        self._finish = None
        self._busy = False       # Resolve is in use for this page (the overlay is up)
        self._calls = ResolveCalls(self)

    def web_ready(self):
        self._push_state()
        self._push_devices()
        self.emit("job", self._job_view())
        self.emit("result", self.result)
        self.emit("log", self.log)

    def working_on(self):
        return "Syncing a podcast" if self.job else None

    def on_connection_changed(self, connected):
        self._push_state()

    def on_app_quitting(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()

    def _ffmpeg(self):
        try:
            from core.media_search.extract import find_ffmpeg
            return find_ffmpeg()
        except Exception:  # noqa: BLE001
            return None

    def _push_state(self):
        self.emit("state", {**self.options, "ffmpeg": bool(self._ffmpeg()),
                            "connected": getattr(self.host, "controller", None) is not None,
                            "busy": bool(self.job), "scanned": self.scanned is not None})

    def _devices(self):
        if self.scanned is None:
            return []
        return engine.group_devices(self.scanned["clips"], self.names, self.order)

    def _push_devices(self):
        if self.scanned is None:
            self.emit("devices", None)
            return
        devices = self._devices()
        master = engine.master_device(devices, self.master)
        self.emit("devices", {
            "what": self.scanned["what"],
            "master": master.name if master else "",
            "chosen": bool(self.master) and master is not None and master.name == self.master,
            "devices": [{
                "name": d.name, "kind": d.kind, "seconds": round(d.seconds, 1),
                "timecode": sum(1 for c in d.clips if c.timecode is not None),
                "sound": any(c.has_audio for c in d.clips),
                "clips": [{"path": c.path, "name": c.name, "seconds": round(c.seconds, 1),
                           "video": c.has_video, "audio": c.has_audio, "timecode": c.timecode is not None,
                           "lane": c.lane} for c in d.clips],
            } for d in devices],
        })

    def _log(self, text, kind="info"):
        self.log.append({"time": time.strftime("%H:%M:%S"), "text": text, "kind": kind})
        del self.log[:-MAX_LOG]
        self.emit("log", self.log)

    def _alert(self, title, text):
        self.emit("alert", {"title": title, "text": text})

    def _save_option(self, key, value):
        self.options[key] = value
        self.settings[key] = value
        try:
            self.settings.save()
        except Exception:  # noqa: BLE001 - not saved is only not remembered
            pass

    # ---------------------------------------------------------- choices --

    def on_option(self, payload):
        payload = payload or {}
        key, value = payload.get("key"), payload.get("value")
        if key == "source" and value in SOURCES:
            self._save_option(key, value)
        elif key == "method" and value in engine.METHODS:
            self._save_option(key, value)
        elif key == "camera_audio" and value in engine.CAMERA_AUDIO:
            self._save_option(key, value)
        elif key == "fine":
            self._save_option(key, bool(value))
        self._push_state()

    def _device_paths(self, name):
        return [c.path for d in self._devices() if d.name == name for c in d.clips]

    def on_rename_device(self, payload):
        payload = payload or {}
        old, new = payload.get("device"), clean_name(payload.get("name"))
        paths = self._device_paths(old)
        if not paths or not new or new == old:
            self._push_devices()
            return
        for path in paths:
            self.names[path] = new
        self.order = [new if n == old else n for n in self.order]
        if self.master == old:
            self.master = new
        self._push_devices()

    def on_move_clip(self, payload):
        payload = payload or {}
        path, device = payload.get("path"), payload.get("device")
        clips = self.scanned["clips"] if self.scanned else []
        if not any(c.path == path for c in clips):
            return
        if device == "__new__":
            here = next((c.device for c in clips if c.path == path), "Device")
            taken = {d.name for d in self._devices()}
            n = 2
            while f"{here} ({n})" in taken:
                n += 1
            device = f"{here} ({n})"
        device = clean_name(device)
        if device:
            self.names[path] = device
        self._push_devices()

    def on_move_device(self, payload):
        payload = payload or {}
        names = [d.name for d in self._devices()]
        name, step = payload.get("device"), payload.get("step")
        if name not in names or step not in (-1, 1):
            return
        i = names.index(name)
        j = i + step
        if 0 <= j < len(names):
            names[i], names[j] = names[j], names[i]
        self.order = names
        self._push_devices()

    def on_master(self, payload):
        name = (payload or {}).get("device") or ""
        self.master = name if name in {d.name for d in self._devices()} else ""
        self._push_devices()

    # ---------------------------------------------------------- reading --

    def _controller(self):
        """The shell's connection to Resolve, or None (the shell says why)."""
        try:
            return self.host.ensure_connected()
        except ResolveConnectionError as exc:
            self._alert("Couldn't reach Resolve", str(exc))
            return None

    def _resolve(self, text, name, args, job, done):
        """A long Resolve step (resolve_ext's) behind the overlay, in the
        Resolve reader's process: Buddy keeps drawing (core/busy_job.py)."""
        self._busy = True

        def back(result, error):
            self._busy = False
            done(result, error)
        busy_job.run(self.host, self._calls, name, args, job, back, text)

    def _read(self, controller, then):
        """The recordings read (resolve_ext.read_here), then then(document,
        workdir, base, what) - or what went wrong said, and nothing."""
        source = self.options["source"]

        def back(result, error):
            if error is not None:
                self._log(f"Couldn't read the clips: {error}", "error")
                return self._alert("Couldn't read the clips", str(error))
            for text in result["warnings"]:
                self._log(text, "warn")
            try:
                with open(result["path"], encoding="utf-8") as handle:
                    document = json.load(handle)
            except (OSError, ValueError) as exc:
                return self._alert("Couldn't read the clips", str(exc))
            then(document, result["workdir"], result["base"], result["what"])
        self._resolve("Reading the clips…", "podsync.read", {"source": source},
                      lambda: sync_ext.read_here(controller, source), back)

    def on_scan(self, _payload=None):
        if self.job or self._busy:
            return
        controller = self._controller()
        if controller is not None:
            self._read(controller, self._scanned)

    def _scanned(self, document, workdir, _base, what):
        shutil.rmtree(workdir, ignore_errors=True)
        try:
            clips = engine.read_clips(document)
        except Exception as exc:  # noqa: BLE001
            self._log(f"Couldn't read the clips: {type(exc).__name__}: {exc}", "error")
            self._alert("Couldn't read the clips", str(exc))
            return
        if not clips:
            self._alert("Couldn't read the clips", "There are no clips there to sync.")
            return
        self.scanned = {"what": what, "clips": clips}
        devices = self._devices()
        self._log(f"Read {len(clips)} clips: {len(devices)} cameras and recorders.")
        self._push_state()
        self._push_devices()

    # ------------------------------------------------------------- sync --

    def on_sync(self, _payload=None):
        if self.job:
            return
        controller = self._controller()
        if controller is None:
            return
        method, fine = self.options["method"], bool(self.options["fine"])
        ffmpeg = self._ffmpeg()
        if method == "waveform" and not ffmpeg:
            self._alert("ffmpeg is needed", "Syncing by sound reads the recordings with ffmpeg, and Buddy "
                        "couldn't find it. Install ffmpeg, or say where it is in Project Setup's settings.")
            return
        if self._busy:
            return
        self._read(controller, lambda document, workdir, base, what: self._sync(
            controller, document, workdir, base, what, method, fine, ffmpeg))

    def _sync(self, controller, document, workdir, base, what, method, fine, ffmpeg):
        """The sync itself, on a worker (pure work: ffmpeg, numpy), then the
        new timeline imported (resolve_ext.build_here)."""
        names, order, master = dict(self.names), list(self.order), self.master
        camera_audio = self.options["camera_audio"]

        def job(report):
            return engine.sync(document, method, ffmpeg_path=ffmpeg, report=report, names=names, order=order,
                               fine=fine, camera_audio=camera_audio, master=master)

        def finish(payload):
            """(the reader job's name, its args, the job here, and what to
            make of its answer) - the import, for _finish_now to run."""
            new_doc, result = payload
            path = os.path.join(workdir, "synced.otio")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(new_doc, handle)
            args = {"path": path, "workdir": workdir, "base": base, "tracks": result["tracks"]}

            def made(answer):
                result["warnings"].extend(answer["warnings"])
                result["timeline"] = answer["timeline"]
                result["what"] = what
                return result
            return "podsync.build", args, lambda: sync_ext.build_here(controller, **args), made

        self._start("Syncing the recordings", job, finish)

    def _start(self, label, job, finish):
        from pages.project_setup.worker import EngineWorker
        self.job = {"label": label, "stage": "Starting", "done": 0, "total": 0, "started": time.monotonic()}
        self._finish = finish
        self._worker = EngineWorker(job, self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.start()
        self._log(f"{label}…")
        self._push_job()
        self._push_state()

    def _job_view(self):
        if not self.job:
            return None
        return {k: self.job[k] for k in ("label", "stage", "done", "total")} | {
            "seconds": round(time.monotonic() - self.job["started"])}

    def _push_job(self):
        self.emit("job", self._job_view())

    def _took(self):
        return took(time.monotonic() - self.job["started"]) if self.job else "0s"

    def _end(self):
        self.job = None
        self._finish = None
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.release()
            worker.deleteLater()
        self._push_job()
        self._push_state()

    def _on_progress(self, stage, done, total):
        if self.job:
            self.job.update(stage=stage, done=done, total=total)
            self._push_job()

    def on_cancel(self, _payload=None):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            if self.job:
                self.job["stage"] = "Stopping"
                self._push_job()

    def _on_cancelled(self):
        length = self._took()
        self._end()
        self._log(f"Stopped after {length} - nothing was changed.", "warn")

    def _on_failed(self, message, expected):
        length = self._took()
        self._end()
        if expected:
            self._log(f"{message} (after {length})", "error")
            self._alert("Couldn't sync", message)
            return
        first, _, rest = message.partition("\n")
        self._log(f"Error after {length}: {first}", "error")
        for line in rest.rstrip().splitlines():
            self._log(f"    {line}", "error")

    def _on_done(self, payload):
        self._finish_now(payload)

    def _finish_now(self, payload):
        if self._busy:                  # Resolve is in use here already: wait its turn
            QTimer.singleShot(250, lambda: self._finish_now(payload))
            return
        finish, length = self._finish, self._took()
        try:
            name, args, job, made = finish(payload)
        except Exception as exc:  # noqa: BLE001
            return self._build_failed(exc)
        self._resolve("Building the synced timeline…", name, args, job,
                      lambda answer, error: self._built(made, answer, error, length))

    def _build_failed(self, exc):
        self._end()
        self._log(f"Couldn't build the timeline: {exc}", "error")
        self._alert("Couldn't build the timeline", str(exc))

    def _built(self, made, answer, error, length):
        if error is not None:
            return self._build_failed(error)
        result = made(answer)
        self._end()
        result["drift_by_device"] = engine.device_drift(result["drift"])
        self.result = result
        self.emit("result", result)
        self._log(f'Built "{result["timeline"]}": {result["placed"]} of {result["total"]} clips placed '
                  f"({length}).", "success")
        for row in result["unplaced"]:
            self._log(f'Not synced: "{row["clip"]}" - {row["why"]}', "warn")
        for text in result["warnings"]:
            self._log(text, "warn")
        for row in result["drift"]:
            if row["warn"]:
                self._log(f'"{row["clip"]}" drifts {abs(row["ms_per_hour"]):.0f} ms per hour against '
                          f'{row["against"]} - its ends are up to {row["frames"] / 2:.1f} frames out.', "warn")
        self.achieve("syncs", 1)
