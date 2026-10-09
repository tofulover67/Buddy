#!/usr/bin/env python3
"""
Clean Up's removal: fills a masked part of a picture with what would be
behind it. Runs in the engine's own environment (engine_setup.py) -
NumPy, OpenCV and ONNX Runtime, no Qt and nothing of Buddy's.

The model is LaMa ("Big LaMa", Samsung AI, Apache 2.0) as OpenCV's model
zoo publishes it: int8 weights, 93 MB, a fixed 512 x 512 input. It
continues what's around a hole - textures, edges, light - and invents
nothing new, which is what removal wants.

A picture of any size: each masked spot is filled in a square cut around
it, with room on every side for the model to see what's there, scaled to
512 for the model and back. A small spot on a big photo is then filled at
(nearly) its own resolution; a big one comes back softer than what's
around it, so grain matching the edge is put back over it.

Only masked pixels change. The fill is put in at the picture's own depth
(8-bit, 16-bit, float), blended over a few pixels at the mask's edge - or,
where the mask is feathered, as much of it as the mask says, so the fill
fades into the picture there - and the alpha channel, if any, is left
alone.
"""

from __future__ import annotations

import numpy as np

import cv2

SIZE = 512                   # the model's input, both sides
# How much of the cut around a spot is the spot: the rest is what the
# model looks at. A spot wider than SIZE / CONTEXT is scaled down.
CONTEXT = 2.2
# A painted mask never quite covers an object's soft edge, and what's
# left of it, the model draws back in (seen in testing: a whole person
# came back from their outline). So the mask grows by this share of the
# picture's longer side before filling - 6 px at 4K - and never by less
# than MIN_GROW.
GROW = 1 / 640
MIN_GROW = 3
# A feathered mask: under FAINT it's nothing, from FULL it's all the way.
FAINT = 1 / 255
FULL = 254 / 255


def _as_float(img: np.ndarray) -> tuple[np.ndarray, float]:
    """The picture as float32, 0..1 for 8 and 16-bit ones, and the scale
    that takes it back."""
    if img.dtype == np.uint8:
        return img.astype(np.float32) / 255.0, 255.0
    if img.dtype == np.uint16:
        return img.astype(np.float32) / 65535.0, 65535.0
    return img.astype(np.float32), 1.0


def _back(fl: np.ndarray, scale: float, dtype) -> np.ndarray:
    if dtype == np.uint8 or dtype == np.uint16:
        return np.clip(np.rint(fl * scale), 0, scale).astype(dtype)
    return fl.astype(dtype)


def grow_px(shape) -> int:
    return max(MIN_GROW, int(round(max(shape[:2]) * GROW)))


MAX_MARGIN = 200             # px: the most Margin goes


