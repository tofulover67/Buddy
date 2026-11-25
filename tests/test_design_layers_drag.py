"""Design's layers list, dragged (pages/design/web/design.js, its "layers-drag" part): a
row - or every picked one - dragged up or down the list to a new place in the pile,
into a group or a combined shape or out of one. The rule on its own
(designView.moveLayers) is run in the page and what it makes is what model.clean keeps;
then real drags, with pointer events, on the page offscreen: one change to undo, a
click still picks, Escape lets go, a drop that can't be is nothing."""

import json
import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401

from pages.design import model as M

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QTimer
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAVE_WEB = True
except ImportError:  # pragma: no cover
    HAVE_WEB = False

ROOT = _paths.APP / "pages" / "design"


def box(lid, kind="rect", name=None, **more):
    return {"id": lid, "kind": kind, "name": name or lid, "x": 300, "y": 300, "w": 200, "h": 120, **more}


def pile():
    """A made-up pile, bottom first: a card, a group of two, a combined shape of two,
    a sun and a title."""
    return M.clean({"size": [1920, 1080], "layers": [
        box("a", name="Back card"),
        {"id": "g", "kind": "group", "name": "Badge", "children": [box("g1", name="Badge base"),
                                                                   box("g2", "text", "Badge text", text="Tess")]},
        {"id": "s", "kind": "boolean", "name": "Cut out", "op": "subtract",
         "children": [box("s1", name="Cut base"), box("s2", "ellipse", "Cut hole")]},
        box("b", "ellipse", "Sun"),
        box("t", "text", "Title", text="Hello Sam"),
        box("p", "path", "Open path", points=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}]),
        box("q", "path", "Closed path", closed=True, points=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}]),
    ]})


def shape(layers):
    """A pile as ids and nesting only: [id, [id, [...]], ...]."""
    return [[layer["id"], shape(layer["children"])] if "children" in layer else layer["id"] for layer in layers]


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "web" / "design.js").read_text(encoding="utf-8")
        self.css = (ROOT / "web" / "design.css").read_text(encoding="utf-8")

    def test_rows_are_dragged_with_pointer_events_in_a_part_of_its_own(self):
        part = self.script.split("// ---- layers-drag --")[1].split("// ---- end layers-drag --")[0]
        for line in ('list.addEventListener("pointerdown"', 'window.addEventListener("pointermove"', "setPointerCapture",
                     "requestAnimationFrame", 'e.key !== "Escape"', "commit();"):
            self.assertIn(line, part)
        self.assertEqual(part.count("commit();"), 1)                    # the whole move is one step
        self.assertNotIn("draggable", self.script)                    # not HTML drag and drop
        self.assertNotIn("dragstart", self.script)
        self.assertIn("row.dataset.id = l.id;", self.script)
        for rule in (".ds-drop-line", ".ds-drop-into", ".ds-dragged"):
            self.assertIn(rule, self.css)


