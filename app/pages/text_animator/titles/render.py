"""The renderer - a composed title drawn with QPainter, in the same Qt fonts measure.py
measures with, so the preview, the contact sheet and the golden frames all show exactly
what the composer laid out. (The Text+ clips are Fusion's own drawing of the same
layout.)

A title is drawn in two passes over all of its words, so one word's thick outline never
lies over its neighbour's letters:
  under: background box -> shadows -> glows -> outer strokes, outermost first
  over:  fill (colour or gradient) -> inner strokes (inside the letters) -> centre strokes
An outer stroke is drawn at twice its reach under the fill, so only its outside half
shows, and stacked strokes add their reach. Shadows and glows are cast from the letters
grown by the outer strokes (+ spread). Each word turns about its own centre.

Style lengths are shares of the letter size; angles are degrees clockwise from 12
o'clock (a shadow at 180 falls straight down; a gradient at 90 runs left to right).
"""

import math
import re
from functools import lru_cache

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QTransform)

from . import composer
from . import kinetic
from . import looks as L
from . import measure as M
from . import timing

_PATH_PX = 1000         # glyph outlines are made at this size and scaled


def colour(value, fallback="#ffffff"):
    """A QColor from "#rrggbb", "#rrggbbaa" or "rgba(r, g, b, a)"."""
    text = str(value or fallback).strip()
    m = re.match(r"rgba?\(([^)]*)\)", text)
    if m:
        parts = [float(p) for p in m.group(1).split(",")]
        c = QColor(int(parts[0]), int(parts[1]), int(parts[2]))
        if len(parts) > 3:
            c.setAlphaF(max(0.0, min(1.0, parts[3])))
        return c
    if re.fullmatch(r"#[0-9a-fA-F]{8}", text):
        c = QColor(text[:7])
        c.setAlpha(int(text[7:], 16))
        return c
    c = QColor(text)
    return c if c.isValid() else QColor(fallback)


def _glyphs(font, text, track=0.0):
    """The text's outline at _PATH_PX, baseline at y 0, starting at x 0 - as Qt shapes it
    (kerned), which is how it was measured. track: px between letters, at _PATH_PX."""
    from .. import canvas_math as cm
    qfont = QFont(cm._measure_font(font[0], font[1]))
    if track:
        qfont.setLetterSpacing(QFont.AbsoluteSpacing, track)
    path = QPainterPath()
    path.addText(QPointF(0, 0), qfont, text)
    return path


@lru_cache(maxsize=4096)
def _outline(family, style, text, track):
    path = _glyphs((family, style), text, track)
    parts, i, n = [], 0, path.elementCount()
    while i < n:
        e = path.elementAt(i)
        if e.type == QPainterPath.ElementType.MoveToElement:
            parts.append("M%.1f %.1f" % (e.x, e.y))
            i += 1
        elif e.type == QPainterPath.ElementType.LineToElement:
            parts.append("L%.1f %.1f" % (e.x, e.y))
            i += 1
        else:       # a curve: CurveToElement and its two CurveToDataElements
            c2, c3 = path.elementAt(i + 1), path.elementAt(i + 2)
            parts.append("C%.1f %.1f %.1f %.1f %.1f %.1f" % (e.x, e.y, c2.x, c2.y, c3.x, c3.y))
            i += 3
    r = path.boundingRect()
    ink = [round(v / _PATH_PX, 5) for v in (r.left(), r.top(), r.right(), r.bottom())]
    return "".join(parts), ink


def outline(box):
    """A box's letters for the Titles tab's page to draw: (SVG path data at _PATH_PX a
    letter, baseline at y 0 and the advance starting at x 0 - even-odd filled, as Qt's
    path is - and the ink's [left, top, right, bottom] in letter sizes). The same
    outline draw() fills, so the page shows exactly these letters."""
    M.load_bundled()
    track = float(box.get("track", 0.0) or 0.0) * _PATH_PX / box["size"] if box["size"] else 0.0
    return _outline(box["font"][0], box["font"][1], box["text"], round(track, 3))


def _grown(path, reach):
    """The letters grown outward by reach (round joins)."""
    if reach <= 0:
        return path
    stroker = QPainterPathStroker()
    stroker.setWidth(2 * reach)
    stroker.setJoinStyle(Qt.RoundJoin)
    stroker.setCapStyle(Qt.RoundCap)
    return stroker.createStroke(path).united(path)


