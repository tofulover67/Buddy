"""buddy.js's macOS overscroll bounce: a scroll box pushed past its top or
bottom stretches (its children move, with resistance) and springs back,
overshooting when it was flicked. Drives the real buddy.js in a real web
view with synthetic wheel events played at trackpad-like intervals, and
reads the stretch it shows on every frame. macOS only - the bounce is off
everywhere else."""

import json
import os
import sys
import unittest

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer, QUrl
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAVE_WEB = True
except ImportError:  # pragma: no cover
    HAVE_WEB = False

ON_GITHUB = os.environ.get("BUDDY_NO_LIVE_GESTURES") == "1"   # set by .github/workflows/tests.yml
# GitHub's Mac build machines have no screen, and a gesture played there
# in real time doesn't bounce the way it does on a Mac you can see - the
# release build skips these; run them on a Mac (python3 -m unittest).
NO_LIVE_GESTURES = "GitHub's headless Mac can't play trackpad gestures in real time"

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "web")

PAGE = """<!doctype html><html data-family="resolve"><head><link rel="stylesheet" href="buddy.css"></head><body>
<div id="box" style="height: 200px; overflow-y: auto">
  <div id="a" style="height: 300px">a</div><div id="b" style="height: 300px">b</div>
</div>
<main id="page" style="height: 150px; overflow-y: auto">
  <nav id="strip" style="overflow-x: auto; white-space: nowrap"><span id="tab">tab</span></nav>
  <div id="s">fits</div>
</main>
<div id="snug" style="height: 150px; width: 200px; overflow-y: auto"><div id="snugitem" style="height: 145px">just fits</div></div>
<div id="pop" style="position: fixed; top: 0; right: 0; width: 80px; height: 100px; overflow-y: auto">
  <div id="popitem">menu</div>
</div>
<script src="buddy.js"></script></body></html>"""

# play(id, deltas): one wheel event per delta at that element, 8 ms apart
# (a trackpad's rate), recording [ms, mode, shown] every frame into
# window.trace until the bounce is back at rest. window.done once it is.
HELPERS = """
window.shift = id => { const m = /translateY\\((-?[\\d.]+)px\\)/.exec(document.getElementById(id).style.transform);
    return m ? parseFloat(m[1]) : 0; };
window.play = (id, deltas, extra) => {
    window.trace = []; window.done = false;
    const start = performance.now(), node = document.getElementById(id);
    deltas.forEach((dy, i) => setTimeout(() => node.dispatchEvent(new WheelEvent("wheel",
        Object.assign({deltaY: dy, bubbles: true, cancelable: true}, extra || {}))), i * 8));
    const last = deltas.length * 8;
    const tick = () => {
        const t = performance.now() - start, b = Buddy._bounce;
        window.trace.push([t, b.mode, b.shown]);
        if (t > last + 20 && b.mode === "idle") { window.done = true; return; }
        if (t > 3000) { window.done = true; return; }
        requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
};
"""


