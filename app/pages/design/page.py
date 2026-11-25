#!/usr/bin/env python3
"""
Design - a tab of Design Studio (pages/design_studio/): a canvas to draw graphic assets on - shapes,
paths drawn with a pen, text, pictures, grouped and combined - that goes onto the
timeline as a new Fusion clip whose nodes can be changed there.

The view (web/) is the editor: tools and the layers list, the canvas, and what's picked.
It edits a copy of the design and sends the whole of it back after each change
("design"); model.clean() makes whatever arrives a valid design, so Python stays the one
that says what a design is. What the canvas draws and what comp.py writes follow the
same rules (model.py, geometry.py). Add to timeline is place.add: a new clip, nothing
already there changed.

The page is Data Graphics' GraphicPage (pages/data_graphics/graphic_page.py): the design
kept in the settings as it's changed, every change a step Undo takes back, the open
timeline and Resolve's fonts read, and Add behind the busy overlay. Designs saved by
name are files of their own (store.py).

Protocol:
    to the view    state, fonts, picked, toast, alert, drop_hover, dropped, dropper_done
    from the view  design, new_image, save, open, new_design, delete_design,
                   option, add, refresh, undo, redo, screen_dropper
"""

import os
import re

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QFileDialog

from core.i18n import tr, tr_filter
from pages.data_graphics import text_look
from pages.data_graphics.graphic_page import FALLBACK_FONTS, GraphicPage, css_face, face_metrics, web_dir

from . import comp, model as M, place, store


# How the canvas is shown, kept in the page's settings rather than the design: the
# rulers, and whether the design's guides are shown and locked. {key: default}
VIEW_OPTIONS = {"rulers": True, "guides_shown": True, "guides_locked": False}


