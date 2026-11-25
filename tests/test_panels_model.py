"""Panels (pages/panels/), the part with no Qt and no Resolve in it: a panel as data
(model.py), where its boxes sit (solve.py), how they're drawn in each style
(styles.py), what it is made from (catalogue.py) and what happens on every frame
(clock.py)."""

import hashlib
import json
import re
import time
import unittest

import _paths  # noqa: F401

from pages.data_graphics.graphs import model as graphs
from pages.panels import catalogue as C, clock as K, model as M, solve as S, styles as Y

FRAME = (1920, 1080)
ROOT = _paths.APP / "pages" / "panels"
WIDTHS = (320, 620, 1200)
PRIM_KEYS = {
    "rect": {"box", "kind", "x", "y", "w", "h", "radius", "fill", "stroke", "stroke_w", "shadow"},
    "ellipse": {"box", "kind", "x", "y", "w", "h", "fill", "stroke", "stroke_w"},
    "line": {"box", "kind", "x1", "y1", "x2", "y2", "stroke", "stroke_w"},
    "text": {"box", "kind", "x", "y", "text", "size", "bold", "align", "fill"},
    "poly": {"box", "kind", "points", "closed", "fill", "stroke", "stroke_w", "draw"},
    "wedge": {"box", "kind", "cx", "cy", "r", "hole", "from", "to", "fill", "draw"},
}
# Words that must never be in the catalogue or anywhere else in the tool, as SHA-256
# of each in lower case - so this file doesn't hold them either.
DENIED = {
    "fdee430d40bd57deeac186cd9790033d0f06f909a8806e7ce6e717ab7c7d5029",
    "80e4d1617020e166c955422ed60ab60fea455b557bb53429f3e0594801e996cb",
    "34ddf679b4b0ed44ce874ec3e11cf84359acb7a09bacaa3e50232bbdce8da5b3",
    "7fac0d0407d163311247ad4e99c8a02cda51b32a7d544507e1e4873c7e1befaf",
    "2f48b881ea7073f1c6f083b296a360bd4c9cf51edaacba1cd9c34d8ae3d994ec",
    "1ed2b38c11c70aa02adedf9fe807482472daef00689af3eeb6141346ec3f3c70",
    "3a6d64c24cf80b69ccda37650406467e8266667b50cfd0b984beb3651b129ed7",
    "84e652f08767e20de0751333973678b66cabd8ba6d5ae1015d3df79ee332423d",
    "ef260e9aa3c673af240d17a2660480361a8e081d1ffeca2a5ed0e3219fc18567",
    "b26d28f46ceba1518fad6a7122320240084a5fe4fe73a5128fffcfffa86f2907",
    "92a70799e19d8734fecfb8e59aa930e9f386f7645059a1f2f04eb8483c9b5f1d",
    "5705a82920cee05ca634e8a82a2a11be30aa0d655c7b87a67d48b828e8e46144",
    "b271dbbaedd8262b2258aecd6614a9687a02eeb5ba8cbd52b2bb43ce1134660f",
    "c9affe7f758723c3eb9fe8b143520c7d3c73fa7cacdbf1a76b34e18624cc6ecf",
    "313ce7d71787960e3bb5f8258c173ae466b4e08e1e7d24b9c7a5ba81c9a02d96",
    "7d0c43beb087fdf3c6e91475a1b1d85bae31d9d16233b4f77dec955d31b2fab3",
    "07f15cde5b181425db8524becd96263d600c9652c7de5b89fe1a644f8fd0724b",
    "a08de976b8a52c8e4b3f2227f5cb44e1ad9254ca0ed8307a8e193a23ef08165b",
}


def panel(*roots, **more):
    """A panel of these pages' trees: a bare window (no bar), 400 wide, drawn one panel
    px to a frame px in the middle of the frame, with a cursor parked off it."""
    return M.clean({"kind": "widget", "width": 400, "zoom": 1, "pages": [{"root": root} for root in roots],
                    "path": [[0, 1, 1]], "clicks": [], **more})


def placed(spec, page="p1"):
    return {b["id"]: b for b in S.solve(spec, page)["boxes"]}


def named(spec, name, page="p1"):
    return next(b for b in S.solve(spec, page)["boxes"] if b["name"] == name)


def over(spec, box, t, page="p1"):
    """A key of the cursor's path: at t seconds, on the middle of a placed box."""
    x, y = S.to_frame(spec, S.solve(spec, page)["h"], box["x"] + box["w"] / 2, box["y"] + box["h"] / 2, FRAME)
    return [t, x, y]


def words(text):
    return re.findall(r"[^\W\d_]+", text.lower())


class CatalogueTests(unittest.TestCase):
    def test_the_layouts_and_components_are_all_there(self):
        groups = [entry["group"] for entry in C.LAYOUTS.values()]
        self.assertEqual([groups.count(g) for g in C.GROUPS], [5, 9, 5, 3])
        self.assertEqual(sorted(set(groups), key=C.GROUPS.index), list(C.GROUPS))
        self.assertEqual([entry["name"] for entry in C.COMPONENTS.values()],
                         ["Heading", "Text", "Button", "Field", "Image", "Card", "Columns", "List", "Table", "Stat tiles",
                          "Line chart", "Area chart", "Bar chart", "Donut", "Gallery", "Sidebar", "Tabs", "Progress", "Badge",
                          "Divider"])
        self.assertEqual(len(C.LAYOUTS), 22)
        self.assertFalse(set(C.LAYOUTS) & set(C.COMPONENTS))            # a tile is known by its id alone
        self.assertEqual((C.KINDS, C.STYLES), (("app", "browser", "phone", "dialog", "widget"),
                                               ("mac", "plain", "glass", "windows", "cartoon")))
        self.assertIsNone(C.layout("nothing"))
        self.assertIsNone(C.component("nothing"))

    def test_every_tree_is_a_fresh_one(self):
        for make in (lambda: C.layout("dashboard"), lambda: C.component("card")):
            one, other = make(), make()
            self.assertEqual(one, other)
            one["children"].clear()
            self.assertNotEqual(one, make())

    def test_no_name_from_the_reference_is_in_the_tool(self):
        said = []
        for path in sorted(ROOT.glob("*.py")) + sorted(ROOT.glob("*.md")):
            said += words(path.read_text(encoding="utf-8"))
        for entry in [*C.LAYOUTS.values(), *C.COMPONENTS.values()]:
            said += words(json.dumps(entry["make"](), ensure_ascii=False) + " " + entry["name"])
        self.assertGreater(len(said), 1000)
        found = {w for w in set(said) if hashlib.sha256(w.encode("utf-8")).hexdigest() in DENIED}
        self.assertEqual(found, set())

    def test_the_sample_people_are_the_three_made_up_ones(self):
        self.assertEqual(C.PEOPLE, ("Tess", "Sam", "Olive"))
        text = json.dumps([entry["make"]() for entry in C.LAYOUTS.values()])
        self.assertEqual(set(re.findall(r"[\w.]+@[\w.]+", text)), {"tess@example.com"})


