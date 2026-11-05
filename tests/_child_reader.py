"""The Resolve reader's process (core/resolve_reader_child.py), stood in for
in a page's test, so its busy_job.run() goes the way it does in Buddy:

    reader = ChildReader(controller, jobs)
    page._calls.reader = reader
    page.on_relink_all(None)            # sent - nothing done yet
    reader.run()                        # answered

A job is answered by the child's own answer() - its function called with
the controller and the args as JSON carries them, its answer back as JSON,
an error as a ReadError of its class's name - and only when the test says,
as the pipe answers later. `jobs` are the JOBS lines the pages need, put in
for the test. load_without_qt(jobs) says whether their modules load in a
process with no Qt, as the reader's has none."""

import json
import os
import subprocess
import sys
from unittest import mock

import _paths  # noqa: F401
from core import resolve_reader_child
from core.resolve_reader import ReadError


class ChildReader:
    def __init__(self, controller, jobs):
        self.controller, self.jobs = controller, dict(jobs)
        self.asked = []             # (name, args) in the order sent
        self.ends = set()           # jobs the process stops in (done(None, ReadError(..., "Ended")))
        self._waiting = []

    def available(self):
        return True

    def ask(self, name, args, done):
        args = json.loads(json.dumps(args or {}))   # what the pipe carries
        self.asked.append((name, args))
        self._waiting.append((name, args, done))
        return True

    def run(self):
        """Answers every job sent - and those sent from the answers."""
        while self._waiting:
            name, args, done = self._waiting.pop(0)
            if name in self.ends:
                done(None, ReadError("The Resolve reader stopped.", "Ended"))
                continue
            with mock.patch.dict(resolve_reader_child.JOBS, self.jobs):
                reply = resolve_reader_child.answer({"id": 1, "do": name, "args": args}, self.controller)
            reply = json.loads(json.dumps(reply, default=resolve_reader_child._plain))
            if reply["ok"]:
                done(reply["result"], None)
            else:
                done(None, ReadError(reply["error"], reply["type"]))


PROBE = r"""
import importlib, json, sys
sys.path.insert(0, sys.argv[1])
class NoQt:
    def find_spec(self, name, path=None, target=None):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ImportError("no Qt in the reader's process")
sys.meta_path.insert(0, NoQt())
bad = []
for name, (module, function) in sorted(json.loads(sys.argv[2]).items()):
    try:
        if not callable(getattr(importlib.import_module(module), function)):
            bad.append(f"{name}: {function} isn't a function")
    except Exception as exc:
        bad.append(f"{name}: {type(exc).__name__}: {exc}")
print("\n".join(bad))
"""


def load_without_qt(jobs):
    """"" when every job's function loads with PySide6 out of reach; else
    what didn't."""
    app = os.path.dirname(os.path.dirname(os.path.abspath(resolve_reader_child.__file__)))
    run = subprocess.run([sys.executable, "-c", PROBE, app, json.dumps(jobs)], capture_output=True, text=True,
                         timeout=120, env=dict(os.environ, BUDDY_RESOLVE_READER="off"))
    return (run.stdout + (run.stderr[-2000:] if run.returncode else "")).strip()
