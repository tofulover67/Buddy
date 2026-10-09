#!/usr/bin/env python3
"""
Animation > Transitions: the seamless motion transitions - the shapes of
them, no Resolve, no Qt. web/animation.js has the same maths line for line
for the live previews (trAt); transition_resolve.py puts them in Resolve.

A transition is `frames` long with the cut in its middle. One eased curve,
D(p) from 0 to 1 over the whole of it (the feel: FEELS), drives one motion
that carries straight across the cut: the outgoing clip (side "a") moves
out - zooms in, whips away, spins, stretches - and the incoming clip (side
"b") arrives doing the same, so the eye reads one move. Each side's share
of the move is w: a's goes 0 -> 1 up to the cut, b's -1 -> 0 after it (a
swing's anticipation and overshoot take w a little past either end).
Edges are mirrored, so a picture smaller than the frame or moved off it
never shows black. Blur follows the speed, so it peaks at the cut.

Positions are shares of the frame, y UP (Fusion's); angles in degrees,
anticlockwise.

The light group (GROUPS "light": light leak, film burn, dreamy blur, flash)
moves nothing: the clips cross under a swell of light that peaks at the cut
and hides it - each side brightened and softened by the swell, and a
generated light (Fusion's FastNoise for a leak or burn, a solid colour for
the rest) screened over the top, in the chosen COLORS. The feel shapes the
swell: Ease a soft bell, Hit a sharp spike, Swing a long broad one, Bounce
a flicker.

The wipe group (liquid, ink, circle, bars, swipe) reveals the incoming
clip through a moving edge: a map - a grey picture, black where the reveal
starts, white where it ends (a gradient: linear, radial, repeated in bars;
with noise mixed in for a liquid or inky edge) - and two thresholds on it
sweeping 0 -> 1. Ahead of the incoming clip runs a band of the chosen
colour (m1 its front, m2 the clip's), so a flat wipe reads as a coloured
shape crossing the frame. The feel paces the sweep.

The glitch group (VHS, bad signal, digital) breaks the picture up around
the cut: the clips stutter between each other, the colour channels split
apart, horizontal bands tear sideways, the picture flickers, loses colour,
breaks into blocks, under scanlines and grain - each frame its own throw of
the dice (_dice: the same numbers in the page's previews), all of it riding
the swell. A transition's comp only sees its own frames, so there's no
"rewind" here (measured: a TimeStretcher asked for earlier ones got black).

The paper group: a paper tear (the outgoing clip torn in two along a
jagged line, the halves pulled apart with white torn edges and shadows over
the incoming one), a burn (the outgoing clip burning away from scattered
holes, a glowing ember edge in the chosen colour behind a charred one), a
brush stroke (a streaky paint wipe, its colour leading) and grunge (a
dirty, boiling ink matte stepping through at 12 a second, dust and
scratches over it, the picture jittering) - the last three wipes on maps of
their own.

The distortion group bends the picture itself, a crossfade hidden at the
peak: a twirl (Fusion's Vortex - the outgoing clip winds up, the incoming
one unwinds the same way), a ripple (Drip: waves running out from the
middle), a kaleidoscope (the picture in polar co-ordinates, mirror-tiled
into segments and turning), a bulge (Dent 2: a fisheye swell), a pinch
(Dent 0, its Strength negative: the picture drawn into its middle) and a
warp (Displace by churning noise). Blur softens each side at the peak.

The split-screen group cuts a clip into rectangles - split in two, panels
across, strips down, a grid - that move one after another (pieces():
along, in a diagonal wave or at random): the outgoing clip's halves sliding
apart off the incoming one (split open), or the incoming clip's pieces
sliding in over the outgoing one from below, the sides or anywhere, or
growing from their middles. A line of the chosen colour runs round each
piece, swelling to the cut and gone at the ends. Its frames run from the
outgoing picture whole (the first) to the incoming one whole (the last).
A push slides both clips at once, one out and one in; a squeeze sets them
side by side, each squashed into its share as the line between crosses; a
film roll runs them up as frames on a strip of film.

The 3D group turns the pictures in perspective (Fusion's DVE): a card
flipping over, a cube rolling round to its next face (either with the
camera pulling back as it turns), a door swinging open and a picture
falling back off the one behind it. Amount brings the
camera closer (stronger perspective); flips and cubes turn in front of the
chosen colour.
"""

import math
import re