class ModelTests(unittest.TestCase):
    def test_anything_cleans_to_a_whole_panel(self):
        self.assertEqual(M.clean(None), M.clean({}))
        self.assertEqual(M.clean("junk"), M.clean({}))
        spec = M.clean({})
        self.assertEqual(set(spec), set(M.DEFAULTS) | {"pages", "start", "path", "clicks"})
        self.assertEqual(M.clean(spec), spec)
        self.assertEqual(json.loads(json.dumps(spec)), spec)
        junk = M.clean({"kind": "fridge", "style": 7, "accent": "blue", "dark": 0, "width": 99999, "zoom": "big",
                        "seconds": 999, "cursor": "hand", "cursor_size": 0, "font": "", "go_with": "spin", "go_seconds": -1,
                        "answers": {"hover": {"grow": 900, "glow": "x"}, "hover_seconds": 5}, "build": "yes",
                        "start": "p9", "rules": "none", "page_keys": [[1, "p9", "fade"], "x", [2, "p1"], [0.5, "p1", "spin"]],
                        "pages": ["nonsense", {"name": "  Second   page ", "layout": "nope", "root": {"role": "blob"}}],
                        "path": [[0, 10, 10], ["x", 1, 1], [0.5, 500, -5], [0.2, 1, 1], [1e9, 0, 0]],
                        "clicks": [[0.1, 0.3], [0.2, 0.4], [5, 4], [0.5, 9999]]})
        self.assertEqual((junk["kind"], junk["style"], junk["accent"], junk["dark"]), ("app", "mac", M.DEFAULTS["accent"], False))
        self.assertEqual((junk["width"], junk["zoom"], junk["seconds"]), (M.MAX_WIDTH, M.DEFAULTS["zoom"], M.MAX_SECONDS))
        self.assertEqual((junk["cursor"], junk["cursor_size"], junk["font"]), (M.DEFAULTS["cursor"], 1.0, "Arial"))
        self.assertEqual((junk["go_with"], junk["go_seconds"]), ("slide", 0.0))
        self.assertEqual(junk["answers"]["hover"], {"grow": 50.0, "lift": 2.0, "glow": 50.0})
        self.assertEqual(junk["answers"]["hover_seconds"], 1.0)
        self.assertEqual(junk["build"], M.DEFAULTS["build"])
        self.assertEqual([(p["id"], p["name"], p["layout"]) for p in junk["pages"]], [("p1", "Page 1", ""), ("p2", "Second page", "")])
        self.assertEqual([p["root"]["role"] for p in junk["pages"]], ["section", "section"])
        self.assertEqual((junk["start"], junk["rules"]), ("p1", []))
        self.assertEqual(junk["page_keys"], [[0.5, "p1", "slide"], [2.0, "p1", "slide"]])
        self.assertEqual(junk["path"], [[0.0, 10.0, 10.0], [0.5, 100.0, 0.0]])
        self.assertEqual(junk["clicks"], [[0.1, 0.3], [0.5, 60.0]])
        self.assertEqual(M.clean(junk), junk)

    def test_a_box_is_made_whole_and_what_isnt_one_is_dropped(self):
        spec = panel({"role": "section", "dir": "row", "pad": 5, "children": [
            {"role": "text", "text": "  two   words ", "variant": "huge", "w": -3},
            {"role": "wobble"}, "nonsense",
            {"role": "button", "text": "Go", "children": [{"role": "text"}], "go": {"page": "p1"}},
            {"role": "image", "aspect": 0}, {"role": "chart", "chart": {"shape": "donut", "rows": "?"}},
            {"role": "section", "variant": "card"}]})
        root = spec["pages"][0]["root"]
        self.assertEqual(set(root), {"id", "role", "name", "children", *M.BOX_DEFAULTS})
        self.assertEqual((root["pad"], root["gap"], root["align"], root["name"]), ([5.0] * 4, 8.0, "center", "Row"))
        text, button, image, chart, card = root["children"]
        self.assertEqual((text["text"], text["variant"], text["w"], text["reacts"]), ("two words", "body", 1.0, False))
        self.assertEqual((button["children"], button["w"], button["reacts"], button["go"]), ([], "hug", True, None))
        self.assertAlmostEqual(image["aspect"], 16 / 9, places=3)
        self.assertEqual((chart["chart"]["shape"], chart["aspect"], len(chart["chart"]["rows"])), ("donut", 1.0, 7))
        self.assertEqual((card["name"], card["reacts"], card["w"], card["dir"]), ("Card", True, "fill", "column"))
        self.assertIsNone(text["chart"])
        in_a_column = panel({"role": "section", "children": [{"role": "text", "text": "a"}]})
        self.assertEqual(in_a_column["pages"][0]["root"]["children"][0]["w"], "fill")

    def test_every_layout_cleans_and_stays_as_cleaned(self):
        for layout_id, entry in C.LAYOUTS.items():
            spec = M.clean({"pages": [{"layout": layout_id, "root": entry["make"]()}], "path": []})
            self.assertEqual(M.clean(spec), spec, layout_id)
            self.assertEqual(spec["pages"][0]["layout"], layout_id)
            self.assertGreater(len(list(M.boxes(spec))), 5, layout_id)

    def test_ids_are_unique_across_the_whole_panel(self):
        ids = list(C.LAYOUTS)
        for chunk in (ids[:8], ids[8:16], ids[16:]):
            spec = M.clean({"pages": [{"root": C.layout(i)} for i in chunk], "path": []})
            found = [box["id"] for box in M.boxes(spec)]
            self.assertEqual(len(found), len(set(found)))
            self.assertTrue(all(re.fullmatch(r"b[1-9]\d*", i) for i in found))
            self.assertEqual([p["id"] for p in spec["pages"]], [f"p{n}" for n in range(1, len(chunk) + 1)])
        twice = panel({"role": "section", "id": "b7", "children": [{"role": "text", "id": "b7"}, {"role": "text", "id": "x"},
                                                                   {"role": "text", "id": "b2"}]})
        self.assertEqual([box["id"] for box in M.boxes(twice)], ["b7", "b8", "b9", "b2"])     # kept where it can be

    def test_a_page_has_only_so_many_boxes_and_a_panel_so_many_pages(self):
        many = panel({"role": "section", "children": [{"role": "divider"}] * (M.MAX_BOXES + 50)})
        self.assertEqual(len(list(M.boxes(many))), M.MAX_BOXES)
        self.assertEqual(len(panel(*[{"role": "section"}] * 12)["pages"]), M.MAX_PAGES)
        self.assertEqual(len(M.clean({"pages": []})["pages"]), 1)

    def test_a_new_panel_is_the_template(self):
        spec = M.clean({})
        self.assertTrue(M.is_template(spec))
        self.assertFalse(M.is_template(M.clean({**spec, "width": 700})))
        self.assertEqual((spec["kind"], spec["style"]), ("app", "mac"))
        self.assertEqual([(p["name"], p["layout"]) for p in spec["pages"]],
                         [("Sign in", "signin"), ("Dashboard", "dashboard"), ("Settings", "settings")])
        linked = [box for box in M.boxes(spec) if box["go"]]
        self.assertEqual([(b["role"], b["text"], b["go"]) for b in linked], [("button", "Sign in", {"page": "p2", "with": ""})])
        # The whole window is on a 16:9 frame, and the cursor's click lands on that button.
        solved = S.solve(spec, "p1")
        left, top = S.to_frame(spec, solved["h"], 0, 0, FRAME)
        right, bottom = S.to_frame(spec, solved["h"], solved["w"], solved["h"], FRAME)
        self.assertTrue(0 < left < right < 100 and 0 < top < bottom < 100)
        for fps in (24, 30, 60):
            played = K.clock(spec, fps, FRAME)
            self.assertEqual([(c["page"], c["why"], c["box"]) for c in played["pages"]],
                             [("p1", "start", ""), ("p2", "click", linked[0]["id"])], fps)
        # A panel with pages of its own gets no template cursor.
        mine = M.clean({"pages": [{}]})
        self.assertEqual((mine["path"], mine["clicks"], mine["title"]), ([[0.0, 50.0, 50.0]], [], ""))
        self.assertEqual(M.clean({"style": "glass"})["pages"], spec["pages"])

    def test_a_box_is_found_and_changed(self):
        spec = M.clean({})
        button = next(box for box in M.boxes(spec) if box["go"])
        found = M.find(spec, button["id"])
        self.assertEqual((found["box"], found["page"]["id"], found["parent"]["id"]),
                         (button, "p1", spec["pages"][0]["root"]["id"]))
        self.assertIsNone(M.find(spec, spec["pages"][0]["root"]["id"])["parent"])
        self.assertIsNone(M.find(spec, "b999"))
        made = M.with_box(spec, button["id"], "text", "Enter")
        self.assertEqual(M.find(made, button["id"])["box"]["text"], "Enter")
        self.assertEqual(M.find(spec, button["id"])["box"]["text"], "Sign in")             # the old one is as it was
        self.assertEqual(M.find(M.with_box(spec, button["id"], "go", {"page": "p3", "with": "fade"}), button["id"])["box"]["go"],
                         {"page": "p3", "with": "fade"})
        self.assertIsNone(M.find(M.with_box(spec, button["id"], "go", None), button["id"])["box"]["go"])
        self.assertEqual(M.find(M.with_box(spec, button["id"], "w", 120), button["id"])["box"]["w"], 120.0)
        self.assertIsNone(M.with_box(spec, button["id"], "text", "Sign in"))                # changes nothing
        self.assertIsNone(M.with_box(spec, button["id"], "role", "text"))
        self.assertIsNone(M.with_box(spec, button["id"], "children", []))
        self.assertIsNone(M.with_box(spec, "b999", "text", "x"))

    def test_a_component_is_put_in_and_a_box_taken_out(self):
        spec = panel({"role": "section", "children": [{"role": "text", "text": "a"}, {"role": "text", "text": "b"}]})
        root = spec["pages"][0]["root"]
        first, second = root["children"]
        end = M.with_insert(spec, "button", root["id"])
        self.assertEqual([b["role"] for b in end["pages"][0]["root"]["children"]], ["text", "text", "button"])
        self.assertEqual([b["id"] for b in end["pages"][0]["root"]["children"]][:2], [first["id"], second["id"]])
        start = M.with_insert(spec, "card", root["id"], 0)
        self.assertEqual([b["name"] for b in start["pages"][0]["root"]["children"]], ["Card", "Text", "Text"])
        after = M.with_insert(spec, "badge", first["id"])                                   # a text holds nothing
        self.assertEqual([b["role"] for b in after["pages"][0]["root"]["children"]], ["text", "badge", "text"])
        ids = [box["id"] for box in M.boxes(start)]
        self.assertEqual(len(ids), len(set(ids)))
        for component in C.COMPONENTS:
            self.assertIsNotNone(M.with_insert(spec, component, root["id"]), component)
        self.assertIsNone(M.with_insert(spec, "nothing", root["id"]))
        self.assertIsNone(M.with_insert(spec, "button", "b999"))
        full = panel({"role": "section", "children": [{"role": "divider"}] * (M.MAX_BOXES - 1)})
        self.assertIsNone(M.with_insert(full, "button", full["pages"][0]["root"]["id"]))
        gone = M.without_box(spec, first["id"])
        self.assertEqual([b["id"] for b in gone["pages"][0]["root"]["children"]], [second["id"]])
        self.assertIsNone(M.without_box(spec, root["id"]))                                  # a page keeps its root
        self.assertIsNone(M.without_box(spec, "b999"))

    def test_pages_are_added_named_made_over_and_removed(self):
        spec = M.clean({})
        more = M.with_page(spec)
        self.assertEqual([(p["id"], p["name"], p["layout"]) for p in more["pages"]][-1], ("p4", "Page 4", ""))
        self.assertEqual(more["pages"][-1]["root"]["children"], [])
        full = spec
        while len(full["pages"]) < M.MAX_PAGES:
            full = M.with_page(full)
        self.assertIsNone(M.with_page(full))
        self.assertEqual(M.with_page_name(spec, "p2", "  Home ")["pages"][1]["name"], "Home")
        self.assertIsNone(M.with_page_name(spec, "p2", "Dashboard"))
        self.assertIsNone(M.with_page_name(spec, "p2", "   "))
        self.assertIsNone(M.with_page_name(spec, "p9", "Home"))
        chat = M.with_layout(more, "p4", "chat")
        self.assertEqual((chat["pages"][3]["layout"], chat["pages"][3]["name"]), ("chat", "Chat"))
        self.assertGreater(len(chat["pages"][3]["root"]["children"]), 3)
        kept = M.with_layout(M.with_page_name(spec, "p2", "Home"), "p2", "mail")
        self.assertEqual((kept["pages"][1]["layout"], kept["pages"][1]["name"]), ("mail", "Home"))
        self.assertIsNone(M.with_layout(spec, "p2", "nothing"))
        self.assertIsNone(M.with_layout(spec, "p9", "mail"))
        ids = [box["id"] for box in M.boxes(chat)]
        self.assertEqual(len(ids), len(set(ids)))
        # Without the page a link went to, the link is gone - and so are its rules and keys.
        ruled = M.with_rule(M.clean({**spec, "page_keys": [[1, "p2", "fade"], [2, "p3", "fade"]]}), "", "page", "p3")
        self.assertEqual(len(ruled["rules"]), 1)
        less = M.without_page(ruled, "p3")
        self.assertEqual(([p["id"] for p in less["pages"]], less["rules"], less["page_keys"]), (["p1", "p2"], [], [[1.0, "p2", "fade"]]))
        self.assertEqual([box["go"] for box in M.boxes(M.without_page(spec, "p2")) if box["go"]], [])
        self.assertEqual(M.without_page(spec, "p1")["start"], "p2")
        self.assertIsNone(M.without_page(spec, "p9"))
        self.assertIsNone(M.without_page(M.clean({"pages": [{}]}), "p1"))

    def test_answers_are_the_panels_or_a_boxes_own(self):
        spec = M.clean({})
        button = next(box for box in M.boxes(spec) if box["go"])["id"]
        self.assertEqual(M.with_answers(spec, "", "hover", "grow", 9)["answers"]["hover"]["grow"], 9.0)
        self.assertEqual(M.with_answers(spec, "", "press", "seconds", 0.3)["answers"]["press_seconds"], 0.3)
        self.assertIsNone(M.with_answers(spec, "", "hover", "grow", 4))
        self.assertIsNone(M.with_answers(spec, "", "hover", "spin", 4))
        self.assertIsNone(M.with_answers(spec, "", "drag", "grow", 4))
        own = M.with_answers(spec, button, "press", "glow", 80)
        self.assertEqual(M.find(own, button)["box"]["states"],
                         {"hover": spec["answers"]["hover"], "press": {**spec["answers"]["press"], "glow": 80.0}})
        self.assertEqual(own["answers"], spec["answers"])
        self.assertIsNone(M.find(M.with_answers(own, button, "press", "reset", None), button)["box"]["states"])
        self.assertIsNone(M.with_answers(spec, button, "press", "reset", None))
        self.assertIsNone(M.with_answers(spec, "b999", "press", "glow", 1))

    def test_rules_are_made_changed_and_removed(self):
        spec = M.clean({})
        self.assertEqual(spec["rules"], [])
        made = M.with_rule(spec, "", "", None)
        (rule,) = made["rules"]
        watched = M.find(made, rule["box"])
        self.assertEqual((rule["id"], rule["over"], rule["with"], watched["box"]["role"], watched["page"]["id"], rule["page"]),
                         ("r1", 60.0, "match", "chart", "p2", "p3"))
        self.assertEqual(M.with_rule(made, "r1", "over", 25)["rules"][0]["over"], 25.0)
        self.assertEqual(M.with_rule(made, "r1", "with", "fade")["rules"][0]["with"], "fade")
        self.assertEqual(M.with_rule(made, "r1", "page", "p1")["rules"][0]["page"], "p1")
        self.assertIsNone(M.with_rule(made, "r1", "page", "p9"))
        self.assertIsNone(M.with_rule(made, "r1", "box", next(M.boxes(spec))["id"]))        # nothing drawn on to watch
        self.assertIsNone(M.with_rule(made, "r1", "over", 60))
        self.assertIsNone(M.with_rule(made, "r9", "over", 10))
        self.assertIsNone(M.with_rule(made, "r1", "colour", "red"))
        self.assertIsNone(M.with_rule(M.clean({"pages": [{"root": C.layout("signin")}, {}]}), "", "", None))
        full = made
        while len(full["rules"]) < M.MAX_RULES:
            full = M.with_rule(full, "", "over", 10 + len(full["rules"]))
        self.assertEqual(len({r["id"] for r in full["rules"]}), M.MAX_RULES)
        self.assertIsNone(M.with_rule(full, "", "", None))
        self.assertEqual(M.without_rule(made, "r1")["rules"], [])
        self.assertIsNone(M.without_rule(made, "r9"))

    def test_a_recording_is_thinned_and_sets_the_length(self):
        spec = M.clean({})
        samples = [[i / 10, 10 + i, 20.0] for i in range(31)]                 # a straight move: two keys say it
        made = M.with_recording(spec, samples, [[0.5, 0.7]], 3.0)
        self.assertEqual((made["seconds"], made["path"], made["clicks"]), (3.0, [[0.0, 10.0, 20.0], [3.0, 40.0, 20.0]], [[0.5, 0.7]]))
        self.assertEqual(made["pages"], spec["pages"])
        self.assertIsNone(M.with_recording(made, samples, [[0.5, 0.7]], 3.0))

    def test_frames_faces_and_the_id(self):
        spec = M.clean({})
        self.assertEqual((M.frames(spec, 24), K.frames(spec, 24), M.frames(M.clean({"seconds": 0}), 1)), (144, 144, 2))
        self.assertEqual(M.faces(spec), [("Arial", "Regular"), ("Arial", "Bold")])
        self.assertEqual(M.faces(M.clean({"bold_face": "Regular"})), [("Arial", "Regular")])
        one = M.panel_id(spec, FRAME, 24)
        self.assertRegex(one, r"^bp-[0-9a-f]{12}$")
        self.assertEqual(one, M.panel_id(M.clean({}), FRAME, 24))
        self.assertNotEqual(one, M.panel_id(spec, FRAME, 25))
        self.assertNotEqual(one, M.panel_id(M.clean({"dark": False}), FRAME, 24))


