"""Looks - a title's styling, as data. A look pairs four role styles (normal, key, accent,
small): the pairing is what gives a stacked title its hierarchy. Every length in a style
is a share of the letter size, so a look draws the same at any resolution.

Look-level keys the composer reads:
  mode      "fit": every line widened to the block width, then clamped between minSize
            and maxSize. "ratio": one body size for every line (the narrowest fit) and
            the roles' scale on top - ragged lines, for serif/script accent looks.
  caps      the text in capitals (a role can say otherwise)
  lines     (fewest, most) lines it looks right with; pref - the count it prefers
  maxWords  the most words on one line
  arrange   center | left | right | stagger | cascade
  bw        the block's width, as a share of the safe zone's
  fill      how much of the safe zone the block should cover (area share)
  gap       the space between lines, as a share of the bigger line's size
  maxSize / minSize   letter size limits, as shares of the frame's height
  place     where the block sits: tl tc tr ml mc mr bl bc br
  accent    "auto" gives one strong word the accent role; "none" never does
  stack     words beside the key (two or more on one side) stand in rows as tall as
            the key, rather than small on its baseline
  inkLines  lines spaced by their letters' ink (lower case: x-height, ascenders and
            descenders), not the face's ascent and descent - tighter lower-case stacks

Role keys: font (family, style), scale, caps, rot (degrees), tuck (an accent overlapping
the line below by that share of its height), and the drawing ones - fill, gradient,
strokes, shadows, glows, bg, copies - which the composer ignores (copies only for their
reach). A copies group with "anim": "spread" fans out from behind its word as it comes
in, and folds back as it goes (timing.py).
"""

import copy
import re


DEFAULTS = {
    "mode": "fit", "caps": True, "lines": (1, 4), "pref": 3, "maxWords": 4, "arrange": "center",
    "bw": 0.9, "fill": 0.32, "gap": 0.14, "maxSize": 0.2, "minSize": 0.035, "place": "mc",
    "accent": "none",
}

_OUTLINE = [{"colour": "#000000", "w": 0.08, "pos": "outer"}]
_SOFT_SHADOW = [{"colour": "rgba(0,0,0,.55)", "angle": 180, "dist": 0.05, "blur": 0.12}]

HORMOZI = {
    "id": "hormozi", "label": "Hormozi stack", "mode": "fit", "caps": True, "lines": (2, 4), "pref": 3,
    "accent": "auto",
    "roles": {
        "normal": {"font": ("Montserrat", "Black"), "fill": "#ffffff", "strokes": _OUTLINE},
        "key":    {"font": ("Montserrat", "Black"), "fill": "#FFD93D", "strokes": _OUTLINE},
        "accent": {"font": ("Montserrat", "Black"), "fill": "#39FF14", "strokes": _OUTLINE},
        "small":  {"font": ("Montserrat", "ExtraBold"), "fill": "#ffffff", "scale": 0.55, "strokes": _OUTLINE},
    },
}

SCRIPT = {
    "id": "script", "label": "Script + gradient", "mode": "fit", "caps": True, "lines": (2, 4), "pref": 3,
    "accent": "auto",
    "roles": {
        "normal": {"font": ("Montserrat", "Black"), "fill": "#ffffff", "shadows": _SOFT_SHADOW},
        "key":    {"font": ("Montserrat", "Black"), "fill": "#ffffff", "shadows": _SOFT_SHADOW},
        "accent": {"font": ("Kaushan Script", "Regular"), "caps": False, "rot": -7, "tuck": 0.32, "scale": 1.05,
                   "gradient": {"angle": 90, "stops": [(0, "#ff5fa2"), (1, "#ff9a3c")]}},
        "small":  {"font": ("Montserrat", "ExtraBold"), "fill": "#e9e7f5", "scale": 0.5},
    },
}

# From the person's reference images (2026-10-05).

CLEAN_OUTLINE = {   # white letters, a thin coloured outline
    "id": "clean_outline", "label": "Clean outline", "mode": "fit", "caps": True, "lines": (2, 3), "pref": 2,
    "anim": "fade",
    "roles": {
        "normal": {"font": ("Montserrat", "Black"), "fill": "#ffffff",
                   "strokes": [{"colour": "#3f63b5", "w": 0.035, "pos": "outer"}]},
        "key": {"font": ("Montserrat", "Black"), "fill": "#ffffff",
                "strokes": [{"colour": "#3f63b5", "w": 0.035, "pos": "outer"}]},
    },
}