KINDS = {
    "zoom_in": {"label": "Zoom in", "move": "zoom", "sign": 1},
    "zoom_out": {"label": "Zoom out", "move": "zoom", "sign": -1},
    "whip_left": {"label": "Whip left", "move": "pan", "dir": (-1, 0)},
    "whip_right": {"label": "Whip right", "move": "pan", "dir": (1, 0)},
    "whip_up": {"label": "Whip up", "move": "pan", "dir": (0, 1)},
    "whip_down": {"label": "Whip down", "move": "pan", "dir": (0, -1)},
    "spin_left": {"label": "Spin left", "move": "spin", "sign": 1},
    "spin_right": {"label": "Spin right", "move": "spin", "sign": -1},
    "stretch_x": {"label": "Stretch sideways", "move": "stretch", "axis": "x"},
    "stretch_y": {"label": "Stretch up", "move": "stretch", "axis": "y"},
    "shake": {"label": "Shake", "move": "shake"},
    "zoom_spin": {"label": "Zoom spin", "move": "twist", "sign": 1},
    "whip_diag": {"label": "Whip diagonal", "move": "pan", "dir": (0.7071, 0.7071)},
    # A zoom through copies of the picture nested inside it ("nest", shown
    # around the cut); a zoom with its colour channels pulled apart
    # ("chroma", a share of the size at the peak); a roll: the picture
    # swung round a point far below the frame (ROLL_PIVOT).
    "tunnel": {"label": "Tunnel", "move": "zoom", "sign": 1, "nest": True},
    "chroma_zoom": {"label": "Chroma zoom", "move": "zoom", "sign": 1, "chroma": 0.05},
    "roll_left": {"label": "Roll left", "move": "roll", "sign": 1},
    "roll_right": {"label": "Roll right", "move": "roll", "sign": -1},
    # A punch: a zoom that lands with a flash ("flash": the gain added at
    # the cut, Amount 1) and a jolt (shaken, frame widths).
    "punch": {"label": "Punch zoom", "move": "zoom", "sign": 1, "flash": 1.2, "jolt": 0.03},
    # Zoom out-in: the outgoing clip pulls back, the incoming one pushes
    # in from small ("back": the incoming side zooms the other way).
    "zoom_out_in": {"label": "Zoom out-in", "move": "zoom", "sign": -1, "back": True},
    # The light group: the crossfade's half-width around the cut, the light
    # (Fusion FastNoise settings, or a solid), how much the swell brightens,
    # softens and lights each side at Amount 1, and how sharp it is (the
    # swell to this power).
    "light_leak": {"label": "Light leak", "group": "light", "fade": 0.15, "gain": 0.4, "soft": 0.25, "glow": 1.0,
                   "sharp": 1.0, "light": "noise",
                   "noise": {"XScale": 1.2, "Detail": 1.5, "Contrast": 2.5, "Brightness": 0.05}},
    "film_burn": {"label": "Film burn", "group": "light", "fade": 0.12, "gain": 0.8, "soft": 0.4, "glow": 1.0,
                  "sharp": 1.0, "light": "noise",
                  "noise": {"XScale": 1.2, "Detail": 3.0, "Contrast": 2.2, "Brightness": 0.25}},
    "dreamy": {"label": "Dreamy blur", "group": "light", "fade": 0.3, "gain": 0.25, "soft": 1.0, "glow": 0.3,
               "sharp": 1.0, "light": "solid"},
    "flash": {"label": "Flash", "group": "light", "fade": 0.0, "gain": 2.5, "soft": 0.3, "glow": 0.85,
              "sharp": 3.0, "light": "solid"},
    # ... and the ones that work each side's own light: "fx" rays (streaks
    # from its brights), bloom (a soft glow) or bokeh (a lens defocus);
    # "flare" a lens flare's streak swept across, in the colour.
    "light_rays": {"label": "Light rays", "group": "light", "fade": 0.2, "gain": 0.2, "soft": 0.0, "glow": 0.0,
                   "sharp": 1.0, "light": "none", "fx": "rays", "color": False},
    "lens_flare": {"label": "Lens flare", "group": "light", "fade": 0.15, "gain": 0.3, "soft": 0.15, "glow": 1.0,
                   "sharp": 1.0, "light": "flare"},
    "bloom": {"label": "Bloom", "group": "light", "fade": 0.2, "gain": 0.15, "soft": 0.1, "glow": 0.0,
              "sharp": 1.0, "light": "none", "fx": "bloom", "color": False},
    "bokeh": {"label": "Bokeh", "group": "light", "fade": 0.25, "gain": 0.1, "soft": 0.0, "glow": 0.0,
              "sharp": 1.0, "light": "none", "fx": "bokeh", "color": False},
    # A dip: the colour laid over (not screened), whole at the cut, Amount
    # how long it holds; a light sweep: a soft slanted band of the colour
    # crossing the frame (BAND_*).
    "dip": {"label": "Dip to colour", "group": "light", "fade": 0.0, "gain": 0.0, "soft": 0.0, "glow": 1.0,
            "sharp": 1.0, "light": "dip"},
    "light_sweep": {"label": "Light sweep", "group": "light", "fade": 0.15, "gain": 0.15, "soft": 0.0, "glow": 0.9,
                    "sharp": 1.0, "light": "band"},
    # A dip that softens as it goes; a crossfade through the negative
    # ("fx" invert: each side turned negative as strongly as the swell); a
    # dissolve smeared sideways ("axis": the Blur only across).
    "blur_to_color": {"label": "Blur to colour", "group": "light", "fade": 0.0, "gain": 0.0, "soft": 0.8, "glow": 1.0,
                      "sharp": 1.0, "light": "dip"},
    "negative": {"label": "Negative", "group": "light", "fade": 0.06, "gain": 0.0, "soft": 0.15, "glow": 0.0,
                 "sharp": 3.0, "light": "none", "fx": "invert", "color": False},
    "dir_blur": {"label": "Directional blur", "group": "light", "fade": 0.25, "gain": 0.1, "soft": 3.0, "glow": 0.0,
                 "sharp": 1.0, "light": "none", "axis": "x", "color": False},
    # ... and smeared out from the middle ("zoom") or round it ("spin"):
    # Directional Blur, its Length soft x Blur at the peak.
    "zoom_blur": {"label": "Zoom blur", "group": "light", "fade": 0.2, "gain": 0.15, "soft": 0.5, "glow": 0.0,
                  "sharp": 1.0, "light": "none", "axis": "zoom", "color": False},
    "spin_blur": {"label": "Spin blur", "group": "light", "fade": 0.2, "gain": 0.1, "soft": 0.12, "glow": 0.0,
                  "sharp": 1.0, "light": "none", "axis": "spin", "color": False},
    # The wipe group: the map's shape, how much noise roughens its edge, the
    # colour band's width (map units, at Amount 1) and the edge's softness
    # (map units, at Blur 1); an ink's band is its colour deepened.
    "liquid": {"label": "Liquid", "group": "wipe", "map": "linear", "noise": 0.35, "band": 0.06, "edge": 0.08},
    "ink": {"label": "Ink", "group": "wipe", "map": "radial", "noise": 0.5, "band": 0.07, "edge": 0.05, "dark": True},
    "circle": {"label": "Circle", "group": "wipe", "map": "radial", "noise": 0.0, "band": 0.15, "edge": 0.02},
    "bars": {"label": "Bars", "group": "wipe", "map": "bars", "noise": 0.0, "band": 0.15, "edge": 0.02},
    "swipe": {"label": "Swipe", "group": "wipe", "map": "linear", "noise": 0.0, "band": 0.18, "edge": 0.02},
    # Fusion's other gradients: Angle (a clock hand, turned clockwise:
    # "flip" reveals from the map's top down), Square (a box) and Reflect
    # (from the middle line out); a star is a shape grown from the middle.
    "clock": {"label": "Clock", "group": "wipe", "map": "angle", "flip": True, "noise": 0.0, "band": 0.05,
              "edge": 0.01},
    "box": {"label": "Box", "group": "wipe", "map": "square", "noise": 0.0, "band": 0.1, "edge": 0.02},
    "barn_doors": {"label": "Barn doors", "group": "wipe", "map": "reflect", "noise": 0.0, "band": 0.12,
                   "edge": 0.02},
    "star": {"label": "Star", "group": "wipe", "map": "star", "noise": 0.0, "band": 0.12, "edge": 0.01},
    # A luma fade: the incoming picture's own brightness is the map, its
    # brights first; blinds: every slat at once (BLINDS); halftone: small
    # squares (HALFTONE across) opening in a diagonal wave; a shutter: a
    # six-bladed aperture closing on the outgoing clip, opening on the next.
    "luma_fade": {"label": "Luma fade", "group": "wipe", "map": "luma", "flip": True, "noise": 0.0, "band": 0.0,
                  "edge": 0.25, "color": False},
    "blinds": {"label": "Blinds", "group": "wipe", "map": "blinds", "noise": 0.0, "band": 0.05, "edge": 0.01},
    "halftone": {"label": "Halftone", "group": "wipe", "map": "halftone", "noise": 0.0, "band": 0.08, "edge": 0.02},
    "shutter": {"label": "Shutter", "group": "wipe", "map": "shutter", "noise": 0.0, "band": 0.0, "edge": 0.0,
                "color": False},
    # A luma fade led by a band of the colour; a scanner: a glowing line
    # ("glow", as a burn's ember) running down the frame.
    "luma_burn": {"label": "Luma burn", "group": "wipe", "map": "luma", "flip": True, "noise": 0.0, "band": 0.1,
                  "edge": 0.05},
    "scanner": {"label": "Scanner", "group": "wipe", "map": "down", "noise": 0.0, "band": 0.02, "edge": 0.01,
                "glow": 0.9},
    # A diagonal swipe, corner to corner; a diamond grown from the middle
    # (a star's way, "shape"); an iris: the shutter's way, round - closing
    # on the outgoing clip, opening on the next.
    "diagonal": {"label": "Diagonal", "group": "wipe", "map": "diag", "noise": 0.0, "band": 0.12, "edge": 0.03},
    # A spiral (a third the clock's turn, two thirds the circle's way out:
    # its edge winds out from the middle); a circle from a corner; a
    # zigzag-edged swipe (ZIGZAG teeth down the frame).
    "spiral": {"label": "Spiral", "group": "wipe", "map": "spiral", "noise": 0.0, "band": 0.06, "edge": 0.02},
    "corner": {"label": "Corner", "group": "wipe", "map": "corner", "noise": 0.0, "band": 0.12, "edge": 0.02},
    "zigzag": {"label": "Zigzag", "group": "wipe", "map": "zigzag", "noise": 0.0, "band": 0.08, "edge": 0.01},
    "diamond": {"label": "Diamond", "group": "wipe", "map": "star", "shape": "diamond", "noise": 0.0, "band": 0.1,
                "edge": 0.01},
    "heart": {"label": "Heart", "group": "wipe", "map": "star", "shape": "heart", "noise": 0.0, "band": 0.1,
              "edge": 0.01},
    "iris": {"label": "Iris", "group": "wipe", "map": "shutter", "shape": "circle", "noise": 0.0, "band": 0.0,
             "edge": 0.0, "color": False},
    # The glitch group, at Amount 1 and the swell's peak: the channel split
    # (frame widths), band tearing (Displace refraction), the bands' height
    # (FastNoise YScale) and whether they're hard-edged, scanlines, grain,
    # flicker (gain), colour lost, pixel blocks (pixels at 1920 wide) and how
    # much of the transition the cut stutters over.
    "vhs": {"label": "VHS", "group": "glitch", "split": 0.014, "tear": 0.05, "rows": 60.0, "hard": False,
            "scan": 0.5, "grain": 0.35, "flicker": 0.15, "fade": 0.65, "block": 0.0, "stutter": 0.2},
    "bad_signal": {"label": "Bad signal", "group": "glitch", "split": 0.02, "tear": 0.08, "rows": 6.0, "hard": True,
                   "scan": 0.15, "grain": 0.3, "flicker": 0.4, "fade": 0.2, "block": 0.0, "stutter": 0.8},
    "digital": {"label": "Digital", "group": "glitch", "split": 0.012, "tear": 0.04, "rows": 4.0, "hard": True,
                "scan": 0.0, "grain": 0.1, "flicker": 0.2, "fade": 0.0, "block": 48.0, "stutter": 0.6},
    # A pixelate: blocks growing to the cut (pixels at 1920 wide, Amount
    # 1), crossfaded under; a TV switching off: the picture squashed to a
    # line, the line to a dot, out - and the next one switching on.
    "pixelate": {"label": "Pixelate", "group": "glitch", "style": "pixelate", "fade": 0.12, "block": 80.0},
    "tv_off": {"label": "TV switch", "group": "glitch", "style": "crt"},
    # A strobe: the clips swapping every frame, flashing - nothing torn.
    "strobe": {"label": "Strobe", "group": "glitch", "split": 0.0, "tear": 0.0, "rows": 6.0, "hard": False,
               "scan": 0.0, "grain": 0.0, "flicker": 0.7, "fade": 0.0, "block": 0.0, "stutter": 1.0},
    # Chaos: all of it at once, harder - and the picture jumping about
    # ("jump", frame widths) and flashing negative ("invert": the share of
    # frames at the peak).
    "chaos": {"label": "Chaos", "group": "glitch", "split": 0.03, "tear": 0.12, "rows": 10.0, "hard": True,
              "scan": 0.25, "grain": 0.35, "flicker": 0.5, "fade": 0.4, "block": 56.0, "stutter": 1.0,
              "jump": 0.04, "invert": 0.35},
    # The paper group. A tear: how much noise makes its line jagged (and the
    # noise: Fusion FastNoise settings, mirrored in the previews), how far
    # the halves tilt at Amount 1; it takes no colour. The rest are wipes
    # (map, noise, band, edge as above) on their own noise; a burn has a
    # charred band ahead of its ember one; grunge steps (STEP_RATE), with
    # dirt over it and the picture jittering (frame widths).
    "paper_tear": {"label": "Paper tear", "group": "paper", "style": "tear", "noise": 0.12, "tilt": 6.0,
                   "grain": {"XScale": 6.0, "YScale": 6.0, "Detail": 6.0, "Contrast": 1.2}, "color": False},
    "burn_away": {"label": "Burn away", "group": "paper", "style": "burn", "map": "radial", "noise": 0.75,
                  "band": 0.03, "char": 0.06, "edge": 0.03, "glow": 0.9,
                  "grain": {"XScale": 4.0, "YScale": 4.0, "Detail": 5.0, "Contrast": 1.6}},
    "brush": {"label": "Brush stroke", "group": "paper", "style": "brush", "map": "linear", "noise": 0.08,
              "band": 0.08, "edge": 0.01, "grain": {"XScale": 0.3, "YScale": 140.0, "Detail": 3.0, "Contrast": 1.4}},
    "grunge": {"label": "Grunge", "group": "paper", "style": "grunge", "map": "linear", "noise": 0.65,
               "band": 0.15, "edge": 0.01, "dark": True, "dirt": 0.8, "jitter": 0.004,
               "grain": {"XScale": 5.0, "YScale": 5.0, "Detail": 4.0, "Contrast": 2.0}},
    "ink_bleed": {"label": "Ink bleed", "group": "paper", "style": "brush", "map": "radial", "noise": 0.6,
                  "band": 0.05, "edge": 0.08, "dark": True,
                  "grain": {"XScale": 3.0, "YScale": 3.0, "Detail": 5.0, "Contrast": 1.3}},
    "paint_splash": {"label": "Paint splash", "group": "paper", "style": "brush", "map": "radial", "noise": 0.85,
                     "band": 0.12, "edge": 0.01, "grain": {"XScale": 2.5, "YScale": 2.5, "Detail": 2.0, "Contrast": 2.2}},
    # The distortion group: the crossfade's half-width around the cut, and
    # how far each bends at Amount 1 and the swell's peak - Vortex degrees,
    # Drip amplitude, Dent strength, Displace refraction; a kaleidoscope's
    # segments, its turn (shares of a turn, across the transition) and zoom.
    "twirl": {"label": "Twirl", "group": "distort", "style": "twirl", "fade": 0.06, "degrees": 540.0},
    "ripple": {"label": "Ripple", "group": "distort", "style": "ripple", "fade": 0.2, "amp": 0.3},
    "kaleido": {"label": "Kaleidoscope", "group": "distort", "style": "kaleido", "fade": 0.1, "segments": 6,
                "spin": 0.25, "zoom": 0.6},
    "bulge": {"label": "Bulge", "group": "distort", "style": "bulge", "fade": 0.12, "strength": 0.9},
    "pinch": {"label": "Pinch", "group": "distort", "style": "pinch", "fade": 0.12, "strength": 0.22},
    "warp": {"label": "Warp", "group": "distort", "style": "warp", "fade": 0.25, "refract": 0.08},
    "wave": {"label": "Wave", "group": "distort", "style": "wave", "fade": 0.2, "amp": 0.25},
    "melt": {"label": "Melt", "group": "distort", "style": "melt", "fade": 0.25, "refract": 0.3},
    "fisheye_zoom": {"label": "Fisheye zoom", "group": "distort", "style": "lens", "fade": 0.1, "zoom": 2.0,
                     "strength": 0.9},
    # Seen through glass blocks (GLASS_CELLS across): each block shifts
    # its piece of the picture (Displace by a saw each way).
    "glass": {"label": "Glass", "group": "distort", "style": "glass", "fade": 0.12, "refract": 0.035},
    # The split-screen group: the pieces (columns, rows), whose they are
    # (the outgoing clip's "a" leave, the incoming's "b" arrive), how they
    # move - "slide" (the way "from" says: "apart" from the middle, "sides"
    # turn about, "random", or an (x, y)) or "grow" - how much of the
    # transition their start times spread over and in what order; split
    # open's incoming clip pushes in (from PUSH bigger).
    "split_open": {"label": "Split open", "group": "split", "layout": (2, 1), "src": "a", "move": "slide",
                   "from": "apart", "distance": 0.55, "spread": 0.0, "order": "along", "push": True},
    # Cut corner to corner instead ("cut"): the halves slide apart along
    # the other diagonal.
    "split_diag": {"label": "Diagonal split", "group": "split", "layout": (1, 1), "src": "a", "move": "slide",
                   "cut": "diag", "distance": 0.55, "spread": 0.0, "order": "along", "push": True},
    "panels": {"label": "Panels", "group": "split", "layout": (4, 1), "src": "b", "move": "slide", "from": (0, -1),
               "spread": 0.45, "order": "along"},
    "strips": {"label": "Strips", "group": "split", "layout": (1, 6), "src": "b", "move": "slide", "from": "sides",
               "spread": 0.35, "order": "along"},
    "grid": {"label": "Grid", "group": "split", "layout": (4, 3), "src": "b", "move": "grow", "spread": 0.5,
             "order": "wave"},
    "mosaic": {"label": "Mosaic", "group": "split", "layout": (4, 3), "src": "b", "move": "slide", "from": "random",
               "spread": 0.6, "order": "random"},
    # A push: both clips, the outgoing one out "from"'s way, the incoming
    # one in after it ("pair").
    "push_left": {"label": "Push left", "group": "split", "layout": (1, 1), "src": "pair", "move": "slide",
                  "from": (-1, 0), "distance": 1.0, "spread": 0.0, "order": "along"},
    "push_up": {"label": "Push up", "group": "split", "layout": (1, 1), "src": "pair", "move": "slide",
                "from": (0, 1), "distance": 1.0, "spread": 0.0, "order": "along"},
    "push_right": {"label": "Push right", "group": "split", "layout": (1, 1), "src": "pair", "move": "slide",
                   "from": (1, 0), "distance": 1.0, "spread": 0.0, "order": "along"},
    "push_down": {"label": "Push down", "group": "split", "layout": (1, 1), "src": "pair", "move": "slide",
                  "from": (0, -1), "distance": 1.0, "spread": 0.0, "order": "along"},
    # Slices: level strips of both clips, each pushing its own way in turn.
    "slices": {"label": "Slices", "group": "split", "layout": (1, 6), "src": "pair", "move": "slide",
               "from": "sides", "distance": 1.0, "spread": 0.3, "order": "along"},
    "columns": {"label": "Columns", "group": "split", "layout": (6, 1), "src": "b", "move": "slide",
                "from": "updown", "spread": 0.4, "order": "along"},
    "checker": {"label": "Checker", "group": "split", "layout": (4, 3), "src": "b", "move": "grow", "spread": 0.5,
                "order": "checker"},
    # A wall of screens: the outgoing clip shrinks to "scale", slides off
    # as the incoming one slides in beside it, and grows.
    "zoom_push": {"label": "Zoom push", "group": "split", "layout": (1, 1), "src": "pair", "move": "zoompush",
                  "from": (-1, 0), "scale": 0.6, "spread": 0.0, "order": "along"},
    # A film roll: the zoom push's way, upward, the clips frames on a
    # strip of film - black between them, sprocket holes up its edges.
    "film_roll": {"label": "Film roll", "group": "split", "layout": (1, 1), "src": "pair", "move": "zoompush",
                  "from": (0, 1), "scale": 0.8, "spread": 0.0, "order": "along", "film": True, "color": False},
    # A squeeze: side by side, the line between them crossing - each
    # picture squashed into its share of the frame, never cut.
    "squeeze": {"label": "Squeeze", "group": "split", "layout": (1, 1), "src": "pair", "move": "squeeze",
                "from": (-1, 0), "spread": 0.0, "order": "along"},
    "squeeze_up": {"label": "Squeeze up", "group": "split", "layout": (1, 1), "src": "pair", "move": "squeeze",
                   "from": (0, 1), "spread": 0.0, "order": "along"},
    # The 3D group: what turns and how ("flip", "cube", "door" - a hinge),
    # round which axis, which way.
    "flip": {"label": "Flip", "group": "turn", "style": "flip", "axis": "y", "sign": 1},
    "flip_up": {"label": "Flip up", "group": "turn", "style": "flip", "axis": "x", "sign": 1},
    "page_turn": {"label": "Page turn", "group": "turn", "style": "page", "axis": "y", "sign": 1},
    # Louvers: slats (LOUVERS) each flipping over on its own axis, top first.
    "louvers": {"label": "Louvers", "group": "turn", "style": "louver", "axis": "x", "sign": 1, "spread": 0.4},
    "cube": {"label": "Cube", "group": "turn", "style": "cube", "axis": "y", "sign": -1},
    "cube_up": {"label": "Cube up", "group": "turn", "style": "cube", "axis": "x", "sign": -1},
    # ... with the camera pulling back as it turns ("pull": its distance
    # grown by this share at the cut).
    "cube_zoom": {"label": "Cube zoom", "group": "turn", "style": "cube", "axis": "y", "sign": -1, "pull": 0.8},
    "flip_zoom": {"label": "Flip zoom", "group": "turn", "style": "flip", "axis": "y", "sign": 1, "pull": 0.8},
    "door": {"label": "Door", "group": "turn", "style": "door", "axis": "y", "sign": 1, "hinge": (0.0, 0.5),
             "color": False},
    "fall": {"label": "Fall back", "group": "turn", "style": "door", "axis": "x", "sign": -1, "hinge": (0.5, 0.0),
             "color": False},
}
ORDER = tuple(KINDS)
GROUPS = {"motion": "Motion", "light": "Light & blur", "wipe": "Wipes", "glitch": "Glitch",
          "paper": "Paper & grunge", "distort": "Distortions", "split": "Split screen", "turn": "3D"}