def widen(mask: np.ndarray, px: float) -> np.ndarray:
    """A mask (0..1, or bool) reaching `px` further out all round - the
    page's Margin: what's removed taken past what's painted, for a glow,
    motion blur or a soft edge the paint missed. Its new edge fades over a
    third of the margin (2 px at least). From the distance to it, so a wide
    margin on a 4K frame costs no more than a narrow one."""
    m = mask.astype(np.float32)
    px = float(min(max(px or 0, 0), MAX_MARGIN))
    if px <= 0 or not (m > FAINT).any():
        return m
    d = cv2.distanceTransform((m <= 0.5).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    soft = max(2.0, px / 3)
    return np.maximum(m, np.clip((px + soft - d) / soft, 0, 1)).astype(np.float32)


class Filler:
    """One loaded model; fill() as often as wanted."""

    def __init__(self, model_path: str, threads: int = 0, preparing=None):
        """On the graphics card when there's one (gpu.py) - preparing() is
        said before it's compiled for it, the first time."""
        import gpu
        self.session = gpu.session(model_path, threads, preparing)

    def _infer(self, rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
        """rgb: SIZE x SIZE x 3 float 0..1; hole: SIZE x SIZE bool -> filled rgb, 0..1."""
        image = np.ascontiguousarray(np.clip(rgb, 0, 1).transpose(2, 0, 1)[None], dtype=np.float32)
        mask = hole.astype(np.float32)[None, None]
        out = self.session.run(None, {"image": image, "mask": mask})[0][0]
        return out.transpose(1, 2, 0) / 255.0

    def fill(self, img: np.ndarray, mask: np.ndarray, progress=None) -> tuple[np.ndarray, tuple | None]:
        """img: H x W (x 1, 3 or 4) as read (BGR order, any depth); mask:
        H x W, how much of the fill goes where - 0..1 (a feathered edge
        fades), or bool. Where it's all the way the fill replaces the
        picture (grown a little, past an object's soft edge); where it
        fades, so does the fill into the picture. Returns (the filled
        picture, the box that changed as (x, y, w, h)) - (img, None) for an
        empty mask."""
        weight = np.clip(mask.astype(np.float32), 0.0, 1.0)
        if not (weight > FAINT).any():
            return img, None
        h, w = weight.shape
        grow = grow_px(img.shape)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
        # The model fills wherever any of it goes; full strength where the
        # mask is all the way (grown), the mask's own strength elsewhere.
        hole = cv2.dilate((weight > FAINT).astype(np.uint8), kernel) > 0
        full = cv2.dilate((weight >= FULL).astype(np.uint8), kernel) > 0

        dtype = img.dtype
        gray = img.ndim == 2 or img.shape[2] == 1
        fl, scale = _as_float(img)
        if gray:
            fl = fl.reshape(h, w)
            color = np.repeat(fl[:, :, None], 3, axis=2)
        else:
            color = fl[:, :, :3].copy()

        left = hole.copy()                     # not filled yet
        count, labels, stats, _ = cv2.connectedComponentsWithStats(hole.astype(np.uint8), connectivity=8)
        # Biggest first: a later small spot then sees the big fill around it.
        order = sorted(range(1, count), key=lambda i: -stats[i, cv2.CC_STAT_AREA])
        jobs = []                              # (cut x, y, w, h, the part it keeps: x, y, w, h | None, label)
        for i in order:
            jobs += _cuts(labels, stats, i, w, h)
        box = [w, h, 0, 0]
        for n, (cut, keep, label) in enumerate(jobs):
            if progress:
                progress(n, len(jobs))
            cx, cy, cw, ch = cut
            region = left[cy:cy + ch, cx:cx + cw].copy()
            if not region.any():
                continue                       # filled with a neighbour's cut
            # What this cut puts in: its own spot's pixels in the part it
            # keeps (a long spot is filled a piece at a time, each piece
            # seeing the one before as picture), or all it holds.
            commit = region & (labels[cy:cy + ch, cx:cx + cw] == label)
            if keep is not None:
                kx, ky, kw, kh = keep
                zone = np.zeros_like(commit)
                zone[max(0, ky - cy):max(0, ky - cy + kh), max(0, kx - cx):max(0, kx - cx + kw)] = True
                commit &= zone
            else:
                commit = region
            if not commit.any():
                continue
            patch = color[cy:cy + ch, cx:cx + cw]
            filled = self._fill_cut(patch, region, grow)
            # Blend: full strength over the part the mask is all the way,
            # fading out over a couple of pixels past it so there's no seam;
            # the mask's own strength over its feathered part. Inside the
            # hole, where the next piece goes, the edge stays hard.
            solid = commit & full[cy:cy + ch, cx:cx + cw]
            seam = cv2.GaussianBlur(solid.astype(np.float32), (0, 0), max(1.0, grow / 2.5))
            strength = np.maximum(np.maximum(seam, solid), commit * weight[cy:cy + ch, cx:cx + cw])
            alpha = np.where(region & ~commit, 0.0, strength)[:, :, None].astype(np.float32)
            color[cy:cy + ch, cx:cx + cw] = patch * (1 - alpha) + filled * alpha
            ys, xs = np.nonzero(alpha[:, :, 0] > 0.002)
            if len(xs):
                box = [min(box[0], cx + xs.min()), min(box[1], cy + ys.min()),
                       max(box[2], cx + xs.max() + 1), max(box[3], cy + ys.max() + 1)]
            left[cy:cy + ch, cx:cx + cw] &= ~commit
        x0, y0, x1, y1 = box

        out = fl.copy()
        if gray:
            out = color.mean(axis=2)
            out = out.reshape(img.shape)
        else:
            out[:, :, :3] = color
        if x1 <= x0:
            return img, None
        box = (int(x0), int(y0), int(x1 - x0), int(y1 - y0))
        # Only the box comes back changed: everything outside it is the
        # picture as it was, bit for bit.
        result = img.copy()
        bx, by, bw, bh = box
        result[by:by + bh, bx:bx + bw] = _back(out[by:by + bh, bx:bx + bw], scale, dtype)
        return result, box

    def _fill_cut(self, patch: np.ndarray, region: np.ndarray, grow: int) -> np.ndarray:
        """One cut: squared up by mirroring its edges, scaled to SIZE, filled,
        scaled back - grain put back where it was scaled up."""
        ch, cw = region.shape
        side = max(ch, cw)
        pad = ((0, side - ch), (0, side - cw))
        sq = np.pad(patch, pad + ((0, 0),), mode="reflect" if min(ch, cw) > 1 else "edge")
        sq_hole = np.pad(region, pad, mode="constant")
        down = cv2.INTER_AREA if side > SIZE else cv2.INTER_CUBIC
        small = cv2.resize(sq, (SIZE, SIZE), interpolation=down)
        small_hole = cv2.resize(sq_hole.astype(np.uint8), (SIZE, SIZE), interpolation=cv2.INTER_NEAREST) > 0
        # Rounding can drop a 1-px sliver of the hole at a small scale.
        small_hole |= cv2.resize(sq_hole.astype(np.float32), (SIZE, SIZE), interpolation=cv2.INTER_AREA) > 0.01
        rgb = small[:, :, ::-1]                # the model takes RGB
        out = self._infer(rgb, small_hole)[:, :, ::-1]
        up = cv2.INTER_CUBIC if side > SIZE else cv2.INTER_AREA
        big = cv2.resize(out, (side, side), interpolation=up)[:ch, :cw]
        # Outside the hole the model returns its own re-drawing of the
        # picture: fill() blends into that over a few pixels past the
        # hole's edge, so there's no seam, and keeps the picture beyond.
        if side > SIZE * 1.4:
            big = _regrain(big, patch, region, grow, side / SIZE)
        return big.astype(np.float32)


def _square(mx, my, side, w, h):
    """A square of `side` centred on (mx, my), kept inside the picture."""
    cw, ch = min(side, w), min(side, h)
    cx = int(np.clip(mx - cw / 2, 0, w - cw))
    cy = int(np.clip(my - ch / 2, 0, h - ch))
    return cx, cy, cw, ch


def _cuts(labels, stats, i, w, h):
    """The cuts that fill spot i: [(cut, keep, i)].

    A compact spot: one square around it, CONTEXT times its size (at least
    SIZE where the picture allows), keep None.

    A long thin one - a pole, a cable, a tower - would need a square as big
    as its length, scaled far down for the model: a soft smear. So it's
    covered by squares just a few times its thickness instead, each
    filling the spot's pixels in its middle part (keep) and each seeing
    the pieces before it, in order along the spot."""
    bx, by, bw, bh, _area = (int(v) for v in stats[i])
    floor = min(SIZE, max(w, h))
    whole = max(int(max(bw, bh) * CONTEXT), floor)
    spot = (labels[by:by + bh, bx:bx + bw] == i).astype(np.uint8)
    thick = 2 * float(cv2.distanceTransform(spot, cv2.DIST_L2, 3).max())
    side = max(int(thick * 4), floor)
    if whole <= side * 1.25:
        return [(_square(bx + bw / 2, by + bh / 2, whole, w, h), None, i)]
    step = side // 2
    ys, xs = np.nonzero(spot)
    pts = np.stack([xs, ys], axis=1).astype(np.float32)
    # Along the spot's own direction, so each piece follows the last.
    mean = pts.mean(axis=0)
    _, vec = np.linalg.eigh(np.cov((pts - mean).T) if len(pts) > 2 else np.eye(2))
    axis = vec[:, -1]
    cells = {}
    for gx, gy in zip(xs // step, ys // step):
        cells.setdefault((int(gx), int(gy)), 0)
    out = []
    for gx, gy in cells:
        kx, ky = bx + gx * step, by + gy * step
        mx, my = kx + step / 2, ky + step / 2
        out.append(((mx - bx - mean[0]) * axis[0] + (my - by - mean[1]) * axis[1],
                    (_square(mx, my, side, w, h), (kx, ky, step, step), i)))
    out.sort(key=lambda o: o[0])
    return [o[1] for o in out]


def _regrain(fill: np.ndarray, patch: np.ndarray, region: np.ndarray, grow: int, factor: float,
             seed: int = 1234) -> np.ndarray:
    """A fill scaled up `factor` times lacks the fine grain of what's around
    it: noise as strong as the grain in a band around the hole goes back
    over the hole. Footage gives each frame its own seed: one noise in every
    frame would sit still over the picture, like dirt on the lens."""
    band = cv2.dilate(region.astype(np.uint8), np.ones((grow * 6 + 1, grow * 6 + 1), np.uint8)) > 0
    band &= ~region
    if band.sum() < 50:
        return fill
    def grain(img, where):
        """The grain's strength per channel: the median deviation of the
        finest detail, which edges and wires (a few strong values) don't
        sway the way they would a plain average."""
        detail = (img - cv2.GaussianBlur(img, (0, 0), 1.0))[where]
        return 1.4826 * np.median(np.abs(detail - np.median(detail, axis=0)), axis=0)

    std = grain(patch, band)
    have = grain(fill, region)                        # what the scaled-up fill already has
    need = np.sqrt(np.maximum(std ** 2 - have ** 2, 0))
    if not np.any(need > 1e-4):
        return fill
    rng = np.random.default_rng(seed)
    # One noise for luma, shared by the channels, plus a little each: film
    # and sensor grain are mostly brightness.
    shared = rng.standard_normal(region.shape).astype(np.float32)
    noise = np.stack([0.8 * shared + 0.6 * rng.standard_normal(region.shape).astype(np.float32)
                      for _ in range(fill.shape[2])], axis=2)
    noise = cv2.GaussianBlur(noise, (0, 0), 0.6)
    noise /= max(float(noise.std()), 1e-6)
    return np.where(region[:, :, None], fill + noise * need, fill)
