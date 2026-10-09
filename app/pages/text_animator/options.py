#!/usr/bin/env python3
"""
Text Animator's settings - every control on the page, its range and default, stored in the
tool's ToolSettings bucket (~/.text_animator/settings.json) under the SAME keys earlier
versions used, so saved values carry over. No Qt: unit-tested
(tests/test_text_animator_page.py).

Colours are stored as float triples (<prefix>_r/_g/_b, 0-1 - what Fusion's Red1/
Green1/Blue1 inputs take) and go to the page as "#RRGGBB".
"""

from __future__ import annotations

import re
from typing import Tuple

from .animation_engine import FusionAnimationEngine
from .overlays import GRID_TYPES, SAFE_ZONE_TYPES
from .layout_presets import LAYOUT_PRESETS
from .word_by_word import LINE_CHOICES as WBW_LINES

ANIM_PRESETS = ["Pop (Scale)", "Bounce (Extra Rebound)", "Fade (Opacity)", "Slide (Direction + Fade)",
                "Typewriter (Letters)", "Letter Fade (Letters)", "Letter Pop (Letters)"]
# Which Text+ clips an Apply works on - each tab's "Apply to" dropdown (Font Styling, Timeline
# Layout, Timeline Animation). "selected" is the clips selected on Resolve's timeline.
SCOPES = ("timeline", "selected", "playhead", "track")
# tab -> settings key of its "Apply to" choice. Font Styling's also reads the two older keys below.
SCOPE_KEYS = {"style_scope": "font_style_scope", "anim_scope": "anim_scope", "layout_scope": "layout_scope"}
# Word-by-word's Space words: under the playhead, or each group of words on the timeline.
WBW_SCOPES = ("playhead", "timeline")

# name -> (label, min, max, step, decimals, default, settings key). The page's sliders.
SLIDERS = {
    "font_size": ("Size", 0.01, 1.0, 0.01, 4, 0.08, "font_size"),   # 4: a typed size, to the pixel
    "outline_thickness": ("Thickness", 0.0, 1.0, 0.005, 3, 0.04, "outline_thickness"),
    "outline_opacity": ("Opacity", 0.0, 1.0, 0.01, 2, 1.0, "outline_opacity"),
    "shadow_offset_x": ("Offset X", -1.0, 1.0, 0.01, 3, 0.0, "shadow_offset_x"),
    "shadow_offset_y": ("Offset Y", -1.0, 1.0, 0.01, 3, -0.02, "shadow_offset_y"),
    "shadow_blur": ("Blur", 0.0, 100.0, 0.5, 2, 5.0, "shadow_blur"),
    "shadow_opacity": ("Opacity", 0.0, 1.0, 0.01, 2, 0.75, "shadow_opacity"),
    "background_outline_width": ("Outline width", 0.0, 1.0, 0.01, 3, 0.0, "background_outline_width"),
    "background_corner_radius": ("Rounding", 0.0, 1.0, 0.01, 3, 0.0, "background_corner_radius"),
    "background_extend_horizontal": ("Padding across", -1.0, 1.0, 0.01, 3, 0.0, "background_extend_horizontal"),
    "background_extend_vertical": ("Padding up and down", -1.0, 1.0, 0.01, 3, 0.0, "background_extend_vertical"),
    "background_opacity": ("Opacity", 0.0, 1.0, 0.01, 2, 0.5, "background_opacity"),
    # Word-by-word's Space as one: the gaps between words, in spaces (1 = one Text+ clip's own).
    "wbw_spacing": ("Word spacing", 0.0, 2.0, 0.05, 2, 1.0, "word_by_word_spacing"),
}
# name -> (default (r, g, b), settings key prefix)
COLORS = {
    "font_color": ((1.0, 1.0, 1.0), "current_font_color"),
    "outline_color": ((1.0, 0.0, 0.0), "outline_color"),
    "shadow_color": ((0.0, 0.0, 0.0), "shadow_color"),
    "background_color": ((0.0, 0.0, 0.0), "background_color"),
    "background_outline_color": ((1.0, 1.0, 1.0), "background_outline_color"),
}
# name -> (default, settings key). "*_group_on" replaced "*_group_checked" when outline/
# shadow/background started defaulting to off, so old default-on settings don't tick them.
TOGGLES = {
    "font_on": (True, "font_group_checked"),
    "outline_on": (False, "outline_group_on"),
    "shadow_on": (False, "shadow_group_on"),
    "background_on": (False, "background_group_on"),
    "background_override_sizing": (False, "background_override_sizing"),
}
TRACKS = {
    "style_track": (1, "font_style_scope_track"),
    "anim_track": (1, "anim_specific_track"),
    "layout_track": (1, "layout_specific_track"),
}
# The canvases' overlay and snapping settings (keys of their own).
OVERLAY = {
    "grid_type": ("None", GRID_TYPES),
    "grid_color": "#E6E6E6",
    "grid_opacity": 0.5,
    "grid_spacing": 0.0,        # fraction of canvas width; 0 = the resolution's own (canvas_math)
    "safe_type": ("None", SAFE_ZONE_TYPES),
    "safe_color": "#000000",
    "safe_opacity": 0.6,
    "snap": True,
    "snap_elements": True,
    "snap_safe": True,
}

