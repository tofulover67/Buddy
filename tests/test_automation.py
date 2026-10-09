"""The Automation tab (pages/automation/): automations as data (steps.py),
running them step after step (runner.py) - waiting on slow steps, stopping
at a failure, Stop, Resume - the page (asking what's marked Ask before it
starts, saving, the notification), and the tools' steps' hooks (Project
Setup, Subtitles, Audit). Stand-in tools and settings in memory: never
Resolve, the real settings or ~/.automation."""

import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.automation import steps as st

# A folder as it's typed on this computer.
A_FOLDER = "C:\\a\\b" if os.name == "nt" else "/a/b"

try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    import core.shell_window as shell_window
    from pages.automation.page import AutomationPage
    from pages.automation.runner import AutomationRun
    from pages.base import ToolPage
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

FOLDER = {"key": "folder", "label": "Folder", "kind": "folder", "required": True, "ask": True, "default": ""}
SPEC = {"id": "import", "label": "Import a folder", "options": [
    FOLDER, {"key": "to_master", "label": "Master", "kind": "check", "default": False},
    {"key": "size", "label": "Size", "kind": "select", "default": "half",
     "choices": [{"id": "half", "label": "Half"}, {"id": "full", "label": "Full"}]},
    {"key": "name", "label": "Name", "kind": "text", "default": "", "ask": True}]}
CATALOG = {("tool", "import"): SPEC}


class Mem(dict):
    @property
    def values(self):
        return self

    def save(self):
        pass


class StepsTests(unittest.TestCase):
    def test_values_are_checked_against_each_option(self):
        self.assertEqual(st.clean_options(SPEC, {"to_master": "yes", "size": "huge", "folder": 3, "name": "  x "}),
                         {"folder": "", "to_master": False, "size": "half", "name": "x"})
        self.assertEqual(st.clean_options(SPEC, {"to_master": True, "size": "full", "folder": f' "{A_FOLDER}{os.sep}" '}),
                         {"folder": A_FOLDER, "to_master": True, "size": "full", "name": ""})
        self.assertEqual(st.clean_ask(SPEC, ["folder", "to_master", "folder", 5]), ["folder"])

    def test_a_saved_automation_cleaned(self):
        raw = {"id": "a1", "name": "  Ingest  ", "steps": [
            {"id": "s1", "tool": "tool", "step": "import", "options": {"size": "nope"}, "ask": ["folder", "size"]},
            {"tool": "gone", "step": "x", "options": {"k": 1}}, "junk", {"tool": 3}]}
        a = st.clean_automation(raw, CATALOG)
        self.assertEqual(a["name"], "Ingest")
        self.assertEqual([s["tool"] for s in a["steps"]], ["tool", "gone"])     # a missing tool's step kept
        self.assertEqual(a["steps"][0]["options"]["size"], "half")
        self.assertEqual(a["steps"][0]["ask"], ["folder"])
        self.assertTrue(a["steps"][1]["id"])
        self.assertIsNone(st.clean_automation("x", CATALOG))

    def test_questions_problems_and_the_options_a_step_runs_with(self):
        a = st.clean_automation({"name": "A", "steps": [{"id": "s1", "tool": "tool", "step": "import",
                                                         "ask": ["folder", "name"]}]}, CATALOG)
        self.assertEqual([(q["key"], q["step_label"]) for q in st.questions(a, CATALOG)],
                         [("folder", "1. Import a folder"), ("name", "1. Import a folder")])
        self.assertEqual(st.problems(a, CATALOG), [])                        # asked: not a problem yet
        self.assertIn("choose the folder", st.problems(a, CATALOG, {"s1": {"folder": ""}})[0])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(st.problems(a, CATALOG, {"s1": {"folder": tmp}}), [])
            self.assertIn("isn't there", st.problems(a, CATALOG, {"s1": {"folder": tmp + "x"}})[0])
            opts = st.options_for_run(a["steps"][0], SPEC, {"s1": {"folder": tmp, "to_master": True}})
        self.assertEqual((opts["folder"], opts["to_master"]), (os.path.normpath(tmp), False))   # only asked keys
        a["steps"][0]["ask"] = []
        self.assertIn("or tick Ask when it runs", st.problems(a, CATALOG)[0])
        self.assertEqual(st.problems({"steps": []}, CATALOG), ["Add a step first."])
        self.assertIn("doesn't have", st.problems({"steps": [{"tool": "x", "step": "y"}]}, CATALOG)[0])

    def test_names_and_times(self):
        self.assertEqual(st.unique_name("Ingest", ["ingest", "Ingest 2"]), "Ingest 3")
        self.assertEqual(st.unique_name("New", []), "New")
        self.assertEqual((st.took(42), st.took(872), st.took(3725)), ("42s", "14m 32s", "1h 02m"))