OUTLINE_FILL = {    # hollow words around a solid key
    "id": "outline_fill", "label": "Outline + fill", "mode": "fit", "caps": True, "lines": (2, 3), "pref": 2,
    "anim": "slide",
    "roles": {
        "normal": {"font": ("Montserrat", "Black"), "fill": "none",
                   "strokes": [{"colour": "#ffffff", "w": 0.045, "pos": "center"}]},
        "key": {"font": ("Montserrat", "Black"), "fill": "#ffffff"},
        "small": {"font": ("Montserrat", "Black"), "fill": "none", "scale": 0.55,
                  "strokes": [{"colour": "#ffffff", "w": 0.05, "pos": "center"}]},
    },
}

ECHO = {            # the key line echoed above and below in fading outlines
    "id": "echo", "label": "Echo", "mode": "fit", "caps": True, "lines": (1, 3), "pref": 2, "gap": 0.2, "keyAlone": True,
    "anim": "fade", "bw": 0.75,
    "roles": {
        "normal": {"font": ("Montserrat", "Bold"), "fill": "#e9e9e9", "scale": 0.6},
        "key": {"font": ("Montserrat", "Bold"), "fill": "#ededed",
                "copies": [{"n": 2, "step": (0, -0.58), "both": True, "fill": None,
                            "stroke": {"colour": "#ffffff", "w": 0.022}, "alpha": 0.7, "fade": 0.55,
                            "anim": "spread"}]},
    },
}

NEON_EXTRUDE = {    # condensed letters stacked in outline layers, glowing
    "id": "neon_extrude", "label": "Neon extrude", "mode": "fit", "caps": True, "lines": (2, 3), "pref": 2,
    "anim": "blur",
    "roles": {
        "normal": {"font": ("Anton", "Regular"), "fill": "#141414",
                   "strokes": [{"colour": "#ffffff", "w": 0.03, "pos": "outer"}],
                   "glows": [{"colour": "#ffffff", "blur": 0.25, "strength": 0.35}],
                   "copies": [{"n": 8, "step": (-0.024, 0.034), "fill": "#141414",
                               "stroke": {"colour": "#ffffff", "w": 0.028}, "alpha": 1.0, "fade": 0.86,
                               "anim": "spread", "travel": 8 / 30, "lag": 1 / 60}]},
        "key": {"font": ("Anton", "Regular"), "fill": "#141414",
                "strokes": [{"colour": "#ffffff", "w": 0.03, "pos": "outer"}],
                "glows": [{"colour": "#ffffff", "blur": 0.25, "strength": 0.35}],
                "copies": [{"n": 8, "step": (-0.024, 0.034), "fill": "#141414",
                            "stroke": {"colour": "#ffffff", "w": 0.028}, "alpha": 1.0, "fade": 0.86,
                            "anim": "spread", "travel": 8 / 30, "lag": 1 / 60}]},
    },
}

TITLE_BAR = {       # a big coloured title over a small, wide-spaced caption on a black bar
    "id": "title_bar", "label": "Title + bar", "mode": "fit", "caps": True, "lines": (2, 3), "pref": 2,
    "others": "small", "arrange": "left", "anim": "slide", "gap": 0.12,
    "roles": {
        "key": {"font": ("Montserrat", "Bold"), "fill": "#f6e95c"},
        "accent": {"font": ("Montserrat", "Bold"), "fill": "#f6e95c"},
        "normal": {"font": ("Montserrat", "Bold"), "fill": "#f6e95c"},
        "small": {"font": ("Montserrat", "Medium"), "fill": "#ffffff", "scale": 0.26, "track": 0.25,
                  "stretch": True, "bar": {"colour": "#000000", "padX": 0.45, "padY": 0.32}},
    },
}

