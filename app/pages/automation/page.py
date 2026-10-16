#!/usr/bin/env python3
"""
Automation - several of Buddy's steps one after another, named and kept:
"Ingest" = Import a folder > Make proxies > Put the bin on a timeline >
Sync. Run it and do something else; Buddy says when it's finished (a
Windows notification) or where it stopped and why.

The steps are the tools' own (ToolPage.automation_steps, run_automation_step
- pages/base.py): Project Setup's, Subtitles', Audit's and Command
Center's so far. Each step keeps its options; an option a step marks "ask"
can be asked when the automation starts instead (the folder to import,
different every shoot) - all of them up front, so the rest runs unattended.
steps.py checks the data, runner.py runs it. A web page (web/index.html +
automation.js).

Saved in ~/.automation/settings.json (ToolSettings), so a Buddy backup
takes them along.

Protocol:
    to the view    state, questions, folder, toast
    from the view  new, rename, duplicate, delete, select, add_step, remove_step, move_step,
                   option, ask, browse, run, browse_answer, start, stop, resume, dismiss
"""

from __future__ import annotations

import copy
import os
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QSystemTrayIcon

from core.i18n import tr
from core.resolve_bridge import ResolveConnectionError
from core.web_page import WebToolPage

from . import steps as st
from .runner import AutomationRun

DEFAULTS = {"automations": [], "selected": ""}
# The tools whose steps are offered, in the order the Add a step list shows them.
TOOL_ORDER = ("project_setup", "transcribe", "audit", "command_center")


