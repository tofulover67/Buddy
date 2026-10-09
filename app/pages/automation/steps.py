#!/usr/bin/env python3
"""
Automations as data: what a step is, what an automation holds, and the
checks on both. No Qt, so it's unit-tested.

A step comes from a tool (ToolPage.automation_steps(), pages/base.py):

    {"id", "label", "about", "options": [option, ...]}

and an option is

    {"key", "label", "kind", "default", ["choices": [{id, label}]],
     ["ask": True]  - it can be asked when the automation starts,
     ["hint"], ["required": True] - the step can't run without it}

kind: "select", "check", "folder", "text" or "textarea".

An automation, as saved (~/.automation/settings.json):

    {"id", "name", "steps": [{"id", "tool", "step", "options": {...},
     "ask": [keys asked when it runs]}], "last": {when, ok, text, took}}
"""

from __future__ import annotations

import os
import secrets

KINDS = ("select", "check", "folder", "text", "textarea")
NAME_MAX = 80


def new_id() -> str:
    return secrets.token_hex(4)


def default_options(spec: dict) -> dict:
    return {o["key"]: o.get("default", False if o["kind"] == "check" else "") for o in spec.get("options", [])}


def clean_value(option: dict, value):
    """One option's value as the step takes it - its default if it isn't one."""
    kind = option["kind"]
    default = option.get("default", False if kind == "check" else "")
    if kind == "check":
        return bool(value) if isinstance(value, (bool, int)) else bool(default)
    if kind == "select":
        ids = [c["id"] for c in option.get("choices", [])]
        return value if value in ids else (default if default in ids else (ids[0] if ids else ""))
    if not isinstance(value, str):
        return default
    if kind == "folder":
        value = value.strip().strip('"')
        return os.path.normpath(value) if value else ""
    return value[:20000] if kind == "textarea" else value.strip()[:500]


def clean_options(spec: dict, raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {o["key"]: clean_value(o, raw.get(o["key"], o.get("default"))) for o in spec.get("options", [])}


def clean_ask(spec: dict, raw) -> list[str]:
    askable = {o["key"] for o in spec.get("options", []) if o.get("ask")}
    return [k for k in dict.fromkeys(raw if isinstance(raw, list) else []) if k in askable]


def new_step(tool: str, spec: dict) -> dict:
    return {"id": new_id(), "tool": tool, "step": spec["id"], "options": default_options(spec), "ask": []}


def clean_automation(raw, catalog: dict) -> dict | None:
    """A saved automation, checked: steps of tools or steps no longer there
    are kept as they were (the tool may come back), but marked "missing"
    by the page. catalog: {(tool, step): spec}. None if it isn't one."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()[:NAME_MAX] or "Automation"
    steps = []
    for s in raw.get("steps") or []:
        if not isinstance(s, dict) or not isinstance(s.get("tool"), str) or not isinstance(s.get("step"), str):
            continue
        spec = catalog.get((s["tool"], s["step"]))
        steps.append({"id": s["id"] if isinstance(s.get("id"), str) else new_id(), "tool": s["tool"],
                      "step": s["step"],
                      "options": clean_options(spec, s.get("options")) if spec else dict(s.get("options") or {}),
                      "ask": clean_ask(spec, s.get("ask")) if spec else list(s.get("ask") or [])})
    last = raw.get("last") if isinstance(raw.get("last"), dict) else None
    return {"id": raw["id"] if isinstance(raw.get("id"), str) and raw["id"] else new_id(), "name": name,
            "steps": steps, "last": last}


def unique_name(name: str, taken) -> str:
    """name, or "name 2", "name 3"… - whichever isn't taken."""
    taken = {t.lower() for t in taken}
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} {n}".lower() in taken:
        n += 1
    return f"{name} {n}"


def questions(automation: dict, catalog: dict) -> list[dict]:
    """What to ask before it starts: [{step (index), id, key, label, kind,
    choices, value, step_label}] - each step's options marked Ask."""
    out = []
    for i, s in enumerate(automation["steps"]):
        spec = catalog.get((s["tool"], s["step"]))
        if not spec:
            continue
        for o in spec.get("options", []):
            if o["key"] in s.get("ask", []):
                out.append({"step": i, "id": s["id"], "key": o["key"], "label": o["label"], "kind": o["kind"],
                            "choices": o.get("choices", []), "value": s["options"].get(o["key"]),
                            "step_label": f"{i + 1}. {spec['label']}"})
    return out


def problems(automation: dict, catalog: dict, answers=None) -> list[str]:
    """Why it can't start, a line each ([] when it can): no steps, a tool
    that isn't there, a required option left empty. answers: what was
    asked when it starts - None before then (an option asked for isn't a
    problem yet)."""
    out = []
    if not automation["steps"]:
        out.append("Add a step first.")
    for i, s in enumerate(automation["steps"]):
        spec = catalog.get((s["tool"], s["step"]))
        if spec is None:
            out.append(f"Step {i + 1} is from a tool this Buddy doesn't have – remove it.")
            continue
        for o in spec.get("options", []):
            if not o.get("required"):
                continue
            if o["key"] in s.get("ask", []):
                if answers is None:
                    continue
                value = answers.get(s["id"], {}).get(o["key"])
            else:
                value = s["options"].get(o["key"])
            if not value:
                what = "choose the folder" if o["kind"] == "folder" else f"fill in {o['label']}"
                out.append(f"Step {i + 1} ({spec['label']}): {what} – or tick Ask when it runs."
                           if o.get("ask") and answers is None else f"Step {i + 1} ({spec['label']}): {what}.")
            elif o["kind"] == "folder" and not os.path.isdir(value):
                out.append(f"Step {i + 1} ({spec['label']}): {value} isn't there.")
    return out


def options_for_run(step: dict, spec: dict, answers=None) -> dict:
    """The options a step runs with: what's saved, and what was asked for."""
    asked = (answers or {}).get(step["id"], {})
    merged = {**step["options"], **{k: v for k, v in asked.items() if k in step.get("ask", [])}}
    return clean_options(spec, merged)


def took(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s" if m else f"{s}s"