LONG_SHADOW = {     # rounded heavy letters casting a long flat shadow
    "id": "long_shadow", "label": "Long shadow", "mode": "fit", "caps": True, "lines": (1, 3), "pref": 2,
    "anim": "slide",
    "roles": {
        "normal": {"font": ("Poppins", "ExtraBold"), "fill": "#ffffff",
                   "copies": [{"n": 16, "step": (0.016, 0.016), "fill": "#7c2232", "alpha": 1.0, "fade": 0.97,
                               "anim": "spread", "travel": 8 / 30, "lag": 1 / 120,
                               "pulseDim": 0.0}]},
        "key": {"font": ("Poppins", "ExtraBold"), "fill": "#ffffff",
                "copies": [{"n": 16, "step": (0.016, 0.016), "fill": "#7c2232", "alpha": 1.0, "fade": 0.97,
                            "anim": "spread", "travel": 8 / 30, "lag": 1 / 120,
                            "pulseDim": 0.0}]},
    },
}

POP_3D = {          # a hard offset shadow - letters lifted off the frame
    "id": "pop_3d", "label": "3D pop", "mode": "fit", "caps": True, "lines": (2, 3), "pref": 2,
    "anim": "slam",
    "roles": {
        "normal": {"font": ("Poppins", "ExtraBold"), "fill": "#ffffff",
                   "shadows": [{"colour": "#4b3fb0", "angle": 135, "dist": 0.075, "blur": 0.0}]},
        "key": {"font": ("Poppins", "ExtraBold"), "fill": "#ffe14d",
                "shadows": [{"colour": "#4b3fb0", "angle": 135, "dist": 0.075, "blur": 0.0}]},
    },
}

MIXED = {           # lowercase, a big key with the words beside it stacked, one word in colour
    "id": "mixed", "label": "Mixed sizes", "mode": "ratio", "caps": False, "lines": (2, 3), "pref": 2,
    "accent": "auto", "anim": "pop", "gap": 0.08, "stack": True, "inkLines": True,
    "roles": {
        "normal": {"font": ("Poppins", "Bold"), "fill": "#ffffff"},
        "key": {"font": ("Poppins", "ExtraBold"), "fill": "#ffffff", "scale": 2.4},
        "accent": {"font": ("Poppins", "Bold"), "fill": "#2ee6a6"},
        "small": {"font": ("Poppins", "Bold"), "fill": "#ffffff", "scale": 0.8},
    },
}

# More looks (2026-10-05): the person asked for lots of variety to choose from.

MB, MX, MD, MM = ("Montserrat", "Black"), ("Montserrat", "ExtraBold"), ("Montserrat", "Bold"), ("Montserrat", "Medium")
PB, PX = ("Poppins", "Bold"), ("Poppins", "ExtraBold")
_DROP = [{"colour": "rgba(0,0,0,.5)", "angle": 180, "dist": 0.04, "blur": 0.1}]


def _both(style, **key):
    """normal and key alike, the key with key's changes."""
    return {"normal": style, "key": {**style, **key}}


RED_BOX = {         # white captions, the key on a red box
    "id": "red_box", "label": "Red box", "lines": (2, 3), "pref": 2, "anim": "slam", "gap": 0.16,
    "roles": {"normal": {"font": MB, "fill": "#ffffff", "shadows": _DROP},
              "key": {"font": MB, "fill": "#ffffff", "bg": {"colour": "#ff2d2d", "pad": 0.14, "radius": 0.12}}},
}

NEON_SIGN = {       # pink and cyan tubes, glowing
    "id": "neon_sign", "label": "Neon sign", "lines": (2, 3), "pref": 2, "anim": "fade",
    "roles": {"normal": {"font": MD, "fill": "#ffe6fb", "strokes": [{"colour": "#ff4fd8", "w": 0.03, "pos": "outer"}],
                         "glows": [{"colour": "#ff3ccf", "blur": 0.35, "strength": 1.6}]},
              "key": {"font": MD, "fill": "#e6fdff", "strokes": [{"colour": "#29e7ff", "w": 0.03, "pos": "outer"}],
                      "glows": [{"colour": "#18d8ff", "blur": 0.35, "strength": 1.6}]}},
}