# A Font Styling look: what it sets, by control name. Picking one puts these on the page's
# controls; saving one keeps the current values of all of them (and the font).
LOOK_SLIDERS = ("font_size", "outline_thickness", "outline_opacity", "shadow_offset_x", "shadow_offset_y",
                "shadow_blur", "shadow_opacity", "background_opacity", "background_corner_radius",
                "background_extend_horizontal", "background_extend_vertical")
LOOK_COLORS = ("font_color", "outline_color", "shadow_color", "background_color")
LOOK_TOGGLES = ("outline_on", "shadow_on", "background_on")
MAX_LOOKS = 60
MAX_LOOK_NAME = 40

_PLAIN = {"outline_on": False, "shadow_on": False, "background_on": False}
_SOFT_SHADOW = {"shadow_on": True, "shadow_color": "#000000", "shadow_offset_x": 0.0, "shadow_offset_y": -0.004,
                "shadow_blur": 5.0, "shadow_opacity": 0.8}
_BLACK_OUTLINE = {"outline_on": True, "outline_color": "#000000", "outline_opacity": 1.0}
_PILL = {"background_on": True, "background_color": "#FFFFFF", "background_opacity": 1.0,
         "background_corner_radius": 0.3, "background_extend_horizontal": 0.015, "background_extend_vertical": 0.008}
_BOX = {"background_on": True, "background_color": "#000000", "background_opacity": 0.65,
        "background_corner_radius": 0.0, "background_extend_horizontal": 0.01, "background_extend_vertical": 0.005}
# The ready-made looks, for a wide (16:9-ish) timeline and a tall (9:16) one: (name, values,
# bold - the boldest style of the picked font's family, if it has one). They leave the font
# itself alone, and a wide one leaves the size too.
BUILT_IN_LOOKS = {
    "wide": [
        ("Clean", {**_PLAIN, "font_color": "#FFFFFF", **_SOFT_SHADOW}, False),
        ("Outlined", {**_PLAIN, "font_color": "#FFFFFF", **_BLACK_OUTLINE, "outline_thickness": 0.05}, False),
        ("Boxed", {**_PLAIN, "font_color": "#FFFFFF", **_BOX}, False),
        ("Broadcast", {**_PLAIN, "font_color": "#FFE600", **_BLACK_OUTLINE, "outline_thickness": 0.06}, False),
        ("Social pill", {**_PLAIN, "font_color": "#000000", **_PILL}, False),
    ],
    "tall": [
        ("Big bold", {**_PLAIN, "font_size": 0.12, "font_color": "#FFFFFF", **_BLACK_OUTLINE,
                      "outline_thickness": 0.08}, True),
        ("Yellow pop", {**_PLAIN, "font_size": 0.12, "font_color": "#FFE600", **_BLACK_OUTLINE,
                        "outline_thickness": 0.08}, True),
        ("Social pill", {**_PLAIN, "font_size": 0.09, "font_color": "#000000", **_PILL}, True),
        ("Boxed", {**_PLAIN, "font_size": 0.09, "font_color": "#FFFFFF", **_BOX, "background_opacity": 0.7}, False),
        ("Minimal", {**_PLAIN, "font_size": 0.08, "font_color": "#FFFFFF", **_SOFT_SHADOW}, False),
    ],
}

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(float(c) * 255))):02X}" for c in rgb)


