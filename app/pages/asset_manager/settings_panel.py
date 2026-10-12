#!/usr/bin/env python3
"""
Asset Manager's section of the shell's Settings window, as fields (see
core/settings_form.py). After a change the page redraws its list.
"""

from core import settings_form as sf
from core.media_search import model as search_model

BEHAVIOURS = ("include_folders_in_sort", "group_all_media_by_folder")


class AssetSettingsMixin:
    def settings_fields(self):
        return [
            sf.heading("Asset Manager"),
            sf.check("include_folders_in_sort", "Include folders when sorting by name or date added",
                     self.settings.get("include_folders_in_sort", True),
                     hint_text="On mixes folders in with individual items by the active sort column. Off always "
                               "lists folders first, sorted alphabetically, with only the individual items "
                               "following the active sort."),
            sf.check("group_all_media_by_folder", "Group All media by shared folder",
                     self.settings.get("group_all_media_by_folder", True),
                     hint_text="On collapses 2+ assets from the same folder into an expandable row on the All "
                               "media tab. Off always lists every asset flat, never merging files by folder. By "
                               "folder and Projects are unaffected."),
            sf.check("search_use_gpu", "Use the graphics card for search by description", search_model.use_gpu(),
                     hint_text="Several times faster than the processor, on a computer with a graphics card of its "
                               "own. Switch it off if Resolve needs all of the card's memory. Footage Search follows "
                               "this too."),
        ]

    def on_setting(self, key, value, ui):
        if key in BEHAVIOURS:
            self.settings[key] = bool(value)
            self.settings.save()
            self._push_list()
        elif key == "search_use_gpu":
            search_model.set_use_gpu(bool(value))
            self._push_describe()