SUNSET = {          # lower case, the key in a sunset gradient
    "id": "sunset", "label": "Sunset gradient", "caps": False, "lines": (2, 3), "pref": 2, "anim": "pop",
    "roles": _both({"font": PX, "fill": "#ffffff", "shadows": [{"colour": "rgba(40,0,40,.45)", "angle": 180, "dist": 0.05, "blur": 0.14}]},
                   gradient={"angle": 180, "stops": [(0, "#ffd36e"), (0.5, "#ff7a59"), (1, "#ff3d8b")]}),
}

_STICKER_EDGE = [{"colour": "#111111", "w": 0.035, "pos": "outer"}, {"colour": "#ffffff", "w": 0.05, "pos": "outer"}]
STICKER = {         # die-cut: a black then a white edge, a hard shadow, the key tilted
    "id": "sticker", "label": "Sticker", "lines": (2, 3), "pref": 2, "anim": "pop", "gap": 0.2,
    "roles": _both({"font": PX, "fill": "#ffffff", "strokes": _STICKER_EDGE,
                    "shadows": [{"colour": "rgba(0,0,0,.6)", "angle": 160, "dist": 0.07, "blur": 0.02}]},
                   fill="#ffcf24", rot=-4),
}

GLITCH = {          # red and cyan copies split either side
    "id": "glitch", "label": "Glitch", "lines": (2, 3), "pref": 2, "anim": "blur",
    "roles": {"normal": {"font": MB, "fill": "#ffffff", "copies": [{"n": 1, "step": (-0.035, 0.0), "fill": "#ff2a55", "alpha": 0.85},
                                                                   {"n": 1, "step": (0.035, 0.0), "fill": "#1ee8ff", "alpha": 0.85}]},
              "key": {"font": MB, "fill": "#ffffff", "copies": [{"n": 1, "step": (-0.05, 0.01), "fill": "#ff2a55", "alpha": 0.9},
                                                                {"n": 1, "step": (0.05, -0.01), "fill": "#1ee8ff", "alpha": 0.9}]}},
}

TABLOID = {         # tall condensed capitals, staggered, the key in red
    "id": "tabloid", "label": "Tabloid", "lines": (2, 4), "pref": 3, "anim": "slam", "arrange": "stagger", "gap": 0.06,
    "roles": {"normal": {"font": ("Anton", "Regular"), "fill": "#ffffff"},
              "key": {"font": ("Anton", "Regular"), "fill": "#ff3b30"}},
}

SIGNATURE = {       # tilted brush script, the key in gold
    "id": "signature", "label": "Signature", "caps": False, "lines": (1, 3), "pref": 2, "anim": "fade", "gap": 0.12,
    "inkLines": True,
    "roles": {"normal": {"font": ("Kaushan Script", "Regular"), "fill": "#ffffff", "rot": -5,
                         "glows": [{"colour": "#ffffff", "blur": 0.3, "strength": 0.35}]},
              "key": {"font": ("Kaushan Script", "Regular"), "rot": -5, "scale": 1.3,
                      "gradient": {"angle": 90, "stops": [(0, "#f9e29c"), (1, "#d79a3c")]},
                      "glows": [{"colour": "#ffcf70", "blur": 0.3, "strength": 0.5}]}},
}

MINIMAL = {         # small wide-spaced capitals low in the frame
    "id": "minimal", "label": "Minimal", "lines": (1, 3), "pref": 2, "anim": "fade", "fill": 0.14, "place": "bc", "gap": 0.35,
    "roles": {"normal": {"font": MM, "fill": "#ffffff", "track": 0.18},
              "key": {"font": MD, "fill": "#ffffff", "track": 0.18}},
}

RETRO_CHROME = {    # 80s chrome: a split gradient, a dark edge and a hot pink drop
    "id": "retro_chrome", "label": "Retro chrome", "lines": (2, 3), "pref": 2, "anim": "slide",
    "roles": _both({"font": MB, "gradient": {"angle": 180, "stops": [(0, "#ffffff"), (0.48, "#b8d7ff"), (0.52, "#5b3db3"), (1, "#f0b8ff")]},
                    "strokes": [{"colour": "#2a0f5c", "w": 0.04, "pos": "outer"}],
                    "shadows": [{"colour": "#ff3fa4", "angle": 150, "dist": 0.08, "blur": 0.0}]},
                   gradient={"angle": 180, "stops": [(0, "#fff7c2"), (0.48, "#ffc23d"), (0.52, "#c2410c"), (1, "#ffe08a")]}),
}

