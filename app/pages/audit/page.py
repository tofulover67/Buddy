#!/usr/bin/env python3
"""
Audit - checks the open timeline before it's delivered: missing media,
fonts that aren't installed, black gaps, flash frames, tracks and clips
turned off, unfinished titles, to-do markers, subtitles too long or too
fast, footage at another frame rate, pictures blown up past their size -
and, with Check sound, the mix's clipping, peaks and loudness, and what
went wrong in each clip's recording: clipped, dropping out, on one side,
out of phase, popping (checks.py says what each looks for). A web page (core/web_page.py): the view is
web/index.html + audit.js.

Reading changes nothing in Resolve. The timeline is read in the Resolve
reader's process (resolve_ext.scan, job "audit.scan"), so even a long one
never freezes Buddy; the checks run here on what it read. A check is made
when the page is first shown and with Check again - not on a poll: the
result says when it was made.

Writing: Go to moves the playhead. A fix (resolve_ext.fix, job
"audit.fix") changes the timeline - only when the person presses it, and
one that takes something off the timeline only after they've said yes in
the view. The timeline is checked again straight after.

Check sound renders the timeline's mix (SoundJob, on a thread: Resolve's
render, then audio.measure()) - the person's Deliver settings and render
queue are left as they were, and the render is deleted once measured -
then decodes each clip's stretch of its file (sources.listen()). Either
is left out when all its checks are turned off.

Protocol:
    to the view    state, toast
    from the view  check, goto, toggle, open_tool, fix, check_sound,
                   stop_sound, target
"""

import os
import time

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication

from core.i18n import tr
from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveCalls
from core.resolve_worker import ResolveWorker
from core.settings_store import BUDDY_DIR
from core.web_page import WebToolPage

from . import audio, checks, resolve_ext, sources
from .automation import AuditAutomationMixin

BUSY_AFTER_S = 2
DEFAULTS = {"off": [], "target": audio.DEFAULT_TARGET}
# A group's own button: where the fix is in Buddy.
FIX_TOOL = {"offline": ("media_relink", "Open Media Relink")}
RENDER_DIR = os.path.join(BUDDY_DIR, "audit", "renders")
# Of the sound check's progress bar: the render, then the measuring,
RENDER_SHARE = 45
MEASURE_SHARE = 60          # ... and the recordings the rest


def installed_fonts():
    """{family: [styles]} - or None where Qt can't see the system's fonts
    (the offscreen platform the tests use has a list of its own)."""
    if QGuiApplication.platformName() == "offscreen":
        return None
    return {family: QFontDatabase.styles(family) for family in QFontDatabase.families()}