STUTTER = 0.25          # half the share of the transition the cut stutters over, at stutter 1
BARS = 6                # bars across the frame
EDGE_MIN = 0.004        # a wipe's edge is never harder than this
FEELS = ("ease", "hit", "swing", "bounce")
# The colour choices: these four by name, or any "#rrggbb" (rgb()).
COLORS = {"warm": (1.0, 0.55, 0.15), "cool": (0.3, 0.62, 1.0), "rose": (1.0, 0.38, 0.6), "white": (1.0, 1.0, 1.0)}
SOFT_MAX = 40.0         # Fusion Blur size at the swell's peak, Blur 1, softness 1
SWEEP = (0.2, 0.8)      # a leak's light crosses from here to here (shares of the width)
SEETHE = 2.5            # ... and churns this much on the way
TEAR_DISTANCE = 0.8     # frame widths each half of a tear goes
TEAR_DROP = 0.08        # ... and drops (frame heights), at Amount 1
TEAR_FRINGE = 0.008     # the torn white edge, map units, once open
TEAR_OVERLAP = 0.004    # the left half reaches under the right's edge: no seam at the start
STEP_RATE = 12.0        # grunge's steps a second
GLOW_SIZE = 30.0        # a burn's ember glow (Fusion Blur size)
TWIRL_SIZE = 1.4        # Vortex Size: past the frame's corners
RIPPLE_FREQ = 12.0      # Drip Frequency
RIPPLE_SPEED = 2.0      # waves (Drip Phase) run out over the transition
BULGE_SIZE = 1.4        # Dent Size: the swell reaches past the frame
BULGE_MAX = 1.3         # ... and past this Strength it turns inside out
PINCH_MAX = 0.3         # a pinch's Strength (negated): by here it's all one point
KALEIDO_OUT = 1.4       # the kaleidoscope zoomed this much: its corners would be black
WARP_SCALE = 5.0        # the warp noise's FastNoise XScale
SOFT_DISTORT = 12.0     # each side's Blur size at the peak, Blur 1
SLIDE_DISTANCE = 1.05   # a sliding piece's way (shares of the frame along it): off the frame, line and all
GAP = 0.01              # the colour line round each piece, frame widths, at Amount 1 and the swell's peak
PUSH = 0.15             # split open's incoming clip starts this much bigger
ASPECT = 16 / 9         # the frame's shape when none is given (the previews')
TWIST_DEGREES = 90.0    # a zoom spin's turn each side, at Amount 1
RAYS_CENTER_Y = 0.6     # light rays stream from here (shares of the height, y up)
FOCUS_MAX = 40.0        # a bokeh's Defocus size at the swell's peak, Blur 1
STAR_COVER = 3.2        # a star's sStar Width when the wipe's at 1: past the corners
CRT_LINE = 0.006        # a TV switching off: the line's height...
CRT_DOT = 0.012         # ... and the dot's width (shares of the frame)
TURN_PERSPECTIVE = 1.2  # DVE Perspective (frame widths) at Amount 1: the camera's distance
SHADE = 0.45            # a door's or fall's picture behind starts this much darker
PAGE_FLAT = 2.5         # a page turns with the camera this much further off (it lifts toward it)...
PAGE_SHADE = 0.35       # ... and darkens this much standing up
NEST_STEP = 0.5         # a tunnel's copies: each this size of the one around it...
NEST_COPIES = 5         # ... this many
ROLL_DEGREES = 40.0     # a roll's swing each side, at Amount 1...
ROLL_PIVOT = -1.5       # ... round this point (share of the height, y up: well below the frame)
BLUR_ROLL = 0.5 * (0.5 - ROLL_PIVOT) * math.pi / 180 / ASPECT     # its Linear blur per degree (the frame's middle)
BAND_WIDTH, BAND_SOFT, BAND_ANGLE = 0.12, 0.06, 30.0              # a light sweep's band (frame shares, degrees)
BAND_FROM, BAND_TO = -0.3, 1.3                                    # ... crossing from here to here
BLINDS = 8              # slats down the frame
HALFTONE = 16           # halftone squares across
HEX_COVER = 1.3         # a shutter's sNGon Width wide open: past the corners
IRIS_COVER = 1.16       # an iris's Ellipse Width (frame widths, as its Height) wide open: just past the corners
DIAMOND_COVER = 1.6     # a diamond's sNGon Width (tip to tip, frame widths) when the wipe's at 1: past the corners
HEART_COVER = 1.25      # a heart's size (the side of its square, frame widths) when the wipe's at 1: past the corners
ZIGZAG = 8              # a zigzag's teeth down the frame
FILM_PITCH = 0.125      # a film roll's sprocket holes: one every this much of the height (at full size)...
FILM_DUTY = 0.45        # ... each this share of that tall...
FILM_HOLE_X = 0.5625    # ... their columns' middles this far either side of the strip's (frame widths)...
FILM_HOLE_W = 0.045     # ... and this wide - all shrinking with the strip
FILM_HOLE = (0.86, 0.85, 0.82)   # ... and their colour (the light through them)
GLASS_CELLS = 10        # glass blocks across
LOUVERS = 6             # louver slats down the frame
PUSH_GAP = 0.04         # a zoom push's gap between screens (frame widths)

