#!/usr/bin/env python3
"""
Audit's step for the Automation tab (pages/automation/): check the open
timeline - and its sound, if the step says so - as the tab's Check and
Check sound do, and say what it found. The step can stop the automation
when it finds problems (or warnings too); otherwise it reports and the
next step runs. Audit's page shows the same result, ready to go through.
"""

from __future__ import annotations

from . import checks

_STOP = [{"id": "never", "label": "Never – just report"},
         {"id": "problems", "label": "If it finds problems"},
         {"id": "warnings", "label": "If it finds problems or warnings"}]


class AuditAutomationMixin:
    def automation_steps(self):
        return [{"id": "check", "label": "Check the timeline",
                 "about": "Audit's checks on the open timeline: missing media and fonts, gaps, flash frames, "
                          "subtitles, frame rates and more.",
                 "options": [{"key": "sound", "label": "Check the sound too", "kind": "check", "default": False,
                              "hint": "Renders the mix to measure loudness and peaks – takes about as long as a "
                                      "fast render."},
                             {"key": "stop_if", "label": "Stop the automation", "kind": "select",
                              "default": "never", "choices": _STOP}]}]

    def run_automation_step(self, step_id, options, run):
        if step_id != "check":
            return run.fail(f"Audit has no step called {step_id}.")
        if self.checking or self.fixing or self.sound_job is not None or self._auto is not None:
            return run.fail("Audit is busy with something else – wait for it to finish, then run it again.")
        self._auto = {"run": run, "options": options}
        run.progress("Reading the timeline…")
        self.on_check()
        if not self.checking:                  # it couldn't start: why is in self.problem
            self._auto_conclude(self.problem or "Couldn't check the timeline.")

    # The page's hooks (page.py calls these while self._auto is set).

    def _auto_scanned(self, error):
        if error:
            return self._auto_conclude(error)
        if self._auto["options"]["sound"]:
            self._auto["run"].progress("Checking the sound…", 0)
            self._auto["run"].on_stop = self.on_stop_sound
            self.on_check_sound()
            if self.sound_job is not None:
                return                         # _on_sound concludes it
            return self._auto_conclude("The sound couldn't be checked – Resolve wasn't reachable, or every "
                                       "sound check is switched off in Audit.")
        self._auto_conclude()

    def _auto_sound_progress(self, pct, stage):
        names = {"render": "Rendering the mix", "measure": "Measuring the mix", "recordings": "Listening to the clips"}
        self._auto["run"].progress(names.get(stage, "Checking the sound"), pct)

    def _auto_conclude(self, error=""):
        a, self._auto = self._auto, None
        if a is None:
            return
        if error:
            return a["run"].fail(error)
        counts = (self.result or {}).get("counts") or {}
        problems, warnings = counts.get(checks.PROBLEM, 0), counts.get(checks.WARNING, 0)
        notes = counts.get(checks.NOTE, 0)
        parts = [f"{n} {word}{'' if n == 1 else 's'}" for n, word in
                 ((problems, "problem"), (warnings, "warning"), (notes, "note")) if n]
        text = ("Found " + ", ".join(parts) + " – see Audit.") if parts else "Nothing to fix."
        if self.sound_error:
            text += f" The sound couldn't be checked: {self.sound_error}"
        stop_if = a["options"]["stop_if"]
        if (stop_if == "problems" and problems) or (stop_if == "warnings" and (problems or warnings)):
            return a["run"].fail(text)
        a["run"].done(text)
