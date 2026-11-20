"""How a Data Graphics number or bar gets from its start to its end: the one clock the
preview in Buddy and the keys in Resolve are both made from: a timing curve the person can drag into any shape. Pure arithmetic."""

# A timing curve is a cubic Bezier from (0, 0) to (1, 1) - time across, how far along
# up - told by its two handles, [x1, y1, x2, y2], as CSS's cubic-bezier() is. The
# handles' x stay within 0..1 (time only runs forward); their y may go past 0 and 1,
# which is an overshoot. The page's curve editor drags the handles; these are the
# curves to start from. ease_out and ease_in are exactly Animation's titles' cubics.
CURVES = {
    "ease_out": [1 / 3, 1.0, 2 / 3, 1.0],
    "ease_in_out": [0.65, 0.0, 0.35, 1.0],
    "ease_in": [1 / 3, 0.0, 2 / 3, 0.0],
    "linear": [1 / 3, 1 / 3, 2 / 3, 2 / 3],
    "overshoot": [0.3, 1.2, 0.5, 1.25],
}
CUSTOM = "custom"
EASINGS = tuple(CURVES) + (CUSTOM,)
DEFAULT = "ease_out"
Y_MIN, Y_MAX = -0.5, 1.5


def clamp(p):
    return 0.0 if p <= 0 else 1.0 if p >= 1 else float(p)


def named(name):
    return [round(v, 4) for v in CURVES[name]]


def clean_curve(raw, defaults):
    """{"easing", "curve"} from a spec: a named curve is that curve; "custom" is the
    handles it carries, kept within what a curve can be."""
    raw = raw or {}
    name = raw.get("easing")
    if name in CURVES:
        return {"easing": name, "curve": named(name)}
    handles = raw.get("curve")
    if name != CUSTOM or not isinstance(handles, (list, tuple)) or len(handles) != 4:
        name = defaults.get("easing", DEFAULT)
        return {"easing": name, "curve": named(name)}
    x1, y1, x2, y2 = (number(v, f) for v, f in zip(handles, CURVES[DEFAULT]))
    return {"easing": CUSTOM, "curve": [round(min(1.0, max(0.0, x1)), 4), round(min(Y_MAX, max(Y_MIN, y1)), 4),
                                        round(min(1.0, max(0.0, x2)), 4), round(min(Y_MAX, max(Y_MIN, y2)), 4)]}


def ease(curve, p):
    """How far along (0 at the start, 1 at the end; past either on an overshoot) a
    timing curve is at progress p, 0..1 through its time."""
    p = clamp(p)
    if p in (0.0, 1.0):
        return p
    x1, y1, x2, y2 = curve
    low, high = 0.0, 1.0
    for _ in range(40):                 # x(s) only rises, so halving finds s
        s = (low + high) / 2
        x = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s ** 2 * x2 + s ** 3
        if x < p:
            low = s
        else:
            high = s
    s = (low + high) / 2
    return round(3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s ** 2 * y2 + s ** 3, 9)


def number(value, fallback, low=None, high=None):
    """A float from whatever was typed, within [low, high]; the fallback if it isn't one."""
    try:
        v = float(str(value).strip().replace(",", ".")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return fallback
    if v != v or v in (float("inf"), float("-inf")):
        return fallback
    if low is not None:
        v = max(low, v)
    if high is not None:
        v = min(high, v)
    return v


def colour(value, fallback):
    """"#rrggbb" in lower case, or the fallback."""
    text = str(value or "").strip().lower()
    if len(text) == 7 and text[0] == "#" and all(c in "0123456789abcdef" for c in text[1:]):
        return text
    return fallback
