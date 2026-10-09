"""The Web tab's spell check dictionaries (pages/web/spelling.py): fetched
for a language into the dictionaries folder, never kept if what came back
isn't a dictionary - against a local server standing in for Chrome's."""

import functools
import http.server
import os
import shutil
import tempfile
import threading
import time
import unittest

import _paths  # noqa: F401

try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class FetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.served = tempfile.mkdtemp(prefix="buddy_dicts_served_")
        self.folder = tempfile.mkdtemp(prefix="buddy_dicts_")
        self.addCleanup(shutil.rmtree, self.served, True)
        self.addCleanup(shutil.rmtree, self.folder, True)
        with open(os.path.join(self.served, "en-us-10-1.bdic"), "wb") as fh:
            fh.write(b"BDic" + b"\0" * 64)
        with open(os.path.join(self.served, "de-de-3-0.bdic"), "wb") as fh:
            fh.write(b"<html>not found</html>")

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=self.served))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_address[1]}/"
        os.environ["QTWEBENGINE_DICTIONARIES_PATH"] = self.folder
        self.addCleanup(os.environ.pop, "QTWEBENGINE_DICTIONARIES_PATH", None)

    def fetch(self, language):
        from pages.web import spelling
        fetcher = spelling.Fetcher(server=self.url)
        self.addCleanup(self._drop, fetcher)          # gone while Qt runs, not as Python exits
        heard = []
        fetcher.done.connect(lambda lang, error: heard.append((lang, error)))
        fetcher.fetch(language)
        end = time.monotonic() + 10
        while not heard and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)
        return heard

    def _drop(self, fetcher):
        fetcher.deleteLater()
        for _ in range(5):
            self.app.processEvents()

    def test_a_dictionary_is_kept_under_its_language(self):
        from pages.web import spelling
        self.assertFalse(spelling.have("en-US"))
        self.assertEqual(self.fetch("en-US"), [("en-US", "")])
        self.assertTrue(spelling.have("en-US"))
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "en-US.bdic")))
        self.assertEqual(spelling.url_for("en-US"), spelling.SERVER + "en-us-10-1.bdic")

    def test_what_isnt_a_dictionary_is_refused(self):
        from pages.web import spelling
        heard = self.fetch("de-DE")
        self.assertEqual(heard[0][0], "de-DE")
        self.assertTrue(heard[0][1])
        self.assertFalse(spelling.have("de-DE"))
        self.assertEqual(os.listdir(self.folder), [])


if __name__ == "__main__":
    unittest.main()