class SoundJob(QThread):
    """Renders the checked timeline's mix and measures it (with `mix`), then
    listens to each clip's recording (with `scan`: sources.listen()).
    progressed(0-100, stage); done(result, error) - one of them None. The
    result: audio.measure()'s (or no figures, without `mix`) with "mixed",
    "sources" (None without `scan`) and "unread" ([path, why])."""

    progressed = Signal(int, str)
    done = Signal(object, object)

    def __init__(self, controller, timeline_id, fps, mix=True, scan=None, parent=None):
        super().__init__(parent)
        self.controller, self.timeline_id, self.fps = controller, timeline_id, fps
        self.mix, self.scan = mix, scan
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        from pages.transcribe.resolve_ext import RenderCancelled, TranscribeController

        path = None
        try:
            project = self.controller.current_project()
            timeline = project.GetCurrentTimeline() if project else None
            if timeline is None:
                raise ResolveConnectionError("No timeline is open in Resolve.")
            if self.timeline_id and str(timeline.GetUniqueId() or "") != self.timeline_id:
                raise ResolveConnectionError("A different timeline is open now – check again first.")
            # Where each stage's progress goes: the render, the measuring, the recordings.
            render_to, measure_to = (RENDER_SHARE, MEASURE_SHARE) if self.scan is not None else (60, 100)
            if not self.mix:
                render_to = measure_to = 0
            result = {"lufs": None, "peak_db": None, "seconds": 0.0, "peaks": [], "clipped": [], "mixed": False}
            if self.mix:
                self.progressed.emit(0, "render")
                path = TranscribeController(self.controller).render_timeline_audio(
                    RENDER_DIR, progress=lambda pct: self.progressed.emit(int(pct * render_to / 100), "render"),
                    cancelled=lambda: self._stop, prefix="buddy_audit")
                if self._stop:
                    raise RenderCancelled()
                self.progressed.emit(render_to, "measure")
                result, error = audio.measure(
                    path, self.fps, lambda f: self.progressed.emit(render_to + int(f * (measure_to - render_to)), "measure"),
                    lambda: self._stop)
                if self._stop:
                    raise RenderCancelled()
                if error:
                    return self.done.emit(None, error)
                result = dict(result, mixed=True)
            result["sources"], result["unread"] = None, []
            if self.scan is not None:
                self.progressed.emit(measure_to, "recordings")
                found, unread = sources.listen(
                    self.scan, lambda f: self.progressed.emit(measure_to + int(f * (100 - measure_to)), "recordings"),
                    lambda: self._stop)
                if self._stop:
                    raise RenderCancelled()
                result["sources"], result["unread"] = found, unread
            self.done.emit(result, None)
        except RenderCancelled:
            self.done.emit(None, "Stopped.")
        except Exception as exc:  # noqa: BLE001 - said on the page
            self.done.emit(None, str(exc) or type(exc).__name__)
        finally:
            if path:
                audio.remove(path)


