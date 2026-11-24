"""Runs the whole test suite and exits with its result.

    python tests/run_suite.py [-v]

The same as `python -m unittest discover tests`, except for how it ends: it
leaves with os._exit() once the results are printed, skipping Python's
normal shutdown. The page tests create QtWebEngine views, and Qt WebEngine's
own teardown at process exit can crash (an access violation after "OK"),
which would turn a passing run into a failed exit code - the release
workflow uses this runner for that reason.

It also sets BUDDY_HOME (core/tool_dirs.py) to a new empty folder, unless
it's set already, so nothing a test writes by accident lands in the real
~/.buddy - the folder is removed at the end.

A run that dies natively (Qt, Chromium) says which test it was in: Python's
faulthandler prints every thread's stack on the way down - for a trap too,
which is how Chromium stops itself on a Mac.

And it collects garbage the way Buddy does (core/gui_gc.py): never by
itself, which is on whatever thread happens to allocate, but here, on the
main thread, after each test. A page a test made and let go of is a cycle
with a web view in it; collected inside another test's worker thread, the
view is destroyed there and Chromium stops the process - exit code 3, in
the middle of a test that has nothing to do with it, and only with exactly
the right number of tests before it.
"""

import faulthandler
import gc
import os
import signal
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL_EVERY = 25             # tests: the young garbage after each, everything this often


class _CollectingResult(unittest.TextTestResult):
    """Collects on this thread between tests, with the collector's own
    timing off - again after each test, since one may have turned it back
    on (core/gui_gc.py's stop())."""

    def stopTest(self, test):
        super().stopTest(test)
        gc.disable()
        gc.collect() if self.testsRun % FULL_EVERY == 0 else gc.collect(0)

if __name__ == "__main__":
    os.chdir(ROOT)
    faulthandler.enable()
    if hasattr(signal, "SIGTRAP") and hasattr(faulthandler, "register"):    # not on Windows
        faulthandler.register(signal.SIGTRAP, chain=True)
    sandbox = None
    if not os.environ.get("BUDDY_HOME", "").strip():
        sandbox = tempfile.mkdtemp(prefix="buddy-home-")
        os.environ["BUDDY_HOME"] = sandbox      # before the tests import anything of Buddy's
    gc.disable()
    program = unittest.main(module=None, argv=[sys.argv[0], "discover", "tests", *sys.argv[1:]], exit=False,
                            testRunner=unittest.TextTestRunner(resultclass=_CollectingResult))
    sys.stdout.flush()
    sys.stderr.flush()
    if sandbox:
        shutil.rmtree(sandbox, ignore_errors=True)
    os._exit(0 if program.result.wasSuccessful() else 1)