def _box_blur(a, r):
    """One box blur of radius r along both axes (r in whole pixels)."""
    if r < 1:
        return a
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(a, pad), axis=axis)
        hi = np.take(c, range(2 * r + 1, c.shape[axis]), axis=axis)
        lo = np.take(c, range(0, c.shape[axis] - 2 * r - 1), axis=axis)
        a = (hi - lo) / (2 * r + 1)
    return a


def _blurred_layer(path, blur_px, rgba, strength=1.0):
    """The path filled, Gaussian-blurred (three box blurs) and tinted: (QImage, its
    top-left in the path's coordinates)."""
    margin = int(math.ceil(blur_px * 2)) + 2
    rect = path.boundingRect().adjusted(-margin, -margin, margin, margin)
    w, h = max(1, int(math.ceil(rect.width()))), max(1, int(math.ceil(rect.height())))
    mask = QImage(w, h, QImage.Format_Grayscale8)
    mask.fill(0)
    p = QPainter(mask)
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(-rect.left(), -rect.top())
    p.fillPath(path, QColor(255, 255, 255))
    p.end()
    stride = mask.bytesPerLine()
    a = np.frombuffer(mask.constBits(), np.uint8, count=stride * h).reshape(h, stride)[:, :w].astype(np.float32)
    sigma = blur_px / 2
    if sigma > 0.3:
        box = max(1, int(round((math.sqrt(4 * sigma * sigma + 1) - 1) / 2)))
        for _ in range(3):
            a = _box_blur(a, box)
    a = np.clip(a * strength * rgba[3] / 255.0, 0, 255)
    out = np.empty((h, w, 4), np.uint8)
    out[..., 0] = rgba[2] * a / 255.0       # premultiplied BGRA (ARGB32 on little-endian)
    out[..., 1] = rgba[1] * a / 255.0
    out[..., 2] = rgba[0] * a / 255.0
    out[..., 3] = a
    image = QImage(out.data, w, h, 4 * w, QImage.Format_ARGB32_Premultiplied).copy()
    return image, rect.topLeft()


def _rgba(c):
    return (c.red(), c.green(), c.blue(), c.alpha())


def _strokes(style, pos):
    return [s for s in style.get("strokes") or [] if s.get("pos", "outer") == pos]


def _outer_reach(style, size):
    return sum(float(s.get("w", 0)) for s in _strokes(style, "outer")) * size


class _Word:
    """One word ready to draw: its outline in frame px, its style, and where its
    animation has it (timing.state: opacity, scale about its own centre, offset as a
    share of the frame height, blur as a share of the letter size, how far out its
    copies are and how far into a breath)."""

    def __init__(self, box, style, state=None, frame_h=1080.0):
        state = state or {}
        scale = float(state.get("scale", 1.0))
        self.box, self.style, self.size = box, style, box["size"] * scale
        self.alpha = float(state.get("alpha", 1.0))
        self.spread = float(state.get("spread", 1.0))
        self.pulse = float(state.get("pulse", 0.0))
        self.blur = float(state.get("blur", 0.0)) * box["size"]
        track = float(box.get("track", 0.0) or 0.0) * _PATH_PX / box["size"] if box["size"] else 0.0
        path = _glyphs(box["font"], box["text"], track)
        t = QTransform()
        cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
        t.translate(cx + float(state.get("dx", 0.0)) * frame_h, cy + float(state.get("dy", 0.0)) * frame_h)
        t.rotate(box.get("rot", 0.0) + float(state.get("rot", 0.0)))
        t.scale(scale, scale)
        t.translate(-cx, -cy)
        t.translate(box["x"], box["baseline"])
        t.scale(box["size"] / _PATH_PX, box["size"] / _PATH_PX)
        self.path = t.map(path)
        self.rect = QRectF(box["x"], box["y"], box["w"], box["h"])