class DesignPage(GraphicPage):
    tool_id = "design"
    display_name = "Design"
    web_dir = web_dir(__file__)
    MODEL = M
    JOB = "design.add"
    WHAT = "design"
    ADDING = "Adding the design…"
    file_drops = True               # image files dropped on the page become layers
    IMAGE_FILTER = "Images (*.png *.jpg *.jpeg)"

    def working_on(self):
        return "Adding a design" if self.adding else None

    # ------------------------------------------------------------ state --

    def build_state(self):
        super().build_state()
        self.rev = 0                    # the view's count of its changes, as last heard
        self.design_id = str(self.settings.get("design_id") or "")
        self._kept = store.load(self.design_id) if self.design_id else None      # as it was last saved
        if self._kept is None:
            self.design_id = ""
        self._designs = store.listed()
        self._faces = {}                # {(family, style): {"em", "baseline"}}, measured once each

    def _face(self, family, style):
        key = (family, style)
        if key not in self._faces:
            self._faces[key] = text_look.clean_metrics(face_metrics(family, style))
        return self._faces[key]

    def metrics(self):
        """How Text+ sets each face the design's text is in: {"Family|Style": {"em", "baseline"}}."""
        return {comp.face_key(family, style): self._face(family, style) for family, style in M.faces(self.spec)}

    def _state(self):
        faces = {comp.face_key(family, style): {"css": css_face(family, style), **self._face(family, style)}
                 for family, style in M.faces(self.spec)}
        return {"connected": bool(self.timeline), "timeline": self.timeline, "problem": self.problem,
                "frame": list(self.frame), "fps": self.fps, "spec": self.spec, "rev": self.rev, "faces": faces,
                # Where the view finds each image layer's picture.
                "images": {layer["path"]: QUrl.fromLocalFile(layer["path"]).toString() for layer in M.images(self.spec)},
                "designs": self._designs, "design_id": self.design_id, "saved": self.spec == self._kept,
                "max_layers": M.MAX_LAYERS, "max_drawn": M.MAX_DRAWN, "max_stops": M.MAX_STOPS,
                "replace": bool(self.settings.get("replace", True)), "adding": self.adding,
                "can_undo": bool(self.undo_steps), "can_redo": bool(self.redo_steps),
                "max_guides": M.MAX_GUIDES,
                **{key: bool(self.settings.get(key, on)) for key, on in VIEW_OPTIONS.items()}}

    def _push_fonts(self):
        fonts = {family: list(styles) for family, styles in (self.fonts or FALLBACK_FONTS).items()}
        # A face in use is always in the list, even one this Resolve doesn't have.
        for family, style in M.faces(self.spec):
            styles = fonts.setdefault(family, [])
            if style not in styles:
                styles.append(style)
        self.emit("fonts", {"fonts": fonts})

    def change(self, key, value):
        return {**self.spec, key: value}

    def _on_read(self, result, error):
        super()._on_read(result, error)
        # A design nothing has been drawn on yet takes the size of the timeline it's for.
        if (error is None and self.timeline and not self.spec["layers"] and not self.undo_steps
                and self.spec["size"] != list(self.frame)):
            self.spec = M.clean({**self.spec, "size": list(self.frame)})
            self._save()
            self._push_state()

    # ----------------------------------------------------------- actions --

    def _heard(self, payload):
        try:
            self.rev = max(self.rev, int((payload or {}).get("rev")))
        except (TypeError, ValueError):
            pass

    def on_set(self, payload):
        """Nothing is set a field at a time here: the view sends the design."""

    def on_reset(self, _payload=None):
        """There is no template to go back to: New design starts again."""

    def on_design(self, payload):
        """The design as the view now has it - one step, or more of the step before when
        "merge" says it is the same kind of change (a field typed in, a slider dragged)."""
        payload = payload or {}
        if self.adding or not isinstance(payload.get("design"), dict):
            return
        self._heard(payload)
        self.change_to(M.clean(payload["design"]), str(payload.get("merge") or "") or None)

    def on_option(self, payload):
        """GraphicPage's options, and how the canvas is shown (VIEW_OPTIONS): Shift+R
        shows the rulers or hides them, the rulers' corner menu the guides."""
        key = (payload or {}).get("key")
        if key not in VIEW_OPTIONS:
            return super().on_option(payload)
        self.settings[key] = bool(payload.get("value"))
        self.settings.save()
        self._push_state()

    def on_undo(self, payload=None):
        self._heard(payload)
        super().on_undo()

    def on_redo(self, payload=None):
        self._heard(payload)
        super().on_redo()

    def _full(self):
        """Whether there's no room for another layer - said, if so."""
        if M.count(self.spec) < M.MAX_LAYERS:
            return False
        self.emit("toast", {"text": f"A design holds up to {M.MAX_LAYERS} layers."})
        return True

    def on_new_image(self, payload=None):
        """A picture file asked for, and added as a layer on top."""
        if self.adding or self._full():
            return
        self._heard(payload)
        start = str(self.settings.get("image_dir") or "")
        path, _chosen = QFileDialog.getOpenFileName(self, tr("Choose an image"),
                                                    start if os.path.isdir(start) else "", tr_filter(self.IMAGE_FILTER))
        if path:
            self.settings["image_dir"] = os.path.dirname(path)
            self.settings.save()
            self._add_image(path)

    def _add_image(self, path):
        """An image file as a new layer in the middle of the canvas, never stretched."""
        size = QImageReader(path).size() if M.is_image(path) else None
        if size is None or not size.isValid():
            self.emit("toast", {"text": "Buddy couldn't read that image. PNG and JPEG images can be added."})
            return False
        px = [size.width(), size.height()]
        w, _h = M.boxed(px, self.spec["size"])
        n = len(M.images(self.spec))
        made, lid = M.with_new(self.spec, {
            "kind": "image", "name": os.path.splitext(os.path.basename(path))[0], "path": os.path.normpath(path), "px": px, "w": w,
            "x": self.spec["size"][0] / 2 + 24 * (n % 8), "y": self.spec["size"][1] / 2 + 24 * (n % 8)})
        if made is None:
            return False
        self.change_to(made)
        self.emit("picked", {"ids": [lid]})
        return True

    def accepts_drops(self):
        return not self.adding

    def on_files_dropped(self, paths):
        """Image files dragged onto the page: each a new layer, while there's room."""
        for path in paths or []:
            if self.adding or self._full():
                break
            self._add_image(path)

    # ------------------------------------------------- designs kept by name --

    def _start(self, design, design_id):
        """A different design on the canvas: its history starts here."""
        self.spec = design
        self.design_id = design_id
        self._kept = design if design_id else None
        self.undo_steps, self.redo_steps = [], []
        self._last_change = None
        self.settings["design_id"] = design_id
        self._save()
        self._push_fonts()
        self._push_state()
        self.emit("picked", {"ids": []})

    def on_save(self, payload):
        """The design kept under its name: over the saved one it came from, or ("copy")
        as a new one."""
        payload = payload or {}
        if self.adding:
            return
        self._heard(payload)
        name = " ".join(str(payload.get("name") or "").split())[:M.MAX_NAME]
        if name != self.spec["name"]:
            self.change_to(M.clean({**self.spec, "name": name}))
        try:
            self.design_id = store.save(self.spec, None if payload.get("copy") else self.design_id)
        except OSError as exc:
            return self.emit("alert", {"title": "Couldn't save the design", "text": str(exc)})
        self._kept = self.spec
        self.settings["design_id"] = self.design_id
        self.settings.save()
        self._designs = store.listed()
        self._push_state()
        self.emit("toast", {"text": "Design saved."})

    def on_open(self, payload):
        if self.adding:
            return
        self._heard(payload)
        design_id = str((payload or {}).get("id") or "")
        design = store.load(design_id)
        if design is None:
            self._designs = store.listed()
            self._push_state()
            return self.emit("toast", {"text": "That design couldn't be opened."})
        self._start(design, design_id)

    def on_new_design(self, payload=None):
        if self.adding:
            return
        self._heard(payload)
        self._start(M.clean({"size": list(self.frame)}), "")

    def on_delete_design(self, payload):
        self._heard(payload)
        design_id = str((payload or {}).get("id") or "")
        try:
            store.remove(design_id)
        except OSError as exc:
            return self.emit("alert", {"title": "Couldn't delete the design", "text": str(exc)})
        if design_id == self.design_id:
            # What's on the canvas stays, as a design not saved yet.
            self.design_id, self._kept = "", None
            self.settings["design_id"] = ""
            self.settings.save()
        self._designs = store.listed()
        self._push_state()

    # -------------------------------------------------------- eyedropper --

    def on_screen_dropper(self, _payload=None):
        """A colour from anywhere on the screen, for the field the view's dropper was
        for: Color Palette's screen dropper, with Buddy's window faded out of the way. Only
        the colour comes back ("dropped") - no picture of the screen is kept or sent -
        then "dropper_done", which also comes when it was cancelled or couldn't start."""
        if getattr(self, "_screen_dropper", None) is not None:
            return
        if self.adding:
            return self.emit("dropper_done", {})
        from pages.color_palette.screen_dropper import ScreenColorDropper

        taken = []
        try:
            dropper = ScreenColorDropper(hide_parent_callback=lambda: self._fade_for_dropper(True),
                                         show_parent_callback=lambda: self._fade_for_dropper(False))
        except Exception as exc:  # noqa: BLE001 - said to the user
            self._fade_for_dropper(False)
            self.emit("dropper_done", {})
            return self.emit("alert", {"title": tr("Screen dropper"),
                                       "text": tr("Could not start the screen colour dropper:\n{0}").format(tr(str(exc)))})
        self._screen_dropper = dropper
        dropper.color_sampled.connect(taken.append)
        dropper.failed.connect(lambda text: self.emit("alert", {"title": tr("Screen dropper"), "text": tr(text)}))
        # A turn later, once the window is back: the colour, then that it's over.
        dropper.ended.connect(lambda: QTimer.singleShot(0, lambda: self._screen_dropped(taken)))

    def _screen_dropped(self, taken):
        self._screen_dropper = None
        hexes = [str(h).upper() for h in taken if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(h))]
        if hexes:
            self.emit("dropped", {"hex": hexes[-1]})
        self.emit("dropper_done", {})

    def _fade_for_dropper(self, hidden):
        """Buddy's window out of the way of the screen dropper - faded, not hidden (hiding
        and showing a window on Windows flashes it bright), as Color Palette does."""
        window = self.window()
        if hidden:
            self._opacity_before_dropper = window.windowOpacity()
            window.setWindowOpacity(0.0)
        else:
            window.setWindowOpacity(getattr(self, "_opacity_before_dropper", 1.0))

    # --------------------------------------------------------------- add --

    def ready_to_add(self):
        if not any(not layer["hidden"] for layer in self.spec["layers"]):
            return "There's nothing to add yet. Draw something on the canvas first."
        return ""

    def here(self, controller, **args):
        return place.add(controller, **args)

    def added_text(self, result):
        return (f"Design replaced on V{result['track']}." if result.get("replaced")
                else f"Design added on V{result['track']}.")