class AutomationPage(WebToolPage):
    tool_id = "automation"
    display_name = "Automation"
    category = "Automation"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id, dict(DEFAULTS))
        self.run = None             # the AutomationRun going, or the last one (kept to show until dismissed)
        self._answers = {}          # the last run's answers (Resume asks nothing again)
        self._pending = None        # the automation waiting for its questions to be answered
        self._automations = None    # read from settings once the tools exist (catalog())
        self._tick = QTimer(self, interval=1000, timeout=self._push)    # the clocks, while one runs

    def web_ready(self):
        self._push()

    def on_shown(self):
        self._push()                # a tool's choices (Subtitles' models) may have changed

    def working_on(self):
        return f"Running {self.run.automation['name']}" if self.run is not None and self.run.result is None else None

    def on_app_quitting(self):
        if self.run is not None and self.run.result is None:
            self.run.stop()

    # ------------------------------------------------------------- data --

    def catalog(self) -> dict:
        """{(tool, step): spec + tool, tool_label} - every step every tool here offers."""
        out = {}
        pages = getattr(self.host, "pages", {}) or {}
        for tool in sorted(pages, key=lambda t: TOOL_ORDER.index(t) if t in TOOL_ORDER else len(TOOL_ORDER)):
            page = pages[tool]
            if page is self:
                continue
            try:
                specs = page.automation_steps() or []
            except Exception:  # noqa: BLE001 - one tool's bug never takes the list with it
                continue
            for spec in specs:
                out[(tool, spec["id"])] = dict(spec, tool=tool, tool_label=page.display_name)
        return out

    def automations(self) -> list[dict]:
        if self._automations is None:
            catalog = self.catalog()
            self._automations = [a for a in (st.clean_automation(raw, catalog)
                                             for raw in self.settings.get("automations") or []) if a]
        return self._automations

    def _save(self):
        self.settings["automations"] = copy.deepcopy(self.automations())
        self.settings.save()

    def _find(self, automation_id):
        return next((a for a in self.automations() if a["id"] == automation_id), None)

    def _editable(self, automation_id):
        """The automation - unless it's the one running (it can't change under the run)."""
        a = self._find(automation_id)
        if a is not None and self.run is not None and self.run.result is None and self.run.automation["id"] == a["id"]:
            self.emit("toast", {"text": "It's running – change it once it's finished."})
            return None
        return a

    def _step(self, a, step_id):
        return next((s for s in a["steps"] if s["id"] == step_id), None) if a else None

    # ------------------------------------------------------------- view --

    def _push(self):
        catalog = self.catalog()
        tools = []
        for (tool, _sid), spec in catalog.items():
            entry = next((t for t in tools if t["tool"] == tool), None)
            if entry is None:
                entry = {"tool": tool, "label": spec["tool_label"], "steps": []}
                tools.append(entry)
            entry["steps"].append({"id": spec["id"], "label": spec["label"], "about": spec.get("about", "")})
        selected = self.settings.get("selected") or ""
        if not self._find(selected):
            selected = self.automations()[0]["id"] if self.automations() else ""
        self.emit("state", {
            "automations": [self._automation_view(a, catalog) for a in self.automations()],
            "selected": selected, "tools": tools,
            "run": self.run.view() if self.run is not None else None,
        })

    def _automation_view(self, a, catalog):
        steps = []
        for s in a["steps"]:
            spec = catalog.get((s["tool"], s["step"]))
            if spec is None:
                steps.append({"id": s["id"], "tool": s["tool"], "label": s["step"], "tool_label": s["tool"],
                              "about": "This step's tool isn't in this Buddy.", "missing": True, "options": []})
                continue
            steps.append({
                "id": s["id"], "tool": s["tool"], "tool_label": spec["tool_label"], "label": spec["label"],
                "about": spec.get("about", ""), "missing": False,
                "options": [{"key": o["key"], "label": o["label"], "kind": o["kind"], "hint": o.get("hint", ""),
                             "choices": o.get("choices", []), "value": s["options"].get(o["key"]),
                             "can_ask": bool(o.get("ask")), "asked": o["key"] in s["ask"],
                             "required": bool(o.get("required"))} for o in spec.get("options", [])],
            })
        return {"id": a["id"], "name": a["name"], "steps": steps, "last": a.get("last"),
                "problems": st.problems(a, catalog)}

    # ---------------------------------------------------------- editing --

    def on_new(self, _payload=None):
        a = {"id": st.new_id(), "name": st.unique_name("New automation", [x["name"] for x in self.automations()]),
             "steps": [], "last": None}
        self.automations().append(a)
        self.settings["selected"] = a["id"]
        self._save()
        self._push()

    def on_select(self, payload):
        if self._find((payload or {}).get("id")):
            self.settings["selected"] = payload["id"]
            self.settings.save()
            self._push()

    def on_rename(self, payload):
        payload = payload or {}
        a = self._find(payload.get("id"))
        name = str(payload.get("name") or "").strip()[:st.NAME_MAX]
        if a is None or not name or name == a["name"]:
            return self._push()
        a["name"] = st.unique_name(name, [x["name"] for x in self.automations() if x is not a])
        self._save()
        self._push()

    def on_duplicate(self, payload):
        a = self._find((payload or {}).get("id"))
        if a is None:
            return
        copy_ = copy.deepcopy(a)
        copy_.update(id=st.new_id(), name=st.unique_name(f"{a['name']} copy", [x["name"] for x in self.automations()]),
                     last=None)
        for s in copy_["steps"]:
            s["id"] = st.new_id()
        self.automations().insert(self.automations().index(a) + 1, copy_)
        self.settings["selected"] = copy_["id"]
        self._save()
        self._push()

    def on_delete(self, payload):
        a = self._editable((payload or {}).get("id"))
        if a is None:
            return
        self.automations().remove(a)
        if self.run is not None and self.run.automation["id"] == a["id"]:
            self.run = None
        self._save()
        self._push()

    def on_add_step(self, payload):
        payload = payload or {}
        a = self._editable(payload.get("id"))
        spec = self.catalog().get((payload.get("tool"), payload.get("step")))
        if a is None or spec is None:
            return
        a["steps"].append(st.new_step(spec["tool"], spec))
        self._save()
        self._push()

    def on_remove_step(self, payload):
        payload = payload or {}
        a = self._editable(payload.get("id"))
        s = self._step(a, payload.get("step"))
        if s is not None:
            a["steps"].remove(s)
            self._save()
            self._push()

    def on_move_step(self, payload):
        payload = payload or {}
        a = self._editable(payload.get("id"))
        s = self._step(a, payload.get("step"))
        to = payload.get("to")
        if s is None or not isinstance(to, int):
            return
        a["steps"].remove(s)
        a["steps"].insert(max(0, min(len(a["steps"]), to)), s)
        self._save()
        self._push()

    def on_option(self, payload):
        payload = payload or {}
        a = self._editable(payload.get("id"))
        s = self._step(a, payload.get("step"))
        spec = self.catalog().get((s["tool"], s["step"])) if s else None
        option = next((o for o in spec.get("options", []) if o["key"] == payload.get("key")), None) if spec else None
        if option is None:
            return
        s["options"][option["key"]] = st.clean_value(option, payload.get("value"))
        self._save()
        self._push()

    def on_ask(self, payload):
        payload = payload or {}
        a = self._editable(payload.get("id"))
        s = self._step(a, payload.get("step"))
        spec = self.catalog().get((s["tool"], s["step"])) if s else None
        if spec is None:
            return
        key = payload.get("key")
        asked = [k for k in s["ask"] if k != key] + ([key] if payload.get("on") else [])
        s["ask"] = st.clean_ask(spec, asked)
        self._save()
        self._push()

    def on_browse(self, payload):
        payload = payload or {}
        a = self._editable(payload.get("id"))
        s = self._step(a, payload.get("step"))
        if s is None:
            return
        start = s["options"].get(payload.get("key")) or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, tr("Choose a folder"), start)
        if folder:
            self.on_option({"id": a["id"], "step": s["id"], "key": payload.get("key"), "value": os.path.normpath(folder)})

    # ---------------------------------------------------------- running --

    def on_run(self, payload):
        a = self._find((payload or {}).get("id"))
        if a is None:
            return
        if self.run is not None and self.run.result is None:
            return self.emit("toast", {"text": f"{self.run.automation['name']} is running – wait for it, or stop it."})
        catalog = self.catalog()
        problems = st.problems(a, catalog)
        if problems:
            return self.emit("toast", {"text": problems[0]})
        questions = st.questions(a, catalog)
        if questions:
            self._pending = a["id"]
            return self.emit("questions", {"id": a["id"], "name": a["name"], "questions": questions})
        self._start(a, {})

    def on_browse_answer(self, payload):
        """A folder question's Choose…: the folder, back to the form."""
        payload = payload or {}
        folder = QFileDialog.getExistingDirectory(self, tr("Choose a folder"),
                                                  payload.get("value") or os.path.expanduser("~"))
        if folder:
            self.emit("folder", {"field": payload.get("field"), "value": os.path.normpath(folder)})

    def on_start(self, payload):
        """The questions answered: {id, answers: {step id: {key: value}}}."""
        payload = payload or {}
        a = self._find(payload.get("id"))
        if a is None or self._pending != a["id"]:
            return
        self._pending = None
        catalog = self.catalog()
        raw = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
        answers = {}
        for s in a["steps"]:
            spec = catalog.get((s["tool"], s["step"]))
            given = raw.get(s["id"]) if isinstance(raw.get(s["id"]), dict) else {}
            if spec:
                answers[s["id"]] = {o["key"]: st.clean_value(o, given.get(o["key"]))
                                    for o in spec.get("options", []) if o["key"] in s["ask"]}
        problems = st.problems(a, catalog, answers)
        if problems:
            return self.emit("toast", {"text": problems[0]})
        self._start(a, answers)

    def _start(self, a, answers, start_at=0):
        try:
            self.host.ensure_connected()
        except ResolveConnectionError as exc:
            return self.emit("toast", {"text": str(exc) or "Not connected to Resolve."})
        self._answers = answers
        pages = dict(getattr(self.host, "pages", {}) or {})
        self.run = AutomationRun(copy.deepcopy(a), self.catalog(), pages, answers, start_at, self)
        self.run.changed.connect(self._push)
        self.run.ended.connect(self._ended)
        self.run.start()
        self._tick.start()
        self._push()

    def on_stop(self, _payload=None):
        if self.run is not None:
            self.run.stop()

    def on_resume(self, _payload=None):
        """Run again from the step that failed, with the same answers."""
        r = self.run
        if r is None or r.result is None or r.result[0]:
            return
        a = self._find(r.automation["id"])
        if a is None:
            return
        failed = next((i for i, s in enumerate(r.state) if s["status"] == "failed"), r.current)
        # The steps before it are taken as done: only while they're the same
        # steps, in the same order, ahead of the same step. It can be edited
        # once it stops - a change there means a run from the start.
        ran = r.automation["steps"]
        if (a["steps"][:failed] != ran[:failed] or len(a["steps"]) <= failed
                or a["steps"][failed]["id"] != ran[failed]["id"]):
            return self.emit("toast", {"text": "It's changed since it stopped – run it from the start."})
        problems = st.problems(a, self.catalog(), self._answers)
        if problems:
            return self.emit("toast", {"text": problems[0]})
        self._start(a, self._answers, failed)

    def on_dismiss(self, _payload=None):
        if self.run is not None and self.run.result is not None:
            self.run = None
            self._push()

    def _ended(self, ok, text):
        self._tick.stop()
        r = self.run
        a = self._find(r.automation["id"]) if r is not None else None
        took = r.view()["took"] if r is not None else ""
        if a is not None:
            a["last"] = {"when": time.strftime("%Y-%m-%d %H:%M"), "ok": ok, "text": text, "took": took}
            self._save()
        if ok:
            self.achieve("automations", 1)      # first, so the run's own notification is the one left showing
        self._notify(r.automation["name"] if r is not None else "Automation", ok, text)
        self._push()

    def _notify(self, name, ok, text):
        """A Windows notification from the tray icon - the run's whole point is
        that nobody's watching. Shown even when Buddy is in front."""
        tray = getattr(self.host, "tray_icon", None)
        if tray is None:
            return
        icon = QSystemTrayIcon.MessageIcon.Information if ok else QSystemTrayIcon.MessageIcon.Warning
        title = f"{name} finished" if ok else f"{name} stopped"
        try:
            tray.showMessage(title, tr(text) if ok else text, icon, 10000)
        except RuntimeError:
            pass
