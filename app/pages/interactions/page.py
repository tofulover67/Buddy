#!/usr/bin/env python3
"""
Interactions - a tab of Design Studio (pages/design_studio/): shapes that answer a cursor the way a
button on a screen does (they grow and glow under it, dip when clicked), added to the
timeline as a new Fusion clip.

The view (web/) is a stage of shapes - buttons, cards, circles, and pictures from image
files (picked with Image, or dropped on the page). Arrange moves and sizes
them; Try it makes the mouse the cursor, so the shapes answer as they will; Record keeps
what the mouse does as the cursor's path and clicks. The preview then plays it back as
Resolve will: what the view draws on a frame is model.py's rule, the same one the
comp's expressions run (comp.py). Add to timeline is place.add: a new clip, nothing
already there changed.

The page is Data Graphics' GraphicPage (pages/data_graphics/graphic_page.py): the
interaction kept in the settings as it's changed, every change a step Undo takes back,
the open timeline and Resolve's fonts read, and Add behind the busy overlay.

Protocol:
    to the view    state, fonts, picked, toast, alert, drop_hover
    from the view  set, element, new_element, remove_element, front_element, answers,
                   recorded, option, add, refresh, undo, redo, reset
"""

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QFileDialog

from core.i18n import tr, tr_filter
from pages.data_graphics.graphic_page import GraphicPage, css_face, web_dir

from . import comp, model as M, place


