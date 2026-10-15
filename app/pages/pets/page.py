#!/usr/bin/env python3
"""
Pets - a small animal that lives on the desktop, outside Buddy's window
(pet_window.py), walking along the bottom of the screen, sitting, dozing
off when nothing happens, typing along while a tool works, and cheering
when an achievement is unlocked (core/achievements.py). Hovering it shows
a pill with Ask Buddy and Open Buddy; a click pats it and opens a speech
bubble with a field that asks Ask Buddy; a right-click offers Pets,
Achievements and Hide; dragged, it's picked up and dangles, and let go
it's thrown - it bounces off the screen's edges and lands on the bottom
(physics.py). With Physics off, a drag just moves it (the system's own
move), anywhere, and it stays at that height.

Not a tab: its page is in Settings (General > Pets - core/settings_dialog.py
takes it from the shell's `extras`): the pet on the desktop or not, which
one and its name, its skin, its size, whether it walks, whether it can be
picked up and thrown, when it falls asleep, whether a click opens Ask
Buddy, and how far it has come. The
species (behaviour.SPECIES): the blob from the start, the rest earned with
achievements; the gallery draws them with web/sprites.js, which the pet's
own window draws with too. Each has a few skins (skins.py), all free; the
page ends with a Donate button that opens Buddy's website.

What it does moment to moment is behaviour.py (no Qt, tested). Python
keeps the settings and where the pet was left.
"""

import os
import sys

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QDesktopServices

from core import atomic_io, settings_form as sf, tool_dirs
from core.settings_dialog import refresh_open

from . import skins as skins_mod
from .behaviour import CARRIED, DEFAULT_SIZE, DEFAULT_SPECIES, SIZES, SPECIES, clean_name, unlocked_species

SLEEP_CHOICES = (0, 5, 15, 30, 60)      # minutes; 0 = never
DEFAULTS = {"species": DEFAULT_SPECIES, "name": "", "size": DEFAULT_SIZE, "shown": False, "walk": True,
            "physics": True, "sleep_minutes": 15, "ask": True, "pos": None, "skins": {}, "colours": {}}
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# Buddy's donation page, on its website: the Pets page's "Donate to support"
# button opens it. With none set, there's no button.
DONATE_URL = ""


def clean_pos(value):
    """Where the pet stands: {"x", "y"} in screen pixels, or None."""
    if not isinstance(value, dict):
        return None
    x, y = value.get("x"), value.get("y")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or abs(v) > 100_000 for v in (x, y)):
        return None
    return {"x": int(x), "y": int(y)}