class SolveTests(unittest.TestCase):
    def check(self, spec, page, where):
        solved = S.solve(spec, page)
        self.assertEqual(solved["w"], spec["width"], where)
        kids = {}
        for box in solved["boxes"]:
            self.assertGreaterEqual(box["w"], 0, (where, box["name"]))
            self.assertGreaterEqual(box["h"], 0, (where, box["name"]))
            self.assertGreaterEqual(box["x"], -0.01, (where, box["name"]))
            self.assertGreaterEqual(box["y"], solved["bar"] - 0.01, (where, box["name"]))
            self.assertLessEqual(box["x"] + box["w"], solved["w"] + 0.01, (where, box["name"]))
            self.assertLessEqual(box["y"] + box["h"], solved["h"] + 0.01, (where, box["name"]))
            kids.setdefault(box["parent"], []).append(box)
        for parent, group in kids.items():
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    apart = (a["x"] + a["w"] <= b["x"] + 0.01 or b["x"] + b["w"] <= a["x"] + 0.01
                             or a["y"] + a["h"] <= b["y"] + 0.01 or b["y"] + b["h"] <= a["y"] + 0.01)
                    self.assertTrue(apart, (where, parent, a["name"], b["name"]))
        return solved

    def test_every_layout_and_component_fits_every_window(self):
        trees = [(i, e["make"]) for i, e in C.LAYOUTS.items()] + [(i, lambda e=e: C.col([e["make"]()], pad=12))
                                                                  for i, e in C.COMPONENTS.items()]
        for name, make in trees:
            for width in WIDTHS:
                for kind in ("app", "phone"):
                    spec = M.clean({"kind": kind, "width": width, "pages": [{"root": make()}], "path": []})
                    solved = self.check(spec, "p1", (name, width, kind))
                    self.assertGreater(len(solved["boxes"]), 1)

    def test_the_window_is_as_tall_as_its_tallest_page_and_a_phone_taller(self):
        spec = M.clean({})
        solved = S.solve_all(spec)
        self.assertEqual(len({page["h"] for page in solved.values()}), 1)
        self.assertEqual(S.solve(spec, "p2"), solved["p2"])
        self.assertEqual(S.solve(spec, "p9"), solved["p1"])
        tallest = max(max(b["y"] + b["h"] for b in page["boxes"][1:]) for page in solved.values())
        self.assertAlmostEqual(solved["p1"]["h"], tallest + 10, places=2)                   # the dashboard's own padding
        self.assertEqual((solved["p1"]["bar"], solved["p1"]["area"][:2]), (S.BAR["app"], [0.0, S.BAR["app"]]))
        phone = S.solve(M.clean({"kind": "phone", "width": 360, "pages": [{"root": C.layout("signin")}]}), "p1")
        self.assertAlmostEqual(phone["h"], 360 * S.PHONE_ASPECT, places=2)
        self.assertEqual(phone["area"][0], S.INSET["phone"])
        self.assertEqual(S.solve(panel({"role": "section"}), "p1")["bar"], 0.0)

    def test_fixed_hug_and_fill_share_a_row(self):
        spec = panel({"role": "section", "dir": "row", "gap": 10, "children": [
            {"role": "image", "w": 50, "h": 20, "name": "fixed"}, {"role": "text", "text": "abcd", "name": "hug"},
            {"role": "field", "name": "fill"}, {"role": "field", "maxw": 60, "name": "capped"}]})
        fixed, hug, fill, capped = (named(spec, n) for n in ("fixed", "hug", "fill", "capped"))
        self.assertEqual((fixed["w"], fixed["h"], hug["w"]), (50.0, 20.0, 4 * S.CHAR_W["body"] * S.SIZE["body"]))
        self.assertEqual(capped["w"], 60.0)
        self.assertAlmostEqual(fill["w"], 400 - 50 - 26 - 60 - 30, places=3)
        self.assertEqual([round(b["x"], 3) for b in (fixed, hug, fill, capped)], [0.0, 60.0, 96.0, 340.0])
        # Across the line a row centres to start with: the short text sits half way down the field's height.
        self.assertAlmostEqual(hug["y"] + hug["h"] / 2, fill["y"] + fill["h"] / 2, places=3)

    def test_children_that_ask_for_too_much_are_all_narrowed(self):
        spec = panel({"role": "section", "dir": "row", "gap": 0, "children": [{"role": "image", "w": 300, "h": 10}] * 2})
        self.assertEqual([b["w"] for b in S.solve(spec, "p1")["boxes"][1:]], [200.0, 200.0])

    def test_min_width_wrap_and_equal_columns(self):
        def tiles(**more):
            return panel({"role": "section", "dir": "row", "gap": 10, **more,
                          "children": [{"role": "section", "minw": 150, "minh": 20}] * 3})
        one_line = [b for b in S.solve(tiles(), "p1")["boxes"][1:]]
        self.assertEqual(len({b["y"] for b in one_line}), 1)                                # no wrap: narrowed to fit
        self.assertAlmostEqual(one_line[0]["w"], 380 / 3, places=3)
        wrapped = [b for b in S.solve(tiles(wrap=True), "p1")["boxes"][1:]]
        self.assertEqual([(b["x"], b["y"], b["w"]) for b in wrapped], [(0.0, 0.0, 195.0), (205.0, 0.0, 195.0), (0.0, 30.0, 400.0)])
        equal = [b for b in S.solve(tiles(equal=120), "p1")["boxes"][1:]]
        self.assertEqual([(round(b["x"], 2), b["y"]) for b in equal], [(0.0, 0.0), (136.67, 0.0), (273.33, 0.0)])
        self.assertEqual(len({b["w"] for b in equal}), 1)
        five = panel({"role": "section", "dir": "row", "gap": 10, "equal": 120,
                      "children": [{"role": "image", "aspect": 1}] * 5})
        cells = S.solve(five, "p1")["boxes"][1:]
        self.assertEqual(len({b["w"] for b in cells}), 1)                                   # the last line isn't stretched
        self.assertEqual([b["y"] > 0 for b in cells], [False, False, False, True, True])
        narrow = [b["w"] for b in S.solve(M.clean({**five, "width": 320, "pages": five["pages"]}), "p1")["boxes"][1:]]
        self.assertEqual(narrow, [155.0] * 5)

    def test_aspect_hide_below_and_center(self):
        tree = {"role": "section", "children": [
            {"role": "image", "aspect": 2, "name": "picture"}, {"role": "text", "text": "wide only", "hide_below": 500, "name": "extra"},
            {"role": "section", "maxw": 100, "minh": 10, "center": True, "name": "middle"},
            {"role": "text", "text": "x", "center": True, "name": "title"}]}
        spec = panel(tree)
        self.assertEqual((named(spec, "picture")["w"], named(spec, "picture")["h"]), (400.0, 200.0))
        self.assertNotIn("extra", [b["name"] for b in S.solve(spec, "p1")["boxes"]])
        self.assertIn("extra", [b["name"] for b in S.solve(panel(tree, width=500), "p1")["boxes"]])
        self.assertEqual((named(spec, "middle")["x"], named(spec, "middle")["w"]), (150.0, 100.0))
        self.assertEqual((named(spec, "title")["w"], named(spec, "title")["center"]), (400.0, True))
        # A page narrower than the window, in the middle of it: the sign-in form.
        form = S.solve(M.clean({"width": 620, "pages": [{"root": C.layout("signin")}]}), "p1")["boxes"][0]
        self.assertEqual((form["w"], form["x"]), (340.0, 140.0))

    def test_align_and_justify(self):
        def row(**more):
            return panel({"role": "section", "dir": "row", "gap": 0, "h": 100, **more, "children": [
                {"role": "image", "w": 40, "h": 20, "name": "a"}, {"role": "section", "w": 40, "name": "b"}]})
        a, b = named(row(), "a"), named(row(), "b")
        self.assertEqual((a["x"], a["y"], b["x"], b["y"], b["h"]), (0.0, 40.0, 40.0, 50.0, 0.0))
        a, b = named(row(align="end", justify="end"), "a"), named(row(align="end", justify="end"), "b")
        self.assertEqual((a["x"], a["y"], b["x"], b["y"]), (320.0, 80.0, 360.0, 100.0))
        a, b = named(row(align="stretch", justify="center"), "a"), named(row(align="stretch", justify="center"), "b")
        self.assertEqual((a["x"], a["y"], a["h"], b["h"]), (160.0, 0.0, 20.0, 100.0))       # only a section stretches
        column = panel({"role": "section", "h": 100, "justify": "end", "align": "center", "children": [
            {"role": "image", "w": 40, "h": 20, "name": "a"}]})
        self.assertEqual((named(column, "a")["x"], named(column, "a")["y"]), (180.0, 80.0))
        filled = panel({"role": "section", "h": 100, "children": [
            {"role": "image", "w": 40, "h": 20}, {"role": "section", "h": "fill", "name": "rest"}]})
        self.assertEqual((named(filled, "rest")["y"], named(filled, "rest")["h"]), (28.0, 72.0))

    def test_text_is_measured_by_rule_and_broken_into_the_lines_drawn(self):
        self.assertEqual(S.broken("one two three four", 5.0, 40.0), ["one two", "three", "four"])
        self.assertEqual(S.broken("abcdefghij kl", 5.0, 20.0), ["abcd", "efgh", "ij", "kl"])
        self.assertEqual(S.broken("", 5.0, 20.0), [])
        spec = panel({"role": "section", "w": 100, "children": [
            {"role": "text", "text": "aaaa bbbb cccc dddd", "name": "words"},
            {"role": "text", "variant": "h1", "text": "Big", "name": "title"},
            {"role": "button", "text": "A very long label indeed", "w": "fill", "name": "button"}]})
        text, title, button = (named(spec, n) for n in ("words", "title", "button"))
        self.assertEqual((text["lines"], text["size"]), (["aaaa bbbb cccc", "dddd"], 13.0))
        self.assertAlmostEqual(text["h"], 2 * 13.0 * S.LINE)
        self.assertEqual((title["lines"], title["size"], title["h"]), (["Big"], 22.0, round(22.0 * S.LINE, 3)))
        self.assertEqual((button["lines"], button["h"]), (["A very lo…"], S.BUTTON["h"]))
        self.assertEqual(named(spec, "Column")["size"], 0.0)

    def test_a_placed_box_carries_its_look_and_its_parent(self):
        spec = M.clean({})
        boxes = S.solve(spec, "p2")["boxes"]
        self.assertEqual({"id", "role", "variant", "name", "x", "y", "w", "h", "depth", "parent", "lines", "size", "text", "right",
                          "strong", "on", "center", "p", "chart", "hue", "reacts", "image", "type", "arrive", "type_lines"},
                         set(boxes[0]))
        seen = {""}
        for box in boxes:
            self.assertIn(box["parent"], seen)                                             # a parent before its children
            seen.add(box["id"])
        self.assertEqual((boxes[0]["depth"], boxes[0]["parent"]), (0, ""))
        sidebar = next(b for b in boxes if b["name"] == "Sidebar")
        self.assertAlmostEqual(sidebar["h"], S.solve(spec, "p2")["area"][3] - 20, places=2)  # down the whole window

    def test_places_on_the_frame_and_in_the_panel_agree(self):
        spec = M.clean({"zoom": 2, "x": 40, "y": 60})
        for frame in (FRAME, (1080, 1920), (3840, 2160)):
            x, y = S.to_panel(spec, 500, *S.to_frame(spec, 500, 123.0, 45.0, frame), frame)
            self.assertAlmostEqual(x, 123.0)
            self.assertAlmostEqual(y, 45.0)
        self.assertEqual(S.to_panel(spec, 500, 40, 60), (spec["width"] / 2, 250.0))
        self.assertEqual(S.to_panel(spec, 500, 90, 60)[0], spec["width"] / 2 + 960 / 2)     # half a frame is 960 px, at zoom 2