HIGHLIGHTER = {     # lower case, the key marked in yellow
    "id": "highlighter", "label": "Highlighter", "caps": False, "lines": (2, 3), "pref": 2, "anim": "pop", "gap": 0.1,
    "roles": {"normal": {"font": PB, "fill": "#ffffff"},
              "key": {"font": PX, "fill": "#111111", "bg": {"colour": "#ffe14d", "pad": 0.1, "radius": 0.04}}},
}

DUOTONE = {         # two colours, left-aligned, an orange accent
    "id": "duotone", "label": "Duotone", "caps": False, "lines": (2, 3), "pref": 2, "anim": "slide", "arrange": "left",
    "accent": "auto",
    "roles": {"normal": {"font": PX, "fill": "#b9a8ff"},
              "key": {"font": PX, "fill": "#ffffff", "scale": 1.15},
              "accent": {"font": PX, "fill": "#ff9a3c"}},
}

FIRE = {            # condensed capitals burning yellow to red
    "id": "fire", "label": "Fire", "lines": (2, 3), "pref": 2, "anim": "blur",
    "roles": {"normal": {"font": ("Anton", "Regular"), "gradient": {"angle": 180, "stops": [(0, "#fff3a0"), (0.55, "#ffa21f"), (1, "#e2241b")]},
                         "glows": [{"colour": "#ff6a00", "blur": 0.3, "strength": 0.9}]},
              "key": {"font": ("Anton", "Regular"), "gradient": {"angle": 180, "stops": [(0, "#ffffff"), (0.4, "#ffe45c"), (1, "#ff5a1f")]},
                      "glows": [{"colour": "#ff6a00", "blur": 0.35, "strength": 1.3}]}},
}

# With the fonts bundled for them (2026-10-05).

CINEMATIC = {       # wide-spaced condensed capitals, a soft shadow
    "id": "cinematic", "label": "Cinematic", "lines": (1, 3), "pref": 2, "anim": "fade", "fill": 0.22, "gap": 0.18,
    "roles": {"normal": {"font": ("Bebas Neue", "Regular"), "fill": "#f2f2f2", "track": 0.12, "scale": 0.7, "shadows": _DROP},
              "key": {"font": ("Bebas Neue", "Regular"), "fill": "#ffffff", "track": 0.08, "shadows": _DROP}},
}

WORD_BLOCKS = {     # every word on its own block, the key's yellow
    "id": "word_blocks", "label": "Word blocks", "lines": (2, 4), "pref": 3, "anim": "slam", "gap": 0.12,
    "roles": {"normal": {"font": ("Oswald", "Bold"), "fill": "#ffffff", "bg": {"colour": "#111111", "pad": 0.12, "radius": 0.03}},
              "key": {"font": ("Oswald", "Bold"), "fill": "#111111", "bg": {"colour": "#ffd60a", "pad": 0.12, "radius": 0.03}}},
}

POSTER = {          # heavy capitals packed tight, left-aligned
    "id": "poster", "label": "Poster", "lines": (2, 4), "pref": 3, "anim": "slide", "arrange": "left", "gap": 0.04,
    "fill": 0.4,
    "roles": {"normal": {"font": ("Archivo Black", "Regular"), "fill": "#ffffff"},
              "key": {"font": ("Archivo Black", "Regular"), "fill": "#ff5a1f"}},
}

BUBBLE = {          # round heavy letters in pastels, a white edge
    "id": "bubble", "label": "Bubble", "caps": False, "lines": (2, 3), "pref": 2, "anim": "pop", "gap": 0.16,
    "roles": _both({"font": ("Rubik", "Black"), "fill": "#ffb3d9",
                    "strokes": [{"colour": "#ffffff", "w": 0.04, "pos": "outer"}],
                    "shadows": [{"colour": "rgba(90,30,120,.45)", "angle": 180, "dist": 0.06, "blur": 0.08}]},
                   fill="#9fe7ff", scale=1.2),
}

