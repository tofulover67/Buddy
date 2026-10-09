"""ResolveCalls (core/resolve_reader.py): pages' Resolve reads through the
shared reader process, the old way when there's none, errors as the page
knows them - and the reads the reader's process makes for them."""

import json
import os
import unittest
from unittest import mock

import _paths  # noqa: F401
from core import resolve_reader_child as child

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from core import resolve_reader
    from core.resolve_bridge import ResolveConnectionError
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class Worker:
    """A ResolveWorker that runs its job when told to."""

    def __init__(self):
        self.started = []

    def start(self, job, done):
        self.started.append((job, done))
        return True

    def busy(self):
        return bool(self.started)

    def running_for(self):
        return 0.0

    def wait_idle(self, _timeout):
        return True


class Reader:
    """The shared reader, answering when told to."""

    def __init__(self, available=True):
        self.ok, self.asked = available, []

    def available(self):
        return self.ok

    def ask(self, name, args, done):
        self.asked.append((name, args, done))
        return True


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class CallsTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.reader, self.worker = Reader(), Worker()
        with mock.patch.object(resolve_reader, "shared_reader", return_value=self.reader):
            self.calls = resolve_reader.ResolveCalls(worker=self.worker, errors={"KeyError": KeyError})
        self.got = []

    def done(self, result, error):
        self.got.append((result, error))

    def test_reads_go_to_the_reader_and_come_back(self):
        self.calls.call("stills.markers", {}, lambda: "here", self.done)
        self.assertTrue(self.calls.busy())
        name, _args, back = self.reader.asked[0]
        self.assertEqual(name, "stills.markers")
        back(["Edit", []], None)
        self.assertEqual(self.got, [(["Edit", []], None)])
        self.assertFalse(self.calls.busy())
        self.assertEqual(self.worker.started, [])

    def test_errors_come_back_as_the_jobs_own(self):
        self.calls.call("x", {}, lambda: None, self.done)
        self.calls.call("y", {}, lambda: None, self.done)
        self.reader.asked[0][2](None, resolve_reader.ReadError("No timeline open.", "ResolveConnectionError"))
        self.reader.asked[1][2](None, resolve_reader.ReadError("'c1'", "KeyError"))
        self.assertIsInstance(self.got[0][1], ResolveConnectionError)
        self.assertEqual(str(self.got[0][1]), "No timeline open.")
        self.assertIsInstance(self.got[1][1], KeyError)

    def test_no_reader_reads_the_old_way(self):
        self.reader.ok = False
        self.calls.call("x", {}, lambda: "here", self.done)
        self.assertEqual(self.reader.asked, [])
        self.assertEqual(len(self.worker.started), 1)

    def test_a_reader_that_could_not_start_falls_back_to_the_old_way(self):
        self.calls.call("x", {}, lambda: "here", self.done)
        self.reader.ok = False
        self.reader.asked[0][2](None, resolve_reader.ReadError("couldn't reach Resolve", "Ended"))
        self.assertEqual(self.got, [])                      # not an error: read again, here
        self.assertEqual(len(self.worker.started), 1)

    def test_waiting_for_a_read_gives_up_after_its_time(self):
        self.calls.call("x", {}, lambda: None, self.done)
        self.assertFalse(self.calls.wait_idle(0.05))        # never answered
        self.reader.asked[0][2]("ok", None)
        self.assertTrue(self.calls.wait_idle(0.05))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class QueuedReaderTests(unittest.TestCase):
    def test_a_broken_pipe_leaves_the_new_request_for_fallback_only(self):
        reader = resolve_reader.ResolveReader(queue=True)
        old_done, new_done = mock.Mock(), mock.Mock()
        proc = mock.Mock()
        reader._proc = proc
        reader._ready = True
        with mock.patch.object(reader, "_start", return_value=True):
            self.assertTrue(reader.ask("old", {}, old_done))
            proc.stdin.write.side_effect = BrokenPipeError("reader pipe closed")
            self.assertFalse(reader.ask("new", {}, new_done))
        old_done.assert_called_once()
        self.assertEqual(old_done.call_args.args[1].kind, "Ended")
        new_done.assert_not_called()
        proc.kill.assert_called_once()
        self.assertFalse(reader.busy())

    def test_answers_go_to_their_own_asker_in_any_order(self):
        reader = resolve_reader.ResolveReader(queue=True)
        got = []
        with mock.patch.object(reader, "_start", return_value=True):
            reader._proc = mock.Mock()
            reader.ask("a", {}, lambda r, e: got.append(("a", r)))
            reader.ask("b", {}, lambda r, e: got.append(("b", r)))
        self.assertTrue(reader.busy())
        reader._on_line({"id": 2, "ok": True, "result": 2})
        reader._on_line({"id": 1, "ok": True, "result": 1})
        self.assertEqual(got, [("b", 2), ("a", 1)])
        self.assertFalse(reader.busy())

    def test_one_at_a_time_unless_queued(self):
        reader = resolve_reader.ResolveReader()
        with mock.patch.object(reader, "_start", return_value=True):
            reader._proc = mock.Mock()
            self.assertTrue(reader.ask("a", {}, lambda r, e: None))
            self.assertFalse(reader.ask("b", {}, lambda r, e: None))

    def test_a_reader_gone_fails_every_read_in_hand(self):
        reader = resolve_reader.ResolveReader(queue=True)
        got = []
        with mock.patch.object(reader, "_start", return_value=True):
            reader._proc = mock.Mock()
            reader.ask("a", {}, lambda r, e: got.append(e.kind))
            reader.ask("b", {}, lambda r, e: got.append(e.kind))
        reader._ended("gone")
        self.assertEqual(got, ["Ended", "Ended"])

    def test_reads_in_hand_of_a_child_that_exited_fail_before_a_new_child_starts(self):
        reader = resolve_reader.ResolveReader(queue=True)
        got = []
        dead, fresh = mock.Mock(), mock.Mock()
        dead.poll.return_value = 1                      # exited; its "ended" line not here yet
        reader._proc, reader._ready = dead, True
        reader._pending = {1: (lambda r, e: got.append(e.kind), 0.0)}
        with mock.patch.object(reader, "available", return_value=True),                 mock.patch.object(resolve_reader, "_python", return_value="python"),                 mock.patch.object(resolve_reader.subprocess, "Popen", return_value=fresh),                 mock.patch.object(resolve_reader.threading, "Thread"):
            self.assertTrue(reader.ask("b", {}, lambda r, e: got.append("new")))
        self.assertEqual(got, ["Ended"])                # the old read failed, the new one is waiting
        self.assertIs(reader._proc, fresh)
        self.assertEqual(len(reader._pending), 1)