class StylesTests(unittest.TestCase):
    def under(self, spec, prims, n):
        """The opaque colour under the nth prim (a line of text), from the fills laid before it."""
        text = prims[n]
        x = text["x"] + {"left": 2, "center": 0, "right": -2}[text["align"]]
        y = text["y"] - text["size"] * 0.35
        ground = Y.backdrop(spec)
        for prim in prims[:n]:
            if prim["kind"] == "rect" and prim["fill"] and prim["x"] <= x <= prim["x"] + prim["w"] \
                    and prim["y"] <= y <= prim["y"] + prim["h"]:
                ground = Y.over(prim["fill"], ground)
        return ground

    def check(self, spec, where):
        for page in spec["pages"]:
            prims = Y.drawn(spec, page["id"])
            self.assertGreater(len(prims), 1, where)
            for n, prim in enumerate(prims):
                self.assertEqual(set(prim) - {"draw"}, PRIM_KEYS[prim["kind"]] - {"draw"}, where)
                for key in ("fill", "stroke"):
                    self.assertRegex(prim.get(key) or "#00000000", r"^#[0-9a-f]{8}$", (where, prim))
                if prim["kind"] == "text":
                    self.assertTrue(prim["text"], where)
                    ground = self.under(spec, prims, n)
                    self.assertGreaterEqual(Y.contrast(prim["fill"][:7], ground), 4.5, (where, page["id"], prim["text"], ground))
                    self.assertEqual(prim["fill"][7:], "ff", where)
        return prims

    def test_every_kind_in_every_style_draws_and_can_be_read(self):
        template = M.clean({})
        for kind in C.KINDS:
            for style in C.STYLES:
                for dark in (True, False):
                    self.check(M.clean({**template, "kind": kind, "style": style, "dark": dark}), (kind, style, dark))

    def test_every_layout_can_be_read_in_every_style_and_any_accent(self):
        ids = list(C.LAYOUTS)
        for style in C.STYLES:
            for dark, accent in ((True, "#ffe000"), (False, "#ffe000"), (True, "#101060"), (False, "#808080")):
                for chunk in (ids[:8], ids[8:16], ids[16:]):
                    spec = M.clean({"style": style, "dark": dark, "accent": accent, "path": [],
                                    "pages": [{"root": C.layout(i), "layout": i} for i in chunk]})
                    self.check(spec, (style, dark, accent))

    def test_contrast_and_legible(self):
        self.assertAlmostEqual(Y.contrast("#000000", "#ffffff"), 21.0)
        self.assertAlmostEqual(Y.contrast("#777777", "#777777"), 1.0)
        self.assertEqual(Y.over("#ffffff80", "#000000"), "#808080")
        self.assertEqual(Y.over("", "#123456"), "#123456")
        self.assertEqual(Y.legible("#ffffffff", "#000000"), "#ffffffff")                    # already clear: left alone
        for ground in ("#000000", "#ffffff", "#777777", "#3c82e0", "#ffe000"):
            for colour in ("#777777", "#3c82e0", "#ffe000", "#000000"):
                self.assertGreaterEqual(Y.contrast(Y.legible(colour, ground)[:7], ground), 4.5, (colour, ground))

    def test_each_kind_of_window_has_its_own_chrome(self):
        def chrome(kind, style="mac", **more):
            return Y.chrome(M.clean({"kind": kind, "style": style, **more}))
        app = chrome("app")
        self.assertEqual(len([p for p in app if p["kind"] == "ellipse"]), 3)                # the three lights
        self.assertEqual([p["text"] for p in app if p["kind"] == "text"], ["Booking app"])
        self.assertTrue(all(p["box"] == "" for p in app))
        self.assertEqual([p["text"] for p in Y.chrome(M.clean({"title": ""}), "p2") if p["kind"] == "text"], ["Dashboard"])
        self.assertEqual([p["text"] for p in Y.chrome(M.clean({"title": ""})) if p["kind"] == "text"], ["Sign in"])
        browser = [p["text"] for p in chrome("browser") if p["kind"] == "text"]
        self.assertEqual(browser, ["Booking app", "example.com"])
        dialog = chrome("dialog")
        self.assertEqual((len([p for p in dialog if p["kind"] == "ellipse"]), len([p for p in dialog if p["kind"] == "line"])), (0, 2))
        widget = chrome("widget")
        self.assertEqual(([p["kind"] for p in widget], widget[0]["radius"] >= 18), (["rect"], True))
        phone = chrome("phone")
        self.assertEqual([p["text"] for p in phone if p["kind"] == "text"], ["9:41"])
        self.assertGreaterEqual(phone[0]["radius"], 30)
        spec = M.clean({})
        self.assertEqual(Y.drawn(spec, "p1")[:len(app)], app)                               # the chrome first
        self.assertEqual({p["box"] for p in Y.drawn(spec, "p1")[len(app):]} - {b["id"] for b in M.boxes(spec)}, set())

    def test_the_styles_differ_as_they_should(self):
        def drawn(style, dark=True):
            spec = M.clean({"style": style, "dark": dark})
            return spec, Y.drawn(spec, "p2")
        spec, windows = drawn("windows")
        width = spec["width"]
        glyphs = [p for p in Y.chrome(spec) if p["kind"] == "line"]
        self.assertTrue(glyphs and all(p["x1"] > width / 2 for p in glyphs))                # the bar's buttons at the right
        self.assertFalse([p for p in Y.chrome(spec) if p["kind"] == "ellipse"])
        self.assertLess(windows[0]["radius"], Y.drawn(M.clean({"style": "glass"}), "p2")[0]["radius"])
        lights = [p for p in Y.chrome(M.clean({})) if p["kind"] == "ellipse"]
        self.assertTrue(all(p["x"] < width / 2 for p in lights))
        for dark in (True, False):
            spec, cartoon = drawn("cartoon", dark)
            card = next(b for b in S.solve(spec, "p2")["boxes"] if b["variant"] == "card")
            shadow, body = [p for p in cartoon if p["box"] == card["id"] and p["kind"] == "rect"][:2]
            self.assertEqual((shadow["x"] - body["x"], shadow["y"] - body["y"], shadow["shadow"], body["shadow"]), (4.0, 4.0, 0.0, 0.0))
            self.assertGreaterEqual(body["stroke_w"], 2.0)
            self.assertEqual((body["fill"][7:], body["stroke"][7:]), ("ff", "ff"))          # flat fills, solid outlines
            self.assertLess(Y.luminance(body["stroke"][:7]), 0.05)
            spec, glass = drawn("glass", dark)
            self.assertLess(int(glass[0]["fill"][7:], 16), 255)                             # see-through
            card = next(b for b in S.solve(spec, "p2")["boxes"] if b["variant"] == "card")
            body = next(p for p in glass if p["box"] == card["id"])
            self.assertLessEqual(int(body["fill"][7:], 16), 128)
            self.assertGreater(Y.luminance(body["stroke"][:7]), 0.9)                        # light strokes
        for style, table in Y.STYLES.items():
            self.assertEqual(set(table["dark"]), set(table["light"]), style)
            self.assertEqual(set(table["radius"]), set(Y.STYLES["mac"]["radius"]), style)
        self.assertEqual(set(Y.STYLES), set(C.STYLES))

    def chart(self, shape, rows=None, **more):
        rows = rows or [{"label": "a", "value": 1}, {"label": "b", "value": 3}, {"label": "c", "value": 2}]
        spec = panel({"role": "section", "pad": 20, "children": [{"role": "chart", "chart": {"shape": shape, "rows": rows}}]}, **more)
        box = S.solve(spec, "p1")["boxes"][1]
        return box, [p for p in Y.drawn(spec, "p1") if p["box"] == box["id"]]

    def test_charts_are_drawn_from_graphs_own_plan(self):
        box, bars = self.chart("bars")
        grown = [p for p in bars if p.get("draw") == "up"]
        self.assertEqual(len(grown), 3)
        self.assertEqual(sorted(p["h"] for p in grown), [p["h"] for p in (grown[0], grown[2], grown[1])])   # 1 < 2 < 3
        self.assertAlmostEqual(grown[1]["h"] / grown[0]["h"], 3.0, places=1)
        self.assertEqual({p["fill"] for p in grown}, {"#3c82e0ff"})
        self.assertEqual([p["text"] for p in bars if p["kind"] == "text"], ["a", "b", "c"])
        box, line = self.chart("line")
        (stroke,) = [p for p in line if p["kind"] == "poly"]
        self.assertEqual((stroke["draw"], stroke["closed"], stroke["fill"], len(stroke["points"])), (True, False, "", 3))
        self.assertLess(stroke["points"][1][1], stroke["points"][0][1])                     # more is higher
        box, area = self.chart("area")
        fill, stroke = [p for p in area if p["kind"] == "poly"]
        # The room under the line wipes on with it ("across"); the line itself draws on by length.
        self.assertEqual((fill["closed"], fill["draw"], fill["stroke"], stroke["draw"]), (True, "across", "", True))
        self.assertEqual(fill["fill"], "#3c82e0" + graphs.FILL_ALPHA)
        box, spark = self.chart("spark")
        self.assertEqual([p["kind"] for p in spark], ["poly"])                              # no axes, no labels
        box, pie = self.chart("pie")
        self.assertEqual([p["kind"] for p in pie], ["wedge"] * 3)
        self.assertEqual(([p["from"] for p in pie], pie[-1]["to"], {p["hole"] for p in pie}), ([0.0, 60.0, 240.0], 360.0, {0.0}))
        self.assertEqual(len({p["fill"] for p in pie}), 3)
        self.assertAlmostEqual(pie[0]["r"] * 2, min(box["w"], box["h"]), places=1)
        box, donut = self.chart("donut")
        self.assertAlmostEqual(donut[0]["hole"], donut[0]["r"] * graphs.HOLE, places=1)
        self.assertEqual((donut[0]["cx"], donut[0]["cy"]), (box["x"] + box["w"] / 2, box["y"] + box["h"] / 2))

    def test_a_chart_stays_in_its_box(self):
        for shape in M.CHART_SHAPES:
            for width in (320, 900):
                box, prims = self.chart(shape, C.rows_of(C.MONTHS, 4), width=width)
                self.assertTrue(prims, shape)
                for prim in prims:
                    if prim["kind"] == "rect":
                        xs, ys = [prim["x"], prim["x"] + prim["w"]], [prim["y"], prim["y"] + prim["h"]]
                    elif prim["kind"] == "poly":
                        xs, ys = [p[0] for p in prim["points"]], [p[1] for p in prim["points"]]
                    elif prim["kind"] == "line":
                        xs, ys = [prim["x1"], prim["x2"]], [prim["y1"], prim["y2"]]
                    elif prim["kind"] == "wedge":
                        xs, ys = [prim["cx"] - prim["r"], prim["cx"] + prim["r"]], [prim["cy"] - prim["r"], prim["cy"] + prim["r"]]
                    else:
                        xs, ys = [prim["x"]], [prim["y"]]
                    self.assertGreaterEqual(min(xs), box["x"] - 1.5, (shape, width, prim["kind"]))
                    self.assertLessEqual(max(xs), box["x"] + box["w"] + 1.5, (shape, width, prim["kind"]))
                    self.assertGreaterEqual(min(ys), box["y"] - 1.5, (shape, width, prim["kind"]))
                    self.assertLessEqual(max(ys), box["y"] + box["h"] + 1.5, (shape, width, prim["kind"]))

    def test_a_progress_bar_is_its_track_and_its_share(self):
        spec = panel({"role": "section", "children": [{"role": "progress", "p": 25}]})
        track, fill = Y.drawn(spec, "p1")[1:]
        self.assertEqual((track["w"], fill["w"], fill["draw"], fill["x"]), (400.0, 100.0, "across", track["x"]))

    def test_every_tile_has_a_picture(self):
        spec = M.clean({"style": "cartoon", "dark": False, "accent": "#e4572e"})
        for tile_id in [*C.LAYOUTS, *C.COMPONENTS]:
            tile = Y.tile(tile_id, spec)
            self.assertGreater(len(tile["prims"]), 1, tile_id)
            self.assertTrue(tile["w"] >= 320 and tile["h"] > 20, tile_id)
            self.assertIn(2.5, {p.get("stroke_w") for p in tile["prims"]}, tile_id)         # in the panel's style
            for prim in tile["prims"]:
                self.assertEqual(set(prim) - {"draw"}, PRIM_KEYS[prim["kind"]] - {"draw"}, tile_id)
        self.assertIsNone(Y.tile("nothing", spec))
        self.assertIn("#e4572eff", {p["fill"] for p in Y.tile("button", spec)["prims"]})