class InteractionsPage(GraphicPage):
    tool_id = "interactions"
    display_name = "Interactions"
    web_dir = web_dir(__file__)
    MODEL = M
    JOB = "interactions.add"
    WHAT = "interaction"
    ADDING = "Adding the interaction…"
    file_drops = True               # image files dropped on the page become shapes
    IMAGE_FILTER = "Images (*.png *.jpg *.jpeg)"

    def working_on(self):
        return "Adding an interaction" if self.adding else None

    # ------------------------------------------------------------ state --

    def preview(self):
        """What the view plays: where the cursor is and whether its button is down on
        every frame, and how many frames a hover and a press take - the view works out
        how each shape answers from those, by model.amounts()'s rule."""
        fps = self.preview_fps()
        total = M.frames(self.spec, fps)
        return {"frame": list(self.frame), "fps": fps, "total": total,
                "cursor": [[round(x, 3), round(y, 3)] for x, y in M.cursor(self.spec, fps, total)],
                "down": [int(d) for d in M.down(self.spec, fps, total)],
                "hover_frames": M.look_back(self.spec["hover_seconds"], fps),
                "press_frames": M.look_back(self.spec["press_seconds"], fps)}

    def _state(self):
        return {"connected": bool(self.timeline), "timeline": self.timeline, "problem": self.problem,
                "frame": list(self.frame), "fps": self.fps, "spec": self.spec, "preview": self.preview(),
                "face": css_face(self.spec["font"], self.spec["style"]),
                # Where the view finds each image shape's picture.
                "images": {el["id"]: QUrl.fromLocalFile(el["path"]).toString()
                           for el in self.spec["elements"] if el["kind"] == "image"},
                "kinds": list(M.KINDS), "full": len(self.spec["elements"]) >= M.MAX_ELEMENTS,
                "max_seconds": M.MAX_SECONDS, "min_seconds": M.MIN_SECONDS,
                # How far each of a state's numbers goes: the States pop's sliders.
                "answer_keys": {key: list(span) for key, span in M.ANSWER_KEYS.items()},
                "glow": {"reach": comp.GLOW_REACH, "soft": comp.GLOW_SOFT, "lift": comp.GLOW_LIFT},
                "cursors": comp.CURSOR_LOOKS, "cursor_dip": comp.CURSOR_DIP,
                "replace": bool(self.settings.get("replace", True)), "adding": self.adding,
                "can_undo": bool(self.undo_steps), "can_redo": bool(self.redo_steps),
                "is_template": self.spec == M.clean({})}

    # ----------------------------------------------------------- actions --

    def on_set(self, payload):
        # The shapes, the path, the clicks and the answers have actions of their own.
        if (payload or {}).get("key") in ("elements", "path", "clicks", "answers"):
            return
        super().on_set(payload)

    def on_answers(self, payload):
        """How shapes look in a state: {"id", "state" ("hover" or "press"), "key" ("grow",
        "lift", "glow", "outline" - or "reset", a shape follows the interaction's again),
        "value"}. id "" is every shape that follows the interaction's answers."""
        payload = payload or {}
        if self.adding:
            return
        spec = M.with_answers(self.spec, payload.get("id") or "", payload.get("state"), payload.get("key"),
                              payload.get("value"))
        if spec is not None:
            self.change_to(spec, f"answers:{payload.get('id') or ''}:{payload.get('state')}:{payload.get('key')}")

    def on_element(self, payload):
        """One thing about a shape changed: its "box" (dragged or stretched on the
        stage), "label", "colour", "radius" or whether it "reacts"."""
        payload = payload or {}
        if self.adding:
            return
        spec = M.with_element(self.spec, payload.get("id"), payload.get("key"), payload.get("value"))
        if spec is not None:
            self.change_to(spec, f"element:{payload.get('id')}:{payload.get('key')}")

    def on_new_element(self, payload):
        """A new shape of a kind, on top of the pile; the view is told which to pick. An
        image's file is asked for first."""
        if self.adding:
            return
        kind = (payload or {}).get("kind")
        if self._full():
            return
        if kind == "image":
            start = str(self.settings.get("image_dir") or "")
            path, _chosen = QFileDialog.getOpenFileName(self, tr("Choose an image"),
                                                        start if os.path.isdir(start) else "", tr_filter(self.IMAGE_FILTER))
            if path:
                self.settings["image_dir"] = os.path.dirname(path)
                self.settings.save()
                self._add_image(path)
            return
        self._add(kind)

    def _full(self):
        """Whether there's no room for another shape - said, if so."""
        if len(self.spec["elements"]) < M.MAX_ELEMENTS:
            return False
        self.emit("toast", {"text": f"An interaction holds up to {M.MAX_ELEMENTS} shapes."})
        return True

    def _add(self, kind, more=None):
        spec, eid = M.with_new(self.spec, kind, more)
        if spec is None:
            return False
        self.change_to(spec)
        self.emit("picked", {"id": eid})
        return True

    def _add_image(self, path):
        """An image file as a new shape, in a box of the picture's own shape."""
        size = QImageReader(path).size() if M.is_image(path) else None
        if size is None or not size.isValid():
            self.emit("toast", {"text": "Buddy couldn't read that image. PNG and JPEG images can be added."})
            return False
        px = [size.width(), size.height()]
        w, h = M.boxed(px, self.frame)
        return self._add("image", {"path": os.path.normpath(path), "px": px, "w": w, "h": h})

    def accepts_drops(self):
        return not self.adding

    def on_files_dropped(self, paths):
        """Image files dragged onto the page: each a new shape, while there's room."""
        for path in paths or []:
            if self.adding or self._full():
                break
            self._add_image(path)

    def on_remove_element(self, payload):
        spec = None if self.adding else M.without(self.spec, (payload or {}).get("id"))
        if spec is not None:
            self.change_to(spec)

    def on_front_element(self, payload):
        if self.adding:
            return
        spec, eid = M.to_front(self.spec, (payload or {}).get("id"))
        if spec is not None:
            self.change_to(spec)
            self.emit("picked", {"id": eid})

    def on_recorded(self, payload):
        """What the mouse did while recording: {"samples": [[t, x, y]], "clicks":
        [[down, up]], "seconds"} - the cursor's path and clicks from now on."""
        payload = payload or {}
        if self.adding or not isinstance(payload.get("samples"), list) or not payload["samples"]:
            return
        self.change_to(M.with_recording(self.spec, payload["samples"], payload.get("clicks"), payload.get("seconds")))

    def here(self, controller, **args):
        return place.add(controller, **args)

    def added_text(self, result):
        return (f"Interaction replaced on V{result['track']}." if result.get("replaced")
                else f"Interaction added on V{result['track']}.")