class Folder:
    def GetUniqueId(self): return "bin1"
    def GetName(self): return "Day 1"


class Timeline:
    def GetUniqueId(self): return "tl1"
    def GetName(self): return "Edit"
    def GetCurrentTimecode(self): return "01:00:00:00"


class Project:
    def GetUniqueId(self): return "p1"
    def GetName(self): return "Film"
    def GetMediaPool(self): return self
    def GetRootFolder(self): return Folder()
    def GetCurrentFolder(self): return Folder()
    def GetCurrentTimeline(self): return Timeline()


class Controller:
    def __init__(self, project=None, alive=True):
        self.project = project
        self.resolve = mock.Mock(GetVersionString=mock.Mock(return_value="21.1" if alive else None))

    def current_project(self):
        return self.project


class ChildJobTests(unittest.TestCase):
    def test_where_resolve_is(self):
        reply = child.answer({"id": 1, "do": "resolve.where"}, Controller(Project()))
        self.assertEqual(reply["result"], {"project": "p1", "bin": "bin1", "root": "bin1", "timeline": "tl1",
                                           "timecode": "01:00:00:00"})
        reply = child.answer({"id": 2, "do": "resolve.where"}, Controller(None))
        self.assertIsNone(reply["result"]["timeline"])

    def test_nothing_open_is_no_reason_to_give_up(self):
        import io
        reads = "".join(json.dumps({"id": n, "do": "chapters.markers"}) + "\n" for n in range(6))
        out = io.StringIO()
        self.assertEqual(child.main(io.StringIO(reads), out, connect=lambda: Controller(None)), 0)
        said = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(said), 7)                          # ready, then every read answered
        self.assertEqual(said[1]["type"], "ResolveConnectionError")

    def test_what_json_cannot_write_is_written_plainly(self):
        import io
        from fractions import Fraction
        out = io.StringIO()
        child._say({"rate": Fraction(1001, 24000), "ids": {"a"}}, out)
        self.assertEqual(json.loads(out.getvalue()), {"rate": 1001 / 24000, "ids": ["a"]})


if __name__ == "__main__":
    unittest.main()