class ClockTests(unittest.TestCase):
    FPS = 10

    def two_pages(self, path=None, clicks=(), **more):
        """A button on page one that goes to page two, which has a progress bar and a card."""
        spec = panel(
            {"role": "section", "pad": 20, "gap": 20, "children": [
                {"role": "text", "text": "Title", "name": "Title"},
                {"role": "button", "text": "Go", "name": "Go", "go": {"page": "p2", "with": "fade"}},
                {"role": "button", "text": "Stay", "name": "Stay"}]},
            {"role": "section", "pad": 20, "gap": 20, "children": [
                {"role": "progress", "p": 80, "name": "Bar"},
                {"role": "section", "variant": "card", "minh": 60, "name": "Tile"},
                {"role": "text", "text": "Title", "name": "Title"}]},
            **{"seconds": 4, "go_seconds": 0.5, "build": {"on": False}, **more})
        if path:
            spec = M.clean({**spec, "path": [over(spec, named(spec, name, page), t, page) if name else [t, 1, 1]
                                             for t, name, page in path], "clicks": list(clicks)})
        return spec

    def test_a_hover_rises_and_runs_back_from_where_it_got_to(self):
        # On the button at 1.0 s for half a second, off it for good - then on it again at 3.0 s for two frames.
        spec = self.two_pages([(0, "", "p1"), (0.9, "", "p1"), (1.0, "Stay", "p1"), (1.4, "Stay", "p1"), (1.5, "", "p1"),
                               (2.9, "", "p1"), (3.0, "Stay", "p1"), (3.1, "Stay", "p1"), (3.2, "", "p1")],
                              answers={"hover_seconds": 0.5, "press_seconds": 0.2})
        played = K.clock(spec, self.FPS, FRAME)
        stay = named(spec, "Stay")["id"]
        self.assertEqual(list(played["amounts"]), [stay])                                   # only a box that ever answers
        hover = played["amounts"][stay]["hover"]
        self.assertEqual((played["total"], len(hover), len(played["cursor"]), len(played["down"])), (40, 40, 40, 40))
        self.assertEqual(hover[:10], [0.0] * 10)
        self.assertTrue(all(a < b for a, b in zip(hover[9:14], hover[10:15])))              # on it: it rises
        self.assertAlmostEqual(hover[11] + hover[12], 1.0, places=4)                        # eased: as slow to start as to stop
        self.assertEqual(hover[14], 1.0)
        self.assertTrue(all(a > b for a, b in zip(hover[14:19], hover[15:20])))             # off it: it falls
        self.assertEqual(hover[19:30], [0.0] * 11)
        # A visit too short to finish: it rises part of the way, and runs back from where it had got to.
        self.assertTrue(0 < hover[30] < hover[31] < 1)
        self.assertEqual(max(hover[30:]), hover[31])
        self.assertTrue(hover[34] > hover[35] > hover[36] == 0.0)
        self.assertEqual(played["amounts"][stay]["press"], [0.0] * 40)
        self.assertEqual([(e["frame"], e["kind"]) for e in played["events"]],
                         [(10, "over"), (15, "out"), (30, "over"), (32, "out")])

    def test_a_click_on_a_linked_button_changes_the_page(self):
        spec = self.two_pages([(0, "", "p1"), (1.0, "Go", "p1"), (4.0, "Go", "p1")], clicks=[[1.5, 1.8]])
        played = K.clock(spec, self.FPS, FRAME)
        go = named(spec, "Go")["id"]
        self.assertEqual(played["pages"], [
            {"at": 0, "page": "p1", "from": "", "with": "cut", "frames": 0, "why": "start", "box": ""},
            {"at": 15, "page": "p2", "from": "p1", "with": "fade", "frames": 5, "why": "click", "box": go}])
        self.assertEqual(played["down"][14:19], [0, 1, 1, 1, 0])
        self.assertIn({"frame": 15, "kind": "page", "box": go, "page": "p2"}, played["events"])
        self.assertIn({"frame": 15, "kind": "press", "box": go, "page": "p1"}, played["events"])
        self.assertIn({"frame": 18, "kind": "release", "box": go, "page": "p1"}, played["events"])
        press = played["amounts"][go]["press"]
        self.assertTrue(press[14] == 0 and press[16] == 1.0 and press[19] < 1 and press[21] == 0)
        # Once the change has begun the cursor answers the page arriving: the button is no longer under it.
        self.assertEqual(played["amounts"][go]["hover"][-1], 0.0)
        self.assertEqual([e["kind"] for e in played["events"] if e["frame"] > 18], [])
        # A click that isn't on the link, and a link's default way of going.
        missed = K.clock(self.two_pages([(0, "Stay", "p1")], clicks=[[1.5, 1.8]]), self.FPS, FRAME)
        self.assertEqual(len(missed["pages"]), 1)
        plain = M.with_box(spec, go, "go", {"page": "p2", "with": ""})
        self.assertEqual(K.clock(plain, self.FPS, FRAME)["pages"][1]["with"], "slide")

    def test_only_the_topmost_reacting_box_under_the_cursor_answers(self):
        spec = panel({"role": "section", "variant": "card", "pad": 20, "name": "Outer", "children": [
            {"role": "text", "text": "words", "name": "Words"}, {"role": "button", "text": "Inner", "name": "Inner"}]})
        outer, inner, text = named(spec, "Outer"), named(spec, "Inner"), named(spec, "Words")
        played = K.clock(M.clean({**spec, "path": [over(spec, inner, 0)]}), self.FPS, FRAME)
        self.assertEqual(list(played["amounts"]), [inner["id"]])
        played = K.clock(M.clean({**spec, "path": [over(spec, text, 0)]}), self.FPS, FRAME)
        self.assertEqual(list(played["amounts"]), [outer["id"]])                            # text doesn't react: the card under it does

    def test_page_keys_change_the_page_and_one_waits_for_another(self):
        spec = self.two_pages(page_keys=[[1.0, "p2", "slide"], [1.2, "p1", "match"], [3.0, "p1", "fade"]])
        played = K.clock(spec, self.FPS, FRAME)
        self.assertEqual([(c["at"], c["page"], c["from"], c["with"], c["why"], c["box"]) for c in played["pages"]],
                         [(0, "p1", "", "cut", "start", ""), (10, "p2", "p1", "slide", "key", ""),
                          (15, "p1", "p2", "match", "key", "")])                           # 12 would overlap: it waits; the last changes nothing
        self.assertEqual([(e["frame"], e["page"]) for e in played["events"] if e["kind"] == "page"], [(10, "p2"), (15, "p1")])

    def test_a_rule_fires_once(self):
        spec = self.two_pages(build={"on": True, "stagger": 0.1, "seconds": 0.5}, start="p2",
                              page_keys=[[3.0, "p2", "fade"]], seconds=6)
        bar = named(spec, "Bar", "p2")["id"]
        ruled = M.with_rule(spec, "", "page", "p1")
        self.assertEqual((ruled["rules"][0]["box"], ruled["rules"][0]["over"]), (bar, 60.0))
        played = K.clock(ruled, self.FPS, FRAME)
        fired = [e for e in played["events"] if e["kind"] == "rule"]
        self.assertEqual(len(fired), 1)
        at = fired[0]["frame"]
        self.assertTrue(played["draw"][bar][at] > 0.6 >= played["draw"][bar][at - 1])       # the first frame it's over
        self.assertEqual([(c["at"], c["page"], c["why"], c["with"], c["box"]) for c in played["pages"]],
                         [(0, "p2", "start", "cut", ""), (at, "p1", "rule", "match", bar), (30, "p2", "key", "fade", "")])
        # Back on its page the bar draws again - and the rule, having fired, lets it.
        self.assertEqual((played["draw"][bar][30], played["draw"][bar][-1]), (0.0, 1.0))

    def test_a_page_builds_on_in_reading_order(self):
        spec = M.clean({**M.clean({}), "build": {"on": True, "stagger": 0.1, "seconds": 0.5}, "seconds": 10, "start": "p2"})
        played = K.clock(spec, 30, FRAME)
        self.assertEqual(set(played["build"]), {"p1", "p2", "p3"})
        for page in ("p1", "p2", "p3"):
            boxes = S.solve(spec, page)["boxes"]
            build = played["build"][page]
            self.assertEqual(set(build), {b["id"] for b in boxes})
            order = sorted(boxes, key=lambda b: (b["y"], b["x"]))
            starts = [build[b["id"]][0] for b in order]
            self.assertEqual(starts, sorted(starts))                                        # top to bottom, then left to right
            self.assertEqual((starts[0], {n for _s, n in build.values()}), (0, {15}))
            self.assertLessEqual(starts[-1], K.BUILD_MOST * 30)                             # never long after the page
            by_id = {b["id"]: b for b in boxes}
            for box in boxes[1:]:
                self.assertGreaterEqual(build[box["id"]][0], build[by_id[box["parent"]]["id"]][0])
        few = self.two_pages(build={"on": True, "stagger": 0.1, "seconds": 0.5})
        build = K.clock(few, self.FPS, FRAME)["build"]["p1"]
        self.assertEqual([build[b["id"]] for b in S.solve(few, "p1")["boxes"]], [[0, 5], [1, 5], [2, 5], [3, 5]])
        # A chart draws on over the build, from nothing to all of it; with no build it is simply there.
        charts = [b["id"] for b in S.solve(spec, "p2")["boxes"] if b["role"] == "chart"]
        self.assertEqual(sorted(played["draw"]), sorted(charts))                            # only pages that show
        for chart in charts:
            row = played["draw"][chart]
            self.assertEqual((row[0], row[-1]), (0.0, 1.0))
            self.assertTrue(all(a <= b for a, b in zip(row, row[1:])))
            self.assertTrue(0 < row[build_at(played, "p2", chart) + 10] < 1)
        still = K.clock(M.clean({**spec, "build": {"on": False}}), 30, FRAME)
        self.assertEqual((still["build"], {v for row in still["draw"].values() for v in row}),
                         ({"p1": {}, "p2": {}, "p3": {}}, {1.0}))

    def test_a_pose_says_how_a_frame_looks(self):
        spec = self.two_pages([(0, "", "p1"), (1.0, "Stay", "p1"), (4.0, "Stay", "p1")], clicks=[[2.0, 2.3]],
                              answers={"hover": {"grow": 10, "lift": 3, "glow": 40}, "press": {"grow": -6, "lift": 0, "glow": 0}})
        played = K.clock(spec, self.FPS, FRAME)
        stay = named(spec, "Stay")
        self.assertEqual(K.pose(spec, played, 0), {"pages": [{"page": "p1", "alpha": 1.0, "dx": 0.0, "boxes": {}}],
                                                   "cursor": played["cursor"][0], "down": 0, "draw": {},
                                                   "typed": {}, "caret": {}})
        hovered = K.pose(spec, played, 19)
        self.assertEqual(hovered["pages"][0]["boxes"], {stay["id"]: {**K.REST, "scale": 1.1, "dy": -3.0, "glow": 0.4}})
        self.assertAlmostEqual(hovered["cursor"][0], stay["x"] + stay["w"] / 2, places=1)
        pressed = K.pose(spec, played, 22)
        self.assertEqual((pressed["down"], pressed["pages"][0]["boxes"][stay["id"]]["scale"]), (1, 1.04))
        own = M.with_answers(spec, stay["id"], "hover", "grow", 20)
        self.assertEqual(K.pose(own, K.clock(own, self.FPS, FRAME), 19)["pages"][0]["boxes"][stay["id"]]["scale"], 1.2)
        self.assertEqual(K.pose(spec, played, 999), K.pose(spec, played, 39))               # past the end is the end
        self.assertEqual(K.pose(spec, played, -5), K.pose(spec, played, 0))

    def changing(self, how):
        spec = self.two_pages(page_keys=[[1.0, "p2", how]])
        played = K.clock(spec, self.FPS, FRAME)
        return spec, played, [K.pose(spec, played, f) for f in range(9, 17)]

    def test_a_fade_and_a_slide(self):
        spec, played, poses = self.changing("fade")
        self.assertEqual([[p["page"] for p in pose["pages"]] for pose in poses],
                         [["p1"], ["p1", "p2"], ["p1", "p2"], ["p1", "p2"], ["p1", "p2"], ["p1", "p2"], ["p2"], ["p2"]])
        alphas = [(pose["pages"][0]["alpha"], pose["pages"][1]["alpha"]) for pose in poses[1:6]]
        self.assertEqual(alphas[0], (1.0, 0.0))
        self.assertTrue(all(abs(a + b - 1) < 1e-3 for a, b in alphas))
        self.assertEqual([b for _a, b in alphas], sorted(b for _a, b in alphas))
        self.assertEqual({page["dx"] for pose in poses for page in pose["pages"]}, {0.0})
        self.assertEqual(poses[-1]["pages"], [{"page": "p2", "alpha": 1.0, "dx": 0.0, "boxes": {}}])
        self.assertEqual(poses[3]["draw"], {named(spec, "Bar", "p2")["id"]: 1.0})           # what shows of what's drawn on
        spec, played, poses = self.changing("slide")
        leaving, arriving = poses[3]["pages"]
        self.assertTrue(-K.SLIDE * 400 < leaving["dx"] < 0 < arriving["dx"] < K.SLIDE * 400)
        self.assertEqual((poses[1]["pages"][1]["dx"], poses[1]["pages"][0]["dx"]), (K.SLIDE * 400, 0.0))
        self.assertAlmostEqual(leaving["alpha"] + arriving["alpha"], 1.0, places=3)

    def test_a_match_glides_the_boxes_both_pages_have(self):
        spec, played, poses = self.changing("match")
        old, new, tile = named(spec, "Title", "p1"), named(spec, "Title", "p2"), named(spec, "Tile", "p2")
        root_old, root_new = S.solve(spec, "p1")["boxes"][0], S.solve(spec, "p2")["boxes"][0]
        self.assertEqual(K.matched(spec, "p1", "p2"), {new["id"]: old, root_new["id"]: root_old})
        self.assertGreater(new["y"], old["y"] + 50)
        first, middle = poses[1], poses[3]
        self.assertEqual([(p["page"], p["alpha"], p["dx"]) for p in first["pages"]], [("p1", 1.0, 0.0), ("p2", 1.0, 0.0)])
        # It starts exactly where the old one is, and the old one doesn't show.
        self.assertEqual(first["pages"][1]["boxes"][new["id"]], {**K.REST, "dy": old["y"] - new["y"]})
        self.assertEqual(first["pages"][0]["boxes"][old["id"]]["own"], 0.0)              # its own prims; what's in it still fades
        self.assertTrue(old["y"] - new["y"] < middle["pages"][1]["boxes"][new["id"]]["dy"] < 0)
        # The rest fade: out on the page left, in on the one arriving.
        go = named(spec, "Go")["id"]
        self.assertNotIn(go, first["pages"][0]["boxes"])                                    # still as it was
        self.assertEqual(first["pages"][1]["boxes"][tile["id"]]["alpha"], 0.0)
        left = K.carried(S.solve(spec, "p1"), middle["pages"][0]["boxes"], go)["alpha"]           # faded in what it's in
        self.assertTrue(0 < left < 1 and 0 < middle["pages"][1]["boxes"][tile["id"]]["alpha"] < 1)
        self.assertEqual(poses[-1]["pages"], [{"page": "p2", "alpha": 1.0, "dx": 0.0, "boxes": {}}])
        # A box that changes size is stretched from the old one's.
        wide = panel({"role": "section", "children": [{"role": "image", "w": 100, "h": 50, "name": "Art"}]},
                     {"role": "section", "children": [{"role": "image", "w": 200, "h": 200, "name": "Art"}]},
                     page_keys=[[1.0, "p2", "match"]], build={"on": False}, seconds=3)
        art = named(wide, "Art", "p2")
        start = K.pose(wide, K.clock(wide, self.FPS, FRAME), 10)["pages"][1]["boxes"][art["id"]]
        self.assertEqual((start["sx"], start["sy"], start["dx"], start["dy"], start["scale"]), (0.5, 0.25, -50.0, -75.0, 1.0))

    def test_a_box_building_on_fades_and_rises_into_place(self):
        spec = self.two_pages(build={"on": True, "stagger": 0.2, "seconds": 0.5})
        played = K.clock(spec, self.FPS, FRAME)
        solved = S.solve(spec, "p1")
        last = solved["boxes"][-1]["id"]
        # Carried through what it's in, each box arrives as its own build says: once, 8 px.
        drawn = K.carried(solved, K.pose(spec, played, 0)["pages"][0]["boxes"], last)
        self.assertEqual((drawn["alpha"], round(drawn["dy"], 9), round(drawn["dx"], 9), drawn["scale"]), (0.0, K.RISE, 0.0, 1.0))
        for f in range(0, 20):
            boxes = K.pose(spec, played, f)["pages"][0]["boxes"]
            for box in solved["boxes"]:
                start, n = played["build"]["p1"][box["id"]]
                there = K._eased((f - start) / n, K.ARRIVES)
                got = K.carried(solved, boxes, box["id"])
                self.assertAlmostEqual(got["alpha"], there, delta=2e-4)
                self.assertAlmostEqual(got["dy"], (1 - there) * K.RISE, delta=2e-4)
        part = K.carried(solved, K.pose(spec, played, 8)["pages"][0]["boxes"], last)
        self.assertTrue(0 < part["alpha"] < 1 and 0 < part["dy"] < K.RISE)
        self.assertEqual(K.pose(spec, played, 20)["pages"][0]["boxes"], {})

    def test_a_box_carries_what_is_in_it(self):
        card = {"role": "section", "variant": "card", "name": "Card", "pad": 10, "children": [{"role": "text", "text": "Total"}]}
        spec = panel({"role": "section", "children": [card]}, {"role": "section", "children": []}, build={"on": False}, seconds=3)
        solved = S.solve(spec, "p1")
        card_box = next(b for b in solved["boxes"] if b["name"] == "Card")
        words = next(b for b in solved["boxes"] if b["parent"] == card_box["id"])
        spot = list(S.to_frame(spec, solved["h"], card_box["x"] + 4, card_box["y"] + 4, FRAME))
        spec = M.clean({**spec, "path": [[0.0, *spot], [3.0, *spot]]})
        played = K.clock(spec, self.FPS, FRAME)
        boxes = K.pose(spec, played, 30)["pages"][0]["boxes"]
        self.assertNotIn(words["id"], boxes)                                                # nothing of its own...
        got = K.carried(solved, boxes, words["id"])                                         # ...but it goes with the card
        grow, lift = 1 + spec["answers"]["hover"]["grow"] / 100, spec["answers"]["hover"]["lift"]
        cx, cy = card_box["x"] + card_box["w"] / 2, card_box["y"] + card_box["h"] / 2
        wx, wy = words["x"] + words["w"] / 2, words["y"] + words["h"] / 2
        self.assertAlmostEqual(got["scale"], grow, places=4)
        self.assertAlmostEqual(got["dx"], cx + grow * (wx - cx) - wx, places=3)
        self.assertAlmostEqual(got["dy"], cy + grow * (wy - cy) - lift - wy, places=3)
        self.assertEqual((got["glow"], K.carried(solved, boxes, card_box["id"])["glow"] > 0), (0.0, True))   # glow stays the card's

    def test_a_linked_box_takes_a_click_though_it_doesnt_answer(self):
        spec = self.two_pages()
        go = named(spec, "Go")
        quiet = M.with_box(spec, go["id"], "reacts", False)
        solved = S.solve(quiet, "p1")
        b = next(x for x in solved["boxes"] if x["id"] == go["id"])
        spot = list(S.to_frame(quiet, solved["h"], b["x"] + b["w"] / 2, b["y"] + b["h"] / 2, FRAME))
        quiet = M.clean({**quiet, "path": [[0.0, *spot], [3.0, *spot]], "clicks": [[1.0, 1.2]]})
        played = K.clock(quiet, self.FPS, FRAME)
        self.assertEqual([(c["why"], c["page"], c["box"]) for c in played["pages"][1:]], [("click", "p2", go["id"])])
        self.assertNotIn(go["id"], played["amounts"])                                       # no hover, no press

    def test_the_same_panel_plays_the_same_and_is_plain_data(self):
        spec = M.clean({})
        one, other = K.clock(spec, 30, FRAME), K.clock(M.clean({}), 30, FRAME)
        self.assertEqual(one, other)
        self.assertEqual(json.loads(json.dumps(one)), one)
        self.assertEqual(set(one), {"total", "cursor", "down", "pages", "amounts", "build", "draw", "events", "typed", "caret"})
        self.assertEqual(S.solve_all(spec), S.solve_all(M.clean({})))
        self.assertEqual(Y.drawn(spec, "p2"), Y.drawn(M.clean({}), "p2"))
        self.assertEqual(json.loads(json.dumps(Y.drawn(spec, "p2"))), Y.drawn(spec, "p2"))
        self.assertEqual(json.loads(json.dumps(S.solve_all(spec))), S.solve_all(spec))
        self.assertEqual([K.pose(spec, one, f) for f in (0, 40, 50, 100)], [K.pose(M.clean({}), other, f) for f in (0, 40, 50, 100)])
        self.assertEqual(json.loads(json.dumps(K.pose(spec, one, 50))), K.pose(spec, one, 50))
        # The frame's shape moves the cursor: on a tall frame the same % are other panel px.
        self.assertNotEqual(one["cursor"], K.clock(spec, 30, (1080, 1920))["cursor"])

    def test_a_minute_at_sixty_frames_a_second_is_quick(self):
        template = M.clean({})
        spec = M.clean({**template, "seconds": 60, "page_keys": [[20, "p3", "match"], [40, "p2", "fade"]],
                        "path": [[t, 20 + (t * 7) % 60, 20 + (t * 11) % 60] for t in range(60)],
                        "clicks": [[t + 0.2, t + 0.4] for t in range(0, 60, 3)]})
        began = time.perf_counter()
        played = K.clock(spec, 60, FRAME)
        took = time.perf_counter() - began
        self.assertEqual(played["total"], 3600)
        self.assertGreater(len(played["amounts"]), 3)
        self.assertLess(took, 1.0)
        began = time.perf_counter()
        for f in range(played["total"]):
            K.pose(spec, played, f)
        self.assertLess(time.perf_counter() - began, 2.0)                                   # what the comp samples, every frame


