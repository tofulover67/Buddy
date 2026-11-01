#!/usr/bin/env python3
"""
More tools - the button at the foot of the sidebar while any tool is hidden
from it (a new install starts with only a few showing: core/onboarding.py).
Each hidden tool with a line on what it does and an Add button that puts it
back in the rail where it was; Open then goes to it. A web window
(app/web/shell/moretools/). What's added is saved at once, so there's
nothing to cancel; Organize sidebar is a button away for the rest.

Protocol:
    to the view    tools
    from the view  add, open, organize, close
"""

import os
import sys

from core import nav_layout
from core.web_page import WEB_COMMON_DIR, WebDialog

# One line on each tool, in everyday words - what someone deciding whether
# to add it needs to know.
BLURBS = {
    "manual_chat": "Ask anything about DaVinci Resolve. It can read your project to help, and changes it only if "
                   "you say yes.",
    "audit": "Checks the timeline before delivery – missing media, gaps, flash frames, loudness – with one-click "
             "fixes.",
    "project_setup": "Bins from a list, folders imported as they are, multicam sync and proxy media.",
    "automation": "Lists of steps from Buddy's tools that run one after another while you do something else.",
    "asset_manager": "A library of the images, audio and video you reuse, searchable by what's in them.",
    "footage_search": "Find a shot in the Media Pool by describing it, and get B-roll ideas for each line of a "
                      "transcript.",
    "image_importer": "Puts images into a bin straight from the clipboard: a copied image, files or an image "
                      "link.",
    "svg_importer": "Brings SVG artwork and Lottie animations into Fusion as nodes.",
    "media_manager": "Renames a bin's clips in one go, and relinks offline media.",
    "dailies": "Review the Media Pool's footage clip after clip, with named source tapes, notes and metadata.",
    "text_animator": "Motion presets, animated titles, zooms and transitions for the clips you select.",
    "transcribe": "Subtitles from the timeline's dialogue, and translations of them, made on this computer.",
    "audio_assistant": "An easier way to set a clip's volume, fades and loudness, clean up voices and fit music "
                       "to length.",
    "daw": "A small music studio for making music in the key and tempo of the song on your timeline.",
    "color_palette": "Colour palettes, colours picked from pictures, and contrast checks.",
    "generative": "Takes things out of stills, or the whole background, on this computer – nothing is uploaded.",
    "marker_manager": "Grabs stills at your markers and exports them, and makes a YouTube chapter list from the "
                      "markers.",
    "time_tracker": "Tracks the time you spend on each project by itself, with reports and invoices.",
    "web": "A light web browser inside Buddy that blocks ads. What you download drags straight into Resolve.",
    "command_center": "System-wide hot keys for markers, clip colours, copying the timecode and more.",
    "essentials": "A calculator that also does timecode, data rates and aspect ratios, with a timer, notes and a "
                  "world clock.",
    "games": "Pong, Snake, Solitaire, Chess and more, for a break.",
    "buddy_network": "An anonymous chat with other people who use Buddy.",
}
if sys.platform == "darwin":
    # No system-wide hot keys on a Mac yet: Command Center has its Run buttons.
    BLURBS["command_center"] = "One-click buttons for markers, clip colours, copying the timecode and more."


class MoreToolsDialog(WebDialog):
    """`shell` is the ShellWindow. After exec(): open_tool (a tool to go to)
    or organize (Organize sidebar was asked for)."""

    web_dir = os.path.join(WEB_COMMON_DIR, "shell", "moretools")

    def __init__(self, shell):
        self.shell = shell
        # The tools hidden as it opens; one added stays in the list, marked.
        self.ids = [t for t in nav_layout.hidden_tool_ids(shell.nav_layout) if t in shell.pages]
        self.open_tool = None
        self.organize = False
        super().__init__(shell, shell, "More tools", (560, 620))

    def web_ready(self):
        self._push()

    def _push(self):
        visible = set(nav_layout.visible_tool_ids(self.shell.nav_layout))
        rows = []
        for tool_id in self.ids:
            page = self.shell.pages[tool_id]
            rows.append({"id": tool_id, "name": page.display_name, "blurb": BLURBS.get(tool_id, ""),
                         "added": tool_id in visible, "placeholder": bool(page.is_placeholder)})
        self.emit("tools", {"rows": rows})

    def on_add(self, payload):
        tool_id = (payload or {}).get("id")
        if tool_id in self.ids and not self.shell.pages[tool_id].is_placeholder:
            self.shell.add_to_rail(tool_id)
            self._push()

    def on_open(self, payload):
        tool_id = (payload or {}).get("id")
        if tool_id in nav_layout.visible_tool_ids(self.shell.nav_layout):
            self.open_tool = tool_id
            self.accept()

    def on_organize(self, _payload=None):
        self.organize = True
        self.accept()

    def on_close(self, _payload=None):
        self.accept()