def _copies(p, word):
    """A style's copies of the word (echo, extrude, long shadow): each group n copies,
    stepped (dx, dy) shares of the letter size apart - on both sides with "both" - filled
    and/or stroked, fading copy by copy. Farthest first, so the nearer lie on top. Copies
    that fan out are where the word's spread has them (timing.copy_at)."""
    size = word.size
    for group in word.style.get("copies") or []:
        n = int(group.get("n", 3))
        dx, dy = (float(v) * size for v in group.get("step", (0.0, -0.5)))
        alpha, fade = float(group.get("alpha", 1.0)), float(group.get("fade", 1.0))
        sides = (1, -1) if group.get("both") else (1,)
        stroke = group.get("stroke")
        for k in range(n, 0, -1):
            out, shown = timing.copy_at(group, k, word.spread, word.pulse)
            a = alpha * fade ** (k - 1) * shown
            if a <= 0.003:
                continue
            for side in sides:
                path = word.path.translated(side * k * dx * out, side * k * dy * out)
                p.save()
                p.setOpacity(p.opacity() * a)
                if group.get("fill"):
                    p.fillPath(path, QBrush(colour(group["fill"])))
                if stroke:
                    pen = QPen(colour(stroke.get("colour"), "#ffffff"), float(stroke.get("w", 0.03)) * size)
                    pen.setJoinStyle(Qt.RoundJoin)
                    p.strokePath(path, pen)
                p.restore()


def _under(p, word):
    style, size = word.style, word.size
    _copies(p, word)
    bg = style.get("bg")
    if bg:
        pad, radius = float(bg.get("pad", 0.15)) * size, float(bg.get("radius", 0.1)) * size
        p.setPen(Qt.NoPen)
        p.setBrush(colour(bg.get("colour"), "#000000"))
        p.drawRoundedRect(word.path.boundingRect().adjusted(-pad, -pad, pad, pad), radius, radius)
    reach = _outer_reach(style, size)
    for shadow in style.get("shadows") or []:
        grown = _grown(word.path, reach + float(shadow.get("spread", 0)) * size)
        angle = math.radians(float(shadow.get("angle", 180)))
        dist = float(shadow.get("dist", 0.05)) * size
        image, at = _blurred_layer(grown, float(shadow.get("blur", 0.1)) * size, _rgba(colour(shadow.get("colour"), "#000000")))
        p.drawImage(at + QPointF(math.sin(angle) * dist, -math.cos(angle) * dist), image)
    for glow in style.get("glows") or []:
        grown = _grown(word.path, reach + float(glow.get("spread", 0)) * size)
        strength = float(glow.get("strength", 1.0))
        rgba = _rgba(colour(glow.get("colour"), "#ffffff"))
        while strength > 0:
            image, at = _blurred_layer(grown, float(glow.get("blur", 0.3)) * size, rgba, min(strength, 1.0))
            p.drawImage(at, image)
            strength -= 1.0
    outer = _strokes(style, "outer")
    reaches = np.cumsum([float(s.get("w", 0)) * size for s in outer]) if outer else []
    for stroke, r in reversed(list(zip(outer, reaches))):
        pen = QPen(colour(stroke.get("colour"), "#000000"), 2 * r)
        pen.setJoinStyle(Qt.RoundJoin)
        p.strokePath(word.path, pen)


def _fill_brush(style, rect):
    gradient = style.get("gradient")
    if not gradient or not gradient.get("stops"):
        return QBrush(colour(style.get("fill"), "#ffffff"))
    a = math.radians(float(gradient.get("angle", 90)))
    dx, dy = math.sin(a), -math.cos(a)
    half = abs(rect.width() / 2 * dx) + abs(rect.height() / 2 * dy)
    c = rect.center()
    g = QLinearGradient(c - QPointF(dx, dy) * half, c + QPointF(dx, dy) * half)
    for at, col in gradient["stops"]:
        g.setColorAt(float(at), colour(col))
    return QBrush(g)


def _over(p, word):
    style, size = word.style, word.size
    if style.get("fill", "#ffffff") != "none" or style.get("gradient"):     # "none": a hollow word
        p.fillPath(word.path, _fill_brush(style, word.path.boundingRect()))
    inner = _strokes(style, "inner")
    if inner:
        p.save()
        p.setClipPath(word.path, Qt.IntersectClip)
        for stroke in inner:
            p.strokePath(word.path, QPen(colour(stroke.get("colour"), "#000000"), 2 * float(stroke.get("w", 0)) * size))
        p.restore()
    for stroke in _strokes(style, "center"):
        p.strokePath(word.path, QPen(colour(stroke.get("colour"), "#000000"), float(stroke.get("w", 0)) * size))


def styles_for(layout, doc=None):
    """Each box's style: its role's in the layout's look, with the person's changes in
    doc on (looks.box_styles)."""
    return L.box_styles(layout, doc)