def _wait(condition, ms=5000):
    loop = QEventLoop()
    for _ in range(ms // 10):
        if condition():
            return True
        QTimer.singleShot(10, loop.quit)
        loop.exec()
    return condition()


if HAVE_QT:
    class _Tool(ToolPage):
        """A stand-in tool with a step of each kind."""
        tool_id, display_name, category = "tool", "Tool", "Tools"

        def build_ui(self):
            QVBoxLayout(self).addWidget(QWidget())
            self.ran = []
            self.stopped = False

        def automation_steps(self):
            return [{"id": i, "label": i.title(), "about": "", "options": [dict(FOLDER, required=False)]}
                    for i in ("quick", "slow", "nope", "boom")]

        def run_automation_step(self, step_id, options, run):
            self.ran.append((step_id, options))
            if step_id == "quick":
                run.log("did it", "info")
                return run.done("Quick done.")
            if step_id == "nope":
                return run.fail("It went wrong.")
            if step_id == "boom":
                raise RuntimeError("a bug")
            run.progress("Half way", 50)
            run.on_stop = lambda: (setattr(self, "stopped", True), run.fail("Stopped."))
            QTimer.singleShot(60, lambda: run.done("Slow done."))


def automation(*names):
    return {"id": "a1", "name": "Test", "last": None,
            "steps": [{"id": f"s{i}", "tool": "tool", "step": n, "options": {"folder": ""}, "ask": []}
                      for i, n in enumerate(names)]}


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tool = _Tool.__new__(_Tool)
        QWidget.__init__(self.tool)
        self.tool.build_ui()
        self.catalog = {("tool", s["id"]): s for s in self.tool.automation_steps()}

    def run_of(self, *names, start_at=0):
        run = AutomationRun(automation(*names), self.catalog, {"tool": self.tool}, {}, start_at)
        ended = []
        run.ended.connect(lambda ok, text: ended.append((ok, text)))
        run.start()
        return run, ended

    def test_one_after_another_waiting_on_a_slow_one(self):
        run, ended = self.run_of("quick", "slow", "quick")
        self.assertTrue(_wait(lambda: ended))
        self.assertTrue(ended[0][0])
        self.assertIn("Finished in", ended[0][1])
        self.assertEqual([s["status"] for s in run.state], ["done", "done", "done"])
        self.assertEqual([r[0] for r in self.tool.ran], ["quick", "slow", "quick"])
        self.assertEqual(run.state[1]["text"], "Slow done.")
        view = run.view()
        self.assertFalse(view["running"])
        self.assertIn("did it", [line["text"] for line in view["log"]])

    def test_a_failure_stops_the_run_there_and_resume_starts_from_it(self):
        run, ended = self.run_of("quick", "nope", "quick")
        self.assertTrue(_wait(lambda: ended))
        self.assertEqual(ended[0], (False, "Stopped at step 2 (Nope): It went wrong."))
        self.assertEqual([s["status"] for s in run.state], ["done", "failed", "not_run"])
        self.tool.ran.clear()
        again, ended = self.run_of("quick", "quick", "quick", start_at=1)
        self.assertTrue(_wait(lambda: ended))
        self.assertEqual(len(self.tool.ran), 2)
        self.assertEqual([s["status"] for s in again.state], ["skipped", "done", "done"])

    def test_a_step_that_raises_fails_saying_what(self):
        run, ended = self.run_of("boom", "quick")
        self.assertTrue(_wait(lambda: ended))
        self.assertIn("RuntimeError: a bug", ended[0][1])
        self.assertEqual(len(self.tool.ran), 1)

    def test_stop_asks_the_step_running_to_stop(self):
        run, ended = self.run_of("slow", "quick")
        self.assertTrue(_wait(lambda: run.state[0]["status"] == "running" and run.state[0]["pct"] == 50))
        run.stop()
        self.assertTrue(_wait(lambda: ended))
        self.assertTrue(self.tool.stopped)
        self.assertFalse(ended[0][0])
        self.assertEqual(len(self.tool.ran), 1)                     # nothing after it

    def test_a_step_with_no_tool_fails(self):
        run = AutomationRun(automation("quick"), self.catalog, {}, {})
        ended = []
        run.ended.connect(lambda ok, text: ended.append((ok, text)))
        run.start()
        self.assertTrue(_wait(lambda: ended))
        self.assertIn("isn't in this Buddy", ended[0][1])


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.store = {}
        for p in [mock.patch.object(shell_window, "SharedSettings", lambda: Mem(theme="Resolve")),
                  mock.patch.object(shell_window, "ToolSettings",
                                    lambda tool_id, defaults=None: self.store.setdefault(tool_id, Mem(defaults or {}))),
                  mock.patch.object(shell_window, "resolve_connect", side_effect=RuntimeError("no Resolve")),
                  mock.patch.object(shell_window.AnnouncementChecker, "start", lambda self: None),
                  mock.patch.object(shell_window.QSystemTrayIcon, "isSystemTrayAvailable", return_value=False)]:
            p.start()
            self.addCleanup(p.stop)
        self.win = shell_window.ShellWindow(self.app, [("Tools", _Tool), ("Automation", AutomationPage)])
        self.addCleanup(self.win.deleteLater)
        self.addCleanup(self.win._shutdown_connect_worker)
        self.win.ensure_connected = lambda: object()
        self.win.tray_icon = mock.Mock()
        self.page = self.win.pages["automation"]
        self.page.emit = mock.Mock()

    def emitted(self, name):
        return [c.args[1] for c in self.page.emit.call_args_list if c.args[0] == name]

    def make(self, *names):
        self.page.on_new()
        a = self.page.automations()[-1]
        for n in names:
            self.page.on_add_step({"id": a["id"], "tool": "tool", "step": n})
        return a

    def test_building_one(self):
        a = self.make("quick", "slow", "nope")
        self.page.on_rename({"id": a["id"], "name": "Ingest"})
        self.assertEqual(a["name"], "Ingest")
        first, second, third = a["steps"]
        self.page.on_move_step({"id": a["id"], "step": third["id"], "to": 0})
        self.assertEqual([s["step"] for s in a["steps"]], ["nope", "quick", "slow"])
        self.page.on_remove_step({"id": a["id"], "step": third["id"]})
        self.page.on_option({"id": a["id"], "step": first["id"], "key": "folder", "value": A_FOLDER + os.sep})
        self.assertEqual(first["options"]["folder"], A_FOLDER)
        self.page.on_ask({"id": a["id"], "step": first["id"], "key": "folder", "on": True})
        self.assertEqual(first["ask"], ["folder"])
        self.page.on_duplicate({"id": a["id"]})
        names = [x["name"] for x in self.page.automations()]
        self.assertEqual(names, ["Ingest", "Ingest copy"])
        self.assertNotEqual(self.page.automations()[1]["steps"][0]["id"], first["id"])
        saved = self.store["automation"]["automations"]
        self.assertEqual([x["name"] for x in saved], names)          # saved as it goes
        state = self.emitted("state")[-1]
        self.assertEqual(state["tools"][0]["label"], "Tool")
        self.assertEqual([s["label"] for s in state["automations"][0]["steps"]], ["Quick", "Slow"])

    def test_it_asks_first_then_runs_saves_and_notifies(self):
        a = self.make("quick", "slow")
        self.page.on_ask({"id": a["id"], "step": a["steps"][0]["id"], "key": "folder", "on": True})
        self.page.on_run({"id": a["id"]})
        questions = self.emitted("questions")[-1]
        self.assertEqual([q["key"] for q in questions["questions"]], ["folder"])
        self.assertIsNone(self.page.run)                             # nothing until they answer
        with tempfile.TemporaryDirectory() as tmp:
            self.page.on_start({"id": a["id"], "answers": {a["steps"][0]["id"]: {"folder": tmp}}})
            self.assertTrue(_wait(lambda: self.page.run is not None and self.page.run.result is not None))
        tool = self.win.pages["tool"]
        self.assertEqual(tool.ran[0][1]["folder"], os.path.normpath(tmp))
        self.assertTrue(self.page.run.result[0])
        self.assertTrue(a["last"]["ok"])
        title, text = self.win.tray_icon.showMessage.call_args.args[:2]
        self.assertEqual(title, f"{a['name']} finished")
        self.assertIn("Finished in", text)
        self.assertIsNone(self.page.working_on())

    def test_while_it_runs_it_cant_be_changed_or_run_twice(self):
        a = self.make("slow")
        self.page.on_run({"id": a["id"]})
        self.assertEqual(self.page.working_on(), f"Running {a['name']}")
        self.page.on_add_step({"id": a["id"], "tool": "tool", "step": "quick"})
        self.assertEqual(len(a["steps"]), 1)
        self.page.on_run({"id": a["id"]})
        self.assertIn("is running", self.emitted("toast")[-1]["text"])
        self.assertTrue(_wait(lambda: self.page.run.result is not None))

    def test_a_failed_run_notifies_and_resumes(self):
        a = self.make("quick", "nope")
        self.page.on_run({"id": a["id"]})
        self.assertTrue(_wait(lambda: self.page.run.result is not None))
        self.assertEqual(self.win.tray_icon.showMessage.call_args.args[0], f"{a['name']} stopped")
        self.assertFalse(a["last"]["ok"])
        tool = self.win.pages["tool"]
        tool.ran.clear()
        self.page.on_resume()
        self.assertTrue(_wait(lambda: self.page.run.result is not None))
        self.assertEqual([r[0] for r in tool.ran], ["nope"])          # from the step that stopped
        self.page.on_dismiss()
        self.assertIsNone(self.page.run)

    def test_one_it_cant_run_says_why(self):
        a = self.make()
        self.page.on_run({"id": a["id"]})
        self.assertEqual(self.emitted("toast")[-1]["text"], "Add a step first.")
        self.assertIsNone(self.page.run)


# ------------------------------------------------------- the tools' hooks --


class _Run:
    def __init__(self):
        self.lines, self.result, self.on_stop, self.pcts = [], None, None, []

    def log(self, text, kind="info"):
        self.lines.append((text, kind))

    def progress(self, text, pct=None):
        self.pcts.append(pct)

    def done(self, summary=""):
        self.result = (True, summary)

    def fail(self, message):
        self.result = (False, message)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ProjectSetupStepTests(unittest.TestCase):
    """The mixin over a stand-in page whose handlers log as the real ones do."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        from pages.project_setup.automation import ProjectSetupAutomationMixin

        class Page(ProjectSetupAutomationMixin):
            def __init__(page):
                page.data_mgr = mock.Mock(settings={"bin_list": "Mine"})
                page.proxy_options = {"scope": "selection", "resolution": "half", "codec": "h264", "recursive": False}
                page.sync_options = {"method": "timecode", "sync_audio": True}
                page._slots = {"main": mock.Mock(job=None), "proxy": mock.Mock(job=None)}
                page._sync_busy = False
                page._auto = None
                page.import_folder = "D:\\before"
                page.seen = {}

            def ensure_connected(page):
                return object()

            def _log(page, tab, text, kind="info"):
                if page._auto is not None:
                    page._auto_log(text, kind)

            def _alert(page, title, text):
                return page._auto_alert(title, text)

            def _scan(page):
                pass

            def _push_state(page):
                pass

            def on_create_bins(page, _p):
                page.seen["bins"] = page.data_mgr.settings["bin_list"]
                page._log("bins", "Created 3 bins in the Media Pool.", "success")

            def on_import_folder(page, _p):
                page.seen["import"] = (page.import_folder, page.data_mgr.settings.get("import_to_master"))
                page._alert("No media found", "Nothing under it was a media file Resolve could import.")

            def on_assemble(page, _p):
                page.seen["sync"] = dict(page.sync_options)
                page._auto_job_started("main")

            def on_cancel_job(page, payload):
                page._auto["stopped"] = True
                page._auto_job_ended(payload["slot"])

        self.page = Page()

    def test_a_step_done_straight_away_with_its_options_then_the_tabs_put_back(self):
        run = _Run()
        self.page.run_automation_step("bins", {"bin_list": "A\n>B"}, run)
        self.assertEqual(self.page.seen["bins"], "A\n>B")
        self.assertEqual(self.page.data_mgr.settings["bin_list"], "Mine")
        self.assertEqual(run.result, (True, "Created 3 bins in the Media Pool."))
        self.assertIsNone(self.page._auto)

    def test_an_alert_is_the_step_failing(self):
        run = _Run()
        self.page.run_automation_step("import", {"folder": "E:\\Card", "to_master": True}, run)
        self.assertEqual(self.page.seen["import"], ("E:\\Card", True))
        self.assertEqual(self.page.import_folder, "D:\\before")
        self.assertNotIn("import_to_master", self.page.data_mgr.settings)
        self.assertEqual(run.result, (False, "Nothing under it was a media file Resolve could import."))

    def test_a_job_is_waited_for_and_stop_cancels_it(self):
        run = _Run()
        self.page.run_automation_step("sync", {"method": "waveform", "sync_audio": False}, run)
        self.assertEqual(self.page.seen["sync"]["method"], "waveform")
        self.assertEqual(self.page.sync_options["method"], "timecode")
        self.assertIsNone(run.result)                                # the job's still going
        self.page._auto_job_progress("main", "Reading audio", 2, 4)
        self.assertEqual(run.pcts[-1], 50)
        self.page._auto_log("Synced 12 clips onto 'Day 1 (Synced)'.", "success")
        self.page._auto_job_ended("main")
        self.assertTrue(_wait(lambda: run.result is not None))
        self.assertEqual(run.result, (True, "Synced 12 clips onto 'Day 1 (Synced)'."))
        run2 = _Run()
        self.page.run_automation_step("sync", {"method": "timecode", "sync_audio": True}, run2)
        run2.on_stop()
        self.assertTrue(_wait(lambda: run2.result is not None))
        self.assertEqual(run2.result, (False, "Stopped."))

    def test_busy_or_unknown(self):
        run = _Run()
        self.page._slots["proxy"].job = {"action": "proxy_go"}
        self.page.run_automation_step("bins", {"bin_list": "A"}, run)
        self.assertIn("busy", run.result[1])
        self.page._slots["proxy"].job = None
        run = _Run()
        self.page.run_automation_step("nope", {}, run)
        self.assertIn("no step called nope", run.result[1])

    def test_the_steps_offered(self):
        ids = [s["id"] for s in self.page.automation_steps()]
        self.assertEqual(ids, ["bins", "import", "proxies", "populate", "sync", "sync_external", "collapse", "shift"])
        for spec in self.page.automation_steps():
            st.clean_options(spec, {})                                # every option is one the data takes
            for o in spec["options"]:
                self.assertIn(o["kind"], st.KINDS)


class AuditStepTests(unittest.TestCase):
    def page(self, counts, stop_if):
        from pages.audit import checks
        from pages.audit.automation import AuditAutomationMixin

        class Page(AuditAutomationMixin):
            pass
        p = Page()
        p.result = {"counts": {checks.PROBLEM: counts[0], checks.WARNING: counts[1], checks.NOTE: counts[2]}}
        p.sound_error = ""
        run = _Run()
        p._auto = {"run": run, "options": {"sound": False, "stop_if": stop_if}}
        p._auto_conclude()
        return run.result

    def test_what_it_found_and_when_it_stops_the_run(self):
        self.assertEqual(self.page((0, 0, 0), "problems"), (True, "Nothing to fix."))
        self.assertEqual(self.page((1, 2, 0), "never"), (True, "Found 1 problem, 2 warnings – see Audit."))
        self.assertFalse(self.page((1, 0, 0), "problems")[0])
        self.assertTrue(self.page((0, 3, 1), "problems")[0])
        self.assertFalse(self.page((0, 3, 1), "warnings")[0])


class SubtitlesStepTests(unittest.TestCase):
    def test_its_questions_answered_from_the_steps_options(self):
        from pages.transcribe.automation import TranscribeAutomationMixin

        class Page(TranscribeAutomationMixin):
            pass
        p = Page()
        got = []
        for reuse, replace in ((True, True), (False, False)):
            run = _Run()
            p._auto = {"run": run, "options": {"reuse": reuse, "replace": replace}}
            p._auto_answer("existing", got.append, None)
            p._auto_answer("replace", lambda _v: got.append("replaced"), lambda: got.append("kept"))
        self.assertEqual(got, ["use", "replaced", "again", "kept"])


if __name__ == "__main__":
    unittest.main()