@unittest.skipUnless(HAVE_WEB and sys.platform == "darwin", "macOS with QtWebEngine only")
@unittest.skipIf(ON_GITHUB, NO_LIVE_GESTURES)
class BounceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = QWebEngineView()
        self.view.resize(400, 500)
        self.view.show()
        loop = QEventLoop()
        self.view.loadFinished.connect(lambda _ok: loop.quit())
        QTimer.singleShot(10000, loop.quit)
        self.view.setHtml(PAGE, QUrl.fromLocalFile(WEB_DIR + os.sep))
        loop.exec()
        self.js(HELPERS)

    def tearDown(self):
        self.view.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def js(self, code):
        """Runs `code` in the page; its value, through JSON (PySide turns a
        JS array into an empty list otherwise)."""
        result, loop = [], QEventLoop()
        wrapped = f"JSON.stringify(eval({json.dumps(code)})) ?? null"
        self.view.page().runJavaScript(wrapped, 0, lambda value: (result.append(value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        self.assertTrue(result, f"no result from: {code}")
        return json.loads(result[0]) if result[0] else None

    def wait(self, ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def play(self, deltas, box="box", **extra):
        """The frames of one gesture, [(ms, mode, shown)], to rest again."""
        self.js(f"play({json.dumps(box)}, {json.dumps(deltas)}, {json.dumps(extra)})")
        for _ in range(80):
            self.wait(50)
            if self.js("window.done"):
                break
        return [tuple(frame) for frame in self.js("window.trace")]

    def test_a_drag_past_the_top_stretches_down_with_resistance_then_settles(self):
        frames = self.play([-6, -8, -10, -10, -10, -8])
        biggest = max(shown for _t, _m, shown in frames)
        self.assertGreater(biggest, 5)
        self.assertLess(biggest, 52, "resists - less than the 52px pulled")
        self.assertEqual(frames[-1][1], "idle")
        self.assertEqual(self.js('document.getElementById("a").style.transform'), "")

    def test_the_stretch_shows_the_moment_the_event_arrives(self):
        # Read back in the same script as the events: no frame has been
        # drawn in between, so anything smoothed or left for the next frame
        # would still read 0 here.
        first, second = self.js('[(wheel => (wheel(-12), shift("a")))(dy => document.getElementById("a")'
                                '.dispatchEvent(new WheelEvent("wheel", {deltaY: dy, bubbles: true}))), '
                                '(document.getElementById("a").dispatchEvent(new WheelEvent("wheel", '
                                '{deltaY: -12, bubbles: true})), shift("a"))]')
        self.assertGreater(first, 0)
        self.assertGreater(second, first)

    def test_both_children_move_together(self):
        self.js('Array.from({length: 6}, () => document.getElementById("a").dispatchEvent('
                'new WheelEvent("wheel", {deltaY: -12, bubbles: true})))')
        self.wait(60)
        a, b = self.js('[shift("a"), shift("b")]')
        self.assertGreater(a, 0)
        self.assertEqual(a, b)

    def test_a_drag_past_the_bottom_stretches_up(self):
        self.js('document.getElementById("box").scrollTop = 1e6')
        frames = self.play([6, 8, 10, 10, 10, 8])
        self.assertLess(min(shown for _t, _m, shown in frames), -5)

    # Where the content really is on screen, not just what was asked of it:
    # moving a scrolled box's content up past its bottom can look right in
    # its styles while the browser scrolls it straight back.
    def _moved_on_screen(self, element, deltas):
        before = self.js(f'document.getElementById("{element}").getBoundingClientRect().top')
        moved = self.js(f'(() => {{ const n = document.getElementById("{element}"); '
                        f'for (const dy of {json.dumps(deltas)}) n.dispatchEvent(new WheelEvent("wheel", '
                        f'{{deltaY: dy, bubbles: true}})); return n.getBoundingClientRect().top; }})()')
        return moved - before, self.js("Buddy._bounce.shown")

    def test_past_the_bottom_of_a_scrolled_box_the_content_really_moves_up(self):
        self.js('document.getElementById("box").scrollTop = 1e6')
        moved, shown = self._moved_on_screen("b", [10, 12, 14, 12])
        self.assertLess(moved, -5)
        self.assertAlmostEqual(moved, shown, delta=1)

    def test_past_the_top_the_content_really_moves_down(self):
        moved, shown = self._moved_on_screen("a", [-10, -12, -14, -12])
        self.assertGreater(moved, 5)
        self.assertAlmostEqual(moved, shown, delta=1)

    def test_after_a_bottom_bounce_the_box_is_as_it_was(self):
        self.js('document.getElementById("box").scrollTop = 1e6')
        end = self.js('document.getElementById("box").scrollTop')
        self.play([10, 12, 14, 12])
        box = self.js('(b => [b.scrollTop, b.scrollHeight, b.className, b.getAttribute("style")])'
                      '(document.getElementById("box"))')
        self.assertEqual(box[0], end)
        self.assertEqual(box[1], 600)
        self.assertEqual(box[2], "")
        self.assertNotIn("buddy-overscroll", box[3])

    def test_a_flick_from_rest_overshoots_after_release_then_returns(self):
        # Fingers speeding up, then lifting. (The momentum that follows
        # never reaches the page - QtWebEngine keeps it.)
        frames = self.play([-4, -8, -14, -20, -26, -30])
        release = next(i for i, (_t, mode, _s) in enumerate(frames) if mode == "spring")
        at_release = frames[release][2]
        peak = max(shown for _t, _m, shown in frames[release:])
        self.assertGreater(peak, at_release + 2, "keeps going outwards after release")
        self.assertEqual(frames[-1][1], "idle")
        self.assertLess(frames[-1][0], 1500, "back at rest within about a second")

    def test_every_gesture_bounces_not_just_the_first(self):
        # As the page really gets them: Chromium's zero-delta "gesture
        # began" marker first, then a pull that slows down as it ends. (That
        # can look like a fling's dying momentum, and then the "ignore the
        # rest of the momentum" must still end - the marker keeps the gap
        # before each next gesture too short to count as quiet.)
        for attempt in range(3):
            frames = self.play([0, -6, -12, -16, -12, -8, -5, -3])
            self.assertGreater(max(shown for _t, _m, shown in frames), 5, f"gesture {attempt + 1} didn't bounce")
            self.wait(150)

    def test_real_trackpad_events_bounce_every_time(self):
        # Qt wheel events with the phases macOS gives them, sent into the
        # view itself: through Chromium's own handling (coalescing, gesture
        # markers, momentum) to buddy.js.
        target = self.view.focusProxy() or self.view
        self.js('window.peak = 0; (function f() { peak = Math.max(peak, Buddy._bounce.shown); '
                'requestAnimationFrame(f); })()')

        def send(px, phase):
            pos = QPointF(50, 60)
            QApplication.sendEvent(target, QWheelEvent(
                pos, QPointF(target.mapToGlobal(pos.toPoint())), QPoint(0, px), QPoint(0, px * 4),
                Qt.NoButton, Qt.NoModifier, phase, False, Qt.MouseEventSynthesizedBySystem))

        for attempt in range(3):
            self.js("peak = 0")
            send(0, Qt.ScrollBegin)
            for px in (3, 6, 10, 12, 9, 6, 4, 2):   # past the top, slowing down
                self.wait(16)
                send(px, Qt.ScrollUpdate)
            send(0, Qt.ScrollEnd)
            for px in (8, 6, 4, 3, 2, 1):
                self.wait(16)
                send(px, Qt.ScrollMomentum)
            send(0, Qt.ScrollEnd)
            self.wait(900)
            self.assertGreater(self.js("peak"), 5, f"gesture {attempt + 1} didn't bounce")
            self.assertEqual(self.js("Buddy._bounce.mode"), "idle")

    def test_the_rebound_is_quick(self):
        frames = self.play([-10, -12, -14, -12])
        release = next(t for t, mode, _s in frames if mode == "spring")
        self.assertLess(frames[-1][0] - release, 450, "back at rest within ~0.4 s of letting go")

    def test_momentum_after_a_flick_neither_holds_nor_restretches(self):
        tail = [round(-28 * 0.93 ** i, 2) for i in range(60)]     # ~0.5 s of momentum
        frames = self.play([-4, -8, -14, -20, -26, -30] + tail)
        release = next(i for i, (_t, mode, _s) in enumerate(frames) if mode == "spring")
        self.assertLess(frames[release][0], 6 * 8 + 6 * 8, "let go within a few momentum events")
        self.assertNotIn("drag", [mode for _t, mode, _s in frames[release:]], "stretched again by the tail")

    def test_after_ignored_momentum_the_next_gesture_still_bounces(self):
        tail = [round(-28 * 0.93 ** i, 2) for i in range(40)]
        self.play([-4, -8, -14, -20, -26, -30] + tail)
        # With Chromium's start marker...
        frames = self.play([0, -6, -10, -14, -12])
        self.assertGreater(max(shown for _t, _m, shown in frames), 5)
        # ...and without one, after a pause.
        self.play([-4, -8, -14, -20, -26, -30] + tail)
        self.wait(150)
        frames = self.play([-2, -6, -10, -14, -12])
        self.assertGreater(max(shown for _t, _m, shown in frames), 5)

    def test_a_finger_that_stops_before_lifting_barely_overshoots(self):
        frames = self.play([-10, -10, -8, -5, -3, -1])
        release = next(i for i, (_t, mode, _s) in enumerate(frames) if mode == "spring")
        after = [shown for _t, _m, shown in frames[release:]]
        # A few px at most: slowing down is let go while the finger is still
        # moving a little (it reads like momentum), so it carries on slightly.
        self.assertLessEqual(max(after), frames[release][2] + 6)

    def test_a_lift_while_still_moving_carries_on_a_little(self):
        frames = self.play([-10, -10, -10, -10, -10, -10])
        release = next(i for i, (_t, mode, _s) in enumerate(frames) if mode == "spring")
        self.assertGreater(max(shown for _t, _m, shown in frames[release:]), frames[release][2])

    def test_the_spring_never_goes_past_rest(self):
        frames = self.play([-4, -8, -14, -20, -26, -30])
        self.assertGreaterEqual(min(shown for _t, _m, shown in frames), 0)

    def test_scrolling_back_undoes_the_stretch_first(self):
        frames = self.play([-20, -20, -20, 15, 15, 15, 15, 15])
        peak = max(range(len(frames)), key=lambda i: frames[i][2])
        self.assertGreater(frames[peak][2], 5)
        self.assertEqual(self.js('document.getElementById("box").scrollTop'), 0,
                         "held at the top while the stretch came undone")

    def test_a_box_that_can_still_scroll_is_left_to_the_browser(self):
        frames = self.play([10, 12, 14])
        self.assertTrue(all(mode == "idle" for _t, mode, _s in frames))

    def test_a_page_whose_content_fits_still_bounces(self):
        # As a Mac scroll view does with little in it.
        frames = self.play([-10, -12, -14], box="s")
        self.assertGreater(max(shown for _t, _m, shown in frames), 5)
        frames = self.play([10, 12, 14], box="s")
        self.assertLess(min(shown for _t, _m, shown in frames), -5, "and at the bottom")

    def test_a_tab_strip_inside_the_page_doesnt_steal_the_bounce(self):
        # overflow-x: auto makes the strip a vertical scroller as far as
        # the browser is concerned; the page it's in is what stretches.
        self.js('Array.from({length: 4}, () => document.getElementById("tab").dispatchEvent('
                'new WheelEvent("wheel", {deltaY: -12, bubbles: true})))')
        self.assertEqual(self.js("Buddy._bounce.box && Buddy._bounce.box.id"), "page")
        self.assertGreater(self.js('shift("strip")'), 0)
        self.assertEqual(self.js('shift("tab")'), 0)

    def test_a_box_that_just_fits_doesnt_grow_a_scrollbar_while_it_bounces(self):
        # The stretch pushes its content past the bottom for a moment; a
        # scrollbar appearing would take room and shift everything over.
        width = self.js('document.getElementById("snug").clientWidth')
        was = self.js('document.getElementById("snug").style.overflowY')
        during = self.js('(() => { const n = document.getElementById("snugitem"); for (let i = 0; i < 4; i++) '
                         'n.dispatchEvent(new WheelEvent("wheel", {deltaY: -12, bubbles: true})); '
                         'return [document.getElementById("snug").clientWidth, Buddy._bounce.shown]; })()')
        self.assertGreater(during[1], 5)
        self.assertEqual(during[0], width)
        self.wait(700)
        self.assertEqual(self.js('document.getElementById("snug").style.overflowY'), was, "put back afterwards")

    def test_a_pop_up_that_fits_stays_put(self):
        frames = self.play([-10, -12, -14], box="popitem")
        self.assertTrue(all(mode == "idle" for _t, mode, _s in frames))

    def test_pinch_zoom_is_left_alone(self):
        frames = self.play([-10, -12, -14], ctrlKey=True)
        self.assertTrue(all(mode == "idle" for _t, mode, _s in frames))


if __name__ == "__main__":
    unittest.main()
