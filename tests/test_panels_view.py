"""Panels' view (pages/panels/web/): the page opened on its own, offscreen, and handed
the state Python sends (made here from the panel's own modules, as page.py makes it).
What it draws, what it sends back, and its copy of the clock's rule (pose) - which must
say what clock.pose() says on every frame - are read back from it."""

import json
import os
import re
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401

from pages.panels import catalogue, clock, model as M, solve, styles

FRAME = (1920, 1080)
FPS = 24.0
ROOT = _paths.APP / "pages" / "panels"

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAVE_WEB = True
except ImportError:  # pragma: no cover - PySide6 without its web engine
    HAVE_WEB = False


def face(style):
    bold = "bold" in style.lower()
    return {"family": "Arial", "weight": 700 if bold else 400, "italic": False}


def file_url(path):
    from PySide6.QtCore import QUrl
    return QUrl.fromLocalFile(str(path)).toString()


def state_of(spec, showing=None, **more):
    """The state page.py sends for a panel (its _state), from the same modules."""
    from pages.interactions import comp as cursor_comp
    pages = [page["id"] for page in spec["pages"]]
    showing = showing or spec["start"]
    images = {box["id"]: file_url(box["image"]["path"]) for box in M.images(spec)}
    return {"connected": False, "timeline": "", "problem": "", "frame": list(FRAME), "fps": FPS, "spec": spec,
            "showing": showing,
            "solved": {pid: solve.solve(spec, pid) for pid in pages},
            "drawn": {pid: styles.drawn(spec, pid) for pid in pages},
            "chrome": styles.chrome(spec),
            "clock": clock.clock(spec, FPS, FRAME),
            "shelf": {"layouts": [{"id": lid, "group": entry["group"], "name": entry["name"], "tile": styles.tile(lid, spec)}
                                  for lid, entry in catalogue.LAYOUTS.items()],
                      "components": [{"id": cid, "name": entry["name"], "tile": styles.tile(cid, spec)}
                                     for cid, entry in catalogue.COMPONENTS.items()]},
            "face": face(spec["face"]), "bold_face": face(spec["bold_face"]),
            "kinds": [{"id": k, "name": catalogue.KIND_NAMES[k]} for k in catalogue.KINDS],
            "styles": [{"id": s, "name": catalogue.STYLE_NAMES[s]} for s in catalogue.STYLES],
            "accents": list(catalogue.ACCENTS),
            "cursors": cursor_comp.CURSOR_LOOKS, "cursor_dip": cursor_comp.CURSOR_DIP,
            "limits": {"width": [M.MIN_WIDTH, M.MAX_WIDTH], "seconds": [M.MIN_SECONDS, M.MAX_SECONDS],
                       "pages": M.MAX_PAGES, "rules": M.MAX_RULES},
            "replace": True, "adding": False, "can_undo": False, "can_redo": False,
            "is_template": spec == M.clean({}), "story": M.story(spec),
            "images": images,
            "animations": {box["id"]: M.animations(spec, box["id"]) for box in M.every(M.page_of(spec, showing)["root"])},
            **more}


