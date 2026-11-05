"""Every job the Resolve reader's process runs (core/resolve_reader_child.py
JOBS) is there and can be loaded in that process - which has no Qt: a job
module that pulled PySide6 in would fail there, and the page would fall
back to doing the work on Buddy's UI thread, frozen meanwhile."""

import os
import subprocess
import sys
import unittest

import _paths  # noqa: F401
from core import resolve_reader_child

APP = os.path.dirname(os.path.dirname(os.path.abspath(resolve_reader_child.__file__)))

PROBE = r"""
import importlib, sys
sys.path.insert(0, sys.argv[1])
class NoQt:
    def find_spec(self, name, path=None, target=None):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ImportError("no Qt in the reader's process")
sys.meta_path.insert(0, NoQt())
from core.resolve_reader_child import JOBS
bad = []
for name, (module, function) in sorted(JOBS.items()):
    try:
        if not callable(getattr(importlib.import_module(module), function)):
            bad.append(f"{name}: {function} isn't a function")
    except Exception as exc:
        bad.append(f"{name}: {type(exc).__name__}: {exc}")
print("\n".join(bad))
"""


class JobTests(unittest.TestCase):
    def test_every_job_loads_without_qt(self):
        run = subprocess.run([sys.executable, "-c", PROBE, APP], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, BUDDY_RESOLVE_READER="off"))
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        self.assertEqual(run.stdout.strip(), "", run.stdout)

    def test_the_podcast_jobs_are_there(self):
        for name in ("podswitch.read", "podswitch.build", "podsync.read", "podsync.build", "podcast_text.build"):
            self.assertIn(name, resolve_reader_child.JOBS)


if __name__ == "__main__":
    unittest.main()