@unittest.skipUnless(HAVE_WEB, "needs PySide6 with QtWebEngine")
class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.home = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"BUDDY_HOME": self.home.name, "BUDDY_RESOLVE_READER": "off"})
        patcher.start()
        self.addCleanup(patcher.stop)
        from test_design import Host
        from pages.design.page import DesignPage
        self.host = Host()
        self.host.tool_settings("design", {})["spec"] = pile()
        self.page = DesignPage(self.host)
        self.page.resize(1500, 900)
        self.page.show()
        self.until("!!(window.designView && designView.design() && document.querySelectorAll('.ds-layer').length)")
        self.js(DRIVE)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait(self, ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def js(self, expression):
        out, loop = [], QEventLoop()
        self.page.view.page().runJavaScript(f"JSON.stringify({expression})", 0, lambda value: (out.append(value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        return json.loads(out[0]) if out and out[0] else None

    def until(self, expression, ms=15000):
        for _ in range(ms // 100):
            if self.js(expression):
                return
            self.wait(100)
        self.fail(f"never true: {expression}")

    def moved(self, ids, parent, index):
        return self.js(f"designView.moveLayers(designView.design().layers, {json.dumps(ids)}, {json.dumps(parent)}, {index})")

    def steps(self):
        self.wait(300)                                                # what the view sent, heard
        return len(self.page.undo_steps)

    # ------------------------------------------------------------- the rule --

    def test_the_rule_moves_layers_into_out_of_and_along_a_pile(self):
        cases = [
            # into the group, on top of what it holds
            ((["t"], "g", 2), ["a", ["g", ["g1", "g2", "t"]], ["s", ["s1", "s2"]], "b", "p", "q"]),
            # two, in the order they're stacked whatever order they're named in, to the top
            ((["b", "a"], None, 7), [["g", ["g1", "g2"]], ["s", ["s1", "s2"]], "t", "p", "q", "a", "b"]),
            # counted in the pile as it was: just under the sun
            ((["a"], None, 3), [["g", ["g1", "g2"]], ["s", ["s1", "s2"]], "a", "b", "t", "p", "q"]),
            # out of a combined shape, to the bottom
            ((["s2"], None, 0), ["s2", "a", ["g", ["g1", "g2"]], ["s", ["s1"]], "b", "t", "p", "q"]),
            # everything out of one: it goes
            ((["s1", "s2"], None, 7), ["a", ["g", ["g1", "g2"]], "b", "t", "p", "q", "s1", "s2"]),
            # a group with one of its own picked too: the group moves, whole
            ((["g1", "g"], None, 0), [["g", ["g1", "g2"]], "a", ["s", ["s1", "s2"]], "b", "t", "p", "q"]),
            # solid shapes into a combined shape: a closed path is one
            ((["b", "q"], "s", 0), ["a", ["g", ["g1", "g2"]], ["s", ["b", "q", "s1", "s2"]], "t", "p"]),
            # where it already is: the same pile
            ((["t"], None, 5), ["a", ["g", ["g1", "g2"]], ["s", ["s1", "s2"]], "b", "t", "p", "q"]),
        ]
        for (ids, parent, index), want in cases:
            with self.subTest(ids=ids, parent=parent, index=index):
                got = self.moved(ids, parent, index)
                self.assertEqual(shape(got), want)
                # What the view sends is what Python keeps.
                kept = M.clean({"size": [1920, 1080], "layers": got})["layers"]
                self.assertEqual(shape(kept), want)
                self.assertEqual(kept, got)
        # The pile on the canvas is left alone: the rule works on a copy.
        self.assertEqual(shape(self.js("designView.design().layers")), shape(pile()["layers"]))

    def test_the_rule_refuses_what_cant_be(self):
        for ids, parent, index in ((["t"], "s", 0),                   # text into a combined shape
                                   (["g"], "s", 0),                   # a group into one
                                   (["p"], "s", 1),                   # an open path into one
                                   (["b", "t"], "s", 0),              # any one that can't, none go
                                   (["g"], "g", 0),                   # a group into itself
                                   (["a"], "b", 0),                   # into what isn't a container
                                   (["a"], "nobody", 0),
                                   (["nobody"], None, 0),
                                   ([], None, 0)):
            with self.subTest(ids=ids, parent=parent):
                self.assertIsNone(self.moved(ids, parent, index))
        # ...which Python would have refused too: a combined shape keeps only solid shapes.
        raw = pile()
        raw["layers"][2]["children"].append(box("x", "text", text="Olive"))
        self.assertEqual(shape(M.clean(raw)["layers"])[2], ["s", ["s1", "s2"]])
        # A group can't go into one it holds.
        nested = M.clean({"layers": [{"id": "o", "kind": "group", "children": [
            box("o1"), {"id": "i", "kind": "group", "children": [box("i1")]}]}]})
        self.assertIsNone(self.js(f"designView.moveLayers({json.dumps(nested['layers'])}, ['o'], 'i', 0)"))
        self.assertEqual(shape(self.js(f"designView.moveLayers({json.dumps(nested['layers'])}, ['i'], null, 1)")),
                         [["o", ["o1"]], ["i", ["i1"]]])

    # ------------------------------------------------------------ the drags --

    def test_a_row_dragged_onto_a_group_goes_in_on_top_as_one_change(self):
        before = self.steps()
        mid = self.js("LD.drag('Title', 'Badge', 0.6, 60, false)")
        self.assertEqual((mid["lit"], mid["line"]), ("Badge", None))   # the group's row is lit, no line
        self.assertEqual(mid["dragged"], ["Title"])
        self.js("LD.up()")
        self.assertEqual(self.steps(), before + 1)
        want = ["a", ["g", ["g1", "g2", "t"]], ["s", ["s1", "s2"]], "b", "p", "q"]
        self.assertEqual(shape(self.page.spec["layers"]), want)
        self.assertEqual(shape(self.js("designView.design().layers")), want)
        self.assertEqual(self.js("LD.state()")["picked"], ["Title"])
        self.assertIsNone(self.js("document.querySelector('.ds-drop-line, .ds-drop-ghost, .ds-drop-into, .ds-dragged')"))
        # Undo takes the whole move back.
        self.page.on_undo({"rev": 99})
        self.wait(300)
        self.assertEqual(shape(self.js("designView.design().layers")), shape(pile()["layers"]))

    def test_a_line_shows_where_it_goes_and_its_depth_follows_the_pointer(self):
        # Under the group's bottom row: in the group (indented)...
        mid = self.js("LD.drag('Sun', 'Badge base', 0.9, 60, false)")
        self.assertEqual((mid["lit"], mid["line"]), (None, 4 + 14))
        # ...or, nearer the list's left, under the group itself.
        mid = self.js("LD.move('Badge base', 0.9, 4)")
        self.assertEqual(mid["line"], 4)
        self.js("LD.up()")
        self.wait(300)
        self.assertEqual(shape(self.page.spec["layers"]),
                         ["a", "b", ["g", ["g1", "g2"]], ["s", ["s1", "s2"]], "t", "p", "q"])

    def test_picked_rows_go_together_and_out_of_their_containers(self):
        self.js("designView.pick(['g2', 'q'])")
        before = self.steps()
        self.js("LD.drag('Closed path', 'Title', 0.1, 60, true)")    # just above the title, at the top level
        self.assertEqual(self.steps(), before + 1)
        self.assertEqual(shape(self.page.spec["layers"]),
                         ["a", ["g", ["g1"]], ["s", ["s1", "s2"]], "b", "t", "g2", "q", "p"])
        # The last one out of a group: the group goes.
        self.js("designView.pick(['g1'])")
        self.js("LD.drag('Badge base', 'Back card', 0.9, 60, true)")
        self.assertEqual(shape(self.page.spec["layers"]), ["g1", "a", ["s", ["s1", "s2"]], "b", "t", "g2", "q", "p"])

    def test_what_cant_be_dropped_and_escape_change_nothing(self):
        before = self.steps()
        mid = self.js("LD.drag('Title', 'Cut out', 0.6, 60, false)")   # text into a combined shape
        self.assertEqual((mid["lit"], mid["line"]), (None, None))
        self.js("LD.up()")
        mid = self.js("LD.drag('Title', 'Cut hole', 0.2, 60, false)")  # nor between its rows
        self.assertIsNone(mid["line"])
        self.js("LD.up()")
        self.js("LD.drag('Badge', 'Badge base', 0.6, 60, false)")      # nor a group into itself
        self.js("LD.up()")
        self.js("LD.drag('Sun', 'Back card', 0.9, 60, 'escape')")
        self.assertEqual(self.steps(), before)
        self.assertEqual(shape(self.page.spec["layers"]), shape(pile()["layers"]))
        self.assertIsNone(self.js("document.querySelector('.ds-drop-line, .ds-drop-ghost, .ds-drop-into, .ds-dragged')"))

    def test_a_click_still_picks_and_a_small_wobble_isnt_a_drag(self):
        before = self.steps()
        self.js("LD.click('Sun', 2)")
        self.assertEqual(self.js("LD.state()")["picked"], ["Sun"])
        self.js("LD.click('Title', 2, true)")
        self.assertEqual(self.js("LD.state()")["picked"], ["Title", "Sun"])     # as the list shows them, top first
        self.assertEqual(self.steps(), before)

    def test_locked_and_hidden_layers_move_too(self):
        self.page.on_design({"design": {**pile(), "layers": [{**layer, "locked": layer["id"] == "b", "hidden": layer["id"] == "t"}
                                                               for layer in pile()["layers"]]}, "rev": 50})
        self.wait(300)
        self.js("LD.drag('Sun', 'Back card', 0.9, 60, true)")       # to the bottom
        self.js("LD.drag('Title', 'Back card', 0.9, 60, true)")     # between the card and the sun under it
        self.wait(300)
        self.assertEqual(shape(self.page.spec["layers"])[:3], ["b", "t", "a"])

    def test_the_list_scrolls_while_a_row_is_held_near_its_edge(self):
        many = M.clean({"layers": [box(f"r{i}", name=f"Box {i}") for i in range(40)]})
        self.page.on_design({"design": many, "rev": 60})
        self.wait(300)
        self.js("LD.drag('Box 39', 'Box 38', 0.5, 60, false)")
        self.js("LD.edge()")
        self.wait(500)
        self.assertGreater(self.js("document.getElementById('layers').scrollTop"), 50)
        self.js("LD.up()")


# Pointer events on the layers list, as a mouse makes them.
DRIVE = r"""(() => {
  const list = document.getElementById("layers");
  const rowOf = name => [...list.querySelectorAll(".ds-layer")].find(r => r.querySelector(".ds-layer-name").textContent === name);
  let at = null;
  const pe = (target, type, x, y, more) => target.dispatchEvent(new PointerEvent(type, Object.assign(
      {bubbles: true, cancelable: true, button: 0, pointerId: 7, clientX: x, clientY: y}, more || {})));
  const state = () => {
    const line = document.querySelector(".ds-drop-line"), lit = list.querySelector(".ds-drop-into .ds-layer-name");
    const names = sel => [...list.querySelectorAll(sel)].map(r => r.querySelector(".ds-layer-name").textContent);
    return {line: line && !line.hidden ? Math.round(line.getBoundingClientRect().left - list.getBoundingClientRect().left) : null,
            lit: lit ? lit.textContent : null, dragged: names(".ds-dragged"), picked: names('[aria-selected="true"]')};
  };
  const move = (to, share, dx) => {
    const b = rowOf(to).getBoundingClientRect();
    at = [b.left + dx, b.top + b.height * share];
    pe(list, "pointermove", ...at);
    return state();
  };
  window.LD = {
    state,
    move,
    drag(from, to, share, dx, release) {
      const a = rowOf(from).getBoundingClientRect(), x0 = a.left + 60, y0 = a.top + a.height / 2;
      pe(rowOf(from).querySelector(".ds-layer-name"), "pointerdown", x0, y0);
      pe(list, "pointermove", x0, y0 + 2);
      const b = rowOf(to).getBoundingClientRect();
      pe(list, "pointermove", (x0 + b.left + dx) / 2, (y0 + b.top + b.height * share) / 2);
      const out = move(to, share, dx);
      if (release === "escape") document.body.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
      if (release) this.up();
      return out;
    },
    up() { pe(list, "pointerup", ...at); list.dispatchEvent(new MouseEvent("click", {bubbles: true})); return state(); },
    edge() { const r = list.getBoundingClientRect(); at = [r.left + 60, r.bottom - 3]; pe(list, "pointermove", ...at); },
    click(name, wobble, shift) {
      const row = rowOf(name), r = row.getBoundingClientRect(), x = r.left + 60, y = r.top + r.height / 2;
      const target = row.querySelector(".ds-layer-name");
      pe(target, "pointerdown", x, y, {shiftKey: !!shift});
      pe(target, "pointermove", x + wobble, y + 1, {shiftKey: !!shift});
      pe(target, "pointerup", x + wobble, y + 1, {shiftKey: !!shift});
      target.dispatchEvent(new MouseEvent("click", {bubbles: true, shiftKey: !!shift}));
    },
  };
  return true;
})()"""


if __name__ == "__main__":
    unittest.main()
