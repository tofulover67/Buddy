"""Animation > Titles in the page: titles_motion.js plays a word exactly as
titles/timing.py says (every entrance and direction, exits, copies fanning out and
breathing, the person's own curves) - what plays in Buddy is what Resolve gets - and
the real Animation page's Titles tab, against the real titles.studio.Studio, draws the
title and sends a drag on a word back as the word's nudge. Qt WebEngine, offscreen."""

import json
import os
import unittest

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl, Signal, Slot
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

WEB = str(_paths.APP / "pages" / "text_animator" / "web")


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _js(page, code, ms=10000):
    loop, out = QEventLoop(), {}
    page.runJavaScript(code, 0, lambda r: (out.update(r=r), loop.quit()))
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    return out.get("r")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class MotionParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.view = QWebEngineView()
        loop = QEventLoop()
        cls.view.loadFinished.connect(lambda _ok: loop.quit())
        html = ("<!DOCTYPE html><html><head><script src=\"titles_motion.js\"></script></head><body></body></html>")
        cls.view.setHtml(html, QUrl.fromLocalFile(os.path.join(WEB, "index.html")))
        QTimer.singleShot(10000, loop.quit)
        loop.exec()

    @classmethod
    def tearDownClass(cls):
        cls.view.deleteLater()

    def compare(self, doc_changes, look="hormozi", text="3 secrets nobody tells you", duration=3.0, over=None):
        from pages.text_animator.titles import composer as C, timing as T

        def fake(t, font, size):
            return len(t) * size * 0.55
        doc = C.new_doc(text, look)
        doc.update(doc_changes)
        if over:
            doc["over"].update(over)
        layout = C.compose(doc, (1920, 1080), fake)
        words = T.timing(doc, layout, duration)["words"]
        times = [i * duration / 180 for i in range(181)]
        want = [[T.state(w, t) for t in times] for w in words]
        got = json.loads(_js(self.view.page(), f"""(() => {{
            TitlesMotion.setCurves({json.dumps(T.CURVES)});
            const words = {json.dumps(words)}, times = {json.dumps(times)};
            return JSON.stringify(words.map(w => times.map(t => TitlesMotion.state(w, t))));
        }})()"""))
        for w, ws, gs in zip(words, want, got):
            for t, a, b in zip(times, ws, gs):
                for channel, v in a.items():
                    self.assertAlmostEqual(b[channel], v, places=9, msg=(look, w["id"], round(t, 3), channel))

    def test_every_entrance_and_direction(self):
        from pages.text_animator.titles import timing as T
        for kind in T.KINDS:
            for direction in ("u", "l", "dr"):
                self.compare({"anim": kind, "dir": direction})

    def test_exits_by_word_line_and_all(self):
        for style, order in (("reverse", "same"), ("slide", "reverse"), ("fade", "all"), ("pop", "same")):
            self.compare({"anim": "slam", "plays": "line", "exit": {"on": True, "style": style, "order": order}})

    def test_copies_fanning_out_and_breathing(self):
        self.compare({"pulse": True, "exit": {"on": True, "style": "reverse", "order": "same"}}, look="echo", duration=5.0)
        self.compare({}, look="long_shadow")

    def test_the_persons_own_curves_and_lengths(self):
        spin = {"len": 0.5, "ch": {"rot": [{"t": 0, "v": -30, "hi": [0, 0], "ho": [0.1, 20]},
                                           {"t": 0.7, "v": 4, "hi": [-0.05, 0], "ho": [0.1, 0]},
                                           {"t": 1, "v": 0, "hi": [-0.1, 0], "ho": [0, 0]}],
                                   "move": [{"t": 0, "v": 0.2, "hi": [0, 0], "ho": [0.3, -0.25]}, {"t": 1, "v": 0, "hi": [-0.2, 0], "ho": [0, 0]}]}}
        self.compare({"curves": spin, "exit": {"on": True, "style": "reverse", "order": "same"}, "times": {"you#1": 1.2}},
                     over={"len": {"tells#1": 0.7}, "dir": {"nobody#1": "r"}})

    def test_exits_of_their_own(self):
        gone = {"len": 0.4, "ch": {"scale": [{"t": 0, "v": 1, "hi": [0, 0], "ho": [0.2, 0.3]}, {"t": 1, "v": 0.2, "hi": [-0.3, 0], "ho": [0, 0]}],
                                   "move": [{"t": 0, "v": 0, "hi": [0, 0], "ho": [0.3, 0]}, {"t": 1, "v": 0.15, "hi": [-0.2, -0.05], "ho": [0, 0]}]}}
        on = {"on": True, "style": "slide", "order": "same"}
        self.compare({"exit": {**on, "dir": "l"}})                                    # the title's way out
        self.compare({"exit": on, "exitCurves": gone})                                # the title's own drawing
        self.compare({"exit": on, "exitCurves": gone},                                # words' own, over the title's
                     over={"exitAnim": {"3#1": "reverse", "secrets#1": "fade"}, "exitDir": {"nobody#1": "r"},
                           "exitCurves": {"tells#1": gone}, "exitLen": {"you#1": 0.7}})

    def test_a_built_in_exit_drawn_as_it_plays_is_the_same_exit(self):
        """Editing an exit in Curves starts from the built-in played backwards (mirror):
        drawn as the word's own, it must move exactly as before - nothing jumps."""
        from pages.text_animator.titles import composer as C, timing as T

        def fake(t, font, size):
            return len(t) * size * 0.55
        for style in ("pop", "slide", "blur", "reverse"):
            doc = C.new_doc("3 secrets nobody tells you", "hormozi")
            doc.update(anim="slam", exit={"on": True, "style": style, "order": "same"})
            words = T.timing(doc, C.compose(doc, (1920, 1080), fake), 3.0)["words"]
            got = json.loads(_js(self.view.page(), f"""(() => {{
                TitlesMotion.setCurves({json.dumps(T.CURVES)});
                return JSON.stringify({json.dumps(words)}.map(w => {{
                    const ex = w.exit, base = TitlesMotion.mirror(ex.reverse ? (w.curve || TitlesMotion.curveOf(w.kind)) : TitlesMotion.curveOf(ex.kind));
                    const drawn = {{...w, exit: {{...ex, curve: base}}}};
                    return [0.1, 0.35, 0.6, 0.9].map(p => [TitlesMotion.state(w, ex.at + p * ex.len), TitlesMotion.state(drawn, ex.at + p * ex.len)]);
                }}));
            }})()"""))
            for pairs in got:
                for a, b in pairs:
                    for channel in a:
                        self.assertAlmostEqual(a[channel], b[channel], places=9, msg=(style, channel))

    def test_the_kinetic_camera_matches(self):
        """titles_motion.js's camera (cameraAt, screenBox, cameraOf at rest) is kinetic.py's."""
        from pages.text_animator.titles import composer as C, kinetic as K, timing as T

        def fake(t, font, size):
            return len(t) * size * 0.55
        for which in K.CAMERAS:
            doc = C.new_doc("Stop scrolling this one trick changes everything", "hormozi")
            doc.update(arrange="kinetic", camera=which)
            layout = C.compose(doc, (1920, 1080), fake)
            clock = T.timing(doc, layout, 8.0)
            times = [i * 7.0 / 70 for i in range(71)]
            want = [K.screen_boxes(layout["boxes"], clock["camera"], t, layout["frame"]) for t in times]
            rest = K.at_rest(layout)["boxes"]
            got = json.loads(_js(self.view.page(), f"""(() => {{
                const c = {{kinetic: {json.dumps(layout["kinetic"])}, camera: {json.dumps(clock["camera"])},
                           frame: {json.dumps(layout["frame"])}, boxes: {json.dumps(layout["boxes"])}}};
                return JSON.stringify({{at: {json.dumps(times)}.map(t => TitlesMotion.onScreen({{...c, t}})),
                                        rest: TitlesMotion.onScreen({{...c, t: null}})}});
            }})()"""))
            for t, ws, gs in zip(times, want, got["at"]):
                for a, b in zip(ws, gs):
                    for key in ("x", "y", "w", "h", "size", "baseline", "rot", "track", "frameRot"):
                        self.assertAlmostEqual(b[key], a[key], places=6, msg=(which, round(t, 2), a["id"], key))
            for a, b in zip(rest, got["rest"]):
                for key in ("x", "y", "size", "rot"):
                    self.assertAlmostEqual(b[key], a[key], places=6, msg=(which, "rest", a["id"], key))

    def test_copy_at_matches(self):
        from pages.text_animator.titles import looks as L, timing as T
        group = L.role_style(L.get("neon_extrude"), "key")["copies"][0]
        cases = [(k, s / 10, p / 4) for k in range(1, 9) for s in range(11) for p in range(5)]
        got = json.loads(_js(self.view.page(), f"JSON.stringify({json.dumps(cases)}.map(([k, s, p]) => TitlesMotion.copyAt({json.dumps(group)}, k, s, p)))"))
        for (k, s, p), g in zip(cases, got):
            want = T.copy_at(group, k, s, p)
            self.assertAlmostEqual(g[0], want[0], places=9)
            self.assertAlmostEqual(g[1], want[1], places=9)