# How far each move goes at Amount 1.
ZOOM_SCALE = 3.0        # a's picture at the cut: 3x (zoom in) or a third (zoom out)
PAN_DISTANCE = 1.0      # frame widths (or heights) a whip travels each side
SPIN_DEGREES = 180.0    # each side: a turn and a half... a half turn out, a half in
STRETCH_SCALE = 4.0     # along the axis, at the cut
SHAKE_SIZE = 0.05       # of the frame, at the cut

# Blur at Blur 1: Fusion Directional Blur's Length for the picture's move in
# one frame (a 180-degree shutter: half of it). Calibrated against renders.
BLUR_PAN = 0.5          # per frame width moved
BLUR_ZOOM = 0.5         # per unit of ln(scale)
BLUR_SPIN = 0.5 / 360   # per degree turned (Radial Length: a share of a whole turn)
BLUR_MAX = 1.0

DEFAULTS = {"kind": "zoom_in", "feel": "ease", "length": 0.6, "amount": 1.0, "blur": 0.6, "color": "warm"}
LIMITS = {"length": (0.2, 3.0), "amount": (0.25, 2.0), "blur": (0.0, 1.0)}

BLUR_TYPES = {"pan": 0, "spin": 1, "twist": 1, "zoom": 3}     # Directional Blur Type: Linear, Radial, -, Zoom


