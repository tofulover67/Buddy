"""Buddy.py, the launcher stub: clearing out old extracted versions without
touching one a running Buddy still uses, and putting back the packages a
newer Python doesn't have. The stub runs Buddy as soon as it loads, so only
its functions are loaded here."""

import os
import sys
import tempfile
import unittest

import _paths


def _stub():
    source = (_paths.APP.parent / "Buddy.py").read_text(encoding="utf-8")
    functions = source.split("\ntry:\n    _run()")[0]
    namespace = {"__name__": "buddy_stub"}
    exec(compile(functions, "Buddy.py", "exec"), namespace)
    return namespace


class OldVersionTests(unittest.TestCase):
    def test_old_versions_go_but_one_in_use_and_other_folders_stay(self):
        stub = _stub()
        with tempfile.TemporaryDirectory() as root:
            def version(name):
                path = os.path.join(root, name)
                os.makedirs(os.path.join(path, "buddy"))
                with open(os.path.join(path, "buddy", "main.py"), "w") as f:
                    f.write("# app")
                return path

            current = version("aaaaaaaaaaaaaaaa")
            old = version("bbbbbbbbbbbbbbbb")
            running = version("cccccccccccccccc")
            half_deleted = version("dddddddddddddddd.deleting")
            someone_elses = version("not-a-version")
            held = open(os.path.join(running, ".in_use"), "a")   # a Buddy still running from it
            try:
                stub["_hold"](current)
                stub["_remove_old_versions"](current)
                left = sorted(os.listdir(root))
            finally:
                held.close()
                stub["_in_use_handle"].close()
            self.assertIn(os.path.basename(current), left)
            self.assertIn(os.path.basename(someone_elses), left)
            self.assertNotIn(os.path.basename(old), left)
            self.assertNotIn(os.path.basename(half_deleted), left)
            if os.name == "nt":   # Windows won't rename a folder with a file open in it
                self.assertIn(os.path.basename(running), left)
                self.assertTrue(os.path.isfile(os.path.join(running, "buddy", "main.py")))

    @unittest.skipUnless(os.name != "nt", "the lock is the Mac's (POSIX's) way; Windows refuses the rename")
    def test_a_folder_a_running_buddy_has_locked_stays_on_a_mac(self):
        # A Mac renames and deletes a folder with files open in it, so only the
        # lock the running Buddy holds (its _hold()) keeps its files there.
        running_stub, new_stub = _stub(), _stub()
        with tempfile.TemporaryDirectory() as root:
            running = os.path.join(root, "cccccccccccccccc")
            current = os.path.join(root, "aaaaaaaaaaaaaaaa")
            for path in (running, current):
                os.makedirs(os.path.join(path, "buddy"))
            with open(os.path.join(running, "buddy", "main.py"), "w") as f:
                f.write("# app")
            running_stub["_hold"](running)
            try:
                new_stub["_hold"](current)
                new_stub["_remove_old_versions"](current)
                self.assertTrue(os.path.isfile(os.path.join(running, "buddy", "main.py")))
            finally:
                running_stub["_in_use_handle"].close()
                new_stub["_in_use_handle"].close()
            new_stub["_remove_old_versions"](current)        # closed: nobody runs it now
            self.assertFalse(os.path.exists(running))


REQUIREMENTS = """# a comment
PySide6>=6.10,<7
numpy>=1.26
cryptography>=50.0   # trailing comment
pyobjc-framework-Cocoa>=9; sys_platform == "darwin"
"""


class PackageRepairTests(unittest.TestCase):
    """Resolve switching to a newer Python with none of Buddy's packages."""

    def setUp(self):
        self.stub = _stub()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        with open(os.path.join(self.dir.name, "requirements.txt"), "w", encoding="utf-8") as f:
            f.write(REQUIREMENTS)
        self.stub["LOG_PATH"] = os.path.join(self.dir.name, "launch.log")
        self.stub["_real_python"] = lambda: r"D:\Python314\python.exe"
        self.stub["_can_write"] = lambda folder: True
        self.installed = {"cryptography": "50.1"}
        self.asked, self.ran = [], []
        self.stub["_ask"] = lambda title, text: self.asked.append(text) or self.answer
        self.stub["_run_windows"] = lambda program, args, as_admin: self._install(program, args)
        self.stub["_run_mac"] = lambda command: self._install(command[0], command[1:])
        self.answer = True
        self.fix = True

    def _install(self, program, args):
        self.ran.append([program] + args)
        if self.fix:
            self.installed.update({"PySide6": "6.12.0", "numpy": "2.3", "pyobjc-framework-Cocoa": "11"})
        return 0 if self.fix else 1

    def _missing(self):
        return self.stub["_missing_packages"](os.path.join(self.dir.name, "requirements.txt"),
                                              self.installed.get)

    def _repair(self):
        real = self.stub["_missing_packages"]
        self.stub["_missing_packages"] = lambda path, version_of=None: real(path, self.installed.get)
        self.stub["_repair_packages"](self.dir.name)

    def test_only_packages_with_no_copy_at_all_count_as_missing(self):
        self.installed["numpy"] = "1.0"     # too old still runs Buddy: not this check's business
        expected = ["PySide6>=6.10,<7"] + (["pyobjc-framework-Cocoa>=9"] if sys.platform == "darwin" else [])
        self.assertEqual(self._missing(), expected)

    def test_nothing_missing_asks_nothing(self):
        self.installed.update({"PySide6": "6.12.0", "numpy": "2.3", "pyobjc-framework-Cocoa": "11"})
        self._repair()
        self.assertEqual((self.asked, self.ran), ([], []))

    def test_a_zip_without_the_list_asks_nothing(self):
        os.remove(os.path.join(self.dir.name, "requirements.txt"))
        self.stub["_repair_packages"](self.dir.name)
        self.assertEqual((self.asked, self.ran), ([], []))

    def test_yes_installs_the_list_isolated_into_the_real_python(self):
        self._repair()
        self.assertEqual(len(self.asked), 1)
        self.assertIn("numpy>=1.26", self.asked[0])
        self.assertIn(r"D:\Python314\python.exe", self.asked[0])
        command = self.ran[0]
        self.assertEqual(command[:5], [r"D:\Python314\python.exe", "-I", "-m", "pip", "install"])
        self.assertEqual(command[command.index("-r") + 1], os.path.join(self.dir.name, "requirements.txt"))

    def test_no_says_what_to_run_instead(self):
        self.answer = False
        with self.assertRaises(RuntimeError) as caught:
            self._repair()
        self.assertEqual(self.ran, [])
        self.assertIn(r'"D:\Python314\python.exe" -I -m pip install "PySide6>=6.10,<7" "numpy>=1.26"',
                      str(caught.exception))

    def test_a_failed_install_says_what_is_still_missing(self):
        self.fix = False
        with self.assertRaises(RuntimeError) as caught:
            self._repair()
        self.assertIn("exit code 1", str(caught.exception))
        self.assertIn('"numpy>=1.26"', str(caught.exception))

    def test_no_real_python_sends_to_the_installer(self):
        self.stub["_real_python"] = lambda: None
        with self.assertRaises(RuntimeError) as caught:
            self._repair()
        self.assertEqual((self.asked, self.ran), ([], []))
        self.assertIn("installer", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