LOGO = "D:\\Footage\\logo.png"


class RoundTwoModelTests(unittest.TestCase):
    """Images, typing, a box's own arrival and moving layers, as data."""

    def test_the_new_keys_are_cleaned(self):
        spec = panel({"role": "section", "children": [
            {"role": "image", "name": "Photo", "image": {"path": LOGO, "px": [400.7, "200"]}},
            {"role": "image", "name": "Bad", "image": {"path": "D:\\Footage\\notes.txt", "px": [10, 10]}},
            {"role": "image", "name": "Flat", "image": {"path": LOGO, "px": [0, 10]}},
            {"role": "image", "name": "Lost", "image": {"path": 7, "px": [10, 10]}},
            {"role": "icon", "name": "Icon", "image": {"path": LOGO, "px": [10, 10]}},
            {"role": "text", "name": "Typed", "text": "Hello", "type": {"speed": 999, "start": "never", "text": "x"}},
            {"role": "field", "name": "Email", "text": "Email", "type": {"speed": 0, "text": "  tess@example.com "}},
            {"role": "button", "name": "Go", "text": "Go", "type": {"speed": 10}},
            {"role": "button", "name": "In", "text": "In", "arrive": {"kind": "pop"}},
            {"role": "button", "name": "Odd", "text": "Odd", "arrive": {"kind": "spin"}}]})
        box = {b["name"]: b for b in M.every(spec["pages"][0]["root"])}
        self.assertEqual(box["Photo"]["image"], {"path": LOGO, "px": [400, 200]})
        for name in ("Bad", "Flat", "Lost", "Icon"):
            self.assertIsNone(box[name]["image"], name)
        self.assertEqual(box["Typed"]["type"], {"speed": 60.0, "start": "page", "caret": True, "text": ""})
        self.assertEqual(box["Email"]["type"], {"speed": 2.0, "start": "click", "caret": True, "text": "tess@example.com"})
        self.assertIsNone(box["Go"]["type"])                                               # only a text or a field types
        self.assertEqual((box["In"]["arrive"], box["Odd"]["arrive"], box["Go"]["arrive"]), ({"kind": "pop"}, None, None))
        self.assertEqual(M.clean(spec), spec)
        self.assertIsNone(M.find(M.clean({}), "b1")["box"]["image"])

    def test_a_picture_is_put_in_and_taken_off(self):
        spec = panel({"role": "section", "children": [{"role": "image", "name": "Photo"}, {"role": "text", "text": "Hi"}]})
        photo, words = (b["id"] for b in spec["pages"][0]["root"]["children"])
        shown = M.with_image(spec, photo, LOGO, [1920, 1080])
        self.assertEqual(M.find(shown, photo)["box"]["image"], {"path": LOGO, "px": [1920, 1080]})
        self.assertEqual([b["id"] for b in M.images(shown)], [photo])
        self.assertIsNone(M.with_image(shown, photo, LOGO, [1920, 1080]))                  # no change
        self.assertIsNone(M.with_image(shown, words, LOGO, [10, 10]))                      # not an image box
        self.assertIsNone(M.with_image(shown, photo, "D:\\Footage\\clip.mov", [10, 10]))   # not a picture: kept as it was
        self.assertIsNone(M.with_image(shown, photo, LOGO, None))
        self.assertIsNone(M.with_image(shown, "b999", LOGO, [10, 10]))
        cleared = M.with_image(shown, photo, "")
        self.assertIsNone(M.find(cleared, photo)["box"]["image"])
        self.assertEqual(cleared, spec)
        self.assertIsNone(M.with_box(spec, photo, "image", {"path": LOGO, "px": [5, 5]}))  # a picture comes by with_image

    def test_the_id_changes_when_a_picture_does(self):
        import os
        import tempfile
        spec = panel({"role": "section", "children": [{"role": "image", "name": "Photo"}]})
        photo = spec["pages"][0]["root"]["children"][0]["id"]
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "logo.png")
            with open(path, "wb") as fh:
                fh.write(b"one")
            shown = M.with_image(spec, photo, path, [10, 10])
            first = M.panel_id(shown, FRAME, 24)
            self.assertNotEqual(first, M.panel_id(spec, FRAME, 24))
            self.assertEqual(first, M.panel_id(shown, FRAME, 24))
            with open(path, "wb") as fh:
                fh.write(b"another picture")                                               # the same path, another file
            self.assertNotEqual(first, M.panel_id(shown, FRAME, 24))
        self.assertEqual(M.panel_id(spec, FRAME, 24), M.panel_id(M.clean(spec), FRAME, 24))

    def test_typing_and_an_arrival_are_set_laid_over_and_taken_off(self):
        spec = panel({"role": "section", "children": [{"role": "field", "text": "Search"}, {"role": "text", "text": "Hi"}]})
        field, words = (b["id"] for b in spec["pages"][0]["root"]["children"])
        typed = M.with_box(spec, field, "type", {"text": "shoes"})
        self.assertEqual(M.find(typed, field)["box"]["type"], {"speed": 18.0, "start": "click", "caret": True, "text": "shoes"})
        faster = M.with_box(typed, field, "type", {"speed": 30})
        self.assertEqual(M.find(faster, field)["box"]["type"], {"speed": 30.0, "start": "click", "caret": True, "text": "shoes"})
        self.assertIsNone(M.find(M.with_box(faster, field, "type", None), field)["box"]["type"])
        popped = M.with_box(spec, words, "arrive", {"kind": "pop"})
        self.assertEqual(M.find(popped, words)["box"]["arrive"], {"kind": "pop"})
        self.assertIsNone(M.with_box(popped, words, "arrive", {"kind": "pop"}))
        self.assertIsNone(M.find(M.with_box(popped, words, "arrive", None), words)["box"]["arrive"])

    def test_a_layer_is_moved_and_what_cant_be_is_refused(self):
        spec = panel({"role": "section", "name": "Root", "children": [
            {"role": "text", "text": "One", "name": "One"},
            {"role": "section", "variant": "card", "name": "Card", "children": [
                {"role": "text", "text": "Two", "name": "Two"}, {"role": "section", "name": "Inner", "children": []}]},
            {"role": "button", "text": "Go", "name": "Go"}]},
            {"role": "section", "name": "Other", "children": [{"role": "text", "text": "Far", "name": "Far"}]})
        ids = {b["name"]: b["id"] for b in M.boxes(spec)}
        names = lambda made, box: [c["name"] for c in M.find(made, ids[box])["box"]["children"]]   # noqa: E731
        moved = M.with_move(spec, ids["One"], ids["Root"], None)                         # to the end of its own section
        self.assertEqual(names(moved, "Root"), ["Card", "Go", "One"])
        moved = M.with_move(spec, ids["Go"], ids["Root"], 0)
        self.assertEqual(names(moved, "Root"), ["Go", "One", "Card"])
        into = M.with_move(spec, ids["One"], ids["Card"], 1)                             # into another section, what it is the same
        self.assertEqual((names(into, "Root"), names(into, "Card")), (["Card", "Go"], ["Two", "One", "Inner"]))
        self.assertEqual(M.find(into, ids["One"])["box"], M.find(spec, ids["One"])["box"])
        card = M.with_move(spec, ids["Card"], ids["Root"], 99)                           # with what is in it
        self.assertEqual((names(card, "Root"), names(card, "Card")), (["One", "Go", "Card"], ["Two", "Inner"]))
        for refused in ((ids["One"], ids["Root"], 0),            # where it already is
                        (ids["Root"], ids["Card"], 0),           # a page's own box
                        (ids["Card"], ids["Card"], 0),           # into itself
                        (ids["Card"], ids["Inner"], 0),          # into what is in it
                        (ids["One"], ids["Go"], 0),              # into what isn't a section
                        (ids["One"], ids["Other"], 0),           # onto another page
                        ("b999", ids["Root"], 0), (ids["One"], "b999", 0)):
            self.assertIsNone(M.with_move(spec, *refused), refused)

    def test_every_way_a_box_moves_is_listed(self):
        spec = panel({"role": "section", "children": [
            {"role": "text", "text": "Hi", "name": "Hi", "type": {"speed": 12}},
            {"role": "field", "text": "Email", "name": "Email"},
            {"role": "chart", "name": "Chart"}, {"role": "progress", "name": "Bar", "arrive": {"kind": "none"}},
            {"role": "button", "text": "Go", "name": "Go", "go": {"page": "p2"}, "arrive": {"kind": "fade"}}]},
            {"role": "section", "children": []}, build={"on": True})
        ids = {b["name"]: b["id"] for b in M.boxes(spec)}
        said = {name: {a["id"]: a for a in M.animations(spec, ids[name])} for name in ids}
        self.assertEqual([a["id"] for a in M.animations(spec, ids["Hi"])], ["arrive", "type", "answer"])
        self.assertEqual(said["Hi"]["type"], {"id": "type", "on": True, "speed": 12.0, "start": "page", "caret": True, "text": ""})
        self.assertEqual(said["Email"]["type"], {"id": "type", "on": False, "speed": 18.0, "start": "click", "caret": True,
                                                 "text": ""})
        self.assertEqual(said["Hi"]["arrive"], {"id": "arrive", "on": True, "kind": "", "build": True})
        self.assertEqual(said["Bar"]["arrive"]["on"], False)
        self.assertEqual(said["Bar"]["draw"], {"id": "draw", "on": True})
        self.assertIn("draw", said["Chart"])
        self.assertNotIn("type", said["Go"])
        self.assertEqual(said["Go"]["answer"], {"id": "answer", "on": True, "own": False, "go": "p2"})
        self.assertEqual(said["Hi"]["answer"]["on"], False)
        still = M.clean({**spec, "build": {"on": False}})
        self.assertEqual([M.animations(still, ids[n])[0]["on"] for n in ("Hi", "Go")], [False, True])   # its own arrives anyway
        self.assertEqual(M.animations(spec, "b999"), [])