class AuditPage(AuditAutomationMixin, WebToolPage):
    tool_id = "audit"
    display_name = "Audit"
    category = "Audit"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id, DEFAULTS)
        self.scan = None            # resolve_ext.scan()'s data, from the last check
        self.result = None          # checks.run() on it
        self.checked_at = 0.0
        self.problem = ""           # why the last check couldn't be made
        self.checking = False
        self.waiting = False        # the check has waited on Resolve a while (it's playing)
        self.fixing = False
        self.sound = None           # {"timeline_id", "result", "checked_at"}: the last sound check
        self.sound_job = None
        self.sound_progress = (0, "")
        self.sound_error = ""
        self._worker = ResolveWorker(self)      # the reads, when there's no reader process
        self._calls = ResolveCalls(self, self._worker)
        self._auto = None           # an Automation step running here (automation.py)

    def web_ready(self):
        self._push_state()

    def on_shown(self):
        if self.result is None and not self.checking and getattr(self.host, "connected", False):
            self.on_check()

    def on_app_quitting(self):
        if self.sound_job is not None:
            self.sound_job.cancel()
            self.sound_job.wait(5000)

    # ------------------------------------------------------------ checking --

    def on_check(self, _payload=None):
        if self.checking:
            return
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError as exc:
            self.problem = str(exc)
            return self._push_state()
        self.checking, self.waiting, self.problem = True, False, ""
        self._push_state()
        if not self._calls.call("audit.scan", {}, lambda: resolve_ext.scan(controller), self._on_scan):
            self.checking = False
            self.problem = "Resolve is busy with another of Buddy's requests – try again in a moment."
            return self._push_state()
        self._started = time.monotonic()
        self._watch_wait()

    def _watch_wait(self):
        """Says "Waiting for Resolve" once a check has taken a while - Resolve
        holds every script's calls while its timeline plays."""
        if not self.checking:
            return
        if not self.waiting and time.monotonic() - self._started >= BUSY_AFTER_S:
            self.waiting = True
            self._push_state()
        QTimer.singleShot(250, self._watch_wait)

    def _on_scan(self, scan, error):
        self.checking = self.waiting = False
        if error is not None:
            self.problem = (str(error) if isinstance(error, ResolveConnectionError)
                            else f"Couldn't read the timeline: {error}")
            self._push_state()
            return self._auto_scanned(self.problem) if self._auto is not None else None
        self.scan, self.checked_at = scan, time.time()
        self.achieve("audits", 1)
        self._run_checks()
        if self._auto is not None:
            self._auto_scanned("")

    def _target(self):
        return audio.TARGET_BY_ID.get(self.settings.get("target"), audio.TARGET_BY_ID[audio.DEFAULT_TARGET])

    def _sound_here(self):
        """The last sound check's result, when it was of the timeline checked."""
        if self.sound and self.scan and self.sound["timeline_id"] == self.scan.get("id"):
            return self.sound["result"]
        return None

    def _run_checks(self):
        if self.scan is not None:
            self.result = checks.run(self.scan, installed_fonts(), set(self.settings.get("off") or ()),
                                     self._sound_here(), self._target())
        self._push_state()

    def on_toggle(self, payload):
        """A check turned on or off - the last scan is checked again at once."""
        cid, on = (payload or {}).get("id"), bool((payload or {}).get("on"))
        if cid not in checks.BY_ID:
            return
        off = [c for c in self.settings.get("off") or [] if c != cid] + ([] if on else [cid])
        self.settings["off"] = off
        self.settings.save()
        self._run_checks()

    # -------------------------------------------------------------- go to --

    def on_goto(self, payload):
        try:
            frame = int((payload or {}).get("frame"))
        except (TypeError, ValueError):
            return
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError as exc:
            return self.emit("toast", {"text": str(exc)})
        timeline_id = (self.scan or {}).get("id", "")
        if not self._calls.call("audit.goto", {"frame": frame, "timeline_id": timeline_id},
                                lambda: resolve_ext.goto(controller, frame, timeline_id), self._on_goto):
            self.emit("toast", {"text": "Resolve is busy – try again in a moment."})

    def _on_goto(self, timecode, error):
        if error is not None:
            self.emit("toast", {"text": str(error)})

    def on_open_tool(self, payload):
        tool_id = (payload or {}).get("tool_id")
        if tool_id in {tool for tool, _label in FIX_TOOL.values()}:
            self.host.switch_tool(tool_id)

    # --------------------------------------------------------------- fixes --

    def on_fix(self, payload):
        """Makes the fixes the view sends back - each one a row's own, as
        checks.py made it."""
        fixes = [f for f in (payload or {}).get("fixes") or () if isinstance(f, dict) and f.get("do") in checks.FIXES]
        if not fixes or self.scan is None:
            return
        if self.fixing or self.checking or self.sound_job is not None:
            return self.emit("toast", {"text": "Wait for Audit to finish what it's doing, then try again."})
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError as exc:
            return self.emit("toast", {"text": str(exc)})
        timeline_id = self.scan.get("id", "")
        if not self._calls.call("audit.fix", {"fixes": fixes, "timeline_id": timeline_id},
                                lambda: resolve_ext.fix(controller, fixes, timeline_id), self._on_fix):
            return self.emit("toast", {"text": "Resolve is busy – try again in a moment."})
        self.fixing = True
        self._push_state()

    def _on_fix(self, result, error):
        self.fixing = False
        if error is not None:
            self.emit("toast", {"text": str(error)})
            return self._push_state()
        done, failed = int(result.get("done") or 0), list(result.get("failed") or ())
        if done:
            # The mix may have changed (a track turned on, a clip gone): measure it again to know.
            self.sound = None
        if failed:
            # The reason translated here: the view translates the sentence
            # around it, and a reason is one of Buddy's sentences.
            why = tr(failed[0])
            self.emit("toast", {"text": f"Couldn't fix it: {why}" if not done
                                else f"Fixed {done}, but not {len(failed)}: {why}"})
        elif done:
            self.emit("toast", {"text": "Fixed – checking again" if done == 1 else f"Fixed {done} – checking again"})
        self._push_state()
        self.on_check()

    # --------------------------------------------------------------- sound --

    def on_check_sound(self, _payload=None):
        if self.sound_job is not None or self.scan is None:
            return
        if self.checking or self.fixing:
            return self.emit("toast", {"text": "Wait for Audit to finish what it's doing, then try again."})
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError as exc:
            return self.emit("toast", {"text": str(exc)})
        off = set(self.settings.get("off") or ())
        mix = bool(checks.SOUND - checks.RECORDING - off)
        listen = bool(checks.RECORDING - off)
        if not mix and not listen:
            return
        self.sound_error = ""
        self.sound_progress = (0, "render" if mix else "recordings")
        job = SoundJob(controller, self.scan.get("id", ""), self.scan.get("fps") or 24.0, mix,
                       self.scan if listen else None, self)
        job.progressed.connect(self._on_sound_progress)
        job.done.connect(self._on_sound)
        job.finished.connect(job.deleteLater)
        self.sound_job = job
        job.start()
        self._push_state()

    def on_stop_sound(self, _payload=None):
        if self.sound_job is not None:
            self.sound_job.cancel()

    def _on_sound_progress(self, pct, stage):
        self.sound_progress = (pct, stage)
        self._push_state()
        if self._auto is not None:
            self._auto_sound_progress(pct, stage)

    def _on_sound(self, result, error):
        timeline_id = self.sound_job.timeline_id if self.sound_job is not None else ""
        self.sound_job = None
        if error is not None:
            self.sound_error = "" if error == "Stopped." else str(error)
            self._push_state()
            return self._auto_conclude("Stopped." if error == "Stopped." else "") if self._auto is not None else None
        self.sound = {"timeline_id": timeline_id, "result": result, "checked_at": time.time()}
        self._run_checks()
        if self._auto is not None:
            self._auto_conclude()

    def on_target(self, payload):
        target = (payload or {}).get("id")
        if target not in audio.TARGET_BY_ID:
            return
        self.settings["target"] = target
        self.settings.save()
        self._run_checks()

    def _sound_state(self):
        here = self._sound_here()
        target = self._target()
        return {
            "running": self.sound_job is not None,
            "progress": self.sound_progress[0],
            "stage": self.sound_progress[1],
            "error": self.sound_error,
            "done": here is not None,
            "checked_at": time.strftime("%H:%M", time.localtime(self.sound["checked_at"])) if here else "",
            "lufs": here.get("lufs") if here else None,
            "mixed": bool(here.get("mixed", True)) if here else False,
            "listened": bool(here and here.get("sources") is not None),
            "clips": len(here.get("sources") or ()) if here else 0,
            "unread": [{"name": os.path.basename(p), "why": tr(why)} for p, why in (here.get("unread") or ())][:5]
                      if here else [],
            "peak_db": here.get("peak_db") if here else None,
            "target": target["id"],
            "target_lufs": target["lufs"],
            "ceiling": target["ceiling"],
            "targets": [{"id": t["id"], "label": t["label"]} for t in audio.TARGETS],
            "on": bool(checks.SOUND - set(self.settings.get("off") or ())),
        }

    # --------------------------------------------------------------- view --

    def _push_state(self):
        scan, off = self.scan or {}, set(self.settings.get("off") or ())
        result = self.result
        groups = []
        for group in (result or {}).get("groups", ()):
            fix = FIX_TOOL.get(group["id"])
            groups.append({**group, "fix": {"tool_id": fix[0], "label": fix[1]} if fix else None})
        self.emit("state", {
            "connected": bool(getattr(self.host, "connected", False)),
            "checking": self.checking,
            "waiting": self.waiting,
            "fixing": self.fixing,
            "problem": self.problem,
            "timeline": scan.get("name", ""),
            "fps": scan.get("fps", 0),
            "checked_at": time.strftime("%H:%M", time.localtime(self.checked_at)) if self.checked_at else "",
            "done": result is not None,
            "groups": groups,
            "counts": (result or {}).get("counts", {}),
            "passed": [checks.BY_ID[c]["title"] for c in (result or {}).get("passed", ())],
            "fonts_unchecked": "fonts" in (result or {}).get("skipped", ()) and "fonts" not in off,
            "fixes": checks.FIXES,
            "sound": self._sound_state(),
            "checks": [{"id": c["id"], "title": c["title"], "about": c["about"], "severity": c["severity"],
                        "on": c["id"] not in off, "sound": bool(c.get("sound"))} for c in checks.CHECKS],
        })
