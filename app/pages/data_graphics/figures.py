"""A number as the text a counter or a graph shows: decimals, the thousands separator,
what's written before and after it, and the clock form. Pure."""

import math

SEPARATORS = {            # name: (between thousands, before the decimals)
    "comma": (",", "."),          # 1,234.5
    "dot": (".", ","),            # 1.234,5
    "space": (" ", ","),     # 1 234,5 (a narrow no-break space)
    "none": ("", "."),            # 1234.5
}
FORMS = ("number", "clock")
MAX_DECIMALS = 3
MAX_AFFIX = 12


def clean_format(raw, defaults):
    """The format fields of a spec, each one valid: decimals, separator, prefix, suffix, form."""
    raw = raw or {}
    try:
        decimals = int(raw.get("decimals", defaults["decimals"]))
    except (TypeError, ValueError):
        decimals = defaults["decimals"]
    return {
        "decimals": min(MAX_DECIMALS, max(0, decimals)),
        "separator": raw.get("separator") if raw.get("separator") in SEPARATORS else defaults["separator"],
        "prefix": str(raw.get("prefix", defaults["prefix"]) or "")[:MAX_AFFIX],
        "suffix": str(raw.get("suffix", defaults["suffix"]) or "")[:MAX_AFFIX],
        "form": raw.get("form") if raw.get("form") in FORMS else defaults.get("form", "number"),
    }


def _grouped(digits, mark):
    if not mark:
        return digits
    parts = []
    while len(digits) > 3:
        parts.insert(0, digits[-3:])
        digits = digits[:-3]
    return mark.join([digits] + parts)


def plain(value, decimals=0, separator="comma"):
    """1234.5 -> "1,234.5": rounded to the decimals, grouped in threes."""
    thousands, point = SEPARATORS.get(separator, SEPARATORS["comma"])
    text = f"{abs(float(value)):.{int(decimals)}f}"
    whole, _dot, part = text.partition(".")
    out = _grouped(whole, thousands) + (point + part if part else "")
    negative = float(value) < 0 and any(c in "123456789" for c in text)     # never "-0"
    return ("-" if negative else "") + out


def clock(seconds, decimals=0, separator="comma", down=False):
    """Seconds as m:ss (h:mm:ss from an hour up), the decimals on the seconds. A clock
    shows the second it's in, not the nearest one: running up it turns over when the
    second is done, running down (down=True) it shows the second still left."""
    _thousands, point = SEPARATORS.get(separator, SEPARATORS["comma"])
    scale = 10 ** int(decimals)
    whole = abs(float(seconds)) * scale
    units = int(math.ceil(whole - 1e-6) if down else math.floor(whole + 1e-6))
    total, part = divmod(units, scale)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    if decimals:
        text += point + str(part).zfill(int(decimals))
    return ("-" if float(seconds) < 0 and units else "") + text


def shown(value, fmt, down=False):
    """The value as the graphic writes it: its form, with the prefix and suffix."""
    if fmt.get("form") == "clock":
        body = clock(value, fmt["decimals"], fmt["separator"], down)
    else:
        body = plain(value, fmt["decimals"], fmt["separator"])
    return f"{fmt.get('prefix', '')}{body}{fmt.get('suffix', '')}"


def runs(texts):
    """[(frame, text)] for each frame the text changes on, from one text per frame."""
    keys = []
    for frame, text in enumerate(texts):
        if not keys or keys[-1][1] != text:
            keys.append((frame, text))
    return keys


def parse(text):
    """A typed number - "1,234.5", "1.234,5", "$1 200", "45%" - as a float, or None."""
    t = "".join(c for c in str(text or "") if c.isdigit() or c in ",.-−")
    t = t.replace("−", "-")
    if not any(c.isdigit() for c in t):
        return None
    negative = t.startswith("-")
    t = t.replace("-", "")
    commas, dots = t.count(","), t.count(".")
    if commas and dots:                                   # the later mark is the decimal one
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif commas or dots:
        mark = "," if commas else "."
        head, _m, tail = t.rpartition(mark)
        # "1,234" and "1.234.567" are thousands; "1,5", "0.25" and "1.234" are decimals.
        if (commas + dots) > 1 or (mark == "," and len(tail) == 3 and head not in ("", "0") and len(head) <= 3):
            t = t.replace(mark, "")
        else:
            t = t.replace(mark, ".")
    try:
        return -float(t) if negative else float(t)
    except ValueError:
        return None
