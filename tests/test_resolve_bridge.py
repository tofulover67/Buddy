"""core/resolve_bridge.py's connection handed over by Resolve (the free
Resolve's only one), and core/python_exe.py's real-interpreter lookup for
when Buddy runs under Resolve's script host."""

import os
import sys
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from core import python_exe, resolve_bridge


class FakeResolve:
    def __init__(self, version="21.0.4.5"):
        self.version = version

    def GetVersionString(self):
        if isinstance(self.version, Exception):
            raise self.version
        return self.version

    def GetProjectManager(self):
        return "project manager"


class HostResolveTests(unittest.TestCase):
    def tearDown(self):
        resolve_bridge._host_resolve = None
        resolve_bridge._launched_by = None

    def test_connects_through_the_handed_over_object(self):
        fake = FakeResolve()
        resolve_bridge.set_host_resolve(fake)
        with mock.patch.object(resolve_bridge.subprocess, "run") as run:
            controller = resolve_bridge.connect()
        run.assert_not_called()   # no probe subprocess: it couldn't connect anyway
        self.assertIs(controller.resolve, fake)
        self.assertEqual(controller.project_manager, "project manager")

    def test_not_reachable_once_resolve_has_quit(self):
        resolve_bridge.set_host_resolve(FakeResolve())
        with mock.patch.object(resolve_bridge.os, "getppid", return_value=1):
            self.assertFalse(resolve_bridge.probe_resolve_reachable())
            with self.assertRaises(resolve_bridge.ResolveConnectionError):
                resolve_bridge.connect()

    def test_not_reachable_when_the_object_stops_answering(self):
        resolve_bridge.set_host_resolve(FakeResolve(version=RuntimeError("gone")))
        self.assertFalse(resolve_bridge.probe_resolve_reachable())
        resolve_bridge.set_host_resolve(FakeResolve(version=None))
        self.assertFalse(resolve_bridge.probe_resolve_reachable())

    def test_without_one_the_probe_subprocess_decides(self):
        with mock.patch.object(resolve_bridge.subprocess, "run") as run:
            run.return_value.returncode = 1
            self.assertFalse(resolve_bridge.probe_resolve_reachable())
            run.assert_called_once()


class FakeProject:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class FakeProjectManager:
    def __init__(self, project):
        self.project = project

    def GetCurrentProject(self):
        return self.project


class TimeTrackerPollTests(unittest.TestCase):
    """pages/time_tracker/resolve_bridge.py polls through the handed-over
    connection when there is one: the free Resolve won't let its worker
    subprocess connect."""

    def setUp(self):
        from PySide6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])

    def tearDown(self):
        resolve_bridge._host_resolve = None
        resolve_bridge._launched_by = None

    def _host(self, project_name):
        host = FakeResolve()
        project = FakeProject(project_name) if project_name is not None else None
        host.GetProjectManager = lambda: FakeProjectManager(project)
        return host

    def test_project_name_rules(self):
        from pages.time_tracker.resolve_bridge import current_project_name
        self.assertEqual(current_project_name(self._host("Emotion Ads")), "Emotion Ads")
        self.assertIsNone(current_project_name(self._host("Untitled Project")))   # the Project Manager screen
        self.assertIsNone(current_project_name(self._host(None)))

    def test_polls_through_the_host_without_a_subprocess(self):
        from pages.time_tracker import resolve_bridge as tt
        resolve_bridge.set_host_resolve(self._host("Emotion Ads"))
        bridge = tt.ResolveBridge()
        results = []
        bridge.project_detected.connect(lambda name, completed: results.append((name, completed)))
        with mock.patch.object(tt.threading, "Thread") as thread:
            bridge.poll()
            self.app.processEvents()
        thread.assert_not_called()
        self.assertEqual(results, [("Emotion Ads", True)])

    def test_falls_back_to_the_worker_once_resolve_has_quit(self):
        from pages.time_tracker import resolve_bridge as tt
        resolve_bridge.set_host_resolve(self._host("Emotion Ads"))
        bridge = tt.ResolveBridge()
        with mock.patch.object(resolve_bridge.os, "getppid", return_value=1), \
                mock.patch.object(tt.threading, "Thread") as thread:
            bridge.poll()
        thread.assert_called_once()


class StandalonePythonTests(unittest.TestCase):
    def test_a_python_binary_is_used_as_is(self):
        with mock.patch.object(sys, "executable", "/usr/local/bin/python3.11"):
            self.assertEqual(python_exe.standalone_python(), "/usr/local/bin/python3.11")

    def test_under_a_script_host_finds_the_embedded_python(self):
        with tempfile.TemporaryDirectory() as prefix:
            version = f"{sys.version_info[0]}.{sys.version_info[1]}"
            os.makedirs(os.path.join(prefix, "bin"))
            real = os.path.join(prefix, "bin", f"python{version}")
            open(real, "w").close()
            with mock.patch.object(sys, "executable", "/Applications/Resolve.app/fuscript"), \
                    mock.patch.object(sys, "exec_prefix", prefix):
                self.assertEqual(python_exe.standalone_python(), real)

    def test_falls_back_to_sys_executable(self):
        with tempfile.TemporaryDirectory() as prefix, \
                mock.patch.object(sys, "executable", "/Applications/Resolve.app/fuscript"), \
                mock.patch.object(sys, "exec_prefix", prefix):
            self.assertEqual(python_exe.standalone_python(), "/Applications/Resolve.app/fuscript")


if __name__ == "__main__":
    unittest.main()
