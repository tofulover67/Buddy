#!/usr/bin/env python3
"""
The full list of Buddy's tools, grouped and ordered the way they appear
in the nav rail.

A tool that isn't available yet is a placeholder (see
pages/placeholder.py), so the complete layout is still visible. Which ones
are real is never written down here - ToolPage.is_placeholder is the one
source of truth.

Adding a tool = writing its own pages/<tool_id>/page.py (a ToolPage
subclass, see pages/base.py) and swapping its placeholder() call below
for the real import - the category/order here don't need to change.

On Windows every tool loads for real. macOS note: a few tools call
Windows-only APIs (winreg, ctypes.windll, Windows font enumeration, etc.)
from some of their features. So on macOS a tool only loads for real once
it's been checked there - its mac=True below. The rest stay placeholders
until then. Enabling one = fixing whatever it does that's Windows-only,
then flipping it to mac=True.
"""

import sys

from pages.placeholder import make_placeholder_page
from pages.buddy_network.page import BuddyNetworkPage

IS_MAC = sys.platform == "darwin"


def _tool(tool_id, display_name, category, import_path, class_name, mac=False):
    """The real page class - except on macOS for a tool not yet checked
    there (mac=False), which gets a placeholder and isn't imported at all.
    See the module docstring above."""
    if IS_MAC and not mac:
        return make_placeholder_page(tool_id, display_name, category)
    import importlib

    module = importlib.import_module(import_path)
    return getattr(module, class_name)


# List of (category, page_cls), in nav order. NOTE: tests/test_network_client.py
# reads this file's source with ast rather than importing it (importing pulls
# in every real page), and asserts REGISTRY is a plain list literal ending in
# a bare ("", BuddyNetworkPage) tuple - keep this a literal list, not built up
# with comprehensions/concatenation, and keep that last entry as-is.
REGISTRY = [
    ("Ask", _tool("manual_chat", "Ask Buddy", "Ask", "pages.manual_chat.page", "ManualChatPage", mac=True)),

    ("Audit", _tool("audit", "Audit", "Audit", "pages.audit.page", "AuditPage", mac=True)),

    ("Setup", _tool("project_setup", "Project Setup", "Setup", "pages.project_setup.page", "ProjectSetupPage", mac=True)),

    ("Automation", _tool("automation", "Automation", "Automation", "pages.automation.page", "AutomationPage", mac=True)),

    ("Media & Assets", _tool("asset_manager", "Asset Manager", "Media & Assets", "pages.asset_manager.page", "AssetManagerPage", mac=True)),
    ("Media & Assets", _tool("footage_search", "Footage Search", "Media & Assets", "pages.footage_search.page", "FootageSearchPage")),
    ("Media & Assets", _tool("image_importer", "Image Importer", "Media & Assets", "pages.image_importer.page", "ImageImporterPage", mac=True)),
    ("Media & Assets", _tool("svg_importer", "SVG Importer", "Media & Assets", "pages.svg_importer.page", "SVGImporterPage", mac=True)),
    # Batch Clip Renamer and Media Relink, as its two tabs - both checked on a Mac.
    ("Media & Assets", _tool("media_manager", "Media Manager", "Media & Assets", "pages.media_manager.page", "MediaManagerPage", mac=True)),

    # Not yet checked on a Mac (its player and video surface are the new part).
    ("Editing Tools", _tool("dailies", "Dailies", "Editing Tools", "pages.dailies.page", "DailiesPage")),
    ("Editing Tools", _tool("text_animator", "Animation", "Editing Tools", "pages.text_animator.page", "AnimationPage", mac=True)),
    ("Editing Tools", _tool("transcribe", "Subtitles", "Editing Tools", "pages.transcribe.page", "TranscribePage", mac=True)),
    ("Editing Tools", _tool("audio_assistant", "Audio Assistant", "Editing Tools", "pages.audio_assistant.page", "AudioAssistantPage", mac=True)),
    ("Editing Tools", _tool("color_palette", "Color Palette Manager", "Editing Tools", "pages.color_palette.page", "ColorPalettePage", mac=True)),

    ("Generative", _tool("generative", "Generative", "Generative", "pages.generative.page", "GenerativePage", mac=True)),

    # Stills Exporter and YouTube Chapters, as its two tabs - both checked on a Mac.
    ("Export & Delivery", _tool("marker_manager", "Marker Manager", "Export & Delivery", "pages.marker_manager.page", "MarkerManagerPage", mac=True)),

    ("Business", _tool("time_tracker", "Time Tracker", "Business", "pages.time_tracker.page", "TimeTrackerPage", mac=True)),

    # Not yet run on a Mac (Qt WebEngine's browsing, the audio ducking and
    # the encrypted sign-ins are Windows-first): a placeholder there for now.
    ("Web", _tool("web", "Web", "Web", "pages.web.page", "WebBrowserPage", mac=False)),

    ("Command Center", _tool("command_center", "Command Center", "Command Center", "pages.command_center.page", "CommandCenterPage", mac=True)),

    ("Essentials", _tool("essentials", "Essentials", "Essentials", "pages.essentials.page", "EssentialsPage", mac=True)),

    ("Games", _tool("games", "Games", "Games", "pages.games.page", "GamesPage", mac=True)),
    # The pet on the desktop (pages/pets/) and the achievements (pages/achievements/)
    # aren't tabs: they're pages in Settings, built by the shell (its `extras`).

    # "" = a plain line above it instead of a heading.
    ("", BuddyNetworkPage),
]