def clean_settings(raw) -> dict:
    """The saved settings made safe: anything odd is its default."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    out["species"] = raw.get("species") if raw.get("species") in SPECIES else DEFAULT_SPECIES
    out["name"] = clean_name(raw.get("name"))
    out["size"] = raw.get("size") if raw.get("size") in SIZES else DEFAULT_SIZE
    for key in ("shown", "walk", "physics", "ask"):
        out[key] = bool(raw[key]) if isinstance(raw.get(key), bool) else DEFAULTS[key]
    minutes = raw.get("sleep_minutes")
    out["sleep_minutes"] = minutes if minutes in SLEEP_CHOICES and not isinstance(minutes, bool) else 15
    out["pos"] = clean_pos(raw.get("pos"))
    out["skins"] = skins_mod.clean_skins(raw.get("skins"))
    out["colours"] = skins_mod.clean_colours(raw.get("colours"))
    return out


class Pets(QObject):
    """The shell's pet (host: the shell - tool_settings, achievements,
    record_stat, bring_to_front, open_settings, working_tools, pages)."""

    tool_id = "pets"
    display_name = "Pets"
    is_placeholder = False

    def __init__(self, host):
        super().__init__(host if isinstance(host, QObject) else None)
        self.host = host
        self.settings = host.tool_settings(self.tool_id, dict(DEFAULTS))
        self.values = clean_settings({k: self.settings.get(k) for k in DEFAULTS})
        self._forget_licence()
        self.window = None
        ach = self._achievements()
        if ach is not None:
            ach.on_change.append(self._achievements_changed)
        if self.values["shown"] and self._species_allowed(self.values["species"]):
            self._open_window()

    def _forget_licence(self):
        """The skins were once unlocked with a licence key kept in licence.json:
        they're free now, and a file from then is tidied away."""
        path = tool_dirs.file_path(self.tool_id, "licence.json")
        for stale in (path, atomic_io.backup_path(path)):
            try:
                os.remove(stale)
            except OSError:
                pass

    # --------------------------------------------------------------- state --

    def _achievements(self):
        return getattr(self.host, "achievements", None)

    def _count(self) -> int:
        ach = self._achievements()
        return len(ach.unlocked) if ach is not None else 0

    def _species_allowed(self, species) -> bool:
        return species in unlocked_species(self._count())

    def skin_of(self, species) -> str:
        """The skin this species wears: what was chosen, if it may still be worn."""
        skin = self.values["skins"].get(species, skins_mod.CLASSIC)
        return skin if skins_mod.is_skin(species, skin) else skins_mod.CLASSIC

    def _window_values(self) -> dict:
        """The settings as the window takes them: skins as worn right now."""
        return dict(self.values, skins={s: self.skin_of(s) for s in SPECIES},
                    colours={s: self.colour_of(s) for s in SPECIES})

    def _save(self):
        for key, value in self.values.items():
            self.settings[key] = value
        self.settings.save()

    def achieve(self, stat, amount=1):
        record = getattr(self.host, "record_stat", None)
        if record is not None:
            try:
                record(stat, amount)
            except Exception:  # noqa: BLE001 - a tally never breaks the pet
                pass

    def _achievements_changed(self):
        """More achievements: a species may have been earned; an open Settings redraws."""
        if self.window is not None:
            self.window.behaviour.activity()
        refresh_open(self.tool_id)

    def set_value(self, key, value) -> bool:
        """One setting, checked: True if it changed."""
        if key not in ("species", "name", "size", "walk", "physics", "sleep_minutes", "ask", "skin", "colour"):
            return False
        if key == "skin":
            return self._set_skin(value)
        if key == "colour":
            return self._set_colour(value)
        if key == "species":
            if value not in SPECIES or not self._species_allowed(value):
                return False
        elif key == "name":
            value = clean_name(value)
        elif key == "size":
            if value not in SIZES:
                return False
        elif key == "sleep_minutes":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return False
            if value not in SLEEP_CHOICES:
                return False
        else:
            value = bool(value)
        if self.values[key] == value:
            return False
        self.values[key] = value
        self._save()
        self._apply_to_window()
        return True

    def _set_skin(self, skin) -> bool:
        """The chosen pet's skin - one it may wear."""
        species = self.values["species"]
        if not isinstance(skin, str) or not skins_mod.is_skin(species, skin):
            return False
        if self.values["skins"].get(species, skins_mod.CLASSIC) == skin:
            return False
        skins = dict(self.values["skins"])
        if skin == skins_mod.CLASSIC:
            skins.pop(species, None)
        else:
            skins[species] = skin
        self.values["skins"] = skins
        self._save()
        self._apply_to_window()
        return True

    def colour_of(self, species) -> str:
        """The colour of your own this species wears when its skin is Your tint."""
        return self.values["colours"].get(species, skins_mod.DEFAULT_COLOUR)

    def _set_colour(self, colour) -> bool:
        """The chosen pet's colour of its own - worn at once."""
        species = self.values["species"]
        colours = skins_mod.clean_colours({species: colour})
        if not colours:
            return False
        changed = self.values["colours"].get(species) != colours[species]
        self.values["colours"] = dict(self.values["colours"], **colours)
        if self.values["skins"].get(species) != skins_mod.CUSTOM:
            self.values["skins"] = dict(self.values["skins"], **{species: skins_mod.CUSTOM})
            changed = True
        if changed:
            self._save()
            self._apply_to_window()
        return changed

    def _apply_to_window(self):
        if self.window is not None:
            self.window.apply(self._window_values())

    def show(self, on: bool):
        """Put the pet on the desktop, or take it away."""
        on = bool(on)
        if on != self.values["shown"]:
            self.values["shown"] = on
            self._save()
        if on and self.window is None:
            self._open_window()
        elif not on:
            self._close_window()

    # -------------------------------------------------------------- window --

    def _open_window(self):
        if self.window is not None:
            return
        from .pet_window import PetWindow     # Qt window: only when one is wanted
        self.window = PetWindow(self, self._window_values())
        self.window.show()

    def _close_window(self):
        window, self.window = self.window, None
        if window is not None:
            window.close()
            window.deleteLater()

    def remember_pos(self, pos):
        """Where the window left the pet (pet_window.py)."""
        self.values["pos"] = clean_pos(pos)
        self._save()

    def hide_pet(self):
        """The window's own Hide (its menu)."""
        self.show(False)
        refresh_open(self.tool_id)

    def celebrate(self, unlocked):
        """Achievements just unlocked (the shell says): the pet cheers and names the first."""
        if self.window is not None and unlocked:
            self.window.celebrate(unlocked[0])

    def ask_buddy(self, text):
        """A question typed into the pet's bubble, to Ask Buddy - in front."""
        text = " ".join(str(text or "").split())[:2000]
        if not text:
            return
        self.host.bring_to_front()
        self.host.switch_tool("manual_chat")
        chat = getattr(self.host, "pages", {}).get("manual_chat")
        if chat is not None and hasattr(chat, "on_send") and not getattr(chat, "is_placeholder", False):
            chat.on_send({"text": text})

    def working(self) -> bool:
        """A tool is at work (the pet types along)."""
        tools = getattr(self.host, "working_tools", None)
        try:
            return bool(tools()) if tools is not None else False
        except Exception:  # noqa: BLE001
            return False

    def on_app_quitting(self):
        self._close_window()

    # -------------------------------------------------------------- donate --

    @staticmethod
    def _donate_fields():
        """Buddy is free, skins and all; the page ends with a way to say thanks."""
        fields = [sf.heading("Support Buddy"),
                  sf.hint("Buddy is free - every tool, every pet, every skin - and its source is public. If it "
                          "earns its keep, a donation helps keep it going.")]
        if DONATE_URL:
            fields.append(sf.buttons(("Donate to support…", "donate",
                                      {"tooltip": "Opens Buddy's donation page in your browser.", "kind": "accent"})))
        return fields

    def settings_pages(self):
        ach = self._achievements()
        summary = ach.summary() if ach is not None else {"unlocked": 0, "total": 0, "points": 0, "level": 1}
        unlocked = unlocked_species(self._count())
        gallery = [{"id": s, "name": name, "need": need, "unlocked": s in unlocked, "chosen": s == self.values["species"],
                    "skin": self.skin_of(s), "colour": self.colour_of(s)}
                   for s, (name, need) in SPECIES.items()]
        v = self.values
        species = v["species"]
        worn = self.skin_of(species)
        skins = [{"id": sid, "name": name, "chosen": sid == worn} for sid, name in skins_mod.skins_for(species)]
        # The chosen one is drawn doing what the pet on the desktop is doing -
        # standing, while it's in the hand or the air: the page wouldn't see it land.
        doing = self.window.behaviour.state if self.window is not None else "idle"
        doing = "idle" if doing in CARRIED else doing
        fields = [
            sf.hint("A small companion that lives on your desktop, outside Buddy's window. It walks along the "
                    "bottom of the screen, dozes off when nothing happens, types along while a tool works, and "
                    "cheers when you unlock an achievement. Hover it for Ask Buddy and Open Buddy; click it to "
                    "pat it and ask Buddy something; right-click it for more; drag it to pick it up, and let go "
                    "to throw it."),
            sf.check("shown", "Show the pet on the desktop", v["shown"],
                     hint_text=("It stays when Buddy is in the menu bar." if sys.platform == "darwin" else
                                "It stays when Buddy is in the tray.")),
            sf.heading("Your pet"),
            sf.pets("species", gallery, state=doing),
            sf.text("name", "Name", v["name"], placeholder="Give it a name"),
            sf.select("size", "Size", v["size"], [("small", "Small"), ("medium", "Medium"), ("large", "Large")]),
            sf.check("walk", "It walks about", v["walk"]),
            sf.check("physics", "Physics: it can be picked up and thrown", v["physics"],
                     hint_text="With it off, a drag puts it anywhere, even high up the screen, and it stays at "
                               "that height."),
            sf.select("sleep_minutes", "Falls asleep after", v["sleep_minutes"],
                      [(0, "Never"), (5, "5 minutes"), (15, "15 minutes"), (30, "30 minutes"), (60, "1 hour")],
                      tooltip="With nothing happening in Buddy for this long, the pet dozes off. A click, or "
                              "any tool doing something, wakes it."),
            sf.check("ask", "A click on the pet opens a field that asks Ask Buddy", v["ask"],
                     hint_text="With it off, a pat just gets a word back."),
            sf.line(),
            sf.heading("Skins"),
            sf.skins("skin", species, skins, colour=self.colour_of(species)),
            sf.color("colour", "Your tint", self.colour_of(species)),
            sf.hint("Picking a colour puts it on the chosen pet: its main colour becomes it, with the darker "
                    "parts a darker shade of it.", indent=True),
            sf.line(),
            *self._donate_fields(),
            sf.line(),
            sf.heading("Growing up"),
            sf.info("Level", str(summary["level"])),
            sf.info("Points", f"{summary['points']:,}"),
            sf.info("Achievements", f"{summary['unlocked']} of {summary['total']}"),
            sf.link("Achievements", "achievements", hint_text="New pets are earned with achievements."),
        ]
        return [{"id": "pets", "group": "general", "title": "Pets", "subtitle": "A companion on the desktop",
                 "fields": fields}]

    def on_setting(self, key, value, ui):
        if key == "shown":
            self.show(bool(value))
        else:
            self.set_value(key, value)

    def on_settings_action(self, action, ui):
        if action == "donate" and DONATE_URL:
            QDesktopServices.openUrl(QUrl(DONATE_URL))