def from_hex(text):
    match = _HEX.match(str(text or "").strip())
    if not match:
        return None
    digits = match.group(1)
    return tuple(int(digits[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Options:
    """The page's settings, read from and written straight to the ToolSettings bucket."""

    def __init__(self, settings):
        self.s = settings

    # ------------------------------------------------------------ reads --

    def slider(self, name) -> float:
        _label, lo, hi, _step, decimals, default, key = SLIDERS[name]
        return round(max(lo, min(hi, _float(self.s.get(key, default), default))), decimals)

    def color(self, name):
        default, prefix = COLORS[name]
        return tuple(_float(self.s.get(f"{prefix}_{c}", d), d) for c, d in zip("rgb", default))

    def toggle(self, name) -> bool:
        default, key = TOGGLES[name]
        return bool(self.s.get(key, default))

    def track(self, name) -> int:
        default, key = TRACKS[name]
        try:
            return max(1, min(99, int(self.s.get(key, default))))
        except (TypeError, ValueError):
            return default

    @property
    def font_name(self) -> str:
        """The picked font's family."""
        return self.font_face[0]

    @property
    def font_face(self) -> Tuple[str, str]:
        """The picked font as (family, style) - what a Text+'s Font and Style are set to.
        An older Buddy kept one name with the style written in ("Arial Bold"); that's
        split by its style words."""
        from .font_utils import DEFAULT_FONT_NAME, split_font_name_and_style
        name = str(self.s.get("selected_font_name") or DEFAULT_FONT_NAME)
        style = self.s.get("selected_font_style")
        if isinstance(style, str) and style.strip():
            return name, style.strip()
        return split_font_name_and_style(name)

    @property
    def font_picked_by_name(self) -> bool:
        """Whether the font was kept as one name (an older Buddy's), not family + style."""
        return not self.s.get("selected_font_style")

    def scope(self, name: str) -> str:
        """A tab's "Apply to" choice (see SCOPES); name is one of SCOPE_KEYS."""
        value = self.s.get(SCOPE_KEYS[name])
        if value in SCOPES:
            return value
        if name == "style_scope":          # saved before the dropdown: two ticks
            if self.s.get("font_style_scope_specific_track"):
                return "track"
            if self.s.get("font_style_scope_playhead"):
                return "playhead"
        return "timeline"

    @property
    def style_scope(self) -> str:
        return self.scope("style_scope")

    @property
    def anim_preset(self) -> str:
        preset = self.s.get("anim_preset")
        return preset if preset in ANIM_PRESETS else ANIM_PRESETS[0]

    @property
    def anim_direction(self) -> str:
        direction = self.s.get("anim_slide_direction")
        return direction if direction in FusionAnimationEngine.SLIDE_DIRECTIONS else FusionAnimationEngine.SLIDE_DIRECTIONS[0]

    @property
    def anim_speed(self) -> str:
        speeds = FusionAnimationEngine.SPEEDS
        try:
            return speeds[max(0, min(len(speeds) - 1, int(self.s.get("anim_speed", len(speeds) - 1))))]
        except (TypeError, ValueError):
            return speeds[-1]

    @property
    def wbw_lines(self) -> str:
        value = str(self.s.get("word_by_word_lines", "auto"))
        return value if value in WBW_LINES else "auto"

    @property
    def wbw_scope(self) -> str:
        value = self.s.get("word_by_word_scope", "playhead")
        return value if value in WBW_SCOPES else "playhead"

    @property
    def layout_preset(self) -> str:
        preset = self.s.get("layout_preset")
        return preset if preset in LAYOUT_PRESETS else next(iter(LAYOUT_PRESETS))

    def overlay(self, name):
        spec = OVERLAY[name]
        if isinstance(spec, tuple):
            default, choices = spec
            value = self.s.get(f"canvas_{name}", default)
            return value if value in choices else default
        value = self.s.get(f"canvas_{name}", spec)
        if isinstance(spec, bool):
            return bool(value)
        if isinstance(spec, float):
            return max(0.0, min(1.0, _float(value, spec)))
        return value if from_hex(value) else spec

    # ------------------------------------------------------------ write --

    def set(self, name, value) -> bool:
        """Store one control's new value (validated). False if it isn't a known control
        or the value doesn't fit it."""
        s = self.s
        if name in SLIDERS:
            _label, lo, hi, _step, decimals, _default, key = SLIDERS[name]
            number = _float(value, None)
            if number is None:
                return False
            s[key] = round(max(lo, min(hi, number)), decimals)
        elif name in COLORS:
            rgb = from_hex(value)
            if rgb is None:
                return False
            prefix = COLORS[name][1]
            for c, v in zip("rgb", rgb):
                s[f"{prefix}_{c}"] = v
        elif name in TOGGLES:
            s[TOGGLES[name][1]] = bool(value)
        elif name in TRACKS:
            try:
                s[TRACKS[name][1]] = max(1, min(99, int(value)))
            except (TypeError, ValueError):
                return False
        elif name == "font_name":         # one name, the style written in ("Arial Bold")
            if not isinstance(value, str) or not value.strip():
                return False
            s["selected_font_name"] = value.strip()
            s.pop("selected_font_style", None)
        elif name == "font_face":         # (family, style), as Resolve names them
            if not isinstance(value, (list, tuple)) or len(value) != 2 \
                    or not all(isinstance(v, str) and v.strip() for v in value):
                return False
            s["selected_font_name"], s["selected_font_style"] = value[0].strip(), value[1].strip()
        elif name in SCOPE_KEYS:
            if value not in SCOPES:
                return False
            s[SCOPE_KEYS[name]] = value
            if name == "style_scope":        # kept in step, for an older Buddy on this PC
                s["font_style_scope_playhead"] = value == "playhead"
                s["font_style_scope_specific_track"] = value == "track"
        elif name == "anim_preset":
            if value not in ANIM_PRESETS:
                return False
            s["anim_preset"] = value
        elif name == "anim_direction":
            if value not in FusionAnimationEngine.SLIDE_DIRECTIONS:
                return False
            s["anim_slide_direction"] = value
        elif name == "anim_speed":
            if value not in FusionAnimationEngine.SPEEDS:
                return False
            s["anim_speed"] = FusionAnimationEngine.SPEEDS.index(value)
        elif name == "layout_preset":
            if value not in LAYOUT_PRESETS:
                return False
            s["layout_preset"] = value
        elif name == "wbw_lines":
            if str(value) not in WBW_LINES:
                return False
            s["word_by_word_lines"] = str(value)
        elif name == "wbw_scope":
            if value not in WBW_SCOPES:
                return False
            s["word_by_word_scope"] = value
        elif name in OVERLAY:
            spec = OVERLAY[name]
            if isinstance(spec, tuple):
                if value not in spec[1]:
                    return False
            elif isinstance(spec, bool):
                value = bool(value)
            elif isinstance(spec, float):
                number = _float(value, None)
                if number is None:
                    return False
                value = max(0.0, min(1.0, number))
            elif not from_hex(value):
                return False
            s[f"canvas_{name}"] = value
        else:
            return False
        s.save()
        return True

    # ------------------------------------------------------------- view --

    def view(self) -> dict:
        """Everything the page's controls show."""
        return {
            "sliders": {name: {"label": spec[0], "min": spec[1], "max": spec[2], "step": spec[3],
                               "decimals": spec[4], "default": spec[5], "value": self.slider(name)}
                        for name, spec in SLIDERS.items()},
            "colors": {name: to_hex(self.color(name)) for name in COLORS},
            "toggles": {name: self.toggle(name) for name in TOGGLES},
            "tracks": {name: self.track(name) for name in TRACKS},
            "font_name": self.font_name, "font_style": self.font_face[1],
            "style_scope": self.style_scope,
            "anim_scope": self.scope("anim_scope"),
            "layout_scope": self.scope("layout_scope"),
            "anim_presets": ANIM_PRESETS, "anim_preset": self.anim_preset,
            "anim_directions": list(FusionAnimationEngine.SLIDE_DIRECTIONS), "anim_direction": self.anim_direction,
            "anim_speeds": list(FusionAnimationEngine.SPEEDS), "anim_speed": self.anim_speed,
            "layout_presets": list(LAYOUT_PRESETS), "layout_preset": self.layout_preset,
            "wbw_lines": self.wbw_lines, "wbw_scope": self.wbw_scope,
            "looks": self.looks_view(),
        }

    # ------------------------------------------------------------ looks --

    def look(self) -> dict:
        """The Font Styling controls as they are now, as a saved look keeps them."""
        values = {name: self.slider(name) for name in LOOK_SLIDERS}
        values.update({name: to_hex(self.color(name)) for name in LOOK_COLORS})
        values.update({name: self.toggle(name) for name in LOOK_TOGGLES})
        return {"font": list(self.font_face), "values": values}

    def put_look(self, look: dict):
        """Puts a look's values on the controls (the ones it has - a ready-made look has no
        font, and a wide one no size). Turns Font on: a look always sets the text's own."""
        for name, value in (look.get("values") or {}).items():
            if name in LOOK_SLIDERS or name in LOOK_COLORS or name in LOOK_TOGGLES:
                self.set(name, value)
        font = look.get("font")
        if font:
            self.set("font_face", font)
        self.set("font_on", True)

    def _matches(self, look: dict) -> bool:
        now = self.look()
        for name, value in (look.get("values") or {}).items():
            if name in LOOK_SLIDERS:
                if abs(now["values"][name] - _float(value, 0.0)) > 1e-6:
                    return False
            elif name in LOOK_COLORS:
                if now["values"][name] != str(value).upper():
                    return False
            elif name in LOOK_TOGGLES and now["values"][name] != bool(value):
                return False
        font = look.get("font")
        return not font or [f.casefold() for f in font] == [f.casefold() for f in now["font"]]

    @staticmethod
    def built_in_look(kind: str, name: str):
        """A ready-made look as {"values", "bold"}, or None."""
        for look_name, values, bold in BUILT_IN_LOOKS.get(kind, ()):
            if look_name == name:
                return {"values": values, "bold": bold}
        return None

    def saved_looks(self) -> list:
        """[{"name", "tall", "look"}] - kept in Buddy's own settings, so every project has them."""
        out = []
        for entry in self.s.get("style_looks") or []:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str) and isinstance(entry.get("look"), dict):
                out.append({"name": entry["name"], "tall": bool(entry.get("tall")), "look": entry["look"]})
        return out

    def _saved(self, name: str):
        key = str(name or "").strip().casefold()
        return next((entry for entry in self.saved_looks() if entry["name"].casefold() == key), None)

    @property
    def current_look(self):
        """The look last picked or saved: ("saved" | "wide" | "tall", name), or None."""
        value = self.s.get("style_look")
        if isinstance(value, (list, tuple)) and len(value) == 2 and value[0] in ("saved", "wide", "tall"):
            return value[0], str(value[1])
        return None

    def pick_look(self, kind: str, name: str):
        """Puts a ready-made ("wide"/"tall") or saved look on the controls. The look as
        {"values", "bold"} (bold: make the font its family's boldest), or None if unknown."""
        if kind == "saved":
            entry = self._saved(name)
            if entry is None:
                return None
            look, name = dict(entry["look"], bold=False), entry["name"]
        else:
            look = self.built_in_look(kind, name)
            if look is None:
                return None
        self.put_look(look)
        self.s["style_look"] = [kind, name]
        self.s.save()
        return look

    def save_look(self, name: str, tall: bool) -> bool:
        """Keeps the controls as a saved look - over one of the same name, if there is one."""
        name = " ".join(str(name or "").split())[:MAX_LOOK_NAME]
        if not name:
            return False
        looks = [entry for entry in self.saved_looks()]
        entry = {"name": name, "tall": bool(tall), "look": self.look()}
        at = next((i for i, e in enumerate(looks) if e["name"].casefold() == name.casefold()), None)
        if at is None:
            if len(looks) >= MAX_LOOKS:
                return False
            looks.append(entry)
        else:
            looks[at] = entry
        self.s["style_looks"] = looks
        self.s["style_look"] = ["saved", name]
        self.s.save()
        return True

    def rename_look(self, old: str, new: str) -> bool:
        new = " ".join(str(new or "").split())[:MAX_LOOK_NAME]
        entry = self._saved(old)
        if not new or entry is None or (self._saved(new) not in (None, entry)):
            return False
        looks = self.saved_looks()
        for e in looks:
            if e["name"] == entry["name"]:
                e["name"] = new
        self.s["style_looks"] = looks
        if self.current_look == ("saved", entry["name"]):
            self.s["style_look"] = ["saved", new]
        self.s.save()
        return True

    def delete_look(self, name: str) -> bool:
        entry = self._saved(name)
        if entry is None:
            return False
        self.s["style_looks"] = [e for e in self.saved_looks() if e["name"] != entry["name"]]
        if self.current_look == ("saved", entry["name"]):
            self.s.pop("style_look", None)
        self.s.save()
        return True

    def looks_view(self) -> dict:
        current = self.current_look
        look = None
        if current:
            entry = self._saved(current[1]) if current[0] == "saved" else None
            look = entry["look"] if entry else self.built_in_look(*current)
        return {
            "wide": [{"name": n, "values": v, "bold": b} for n, v, b in BUILT_IN_LOOKS["wide"]],
            "tall": [{"name": n, "values": v, "bold": b} for n, v, b in BUILT_IN_LOOKS["tall"]],
            "saved": self.saved_looks(),
            "current": list(current) if look else None,
            "changed": bool(look) and not self._matches(look),
        }

    def overlay_view(self) -> dict:
        return {name: self.overlay(name) for name in OVERLAY}
