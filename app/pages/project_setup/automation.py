#!/usr/bin/env python3
"""
Project Setup's steps for the Automation tab (pages/automation/): the same
handlers the tab's buttons run, with the step's own options put in place
for the length of the call - so a button and an automation never do the
job two ways.

While a step runs (self._auto), what the tab would show goes to the run
instead (page.py routes _log and _alert here): an alert is the step
failing, an error line too, the last success line is what the step says it
did. A step that starts a job - Sync, Collapse, the proxies - is waited
for: _end_job concludes it (after the finish has logged its lines), and
Stop is the job's Cancel. Meanwhile the tab's own buttons wait.

Each step leaves Resolve ready for the next: Import opens the bin it made,
Populate opens the timeline it made, and the proxies, Populate and Sync go
by what's open.
"""

from __future__ import annotations

from contextlib import contextmanager

from PySide6.QtCore import QTimer

from . import align_engine, proxy

_SCOPES = [{"id": "bin", "label": "The open bin"}, {"id": "all", "label": "Every clip in the Media Pool"},
           {"id": "timeline", "label": "The clips on the open timeline"}]
_METHODS = [{"id": align_engine.METHOD_TIMECODE, "label": "Timecode"},
            {"id": align_engine.METHOD_WAVEFORM, "label": "Waveform (by their audio)"}]


