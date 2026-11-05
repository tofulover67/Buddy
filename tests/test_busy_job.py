"""core/busy_job.py: a long Resolve job goes to the reader's process, the
busy overlay up until its answer comes back - so Buddy's UI thread is free
meanwhile - or, with no reader, is done here as before (never on a thread)."""

import threading
import unittest

import _paths  # noqa: F401
from core import busy_job

try:
    from core.resolve_reader import ReadError
except ImportError:  # pragma: no cover - no PySide6: the same shape
    class ReadError(RuntimeError):
        def __init__(self, message, kind=""):
            super().__init__(message)
            self.kind = kind


class Host:
    def __init__(self):
        self.busy = []

    def set_busy(self, on, text=None):
        self.busy.append(on)


class Reader:
    def __init__(self, available=True, takes=True):
        self._available, self._takes, self.asked = available, takes, []

    def available(self):
        return self._available

    def ask(self, name, args, done):
        if not self._takes:
            return False
        self.asked.append((name, args, done))
        return True


class Calls:
    def __init__(self, reader):
        self.reader = reader


class RunTests(unittest.TestCase):
    def setUp(self):
        self.host, self.got = Host(), []
        self.done = lambda result, error: self.got.append((result, error, list(self.host.busy)))

    def test_the_reader_does_it_and_the_overlay_stays_up_until_it_answers(self):
        reader = Reader()
        here = []
        busy_job.run(self.host, Calls(reader), "x.build", {"n": 1}, lambda: here.append(1), self.done, "Building")
        self.assertEqual(here, [])                                 # not done in Buddy's process
        self.assertEqual(self.host.busy, [True])                   # the overlay's up, the UI thread free
        name, args, answer = reader.asked[0]
        self.assertEqual((name, args), ("x.build", {"n": 1}))
        answer({"made": True}, None)
        self.assertEqual(self.got, [({"made": True}, None, [True, False])])   # down before done() hears

    def test_with_no_reader_its_done_here_on_this_thread(self):
        threads = []
        busy_job.run(self.host, Calls(Reader(available=False)), "x.build", {},
                     lambda: threads.append(threading.current_thread()) or 7, self.done, "Building")
        self.assertEqual(threads, [threading.current_thread()])
        self.assertEqual(self.got, [(7, None, [True, False])])

    def test_a_reader_that_cant_start_falls_back_and_an_error_is_handed_on(self):
        def boom():
            raise RuntimeError("no timeline")
        busy_job.run(self.host, Calls(Reader(takes=False)), "x.build", {}, boom, self.done, "Building")
        result, error, busy = self.got[0]
        self.assertIsNone(result)
        self.assertEqual(str(error), "no timeline")
        self.assertEqual(busy, [True, False])

    def test_a_reader_that_never_reached_resolve_leaves_the_job_to_be_done_here(self):
        # What the real reader does where nothing outside Resolve may connect
        # (the free Resolve on a Mac, external scripting off): its process says
        # it couldn't, and ends - the job "Ended", the reader unavailable for a while.
        reader = Reader()
        here = []
        busy_job.run(self.host, Calls(reader), "x.build", {}, lambda: here.append(1) or 7, self.done, "Building")
        reader._available = False
        reader.asked[0][2](None, ReadError("Couldn't reach Resolve: Could not connect to DaVinci Resolve.", "Ended"))
        self.assertEqual(here, [1])
        self.assertEqual(self.got, [(7, None, [True, False])])

    def test_a_job_the_readers_process_died_in_isnt_made_twice(self):
        reader = Reader()               # still available: it had reached Resolve
        here = []
        busy_job.run(self.host, Calls(reader), "x.build", {}, lambda: here.append(1), self.done, "Building")
        reader.asked[0][2](None, ReadError("The Resolve reader stopped.", "Ended"))
        self.assertEqual(here, [])
        result, error, busy = self.got[0]
        self.assertEqual((result, error.kind, busy), (None, "Ended", [True, False]))

    def test_args_the_pipe_cant_carry_are_said_and_the_overlay_comes_down(self):
        class Strict(Reader):
            def ask(self, name, args, done):
                raise TypeError("Object of type set is not JSON serializable")
        here = []
        busy_job.run(self.host, Calls(Strict()), "x.build", {"ids": {1}}, lambda: here.append(1), self.done, "Building")
        self.assertEqual(here, [])
        result, error, busy = self.got[0]
        self.assertIsInstance(error, TypeError)
        self.assertEqual(busy, [True, False])


if __name__ == "__main__":
    unittest.main()