COMIC = {           # comic capitals, black-edged, a hard shadow, the key tilted
    "id": "comic", "label": "Comic", "lines": (2, 3), "pref": 2, "anim": "slam", "gap": 0.12,
    "roles": _both({"font": ("Bangers", "Regular"), "fill": "#ffffff", "track": 0.03,
                    "strokes": [{"colour": "#000000", "w": 0.06, "pos": "outer"}],
                    "shadows": [{"colour": "#000000", "angle": 135, "dist": 0.08, "blur": 0.0}]},
                   fill="#ffe500", rot=-4),
}

MARKER = {          # hand-lettered, the key on a tilted highlight
    "id": "marker", "label": "Marker", "lines": (2, 3), "pref": 2, "anim": "pop", "gap": 0.12,
    "roles": {"normal": {"font": ("Permanent Marker", "Regular"), "fill": "#ffffff", "shadows": _DROP},
              "key": {"font": ("Permanent Marker", "Regular"), "fill": "#111111", "rot": -3,
                      "bg": {"colour": "#ff5fa2", "pad": 0.1, "radius": 0.06}}},
}

EDITORIAL = {       # serif, lower case, the key in italic
    "id": "editorial", "label": "Editorial", "mode": "ratio", "caps": False, "lines": (2, 3), "pref": 2,
    "anim": "fade", "gap": 0.1,
    "roles": {"normal": {"font": ("DM Serif Display", "Regular"), "fill": "#f5f1e8"},
              "key": {"font": ("DM Serif Display", "Italic"), "fill": "#ffffff", "scale": 1.6}},
}

LUXURY = {          # gold serif capitals, wide-spaced
    "id": "luxury", "label": "Luxury", "lines": (1, 3), "pref": 2, "anim": "fade", "fill": 0.2, "gap": 0.22,
    "roles": _both({"font": ("DM Serif Display", "Regular"), "track": 0.14, "scale": 0.75,
                    "gradient": {"angle": 160, "stops": [(0, "#fff1c1"), (0.5, "#d6a84a"), (1, "#8a6420")]},
                    "shadows": [{"colour": "rgba(0,0,0,.5)", "angle": 180, "dist": 0.03, "blur": 0.1}]},
                   scale=1.0, track=0.1),
}

TERMINAL = {        # green monospace, glowing, typed out
    "id": "terminal", "label": "Terminal", "caps": False, "lines": (2, 3), "pref": 2, "anim": "type", "arrange": "left",
    "fill": 0.24, "inkLines": True, "gap": 0.3,
    "roles": _both({"font": ("Space Mono", "Bold"), "fill": "#39ff6a",
                    "glows": [{"colour": "#1aff4d", "blur": 0.3, "strength": 0.7}]},
                   fill="#d6ffe0"),
}

DINER = {           # 50s script: pink, a white edge and a teal drop
    "id": "diner", "label": "Diner", "mode": "ratio", "caps": False, "lines": (1, 3), "pref": 2, "anim": "pop", "gap": 0.1, "inkLines": True,
    "roles": _both({"font": ("Pacifico", "Regular"), "fill": "#ff6fa8", "rot": -4,
                    "strokes": [{"colour": "#ffffff", "w": 0.045, "pos": "outer"}],
                    "shadows": [{"colour": "#1fb5a8", "angle": 135, "dist": 0.07, "blur": 0.0}]},
                   fill="#ffd23f", scale=1.5),
}

_CARTOON_EDGE = [{"colour": "#2b1400", "w": 0.06, "pos": "outer"}]
CARTOON = {         # chunky cartoon letters, a dark edge, a short extrusion
    "id": "cartoon", "label": "Cartoon", "lines": (2, 3), "pref": 2, "anim": "pop", "gap": 0.16,
    "roles": _both({"font": ("Luckiest Guy", "Regular"), "fill": "#ffffff", "strokes": _CARTOON_EDGE,
                    "copies": [{"n": 6, "step": (0.01, 0.014), "fill": "#2b1400", "alpha": 1.0, "fade": 1.0}]},
                   fill="#ffc233"),
}

