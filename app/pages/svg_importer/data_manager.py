#!/usr/bin/env python3
"""
Settings persistence for SVG to Fusion.

Deliberately uses the EXACT same settings file path as the standalone SVG
to Fusion app (once `~/.figma_fusion_importer_settings.json`, now
`~/.buddy/tools/svg_importer/settings.json` - core/tool_dirs.py) so an existing
installation's saved preferences (accent/background color, import options)
carry over automatically - no migration step, no reset to defaults.
"""

import json
import os

from core import tool_dirs

SETTINGS_PATH = tool_dirs.file_path("svg_importer")    # ~/.buddy/tools/svg_importer/settings.json (core/tool_dirs.py)

DEFAULT_SETTINGS = {
    "theme_preset": "Default",
    "accent": "#D0BCFF",
    "background": "#141218",
    "svg_scale_mode": "Native (1:1 pixels)",
    "paste_orientation": "Horizontal",
    "consolidate_colors": False,
    "lottie_scale_mode": "Native (1:1 pixels)",
    "lottie_paste_orientation": "Horizontal",
    "stay_on_top": True,
}


class DataManager:
    def __init__(self):
        self.settings = dict(DEFAULT_SETTINGS)
        self._load_settings()

    def _load_settings(self):
        if not os.path.exists(SETTINGS_PATH):
            return
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            return

        # A settings file with no "theme_preset" key at all comes from the
        # standalone app, where the user picked their own custom
        # accent/background - "Custom" is what keeps those exact colors
        # active instead of silently snapping to the "Default" preset.
        if "theme_preset" not in saved:
            saved["theme_preset"] = "Custom"

        # Keys this version doesn't use (the standalone's transparency/scale
        # sliders) are simply ignored here rather than raising on them.
        self.settings.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS})

    def save_settings(self):
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except Exception:
            pass