class RoundTwoStylesTests(unittest.TestCase):
    def test_a_picture_fills_its_box_and_is_cropped_never_stretched(self):
        # Wider than the box: its sides go; taller: its top and foot; the same shape: all of it.
        self.assertEqual(Y.crop([400, 100], 200, 100), [100.0, 0.0, 200.0, 100.0])
        self.assertEqual(Y.crop([100, 400], 200, 100), [0.0, 175.0, 100.0, 50.0])
        self.assertEqual(Y.crop([1920, 1080], 160, 90), [0.0, 0.0, 1920.0, 1080.0])
        for px, w, h in (([4000, 3000], 300, 120), ([640, 1136], 120, 120), ([1, 1], 50, 200)):
            sx, sy, sw, sh = Y.crop(px, w, h)
            self.assertAlmostEqual(sw / sh, w / h, delta=0.02 * w / h)                      # the box's shape
            self.assertTrue(sw == px[0] or sh == px[1])                                     # as much as fits
            self.assertAlmostEqual(sx * 2 + sw, px[0], places=1)                            # its middle
            self.assertAlmostEqual(sy * 2 + sh, px[1], places=1)

    def test_an_image_box_with_a_picture_draws_it_in_place_of_its_placeholder(self):
        spec = panel({"role": "section", "pad": 10, "children": [{"role": "image", "name": "Photo", "aspect": 2}]})
        photo = spec["pages"][0]["root"]["children"][0]["id"]
        plain = [p for p in Y.drawn(spec, "p1") if p["box"] == photo]
        self.assertNotIn("image", {p["kind"] for p in plain})
        shown = M.with_image(spec, photo, LOGO, [300, 300])
        (picture,) = [p for p in Y.drawn(shown, "p1") if p["box"] == photo]
        box = named(shown, "Photo")
        self.assertEqual(set(picture), {"box", "kind", "x", "y", "w", "h", "radius", "path", "px", "crop"})
        self.assertEqual((picture["x"], picture["y"], picture["w"], picture["h"]), (box["x"], box["y"], box["w"], box["h"]))
        self.assertEqual((picture["path"], picture["px"], picture["crop"]), (LOGO, [300, 300], [0.0, 75.0, 300.0, 150.0]))
        self.assertEqual(picture["radius"], Y.STYLES["mac"]["radius"]["image"])
        cartoon = [p["kind"] for p in Y.drawn(M.clean({**shown, "style": "cartoon"}), "p1") if p["box"] == photo]
        self.assertEqual(cartoon, ["rect", "image", "rect"])                               # its hard shadow and outline

    def test_a_line_that_types_says_which_letters_it_holds_and_starts_where_the_whole_line_does(self):
        words = "Type these words on"
        spec = panel({"role": "section", "children": [
            {"role": "text", "variant": "h1", "text": words, "name": "Head", "center": True, "w": 200,
             "type": {"speed": 10}}]})
        head = named(spec, "Head")
        lines = [p for p in Y.drawn(spec, "p1") if p["box"] == head["id"]]
        self.assertEqual([p["text"] for p in lines], head["lines"])
        self.assertGreater(len(lines), 1)
        first = 0
        size, char_w = S.letter(head)
        for prim, line in zip(lines, head["lines"]):
            self.assertEqual(prim["type"], [first, first + len(line)])
            self.assertEqual(prim["align"], "left")                                        # set from its start...
            self.assertAlmostEqual(prim["x"], head["x"] + (head["w"] - len(line) * char_w) / 2, places=1)   # ...where it starts centred
            first += len(line)
        self.assertEqual(first, sum(len(line) for line in head["lines"]))

    def test_a_field_that_types_has_its_placeholder_and_its_typed_words(self):
        spec = panel({"role": "section", "children": [
            {"role": "field", "text": "Email", "name": "Email", "type": {"text": "tess@example.com"}}]})
        field = named(spec, "Email")
        lines = [p for p in Y.drawn(spec, "p1") if p["box"] == field["id"] and p["kind"] == "text"]
        hint, typed = lines
        self.assertEqual((hint["text"], hint.get("placeholder"), "type" in hint), ("Email", True, False))
        self.assertEqual((typed["text"], typed["type"], typed["x"]), ("tess@example.com", [0, 16], hint["x"]))
        t = Y.look(spec)
        under = Y.over(t["field"], t["ground"])
        self.assertEqual((hint["fill"], typed["fill"]), (Y.legible(t["dim"], under), Y.legible(t["text"], under)))
        self.assertEqual(field["type_lines"], ["tess@example.com"])