ARCADE = {          # blocky signage capitals, cyan, extruded in magenta
    "id": "arcade", "label": "Arcade", "lines": (2, 3), "pref": 2, "anim": "slam", "gap": 0.2,
    "roles": _both({"font": ("Bungee", "Regular"), "gradient": {"angle": 180, "stops": [(0, "#b8fbff"), (1, "#25a8ff")]},
                    "copies": [{"n": 8, "step": (0.012, 0.012), "fill": "#d61f8c", "alpha": 1.0, "fade": 0.97,
                                "anim": "spread", "travel": 8 / 30, "lag": 1 / 120}]},
                   gradient={"angle": 180, "stops": [(0, "#fffbb8"), (1, "#ffb625")]}),
}

SYNTHWAVE = {       # rounded retro letters, pink to orange, glowing
    "id": "synthwave", "label": "Synthwave", "lines": (2, 3), "pref": 2, "anim": "slide", "gap": 0.14,
    "roles": _both({"font": ("Righteous", "Regular"), "gradient": {"angle": 180, "stops": [(0, "#ffd1f2"), (0.5, "#ff4fb4"), (1, "#7a2bff")]},
                    "glows": [{"colour": "#ff2fa8", "blur": 0.3, "strength": 0.8}]},
                   gradient={"angle": 180, "stops": [(0, "#fff3b0"), (0.5, "#ff9d3c"), (1, "#ff2f6d")]}),
}

FASHION = {         # a fat serif, lower case, the key in red
    "id": "fashion", "label": "Fashion", "caps": False, "lines": (2, 3), "pref": 2, "anim": "fade", "gap": 0.02,
    "roles": {"normal": {"font": ("Abril Fatface", "Regular"), "fill": "#ffffff"},
              "key": {"font": ("Abril Fatface", "Regular"), "fill": "#ff3b4f"}},
}

STENCIL = {         # stencilled capitals in khaki, the key orange
    "id": "stencil", "label": "Stencil", "lines": (2, 3), "pref": 2, "anim": "slam", "gap": 0.12,
    "roles": {"normal": {"font": ("Black Ops One", "Regular"), "fill": "#d7cf9f",
                         "shadows": [{"colour": "rgba(0,0,0,.65)", "angle": 180, "dist": 0.04, "blur": 0.04}]},
              "key": {"font": ("Black Ops One", "Regular"), "fill": "#ff8a1f",
                      "shadows": [{"colour": "rgba(0,0,0,.65)", "angle": 180, "dist": 0.04, "blur": 0.04}]}},
}

NEON_TUBES = {      # letters drawn in lines, lit like tubes
    "id": "neon_tubes", "label": "Neon tubes", "lines": (2, 3), "pref": 2, "anim": "fade", "gap": 0.18,
    "roles": _both({"font": ("Monoton", "Regular"), "fill": "#ffe3f6", "glows": [{"colour": "#ff3ccf", "blur": 0.28, "strength": 1.4}]},
                   fill="#e3fbff", glows=[{"colour": "#22d3ff", "blur": 0.28, "strength": 1.4}]),
}

VINTAGE = {         # bold script in cream, a long brown shadow
    "id": "vintage", "label": "Vintage", "caps": False, "lines": (1, 3), "pref": 2, "anim": "slide", "gap": 0.1, "inkLines": True,
    "roles": _both({"font": ("Lobster", "Regular"), "fill": "#ffe9c7",
                    "copies": [{"n": 10, "step": (0.012, 0.012), "fill": "#6b3a1e", "alpha": 1.0, "fade": 0.97,
                                "anim": "spread", "travel": 8 / 30, "lag": 1 / 120}]},
                   fill="#ffb84d"),
}

HANDWRITTEN = {     # a casual hand, lower case, the key bigger and yellow
    "id": "handwritten", "label": "Handwritten", "mode": "ratio", "caps": False, "lines": (2, 3), "pref": 2,
    "anim": "type", "gap": 0.12, "inkLines": True,
    "roles": {"normal": {"font": ("Caveat", "Bold"), "fill": "#ffffff", "rot": -2, "shadows": _DROP},
              "key": {"font": ("Caveat", "Bold"), "fill": "#ffe14d", "scale": 1.5, "rot": -2, "shadows": _DROP}},
}

