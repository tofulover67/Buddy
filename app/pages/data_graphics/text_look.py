"""The text of a Data Graphics comp: one Text+ per number or label, written for
core/fusion_comp.py - and the look every counter and graph shares (font, colour,
outline, shadow). Pure: no Resolve and no Qt.

Where the text goes, from px in the frame (top-down):
  Size     the letter size over the face's px per Size (metrics["em"]: Text+'s Size is
           a share of the frame's WIDTH)
  Center   across: x itself - HorizontalLeftCenterRight says which side of it the text
           runs (-1: starts there, 0: centred on it, 1: ends there; measured on Studio
           21.1.2 - HorizontalJustificationNew moved nothing); down: the baseline less
           the face's baseline offset (metrics["baseline"]), flipped to Fusion's y-up
"""

from core import fusion_comp as fc

from .easing import colour

ALIGN = {"left": -1, "center": 0, "right": 1}
OUTLINE_W = 0.06            # an outline's reach, as a share of the letter size
SHADOW_DIST = 0.06          # a shadow's distance, the same way, down and to the right
SHADOW_SOFT = 4.0           # its Softness
CAP = 0.7                   # a capital's or a digit's height, as a share of the letter size - about
LOOK_DEFAULTS = {"font": "Arial", "style": "Bold", "colour": "#ffffff", "outline": False,
                 "outline_colour": "#000000", "shadow": False}
MAX_NAME = 80


def clean_look(raw, defaults=LOOK_DEFAULTS):
    raw = raw or {}
    return {
        "font": str(raw.get("font") or defaults["font"])[:MAX_NAME],
        "style": str(raw.get("style") or defaults["style"])[:MAX_NAME],
        "colour": colour(raw.get("colour"), defaults["colour"]),
        "outline": bool(raw.get("outline", defaults["outline"])),
        "outline_colour": colour(raw.get("outline_colour"), defaults["outline_colour"]),
        "shadow": bool(raw.get("shadow", defaults["shadow"])),
    }


def clean_metrics(raw):
    """{"em": px of letter size per (Size x width), "baseline": ems the baseline sits
    below the Center} - the page measures the face; these stand in when it couldn't."""
    raw = raw or {}

    def within(key, fallback, low, high):
        try:
            v = float(raw.get(key))
        except (TypeError, ValueError):
            return fallback
        return v if low <= v <= high else fallback
    return {"em": within("em", fc.EM_PER_SIZE, 0.2, 2.0), "baseline": within("baseline", fc.TEXT_BASELINE, 0.0, 1.5)}


def text_plus(name, frame, comp_frames, look, metrics, size_px, x, baseline_y, pos, text=None, keyed=None,
              center=None, align="center", turned=False):
    """A Text+ of the look's face. text: what it says - or keyed: the spline its text is
    keyed by (fusion_comp.text_spline). x, baseline_y: where it sits, px in the frame -
    or center: the path its Center is keyed by (see center_point for its values).
    turned: it reads bottom to top (a title up the side of a graph) - x is then where
    its baseline runs, the letters' feet towards the right, and baseline_y its middle."""
    fw, fh = frame
    em = metrics["em"]
    inputs = [fc.input_("GlobalOut", comp_frames - 1), fc.input_("Width", fw), fc.input_("Height", fh),
              fc.input_("UseFrameFormatSettings", 1),
              fc.wired("StyledText", keyed) if keyed else fc.input_("StyledText", str(text or "")),
              fc.input_("Font", look["font"]), fc.input_("Style", look["style"]),
              fc.input_("Size", size_px / (em * fw)),
              fc.wired("Center", center) if center
              # Turned a quarter counter-clockwise about its Center, "below" is to the right.
              else fc.input_("Center", ((x - metrics["baseline"] * size_px) / fw, 1 - baseline_y / fh)) if turned
              else fc.input_("Center", center_point(frame, metrics, size_px, x, baseline_y)),
              *([fc.input_("AngleZ", 90)] if turned else []),
              fc.input_("HorizontalLeftCenterRight", ALIGN.get(align, 0))]
    r, g, b, a = fc.rgba(look["colour"])
    inputs += [fc.input_("Red1", r), fc.input_("Green1", g), fc.input_("Blue1", b), fc.input_("Alpha1", a)]
    if look.get("outline"):
        r, g, b, a = fc.rgba(look["outline_colour"], "#000000")
        inputs += [fc.input_("Enabled2", 1), fc.input_("ElementShape2", 1), fc.input_("OutsideOnly2", 1),
                   fc.input_("Thickness2", OUTLINE_W * em / fc.OUTLINE_REACH),
                   fc.input_("Red2", r), fc.input_("Green2", g), fc.input_("Blue2", b), fc.input_("Alpha2", a)]
    if look.get("shadow"):
        d = SHADOW_DIST * em
        inputs += [fc.input_("Enabled3", 1), fc.input_("Offset3", (d, -d)), fc.input_("SoftnessX3", SHADOW_SOFT),
                   fc.input_("SoftnessY3", SHADOW_SOFT), fc.input_("Red3", 0), fc.input_("Green3", 0),
                   fc.input_("Blue3", 0), fc.input_("Alpha3", 0.7)]
    return fc.block(name, "TextPlus", inputs, pos)


def arriving(name, upstream, frame, states, size_px, pivot, pos):
    """A text's arriving after its Text+ (parts.states: [(alpha, dy, scale)] from the
    clip's first frame until it's there): a Transform that scales it about pivot (px in the frame) and moves it
    up into place. Returns ([tool text], the last tool, the keys of its opacity - for the
    Blend of the Merge that puts it on, or None when it doesn't fade)."""
    fw, fh = frame
    tools, last = [], upstream
    scales = [(f, s) for f, (_a, _dy, s) in enumerate(states)]
    moves = [(f, dy) for f, (_a, dy, _s) in enumerate(states)]
    inputs = [fc.feed("Input", upstream), fc.input_("Pivot", (pivot[0] / fw, 1 - pivot[1] / fh))]
    if any(abs(s - 1.0) > 1e-6 for _f, s in scales):
        tools.append(fc.spline(f"{name}Size", fc.straight(scales)))
        inputs.append(fc.wired("Size", f"{name}Size"))
    if any(abs(dy) > 1e-6 for _f, dy in moves):
        # Fusion's Center: 0.5 is where it is; y up, so below its place is less.
        tools += fc.path(f"{name}Path", [(0, 0.5), (len(states) - 1, 0.5)],
                         fc.straight([(f, 0.5 - dy * size_px / fh) for f, dy in moves]), (pos[0], pos[1] + 20))
        inputs.append(fc.wired("Center", f"{name}Path"))
    if len(inputs) > 2:
        tools.append(fc.block(name, "Transform", inputs, (pos[0], pos[1] + 33)))
        last = name
    alphas = fc.straight([(f, a) for f, (a, _dy, _s) in enumerate(states)])
    return tools, last, (alphas if any(abs(a - 1.0) > 1e-6 for _f, a in alphas) else None)


def center_point(frame, metrics, size_px, x, baseline_y):
    """The Text+ Center (0..1, y up) that puts the text's baseline at baseline_y."""
    fw, fh = frame
    return x / fw, 1 - (baseline_y - metrics["baseline"] * size_px) / fh
