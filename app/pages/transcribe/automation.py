#!/usr/bin/env python3
"""
Subtitles' step for the Automation tab (pages/automation/): transcribe the
open timeline, as the Subtitles tab's Transcribe button does - the same
on_run - with the step's model, language and placing put in place for the
call. The two questions the tab would ask are the step's options instead:
use Resolve's own transcription if there is one, and replace subtitles
already on track 1 or keep the SRT file only.

While the step runs (self._auto) an alert is the step failing, and the
job's end (_on_finished) concludes it; Stop is the tab's Stop.
"""

from __future__ import annotations

from . import plan

SAME = "same"


class TranscribeAutomationMixin:
    def automation_steps(self):
        models, _chosen = self._model_options()
        return [{
            "id": "transcribe", "label": "Transcribe the timeline",
            "about": "Turns the open timeline's dialogue into subtitles on subtitle track 1, and saves them as an "
                     "SRT file.",
            "options": [
                {"key": "model", "label": "Model", "kind": "select", "default": SAME,
                 "choices": [{"id": SAME, "label": "As set in Subtitles"}]
                 + [{"id": m["id"], "label": m["label"]} for m in models]},
                {"key": "language", "label": "Language", "kind": "select", "default": SAME, "ask": True,
                 "choices": [{"id": SAME, "label": "As set in Subtitles"}]
                 + [{"id": code, "label": label} for label, code in plan.LANGUAGES]},
                {"key": "place", "label": "Put them on the timeline", "kind": "check", "default": True},
                {"key": "replace", "label": "Replace subtitles already on track 1", "kind": "check", "default": False,
                 "hint": "Off: when track 1 has subtitles, the new ones are kept as an SRT file only."},
                {"key": "reuse", "label": "Use Resolve's own transcription if it has one", "kind": "check",
                 "default": True},
            ]}]

    def run_automation_step(self, step_id, options, run):
        if step_id != "transcribe":
            return run.fail(f"Subtitles has no step called {step_id}.")
        if self.job is not None or self._placing or getattr(self, "_auto", None) is not None:
            return run.fail("Subtitles is busy with something else – wait for it to finish, then run it again.")
        if not self.ready:
            return run.fail("Transcription isn't set up on this computer yet – set it up on Subtitles' Setup tab.")
        s = self.settings
        saved = {k: s.get(k) for k in ("model", "language", "place_on_timeline")}
        if options["model"] != SAME:
            s["model"] = options["model"]
        if options["language"] != SAME:
            s["language"] = options["language"]
        s["place_on_timeline"] = bool(options["place"])
        # Put back when the step ends: placing reads it when the job's done.
        self._auto = {"run": run, "options": options, "error": "", "saved": saved}
        run.on_stop = self.on_stop
        try:
            self.on_run()
        except Exception as exc:  # noqa: BLE001 - the step fails, saying why
            self._auto["error"] = f"{type(exc).__name__}: {exc}"
        if self._auto is not None and self.job is None and not self._placing:
            self._auto_conclude()              # it didn't start: why is in the error

    # The page's hooks (page.py calls these while self._auto is set).

    def _auto_answer(self, kind, then, cancelled):
        """The tab's questions, answered from the step's options."""
        options = self._auto["options"]
        if kind == "existing":
            return then("use" if options["reuse"] else "again")
        if kind == "replace":
            if options["replace"]:
                return then(None)
            self._auto["run"].log("Track 1 already has subtitles – kept the new ones as an SRT file only.", "warn")
            return cancelled() if cancelled else None
        return cancelled() if cancelled else None

    def _auto_conclude(self):
        a, self._auto = self._auto, None
        if a is None:
            return
        self._pending_translate = None          # an automation translates only if it has the step
        for k, v in a["saved"].items():         # the tab's own choices, as they were
            if v is None:
                self.settings.values.pop(k, None)
            else:
                self.settings[k] = v
        self.settings.save()
        self._push_options()
        r = self.result or {}
        if a["error"]:
            a["run"].fail(a["error"])
        elif not r:
            a["run"].fail("Transcribing didn't start.")
        elif r.get("ok"):
            a["run"].done(" ".join(t for t in (r.get("summary", ""), r.get("message", "")) if t))
        else:
            a["run"].fail(r.get("message") or "Transcribing stopped.")