# The Looks rail, group by group, in this order.
GROUPS = (
    ("Bold", (HORMOZI, RED_BOX, WORD_BLOCKS, TABLOID, POSTER, HIGHLIGHTER, MIXED, DUOTONE)),
    ("Clean", (CLEAN_OUTLINE, OUTLINE_FILL, TITLE_BAR, MINIMAL, CINEMATIC, EDITORIAL, LUXURY, FASHION)),
    ("Glow", (NEON_SIGN, NEON_TUBES, NEON_EXTRUDE, FIRE, SYNTHWAVE, GLITCH, TERMINAL)),
    ("Script", (SCRIPT, SIGNATURE, HANDWRITTEN, MARKER, DINER, VINTAGE, SUNSET)),
    ("Depth", (POP_3D, LONG_SHADOW, ECHO, RETRO_CHROME, STICKER, CARTOON, ARCADE, COMIC, BUBBLE, STENCIL)),
)
LOOKS = {look["id"]: {**look, "group": name} for name, group in GROUPS for look in group}
ROLES = ("normal", "key", "accent", "small")


def get(look_id):
    """The look by id (an unknown id gets the first), with DEFAULTS filled in."""
    look = LOOKS.get(look_id) or next(iter(LOOKS.values()))
    return {**DEFAULTS, **look}


def role_style(look, role, doc=None):
    """A role's style. A role a look leaves out takes normal's. With doc, the person's
    changes for the whole role go on: its face (roleStyle[role]["font"]) and colour
    (roleColour[role])."""
    roles = look.get("roles") or {}
    style = roles.get(role) or roles.get("normal") or {"font": ("Arial", "Bold")}
    if doc:
        style = restyled(style, font=((doc.get("roleStyle") or {}).get(role) or {}).get("font"),
                         colour=(doc.get("roleColour") or {}).get(role))
    return style


def word_style(look, role, word_id, line_first, doc):
    """A word's style: its role's (with the role's changes), then its line's colour, then
    its own colour and outline width - the most particular wins."""
    over = (doc or {}).get("over") or {}
    style = role_style(look, role, doc)
    line = ((doc or {}).get("lineColour") or {}).get(line_first)
    style = restyled(style, colour=(over.get("colour") or {}).get(word_id) or line,
                     stroke=(over.get("stroke") or {}).get(word_id))
    return style


def box_styles(layout, doc=None):
    """Each of a layout's boxes' style, with the person's changes (doc) on."""
    look = get(layout["look"])
    if not doc:
        return [role_style(look, b["role"]) for b in layout["boxes"]]
    firsts = [ids[0] for ids in layout.get("lines") or []]
    return [word_style(look, b["role"], b["id"], firsts[b["line"]] if b["line"] < len(firsts) else None, doc)
            for b in layout["boxes"]]


def is_colour(value):
    """"#rgb", "#rrggbb" or "#rrggbbaa"."""
    return isinstance(value, str) and re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value) is not None


def is_face(value):
    """A (family, style) pair of names."""
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, str) and 0 < len(v) <= 80 for v in value))


def restyled(style, font=None, colour=None, stroke=None):
    """style with a face, a colour and an outline width (a multiple of the look's) put
    on - the same style if none is. A colour fills the letters (a gradient gives way to
    it); a hollow word's letters are its strokes, so they take it instead."""
    stroke = float(stroke) if isinstance(stroke, (int, float)) and not isinstance(stroke, bool) else None
    font = tuple(font) if is_face(font) else None
    colour = colour if is_colour(colour) else None
    if font is None and colour is None and (stroke is None or stroke == 1.0):
        return style
    out = copy.deepcopy(style)
    if font:
        out["font"] = font
    if colour:
        if out.get("fill", "#ffffff") == "none" and not out.get("gradient"):
            out["strokes"] = [{**k, "colour": colour} for k in out.get("strokes") or []]
        else:
            out["fill"] = colour
            out.pop("gradient", None)
    if stroke is not None:
        stroke = min(max(stroke, 0.0), 4.0)
        out["strokes"] = [{**k, "w": float(k.get("w", 0.0)) * stroke} for k in out.get("strokes") or []]
    return out
