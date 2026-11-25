#!/usr/bin/env python3
"""
Panels - a tab of Design Studio (pages/design_studio/): a mock app window - an app, a
browser, a phone, a dialog or a widget - built from ready-made layouts and components,
with pages, boxes that answer a cursor, and a cursor that was recorded moving and
clicking through it. It goes onto the timeline as a new Fusion clip with everything
written as keys.

The view (web/) is the shelf of layouts and components, the stage, and what's picked.
Python says what a panel is (model.py), where its boxes sit (solve.py), how they are
drawn (styles.py) and what happens on every frame (clock.py); the view draws what it is
sent and plays the clock by the same rule (clock.pose), and comp.py writes the same
frames as keys. Add to timeline is place.add: a new clip, nothing already there changed.

The page is Data Graphics' GraphicPage (pages/data_graphics/graphic_page.py): the panel
kept in the settings as it's changed, every change a step Undo takes back, the open
timeline and Resolve's fonts read, and Add behind the busy overlay.

An image box shows a picture from a file - picked with a file dialog (image), or dropped
on the page (it goes to the picked image box, else the first one on the page in view
that has none) - filling the box and cropped to it.

Protocol:
    to the view    state, picked, toast, alert, drop_hover
    from the view  set, box, insert, remove_box, layout, page_add, page_remove,
                   page_rename, page_show, answers, rule, rule_remove, recorded,
                   image, image_clear, move, pick,
                   option, add, refresh, undo, redo, reset, switch_tab
"""

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QFileDialog

from core.i18n import tr, tr_filter
from pages.data_graphics import text_look
from pages.data_graphics.graphic_page import FALLBACK_FONTS, GraphicPage, css_face, face_metrics, web_dir
from pages.design.comp import face_key
from pages.interactions import comp as cursor_comp, model as interactions_model
from pages.interactions.page import InteractionsPage

from . import catalogue, clock, model as M, place, solve, styles

# What a face is called when it is the bold one, in the order tried.
BOLD_STYLES = ("Bold", "Semibold", "Demi Bold", "Medium", "Black")
PLAIN_STYLES = ("Regular", "Book", "Roman", "Normal", "Medium")
# The keys of the spec the look of a shelf tile follows.
TILE_KEYS = ("kind", "style", "accent", "dark")