def _blur_image(image, radius):
    """A premultiplied ARGB image blurred (three box blurs ~ a Gaussian of that radius)."""
    w, h = image.width(), image.height()
    stride = image.bytesPerLine()
    a = np.frombuffer(image.constBits(), np.uint8, count=stride * h).reshape(h, stride)[:, :4 * w]
    a = a.reshape(h, w, 4).astype(np.float32)
    sigma = radius / 2
    box = max(1, int(round((math.sqrt(4 * sigma * sigma + 1) - 1) / 2)))
    for _ in range(3):
        for c in range(4):
            a[..., c] = _box_blur(a[..., c], box)
    out = np.clip(a, 0, 255).astype(np.uint8)
    return QImage(out.data, w, h, 4 * w, QImage.Format_ARGB32_Premultiplied).copy()


def _draw_blurred(p, word):
    """A word in motion blur: drawn whole on its own layer, blurred, laid down."""
    s = p.worldTransform().m11() or 1.0
    reach = _outer_reach(word.style, word.size) + 3 * word.blur + 0.3 * word.size
    rect = word.path.boundingRect().adjusted(-reach, -reach, reach, reach)
    w, h = max(1, int(math.ceil(rect.width() * s))), max(1, int(math.ceil(rect.height() * s)))
    layer = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    layer.fill(Qt.transparent)
    lp = QPainter(layer)
    lp.setRenderHint(QPainter.Antialiasing)
    lp.scale(s, s)
    lp.translate(-rect.left(), -rect.top())
    _under(lp, word)
    _over(lp, word)
    lp.end()
    p.save()
    p.setOpacity(word.alpha)
    p.drawImage(rect, _blur_image(layer, word.blur * s))
    p.restore()


def draw(p, layout, styles=None, states=None):
    """Draw the layout on p, in frame px (scale p first for a smaller picture).
    states: each word's timing.state, for a frame of its animation (default: at rest). A
    kinetic layout is drawn where its camera has it - settled, unless kinetic.at() has
    put it at a moment of its own."""
    M.load_bundled()
    layout = kinetic.on_screen(layout)
    styles = styles or styles_for(layout)
    states = states or [None] * len(layout["boxes"])
    frame_h = layout["frame"][1]
    words = [_Word(b, s, st, frame_h) for b, s, st in zip(layout["boxes"], styles, states)]
    p.setRenderHint(QPainter.Antialiasing)
    # Line bars first, under every word - faded with their line's first word.
    alpha_of = {b["id"]: w.alpha for b, w in zip(layout["boxes"], words)}
    for bar in layout.get("bars") or []:
        a = alpha_of.get(bar["first"], 1.0)
        if a > 0.001:
            p.setOpacity(a)
            p.fillRect(QRectF(bar["x"], bar["y"], bar["w"], bar["h"]), colour(bar["colour"], "#000000"))
    p.setOpacity(1.0)
    words = [w for w in words if w.alpha > 0.001]
    sharp = [w for w in words if w.blur * (p.worldTransform().m11() or 1.0) < 0.5]
    for word in sharp:
        p.setOpacity(word.alpha)
        _under(p, word)
    for word in sharp:
        p.setOpacity(word.alpha)
        _over(p, word)
    p.setOpacity(1.0)
    for word in words:
        if word not in sharp:
            _draw_blurred(p, word)


def background(p, w, h):
    """The preview's dark gradient - text of any colour reads on it."""
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#2b2f3a"))
    g.setColorAt(1, QColor("#121318"))
    p.fillRect(QRectF(0, 0, w, h), QBrush(g))


def render(layout, width, transparent=False, states=None):
    """The title as a QImage width px wide, its frame's aspect - on the preview's
    background, or with alpha. Returns (image, hit boxes as frame fractions)."""
    fw, fh = layout["frame"]
    scale = width / fw
    image = QImage(int(round(width)), int(round(fh * scale)), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    p = QPainter(image)
    if not transparent:
        background(p, image.width(), image.height())
    p.scale(scale, scale)
    layout = kinetic.on_screen(layout)
    draw(p, layout, states=states)
    p.end()
    return image, composer.hit_boxes(layout)


def render_at(layout, clock, t, width, transparent=False):
    """The title t seconds in, by the clock (timing.timing) - a frame of its animation."""
    states = [timing.state(w, t) for w in clock["words"]]
    return render(kinetic.at(layout, clock, t), width, transparent, states)[0]