_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def rgb(color):
    """A colour choice (COLORS' names or "#rrggbb") as (r, g, b), 0-1."""
    if color in COLORS:
        return COLORS[color]
    if isinstance(color, str) and _HEX.fullmatch(color):
        return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return COLORS[DEFAULTS["color"]]


def clean(settings):
    """Settings as the page sends them, made safe: DEFAULTS for anything
    missing or wrong, numbers held in LIMITS."""
    s = dict(DEFAULTS)
    settings = settings if isinstance(settings, dict) else {}
    if settings.get("kind") in KINDS:
        s["kind"] = settings["kind"]
    if settings.get("feel") in FEELS:
        s["feel"] = settings["feel"]
    color = settings.get("color")
    if color in COLORS or (isinstance(color, str) and _HEX.fullmatch(color)):
        s["color"] = color.lower() if color.startswith("#") else color
    for key, (low, high) in LIMITS.items():
        try:
            value = float(settings.get(key, s[key]))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            s[key] = min(max(value, low), high)
    return s


# ------------------------------------------------------------------ feels --

def _power(p, n):
    return 0.5 * (2 * p) ** n if p < 0.5 else 1 - 0.5 * (2 - 2 * p) ** n


def _bounce_out(u):
    """0 -> 1, landing and bouncing twice, smaller each time."""
    n, d = 7.5625, 2.75
    if u < 1 / d:
        return n * u * u
    if u < 2 / d:
        u -= 1.5 / d
        return n * u * u + 0.75
    if u < 2.5 / d:
        u -= 2.25 / d
        return n * u * u + 0.9375
    u -= 2.625 / d
    return n * u * u + 0.984375


def feel(p, how):
    """D(p): the move's progress at p (0-1 through the transition), 0.5 at
    the cut. Ease: smooth in and out. Hit: a long run-up, a sharp strike at
    the cut, a quick stop. Swing: pulls back first and overshoots after.
    Bounce: speeds in, lands and bounces."""
    p = min(max(p, 0.0), 1.0)
    if how == "hit":
        return _power(p, 5)
    if how == "swing":
        c = 1.70158 * 1.525
        if p < 0.5:
            return ((2 * p) ** 2 * ((c + 1) * 2 * p - c)) / 2
        return ((2 * p - 2) ** 2 * ((c + 1) * (2 * p - 2) + c) + 2) / 2
    if how == "bounce":
        return 0.5 * (2 * p) ** 3 if p < 0.5 else 0.5 + 0.5 * _bounce_out(2 * p - 1)
    return _power(p, 3)


# ------------------------------------------------------------------ shapes --

def _shake_noise(t, seed):
    """Smooth-ish jitter, -1..1: a few sines at unrelated rates."""
    return (math.sin(t * 61.0 + seed) * 0.5 + math.sin(t * 97.0 + seed * 2.1) * 0.3
            + math.sin(t * 151.0 + seed * 3.7) * 0.2)


def _pose(s, p):
    """(side, sx, sy, cx, cy, degrees) at p: the side showing ("a" before
    the cut, "b" from it) and its picture's sizes, middle and turn."""
    kind = KINDS[s["kind"]]
    move, amount = kind["move"], s["amount"]
    side = "a" if p < 0.5 else "b"
    d = feel(p, s["feel"])
    w = 2 * d if side == "a" else 2 * d - 2
    sx = sy = 1.0
    cx = cy = 0.5
    deg = 0.0
    if move == "zoom":
        sign = -kind["sign"] if kind.get("back") and side == "b" else kind["sign"]
        scale = (1 + (ZOOM_SCALE - 1) * amount) ** (sign * w)
        sx = sy = scale
        if kind.get("jolt"):
            jolt = kind["jolt"] * amount * math.sin(math.pi * p) ** 6
            cx += jolt * _shake_noise(p, 1.3)
            cy += jolt * _shake_noise(p, 4.7)
    elif move == "pan":
        dx, dy = kind["dir"]
        cx += w * PAN_DISTANCE * amount * dx
        cy += w * PAN_DISTANCE * amount * dy
    elif move == "spin":
        deg = w * SPIN_DEGREES * amount * kind["sign"]
    elif move == "twist":
        sx = sy = (1 + (ZOOM_SCALE - 1) * amount) ** (kind["sign"] * w)
        deg = w * TWIST_DEGREES * amount * kind["sign"]
    elif move == "roll":
        deg = w * ROLL_DEGREES * amount * kind["sign"]
    elif move == "stretch":
        stretch = (1 + (STRETCH_SCALE - 1) * amount) ** abs(w)
        if kind["axis"] == "x":
            sx = stretch
        else:
            sy = stretch
    elif move == "shake":
        envelope = math.sin(math.pi * p) ** (2 if s["feel"] != "hit" else 6)
        size = SHAKE_SIZE * amount * envelope
        cx += size * _shake_noise(p, 1.3)
        cy += size * _shake_noise(p, 4.7)
        deg = 3.0 * amount * envelope * _shake_noise(p, 8.1)
        sx = sy = 1 + 2 * size
    return side, sx, sy, cx, cy, deg


def burn_dark(color):
    """A film burn's darks: its colour deepened - red under warm, blue under cool."""
    r, g, b = rgb(color)
    return (r * r * 0.55, g * g * 0.25, b * b * 0.3)


def char(color):
    """A burn's charred edge: near black, warmed by its colour."""
    r, g, b = rgb(color)
    return (r * 0.12, g * 0.08, b * 0.06)


def group(kind):
    return KINDS[kind].get("group", "motion")


def swell(p, how):
    """The light group's swell at p: 0 at the ends, 1 at the cut."""
    p = min(max(p, 0.0), 1.0)
    bell = math.sin(math.pi * p)
    if how == "hit":
        return bell ** 8
    if how == "swing":
        return bell
    if how == "bounce":
        return bell ** 2 * (0.7 + 0.3 * math.cos(12 * math.pi * p))
    return bell ** 2


def light_at(settings, p):
    """The light group at p: {"mix" (0 all the outgoing clip, 1 all the
    incoming), "gain" (each side's brightness, x), "soft" (each side's Blur
    size), "glow" (the light's strength over it, 0-1), "sweep" (the light's
    middle, share of the width), "seethe", "fx" (rays', bloom's strength,
    0-1), "focus" (a bokeh's Defocus size), "band" (a light sweep's middle,
    share of the width)}."""
    s = settings
    kind = KINDS[s["kind"]]
    e = swell(p, s["feel"]) ** kind["sharp"]
    if kind["light"] == "dip":       # whole at the cut whatever the Amount, which holds it longer
        e = swell(p, s["feel"]) ** (1 / s["amount"])
    fade = kind["fade"]
    if fade <= 0:
        mix = 0.0 if p < 0.5 else 1.0
    else:
        u = min(max((p - (0.5 - fade)) / (2 * fade), 0.0), 1.0)
        mix = u * u * (3 - 2 * u)
    amount = s["amount"]
    return {"mix": mix, "gain": 1 + kind["gain"] * amount * e, "soft": SOFT_MAX * kind["soft"] * s["blur"] * e,
            "glow": e if kind["light"] == "dip" else min(1.0, kind["glow"] * amount * e),
            "sweep": SWEEP[0] + (SWEEP[1] - SWEEP[0]) * feel(p, "ease"), "seethe": SEETHE * p,
            "fx": min(1.0, amount * e), "focus": FOCUS_MAX * s["blur"] * e,
            "band": BAND_FROM + (BAND_TO - BAND_FROM) * feel(p, s["feel"])}


def wipe_at(settings, p):
    """The wipe group at p: {"m1" (the colour band's front), "m2" (the
    incoming clip's), "soft"} - map values; a pixel shows the incoming clip
    where the map is below m2, the band below m1, the outgoing clip above.
    Both run from below the map's darkest to above its lightest, so the
    reveal starts and ends clean. A shutter adds "mix" (the clip showing),
    "open" (the aperture, 0 shut - 1 wide open) and "turn" (degrees)."""
    s = settings
    kind = KINDS[s["kind"]]
    soft = EDGE_MIN + kind["edge"] * s["blur"]
    band = kind["band"] * s["amount"]
    prog = min(max(feel(p, s["feel"]), 0.0), 1.0)
    m1 = -soft + (1 + band + 2 * soft) * prog
    if kind.get("map") == "shutter":
        # Closing on the outgoing clip to the cut, opening on the incoming one; the blades turn all the way.
        return {"m1": m1, "m2": m1 - band, "soft": soft, "mix": 0.0 if p < 0.5 else 1.0,
                "open": min(1.0, abs(2 * feel(p, s["feel"]) - 1)), "turn": 60.0 * feel(p, s["feel"])}
    return {"m1": m1, "m2": m1 - band, "soft": soft}


