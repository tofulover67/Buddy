#!/usr/bin/env python3
"""
Running an automation: its steps one after another, each handed to the tool
it's from (ToolPage.run_automation_step, pages/base.py) with a StepRun to
report through. A step says done() or fail() exactly once - straight away,
or minutes later when its job ends; the next starts only then, on a fresh
turn of the event loop. A failure stops the run there; Stop asks the step
running to stop (StepRun.on_stop, if it can) and stops the run after it.

No pop-ups: a step reports through the StepRun, and the run says how it
went with a Windows notification (the tray's) and in the Automation tab.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import steps as st

LOG_LIMIT = 400


class StepRun:
    """What a tool's step reports through."""

    def __init__(self, run, index):
        self._run = run
        self.index = index
        self.finished = False
        self.on_stop = None         # the step sets it if it can stop partway (a job's Cancel)

    @property
    def stopping(self) -> bool:
        return self._run.stopping

    def progress(self, text, pct=None):
        if not self.finished:
            self._run._step_progress(self.index, text, pct)

    def log(self, text, kind="info"):
        if not self.finished:
            self._run._log(text, kind, self.index)

    def done(self, summary=""):
        if not self.finished:
            self.finished = True
            QTimer.singleShot(0, lambda: self._run._step_ended(self.index, True, summary))

    def fail(self, message):
        if not self.finished:
            self.finished = True
            QTimer.singleShot(0, lambda: self._run._step_ended(self.index, False, message or "It stopped."))


class AutomationRun(QObject):
    """One run of one automation. changed: something the view shows moved
    on; ended(ok, text): it's over."""
    changed = Signal()
    ended = Signal(bool, str)

    def __init__(self, automation, catalog, pages, answers=None, start_at=0, parent=None):
        super().__init__(parent)
        self.automation = automation
        self.catalog = catalog
        self.pages = pages          # tool id -> page
        self.answers = answers or {}
        self.stopping = False
        self.started = time.monotonic()
        self.log = []
        self.state = []             # per step: {status, text, pct, took, started}
        for i, s in enumerate(automation["steps"]):
            spec = catalog.get((s["tool"], s["step"])) or {}
            self.state.append({"status": "skipped" if i < start_at else "waiting", "text": "", "pct": None,
                               "took": "", "started": 0.0, "label": spec.get("label", s["step"])})
        self.current = start_at
        self.result = None          # (ok, text) once it's over

    # ------------------------------------------------------------ running --

    def start(self):
        QTimer.singleShot(0, self._next)

    def stop(self):
        """Stop: the step running is asked to stop if it can; either way
        nothing after it starts."""
        if self.result is not None or self.stopping:
            return
        self.stopping = True
        self._log("Stopping…", "warn")
        step = getattr(self, "_step", None)
        if step is not None and step.on_stop is not None and not step.finished:
            try:
                step.on_stop()
            except Exception as exc:  # noqa: BLE001 - it ends on its own then
                self._log(f"Couldn't stop it partway ({exc}) – the run stops once it finishes.", "warn")
        self.changed.emit()

    def _next(self):
        if self.result is not None:
            return
        if self.stopping:
            return self._end(False, f"Stopped before step {self.current + 1}.")
        if self.current >= len(self.automation["steps"]):
            return self._end(True, f"Finished in {st.took(time.monotonic() - self.started)}.")
        i = self.current
        s = self.automation["steps"][i]
        spec = self.catalog.get((s["tool"], s["step"]))
        page = self.pages.get(s["tool"])
        state = self.state[i]
        state.update(status="running", text="Starting…", pct=None, started=time.monotonic())
        self._log(f"Step {i + 1}: {state['label']}", "step", i)
        self.changed.emit()
        if spec is None or page is None or not hasattr(page, "run_automation_step"):
            return self._step_ended(i, False, "That step's tool isn't in this Buddy.")
        self._step = StepRun(self, i)
        try:
            page.run_automation_step(s["step"], st.options_for_run(s, spec, self.answers), self._step)
        except Exception as exc:  # noqa: BLE001 - a bug in a step: the run stops there, saying so
            self._step.fail(f"{type(exc).__name__}: {exc}")

    def _step_progress(self, index, text, pct):
        if index == self.current:
            self.state[index].update(text=text or self.state[index]["text"],
                                     pct=None if pct is None else max(0, min(100, int(pct))))
            self.changed.emit()

    def _step_ended(self, index, ok, text):
        if index != self.current or self.result is not None:
            return
        state = self.state[index]
        state.update(status="done" if ok else "failed", text=text or ("Done." if ok else ""), pct=None,
                     took=st.took(time.monotonic() - state["started"]))
        self._log(text or "Done.", "success" if ok else "error", index)
        self._step = None
        if not ok:
            return self._end(False, f"Stopped at step {index + 1} ({state['label']}): {text}")
        self.current += 1
        self.changed.emit()
        QTimer.singleShot(0, self._next)

    def _end(self, ok, text):
        self.result = (ok, text)
        for s in self.state:
            if s["status"] == "waiting":
                s["status"] = "not_run"
        self._log(text, "success" if ok else "error")
        self.changed.emit()
        self.ended.emit(ok, text)

    # --------------------------------------------------------------- view --

    def _log(self, text, kind="info", index=None):
        self.log.append({"time": time.strftime("%H:%M:%S"), "text": str(text), "kind": kind, "step": index})
        del self.log[:-LOG_LIMIT]
        self.changed.emit()

    def view(self) -> dict:
        return {"automation": self.automation["id"], "name": self.automation["name"], "running": self.result is None,
                "stopping": self.stopping, "ok": self.result[0] if self.result else None,
                "text": self.result[1] if self.result else "", "current": self.current,
                "took": st.took(time.monotonic() - self.started),
                "steps": [dict({k: v for k, v in s.items() if k != "started"},
                               took=st.took(time.monotonic() - s["started"]) if s["status"] == "running" else s["took"])
                          for s in self.state],
                "log": self.log[-120:]}