class ProjectSetupAutomationMixin:
    def automation_steps(self):
        s = self.data_mgr.settings
        resolutions = [{"id": k, "label": v["label"]} for k, v in proxy.RESOLUTIONS.items()]
        codecs = [{"id": c["id"], "label": c["label"]} for c in proxy.codec_choices()]
        return [
            {"id": "bins", "label": "Create bins",
             "about": "Makes the bins in the list in the Media Pool – one per line, '>' for one inside the bin above.",
             "options": [{"key": "bin_list", "label": "Bins", "kind": "textarea", "required": True,
                          "default": s.get("bin_list", "")}]},
            {"id": "import", "label": "Import a folder",
             "about": "Brings a folder on disk into the Media Pool as bins, its subfolders as bins inside, and "
                      "opens the bin it made.",
             "options": [{"key": "folder", "label": "Folder", "kind": "folder", "required": True, "ask": True,
                          "default": ""},
                         {"key": "to_master", "label": "Put it at the top of the Media Pool (Master)", "kind": "check",
                          "default": bool(s.get("import_to_master", False))}]},
            {"id": "proxies", "label": "Make proxies",
             "about": "Renders proxy files beside the camera originals with ffmpeg and links them in Resolve. "
                      "Clips that have one already are linked, not rendered again.",
             "options": [{"key": "scope", "label": "For", "kind": "select", "default": "bin", "choices": _SCOPES},
                         {"key": "resolution", "label": "Size", "kind": "select", "choices": resolutions,
                          "default": self.proxy_options["resolution"]},
                         {"key": "codec", "label": "Format", "kind": "select", "choices": codecs,
                          "default": self.proxy_options["codec"]}]},
            {"id": "populate", "label": "Put the bin on a timeline",
             "about": "Puts the open bin's clips on a timeline, in order.",
             "options": [{"key": "new_timeline", "label": "On a new timeline, named after the bin", "kind": "check",
                          "default": True},
                         {"key": "name", "label": "Timeline name (empty: the bin's)", "kind": "text", "default": "",
                          "ask": True},
                         {"key": "recursive", "label": "Include the bins inside it", "kind": "check",
                          "default": True}]},
            {"id": "sync", "label": "Sync",
             "about": "Syncs the open timeline's clips into a new multicam-style timeline, '(Synced)' – the one "
                      "you started from isn't changed.",
             "options": [{"key": "method", "label": "Sync by", "kind": "select", "choices": _METHODS,
                          "default": self.sync_options["method"]},
                         {"key": "sync_audio", "label": "Also line up the audio by its waveform", "kind": "check",
                          "default": bool(self.sync_options["sync_audio"])}]},
            {"id": "sync_external", "label": "Sync external audio",
             "about": "Lines up separately recorded audio with the cameras' own sound, on a new timeline.",
             "options": []},
            {"id": "collapse", "label": "Collapse the timeline",
             "about": "Packs the open timeline's clips onto as few tracks as they fit on, on a new timeline.",
             "options": [{"key": "delete_silent", "label": "Leave out silent audio clips", "kind": "check",
                          "default": bool(s.get("collapse_delete_silent_audio", False))}]},
            {"id": "shift", "label": "Shift to the start",
             "about": "Moves everything on the open timeline to its first frame.",
             "options": [{"key": "close_gaps", "label": "Close the gaps between groups", "kind": "check",
                          "default": bool(s.get("shift_close_gaps", False))}]},
        ]

    # ------------------------------------------------------------ running --

    def run_automation_step(self, step_id, options, run):
        if self._auto is not None or self._sync_busy or any(s.job is not None for s in self._slots.values()):
            return run.fail("Project Setup is busy with something else – wait for it to finish, then run it again.")
        if self.ensure_connected() is None:
            return run.fail("Not connected to Resolve – open Resolve with a project, then run it again.")
        self._auto = {"run": run, "error": "", "last": "", "slot": None, "stopped": False}
        try:
            if step_id == "bins":
                with self._auto_settings(bin_list=options["bin_list"]):
                    self.on_create_bins({})
            elif step_id == "import":
                with self._auto_settings(import_to_master=options["to_master"]), \
                        self._auto_attrs(import_folder=options["folder"]):
                    self._scan()
                    self.on_import_folder({})
            elif step_id == "proxies":
                with self._auto_attrs(proxy_options={**self.proxy_options, "scope": options["scope"],
                                                     "resolution": options["resolution"],
                                                     "codec": options["codec"]}):
                    self.on_proxy_go({})
            elif step_id == "populate":
                self._auto_populate(options)
            elif step_id == "sync":
                with self._auto_attrs(sync_options={**self.sync_options, "method": options["method"],
                                                    "sync_audio": options["sync_audio"]}):
                    self.on_assemble({})
            elif step_id == "sync_external":
                self.on_sync_external({})
            elif step_id == "collapse":
                with self._auto_settings(collapse_delete_silent_audio=options["delete_silent"]):
                    self.on_collapse({})
            elif step_id == "shift":
                with self._auto_settings(shift_close_gaps=options["close_gaps"]):
                    self.on_shift({})
            else:
                self._auto["error"] = f"Project Setup has no step called {step_id}."
        except Exception as exc:  # noqa: BLE001 - the step fails, saying why; the run stops there
            self._auto_error(f"{type(exc).__name__}: {exc}")
        if self._auto is not None and self._auto["slot"] is None:
            self._auto_conclude()           # done straight away: nothing started that's still running

    @contextmanager
    def _auto_settings(self, **values):
        """Saved settings set for the length of a step - never saved."""
        settings, missing = self.data_mgr.settings, object()
        before = {k: settings.get(k, missing) for k in values}
        settings.update(values)
        try:
            yield
        finally:
            for k, v in before.items():
                if v is missing:
                    settings.pop(k, None)
                else:
                    settings[k] = v

    @contextmanager
    def _auto_attrs(self, **values):
        before = {k: getattr(self, k) for k in values}
        for k, v in values.items():
            setattr(self, k, v)
        try:
            yield
        finally:
            for k, v in before.items():
                setattr(self, k, v)
            if "import_folder" in values:
                self._scan()            # the Import tab shows its own folder again

    # The page's hooks (page.py calls these while self._auto is set).

    def _auto_log(self, text, kind):
        a = self._auto
        a["run"].log(text, kind)
        if kind == "error" and not a["error"]:
            a["error"] = text
        elif kind == "success":
            a["last"] = text

    def _auto_alert(self, title, text):
        a = self._auto
        if text != a["error"]:          # the log line before it usually said it already
            a["run"].log(f"{title}: {text}" if title else text, "error")
        if not a["error"]:
            a["error"] = text

    def _auto_error(self, text):
        if self._auto is not None:
            self._auto["run"].log(text, "error")
            if not self._auto["error"]:
                self._auto["error"] = text

    def _auto_job_started(self, slot_name):
        a = self._auto
        a["slot"] = slot_name
        a["run"].on_stop = lambda: self.on_cancel_job({"slot": slot_name})

    def _auto_job_progress(self, slot_name, stage, done, total):
        a = self._auto
        if a is not None and a["slot"] == slot_name:
            a["run"].progress(f"{stage} ({done + 1} of {total})" if total and total > 1 else stage,
                              100 * done / total if total else None)

    def _auto_job_ended(self, slot_name, cancelled=False):
        a = self._auto
        if a is None or a["slot"] != slot_name:
            return
        if cancelled:
            a["stopped"] = True
        # After the finish has logged what it did (it ends the job first).
        QTimer.singleShot(0, self._auto_conclude)

    def _auto_conclude(self):
        a, self._auto = self._auto, None
        if a is None:
            return
        self._push_state()
        if a["stopped"]:
            a["run"].fail("Stopped.")
        elif a["error"]:
            a["run"].fail(a["error"])
        else:
            a["run"].done(a["last"] or "Done.")

    # ------------------------------------------------------------ Populate --

    def _auto_populate(self, options):
        controller = self.ensure_connected()
        folder = controller.get_current_bin()
        if folder is None:
            return self._auto_error("Couldn't tell which bin is open in the Media Pool.")
        name = folder.GetName() or "Master"
        clips = controller.get_bin_clips(folder, recursive=options["recursive"])
        if not clips:
            return self._auto_error(f"'{name}' has no media clips to put on a timeline.")
        if not options["new_timeline"]:
            with self._auto_attrs(recursive=options["recursive"]):
                return self.on_add_to_timeline({})
        self._overlay(True, f"Making a timeline of '{name}'…")
        try:
            title = controller.unique_timeline_name(options.get("name") or name)
            controller.create_timeline(title, clips)
        except Exception as exc:  # noqa: BLE001 - Resolve refused, or the project went away
            return self._auto_error(f"Couldn't make the timeline: {exc}")
        finally:
            self._overlay(False)
        self._auto_log(f"Made the timeline '{title}' from {len(clips)} clip(s) in '{name}', and opened it.", "success")
        self._refresh_tab(connect=False)