def _dice(k, salt):
    """A throw of the dice for frame k: 0-1, the same every time (and in
    transitions.js)."""
    v = math.sin(k * 12.9898 + salt * 78.233) * 43758.5453
    return v - math.floor(v)


def glitch_at(settings, p, frames=24):
    """The glitch group at p: {"mix" (0 the outgoing clip, 1 the incoming),
    "split" (frame widths, + red right / blue left), "tear" (Displace
    refraction), "seethe" (where the bands fall), "gain", "sat",
    "scan", "grain", "block" (pixels at 1920 wide; 1 = none), "jx", "jy" (the
    picture jumped, frame widths), "neg" (1: negative)} - each frame
    (k) its own dice. A pixelate: {"mix", "block"}; a TV switch: {"mix",
    "sx", "sy" (the picture's size), "gain"}."""
    s = settings
    kind = KINDS[s["kind"]]
    if kind.get("style") == "pixelate":
        e = swell(p, s["feel"])
        return {"mix": _crossfade(p, kind["fade"]),
                "block": float(max(1, round(1 + (kind["block"] * s["amount"] - 1) * e)))}
    if kind.get("style") == "crt":
        # Each side's way to off (t 0 -> 1): the outgoing clip's to the cut,
        # the incoming one's back from it - the feel paces it, so an ease
        # squashes slowly and goes out quickly.
        d = feel(p, s["feel"])
        t = min(max(2 * d if p < 0.5 else 2 - 2 * d, 0.0), 1.0)
        squash, narrow = min(1.0, t / 0.6), min(max((t - 0.6) / 0.3, 0.0), 1.0)
        out = min(max((t - 0.9) / 0.1, 0.0), 1.0)
        return {"mix": 0.0 if p < 0.5 else 1.0, "sx": 1 - (1 - CRT_DOT) * narrow, "sy": 1 - (1 - CRT_LINE) * squash,
                "gain": (1 + 2 * s["amount"] * squash) * (1 - out)}
    k = min(frames - 1, max(0, math.floor(p * frames)))
    e = swell(p, s["feel"])
    hit = e * (0.4 + 0.6 * _dice(k, 1))            # how hard this frame is hit
    amount = s["amount"]
    w = STUTTER * kind["stutter"]
    if w <= 0:
        mix = 0.0 if p < 0.5 else 1.0
    else:
        # Across the middle of the window the clips swap every frame; either side, one or the other.
        q = min(max((p - (0.5 - w)) / (2 * w), 0.0), 1.0)
        mix = float(k % 2) if 0.15 < q < 0.85 else (1.0 if q >= 0.5 else 0.0)
    side = -1 if _dice(k, 2) < 0.5 else 1
    block = 1.0
    if kind["block"] > 0 and _dice(k, 6) < e:
        block = max(1.0, float(round(kind["block"] * amount * (0.3 + 0.7 * _dice(k, 7)))))
    jump = kind.get("jump", 0.0) * amount * hit
    return {"mix": mix, "split": kind["split"] * amount * hit * side, "tear": kind["tear"] * amount * hit,
            "seethe": 3.7 * k, "gain": 1 + kind["flicker"] * amount * e * (_dice(k, 4) * 2 - 1),
            "sat": 1 - kind["fade"] * e, "scan": kind["scan"] * e, "grain": kind["grain"] * e, "block": block,
            "jx": jump * (_dice(k, 21) * 2 - 1), "jy": jump * 0.5 * (_dice(k, 22) * 2 - 1),
            "neg": 1.0 if _dice(k, 23) < kind.get("invert", 0.0) * e else 0.0}


def paper_at(settings, p):
    """The paper group at p. A tear: {"open" (0-1, more on a bounce),
    "lx", "ly", "la", "rx", "ry", "ra" (each half's middle, y up, and
    degrees), "fringe"}. The rest, as wipe_at: {"m0" (a burn's charred
    front; the band's otherwise), "m1", "m2", "soft", "dirt", "jx", "jy"
    (the picture's jitter), "seethe" (where grunge's noise boils to)}."""
    s = settings
    kind = KINDS[s["kind"]]
    amount = s["amount"]
    if kind["style"] == "tear":
        d = max(0.0, feel(p, s["feel"]))
        drop = TEAR_DROP * amount * d
        tilt = kind["tilt"] * amount * d
        return {"open": d, "lx": 0.5 - TEAR_DISTANCE * d, "ly": 0.5 - drop, "la": tilt,
                "rx": 0.5 + TEAR_DISTANCE * d, "ry": 0.5 - drop, "ra": -tilt,
                "fringe": TEAR_FRINGE * min(1.0, 8 * d)}
    out = {"dirt": 0.0, "jx": 0.0, "jy": 0.0, "seethe": 0.0}
    if kind["style"] == "grunge":
        # Held a step at a time - the first step the start, the last the end.
        steps = max(4, math.floor(s["length"] * STEP_RATE + 0.5))
        j = min(steps - 1, math.floor(p * steps))
        p = j / (steps - 1)
        e = swell(p, s["feel"])
        out = {"dirt": kind["dirt"] * e, "jx": (_dice(j, 8) * 2 - 1) * kind["jitter"] * amount * e,
               "jy": (_dice(j, 9) * 2 - 1) * kind["jitter"] * amount * e, "seethe": 1.7 * j}
    if kind["style"] == "burn":
        soft = EDGE_MIN + kind["edge"] * s["blur"]
        band, char = kind["band"] * amount, kind["char"] * amount
        prog = min(max(feel(p, s["feel"]), 0.0), 1.0)
        m0 = -soft * 1.5 + (1 + soft * 2 + char + band) * prog
        return {"m0": m0, "m1": m0 - char, "m2": m0 - char - band, "soft": soft, **out}
    v = wipe_at(s, p)
    return {"m0": v["m1"], **v, **out}


def _crossfade(p, fade):
    if fade <= 0:
        return 0.0 if p < 0.5 else 1.0
    u = min(max((p - (0.5 - fade)) / (2 * fade), 0.0), 1.0)
    return u * u * (3 - 2 * u)


def distort_at(settings, p):
    """The distortion group at p: {"mix", "soft"} and the style's own - a
    twirl's "angle_a" / "angle_b" (each side's, one turn carried across
    the cut), a ripple's "amp" and "phase", a kaleidoscope's "kaleido" (how
    much of it shows), "spin" and "zoom", a bulge's or pinch's "strength",
    a warp's "refract" and "seethe"."""
    s = settings
    kind = KINDS[s["kind"]]
    amount = s["amount"]
    e = swell(p, s["feel"])
    out = {"mix": _crossfade(p, kind["fade"]), "soft": SOFT_DISTORT * s["blur"] * e}
    style = kind["style"]
    if style == "twirl":
        d = feel(p, s["feel"])
        out.update(angle_a=kind["degrees"] * amount * 2 * d, angle_b=kind["degrees"] * amount * (2 * d - 2))
    elif style in ("ripple", "wave"):
        out.update(amp=kind["amp"] * amount * e, phase=RIPPLE_SPEED * p)
    elif style == "kaleido":
        out.update(kaleido=min(1.0, 2 * e), spin=kind["spin"] * amount * (feel(p, s["feel"]) - 0.5),
                   zoom=1 + kind["zoom"] * amount * e)
    elif style == "bulge":
        out.update(strength=min(BULGE_MAX, kind["strength"] * amount) * e)
    elif style == "pinch":
        out.update(strength=min(PINCH_MAX, kind["strength"] * amount) * e)
    elif style == "glass":
        out.update(refract=kind["refract"] * amount * e)
    elif style == "lens":
        # A zoom carried across the cut (as the motion group's), bulging at the peak.
        d = feel(p, s["feel"])
        out.update(zoom_a=kind["zoom"] ** (2 * d), zoom_b=kind["zoom"] ** (2 * d - 2),
                   strength=min(BULGE_MAX, kind["strength"] * amount) * e)
    else:
        out.update(refract=kind["refract"] * amount * e, seethe=SEETHE * p)
    return out