class _Settings(dict):
    def save(self):
        pass


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TitlesTabTests(unittest.TestCase):
    """The real page, its bridge shaped like core/web_page.py's, the real studio."""

    def setUp(self):
        from core.theme import get_theme_tokens
        from core.web_theme import web_theme
        from pages.text_animator.titles import measure, studio
        self.app = QApplication.instance() or QApplication([])
        self.addCleanup(measure.unload_bundled)
        test = self
        self.console = []

        class Page:
            settings = _Settings()

            def emit(self, name, payload=None):
                bridge.event.emit(name, json.dumps(payload))

        class Bridge(QObject):
            event = Signal(str, str)

            @Slot()
            def ready(self):
                page.emit("theme", web_theme("Resolve", None, get_theme_tokens("Resolve", None)))
                test.studio.view(gallery=True)

            @Slot(str, str)
            def send(self, name, payload):
                data = json.loads(payload) if payload else None
                handler = {"ti_change": test.studio.on_change, "ti_ui": test.studio.on_ui}.get(name)
                if handler:
                    QTimer.singleShot(0, lambda: handler(data))

        class WebPage(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, message, line, source):
                test.console.append(f"{os.path.basename(source)}:{line} {message}")

        page, bridge = Page(), Bridge()
        self.page = page
        self.studio = studio.Studio(page)
        self.addCleanup(self.studio.close)
        self.view = QWebEngineView()
        self.addCleanup(self.view.deleteLater)
        self.web = WebPage(self.view)
        self.view.setPage(self.web)
        channel = QWebChannel(self.web)
        channel.registerObject("backend", bridge)
        self.web.setWebChannel(channel)
        self.view.resize(1400, 900)
        self.view.show()
        self.web.load(QUrl.fromLocalFile(os.path.join(WEB, "index.html")))
        for _ in range(200):
            if _js(self.web, "typeof TI !== 'undefined' && !!TI.view"):
                break
            _wait(50)
        _js(self.web, "goTab('titles')")
        _wait(300)

    def test_the_tab_draws_the_title_and_a_drag_comes_back_as_a_nudge(self):
        state = json.loads(_js(self.web, """JSON.stringify({
            chips: document.querySelectorAll('#ti-words [data-chip]').length,
            paths: document.querySelectorAll('#ti-title path').length,
            words: document.querySelectorAll('#ti-over [data-hit=word]').length,
            lanes: document.querySelectorAll('#ti-rows [data-bar]').length})"""))
        self.assertEqual(state["chips"], 5)
        self.assertEqual(state["words"], 5)
        self.assertEqual(state["lanes"], 5)
        self.assertGreaterEqual(state["paths"], 10)          # every word's outline and fill
        _js(self.web, """(() => {
            Element.prototype.setPointerCapture = function () {};
            const over = document.getElementById('ti-over');
            const w = over.querySelector('[data-id="nobody#1"]');
            const r = w.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
            const fire = (el, type, px, py) => el.dispatchEvent(new PointerEvent(type, {bubbles: true, clientX: px, clientY: py, pointerId: 1, button: 0}));
            fire(w, 'pointerdown', x, y); fire(over, 'pointerup', x, y);
            const w2 = over.querySelector('[data-id="nobody#1"]');
            fire(w2, 'pointerdown', x, y);
            for (let i = 1; i <= 5; i++) fire(over, 'pointermove', x + 6 * i, y - 4 * i);
            fire(over, 'pointerup', x + 30, y - 20);
        })()""")
        for _ in range(60):
            if self.studio.title["over"]["offset"].get("nobody#1"):
                break
            _wait(50)
        dx, dy = self.studio.title["over"]["offset"]["nobody#1"]
        self.assertGreater(dx, 0)
        self.assertLess(dy, 0)
        self.assertEqual(_js(self.web, "TI.sel.join()"), "nobody#1")
        self.assertEqual([c for c in self.console if "rror" in c], [])

    def _drag(self, selector, at, dx):
        """A pointer drag on an element in the lanes: at "middle", "left" or "right"."""
        _js(self.web, f"""(() => {{
            Element.prototype.setPointerCapture = function () {{}};
            const rows = document.getElementById('ti-rows'), el = rows.querySelector('{selector}');
            const r = el.getBoundingClientRect(), y = r.top + r.height / 2;
            const x = {{middle: r.left + r.width / 2, left: r.left + 2, right: r.right - 2}}['{at}'];
            const fire = (node, type, px) => node.dispatchEvent(new PointerEvent(type, {{bubbles: true, clientX: px, clientY: y, pointerId: 1, button: 0}}));
            fire(el, 'pointerdown', x);
            for (let i = 1; i <= 5; i++) fire(rows, 'pointermove', x + {dx} * i / 5);
            fire(rows, 'pointerup', x + {dx});
        }})()""")

    def test_an_exit_moves_and_lengthens_in_the_lanes(self):
        self.studio.on_change({"change": {"exit": {"on": True, "style": "fade"}}})
        self._until(lambda: _js(self.web, "document.querySelectorAll('#ti-rows [data-out]').length") == 5)
        before = _js(self.web, "JSON.stringify(TI.view.clock.words[2].exit)")
        before = json.loads(before)
        self._drag('[data-out="nobody#1"]', "middle", -40)
        self._until(lambda: "nobody#1" in self.studio.title["exitTimes"])
        moved = self.studio.title["exitTimes"]["nobody#1"]
        self.assertLess(moved, before["at"] - 0.05)
        self.assertEqual(self.studio.title["over"]["exitLen"], {})       # moved, not lengthened
        # Its left edge: sooner, its end where it was - so longer.
        _wait(200)
        self._drag('[data-out="nobody#1"]', "left", -30)
        self._until(lambda: "nobody#1" in self.studio.title["over"]["exitLen"])
        ex = self.studio.title
        self.assertGreater(ex["over"]["exitLen"]["nobody#1"], before["len"])
        self.assertAlmostEqual(ex["exitTimes"]["nobody#1"] + ex["over"]["exitLen"]["nobody#1"], moved + before["len"], places=3)
        # The other words' exits stay where they were.
        others = json.loads(_js(self.web, "JSON.stringify(TI.view.clock.words.filter(w => w.id !== 'nobody#1').map(w => w.exit.at))"))
        self.assertTrue(all(abs(a - before["at"]) < 1.0 for a in others))
        _js(self.web, "document.getElementById('ti-undo').click()")
        self._until(lambda: not self.studio.title["over"]["exitLen"])
        self.assertEqual(self.studio.title["over"]["exitLen"], {})
        self.assertAlmostEqual(self.studio.title["exitTimes"]["nobody#1"], moved)

    def test_a_words_in_and_out_typed_in_the_inspector(self):
        self.studio.on_change({"change": {"exit": {"on": True, "style": "fade"}}})
        self._until(lambda: _js(self.web, "TI.title.exit.on"))
        _js(self.web, "tiSelect(['nobody#1'])")
        _wait(300)
        heads = _js(self.web, "[...document.querySelectorAll('#ti-insp .ti-io')].map(n => n.textContent).join()")
        self.assertEqual(heads, "In,Out")
        for act, text in (("wstart", "0.8"), ("wlen", "0.5"), ("xstart", "2"), ("xlen", "0.6")):
            _js(self.web, f"(() => {{ const n = document.querySelector('#ti-insp [data-act={act}]'); n.value = '{text}'; n.dispatchEvent(new Event('change', {{bubbles: true}})); }})()")
            _wait(250)
        t = self.studio.title
        self.assertEqual((t["times"], t["over"]["len"], t["exitTimes"], t["over"]["exitLen"]),
                         ({"nobody#1": 0.8}, {"nobody#1": 0.5}, {"nobody#1": 2.0}, {"nobody#1": 0.6}))
        _js(self.web, "document.querySelector('#ti-insp [data-reset=exitTimes]').click()")
        self._until(lambda: not self.studio.title["exitTimes"])
        self.assertEqual(self.studio.title["exitTimes"], {})
        # No exit on the title: the word offers one.
        self.studio.on_change({"change": {"exit": {"on": False}}})
        self._until(lambda: not _js(self.web, "TI.title.exit.on"))
        _js(self.web, "document.querySelector('#ti-insp [data-act=exiton]').click()")
        self._until(lambda: self.studio.title["exit"]["on"])
        self.assertTrue(self.studio.title["exit"]["on"])

    def _cv(self, kind, x, y, mods=""):
        """A pointer event on Curves at (x, y) in its own pixels."""
        _js(self.web, f"""(() => {{
            Element.prototype.setPointerCapture = function () {{}};
            const svg = document.getElementById('ti-curves'), r = svg.getBoundingClientRect();
            const at = document.elementFromPoint(r.left + {x}, r.top + {y}) || svg;
            const el = '{kind}' === 'pointerdown' ? at : svg;
            el.dispatchEvent(new PointerEvent('{kind}', {{bubbles: true, clientX: r.left + {x}, clientY: r.top + {y}, pointerId: 1, button: 0,
                                                          shiftKey: {str('shift' in mods).lower()}, ctrlKey: {str('ctrl' in mods).lower()}}}));
        }})()""")
        _wait(40)

    def _key_xy(self, ch, i):
        k = json.loads(_js(self.web, f"JSON.stringify(tiCvKeys(tiCurveShown(), '{ch}')[{i}])"))
        return _js(self.web, f"TI.cv.X({k['t']})"), _js(self.web, f"TI.cv.Y('{ch}', {k['v']})")

    def test_curves_select_several_keys_and_move_them_together(self):
        _js(self.web, "TI.ch = 'scale'; tiCurves()")
        _wait(100)
        # A key dragged a long way stays under the pointer (the scale doesn't refit under it).
        x, y = self._key_xy("scale", 1)
        self._cv("pointerdown", x, y)
        for step in range(1, 11):
            self._cv("pointermove", x, y - 10 * step)
            # Drawn on the next frame (tiCvApply), which a slow runner's 40 ms can miss.
            self._until(lambda: abs(self._key_xy("scale", 1)[1] - (y - 10 * step)) <= 0.5)
            self.assertAlmostEqual(self._key_xy("scale", 1)[1], y - 10 * step, delta=0.5)
        self._cv("pointerup", x, y - 100)
        self._until(lambda: self.studio.title["curves"] is not None)
        # Shift-click a second key (another channel's): both selected, dragged together.
        _wait(300)
        self._cv("pointerdown", *self._key_xy("scale", 2))
        self._cv("pointerup", *self._key_xy("scale", 2))
        self._cv("pointerdown", *self._key_xy("alpha", 1), mods="shift")
        self._cv("pointerup", *self._key_xy("alpha", 1))
        self.assertEqual(json.loads(_js(self.web, "JSON.stringify(TI.cvSel)")), {"scale": [2], "alpha": [1]})
        before = {ch: json.loads(_js(self.web, f"JSON.stringify(tiCurveShown().ch.{ch}[{i}].t)")) for ch, i in (("scale", 2), ("alpha", 1))}
        x, y = self._key_xy("scale", 2)
        self._cv("pointerdown", x, y)
        self._cv("pointermove", x - 8, y)
        self._cv("pointerup", x - 8, y)
        self._until(lambda: abs(self.studio.title["curves"]["ch"]["scale"][2]["t"] - before["scale"]) > 1e-3)
        c = self.studio.title["curves"]["ch"]
        self.assertAlmostEqual(c["scale"][2]["t"] - before["scale"], c["alpha"][1]["t"] - before["alpha"], places=6)
        # Delete: both go.
        n = {ch: len(c[ch]) for ch in ("scale", "alpha")}
        _js(self.web, "document.getElementById('ti-curves').focus(); window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Delete'}))")
        self._until(lambda: len(self.studio.title["curves"]["ch"]["scale"]) == n["scale"] - 1)
        c = self.studio.title["curves"]["ch"]
        self.assertEqual((len(c["scale"]), len(c["alpha"])), (n["scale"] - 1, n["alpha"] - 1))

    def test_a_key_clicked_doesnt_keep_its_curve_for_the_next_word(self):
        _js(self.web, "TI.ch = 'scale'; tiCurves()")
        _wait(100)
        x, y = self._key_xy("scale", 0)
        self._cv("pointerdown", x, y)
        self._cv("pointerup", x, y)
        self.assertFalse(_js(self.web, "!!(TI.local && TI.local.curve)"))
        self.studio.on_change({"change": {"over": {**self.studio.title["over"], "anim": {"nobody#1": "slide"}}}})
        _wait(300)
        _js(self.web, "tiSelect(['nobody#1'])")
        _wait(200)
        self.assertIn("Slide", _js(self.web, "document.getElementById('ti-cv-sub').textContent"))

    def test_out_has_every_control_in_has(self):
        click = lambda sel: _js(self.web, f"document.querySelector('{sel}').click()") or _wait(250)
        click('#ti-insp [data-act="exit"][data-v="slide"]')
        self._until(lambda: self.studio.title["exit"]["on"])
        click('#ti-insp [data-act="xdir"][data-v="r"]')
        self._until(lambda: self.studio.title["exit"]["dir"] == "r")
        _js(self.web, "tiSelect(['nobody#1'])")
        _wait(250)
        click('#ti-insp [data-act="wxanim"][data-v="reverse"]')
        self._until(lambda: self.studio.title["over"]["exitAnim"])
        self.assertEqual(self.studio.title["over"]["exitAnim"], {"nobody#1": "reverse"})
        click('#ti-insp [data-act="wxanim"][data-v="slide"]')
        click('#ti-insp [data-act="wxdir"][data-v="d"]')
        self._until(lambda: self.studio.title["over"]["exitDir"])
        self.assertEqual(self.studio.title["over"]["exitDir"], {"nobody#1": "d"})
        click('#ti-insp [data-act="editcurve"][data-v="out"]')
        self.assertEqual(_js(self.web, "TI.cvIo"), "out")
        # A key moved a little in Out: the word's own exit curve - and the scale stays put.
        _js(self.web, "TI.ch = 'alpha'; tiCurves()")
        rng = _js(self.web, "JSON.stringify(TI.cvView.rng.alpha)")
        x, y = self._key_xy("alpha", 0)
        self._cv("pointerdown", x, y)
        self._cv("pointermove", x, y + 4)
        self._cv("pointerup", x, y + 4)
        self._until(lambda: "nobody#1" in self.studio.title["over"]["exitCurves"])
        self.assertIn("nobody#1", self.studio.title["over"]["exitCurves"])
        self.assertEqual(_js(self.web, "JSON.stringify(TI.cvView.rng.alpha)"), rng)
        click('#ti-insp [data-reset="outAll"]')
        self._until(lambda: not self.studio.title["over"]["exitCurves"])
        self.assertEqual((self.studio.title["over"]["exitCurves"], self.studio.title["over"]["exitDir"]), ({}, {}))

    def test_a_click_outside_lets_the_words_go(self):
        _js(self.web, "tiSelect(['nobody#1'])")
        _wait(200)
        press = "(sel => { const n = document.querySelector(sel), r = n.getBoundingClientRect(); n.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, button: 0, clientX: r.left + 2, clientY: r.top + 2})); })"
        _js(self.web, f"{press}('#ti-insp .ti-ihead')")
        self.assertEqual(_js(self.web, "TI.sel.join()"), "nobody#1")          # the inspector keeps it
        _js(self.web, f"{press}('#ti-stage-wrap')")
        _wait(100)
        self.assertEqual(_js(self.web, "TI.sel.join()"), "")

    def test_reset_all_asks_first_unless_told_not_to(self):
        self.studio.on_change({"change": {"seconds": 5.0, "times": {"tells#1": 0.9}}})
        self._until(lambda: _js(self.web, "TI.title.seconds") == 5.0)
        _js(self.web, "document.getElementById('ti-reset-all').click()")
        _wait(100)
        self.assertTrue(_js(self.web, "!!document.getElementById('ti-reset-yes')"))
        self.assertEqual(self.studio.title["seconds"], 5.0)                 # nothing yet: it asked
        _js(self.web, "document.getElementById('ti-reset-ask').click(); document.getElementById('ti-reset-yes').click()")
        self._until(lambda: self.studio.title["seconds"] == 3.0)
        self.assertEqual((self.studio.title["seconds"], self.studio.title["times"]), (3.0, {}))
        self.assertEqual(self.studio.title["text"], "3 secrets nobody tells you")
        self._until(lambda: (self.page.settings.get("titles_ui") or {}).get("askReset") is False)
        self.assertFalse(self.page.settings["titles_ui"]["askReset"])
        # Told not to ask: straight away - and Undo brings it back.
        self.studio.on_change({"change": {"seconds": 6.0}})
        self._until(lambda: _js(self.web, "TI.title.seconds") == 6.0)
        _js(self.web, "document.getElementById('ti-reset-all').click()")
        self.assertFalse(_js(self.web, "!!document.getElementById('ti-reset-yes')"))
        self._until(lambda: self.studio.title["seconds"] == 3.0)
        self.assertEqual(self.studio.title["seconds"], 3.0)
        _js(self.web, "document.getElementById('ti-undo').click()")
        self._until(lambda: self.studio.title["seconds"] == 6.0)
        self.assertEqual(self.studio.title["seconds"], 6.0)
        self.assertEqual([c for c in self.console if "rror" in c], [])

    def test_the_length_steps_and_takes_a_typed_value(self):
        _js(self.web, "document.querySelector('.ti-dock .ti-step[data-len=\"1\"]').click()")
        for _ in range(40):
            if self.studio.title["seconds"] != 3.0:
                break
            _wait(50)
        self.assertEqual(self.studio.title["seconds"], 3.5)
        _js(self.web, "const n = document.querySelector('.ti-dock .ti-len-v'); n.value = '6.25'; n.dispatchEvent(new Event('change'))")
        for _ in range(40):
            if self.studio.title["seconds"] != 3.5:
                break
            _wait(50)
        self.assertEqual(self.studio.title["seconds"], 6.3)

    def test_kinetic_marks_between_the_words_and_a_turned_word_dragged(self):
        """Kinetic: the words strip holds each word's mark (click: the next), the title
        is made long enough for its blocks, and a word in a block the camera shows turned
        is nudged along its own block - the drag turned back."""
        self.studio.on_change({"change": {"text": "Stop scrolling this one trick changes everything", "arrange": "kinetic"}})
        self._until(lambda: _js(self.web, "document.querySelectorAll('#ti-words [data-turn]').length") == 6)
        self.assertGreater(self.studio.title["seconds"], 3.0)
        self.assertEqual(_js(self.web, "document.querySelectorAll('#ti-over [data-hit=block]').length"), 0)
        marks = json.loads(_js(self.web, "JSON.stringify([...document.querySelectorAll('#ti-words [data-turn]')].map(b => b.dataset.mark))"))
        self.assertEqual(marks[0], "cw")                        # Buddy's: "scrolling" turns
        # At the end, the camera settled: "scrolling" is a quarter turn on the frame.
        _js(self.web, "TI.T = TI.title.seconds; tiRenderAll()")
        rot = _js(self.web, "tiScreen()[tiIndex('scrolling#1')].rot")
        self.assertAlmostEqual(abs(rot), 90.0, places=3)
        _js(self.web, """(() => {
            Element.prototype.setPointerCapture = function () {};
            const over = document.getElementById('ti-over');
            const w = over.querySelector('[data-id="scrolling#1"]');
            const r = w.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
            const fire = (el, type, px, py) => el.dispatchEvent(new PointerEvent(type, {bubbles: true, clientX: px, clientY: py, pointerId: 1, button: 0}));
            fire(w, 'pointerdown', x, y); fire(over, 'pointerup', x, y);
            const w2 = over.querySelector('[data-id="scrolling#1"]');
            fire(w2, 'pointerdown', x, y);
            for (let i = 1; i <= 5; i++) fire(over, 'pointermove', x + 8 * i, y);
            fire(over, 'pointerup', x + 40, y);
        })()""")
        self._until(lambda: self.studio.title["over"]["offset"].get("scrolling#1"))
        ox, oy = self.studio.title["over"]["offset"]["scrolling#1"]
        # Right on the frame is across a block turned a quarter: along its own up or down.
        self.assertLess(abs(ox), abs(oy) * 0.05 + 1e-6)
        self.assertGreater(abs(oy), 0.0)
        self.assertIn("Kinetic", _js(self.web, "document.getElementById('ti-insp').textContent"))
        # A gap clicked: the next mark ("this" is Buddy's new block underneath: then clockwise).
        _js(self.web, "document.querySelector('#ti-words [data-turn=\"this#1\"]').click()")
        self._until(lambda: "this#1" in self.studio.title["turns"])
        self.assertEqual(self.studio.title["turns"]["this#1"], "cw")
        self.assertEqual([c for c in self.console if "rror" in c], [])

    def test_kinetic_camera_eases_into_a_new_framing(self):
        """A word nudged: the camera starts from where it was, and lands on the new
        framing once the ease is over - no jump."""
        self.studio.on_change({"change": {"text": "Stop scrolling this one trick changes everything", "arrange": "kinetic"}})
        self._until(lambda: _js(self.web, "!!(TI.view && TI.view.kinetic)"))
        _js(self.web, "TI.T = TI.title.seconds; tiRenderAll()")
        _wait(500)
        before = json.loads(_js(self.web, "JSON.stringify(tiCamera())"))
        over = {**self.studio.title["over"], "offset": {"everything#1": [0.5, 0.0]}}
        self.studio.on_change({"change": {"over": over}})
        _wait(30)
        soon = json.loads(_js(self.web, "JSON.stringify({cam: tiCamera(), easing: !!TI.camEase})"))
        self.assertTrue(soon["easing"])
        _wait(600)
        done = json.loads(_js(self.web, """JSON.stringify({cam: tiCamera(), easing: !!TI.camEase,
            want: TitlesMotion.cameraOf({kinetic: TI.view.kinetic, camera: TI.view.clock.camera, t: TI.T, frame: TI.view.frame})})"""))
        self.assertFalse(done["easing"])
        for k in ("cx", "cy", "z", "angle"):
            self.assertAlmostEqual(done["cam"][k], done["want"][k], places=6)
        self.assertGreater(abs(done["cam"]["cx"] - before["cx"]) + abs(done["cam"]["cy"] - before["cy"]), 1.0)
        # Partway, it was between the two.
        frac = (soon["cam"]["cx"] - before["cx"]) / (done["cam"]["cx"] - before["cx"]) if done["cam"]["cx"] != before["cx"] else 0
        self.assertLess(frac, 0.5)
        self.assertEqual([c for c in self.console if "rror" in c], [])

    def _until(self, check, tries=40):
        for _ in range(tries):
            if check():
                return
            _wait(50)

    def test_a_sliders_value_double_clicked_takes_a_typed_one(self):
        val = "[...document.querySelectorAll('#ti-insp .ti-val')].find(v => v.previousElementSibling.dataset.act === 'seconds')"
        _js(self.web, f"{val}.dispatchEvent(new MouseEvent('dblclick', {{bubbles: true}}))")
        self.assertTrue(_js(self.web, "document.activeElement.classList.contains('ti-typein')"))
        _js(self.web, "const b = document.activeElement; b.value = '4.5'; b.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}))")
        self._until(lambda: self.studio.title["seconds"] != 3.0)
        self.assertEqual(self.studio.title["seconds"], 4.5)
        # Esc leaves it be.
        _js(self.web, f"{val}.dispatchEvent(new MouseEvent('dblclick', {{bubbles: true}}))")
        _js(self.web, "const b = document.activeElement; b.value = '9'; b.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        _wait(300)
        self.assertEqual(self.studio.title["seconds"], 4.5)

    def test_times_in_frames(self):
        fps = _js(self.web, "tiFps()")
        _js(self.web, "document.querySelector('#ti-units [data-v=f]').click()")
        self._until(lambda: (self.page.settings.get("titles_ui") or {}).get("units") == "f")
        self.assertEqual(self.page.settings["titles_ui"]["units"], "f")
        shown = json.loads(_js(self.web, """JSON.stringify({
            len: document.querySelector('.ti-dock .ti-len-v').value,
            unit: document.querySelector('.ti-dock .ti-len-u').textContent,
            ruler: [...document.querySelectorAll('#ti-ruler b')].map(b => b.textContent)})"""))
        self.assertEqual((shown["len"], shown["unit"]), (str(round(3 * fps)), "f"))
        self.assertIn(str(round(fps)), shown["ruler"])                 # a second's frame is numbered
        _js(self.web, "const n = document.querySelector('.ti-dock .ti-len-v'); n.value = '50'; n.dispatchEvent(new Event('change'))")
        self._until(lambda: self.studio.title["seconds"] != 3.0)
        self.assertAlmostEqual(self.studio.title["seconds"] * fps, 50, places=6)
        _js(self.web, "document.querySelector('.ti-dock .ti-step[data-len=\"1\"]').click()")
        self._until(lambda: abs(self.studio.title["seconds"] * fps - 50) > 0.5)
        self.assertAlmostEqual(self.studio.title["seconds"] * fps, 51, places=6)

    def test_folding_the_dock_is_remembered(self):
        _js(self.web, "document.getElementById('ti-fold').click()")
        for _ in range(40):
            if self.page.settings.get("titles_ui"):
                break
            _wait(50)
        self.assertFalse(self.page.settings["titles_ui"]["dock"])
        self.assertTrue(_js(self.web, "document.getElementById('ti-dock').hidden && !document.getElementById('ti-strip').hidden"))


if __name__ == "__main__":
    unittest.main()