class RoundTwoClockTests(unittest.TestCase):
    FPS = 10

    def typing(self, *children, **more):
        return panel({"role": "section", "pad": 20, "gap": 20, "children": list(children)},
                     **{"seconds": 4, "build": {"on": False}, **more})

    def test_letters_type_at_their_speed_from_when_the_box_has_arrived(self):
        spec = self.typing({"role": "text", "text": "Hello there", "name": "Hi", "type": {"speed": 4}})
        hi = named(spec, "Hi")["id"]
        played = K.clock(spec, self.FPS, FRAME)
        self.assertEqual(list(played["typed"]), [hi])
        # The first letter on the frame it starts, then 4 a second at 10 frames a second: one each 2.5 frames.
        self.assertEqual(played["typed"][hi][:12], [1, 1, 1, 2, 2, 3, 3, 3, 4, 4, 5, 5])
        self.assertEqual(played["typed"][hi][-1], 11)
        self.assertEqual(max(played["typed"][hi]), 11)
        self.assertIn({"frame": 0, "kind": "type", "box": hi, "page": "p1"}, played["events"])
        # With a build, it starts when it has arrived.
        built = M.clean({**spec, "build": {"on": True, "stagger": 0.1, "seconds": 0.5}})
        played = K.clock(built, self.FPS, FRAME)
        start, n = played["build"]["p1"][hi]
        self.assertEqual(played["typed"][hi].index(1), start + n)
        self.assertEqual(sum(played["typed"][hi][:start + n]), 0)
        self.assertEqual(K.pose(built, played, start + n)["typed"], {hi: 1})

    def test_a_field_types_from_the_first_click_in_it_answering_or_not(self):
        spec = self.typing({"role": "field", "text": "Email", "name": "Email", "reacts": False,
                            "type": {"text": "tess@example.com", "speed": 20, "caret": False}})
        field = named(spec, "Email")
        x, y = S.to_frame(spec, S.solve(spec, "p1")["h"], field["x"] + 10, field["y"] + 10, FRAME)
        spec = M.clean({**spec, "path": [[0.0, x, y], [4.0, x, y]], "clicks": [[1.0, 1.1], [2.0, 2.1]]})
        played = K.clock(spec, self.FPS, FRAME)
        row = played["typed"][field["id"]]
        self.assertEqual(row[:10], [0] * 10)
        self.assertEqual(row[10:14], [1, 3, 5, 7])                                          # 20 a second: two a frame
        self.assertEqual(row[-1], 16)
        self.assertEqual([e["frame"] for e in played["events"] if e["kind"] == "type"], [10])   # the first click only
        self.assertEqual(played["caret"], {})
        # A click that isn't in it starts nothing.
        away = M.clean({**spec, "path": [[0.0, 1.0, 1.0]]})
        self.assertEqual(set(K.clock(away, self.FPS, FRAME)["typed"][field["id"]]), {0})

    def test_typing_starts_afresh_each_time_its_page_arrives(self):
        spec = panel({"role": "section", "children": [{"role": "text", "text": "Welcome", "name": "Hi", "type": {"speed": 10}}]},
                     {"role": "section", "children": [{"role": "text", "text": "Other"}]},
                     seconds=5, build={"on": False}, go_seconds=0.2,
                     page_keys=[[2.0, "p2", "fade"], [3.0, "p1", "fade"]])
        hi = named(spec, "Hi")["id"]
        played = K.clock(spec, self.FPS, FRAME)
        row = played["typed"][hi]
        self.assertEqual((row[0], row[6], row[29], row[30], row[31]), (1, 7, 7, 1, 2))
        self.assertEqual([e["frame"] for e in played["events"] if e["kind"] == "type"], [0, 30])
        self.assertNotIn(hi, K.pose(spec, played, 25)["typed"])                            # its page doesn't show

    def test_the_caret_stands_after_the_last_letter_and_blinks_a_while(self):
        spec = self.typing({"role": "text", "text": "Hey you", "name": "Hi", "type": {"speed": 10}}, seconds=6)
        hi = named(spec, "Hi")
        solved = S.solve(spec, "p1")
        size, char_w = S.letter(hi)
        line, first, last, x, baseline = S.typing(hi)[0]
        self.assertEqual((line, first, last, x), ("Hey you", 0, 7, hi["x"]))
        for shown in (0, 1, 4, 7, 99):
            self.assertEqual(K.caret(solved, hi["id"], shown),
                             [round(hi["x"] + min(shown, 7) * char_w, 2), round(baseline - K.CARET_UP * size, 2),
                              round(max(1.0, K.CARET_W * size), 2), round((K.CARET_UP + K.CARET_DOWN) * size, 2)])
        played = K.clock(spec, self.FPS, FRAME)
        marks = played["caret"][hi["id"]]
        done = played["typed"][hi["id"]].index(7)                                          # frame 6
        self.assertTrue(all(marks[f] for f in range(done + 1)))                            # standing while it types
        self.assertEqual(marks[3], K.caret(solved, hi["id"], 4))                           # after the last letter shown
        blinks = [1 if m else 0 for m in marks[done + 1:]]
        hold = int(K.CARET_HOLD * self.FPS)
        self.assertEqual(blinks[:hold], [1, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 0])     # on half of each blink
        self.assertEqual(set(blinks[hold:]), {0})                                          # then gone
        self.assertEqual(K.pose(spec, played, 2)["caret"], {hi["id"]: marks[2]})
        self.assertEqual(K.pose(spec, played, 30)["caret"], {})
        self.assertIsNone(K.caret(solved, "b999", 1))
        quiet = M.with_box(spec, hi["id"], "type", {"caret": False})
        self.assertIsNone(K.caret(S.solve(quiet, "p1"), hi["id"], 1))
        # A second line's caret is on it.
        two = self.typing({"role": "text", "text": "One two three four", "w": 60, "name": "Two", "type": {}})
        box = named(two, "Two")
        rows = S.typing(box)
        self.assertGreater(len(rows), 1)
        rect = K.caret(S.solve(two, "p1"), box["id"], rows[1][1] + 1)
        self.assertEqual((rect[0], rect[1]), (round(rows[1][3] + S.letter(box)[1], 2), round(rows[1][4] - K.CARET_UP * box["size"], 2)))

    def test_a_box_arrives_its_own_way(self):
        spec = self.typing({"role": "button", "text": "Rise", "name": "Rise"},
                           {"role": "button", "text": "Fade", "name": "Fade", "arrive": {"kind": "fade"}},
                           {"role": "button", "text": "Pop", "name": "Pop", "arrive": {"kind": "pop"}},
                           {"role": "button", "text": "None", "name": "None", "arrive": {"kind": "none"}},
                           build={"on": True, "stagger": 0.1, "seconds": 0.5})
        played = K.clock(spec, self.FPS, FRAME)
        ids = {name: named(spec, name)["id"] for name in ("Rise", "Fade", "Pop", "None")}
        self.assertNotIn(ids["None"], played["build"]["p1"])
        solved = S.solve(spec, "p1")
        looks = {name: [] for name in ids}
        for f in range(15):
            boxes = K.pose(spec, played, f)["pages"][0]["boxes"]
            for name, bid in ids.items():
                looks[name].append(K.carried(solved, boxes, bid))
        start = played["build"]["p1"][ids["Pop"]][0]
        self.assertEqual({round(g["sx"], 4) for g in looks["Rise"]} | {round(g["sx"], 4) for g in looks["Fade"]}, {1.0})
        self.assertTrue(any(g["dy"] > 1 for g in looks["Rise"]))
        self.assertEqual({round(g["dy"], 4) for g in looks["Fade"]}, {0.0})
        self.assertTrue(any(0 < g["alpha"] < 1 for g in looks["Fade"]))
        self.assertAlmostEqual(looks["Pop"][start]["sx"], K.POP, places=4)                 # it pops from POP of its size...
        self.assertGreater(max(g["sx"] for g in looks["Pop"]), 1.0)                        # ...past it a little...
        self.assertAlmostEqual(looks["Pop"][-1]["sx"], 1.0, places=4)                      # ...and settles
        self.assertEqual({round(g["dy"], 4) for g in looks["Pop"]}, {0.0})
        # Just there: never moved, and seen as much as what it is in (the page's own box, which arrives).
        root = solved["boxes"][0]["id"]
        for f, got in enumerate(looks["None"]):
            boxes = K.pose(spec, played, f)["pages"][0]["boxes"]
            self.assertEqual((round(got["dy"], 4), got["scale"]), (0.0, 1.0))
            self.assertAlmostEqual(got["alpha"], K.carried(solved, boxes, root)["alpha"], places=4)
        # With the build off, a box with its own arrival still arrives; the rest are just there.
        still = K.clock(M.clean({**spec, "build": {"on": False}}), self.FPS, FRAME)
        self.assertEqual(set(still["build"]["p1"]), {ids["Fade"], ids["Pop"]})

    def test_round_two_keeps_the_clock_quick_and_plain(self):
        template = M.clean({})
        spec = template
        for box in M.every(template["pages"][0]["root"]):
            if box["role"] == "text":
                spec = M.with_box(spec, box["id"], "type", {"speed": 20})
            if box["role"] == "field":
                spec = M.with_box(spec, box["id"], "type", {"text": "tess@example.com"})
        spec = M.clean({**spec, "seconds": 60, "path": [[t, 20 + (t * 7) % 60, 20 + (t * 11) % 60] for t in range(60)],
                        "clicks": [[t + 0.2, t + 0.4] for t in range(0, 60, 3)]})
        began = time.perf_counter()
        played = K.clock(spec, 60, FRAME)
        self.assertLess(time.perf_counter() - began, 1.0)
        self.assertTrue(played["typed"])
        self.assertEqual(json.loads(json.dumps(played)), played)
        began = time.perf_counter()
        for f in range(played["total"]):
            K.pose(spec, played, f)
        self.assertLess(time.perf_counter() - began, 2.0)


def build_at(played, page, box_id):
    """The frame a box starts to arrive on, on its page's first arrival."""
    arrives = next(change["at"] for change in played["pages"] if change["page"] == page)
    return arrives + played["build"][page][box_id][0]


if __name__ == "__main__":
    unittest.main()