def pieces(kind):
    """The rectangles a split-screen kind cuts the picture into, in reading
    order: {"home": (x, y, w, h) - middle and size, shares of the frame, y
    up - "dir": (dx, dy), the way it slides (in from, or out to), "order":
    0-1, when it goes among the rest, "src": whose picture, "a" leaving or
    "b" arriving; a diagonal split's "half"}."""
    k = KINDS[kind]
    if k.get("cut") == "diag":
        # The halves either side of the corner-to-corner line ("half": the
        # upper left, the lower right), whole-frame pieces cut by it.
        return [{"home": (0.5, 0.5, 1.0, 1.0), "dir": d, "order": 0.0, "src": k["src"], "half": half}
                for half, d in (("ul", (-1, 1)), ("lr", (1, -1)))]
    cols, rows = k["layout"]
    n = cols * rows
    ranked = sorted(range(n), key=lambda j: _dice(j, 31))
    out = []
    for i in range(n):
        row, col = divmod(i, cols)
        way = k.get("from")
        if way == "apart":
            d = (-1, 0) if col < cols / 2 else (1, 0)
        elif way == "sides":
            d = (-1, 0) if row % 2 == 0 else (1, 0)
        elif way == "updown":
            d = (0, -1) if col % 2 == 0 else (0, 1)
        elif way == "random":
            d = ((-1, 0), (1, 0), (0, -1), (0, 1))[min(3, math.floor(_dice(i, 32) * 4))]
        else:
            d = tuple(way) if way else (0, 0)
        if k["order"] == "wave":
            order = (col + row) / max(1, cols + rows - 2)
        elif k["order"] == "random":
            order = ranked.index(i) / max(1, n - 1)
        elif k["order"] == "checker":
            order = float((col + row) % 2)
        else:
            order = i / max(1, n - 1)
        home = ((col + 0.5) / cols, 1 - (row + 0.5) / rows, 1 / cols, 1 / rows)
        if k["src"] == "pair":      # the outgoing piece leaves the way given, the incoming one follows it in
            out.append({"home": home, "dir": d, "order": order, "src": "a"})
            out.append({"home": home, "dir": (-d[0], -d[1]), "order": order, "src": "b"})
        else:
            out.append({"home": home, "dir": d, "order": order, "src": k["src"]})
    return out


