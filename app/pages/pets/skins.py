#!/usr/bin/env python3
"""
The pets' skins - a few looks for each species, drawn by web/sprites.js
(which holds the colours and the hats; this is the list and the rules).
Every pet has Classic, a colour of its own, a colour of your own and the
accessories every pet can wear - all free. No Qt; tested.
"""

from __future__ import annotations

import re

from .behaviour import SPECIES

CLASSIC = "classic"
# The accessories every pet can wear, in the order the gallery shows them.
ACCESSORIES = [("shades", "Shades"), ("tophat", "Top hat"), ("bow", "Bow tie"), ("crown", "Crown")]
# Each species' own colour (id, name) - sprites.js says what it recolours.
COLOURS = {
    "blob": ("mint", "Mint"),
    "robot": ("gold", "Gold"),
    "shroomy": ("bluecap", "Blue cap"),
    "poof": ("storm", "Storm cloud"),
    "blaze": ("bluefire", "Blue flame"),
    "spike": ("autumn", "Autumn"),
    "cheddar": ("blue", "Blue cheese"),
    "crumb": ("choc", "Double chocolate"),
    "nutty": ("golden", "Golden"),
    "pebble": ("mossy", "Mossy"),
    "snappy": ("bluecrab", "Blue crab"),
}


CUSTOM = "custom"           # a colour of the person's own (clean_colours), tinting the pet
DEFAULT_COLOUR = "#7c5cff"
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def skins_for(species) -> list:
    """[(id, name)] for a species: Classic first, then its colour, a
    colour of your own, then the accessories."""
    out = [(CLASSIC, "Classic")]
    colour = COLOURS.get(species)
    if colour:
        out.append(colour)
    out.append((CUSTOM, "Your colour"))
    out.extend(ACCESSORIES)
    return out


def clean_colours(raw) -> dict:
    """The saved colour of your own per species: {species: "#rrggbb"}."""
    raw = raw if isinstance(raw, dict) else {}
    return {s: c.upper() for s, c in raw.items() if s in SPECIES and isinstance(c, str) and _HEX.match(c)}


def is_skin(species, skin) -> bool:
    """Whether this is one of the species' skins (the robot's Gold isn't the blob's)."""
    return any(sid == skin for sid, _name in skins_for(species))


def clean_skins(raw) -> dict:
    """The saved choice of skin per species, made safe: {species: skin id}
    with only real pairs, Classic left out (it's the default)."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for species, skin in raw.items():
        if species in SPECIES and isinstance(skin, str) and skin != CLASSIC and is_skin(species, skin):
            out[species] = skin
    return out