def close(test, got, want, where="pose"):
    """got and want the same, numbers to within 1e-6."""
    if isinstance(want, dict):
        test.assertIsInstance(got, dict, where)
        test.assertEqual(sorted(got), sorted(want), where)
        for key in want:
            close(test, got[key], want[key], f"{where}.{key}")
    elif isinstance(want, (list, tuple)):
        test.assertEqual(len(got), len(want), where)
        for i, (a, b) in enumerate(zip(got, want)):
            close(test, a, b, f"{where}[{i}]")
    elif isinstance(want, bool) or not isinstance(want, (int, float)):
        test.assertEqual(got, want, where)
    else:
        test.assertAlmostEqual(float(got), float(want), delta=1e-6, msg=where)


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class ViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        from test_audio_assistant import qt_app
        cls.app = qt_app()
        if not isinstance(cls.app, QApplication):     # another file's test made a bare one
            raise unittest.SkipTest("a QCoreApplication was made first; a web view needs a QApplication")
        cls.story = M.clean({})                                # a new panel: Buddy moves the cursor
        cls.spec = M.clean({**cls.story, "auto": False})       # the same, the cursor its own: what the older tests are about

    def setUp(self):
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from shiboken6 import delete
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = home.name
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.view = QWebEngineView()
        self.addCleanup(delete, self.view)
        self.view.resize(1400, 900)
        loop, loaded = QEventLoop(), []
        self.view.loadFinished.connect(lambda ok: (loaded.append(ok), loop.quit()))
        self.view.setUrl(QUrl.fromLocalFile(str(ROOT / "web" / "index.html")))
        self.view.show()
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        self.assertEqual(loaded, [True])
        self.js("try { sessionStorage.clear(); } catch (e) {}"
                " window.__sent = []; Buddy.send = (name, data) => window.__sent.push([name, JSON.parse(JSON.stringify(data === undefined ? null : data))]);"
                " return true;")

    # ------------------------------------------------------------ helpers --

    def js(self, body):
        from PySide6.QtCore import QEventLoop, QTimer
        loop, result = QEventLoop(), []
        self.view.page().runJavaScript("JSON.stringify((() => {" + body + "})())", 0,
                                       lambda value: (result.append(value), loop.quit()))
        QTimer.singleShot(8000, loop.quit)
        loop.exec()
        self.assertTrue(result, "JavaScript timed out")
        return json.loads(result[0]) if result[0] is not None else None

    def wait(self, ms):
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def receive(self, name, payload):
        self.js(f"Buddy.receive({json.dumps(name)}, {json.dumps(payload)}); return true;")

    def show(self, spec=None, **more):
        spec = spec or self.spec
        self.state = state_of(spec, **more)
        self.receive("state", self.state)
        return self.state

    def sent(self, name=None, but=("pick",)):
        """What the view sent - all but its "pick"s (where a dropped picture goes), unless asked for."""
        sent = self.js("return window.__sent;")
        return [s for s in sent if (s[0] == name if name else s[0] not in but)]

    def forget(self):
        self.js("window.__sent.length = 0; return true;")

    def placed(self, page, found):
        return next(b for b in self.state["solved"][page]["boxes"] if found(b))

    def pointer(self, kind, x, y, buttons=0):
        """A pointer event on the stage at (x, y) in panel px."""
        self.js(f"const [cx, cy] = panelsView.toPage({x}, {y}); const c = panelsView.picture();"
                f" c.dispatchEvent(new PointerEvent({json.dumps(kind)}, {{clientX: cx, clientY: cy, button: 0, buttons: {buttons},"
                f" pointerId: 1, bubbles: true, isPrimary: true}})); return true;")

    def click_stage(self, x, y):
        self.pointer("pointerdown", x, y, 1)
        self.pointer("pointerup", x, y, 0)

    def middle(self, b):
        return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2

    def click(self, selector):
        self.js(f"document.querySelector({json.dumps(selector)}).click(); return true;")

    def hidden(self, selector):
        return self.js(f"const n = document.querySelector({json.dumps(selector)}); return !n || n.hidden || !n.getClientRects().length;")

    def rows(self):
        return self.js("return [...document.querySelectorAll('#layers .pn-layer')].map(r => [r.dataset.id,"
                       " r.getAttribute('aria-level'), r.textContent, r.getAttribute('aria-selected')]);")

    def row_drag(self, from_id, to_id, share, dx=0):
        """A Layers row dragged by the pointer onto another row, `share` of the way down it."""
        self.js(f"""
            const row = id => document.querySelector(`#layers .pn-layer[data-id="${{id}}"]`);
            const a = row({json.dumps(from_id)}).getBoundingClientRect(), b = row({json.dumps(to_id)}).getBoundingClientRect();
            const ev = (node, kind, x, y) => node.dispatchEvent(new PointerEvent(kind, {{clientX: x, clientY: y, button: 0,
                buttons: kind === 'pointerup' ? 0 : 1, pointerId: 7, bubbles: true, isPrimary: true}}));
            ev(row({json.dumps(from_id)}), 'pointerdown', a.left + 40, a.top + a.height / 2);
            ev(document.body, 'pointermove', a.left + 40, a.top + a.height / 2 + 6);
            ev(document.body, 'pointermove', b.left + 40 + {dx}, b.top + b.height * {share});
            window.__line = !document.querySelector('.pn-drop-line') ? null : !document.querySelector('.pn-drop-line').hidden;
            window.__lit = [...document.querySelectorAll('#layers .pn-drop-into')].map(r => r.dataset.id);
            ev(document.body, 'pointerup', b.left + 40 + {dx}, b.top + b.height * {share});
            return true;""")
        return self.js("return [window.__line, window.__lit];")

    def own(self, box_id):
        """Whether a box has something of its own beyond the defaults (Layers' dot)."""
        box = M.find(self.state["spec"], box_id)["box"]
        return bool(box["type"] or (box["arrive"] and box["arrive"]["kind"] != "none") or box["states"] or box["go"]
                    or any(r["box"] == box_id for r in self.state["spec"]["rules"]))

    # -------------------------------------------------------------- tests --

    def test_the_design_studio_tab_row_with_panels_chosen(self):
        tabs = self.js("return [...document.querySelectorAll('#graphics-tabs button')]"
                       ".map(b => [b.dataset.tab, b.textContent, b.getAttribute('aria-selected')]);")
        self.assertEqual(tabs, [["design", "Design", "false"], ["interactions", "Interactions", "false"],
                                ["panels", "Panels", "true"]])
        self.show()
        self.click("#graphics-tabs [data-tab=design]")
        self.assertEqual(self.sent(), [["switch_tab", {"tab": "design"}]])

    def test_nothing_is_sent_on_load(self):
        self.show()
        self.wait(150)
        self.assertEqual(self.sent(but=()), [])
        self.assertEqual(self.js("return panelsView.info().playing;"), False)

    def test_every_tile_has_a_picture(self):
        self.show()
        tiles = self.js("""
            return [...document.querySelectorAll('.pn-tile')].map(b => {
                const c = b.querySelector('canvas'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                let n = 0;
                for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
                return [b.closest('.pn-shelf-part').dataset.part, b.dataset.id, n, c.width];
            });""")
        layouts = [t for t in tiles if t[0] == "layouts"]
        components = [t for t in tiles if t[0] == "insert"]
        self.assertEqual([t[1] for t in layouts], list(catalogue.LAYOUTS))
        self.assertEqual([t[1] for t in components], list(catalogue.COMPONENTS))
        for part, tid, drawn, width in tiles:
            self.assertGreater(drawn, width, f"{part} {tid} is blank")
        groups = self.js("return [...document.querySelectorAll('.pn-shelf-group')].map(h => h.textContent);")
        self.assertEqual(groups, list(catalogue.GROUPS))

    def test_add_opens_the_shelf_over_the_stage_and_closes(self):
        self.show()
        self.assertTrue(self.hidden("#add-pop"))
        self.click("#add-open")
        self.assertFalse(self.hidden("#add-pop"))
        self.assertEqual(self.js("return document.getElementById('add-open').getAttribute('aria-expanded');"), "true")
        # On a page with something on it, the pieces first; then a whole page, in its groups.
        self.assertTrue(self.hidden("[data-part=layouts]"))
        self.assertFalse(self.hidden("[data-part=insert]"))
        self.click("#add-mode [data-value=layouts]")
        self.assertFalse(self.hidden("[data-part=layouts]"))
        self.assertTrue(self.hidden("[data-part=insert]"))
        self.click("#add-mode [data-value=insert]")
        # The field finds them by name.
        self.js("const f = document.getElementById('add-find'); f.value = 'chart'; f.dispatchEvent(new Event('input')); return true;")
        shown = self.js("return [...document.querySelectorAll('[data-part=insert] .pn-tile')].filter(b => !b.hidden).map(b => b.dataset.id);")
        self.assertEqual(shown, [cid for cid, entry in catalogue.COMPONENTS.items() if "chart" in entry["name"].lower()])
        self.js("const f = document.getElementById('add-find'); f.value = 'zzz'; f.dispatchEvent(new Event('input')); return true;")
        self.assertFalse(self.hidden("#no-tiles"))
        self.js("const f = document.getElementById('add-find'); f.value = ''; f.dispatchEvent(new Event('input')); return true;")
        # Escape closes it; so does a click anywhere else.
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); return true;")
        self.assertTrue(self.hidden("#add-pop"))
        self.click("#add-open")
        self.js("document.getElementById('layers').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})); return true;")
        self.assertTrue(self.hidden("#add-pop"))
        self.assertEqual(self.sent(), [])

    def test_a_layout_tile_makes_the_page_in_view_that_layout(self):
        self.show(showing="p2")
        self.click("#add-open")
        self.click("#add-mode [data-value=layouts]")
        self.click("[data-part=layouts] .pn-tile[data-id=pricing]")
        self.assertEqual(self.sent(), [["layout", {"page": "p2", "layout": "pricing"}]])
        self.assertTrue(self.hidden("#add-pop"))

    def test_a_component_tile_goes_into_the_picked_box(self):
        self.show()
        self.click("#add-open")
        self.click("#add-mode [data-value=insert]")
        self.click("[data-part=insert] .pn-tile[data-id=button]")
        self.assertEqual(self.sent(), [["insert", {"component": "button", "into": "", "at": -1}]])
        self.assertTrue(self.hidden("#add-pop"))
        self.forget()
        field = self.placed("p1", lambda b: b["role"] == "field")
        self.click_stage(*self.middle(field))
        self.click("#add-open")
        self.assertIn("Goes into", self.js("return document.getElementById('shelf-hint').textContent;"))
        self.click("[data-part=insert] .pn-tile[data-id=card]")
        self.assertEqual(self.sent(), [["insert", {"component": "card", "into": field["id"], "at": -1}]])

    def test_a_click_picks_a_box_and_the_trail_picks_what_its_in(self):
        self.show()
        button = self.placed("p1", lambda b: b["role"] == "button" and b["variant"] == "")
        x, y = self.middle(button)
        # The hit test is the solver's boxes, back to front: the last one hit wins.
        want = [b["id"] for b in self.state["solved"]["p1"]["boxes"]
                if b["x"] <= x <= b["x"] + b["w"] and b["y"] <= y <= b["y"] + b["h"]][-1]
        self.assertEqual(want, button["id"])
        self.assertEqual(self.js(f"return panelsView.hit({x}, {y});"), button["id"])
        # Nothing picked: no card. Picked: its card, by it on the stage.
        self.assertTrue(self.hidden("#pick-card"))
        self.click_stage(x, y)
        self.assertEqual(self.js("return panelsView.info().picked;"), button["id"])
        self.assertFalse(self.hidden("#pick-card"))
        self.js("document.getElementById('card-more').open = true; return true;")
        trail = self.js("return [...document.querySelectorAll('#trail button')].map(b => b.textContent);")
        self.assertEqual(trail, ["Window", "Button"])
        self.assertEqual(self.js("return document.getElementById('picked-name').textContent;"), "Button · Sign in")
        self.assertEqual(self.js("return document.getElementById('box-go').value;"), "p2")
        # The stage's pick marks its row in Layers.
        self.assertEqual([r[0] for r in self.rows() if r[3] == "true"], [button["id"]])
        self.click("#card-close")
        self.assertEqual(self.js("return panelsView.info().picked;"), "")
        self.assertTrue(self.hidden("#pick-card"))
        # Picking is the view's own - Python is only told where a dropped picture would go.
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.sent("pick"), [["pick", {"id": button["id"]}], ["pick", {"id": ""}]])
        # Delete takes the picked box out.
        self.click_stage(x, y)
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Delete', bubbles: true})); return true;")
        self.assertEqual(self.sent(), [["remove_box", {"id": button["id"]}]])

    def test_the_picked_box_is_changed_in_the_fields(self):
        self.show()
        button = self.placed("p1", lambda b: b["role"] == "button" and b["variant"] == "")
        self.click_stage(*self.middle(button))
        self.js("const f = document.getElementById('box-text'); f.value = 'Log in';"
                " f.dispatchEvent(new Event('change')); return true;")
        self.js("const s = document.getElementById('box-go'); s.value = 'p3'; s.dispatchEvent(new Event('change')); return true;")
        self.click("#box-w [data-value=fill]")
        self.assertEqual(self.sent(), [["box", {"id": button["id"], "key": "text", "value": "Log in"}],
                                       ["box", {"id": button["id"], "key": "go", "value": {"page": "p3", "with": ""}}],
                                       ["box", {"id": button["id"], "key": "w", "value": "fill"}]])

    def test_the_windows_settings_are_on_the_right_when_nothing_is_picked(self):
        self.show()
        self.js("const s = document.querySelector('select[data-key=kind]'); s.value = 'phone'; s.dispatchEvent(new Event('change')); return true;")
        self.js("const s = document.querySelector('select[data-key=style]'); s.value = 'glass'; s.dispatchEvent(new Event('change')); return true;")
        self.js("const s = document.querySelector('select[data-key=cursor]'); s.value = 'dot'; s.dispatchEvent(new Event('change')); return true;")
        self.click("#dark [data-value=false]")
        self.click("#build-on")
        self.assertEqual(self.sent(), [["set", {"key": "kind", "value": "phone"}], ["set", {"key": "style", "value": "glass"}],
                                       ["set", {"key": "cursor", "value": "dot"}], ["set", {"key": "dark", "value": False}],
                                       ["set", {"key": "build", "value": {**self.spec["build"], "on": False}}]])

    def test_the_layers_tree_is_the_page_in_view(self):
        self.show()
        rows = self.rows()

        def walk(box, depth, out):
            for child in box["children"]:
                out.append((child["id"], str(depth + 1)))
                if child["role"] == "section":
                    walk(child, depth + 1, out)
            return out
        self.assertEqual([(r[0], r[1]) for r in rows], walk(self.spec["pages"][0]["root"], 0, []))
        named = {r[0]: r[2] for r in rows}
        self.assertEqual(named["b2"], "Heading · Welcome back")
        self.assertEqual(named["b6"], "Button · Sign in")
        self.assertEqual(named["b4"], "Field · Email")
        self.assertEqual(named["b7"], "Row")
        self.assertEqual(self.js("return document.getElementById('layers').getAttribute('role');"), "tree")
        self.assertEqual(self.js("return document.querySelector('#layers [data-id=b7]').getAttribute('aria-expanded');"), "true")
        # A row with an animation of its own has a dot: the buttons answer the cursor.
        dotted = self.js("return [...document.querySelectorAll('#layers .pn-layer')].filter(r => r.querySelector('.pn-layer-dot')).map(r => r.dataset.id);")
        self.assertEqual(dotted, [r[0] for r in rows if self.own(r[0])])
        self.assertIn("b6", dotted)                     # a link
        self.assertNotIn("b11", dotted)                 # answering with the panel's answers is every button's
        # A container folds away.
        self.click("#layers [data-id=b7] .pn-layer-twist")
        self.assertEqual([r[0] for r in self.rows()], ["b2", "b3", "b4", "b5", "b6", "b7", "b11"])
        # Another page, its own rows.
        self.show(showing="p3")
        self.assertEqual(self.rows()[0][0], "b98")

    def test_layers_opens_the_top_level_and_names_containers_by_their_component(self):
        self.show(showing="p2")
        rows = self.rows()
        root = self.spec["pages"][1]["root"]
        want = []
        for child in root["children"]:
            want.append(child["id"])
            want += [c["id"] for c in child["children"]]     # only the top level is open at first
        self.assertEqual([r[0] for r in rows], want)
        self.assertLess(len(rows), 20)
        named = {r[0]: r[2] for r in rows}
        self.assertEqual(named["b13"], "Sidebar")
        self.assertEqual(named["b14"], "Overview")
        self.assertEqual(named["b33"], "Stat tiles")
        self.assertEqual(named["b29"], "Column")
        # Boxes of one name beside each other are told apart by their first words.
        self.click("#layers [data-id=b33] .pn-layer-twist")
        named = {r[0]: r[2] for r in self.rows()}
        self.assertEqual([named[i] for i in ("b34", "b38")], ["Stat · Sales", "Stat · Orders"])
        # Picking a box deep in opens what it's in; a state update keeps what was opened and closed.
        self.js("panelsView.pick('b70'); return true;")
        self.assertIn("b70", [r[0] for r in self.rows()])
        self.click("#layers [data-id=b13] .pn-layer-twist")
        opened = [r[0] for r in self.rows()]
        self.assertNotIn("b14", opened)
        self.show(M.clean({**self.spec, "accent": "#e4572e"}), showing="p2")
        self.assertEqual([r[0] for r in self.rows()], opened)
        # Each page keeps its own.
        self.show(showing="p1")
        self.show(showing="p2")
        self.assertEqual([r[0] for r in self.rows()], opened)

    def test_no_null_ever_reaches_the_page(self):
        for page in self.spec["pages"]:
            state = self.show(showing=page["id"])
            for box in state["solved"][page["id"]]["boxes"][1:]:
                self.js(f"panelsView.pick({json.dumps(box['id'])}); return true;")
                bad = self.js("""const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), out = [];
                    while (w.nextNode()) if (/null|undefined/.test(w.currentNode.nodeValue)) out.push(w.currentNode.nodeValue);
                    for (const n of document.querySelectorAll('[title], [aria-label]'))
                        for (const a of ['title', 'aria-label']) if (/null|undefined/.test(n.getAttribute(a) || '')) out.push(a + ': ' + n.getAttribute(a));
                    return out;""")
                self.assertEqual(bad, [], f"{page['id']} {box['id']}")

    def test_the_dock_has_lanes_only_for_what_happens(self):
        state = self.show(showing="p2")
        clk = state["clock"]

        def happens(i):
            rows = clk["amounts"].get(i)
            box = M.find(self.spec, i)["box"]
            return bool((rows and (max(rows["hover"]) > 0.5 or max(rows["press"]) > 0.5)) or any(clk["typed"].get(i, [0]))
                        or i in clk["draw"] or (box["arrive"] and box["arrive"]["kind"] != "none"))
        want = [b["id"] for b in state["solved"]["p2"]["boxes"] if b["parent"] and happens(b["id"])]
        lanes = self.js("return [...document.querySelectorAll('#lanes .pn-lane-pick')].map(n => n.dataset.id);")
        self.assertEqual(lanes, want[:8])
        self.assertNotIn("b14", lanes)                  # Overview answers, but the cursor never goes there
        # The picked layer's lanes show though nothing is in them yet.
        self.click("#layers [data-id=b14]")
        mine = self.js("return [...document.querySelectorAll('#lanes .dg-lane.pn-mine')].length;")
        self.assertEqual(mine, len([a for a in state["animations"]["b14"] if a["on"]]))

    def test_a_row_picks_and_renames_and_deletes(self):
        self.show()
        self.click("#layers [data-id=b3]")
        self.assertEqual(self.js("return panelsView.info().picked;"), "b3")
        words = M.find(self.spec, "b3")["box"]["text"]
        self.assertEqual(self.js("return document.getElementById('picked-name').textContent;"), f"Text · {words}")
        self.js("document.querySelector('#layers [data-id=b3]').dispatchEvent(new MouseEvent('dblclick', {bubbles: true})); return true;")
        self.js("const f = document.querySelector('#layers input'); f.value = 'Subtitle';"
                " f.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); return true;")
        self.assertEqual(self.sent(), [["box", {"id": "b3", "key": "name", "value": "Subtitle"}]])
        self.forget()
        # F2 renames the picked row too; Escape keeps the name.
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'F2', bubbles: true})); return true;")
        self.js("const f = document.querySelector('#layers input'); f.value = 'Nope';"
                " f.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); return true;")
        self.assertEqual(self.sent(), [])
        self.js("document.querySelector('#layers [data-id=b3]').focus();"
                " document.querySelector('#layers [data-id=b3]').dispatchEvent(new KeyboardEvent('keydown', {key: 'Delete', bubbles: true})); return true;")
        self.assertEqual(self.sent(), [["remove_box", {"id": "b3"}]])
        # The arrows walk the rows.
        self.show()
        self.click("#layers [data-id=b4]")
        self.js("document.querySelector('#layers [data-id=b4]').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true})); return true;")
        self.assertEqual(self.js("return panelsView.info().picked;"), "b5")

    def test_a_row_dragged_moves_its_box(self):
        self.show()
        self.click("#layers-open")
        self.assertFalse(self.hidden("#layers-pop"))
        # Down past the next one: into the same column, one place on (counted without it).
        line, lit = self.row_drag("b2", "b3", 0.9)
        self.assertEqual((line, lit), (True, []))
        self.assertEqual(self.sent(), [["move", {"id": "b2", "into": "b1", "at": 1}]])
        self.forget()
        # Onto the middle of a container's row: into it, at its end.
        line, lit = self.row_drag("b2", "b7", 0.5)
        self.assertEqual(lit, ["b7"])
        self.assertEqual(self.sent(), [["move", {"id": "b2", "into": "b7", "at": 3}]])
        self.forget()
        # Out of a container, to the top of the page.
        self.row_drag("b9", "b2", 0.1)
        self.assertEqual(self.sent(), [["move", {"id": "b9", "into": "b1", "at": 0}]])
        self.forget()
        # Never into itself or what's in it; and where it was is no move.
        line, lit = self.row_drag("b7", "b9", 0.5)
        self.assertEqual(lit, [])
        self.assertEqual(self.sent(), [])
        self.row_drag("b4", "b4", 0.2)
        self.assertEqual(self.sent(), [])
        # What Python does with it: the same order the line showed.
        self.assertEqual([c["id"] for c in M.with_move(self.spec, "b2", "b1", 1)["pages"][0]["root"]["children"]][:3],
                         ["b3", "b2", "b4"])

    def test_the_animations_of_a_text(self):
        self.show()
        self.click("#layers [data-id=b2]")
        rows = self.js("return [...document.querySelectorAll('.pn-anims [data-anim]')].filter(r => !r.hidden).map(r => r.dataset.anim);")
        self.assertEqual(rows, [a["id"] for a in self.state["animations"]["b2"]])
        self.assertEqual(rows, ["arrive", "type", "answer"])
        self.assertTrue(self.js("return document.getElementById('an-arrive').checked;"))
        self.click("#an-arrive")
        self.js("const s = document.getElementById('an-arrive-kind'); s.value = 'pop'; s.dispatchEvent(new Event('change')); return true;")
        self.click("#an-type")
        self.click("#box-reacts")
        self.assertEqual(self.sent(), [["box", {"id": "b2", "key": "arrive", "value": {"kind": "none"}}],
                                       ["box", {"id": "b2", "key": "arrive", "value": {"kind": "pop"}}],
                                       ["box", {"id": "b2", "key": "type", "value": {"speed": 18, "start": "page", "caret": True, "text": ""}}],
                                       ["box", {"id": "b2", "key": "reacts", "value": True}]])
        self.forget()
        # Typing on: its speed beside it, the rest behind Edit…; a text types its own words.
        typed = M.with_box(self.spec, "b2", "type", {"speed": 12, "start": "page", "caret": True, "text": ""})
        self.show(typed)
        self.assertFalse(self.hidden("#an-type-speed"))
        self.assertTrue(self.hidden("#an-type-text"))
        self.js("const f = document.getElementById('an-type-speed'); f.value = '30'; f.dispatchEvent(new Event('change')); return true;")
        self.click("#edit-typing")
        self.assertFalse(self.hidden("#typing-pop"))
        self.js("const s = document.getElementById('an-type-start'); s.value = 'click'; s.dispatchEvent(new Event('change')); return true;")
        want = M.find(typed, "b2")["box"]["type"]
        self.assertEqual(self.sent(), [["box", {"id": "b2", "key": "type", "value": {**want, "speed": 30}}],
                                       ["box", {"id": "b2", "key": "type", "value": {**want, "start": "click"}}]])

    def test_the_animations_of_a_field_and_a_chart(self):
        typed = M.with_box(self.spec, "b4", "type", {"speed": 12, "start": "click", "caret": True, "text": "tess@example.com"})
        self.show(typed)
        self.click("#layers [data-id=b4]")
        self.assertFalse(self.hidden("#an-type-text"))
        self.assertEqual(self.js("return document.getElementById('an-type-text').value;"), "tess@example.com")
        self.js("const f = document.getElementById('an-type-text'); f.value = 'sam@example.com'; f.dispatchEvent(new Event('change')); return true;")
        self.assertEqual(self.sent()[-1][1]["value"]["text"], "sam@example.com")
        # A chart draws on: always, no control.
        self.show(showing="p2")
        chart = self.placed("p2", lambda b: b["role"] == "chart")
        self.click_stage(*self.middle(chart))                         # the chart's card...
        self.click_stage(*self.middle(chart))                         # ...and then the chart in it
        self.assertEqual(self.js("return panelsView.info().picked;"), chart["id"])
        rows = self.js("return [...document.querySelectorAll('.pn-anims [data-anim]')].filter(r => !r.hidden).map(r => r.dataset.anim);")
        self.assertIn("draw", rows)
        self.assertTrue(self.js("const c = document.getElementById('an-draw'); return c.checked && c.disabled;"))
        self.assertFalse(self.hidden("#edit-chart"))

    def test_an_image_box_chooses_clears_and_is_drawn_from_its_file(self):
        from PySide6.QtGui import QColor, QImage
        spec = M.with_insert(self.spec, "image", "b1")
        image = next(b for b in M.boxes(spec) if b["role"] == "image")
        self.show(spec)
        self.click(f"#layers [data-id={image['id']}]")
        self.assertFalse(self.hidden("#image-row"))
        self.assertFalse(self.hidden("#image-choose"))
        self.assertTrue(self.hidden("#image-clear"))
        self.click("#image-choose")
        self.assertEqual(self.sent(), [["image", {"id": image["id"]}]])
        self.forget()
        # A double-click on the box on the stage asks too.
        box = self.placed("p1", lambda b: b["id"] == image["id"])
        self.js(f"const [cx, cy] = panelsView.toPage({box['x'] + box['w'] / 2}, {box['y'] + box['h'] / 2});"
                " panelsView.picture().dispatchEvent(new MouseEvent('dblclick', {clientX: cx, clientY: cy, bubbles: true})); return true;")
        self.assertEqual(self.sent(), [["image", {"id": image["id"]}]])
        self.forget()
        # With a picture: drawn from its file, cropped into the box; Remove image takes it off.
        path = os.path.join(self.home, "red.png")
        picture = QImage(80, 40, QImage.Format.Format_RGB32)
        picture.fill(QColor(220, 20, 20))
        self.assertTrue(picture.save(path))
        spec = M.with_image(spec, image["id"], path, [80, 40])
        state = self.show(spec)
        self.assertEqual(state["images"], {image["id"]: file_url(path)})
        prims = [p for p in state["drawn"]["p1"] if p["box"] == image["id"]]
        self.assertIn("image", [p["kind"] for p in prims])
        self.click(f"#layers [data-id={image['id']}]")
        self.assertFalse(self.hidden("#image-clear"))
        self.click("#image-clear")
        self.assertEqual(self.sent(), [["image_clear", {"id": image["id"]}]])
        self.click("#trail button")
        self.wait(300)
        lit = self.js(f"const [cx, cy] = panelsView.toPage({box['x'] + box['w'] / 2}, {box['y'] + box['h'] / 2});"
                      " const c = panelsView.picture(), r = c.getBoundingClientRect(), k = c.width / r.width;"
                      " return [...c.getContext('2d').getImageData(Math.round((cx - r.left) * k), Math.round((cy - r.top) * k), 1, 1).data];")
        self.assertGreater(lit[0], 180, lit)
        self.assertLess(lit[1], 60, lit)

    def test_typing_is_drawn_in_place_as_the_clock_says(self):
        spec = M.with_box(self.spec, "b2", "type", {"speed": 10, "start": "page", "caret": True, "text": ""})
        state = self.show(spec)
        clk = state["clock"]
        row = clk["typed"]["b2"]
        start = row.index(1)
        frames = [0, start, start + 3, start + 6, len(row) - 1]
        got = self.js(f"return {json.dumps(frames)}.map(f => panelsView.pose(null, f));")
        for f, mine in zip(frames, got):
            close(self, mine, clock.pose(spec, clk, f), f"frame {f}")
            if f < len(row) - 1:                   # (by the last frame the page has changed)
                self.assertEqual(mine["typed"]["b2"], row[f])
        # A line's letters show in place: the same start, only the first n drawn.
        line = next(p for p in state["drawn"]["p1"] if p["box"] == "b2" and p["kind"] == "text")
        self.assertEqual(line["type"][0], 0)
        size = line["size"]
        spots = [[line["x"] + size * 0.3, line["y"] - size * 0.3], [line["x"] + size * 4.2, line["y"] - size * 0.3]]
        prim = {**line, "x": line["x"] - (line["x"] - 4), "y": size * 1.5}
        spots = [[x - line["x"] + prim["x"], prim["y"] - size * 0.3] for x, _y in spots]
        lit = lambda shown: self.js(f"return panelsView.drawn({json.dumps(prim)}, 1, 400, 60, {json.dumps(spots)}, {json.dumps(shown)});")
        self.assertEqual([a > 0 for a in lit(2)], [True, False])
        self.assertEqual([a > 0 for a in lit(len(line["text"]))], [True, True])
        self.assertEqual([a > 0 for a in lit(0)], [False, False])
        # Not playing (Edit), the words show whole.
        self.assertEqual([a > 0 for a in lit(None)], [True, True])

    def test_a_lanes_name_picks_its_layer(self):
        self.show()
        names = self.js("return [...document.querySelectorAll('#lanes .dg-lane-name')].map(n => [n.textContent, n.dataset.id || '']);")
        self.assertEqual(names[:2], [["Cursor", ""], ["Page", ""]])
        self.assertIn(["Sign in", "b6"], names)
        self.click("#lanes .pn-lane-pick[data-id=b6]")
        self.assertEqual(self.js("return panelsView.info().picked;"), "b6")
        # The picked layer's own lanes come first, lit: one for each way it moves.
        mine = self.js("return [...document.querySelectorAll('#lanes .dg-lane.pn-mine .dg-lane-name > span:first-child')].map(n => n.textContent);")
        self.assertEqual(mine, ["Arrives", "Hover / Pressed"])
        lanes = self.js("return [...document.querySelectorAll('#lanes .dg-lane-name')].map(n => n.dataset.id || n.textContent);")
        self.assertEqual(lanes[:4], ["Cursor", "Page", "b6", "b6"])

    def test_window_or_in_the_frame(self):
        self.show()
        self.assertEqual(self.js("return panelsView.info().fit;"), "window")
        big = self.js("return panelsView.toPage(100, 0)[0] - panelsView.toPage(0, 0)[0];")
        self.click("#fit [data-value=frame]")
        self.assertEqual(self.js("return panelsView.info().fit;"), "frame")
        small = self.js("return panelsView.toPage(100, 0)[0] - panelsView.toPage(0, 0)[0];")
        self.assertGreater(big, small * 1.2)          # the window fills the stage, larger than on the frame
        # Trying it shows the frame, and a banner over the stage says what the stage is doing - with the way out.
        self.click("#fit [data-value=window]")
        self.assertTrue(self.hidden("#stage-banner"))
        self.click("#mode [data-value=try]")
        self.assertEqual(self.js("return panelsView.info().fit;"), "frame")
        self.assertEqual(self.js("return document.getElementById('banner-text').textContent;"),
                         "Your mouse is the cursor now. Nothing is kept.")
        self.click("#banner-done")
        self.assertEqual(self.js("return panelsView.info().mode;"), "edit")
        self.assertTrue(self.hidden("#stage-banner"))
        self.assertEqual(self.sent(), [])

    def test_the_timeline_in_detail_is_shown_from_more(self):
        self.show()
        # The story is what's under the stage; the lanes, Try it and Record are for who asks.
        self.assertTrue(self.hidden("#dock"))
        self.assertFalse(self.hidden("#play"))
        self.assertFalse(self.hidden("#pages"))
        self.click("#more-open")
        labels = self.js("return [...document.querySelectorAll('.menu-pop button')].map(b => b.textContent);")
        self.assertEqual(labels, ["Try it with your mouse", "Record a cursor of your own", "Let Buddy move the cursor again",
                                  "Show the window in the frame", "The timeline in detail", "Replace the panel Buddy put there before"])
        self.js("[...document.querySelectorAll('.menu-pop button')].find(b => b.textContent === 'The timeline in detail').click(); return true;")
        self.assertFalse(self.hidden("#lanes"))
        self.assertEqual(self.js("try { return [sessionStorage.getItem('buddy.panels.timeline'), panelsView.info().dock]; }"
                                 " catch (e) { return ['shown', true]; }"), ["shown", True])
        self.click("#dock-toggle")
        self.assertTrue(self.hidden("#lanes"))
        self.assertEqual(self.sent(), [])

    def test_the_pages_list(self):
        self.show()
        rows = self.js("return [...document.querySelectorAll('#pages .pn-page')].map(r => [r.dataset.page,"
                       " r.querySelector('.pn-page-name').textContent, r.getAttribute('aria-selected'),"
                       " !!r.querySelector('.pn-page-start')]);")
        self.assertEqual(rows, [["p1", "1Sign in", "true", True], ["p2", "2Dashboard", "false", False],
                                ["p3", "3Settings", "false", False]])
        # Each has a small picture of itself; over the stage is the one in view, by name.
        drawn = self.js("""
            return [...document.querySelectorAll('#pages canvas')].map(c => {
                const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                let n = 0;
                for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
                return n > c.width;
            });""")
        self.assertEqual(drawn, [True, True, True])
        self.assertEqual(self.js("return [document.getElementById('page-name').textContent, document.getElementById('page-count').textContent,"
                                 " document.getElementById('page-prev').disabled, document.getElementById('page-next').disabled];"),
                         ["Sign in", "1 / 3", True, False])
        self.js("document.querySelectorAll('#pages .pn-page')[1].click(); return true;")
        self.click("#page-add")
        self.click("#page-next")
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'PageDown', bubbles: true})); return true;")
        self.assertEqual(self.sent(), [["page_show", {"id": "p2"}], ["page_add", {}], ["page_show", {"id": "p2"}],
                                       ["page_show", {"id": "p2"}]])
        self.forget()
        self.js("document.querySelectorAll('#pages .pn-page')[2].dispatchEvent(new MouseEvent('dblclick', {bubbles: true})); return true;")
        self.js("const f = document.querySelector('#pages input'); f.value = 'Preferences';"
                " f.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); return true;")
        self.assertEqual(self.sent(), [["page_rename", {"id": "p3", "name": "Preferences"}]])
        self.forget()
        # The rest is in a page's menu: its "more" button, or a right-click.
        menu = lambda: self.js("return [...document.querySelectorAll('.menu-pop button')].map(b => [b.textContent, b.disabled]);")   # noqa: E731
        pick = lambda text: self.js("[...document.querySelectorAll('.menu-pop button')].find(b => b.textContent === "   # noqa: E731
                                    + json.dumps(text) + ").click(); return true;")
        self.js("document.querySelector('#pages .pn-page[data-page=p1] .pn-page-more').click(); return true;")
        self.assertEqual(menu(), [["Rename", False], ["Duplicate", False], ["Start the clip on this page", True],
                                  ["Move earlier", True], ["Move later", False], ["Remove", False]])
        pick("Duplicate")
        for label in ("Move later", "Remove"):
            self.js("document.querySelector('#pages .pn-page[data-page=p1]').dispatchEvent(new MouseEvent('contextmenu',"
                    " {bubbles: true, cancelable: true, clientX: 60, clientY: 80})); return true;")
            pick(label)
        self.js("document.querySelector('#pages .pn-page[data-page=p2] .pn-page-more').click(); return true;")
        pick("Start the clip on this page")
        self.assertEqual(self.sent(), [["page_copy", {"id": "p1"}], ["page_move", {"id": "p1", "at": 1}],
                                       ["page_remove", {"id": "p1"}], ["page_start", {"id": "p2"}]])
        # One page: it can't be removed.
        one = M.clean({**self.spec, "pages": self.spec["pages"][:1]})
        self.show(one)
        self.js("document.querySelector('#pages .pn-page-more').click(); return true;")
        self.assertEqual(menu()[-1], ["Remove", True])
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); return true;")

    def test_a_page_dragged_moves_it(self):
        self.show()

        def drag(from_id, to_id, share):
            self.js(f"""
                const row = id => document.querySelector(`#pages .pn-page[data-page="${{id}}"]`);
                const a = row({json.dumps(from_id)}).getBoundingClientRect(), b = row({json.dumps(to_id)}).getBoundingClientRect();
                const ev = (node, kind, x, y) => node.dispatchEvent(new PointerEvent(kind, {{clientX: x, clientY: y, button: 0,
                    buttons: kind === 'pointerup' ? 0 : 1, pointerId: 9, bubbles: true, isPrimary: true}}));
                ev(row({json.dumps(from_id)}), 'pointerdown', a.left + a.width / 2, a.top + 20);
                ev(document.body, 'pointermove', a.left + a.width / 2 + 12, a.top + 20);
                ev(document.body, 'pointermove', b.left + b.width * {share}, b.top + 20);
                ev(document.body, 'pointerup', b.left + b.width * {share}, b.top + 20);
                return true;""")
        drag("p1", "p3", 0.9)                                        # past the last: the end (counted without it)
        self.assertEqual(self.sent(), [["page_move", {"id": "p1", "at": 2}]])
        self.forget()
        drag("p3", "p1", 0.1)
        self.assertEqual(self.sent(), [["page_move", {"id": "p3", "at": 0}]])
        self.forget()
        drag("p2", "p3", 0.2)                                        # just under itself: where it is - no more than a click
        self.assertEqual(self.sent(), [["page_show", {"id": "p2"}]])
        self.assertEqual(M.with_page_move(self.spec, "p1", 2)["pages"][-1]["id"], "p1")

    def chart_page(self):
        """The template with a fourth page in view: a chart's card, a line of text, a button."""
        spec = M.with_page(self.spec)
        page = spec["pages"][-1]
        for component in ("line_chart", "text", "button"):
            spec = M.with_insert(spec, component, page["root"]["id"])
        self.show(spec, showing=page["id"])
        find = lambda found: self.placed(page["id"], found)                       # noqa: E731
        return (find(lambda b: b["variant"] == "card"), find(lambda b: b["role"] == "chart"),
                find(lambda b: b["role"] == "text" and b["variant"] == "body"), find(lambda b: b["role"] == "button"))

    def test_a_click_picks_the_whole_piece_first_then_what_is_in_it(self):
        card, chart, text, button = self.chart_page()
        picked = lambda: self.js("return panelsView.info().picked;")              # noqa: E731
        self.click_stage(*self.middle(chart))
        self.assertEqual(picked(), card["id"])
        self.click_stage(*self.middle(chart))
        self.assertEqual(picked(), chart["id"])
        self.click_stage(*self.middle(chart))                         # nothing further in: it stays
        self.assertEqual(picked(), chart["id"])
        # The plain row that only lines the card's heading up is passed over; Layers has it.
        heading = self.placed(self.state["showing"], lambda b: b["role"] == "text" and b["variant"] == "h2")
        self.click_stage(*self.middle(heading))                       # beside the picked chart, at its depth
        self.assertEqual(picked(), heading["id"])
        self.click_stage(*self.middle(text))                          # out of the card: the outermost thing there
        self.assertEqual(picked(), text["id"])
        # A double-click goes straight to the innermost; Escape steps back out.
        x, y = self.middle(heading)
        self.js(f"const [cx, cy] = panelsView.toPage({x}, {y}); panelsView.picture().dispatchEvent(new MouseEvent('dblclick',"
                " {clientX: cx, clientY: cy, bubbles: true})); return true;")
        self.assertEqual(picked(), heading["id"])
        for want in (heading["parent"], card["id"], ""):
            self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); return true;")
            self.assertEqual(picked(), want)
        # What the pointer is on is named, and marked in Layers.
        self.pointer("pointermove", *self.middle(button))
        self.assertEqual(self.js("return panelsView.info().hover;"), button["id"])
        self.assertFalse(self.hidden("#hover-tag"))
        self.assertEqual(self.js("return document.querySelector('#layers .pn-hovered').dataset.id;"), button["id"])
        self.assertEqual(self.sent(), [])

    def test_the_picked_box_is_dragged_to_another_place_on_the_stage(self):
        card, chart, text, button = self.chart_page()
        root = self.state["solved"][self.state["showing"]]["boxes"][0]

        def drag(box, x, y, up=True):
            bx, by = self.middle(box)
            self.pointer("pointerdown", bx, by, 1)
            self.pointer("pointermove", bx + 9, by + 9, 1)
            self.pointer("pointermove", x, y, 1)
            drop = self.js(f"return panelsView.dropAt({x}, {y}, {json.dumps(box['id'])});")
            if up:
                self.pointer("pointerup", x, y, 0)
            return drop
        # Onto the top half of the text: before it, among the page's boxes (counted without the button).
        drop = drag(button, text["x"] + text["w"] / 2, text["y"] + 2)
        self.assertEqual((drop["into"], drop["at"], drop["same"]), (root["id"], 1, False))
        self.assertEqual(self.sent(), [["move", {"id": button["id"], "into": root["id"], "at": 1}]])
        self.forget()
        # By the card's right side: beside it - Python makes the row.
        drop = drag(button, card["x"] + card["w"] - 5, card["y"] + card["h"] / 2)
        self.assertEqual((drop["beside"], drop["side"]), (card["id"], "right"))
        self.assertEqual(self.sent(), [["beside", {"id": button["id"], "target": card["id"], "side": "right"}]])
        self.forget()
        # Well inside the card: into it, after the chart.
        drop = drag(button, chart["x"] + chart["w"] / 2, chart["y"] + chart["h"] - 4)
        self.assertEqual((drop["into"], drop["at"]), (card["id"], 2))
        self.assertEqual(self.sent(), [["move", {"id": button["id"], "into": card["id"], "at": 2}]])
        self.forget()
        # Where it already is, a click that barely moves, and a drag let go with Escape: nothing.
        drop = drag(button, text["x"] + text["w"] / 2, text["y"] + text["h"] - 1)
        self.assertTrue(drop["same"])
        self.click_stage(*self.middle(button))
        drag(text, card["x"] + card["w"] / 2, card["y"] + 2, up=False)
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); return true;")
        self.pointer("pointerup", card["x"] + card["w"] / 2, card["y"] + 2, 0)
        self.assertEqual(self.sent(), [])
        # A card never goes into itself: over its own middle it's aimed at the page.
        self.click_stage(*self.middle(text))
        self.js(f"panelsView.pick({json.dumps(card['id'])}); return true;")
        drop = drag(card, *self.middle(chart))
        self.assertEqual(drop["into"], root["id"])

    def test_the_picked_boxs_edges_size_it(self):
        card, chart, text, button = self.chart_page()
        self.js(f"panelsView.pick({json.dumps(chart['id'])}); return true;")
        x, y = chart["x"] + chart["w"] / 2, chart["y"] + chart["h"]
        self.pointer("pointerdown", x, y, 1)
        self.pointer("pointermove", x, y - 60, 1)
        self.pointer("pointerup", x, y - 60, 0)
        self.assertEqual(self.sent()[-1], ["box", {"id": chart["id"], "key": "h", "value": round(chart["h"] - 60)}])
        self.forget()
        self.js(f"panelsView.pick({json.dumps(card['id'])}); return true;")
        x, y = card["x"] + card["w"], card["y"] + card["h"] / 2
        self.pointer("pointerdown", x, y, 1)
        self.pointer("pointermove", x - 120, y, 1)
        self.pointer("pointerup", x - 120, y, 0)
        self.assertEqual(self.sent()[-1], ["box", {"id": card["id"], "key": "w", "value": round(card["w"] - 120)}])
        self.forget()
        # A line of text has no height to set: its bottom edge isn't a handle.
        self.js(f"panelsView.pick({json.dumps(text['id'])}); return true;")
        self.assertTrue(self.hidden("#box-h-row"))
        # The same from the right column: a height of its own, or as it comes out.
        self.js(f"panelsView.pick({json.dumps(chart['id'])}); return true;")
        self.assertFalse(self.hidden("#box-h-row"))
        self.assertTrue(self.hidden("#box-hpx-box"))
        self.click("#box-h [data-value=fixed]")
        self.assertEqual(self.sent(), [["box", {"id": chart["id"], "key": "h", "value": round(chart["h"])}]])
        fixed = M.with_box(self.state["spec"], chart["id"], "h", 120)
        self.show(fixed, showing=self.state["showing"])
        self.forget()
        self.assertEqual(self.js("return document.getElementById('box-hpx').value;"), "120")
        self.click("#box-h [data-value=hug]")
        self.assertEqual(self.sent(), [["box", {"id": chart["id"], "key": "h", "value": "hug"}]])

    def test_the_picked_layers_toolbar_and_keys(self):
        card, chart, text, button = self.chart_page()
        root = self.state["solved"][self.state["showing"]]["boxes"][0]
        self.assertTrue(self.hidden("#pick-tools"))
        self.click_stage(*self.middle(text))
        self.assertFalse(self.hidden("#pick-tools"))
        self.assertEqual(self.js("return [document.getElementById('pick-name').textContent,"
                                 " document.getElementById('tool-back').disabled, document.getElementById('tool-forth').disabled,"
                                 " document.getElementById('pick-tools').classList.contains('down')];"),
                         [f"Text · {M.find(self.state['spec'], text['id'])['box']['text']}", False, False, True])
        for tool in ("tool-back", "tool-forth", "tool-copy"):
            self.click("#" + tool)
        key = lambda init: self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {bubbles: true, cancelable: true, "   # noqa: E731
                                   + init + "})); return true;")
        key("key: 'ArrowUp', altKey: true")
        key("key: 'ArrowDown', altKey: true")
        key("key: 'd', ctrlKey: true")
        move = lambda at: ["move", {"id": text["id"], "into": root["id"], "at": at}]                                    # noqa: E731
        copy = ["copy_box", {"id": text["id"]}]
        self.assertEqual(self.sent(), [move(0), move(2), copy, move(0), move(2), copy])
        self.forget()
        self.click("#box-copy")
        self.click("#tool-remove")
        self.assertEqual(self.sent(), [copy, ["remove_box", {"id": text["id"]}]])
        self.forget()
        # The first of its container can't go earlier; "out" picks what it's in.
        self.js(f"panelsView.pick({json.dumps(chart['id'])}); return true;")
        self.assertEqual(self.js("return [document.getElementById('tool-back').disabled, document.getElementById('tool-forth').disabled];"),
                         [False, True])
        self.click("#tool-out")
        self.assertEqual(self.js("return panelsView.info().picked;"), card["id"])
        # A row's menu has the same.
        self.js(f"document.querySelector('#layers [data-id={button['id']}]').dispatchEvent(new MouseEvent('contextmenu',"
                " {bubbles: true, cancelable: true, clientX: 60, clientY: 300})); return true;")
        self.assertEqual(self.js("return [...document.querySelectorAll('.menu-pop button')].map(b => [b.textContent, b.disabled]);"),
                         [["Rename", False], ["Duplicate", False], ["Move it earlier", False], ["Move it later", True],
                          ["Remove", False]])
        self.js("[...document.querySelectorAll('.menu-pop button')].find(b => b.textContent === 'Move it earlier').click(); return true;")
        self.assertEqual(self.sent(), [["move", {"id": button["id"], "into": root["id"], "at": 1}]])

    def test_a_container_says_which_way_its_boxes_go_and_how_much_room(self):
        card, chart, text, button = self.chart_page()
        self.js(f"panelsView.pick({json.dumps(card['id'])}); return true;")
        self.assertFalse(self.hidden("#box-dir-row"))
        self.assertEqual(self.js("return document.querySelector('#box-dir [aria-pressed=true]').dataset.value;"), "column")
        self.click("#box-dir [data-value=row]")
        self.assertTrue(self.hidden("#spacing-pop"))
        self.click("#edit-spacing")
        self.assertFalse(self.hidden("#spacing-pop"))
        self.assertTrue(self.hidden("#box-wrap-box"))                 # only a row wraps
        self.js("const g = document.getElementById('box-gap'); g.value = '20'; g.dispatchEvent(new Event('input', {bubbles: true}));"
                " const p = document.getElementById('box-pad'); p.value = '6'; p.dispatchEvent(new Event('input', {bubbles: true}));"
                " return true;")
        self.click("#box-align [data-value=center]")
        self.wait(120)
        self.assertEqual(sorted(self.sent(), key=lambda s: s[1]["key"]),
                         [["box", {"id": card["id"], "key": "align", "value": "center"}],
                          ["box", {"id": card["id"], "key": "dir", "value": "row"}],
                          ["box", {"id": card["id"], "key": "gap", "value": 20}],
                          ["box", {"id": card["id"], "key": "pad", "value": [6, 6, 6, 6]}]])
        # What isn't a container has no such thing, and the panel closes.
        self.js(f"panelsView.pick({json.dumps(text['id'])}); return true;")
        self.assertTrue(self.hidden("#box-dir-row"))
        self.assertTrue(self.hidden("#spacing-pop"))

    def test_an_empty_page_says_how_to_begin(self):
        self.show()
        self.assertTrue(self.hidden("#empty-page"))
        spec = M.with_page(self.spec)
        self.show(spec, showing=spec["pages"][-1]["id"])
        self.assertFalse(self.hidden("#empty-page"))
        self.click("#empty-piece")
        self.assertFalse(self.hidden("#add-pop"))
        self.assertEqual(self.js("return panelsView.info().addMode;"), "insert")
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); return true;")
        self.click("#empty-layout")
        self.assertEqual(self.js("return panelsView.info().addMode;"), "layouts")
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); return true;")
        # + Add opens on what the page needs: a whole page for an empty one, a piece otherwise.
        self.click("#add-open")
        self.assertEqual(self.js("return panelsView.info().addMode;"), "layouts")
        self.click("#add-open")
        self.show()
        self.click("#add-open")
        self.assertEqual(self.js("return panelsView.info().addMode;"), "insert")
        # A link is followed from the box that has it.
        self.click("#add-open")
        button = self.placed("p1", lambda b: b["role"] == "button" and b["variant"] == "")
        self.js(f"panelsView.pick({json.dumps(button['id'])}); return true;")
        self.js("document.getElementById('card-more').open = true; return true;")
        self.assertFalse(self.hidden("#box-go-row"))
        self.click("#box-go-open")
        self.assertEqual(self.sent(), [["page_show", {"id": "p2"}]])

    def test_the_window_has_a_title(self):
        self.show()
        self.assertEqual(self.js("return document.querySelector('[data-key=title]').value;"), "Booking app")
        self.js("const f = document.querySelector('[data-key=title]'); f.value = 'Tide'; f.dispatchEvent(new Event('change', {bubbles: true}));"
                " return true;")
        self.assertEqual(self.sent(), [["set", {"key": "title", "value": "Tide"}]])

    def player(self, **more):
        """The template with a fourth page in view: the music player. (its bar, its time, its Play button)"""
        spec = M.with_layout(M.with_page(self.spec), "p4", "player")
        self.show(M.clean({**spec, **more}), showing="p4")
        find = lambda found: self.placed("p4", found)                               # noqa: E731
        return find(lambda b: b["role"] == "progress"), find(lambda b: b["text"] == "1:24"), find(lambda b: b["text"] == "Play")

    def test_a_page_nothing_leads_to_says_so_and_one_after_another_asks_for_them_all(self):
        self.player()
        marks = self.js("return [...document.querySelectorAll('#pages .pn-page')].map(r => (r.querySelector('.pn-page-start') || {}).textContent || '');")
        self.assertEqual(marks, ["The clip starts here", "", "", "Never shown in the clip"])
        self.click("#flow")
        self.assertEqual(self.sent(), [["flow", {}]])
        self.forget()
        # A time of its own can be taken off again.
        keyed = M.clean({**self.state["spec"], "page_keys": [[2.0, "p4", "fade"], [5.0, "p1", "fade"]]})
        self.show(keyed, showing="p4")
        self.assertFalse(self.js("return !!document.querySelector('#pages .pn-page[data-page=p4] .pn-never');"))
        self.js("[...document.querySelectorAll('#changes li')].find(l => l.textContent.startsWith('At 2')).querySelector('button').click();"
                " return true;")
        self.assertEqual(self.sent(), [["set", {"key": "page_keys", "value": [[5.0, "p1", "fade"]]}]])

    def test_choosing_a_page_always_shows_it(self):
        self.show()
        # Trying it, the stage is the mouse's: a page chosen ends that and is shown, still.
        self.click("#mode [data-value=try]")
        self.js("document.querySelectorAll('#pages .pn-page')[2].click(); return true;")
        self.assertEqual(self.js("return [panelsView.info().mode, panelsView.info().view];"), ["edit", "edit"])
        self.assertEqual(self.sent(), [["page_show", {"id": "p3"}]])
        self.forget()
        # Playing, the stage is the clock's: the same.
        self.click("#play")
        self.assertTrue(self.js("return panelsView.info().playing;"))
        self.js("document.querySelectorAll('#pages .pn-page')[0].click(); return true;")
        self.assertEqual(self.js("return [panelsView.info().playing, panelsView.info().view];"), [False, "edit"])

    def test_a_number_counts_and_a_click_starts_what_runs(self):
        bar, time, play = self.player()
        self.js(f"panelsView.pick({json.dumps(time['id'])}); return true;")
        self.assertTrue(self.js("return document.getElementById('an-count').checked;"))
        self.assertFalse(self.hidden("#an-run-row"))
        options = self.js("return [...document.getElementById('an-run-by').options].map(o => [o.value, o.textContent]);")
        self.assertEqual(options[0], ["", "With the page"])
        self.assertIn([play["id"], "On a click on Play"], options)
        self.assertEqual(self.js("return [document.getElementById('an-run-by').value, document.getElementById('an-run-seconds').value];"),
                         [play["id"], "4"])
        self.js("const s = document.getElementById('an-run-by'); s.value = ''; s.dispatchEvent(new Event('change')); return true;")
        self.js("const f = document.getElementById('an-run-seconds'); f.value = '2,5'; f.dispatchEvent(new Event('change')); return true;")
        self.click("#an-count")
        run = lambda value: ["box", {"id": time["id"], "key": "run", "value": value}]     # noqa: E731
        self.assertEqual(self.sent(), [run({"by": "", "seconds": 4}), run({"by": play["id"], "seconds": 2.5}),
                                       ["box", {"id": time["id"], "key": "count", "value": False}]])
        self.forget()
        # A bar draws on as the page builds unless something starts it; a text with no number has neither.
        self.js(f"panelsView.pick({json.dumps(bar['id'])}); return true;")
        self.assertEqual(self.js("return document.getElementById('an-run-by').options[0].textContent;"), "As the page builds")
        self.js("const s = document.getElementById('an-run-by'); s.value = ''; s.dispatchEvent(new Event('change')); return true;")
        self.assertEqual(self.sent(), [["box", {"id": bar["id"], "key": "run", "value": None}]])
        title = self.placed("p4", lambda b: b["text"] == "Slow Morning")
        self.js(f"panelsView.pick({json.dumps(title['id'])}); return true;")
        self.assertTrue(self.hidden("#an-run-row"))
        self.assertTrue(self.hidden("#an-count"))

    def test_the_view_says_what_a_count_says_as_the_clock_does(self):
        bar, time, play = self.player()
        x, y = solve.to_frame(self.state["spec"], self.state["solved"]["p4"]["h"], *self.middle(play), FRAME)
        spec = M.clean({**self.state["spec"], "start": "p4", "path": [[0, x, y]], "clicks": [[1.0, 1.2]], "seconds": 6})
        state = self.show(spec, showing="p4")
        for frame in (0, 30, 60, 100, 143):
            want = clock.pose(spec, state["clock"], frame)
            got = self.js(f"return panelsView.pose(null, {frame});")
            close(self, got, want, f"frame {frame}")
        self.assertEqual(clock.pose(spec, state["clock"], 72)["said"][time["id"]], "0:42")
        # In Edit the page is shown whole: the bar full, the time as it's written.
        self.assertEqual(self.js("return panelsView.info().view;"), "edit")

    def test_the_story_strip_when_buddy_moves_the_cursor(self):
        spec = M.with_layout(M.with_page(self.story), "p4", "player")
        self.show(spec, showing="p4")
        boxes = {b["text"]: b["id"] for b in M.every(spec["pages"][3]["root"]) if b["text"]}
        cards = self.js("return [...document.querySelectorAll('#pages .pn-page')].map(r => [r.dataset.page,"
                        " r.querySelector('.pn-page-secs').textContent, [...r.querySelectorAll('.pn-page-note')].map(n => n.textContent)]);")
        self.assertEqual(cards, [["p1", "2 s", []], ["p2", "3 s", []], ["p3", "2 s", []], ["p4", "5.5 s", ["click “Play” · 1 s"]]])
        # Between two cards: how the clip goes on.
        pills = self.js("return [...document.querySelectorAll('#pages .pn-link')].map(b => [b.dataset.from, b.querySelector('.pn-pill').textContent]);")
        self.assertEqual(pills, [["p1", "click “Sign in”"], ["p2", "click “Settings”"], ["p3", "after 2 s"]])
        self.assertTrue(self.hidden("#flow"))
        self.js("document.querySelector('#pages .pn-link[data-from=p3]').click(); return true;")
        menu = self.js("return [...document.querySelectorAll('.menu-pop button')].map(b => [b.textContent, b.getAttribute('aria-checked')]);")
        self.assertEqual(menu[0], ["When its time is up (2 s)", "true"])
        self.assertIn(["Click on “Save”", "false"], menu)
        save = next(b["id"] for b in M.every(spec["pages"][2]["root"]) if b["text"] == "Save")
        self.js("[...document.querySelectorAll('.menu-pop button')].find(b => b.textContent === 'Click on “Save”').click(); return true;")
        self.js("document.querySelector('#pages .pn-link[data-from=p1]').click();"
                " [...document.querySelectorAll('.menu-pop button')][0].click(); return true;")
        self.assertEqual(self.sent(), [["story", {"page": "p3", "key": "by", "value": save}], ["story", {"page": "p1", "key": "by", "value": ""}]])
        self.forget()
        # A card's seconds are its own to set; nothing typed gives them back.
        for said, value in (("4,5", 4.5), ("", 0)):
            self.js("document.querySelector('#pages .pn-page[data-page=p2] .pn-page-secs').click(); return true;")
            self.js(f"const f = document.querySelector('#pages .pn-page[data-page=p2] input'); f.value = {json.dumps(said)};"
                    " f.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); return true;")
            self.assertEqual(self.sent(), [["story", {"page": "p2", "key": "secs", "value": value}]])
            self.forget()
        # In a story the clip starts on the first page: the menu doesn't offer another.
        self.js("document.querySelector('#pages .pn-page[data-page=p2] .pn-page-more').click(); return true;")
        labels = self.js("return [...document.querySelectorAll('.menu-pop button')].map(b => b.textContent);")
        self.assertEqual(labels, ["Rename", "Duplicate", "Move earlier", "Move later", "Remove"])
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); return true;")
        # The arrows either side of the window.
        self.click("#page-prev")
        self.assertEqual(self.sent(), [["page_show", {"id": "p3"}]])
        self.assertEqual(self.js("return [document.getElementById('page-prev').disabled, document.getElementById('page-next').disabled];"),
                         [False, True])
        self.assertEqual(boxes["Play"], self.state["story"][3]["clicks"][0][1])

    def test_the_card_says_what_a_click_starts_and_picks_more_on_the_page(self):
        spec = M.with_layout(M.with_page(self.story), "p4", "player")
        self.show(spec, showing="p4")
        find = lambda found: self.placed("p4", found)                               # noqa: E731
        play, nxt, bar = find(lambda b: b["text"] == "Play"), find(lambda b: b["text"] == "Next"), find(lambda b: b["role"] == "progress")
        time, total = find(lambda b: b["text"] == "1:24"), find(lambda b: b["text"] == "3:41")
        self.js(f"panelsView.pick({json.dumps(play['id'])}); return true;")
        self.assertFalse(self.hidden("#pick-card"))
        rows = self.js("return [...document.querySelectorAll('#clicked-list li')].map(l => [l.dataset.id, l.querySelector('.pn-clicked-what').textContent,"
                       " (l.querySelector('input') || {}).value]);")
        self.assertEqual(rows, [[bar["id"], "The bar fills up", "4"], [time["id"], "“1:24” counts up", "4"]])
        self.assertTrue(self.hidden("#no-clicked"))
        self.assertTrue(self.hidden("#leave-add"))                    # the last page goes on to nothing
        self.js("const f = document.querySelector('#clicked-list li input'); f.value = '2'; f.dispatchEvent(new Event('change')); return true;")
        self.js("document.querySelectorAll('#clicked-list li button')[1].click(); return true;")
        self.assertEqual(self.sent(), [["box", {"id": bar["id"], "key": "run", "value": {"by": play["id"], "seconds": 2}}],
                                       ["box", {"id": time["id"], "key": "count", "value": False}]])
        self.forget()
        # "+ Start something": the next click on the stage picks what - a bar, a chart, a number.
        self.js(f"panelsView.pick({json.dumps(nxt['id'])}); return true;")
        self.assertFalse(self.hidden("#no-clicked"))
        self.click("#start-add")
        self.assertEqual(self.js("return [panelsView.info().choosing, document.getElementById('banner-text').textContent];"),
                         ["start", "Click the bar, the chart or the number this should start."])
        self.assertTrue(self.hidden("#pick-card"))
        title = find(lambda b: b["text"] == "Slow Morning")
        self.click_stage(*self.middle(title))                         # words with no number: not that
        self.assertEqual((self.sent(), self.js("return panelsView.info().choosing;")), ([], "start"))
        self.click_stage(*self.middle(total))
        self.assertEqual(self.sent(), [["starts", {"id": total["id"], "by": nxt["id"]}]])
        self.assertEqual(self.js("return [panelsView.info().choosing, panelsView.info().picked];"), ["", nxt["id"]])
        self.forget()
        self.click("#start-add")
        self.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); return true;")
        self.assertEqual(self.js("return [panelsView.info().choosing, panelsView.info().picked];"), ["", nxt["id"]])
        # On a page that isn't the last, a box can be what goes on to the next one.
        self.show(spec, showing="p3")
        save = self.placed("p3", lambda b: b["text"] == "Save")
        self.js(f"panelsView.pick({json.dumps(save['id'])}); return true;")
        self.assertFalse(self.hidden("#leave-add"))
        self.click("#leave-add")
        self.assertEqual(self.sent(), [["story", {"page": "p3", "key": "by", "value": save["id"]}]])
        went = M.with_story(spec, "p3", "by", save["id"])
        self.show(went, showing="p3")
        self.js(f"panelsView.pick({json.dumps(save['id'])}); return true;")
        self.assertEqual(self.js("return [...document.querySelectorAll('#clicked-list li')].map(l => l.querySelector('.pn-clicked-what').textContent);"),
                         ["The clip goes on to the next page"])
        self.assertTrue(self.hidden("#leave-add"))

    def test_the_windows_look_and_the_layers_are_panels_from_the_bar(self):
        self.show()
        self.assertTrue(self.hidden("#look-pop"))
        self.assertTrue(self.hidden("#layers-pop"))
        self.click("#look-open")
        self.assertFalse(self.hidden("#look-pop"))
        self.assertFalse(self.hidden("[data-key=title]"))
        self.click("#layers-open")                                   # one panel at a time
        self.assertTrue(self.hidden("#look-pop"))
        self.assertFalse(self.hidden("#layers"))
        self.assertEqual(self.js("return document.querySelectorAll('#layers .pn-layer').length > 3;"), True)
        self.assertEqual(self.sent(), [])

    def test_trying_it_the_boxes_answer_and_nothing_is_sent(self):
        self.show()
        button = self.placed("p1", lambda b: b["role"] == "button" and b["variant"] == "")
        self.click("#mode [data-value=try]")
        self.pointer("pointermove", *self.middle(button))
        self.wait(600)
        pose = self.js("return panelsView.live();")
        self.assertIsNotNone(pose, "not trying it")
        look = pose["pages"][-1]["boxes"].get(button["id"])
        self.assertIsNotNone(look, "the button doesn't answer")
        self.assertGreater(look["scale"], 1.0)
        self.assertGreater(look["glow"], 0.0)
        # A click on it goes to the page it links to - in the preview only.
        self.pointer("pointerdown", *self.middle(button), 1)
        self.wait(120)
        self.pointer("pointerup", *self.middle(button), 0)
        self.wait(800)
        self.assertEqual(self.js("return panelsView.info().livePage;"), "p2")
        self.assertEqual(self.sent(but=()), [])

    def test_recording_sends_what_the_mouse_did(self):
        self.show()
        self.click("#record")
        self.assertEqual(self.js("return panelsView.info().recording;"), True)
        self.assertEqual(self.js("return panelsView.info().fit;"), "frame")
        self.pointer("pointermove", 100, 100)
        self.wait(250)
        self.pointer("pointermove", 300, 200)
        self.pointer("pointerdown", 300, 200, 1)
        self.wait(150)
        self.pointer("pointerup", 300, 200, 0)
        self.wait(250)
        self.click("#record")
        (recorded,) = self.sent("recorded")
        payload = recorded[1]
        self.assertGreaterEqual(len(payload["samples"]), 2)
        self.assertEqual(payload["samples"][0][0], 0)
        self.assertEqual(len(payload["clicks"]), 1)
        self.assertGreaterEqual(payload["seconds"], M.MIN_SECONDS)
        for t, x, y in payload["samples"]:
            self.assertTrue(0 <= x <= 100 and 0 <= y <= 100)

    def test_the_views_pose_is_the_clocks(self):
        for how in ("slide", "fade", "match"):
            spec = M.clean({**self.spec, "go_with": how})
            state = self.show(spec)
            clk = state["clock"]
            change = next(c for c in clk["pages"] if c["why"] == "click")
            frames = sorted({0, 5, 12, 20, change["at"] - 1, change["at"], change["at"] + 1, change["at"] + 3,
                             change["at"] + change["frames"] // 2, change["at"] + change["frames"] - 1,
                             change["at"] + change["frames"], change["at"] + change["frames"] + 4, 100, clk["total"] - 1})
            got = self.js(f"return {json.dumps(frames)}.map(f => panelsView.pose(null, f));")
            for f, mine in zip(frames, got):
                close(self, mine, clock.pose(spec, clk, f), f"{how} frame {f}")

    def test_a_boxs_own_arrival_is_posed_as_the_clock_says(self):
        spec = self.spec
        for box_id, kind in (("b2", "pop"), ("b3", "fade"), ("b6", "rise"), ("b7", "pop"), ("b9", "fade"), ("b4", "none")):
            spec = M.with_box(spec, box_id, "arrive", {"kind": kind})
        for build in (True, False):
            built = M.clean({**spec, "build": {**spec["build"], "on": build}})
            state = self.show(built)
            clk = state["clock"]
            frames = list(range(0, 40, 2))
            got = self.js(f"return {json.dumps(frames)}.map(f => panelsView.pose(null, f));")
            for f, mine in zip(frames, got):
                close(self, mine, clock.pose(built, clk, f), f"build {build} frame {f}")

    def test_a_box_carries_what_is_in_it_as_the_clock_says(self):
        # The template's frames - built on, hovered, pressed, changing page - and a hovered card with text in it.
        card = {"role": "section", "variant": "card", "name": "Card", "pad": 10, "children": [{"role": "text", "text": "Total"}]}
        cards = M.clean({**self.spec, "pages": [{"id": "p1", "name": "One", "root": {"role": "section", "pad": 14, "children": [card]}}],
                         "build": {"on": False}})
        solved = solve.solve(cards, "p1")
        box = next(b for b in solved["boxes"] if b["name"] == "Card")
        spot = list(solve.to_frame(cards, solved["h"], box["x"] + 4, box["y"] + 4, FRAME))
        cards = M.clean({**cards, "path": [[0.0, 95.0, 95.0], [1.0, *spot], [3.0, *spot]]})
        for spec, frames in ((self.spec, (0, 6, 12, 30, 45, 60, 100)), (cards, (20, 40, 60))):
            state = self.show(spec)
            clk = state["clock"]
            for f in frames:
                want = clock.pose(spec, clk, f)
                close(self, self.js(f"return panelsView.pose(null, {f});"), want, f"frame {f}")
                for layer in want["pages"]:
                    page = solve.solve(spec, layer["page"])
                    ids = [b["id"] for b in page["boxes"]]
                    got = self.js(f"return {json.dumps(ids)}.map(id => panelsView.carried({json.dumps(layer['page'])},"
                                  f" {json.dumps(layer['boxes'])}, id));")
                    for bid, mine in zip(ids, got):
                        close(self, mine, clock.carried(page, layer["boxes"], bid), f"frame {f} carried {bid}")
        # The card's text has no pose of its own, but is drawn grown and lifted with the card.
        words = next(b for b in solved["boxes"] if b["parent"] == box["id"])
        boxes = clock.pose(cards, state["clock"], 60)["pages"][0]["boxes"]
        self.assertNotIn(words["id"], boxes)
        moved = self.js(f"return panelsView.carried('p1', {json.dumps(boxes)}, {json.dumps(words['id'])});")
        self.assertGreater(moved["scale"], 1.0)
        self.assertLess(moved["dy"], 0.0)

    def test_trying_it_a_link_takes_a_click_though_it_doesnt_answer(self):
        spec = M.clean(self.spec)
        button = next(b for b in solve.solve(spec, "p1")["boxes"] if b["role"] == "button" and b["variant"] == "")
        spec = M.with_box(spec, button["id"], "reacts", False)
        self.show(spec)
        self.click("#mode [data-value=try]")
        self.pointer("pointermove", *self.middle(button))
        self.wait(400)
        self.assertIsNone(self.js("return panelsView.live();")["pages"][-1]["boxes"].get(button["id"]))   # no answer
        self.pointer("pointerdown", *self.middle(button), 1)
        self.wait(120)
        self.pointer("pointerup", *self.middle(button), 0)
        self.wait(800)
        self.assertEqual(self.js("return panelsView.info().livePage;"), "p2")

    def test_an_area_wipes_on_left_to_right(self):
        self.show()
        area = {"box": "b1", "kind": "poly", "points": [[10, 40], [50, 10], [90, 30], [90, 60], [10, 60]], "closed": True,
                "fill": "#3c82e0ff", "stroke": "", "stroke_w": 0.0, "draw": "across"}
        spots = [[20, 50], [45, 50], [55, 50], [85, 50]]
        lit = lambda share, p=area: self.js(f"return panelsView.drawn({json.dumps(p)}, {share}, 100, 70, {json.dumps(spots)});")
        self.assertEqual([a > 0 for a in lit(0.5)], [True, True, False, False])     # half of 80 px from x 10: up to 50
        self.assertEqual([a > 0 for a in lit(1.0)], [True] * 4)
        self.assertEqual([a > 0 for a in lit(0.0)], [False] * 4)
        whole = {**area, "draw": False}
        self.assertEqual([a > 0 for a in lit(0.5, whole)], [True] * 4)              # one that isn't drawn on is whole

    def test_a_change_of_a_value_never_starts_playback(self):
        self.show()
        self.js("const s = document.getElementById('scrub'); s.value = '20'; s.dispatchEvent(new Event('input')); return true;")
        self.assertEqual(self.js("return panelsView.info();")["frame"], 20)
        self.show(M.clean({**self.spec, "accent": "#e4572e"}))
        self.wait(200)
        info = self.js("return panelsView.info();")
        self.assertEqual((info["playing"], info["frame"], info["view"]), (False, 20, "frame"))
        # Nor does a change to a layer's animation.
        self.show(M.with_box(self.spec, "b2", "arrive", {"kind": "pop"}))
        self.wait(200)
        info = self.js("return panelsView.info();")
        self.assertEqual((info["playing"], info["frame"]), (False, 20))
        self.assertEqual(self.sent(), [])

    def test_the_stage_is_drawn_at_the_screens_pixel_ratio(self):
        self.js("Object.defineProperty(window, 'devicePixelRatio', {configurable: true, get: () => 2}); return true;")
        self.click("#fit [data-value=frame]")
        state = self.show()
        size = self.js("const c = panelsView.picture(); return [c.width, c.height, parseFloat(c.style.width), parseFloat(c.style.height)];")
        self.assertEqual((size[0], size[1]), (round(size[2] * 2), round(size[3] * 2)))
        # The window fills the place the panel says, in device px: its inside is drawn
        # right up to its far corners, and the frame round it is black.
        spec, solved = state["spec"], state["solved"]["p1"]
        k = size[0] / FRAME[0]
        s = spec["zoom"] * FRAME[0] / 1920
        ox, oy = spec["x"] / 100 * FRAME[0] - solved["w"] * s / 2, spec["y"] / 100 * FRAME[1] - solved["h"] * s / 2
        points = [((ox + solved["w"] * s * a) * k, (oy + solved["h"] * s * b) * k) for a, b in
                  ((0.04, 0.5), (0.96, 0.5), (0.5, 0.06), (0.5, 0.97), (0.96, 0.96))]
        lit = self.js(f"const c = panelsView.picture(), d = c.getContext('2d');"
                      f" return {json.dumps(points)}.map(([x, y]) => [...d.getImageData(Math.round(x), Math.round(y), 1, 1).data]);")
        for (x, y), px in zip(points, lit):
            self.assertGreater(sum(px[:3]), 30, f"nothing drawn at {x:.0f}, {y:.0f}")
        corner = self.js("const d = panelsView.picture().getContext('2d'); return [...d.getImageData(2, 2, 1, 1).data];")
        self.assertEqual(corner[:3], [0, 0, 0])
        # Shown as Window, the canvas is the stage's size at the same ratio.
        self.click("#fit [data-value=window]")
        size = self.js("const c = panelsView.picture(); return [c.width, c.height, parseFloat(c.style.width), parseFloat(c.style.height)];")
        self.assertEqual((size[0], size[1]), (round(size[2] * 2), round(size[3] * 2)))


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.script = (ROOT / "web" / "panels.js").read_text(encoding="utf-8")

    def test_the_choices_are_the_models(self):
        for key, values in (("kind", catalogue.KINDS), ("style", catalogue.STYLES)):
            part = self.html.split(f'data-key="{key}"')[1].split("</select>")[0]
            self.assertEqual(tuple(re.findall(r'option value="(\w+)"', part)), values)
        part = self.html.split('data-key="go_with"')[1].split("</div>")[0]
        self.assertEqual(tuple(re.findall(r'data-value="(\w+)"', part)), M.GO_WITH)
        part = self.html.split('id="chart-shape"')[1].split("</div>")[0]
        self.assertEqual(tuple(re.findall(r'data-value="(\w+)"', part)), M.CHART_SHAPES)
        part = self.html.split('id="an-arrive-kind"')[1].split("</select>")[0]
        self.assertEqual(set(re.findall(r'option value="(\w*)"', part)) - {""}, {"fade", "rise", "pop"})

    def test_no_inline_script_and_the_same_policy_as_interactions(self):
        other = (_paths.APP / "pages" / "interactions" / "web" / "index.html").read_text(encoding="utf-8")
        policy = lambda text: text.split('content="default-src')[1].split('"')[0]
        self.assertEqual(policy(self.html), policy(other))
        self.assertNotIn("<script>", self.html)
        self.assertIn('<script src="panels.js"></script>\n<script src="../../data_graphics/web/tabs.js"></script>', self.html)

    def test_the_files_are_lf(self):
        for name in ("index.html", "panels.js", "panels.css"):
            self.assertNotIn(b"\r\n", (ROOT / "web" / name).read_bytes(), name)

    def test_the_view_mirrors_the_clocks_numbers(self):
        for name, value in (("RISE", clock.RISE), ("SLIDE", clock.SLIDE), ("BUILD_MOST", clock.BUILD_MOST),
                            ("POP", clock.POP)):
            self.assertIn(f"{name} = {value}", self.script)
        from pages.data_graphics import easing
        self.assertIn(f"overshoot: {easing.CURVES['overshoot']}", self.script)

    def test_the_layers_list_is_a_tree(self):
        self.assertIn('id="layers" role="tree"', self.html)
        self.assertIn('role: "treeitem"', self.script)


if __name__ == "__main__":
    unittest.main()