def _smooth(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def _share(kind, piece, p, how):
    """A squeeze's piece's share of the frame at p: the outgoing one's
    shrinking as the incoming one's grows."""
    q = min(max(feel(p, how), 0.0), 1.0)
    return 1 - q if piece["src"] == "a" else q


def _squash(kind, piece, p, how):
    """(across, down): how much a piece is squashed each way (a squeeze's,
    along its way; 1 otherwise)."""
    if kind["move"] != "squeeze":
        return 1.0, 1.0
    share = _share(kind, piece, p, how)
    return (share, 1.0) if piece["dir"][0] else (1.0, share)


def _piece(kind, piece, p, how):
    """(x, y, scale) of a piece at p: its middle (y up) and its size."""
    hx, hy = piece["home"][:2]
    if kind["move"] == "squeeze":
        away = (1 - _share(kind, piece, p, how)) / 2
        return hx + piece["dir"][0] * away, hy + piece["dir"][1] * away, 1.0
    if kind["move"] == "zoompush":
        # Shrinks over the first third, slides (at the feel) over the middle, grows over the last.
        z = 1 - (1 - kind["scale"]) * (_smooth(p / 0.35) - _smooth((p - 0.65) / 0.35))
        q = feel((p - 0.3) / 0.4, how)
        away = (q if piece["src"] == "a" else 1 - q) * (z + PUSH_GAP)
        return hx + piece["dir"][0] * away, hy + piece["dir"][1] * away, z
    d = feel((p - kind["spread"] * piece["order"]) / (1 - kind["spread"]), how)
    if kind["move"] == "grow":
        return hx, hy, max(0.0, d)
    away = (d if piece["src"] == "a" else 1 - d) * kind.get("distance", SLIDE_DISTANCE)
    return hx + piece["dir"][0] * away, hy + piece["dir"][1] * away, 1.0


def split_at(settings, p, frames=24, aspect=ASPECT):
    """The split-screen group at p: each piece's (pieces()) "x", "y" (its
    middle), "s" (scale), "sw", "sh" (squashed across, down: _squash) and
    "blur" (Directional Blur Length, from its move
    a frame back - the plan's frames are 1 / (frames - 1) apart), lists;
    "border" (the colour line, frame widths) and "push" (the clip under
    the pieces' scale); a film roll's "hole_y" (where a sprocket hole's
    top is, y up), "hole_p" (their pitch, shares of the height), "hole_x",
    "hole_w" (their columns' offset and width, shares of the width)."""
    s = settings
    kind = KINDS[s["kind"]]
    back = max(0.0, p - 1.0 / max(1, frames - 1))
    xs, ys, sizes, blurs, across, down = [], [], [], [], [], []
    for piece in pieces(s["kind"]):
        x, y, z = _piece(kind, piece, p, s["feel"])
        sw, sh = _squash(kind, piece, p, s["feel"])
        across.append(sw)
        down.append(sh)
        ox, oy, oz = _piece(kind, piece, back, s["feel"])
        if kind["move"] == "grow":
            speed = abs(z - oz) * BLUR_ZOOM
        else:
            speed = math.hypot(x - ox, (y - oy) / aspect) * BLUR_PAN
        xs.append(x)
        ys.append(y)
        sizes.append(z)
        blurs.append(min(BLUR_MAX, speed * s["blur"]))
    push = 1 + PUSH * (1 - feel(p, s["feel"])) if kind.get("push") else 1.0
    out = {"x": xs, "y": ys, "s": sizes, "sw": across, "sh": down, "blur": blurs,
           "border": 0.0 if kind.get("film") else GAP * s["amount"] * swell(p, s["feel"]), "push": push}
    if kind.get("film"):
        # The holes ride with the outgoing frame and shrink with it.
        z = sizes[0]
        out.update(hole_y=ys[0], hole_p=FILM_PITCH * z, hole_x=FILM_HOLE_X * z, hole_w=FILM_HOLE_W * z)
    return out


def turn_rig(kind, persp, aspect=ASPECT):
    """A 3D kind's DVE set-up: {"pivot" (shares, y up), "zpivot" (frame
    widths behind the pivot; negative in front), "zmove"}. Measured on
    21.1: the camera sits Perspective x Z Move in front of the pivot, the
    picture Z Pivot behind it, drawn at Perspective / its distance - so a
    cube's face (half a side in front of its middle) is full size with Z
    Move (Perspective + half) / Perspective."""
    k = KINDS[kind]
    if k["style"] == "cube":
        half = 0.5 if k["axis"] == "y" else 0.5 / aspect
        return {"pivot": (0.5, 0.5), "zpivot": -half, "zmove": (persp + half) / persp}
    return {"pivot": k.get("hinge", (0.5, 0.5)), "zpivot": 0.0, "zmove": 1.0}


def turn_at(settings, p):
    """The 3D group at p: {"rot_a", "rot_b" (each side's turn, degrees -
    DVE's Y Rotation, or X for an "x" axis), "show_a", "show_b" (0 or 1),
    "persp" (DVE Perspective), "shade" (the picture behind's gain - a
    page's own, as it turns), "pull" (Z Move's share of turn_rig's, the
    camera pulled back)}."""
    s = settings
    kind = KINDS[s["kind"]]
    d = feel(p, s["feel"])
    sign = kind["sign"]
    shade = 1.0
    if kind["style"] == "flip":
        # One half turn carried across: the outgoing card edge-on at the cut, the incoming one from edge-on.
        a, b = (180 * d, -90.0) if p < 0.5 else (90.0, 180 * d - 180)
        show_a, show_b = (1.0, 0.0) if p < 0.5 else (0.0, 1.0)
    elif kind["style"] == "cube":
        a, b = 90 * d, 90 * d - 90
        show_a = show_b = 1.0
    elif kind["style"] == "louver":
        # Each slat a flip of its own, the top one first: lists, a slat each.
        a, b, show_a, show_b = [], [], [], []
        for i in range(LOUVERS):
            q = feel((p - kind["spread"] * i / (LOUVERS - 1)) / (1 - kind["spread"]), s["feel"])
            turn = 180 * q
            first = turn < 90
            a.append((turn if first else 90.0) * sign)
            b.append((-90.0 if first else turn - 180) * sign)
            show_a.append(1.0 if first else 0.0)
            show_b.append(0.0 if first else 1.0)
        return {"rot_a": a, "rot_b": b, "show_a": show_a, "show_b": show_b,
                "persp": TURN_PERSPECTIVE / s["amount"], "shade": 1.0, "pull": 1.0}
    elif kind["style"] == "page":
        # The outgoing clip's right half lifts off the spine and over; its
        # back - the incoming one's left half - lands on the left.
        turn = min(max(180 * d, 0.0), 180.0)
        a, b = -min(turn, 90.0), 180 - max(turn, 90.0)
        show_a, show_b = (1.0 if turn < 90 else 0.0), (1.0 if turn >= 90 else 0.0)
        shade = 1 - PAGE_SHADE * math.sin(math.radians(turn))       # the page darkens as it stands up
    else:
        a, b = min(90.0, 90 * d), 0.0
        show_a, show_b = (1.0 if a < 90 else 0.0), 1.0
        shade = 1 - SHADE * (1 - min(max(d, 0.0), 1.0))
    flat = PAGE_FLAT if kind["style"] == "page" else 1.0
    return {"rot_a": a * sign, "rot_b": b * sign, "show_a": show_a, "show_b": show_b,
            "persp": TURN_PERSPECTIVE * flat / s["amount"], "shade": shade,
            "pull": 1 + kind.get("pull", 0.0) * swell(p, s["feel"])}


def at(settings, p, frames=24):
    """What the transition shows at p: {"side", "sx", "sy", "cx", "cy",
    "deg", "blur_type", "blur", "blur_angle", "nest" (a tunnel's copies'
    strength), "chroma", "flash" (a punch's gain)} - blur as Directional Blur's
    Length, from how far the picture moves in one of the `frames` frames
    (measured on its own side of the cut: a frame back before it, on after)."""
    s = settings
    if group(s["kind"]) == "light":
        return {"side": "a" if p < 0.5 else "b", **light_at(s, p)}
    if group(s["kind"]) == "wipe":
        return {"side": "a" if p < 0.5 else "b", **wipe_at(s, p)}
    if group(s["kind"]) == "glitch":
        return {"side": "a" if p < 0.5 else "b", **glitch_at(s, p, frames)}
    if group(s["kind"]) == "paper":
        return {"side": "a" if p < 0.5 else "b", **paper_at(s, p)}
    if group(s["kind"]) == "distort":
        return {"side": "a" if p < 0.5 else "b", **distort_at(s, p)}
    if group(s["kind"]) == "split":
        return {"side": "a" if p < 0.5 else "b", **split_at(s, p, frames)}
    if group(s["kind"]) == "turn":
        return {"side": "a" if p < 0.5 else "b", **turn_at(s, p)}
    side, sx, sy, cx, cy, deg = _pose(s, p)
    kind = KINDS[s["kind"]]
    move = kind["move"]
    step = 1.0 / max(1, frames)
    other = _pose(s, max(0.0, p - step) if side == "a" else min(1.0, p + step))[1:]
    osx, osy, ocx, ocy, odeg = other
    angle = 0.0
    if move == "zoom":
        blur = abs(math.log(sx) - math.log(osx)) * BLUR_ZOOM
    elif move == "spin":
        blur = abs(deg - odeg) * BLUR_SPIN
    elif move == "roll":                 # swung round a far point: sideways, mostly
        blur = abs(deg - odeg) * BLUR_ROLL
    elif move == "twist":                # turning and growing: the bigger of the two smears, round
        blur = max(abs(deg - odeg) * BLUR_SPIN, abs(math.log(sx) - math.log(osx)) * BLUR_ZOOM * 0.5)
    elif move == "stretch":
        axis_now, axis_then = (sx, osx) if kind["axis"] == "x" else (sy, osy)
        blur = abs(math.log(axis_now) - math.log(axis_then)) * BLUR_ZOOM
        angle = 0.0 if kind["axis"] == "x" else 90.0
    else:                               # pan, shake: along the way it's going
        blur = math.hypot(cx - ocx, cy - ocy) * BLUR_PAN
        if move == "pan":
            angle = math.degrees(math.atan2(kind["dir"][1], kind["dir"][0]))
        elif blur > 0:
            angle = math.degrees(math.atan2(cy - ocy, cx - ocx))
    e = swell(p, s["feel"])
    return {"side": side, "sx": sx, "sy": sy, "cx": cx, "cy": cy, "deg": deg,
            "blur_type": BLUR_TYPES.get(move, 0), "blur": min(BLUR_MAX, blur * s["blur"]), "blur_angle": angle,
            "nest": e if kind.get("nest") else 0.0, "chroma": kind.get("chroma", 0.0) * s["amount"] * e,
            "flash": 1 + kind.get("flash", 0.0) * s["amount"] * swell(p, "hit")}


def cut_frame(frames):
    """The first frame of the incoming clip: frame f shows p = (f + 0.5) / frames."""
    return next(f for f in range(frames + 1) if f == frames or (f + 0.5) / frames >= 0.5)


def plan(settings, frames, aspect=ASPECT):
    """Every frame's values, for Fusion: {"a": {input: [per frame]}, "b":
    ..., "cut": cut_frame, "blur_type", "blur_angle"}. A side's values hold
    still where the other side is showing (on its last / first value), so
    nothing it does then is ever seen. The other groups: {"group", and
    their own values per frame}; split screen's "pieces" each a pieces()
    entry with its "x", "y", "s", "blur" per frame, on a frame `aspect`
    wide to its height."""
    s = clean(settings)
    frames = max(2, int(frames))
    cut = cut_frame(frames)
    if group(s["kind"]) == "light":
        rows = [light_at(s, (f + 0.5) / frames) for f in range(frames)]
        return {"cut": cut, "group": "light", **{k: [r[k] for r in rows] for k in rows[0]}}
    if group(s["kind"]) == "wipe":
        rows = [wipe_at(s, (f + 0.5) / frames) for f in range(frames)]
        return {"cut": cut, "group": "wipe", **{k: [r[k] for r in rows] for k in rows[0]}}
    if group(s["kind"]) == "glitch":
        rows = [glitch_at(s, (f + 0.5) / frames, frames) for f in range(frames)]
        return {"cut": cut, "group": "glitch", **{k: [r[k] for r in rows] for k in rows[0]}}
    if group(s["kind"]) == "paper":
        rows = [paper_at(s, (f + 0.5) / frames) for f in range(frames)]
        return {"cut": cut, "group": "paper", **{k: [r[k] for r in rows] for k in rows[0]}}
    if group(s["kind"]) == "distort":
        rows = [distort_at(s, (f + 0.5) / frames) for f in range(frames)]
        return {"cut": cut, "group": "distort", **{k: [r[k] for r in rows] for k in rows[0]}}
    if group(s["kind"]) == "turn":
        rows = [turn_at(s, f / (frames - 1)) for f in range(frames)]
        return {"cut": cut, "group": "turn", **{k: [r[k] for r in rows] for k in rows[0]}}
    if group(s["kind"]) == "split":
        # First frame to last, 0 to 1: the outgoing picture whole, the incoming one whole.
        rows = [split_at(s, f / (frames - 1), frames, aspect) for f in range(frames)]
        shapes = pieces(s["kind"])
        film = {k: [r[k] for r in rows] for k in ("hole_y", "hole_p", "hole_x", "hole_w") if k in rows[0]}
        return {"cut": cut, "group": "split", "border": [r["border"] for r in rows], "push": [r["push"] for r in rows],
                **film,
                "pieces": [{**shape, **{k: [r[k][i] for r in rows] for k in ("x", "y", "s", "sw", "sh", "blur")}}
                           for i, shape in enumerate(shapes)]}
    rows = [at(s, (f + 0.5) / frames, frames) for f in range(frames)]
    out = {"cut": cut, "blur_type": rows[0]["blur_type"], "blur_angle": rows[0]["blur_angle"]}
    for side in ("a", "b"):
        mine = [r for r in rows if r["side"] == side] or rows[-1:]
        first, last = mine[0], mine[-1]
        fill = [r if r["side"] == side else (last if side == "a" else first) for r in rows]
        out[side] = {
            "XSize": [r["sx"] for r in fill], "YSize": [r["sy"] for r in fill],
            "Center.X": [r["cx"] for r in fill], "Center.Y": [r["cy"] for r in fill],
            "Angle": [r["deg"] for r in fill],
            "Length": [r["blur"] if r["side"] == side else 0.0 for r in rows],
            "BlurAngle": [r["blur_angle"] for r in fill],
            "Nest": [r["nest"] for r in rows], "Chroma": [r["chroma"] for r in rows],
            "Flash": [r["flash"] for r in rows],
        }
    return out
