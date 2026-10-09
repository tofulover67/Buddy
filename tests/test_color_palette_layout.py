"""Color Palette's colour harmony generator, rendered for real: its swatches
fill the preview card rather than huddling at its left, and nothing - not
even a long palette name on "Add colour ->" - makes the page scroll
sideways. Measured in the page itself at a few pane widths, with a
throwaway data folder."""

import json
import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QTimer
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from PySide6.QtWidgets import QApplication
    HAVE_WEB = True
except ImportError:  # pragma: no cover
    HAVE_WEB = False

LONG_NAME = "Cinematic Look for the Autumn Campaign"

MEASURE = """(() => {
  const shown = sel => [...document.querySelectorAll(sel)].find(n => n.offsetParent);
  const page = document.querySelector('.page'), card = shown('.gen-preview-card');
  const row = card.querySelector('.gen-preview'), last = row.children[3].querySelector('.sw');
  return {sideways: page.scrollWidth - page.clientWidth,
          rightGap: card.getBoundingClientRect().right - last.getBoundingClientRect().right,
          card: card.getBoundingClientRect().width};
})()"""


ON_GITHUB = os.environ.get("BUDDY_NO_LIVE_GESTURES") == "1"   # set by .github/workflows/tests.yml
# GitHub's Mac build machines have no screen, and a gesture played there
# in real time doesn't bounce the way it does on a Mac you can see - the
# release build skips these; run them on a Mac (python3 -m unittest).
NO_LIVE_GESTURES = "GitHub's headless Mac can't play trackpad gestures in real time"


@unittest.skipUnless(HAVE_WEB, "PySide6 with QtWebEngine not installed")
class HarmonyLayoutTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        import test_color_palette as tcp
        from pages.color_palette import page as page_mod
        from pages.color_palette.data_manager import DataManager

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        dm = tcp.data_in(tmp.name)
        for save in (dm.save_palette_data, dm.save_palette_folders, dm.save_palette_custom_tags):
            save()
        patcher = mock.patch.object(page_mod.ColorPalettePage, "_make_data_manager",
                                    lambda s: DataManager(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.page = page_mod.ColorPalettePage(tcp.Host())
        self.page.resize(1100, 900)
        self.page.show()
        for _ in range(100):
            self.wait(100)
            if self.page._ready:
                break
        self.assertTrue(self.page._ready, "the page never loaded")
        self.page.on_tab({"tab": "generators"})
        self.page.on_generator({"id": "harmony"})
        self.wait(500)

    def tearDown(self):
        self.page.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait(self, ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def js(self, expression):
        out, loop = [], QEventLoop()
        self.page.view.page().runJavaScript(f"JSON.stringify({expression})", 0,
                                            lambda value: (out.append(value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        return json.loads(out[0]) if out and out[0] else None

    def measure(self, width):
        self.page.resize(width, 900)
        self.wait(400)
        return self.js(MEASURE)

    def test_the_swatches_fill_the_preview_card(self):
        for width in (1000, 1300, 1700):
            m = self.measure(width)
            self.assertLess(m["rightGap"], m["card"] * 0.12, f"dead space right of the swatches at {width}px: {m}")

    @unittest.skipIf(ON_GITHUB, NO_LIVE_GESTURES)
    def test_the_page_bounces_though_it_all_fits(self):
        # The harmony screen fits in the window, so the page doesn't
        # overflow - a Mac scroll view bounces all the same (buddy.js).
        self.measure(1300)
        stretch = self.js("(() => { const sw = [...document.querySelectorAll('.gen-preview .sw')]"
                          ".find(n => n.offsetParent); for (let i = 0; i < 4; i++) sw.dispatchEvent("
                          "new WheelEvent('wheel', {deltaY: -12, bubbles: true})); "
                          "return [Buddy._bounce.box && Buddy._bounce.box.tagName, Buddy._bounce.shown]; })()")
        if not self.js("Buddy.IS_MAC"):
            self.skipTest("the bounce is macOS-only")
        self.assertEqual(stretch[0], "MAIN")
        self.assertGreater(stretch[1], 5)

    @unittest.skipIf(ON_GITHUB, NO_LIVE_GESTURES)
    def test_scrolling_down_past_the_bottom_bounces(self):
        # Short enough that the page scrolls, and scrolled to its end: the
        # content must visibly move up, not just be told to.
        if not self.js("Buddy.IS_MAC"):
            self.skipTest("the bounce is macOS-only")
        self.page.resize(1300, 420)
        self.wait(400)
        moved = self.js("(() => { const pg = document.querySelector('.page'); pg.scrollTop = 1e6; "
                        "if (pg.scrollHeight <= pg.clientHeight) return 'fits'; "
                        "const sw = [...document.querySelectorAll('.gen-preview .sw')].find(n => n.offsetParent); "
                        "const before = sw.getBoundingClientRect().top; "
                        "for (let i = 0; i < 4; i++) sw.dispatchEvent(new WheelEvent('wheel', {deltaY: 12, bubbles: true})); "
                        "return sw.getBoundingClientRect().top - before; })()")
        self.assertNotEqual(moved, "fits", "the page should scroll at this height")
        self.assertLess(moved, -5)

    @unittest.skipIf(ON_GITHUB, NO_LIVE_GESTURES)
    def test_extract_doesnt_shift_sideways_while_it_bounces(self):
        # Sized so Extract just fits: its bounce mustn't push the content past
        # the bottom, where a scrollbar would appear and move everything over.
        if not self.js("Buddy.IS_MAC"):
            self.skipTest("the bounce is macOS-only")
        self.page.on_tab({"tab": "extract"})
        self.page.resize(1300, 2000)
        self.wait(500)
        natural = self.js("(() => { const pg = document.querySelector('.page'); let b = 0; "
                          "for (const n of pg.querySelectorAll('*')) if (n.offsetParent) "
                          "b = Math.max(b, n.getBoundingClientRect().bottom); "
                          "return Math.ceil(b + parseFloat(getComputedStyle(pg).paddingBottom)); })()")
        self.page.resize(1300, natural + 8)
        self.wait(500)
        widths = self.js("(() => { const pg = document.querySelector('.page'), before = pg.clientWidth; "
                         "const t = [...document.querySelectorAll('.panel *')].find(n => n.offsetParent && !n.children.length); "
                         "for (let i = 0; i < 4; i++) t.dispatchEvent(new WheelEvent('wheel', {deltaY: -12, bubbles: true})); "
                         "return [before, pg.clientWidth, Buddy._bounce.shown]; })()")
        self.assertGreater(widths[2], 5, "it should be bouncing")
        self.assertEqual(widths[1], widths[0])

    def test_a_long_palette_name_never_scrolls_the_page_sideways(self):
        self.js("[...document.querySelectorAll('button')].filter(b => b.textContent.startsWith('Add colour'))"
                f".forEach(b => b.textContent = {json.dumps('Add colour → ' + LONG_NAME)}), 0")
        for width in (820, 1000, 1300, 1700):
            self.assertEqual(self.measure(width)["sideways"], 0, f"scrolls sideways at {width}px")


if __name__ == "__main__":
    unittest.main()
