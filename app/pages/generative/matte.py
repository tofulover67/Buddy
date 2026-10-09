#!/usr/bin/env python3
"""
Generative's cut-outs: which part of a picture is its subject, as a matte
(0 background .. 1 subject, soft at hair and fur), and the subject's own
colours at its edges. Runs in the engine's environment (engine_setup.py),
like fill.py.

The model is BiRefNet (Zheng Peng et al., MIT) as onnx-community exports
it: a fixed 1024 x 1024 input, the subject found in one pass - no clicks.
Two sizes: "lite" (115 MB, ~3 s on a desktop CPU) and the full one
(490 MB, ~7 s, finer hair).

A big picture's matte comes back from 1024 px soft at its edges. A
guided filter (He et al.) then makes the matte's edges follow the full
picture's own edges - only near them: inside and outside stay as the
model said.

Edges hold both colours - a strand of hair over a green wall is part
green - so a cut-out put on another background would carry a fringe of
the old one. The subject's colours there are estimated with blur fusion
(Forte & Pitie, "Approximate Fast Foreground Colour Estimation", 2021):
a few box blurs, any size picture.
"""

from __future__ import annotations

import numpy as np

import cv2

SIZE = 1024
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


class Matter:
    """One loaded model; find() as often as wanted."""

    def __init__(self, model_path: str, preparing=None):
        """On the graphics card when there's one (gpu.py) - preparing() is
        said before it's compiled for it, the first time."""
        import gpu
        self.session = gpu.session(model_path, 0, preparing)
        self.input = self.session.get_inputs()[0].name

    def find(self, bgr8: np.ndarray) -> np.ndarray:
        """bgr8: H x W x 3 uint8 -> the matte, H x W float32 0..1."""
        h, w = bgr8.shape[:2]
        small = cv2.resize(bgr8, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
        x = (small[:, :, ::-1].astype(np.float32) / 255.0 - MEAN) / STD
        out = self.session.run(None, {self.input: np.ascontiguousarray(x.transpose(2, 0, 1)[None])})[-1]
        logits = out[0, 0].astype(np.float32)
        small_matte = 1.0 / (1.0 + np.exp(-np.clip(logits, -50, 50)))
        matte = cv2.resize(small_matte, (w, h), interpolation=cv2.INTER_LINEAR)
        if max(h, w) > SIZE * 1.25:
            matte = refine(bgr8, matte)
        return np.clip(matte, 0, 1).astype(np.float32)


def _box(img, r):
    return cv2.boxFilter(img, -1, (2 * r + 1, 2 * r + 1), normalize=True, borderType=cv2.BORDER_REFLECT)


def guided(guide: np.ndarray, src: np.ndarray, r: int, eps: float) -> np.ndarray:
    """He et al.'s guided filter, grey guide: src smoothed so its edges
    follow guide's."""
    mean_i, mean_p = _box(guide, r), _box(src, r)
    cov = _box(guide * src, r) - mean_i * mean_p
    var = _box(guide * guide, r) - mean_i * mean_i
    a = cov / (var + eps)
    b = mean_p - a * mean_i
    return _box(a, r) * guide + _box(b, r)


def refine(bgr8: np.ndarray, matte: np.ndarray) -> np.ndarray:
    """The upscaled matte's soft band made to follow the picture's edges."""
    h, w = matte.shape
    scale = max(h, w) / SIZE
    r = max(2, int(round(scale * 2)))
    gray = cv2.cvtColor(bgr8, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    sharp = np.clip(guided(gray, matte, r, 1e-3), 0, 1)
    # Only near the edge: where the model was unsure, give or take the
    # filter's own reach.
    edge = ((matte > 0.02) & (matte < 0.98)).astype(np.uint8)
    band = cv2.dilate(edge, np.ones((2 * r + 1, 2 * r + 1), np.uint8)).astype(np.float32)
    band = cv2.GaussianBlur(band, (0, 0), r / 2)
    return matte * (1 - band) + sharp * band


def foreground(img: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """The subject's own colours (float 0..1, same channels as img) where
    the matte is soft; elsewhere the picture's. img: float 0..1."""
    a = alpha[:, :, None] if img.ndim == 3 else alpha
    f, b = _fusion(img, img, img, a, 90)
    f, _ = _fusion(img, f, b, a, 6)
    return f


def _fusion(img, f, b, a, r):
    blur_a = _blur(a, r)
    blur_f = _blur(f * a, r) / (blur_a + 1e-5)
    blur_b = _blur(b * (1 - a), r) / ((1 - blur_a) + 1e-5)
    f = blur_f + a * (img - a * blur_f - (1 - a) * blur_b)
    return np.clip(f, 0, None), blur_b


def _blur(x, r):
    out = cv2.blur(x, (r, r))
    return out[:, :, None] if x.ndim == 3 and out.ndim == 2 else out


def apply_strokes(matte: np.ndarray, strokes, soft: float = 1.0, outward: bool = False) -> np.ndarray:
    """The matte with the person's fixes painted on: "keep" strokes make
    it subject, the others background - in order. Edges `soft` pixels soft
    (a Gaussian sigma; 0: hard), or a stroke's own feather - see
    feather_width(). outward: a feather fades out past the outline only,
    all the way inside it (Remove's paint: _ramp_out)."""
    if not strokes:
        return matte
    out = matte.astype(np.float32, copy=True)
    h, w = out.shape[:2]
    for s in strokes:
        paint = np.zeros((h, w), np.uint8)
        if not draw_stroke(paint, s, 255):
            continue
        half = feather_width(s)
        # Only round the stroke changes: its box, and as far past it as it fades.
        reach = int(np.ceil(max(2 * half if outward else half, 3 * soft))) + 2
        bx, by, bw, bh = cv2.boundingRect(paint)
        x0, y0 = max(0, bx - reach), max(0, by - reach)
        x1, y1 = min(w, bx + bw + reach), min(h, by + bh + reach)
        inside = paint[y0:y1, x0:x1] > 0
        if half > soft:
            cover = _ramp_out(inside, half) if outward else _ramp(inside, half)
        elif soft:
            cover = cv2.GaussianBlur(inside.astype(np.float32), (0, 0), soft)
        else:
            cover = inside.astype(np.float32)
        target = 1.0 if s.get("keep") else 0.0
        part = out[y0:y1, x0:x1]
        out[y0:y1, x0:x1] = part * (1 - cover) + target * cover
    return out


def feather_width(stroke) -> float:
    """How far either side of a stroke's outline its feather fades, in
    pixels: half its feather's share of its radius. All the way inside
    that, nothing past it - so even at 100% the middle is all the way."""
    return feather_share(stroke) * stroke_radius(stroke) / 2


def _ramp(inside: np.ndarray, half: float) -> np.ndarray:
    """0..1 across the outline of `inside`: 0.5 on it, smoothly to 1 at
    `half` pixels in and 0 at `half` pixels out."""
    mask = inside.astype(np.uint8)
    d_in = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    d_out = cv2.distanceTransform(1 - mask, cv2.DIST_L2, 5)
    # Pixel centres: an edge pixel is half a pixel inside, its neighbour half out.
    d = np.where(inside, d_in - 0.5, 0.5 - d_out)
    t = np.clip((d + half) / (2 * half), 0.0, 1.0)
    return (t * t * (3 - 2 * t)).astype(np.float32)


def _ramp_out(inside: np.ndarray, half: float) -> np.ndarray:
    """1 all over `inside`, fading smoothly to 0 at 2 x `half` pixels past
    its outline - as wide a fade as _ramp's, all of it outside. For Remove:
    a feathered shape over a thing takes all of the thing, blending in past
    it. _ramp's half-in, half-out left a ghost - on a 290 px ellipse at
    100%, only a quarter of it was replaced all the way (2026-10-07)."""
    d_out = cv2.distanceTransform(1 - inside.astype(np.uint8), cv2.DIST_L2, 5)
    t = np.clip(1.0 - (d_out - 0.5) / (2 * half), 0.0, 1.0)
    t = np.where(inside, 1.0, t)
    return (t * t * (3 - 2 * t)).astype(np.float32)


# -- strokes: {"shape": "brush" (or none) | "rect" | "ellipse" | "lasso", "points": [...]}
#    brush:    "size" (diameter) and points along its path
#    rect:     two opposite corners, x0 y0 x1 y1; ellipse: the corners of its box -
#              either turned about its middle by "angle" (degrees, clockwise on screen)
#    lasso:    a freehand outline, closed by a straight line back to its start

def _points(stroke):
    try:
        pts = [float(v) for v in stroke.get("points") or ()]
    except (TypeError, ValueError, AttributeError):
        return None
    return np.array(pts[: len(pts) // 2 * 2], np.float64).reshape(-1, 2)


def _angle(stroke) -> float:
    """A rectangle or ellipse's turn: degrees, clockwise on screen, about its middle (0: none)."""
    try:
        return float(stroke.get("angle") or 0.0) % 360.0
    except (TypeError, ValueError):
        return 0.0


def draw_stroke(paint: np.ndarray, stroke, value) -> bool:
    """Draws a brush stroke, rectangle or ellipse onto paint (H x W uint8)
    in value, hard-edged. False when there's nothing to draw."""
    xy = _points(stroke)
    if xy is None or not len(xy):
        return False
    shape = stroke.get("shape") or "brush"
    if shape == "lasso":
        if len(xy) < 3:
            return False
        # Pixel centres, a 16th of a pixel at a time (shift=4), as the view draws it.
        poly = np.rint((xy - 0.5) * 16).astype(np.int32)
        if np.ptp(poly[:, 0]) < 16 or np.ptp(poly[:, 1]) < 16:
            return False
        cv2.fillPoly(paint, [poly], value, cv2.LINE_8, 4)
        return True
    if shape in ("rect", "ellipse"):
        if len(xy) < 2:
            return False
        (x0, y0), (x1, y1) = np.minimum(xy[0], xy[1]), np.maximum(xy[0], xy[1])
        angle = _angle(stroke)
        if angle and shape == "rect":
            # Turned about its middle (clockwise on screen, as the view's canvas turns it): its four
            # corners, pixel centres a 16th of a pixel at a time, as a lasso is drawn.
            if x1 - x0 < 1 or y1 - y0 < 1:
                return False
            cx, cy, a = (x0 + x1) / 2, (y0 + y1) / 2, np.radians(angle)
            c, s = np.cos(a), np.sin(a)
            corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) - [cx, cy]
            turned = corners @ np.array([[c, s], [-s, c]]) + [cx, cy]
            cv2.fillPoly(paint, [np.rint((turned - 0.5) * 16).astype(np.int32)], value, cv2.LINE_8, 4)
            return True
        if shape == "rect":
            # Pixels whose centres are inside, as the view draws it.
            h, w = paint.shape[:2]
            c0, r0 = max(0, int(np.ceil(x0 - 0.5))), max(0, int(np.ceil(y0 - 0.5)))
            c1, r1 = min(w, int(np.ceil(x1 - 0.5))), min(h, int(np.ceil(y1 - 0.5)))
            if c1 <= c0 or r1 <= r0:
                return False
            paint[r0:r1, c0:c1] = value
        else:
            if x1 - x0 < 1 or y1 - y0 < 1:
                return False
            # cv2.ellipse works in whole pixels: a 16th of one with shift=4,
            # its outline drawn too - so half a pixel in from the box.
            centre = (int(round((x0 + x1) / 2 * 16 - 8)), int(round((y0 + y1) / 2 * 16 - 8)))
            axes = (int(round(((x1 - x0) / 2 - 0.5) * 16)), int(round(((y1 - y0) / 2 - 0.5) * 16)))
            cv2.ellipse(paint, centre, axes, angle, 0, 360, value, -1, cv2.LINE_8, 4)
        return True
    if shape != "brush":
        return False
    try:
        size = max(1, int(round(float(stroke.get("size", 1)))))
    except (TypeError, ValueError):
        return False
    pts = np.rint(xy).astype(np.int32)
    if len(pts) > 1:
        cv2.polylines(paint, [pts], False, value, size, cv2.LINE_8)
    # polylines' wide lines have square-ish joins: round ends and corners.
    for x, y in pts:
        cv2.circle(paint, (int(x), int(y)), max(1, size // 2), value, -1, cv2.LINE_8)
    return True


def stroke_radius(stroke) -> float:
    """What a stroke's feather is a share of: a brush's radius, half the
    shorter side of a rectangle, ellipse or lasso's box."""
    if (stroke.get("shape") or "brush") in ("rect", "ellipse", "lasso"):
        xy = _points(stroke)
        if xy is None or len(xy) < 2:
            return 0.0
        return float(np.ptp(xy, axis=0).min()) / 2
    try:
        return float(stroke.get("size") or 0) / 2
    except (TypeError, ValueError):
        return 0.0



def feather_share(stroke) -> float:
    """A stroke's feather as a share of its radius, 0..1."""
    try:
        return min(1.0, max(0.0, float(stroke.get("feather") or 0) / 100.0))
    except (TypeError, ValueError):
        return 0.0
