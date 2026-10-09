"""BUDDY_HOME (core/tool_dirs.py): set, everything Buddy keeps - ~/.buddy,
the tools' folders, automatic backups - is under that folder instead of the
home directory; unset or empty, the home directory as before. The engines'
own modules, which can't import core, read it the same way."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _paths  # noqa: F401
from core import backup, tool_dirs

APP = Path(__file__).resolve().parents[1] / "app"


class BuddyHomeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = os.path.abspath(tmp.name)

    def test_home_is_buddy_home_when_set(self):
        with mock.patch.dict(os.environ, {"BUDDY_HOME": self.folder}):
            self.assertEqual(tool_dirs.home(), self.folder)
            self.assertEqual(tool_dirs.buddy_dir(), os.path.join(self.folder, ".buddy"))
            self.assertEqual(tool_dirs.tools_dir(), os.path.join(self.folder, ".buddy", "tools"))

    def test_tilde_and_variables_are_expanded(self):
        with mock.patch.dict(os.environ, {"BUDDY_HOME": "~/buddy-try"}):
            self.assertEqual(tool_dirs.home(), os.path.abspath(os.path.join(os.path.expanduser("~"), "buddy-try")))
        with mock.patch.dict(os.environ, {"BUDDY_HOME_TEST_ROOT": self.folder,
                                          "BUDDY_HOME": os.path.join("%BUDDY_HOME_TEST_ROOT%", "x")
                                          if os.name == "nt" else os.path.join("$BUDDY_HOME_TEST_ROOT", "x")}):
            self.assertEqual(tool_dirs.home(), os.path.join(self.folder, "x"))

    def test_unset_or_empty_is_the_home_directory(self):
        for value in (None, "", "   "):
            with self.subTest(value=value), mock.patch.dict(os.environ):
                os.environ.pop("BUDDY_HOME", None)
                if value is not None:
                    os.environ["BUDDY_HOME"] = value
                self.assertEqual(tool_dirs.home(), os.path.expanduser("~"))
                self.assertEqual(tool_dirs.buddy_dir(), os.path.join(os.path.expanduser("~"), ".buddy"))

    def test_backups_follow_it(self):
        with mock.patch.dict(os.environ, {"BUDDY_HOME": self.folder}):
            self.assertEqual(backup.home(), Path(self.folder))
            self.assertEqual(backup.buddy_dir(), Path(self.folder) / ".buddy")
            self.assertEqual(backup.default_folder(), Path(self.folder) / "Documents" / "Buddy backups")

    def test_the_engines_modules_read_it_too(self):
        # gpu.py and transcribe's env_setup.py run in the engines' own Python,
        # imported plainly from their folders; a fresh process settles them.
        probe = ("import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; "
                 "import gpu, env_setup; print(gpu.CACHE); print(env_setup.ROOT)")
        env = {**os.environ, "BUDDY_HOME": self.folder}
        out = subprocess.run([sys.executable, "-c", probe, str(APP / "pages" / "generative"),
                              str(APP / "pages" / "transcribe")],
                             env=env, capture_output=True, text=True, timeout=60, check=True).stdout.split("\n")
        self.assertEqual(out[0], os.path.join(self.folder, ".buddy", "generative", "gpu"))
        self.assertEqual(out[1], str(Path(self.folder) / ".buddy" / "transcribe"))


if __name__ == "__main__":
    unittest.main()