class PanelsPage(GraphicPage):
    tool_id = "panels"
    display_name = "Panels"
    web_dir = web_dir(__file__)
    MODEL = M
    JOB = "panels.add"
    WHAT = "panel"
    ADDING = "Adding the panel…"
    file_drops = True                           # pictures dropped on the page go into image boxes
    IMAGE_FILTER = InteractionsPage.IMAGE_FILTER

    def working_on(self):
        return "Adding a panel" if self.adding else None

    # ------------------------------------------------------------ state --

    def build_state(self):
        super().build_state()
        self.showing = self.spec["start"]       # the page in view
        self.picked = ""                        # the box picked in the view (pick), where a dropped picture goes
        self._faces = {}                        # {(family, style): {"em", "baseline"}}, measured once each
        self._shelf, self._shelf_for = None, None

    def _face(self, family, style):
        key = (family, style)
        if key not in self._faces:
            self._faces[key] = text_look.clean_metrics(face_metrics(family, style))
        return self._faces[key]

    def metrics(self):
        """How Text+ sets the panel's two faces: {"Family|Style": {"em", "baseline"}}."""
        return {face_key(self.spec["font"], style): self._face(self.spec["font"], style)
                for style in (self.spec["face"], self.spec["bold_face"])}

    def shelf(self):
        """The shelf's tiles, each with its small picture in the panel's own kind, style
        and accent - drawn again only when one of those changes."""
        look = tuple(self.spec[key] for key in TILE_KEYS)
        if self._shelf_for != look:
            self._shelf_for = look
            self._shelf = {
                "layouts": [{"id": lid, "group": made["group"], "name": made["name"],
                             "tile": styles.tile(lid, self.spec)} for lid, made in catalogue.LAYOUTS.items()],
                "components": [{"id": cid, "name": made["name"], "tile": styles.tile(cid, self.spec)}
                               for cid, made in catalogue.COMPONENTS.items()],
                "groups": list(catalogue.GROUPS),
            }
        return self._shelf

    def _page_ids(self):
        return [page["id"] for page in self.spec["pages"]]

    def _state(self):
        if self.showing not in self._page_ids():
            self.showing = self.spec["start"]
        fps = self.preview_fps()
        pages = self._page_ids()
        return {"connected": bool(self.timeline), "timeline": self.timeline, "problem": self.problem,
                "frame": list(self.frame), "fps": fps, "spec": self.spec, "showing": self.showing,
                "solved": (solved := solve.solve_all(self.spec)),
                "drawn": {pid: styles.drawn(self.spec, pid, solved) for pid in pages},
                "chrome": styles.chrome(self.spec),
                "clock": clock.clock(self.spec, fps, self.frame),
                "shelf": self.shelf(),
                "face": css_face(self.spec["font"], self.spec["face"]),
                "bold_face": css_face(self.spec["font"], self.spec["bold_face"]),
                "kinds": [{"id": k, "name": catalogue.KIND_NAMES[k]} for k in catalogue.KINDS],
                "styles": [{"id": s, "name": catalogue.STYLE_NAMES[s]} for s in catalogue.STYLES],
                "accents": list(catalogue.ACCENTS),
                "cursors": cursor_comp.CURSOR_LOOKS, "cursor_dip": cursor_comp.CURSOR_DIP,
                "limits": {"width": [M.MIN_WIDTH, M.MAX_WIDTH], "seconds": [M.MIN_SECONDS, M.MAX_SECONDS],
                           "pages": M.MAX_PAGES, "rules": M.MAX_RULES},
                "replace": bool(self.settings.get("replace", True)), "adding": self.adding,
                "can_undo": bool(self.undo_steps), "can_redo": bool(self.redo_steps),
                "is_template": M.is_template(self.spec),
                # Where the view finds each image box's picture.
                "images": {box["id"]: QUrl.fromLocalFile(box["image"]["path"]).toString() for box in M.images(self.spec)},
                # Every way each box of the page in view moves: its Animations list.
                "animations": {box["id"]: M.animations(self.spec, box["id"])
                               for box in M.every(M.page_of(self.spec, self.showing)["root"])}}

    def _push_fonts(self):
        """Nothing: the view has no font to choose yet - a panel is set in Arial, which
        its text is measured for (solve.py)."""

    # ----------------------------------------------------------- actions --

    def change(self, key, value):
        """One top-level field changed in the view. A font new to the panel takes a plain
        and a bold style it has."""
        raw = {**self.spec, key: value}
        if key == "font":
            has = (self.fonts or FALLBACK_FONTS).get(str(value)) or []
            if has:
                raw["face"] = next((s for s in PLAIN_STYLES if s in has), has[0])
                raw["bold_face"] = next((s for s in BOLD_STYLES if s in has), raw["face"])
        return raw

    def on_set(self, payload):
        # The pages and their boxes have actions of their own.
        if (payload or {}).get("key") in ("pages", "rules", "path", "clicks", "start"):
            return
        super().on_set(payload)

    def _edit(self, spec, merge=None):
        """A panel an edit made (None: the edit changed nothing) becomes the panel."""
        if spec is None or self.adding:
            return False
        self.change_to(spec, merge)
        return True

    def on_box(self, payload):
        """One thing about a box changed: its "text", "w", "reacts", "go", "p", "chart"..."""
        payload = payload or {}
        self._edit(M.with_box(self.spec, payload.get("id"), payload.get("key"), payload.get("value")),
                   f"box:{payload.get('id')}:{payload.get('key')}")

    def on_insert(self, payload):
        """A component from the shelf, into the box that was picked (or the page in view's
        own box); the view is told which box to pick."""
        payload = payload or {}
        if self.adding:
            return
        into = payload.get("into") or M.page_of(self.spec, self.showing)["root"]["id"]
        at = payload.get("at")
        spec = M.with_insert(self.spec, payload.get("component"), into, None if at in (None, -1) else at)
        if spec is None:
            return
        had = {box["id"] for box in M.boxes(self.spec)}
        new = [box["id"] for box in M.boxes(spec) if box["id"] not in had]
        self.change_to(spec)
        if new:
            self.picked = new[0]
            self.emit("picked", {"id": new[0]})        # the component's own box: a parent comes before what's in it

    def on_remove_box(self, payload):
        self._edit(M.without_box(self.spec, (payload or {}).get("id")))

    def on_layout(self, payload):
        """The page (the one in view, unless said) becomes a layout from the shelf."""
        payload = payload or {}
        self._edit(M.with_layout(self.spec, payload.get("page") or self.showing, payload.get("layout")))

    def on_page_add(self, _payload=None):
        if self.adding:
            return
        if len(self.spec["pages"]) >= M.MAX_PAGES:
            return self.emit("toast", {"text": f"A panel holds up to {M.MAX_PAGES} pages."})
        spec = M.with_page(self.spec)
        if spec is not None:
            self.showing = spec["pages"][-1]["id"]
            self.change_to(spec)

    def on_page_remove(self, payload):
        self._edit(M.without_page(self.spec, (payload or {}).get("id")))

    def on_page_rename(self, payload):
        payload = payload or {}
        self._edit(M.with_page_name(self.spec, payload.get("id"), payload.get("name")),
                   f"page_name:{payload.get('id')}")

    def on_page_show(self, payload):
        """Which page is in view on the stage - how the view is looking at the panel, not
        part of it: nothing to undo."""
        page_id = (payload or {}).get("id")
        if page_id in self._page_ids() and page_id != self.showing:
            self.showing = page_id
            self._push_state()

    def on_answers(self, payload):
        """How a state looks: a box's own ("id"), or every box's ("" - the panel's)."""
        payload = payload or {}
        self._edit(M.with_answers(self.spec, payload.get("id") or "", payload.get("state"), payload.get("key"),
                                  payload.get("value")),
                   f"answers:{payload.get('id')}:{payload.get('state')}:{payload.get('key')}")

    def on_rule(self, payload):
        """A rule that changes the page: a new one ("id" "") or one thing about one."""
        payload = payload or {}
        if not payload.get("id") and len(self.spec["rules"]) >= M.MAX_RULES:
            return self.emit("toast", {"text": f"A panel holds up to {M.MAX_RULES} rules."})
        self._edit(M.with_rule(self.spec, payload.get("id") or "", payload.get("key"), payload.get("value")),
                   f"rule:{payload.get('id')}:{payload.get('key')}")

    def on_rule_remove(self, payload):
        self._edit(M.without_rule(self.spec, (payload or {}).get("id")))

    def on_recorded(self, payload):
        """What the mouse did while recording: {"samples": [[t, x, y]], "clicks":
        [[down, up]], "seconds"} - the cursor's path and clicks from now on."""
        payload = payload or {}
        if self.adding or not isinstance(payload.get("samples"), list) or not payload["samples"]:
            return
        self.change_to(M.with_recording(self.spec, payload["samples"], payload.get("clicks"), payload.get("seconds")))

    # ----------------------------------------------------------- layers --

    def on_pick(self, payload):
        """Which box is picked in the view ("" for none) - where a dropped picture goes.
        How the view is looking at the panel, not part of it: nothing to undo, nothing sent."""
        box_id = (payload or {}).get("id") or ""
        self.picked = box_id if M.find(self.spec, box_id) else ""

    def on_move(self, payload):
        """A layer (and what is in it) moved: {"id", "into" (a section on its page), "at"
        (its place among that section's children, counted without it; None or -1 for the end)}."""
        payload = payload or {}
        at = payload.get("at")
        self._edit(M.with_move(self.spec, payload.get("id"), payload.get("into"), None if at in (None, -1) else at))

    # ----------------------------------------------------------- images --

    def _image_box(self, box_id):
        found = M.find(self.spec, box_id) if box_id else None
        return found["box"] if found and found["box"]["role"] == "image" else None

    def on_image(self, payload):
        """A picture for an image box, asked for with a file dialog: {"id"}."""
        box = self._image_box((payload or {}).get("id"))
        if box is None or self.adding:
            return
        start = str(self.settings.get("image_dir") or "")
        path, _chosen = QFileDialog.getOpenFileName(self, tr("Choose an image"), start if os.path.isdir(start) else "",
                                                    tr_filter(self.IMAGE_FILTER))
        if path:
            self.settings["image_dir"] = os.path.dirname(path)
            self.settings.save()
            self._put_image(box["id"], path)

    def on_image_clear(self, payload):
        """An image box's picture taken off: its placeholder again. {"id"}."""
        box = self._image_box((payload or {}).get("id"))
        if box is not None:
            self._edit(M.with_image(self.spec, box["id"], ""))

    def _put_image(self, box_id, path):
        """A picture file into an image box - said, when it can't be read."""
        size = QImageReader(path).size() if interactions_model.is_image(path) else None
        if size is None or not size.isValid():
            self.emit("toast", {"text": "Buddy couldn't read that image. PNG and JPEG images can be added."})
            return False
        if not self._edit(M.with_image(self.spec, box_id, os.path.normpath(path), [size.width(), size.height()])):
            return False
        self.picked = box_id
        self.emit("picked", {"id": box_id})
        return True

    def accepts_drops(self):
        return not self.adding

    def on_files_dropped(self, paths):
        """Pictures dragged onto the page: the first into the picked image box - else the
        first image box on the page in view that has no picture - and each one after
        into the next such box."""
        if self.adding:
            return
        picked = self._image_box(self.picked)
        page = M.page_of(self.spec, self.showing)
        empty = [box["id"] for box in M.every(page["root"]) if box["role"] == "image" and not box["image"]]
        targets = ([picked["id"]] if picked else []) + [bid for bid in empty if not picked or bid != picked["id"]]
        if not targets:
            return self.emit("toast", {"text": "There's no image box for a picture here. Add one from + Add first."})
        for path in paths or []:
            if not targets:
                break
            if self._put_image(targets[0], path):
                targets.pop(0)

    def here(self, controller, **args):
        return place.add(controller, **args)

    def added_text(self, result):
        return (f"Panel replaced on V{result['track']}." if result.get("replaced")
                else f"Panel added on V{result['track']}.")
