#!/usr/bin/env python3
"""
Removal across a run of frames - Generative's footage counterpart of
fill.py. Runs in the engine's environment.

A still has only itself to fill a hole from, so the model invents what's
behind. Footage mostly doesn't need to: as things move, what one frame
hides another shows. Filled one frame at a time by the model, a removal
also flickers - each frame's invention differs from the last. So each
frame's hole is filled, in this order:

  1. from its neighbours (RADIUS frames either side): where the flow says a
     hole pixel's background went in a frame, and that frame shows it there
     (not painted over itself), that frame gives it - every such frame, the
     nearer weighing more, averaged. Inside the hole the flow is the removed
     thing's own movement, so the flow there is rebuilt from what's round
     it (track.fill_flow) - the background moved with its surroundings;
  2. from the frame before's finished result, carried along the flow: one
     more voice in that average (CARRY_WEIGHT), so the fill holds still from
     frame to frame - and on its own where no frame showed the background,
     so what had to be invented stays put;
  3. by the model (a filler: frame and what's left -> the frame filled) -
     only what no frame nearby ever showed. In a moving shot that's little;
     on a locked-off shot of something standing still it's all of it, filled
     once and then carried (2).

Measured on a 48-frame panning shot of a brick wall with a post walking
across (2026-10-07): against LaMa a frame at a time, half the error (0.022
against 0.041, 0..1) and a third of the flicker, in a third of the time -
the model ran on 1 frame of the 48.

Then it's put in as fill.py does: all the way where the mask is, fading out
just past it, and by the mask's own strength where it's feathered.

The warping is done on the hole's box (with room round it), not the whole
frame: on a 4K frame a hole is usually a small part of it.
"""

from __future__ import annotations

import numpy as np

import cv2

import fill
import footage
import track

RADIUS = 6                 # frames either side a hole may be filled from
CARRY_WEIGHT = 1.0         # the frame before's fill, against a neighbour 1 frame away (1.0)
FAINT, FULL = fill.FAINT, fill.FULL


def box_of(mask: np.ndarray, pad: int, h: int, w: int) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad),
            min(w, int(xs.max()) + 1 + pad), min(h, int(ys.max()) + 1 + pad))


def warp_box(src: np.ndarray, flow: np.ndarray, box) -> np.ndarray:
    """src (a whole frame) drawn where things are inside `box` of the
    frame `flow` belongs to (flow: that box's part of the full-size flow)."""
    x0, y0, x1, y1 = box
    gx, gy = np.meshgrid(np.arange(x0, x1, dtype=np.float32), np.arange(y0, y1, dtype=np.float32))
    return cv2.remap(src, gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def inside_box(flow: np.ndarray, box, h: int, w: int) -> np.ndarray:
    x0, y0, x1, y1 = box
    gx, gy = np.meshgrid(np.arange(x0, x1, dtype=np.float32), np.arange(y0, y1, dtype=np.float32))
    x, y = gx + flow[..., 0], gy + flow[..., 1]
    return (x >= 0) & (x <= w - 1) & (y >= 0) & (y <= h - 1)


MISMATCH = 0.035           # mean difference (0..1 grey) round the hole past which what was invented no longer fits
FADE = 8                   # frames a new invention fades in over the old one
BLURS = (0.0, 0.6, 1.0, 1.5, 2.2, 3.0, 4.0, 5.5, 7.5, 10.0)   # small-size sigmas tried, to match a frame's focus
DENOISE = 1.0              # small-size sigma both are smoothed by before their focus is compared (grain)
SHARPER = 0.85             # blurring this frame matches the kept one this much better: it's sharper - invent again


def ring_of(hole_small: np.ndarray) -> np.ndarray:
    """The background round a hole (small size): what tells how what's behind
    it moved - a band about half the hole's size wide, kept clear of the
    hole's own edge (whose flow is the removed thing's)."""
    area = float(hole_small.sum())
    width = int(max(12, min(80, np.sqrt(area) * 0.6)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * width + 1, 2 * width + 1))
    near = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * track.FLOW_MARGIN + 1, 2 * track.FLOW_MARGIN + 1))
    out = cv2.dilate(hole_small.astype(np.uint8), k) > 0
    return out & ~(cv2.dilate(hole_small.astype(np.uint8), near) > 0)


class Backdrop:
    """What had to be invented behind a hole - the frame it was made on,
    finished - and how to take it to each later frame: one homography
    matched straight back to that frame on the background round the hole
    (track.Anchor, as tracking matches back to its keyframe), not frame to
    frame. Handed on frame to frame, each step's resampling and error stayed
    in the next: on the user's mirror shot (a camera's reflection, in the
    same place in every frame - nothing behind it ever shown, so all of it
    invented once and carried 178 frames) it slid, smeared, and darkened
    from the frame's edge in (2026-10-07).

    It's made to look as each frame does (look()): as soft - the background
    round the hole blurred to match, the focus found by trying BLURS - and
    as bright. A focus pull or a change of light no longer means inventing
    it again; only a frame sharper than the kept one does (a fill can't be
    made sharper) - or one that still doesn't match (MISMATCH). Then the
    frame is invented again and kept instead, the old one fading out over
    FADE frames, so there's no jump; over a shot it settles on its
    sharpest view, softened from there."""

    def __init__(self):
        self.kept = None            # {"t", "image", "anchor", "ring", "gray"}
        self.look = (0.0, 1.0)      # this frame against the kept one: (blur sigma at the small size, brightness)
        self.invented = None        # (frame number, its full-size mask of what was invented there, not shown)
        self.old = None             # the one before, fading out: {"kept", "total", "left"}
        self.total = None           # kept frame -> the last frame reached (small size)
        self.last = None            # (frame number, its grey, its ring) - the last frame reached

    def keep(self, t, image, gray, ring):
        if self.kept is not None and self.total is not None:
            self.old = {"kept": self.kept, "total": self.total, "left": FADE}
        self.kept = {"t": t, "image": image, "gray": gray, "ring": ring,
                     "anchor": track.Anchor(gray, ring.astype(np.float32), refine=False)}
        self.total = np.eye(3)
        self.last = (t, gray, ring)

    def reach(self, t, gray, ring):
        """kept frame -> t (small size), or None when it can't be told; and
        how unlike the kept frame the background round the hole is there."""
        if self.kept is None or self.last is None:
            return None, 1.0
        lt, lgray, lring = self.last
        step = track.movement(lgray, gray, lring.astype(np.float32))
        guess = None if step is None else np.vstack([step, [0, 0, 1]]).astype(np.float64) @ self.total
        h = self.kept["anchor"].match(gray, ring.astype(np.float32), guess)
        if h is not None and guess is not None:
            # Frame to frame is nearly exact over a short span, the match straight back a little noisy
            # (a locked-off shot came out softer than handing on did): the step's kept while the two
            # agree within a pixel, the match taking over only once the steps have drifted past that.
            c = self.kept["anchor"].corners
            if c is not None and float(np.abs(cv2.perspectiveTransform(c, h) - cv2.perspectiveTransform(c, guess)).max()) < 1.0:
                h = guess
        h = h if h is not None else guess
        if h is None:
            return None, 1.0
        self.total, self.last = h, (t, gray, ring)
        if self.old is not None:
            self.old["left"] -= 1
            if self.old["left"] <= 0:
                self.old = None
        # How alike: the kept frame's background round the hole, brought here, against this frame's.
        sh, sw = gray.shape
        moved = cv2.warpPerspective(self.kept["gray"], h, (sw, sh), flags=cv2.INTER_LINEAR)
        there = cv2.warpPerspective(np.ones_like(self.kept["gray"]), h, (sw, sh), flags=cv2.INTER_NEAREST) > 0
        where = ring & there
        if where.sum() < 50:
            self.look = (0.0, 1.0)
            return h, 1.0
        self.look, unlike = look(moved, gray, where)
        return h, unlike

    def seen(self, h_small, box, full_hw, small_hw):
        """The kept frame's finished picture brought to `box` of this frame,
        and where it covers - mixed with the one fading out, if any."""
        out, ok = self._warp(self.kept["image"], h_small, box, full_hw, small_hw)
        if self.old is not None:
            # The old invention: to the new kept frame as it was taken there, then on with the new one's.
            o, ook = self._warp(self.old["kept"]["image"], h_small @ self.old["total"], box, full_hw, small_hw)
            a = np.float32(self.old["left"] / (FADE + 1))
            both = ok & ook
            out[both] = out[both] * (1 - a) + o[both] * a
        # As soft and as bright as this frame (look()): the blur found at the small size, at full size.
        sigma, gain = self.look
        if sigma > 0:
            out = cv2.GaussianBlur(out, (0, 0), sigma * full_hw[1] / small_hw[1])
        if gain != 1.0:
            out = out * np.float32(gain)
        return out, ok

    @staticmethod
    def _warp(image, h_small, box, full_hw, small_hw):
        (H, W), (sh, sw) = full_hw, small_hw
        k = np.diag([sw / W, sh / H, 1.0])
        h_full = np.linalg.inv(k) @ h_small @ k
        x0, y0, x1, y1 = box
        size = (x1 - x0, y1 - y0)
        corners = np.float32([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]])
        if float(np.abs(cv2.perspectiveTransform(corners, h_full) - corners).max()) < 0.25:
            # Not moved (a locked-off shot): as it was, not put through a warp that softens it a little each time.
            return image[y0:y1, x0:x1].copy(), np.ones((size[1], size[0]), bool)
        m = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], np.float64) @ h_full
        # A pixel or few past the kept frame's edge: its edge pixel, not black (that crept in from the
        # frame's edge before - nothing there, so the model filled it, and it darkened as it was carried).
        out = cv2.warpPerspective(image, m, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        inside = cv2.warpPerspective(np.ones(image.shape[:2], np.uint8), m, size, flags=cv2.INTER_NEAREST,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 0
        near = cv2.dilate(inside.astype(np.uint8), np.ones((17, 17), np.uint8)) > 0
        return out, near


def look(kept: np.ndarray, now: np.ndarray, where: np.ndarray) -> tuple[tuple[float, float], float]:
    """How a frame (`now`, small grey) looks against the kept frame brought
    to it (`kept`), round the hole (`where`): ((the blur that makes the kept
    one as soft - a sigma at the small size - and the brightness that makes
    it as bright), how unlike they still are, 0..1). Unlike is 1 when this
    frame is the sharper - blurring it matches better than blurring the kept
    one - since a fill can't be sharpened: it's invented again then."""
    ys, xs = np.nonzero(where)
    pad = int(3 * BLURS[-1]) + 2
    # Both a little smoothed first: grain is different in every frame, and softening one frame's grain
    # away always makes the two look more alike - which read as "softer", and blurred fills that weren't.
    kept = cv2.GaussianBlur(kept.astype(np.float32), (0, 0), DENOISE)
    now = cv2.GaussianBlur(now.astype(np.float32), (0, 0), DENOISE)
    y0, y1 = max(0, ys.min() - pad), min(now.shape[0], ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(now.shape[1], xs.max() + 1 + pad)
    k, n, w = (a[y0:y1, x0:x1] for a in (kept.astype(np.float32), now.astype(np.float32), where))
    target = n[w]

    def score(img, ref):
        soft = img[w]
        gain = float(np.clip(ref.mean() / max(soft.mean(), 1e-3), 0.6, 1.6))
        return float(np.abs(soft * gain - ref).mean()) / 255.0, gain

    best = (1.0, 0.0, 1.0)
    for sigma in BLURS:
        img = cv2.GaussianBlur(k, (0, 0), sigma) if sigma else k
        d, gain = score(img, target)
        if d < best[0]:
            best = (d, sigma, gain)
    # The other way round: does softening this frame match the kept one better? Then it's the sharper.
    sharper = min(score(cv2.GaussianBlur(n, (0, 0), sigma), k[w])[0] for sigma in BLURS[1:6])
    unlike = 1.0 if sharper < best[0] * SHARPER and best[1] == 0.0 else best[0]
    return (best[1], best[2]), unlike


class Clip:
    """What the filling reads: frames (float 0..1 BGR), their small greys
    for the flow, and their masks (float 0..1, full size) - each made once
    and kept while it's near the frame being filled."""

    def __init__(self, frame_of, mask_of):
        self._frame_of, self._mask_of = frame_of, mask_of
        self.frames, self.grays, self.masks = {}, {}, {}

    def frame(self, i):
        if i not in self.frames:
            f = self._frame_of(i)
            if f is None:
                return None
            self.frames[i] = footage.to_float(f)
            self.grays[i] = track.gray_small(f)
        return self.frames[i]

    def gray(self, i):
        return self.grays[i] if self.frame(i) is not None else None

    def mask(self, i):
        if i not in self.masks:
            self.masks[i] = self._mask_of(i)
        return self.masks[i]

    def keep(self, lo, hi):
        """Forgets what's outside lo .. hi."""
        for d in (self.frames, self.grays, self.masks):
            for i in [i for i in d if i < lo or i > hi]:
                del d[i]


def borrow(clip: Clip, t: int, hole: np.ndarray, hole_small: np.ndarray, kernel, flow: track.Flow,
           radius: int = RADIUS, lo: int = 0, hi: int | None = None):
    """Step 1 of fill_frame, on its own (rose_fill uses it too): frame t's
    hole as the frames round it show it - every one that shows a hole
    pixel's background gives it, the nearer the more. Taking only the
    nearest made a patchwork - strips from different frames, each a fraction
    of a pixel off its neighbours - that showed as combed edges and shifted
    every frame. Returns (the hole's box (x0, y0, x1, y1), the sum of what
    was seen there, its weight - 0 where no frame showed it)."""
    f = clip.frame(t)
    h, w = f.shape[:2]
    hi = hi if hi is not None else t + radius

    def flow_to(s):
        fl = track.fill_flow(flow.small(clip.gray(t), clip.gray(s)), hole_small)
        return track.to_size(fl, h, w, flow=True)

    b = box_of(hole, 2, h, w)
    x0, y0, x1, y1 = b
    want = hole[y0:y1, x0:x1]
    total = np.zeros(want.shape + (f.shape[2],), np.float32)
    weight = np.zeros(want.shape, np.float32)
    for d in (d for d in range(-radius, radius + 1) if d):
        s = t + d
        if s < lo or s > hi or clip.frame(s) is None:
            continue
        part = flow_to(s)[y0:y1, x0:x1]
        seen = warp_box(clip.frame(s), part, b)
        theirs = clip.mask(s)
        hidden = np.zeros(want.shape, bool) if theirs is None else             warp_box(cv2.dilate((theirs > FAINT).astype(np.uint8), kernel).astype(np.float32), part, b) > 0.01
        ok = want & ~hidden & inside_box(part, b, h, w)
        wd = np.float32(1.0 / abs(d))
        total[ok] += seen[ok] * wd
        weight[ok] += wd
    return b, total, weight


def fill_frame(clip: Clip, t: int, previous: tuple | None, filler, flow: track.Flow,
               radius: int = RADIUS, lo: int = 0, hi: int | None = None,
               backdrop: Backdrop | None = None) -> tuple[np.ndarray, dict]:
    """Frame t with its mask's hole filled (float 0..1 BGR), and where each
    part came from: {"neighbours", "carried", "model"} as pixel counts.
    previous: (number, its finished frame) - the frame before, filled - or None.
    backdrop: what was invented, kept on its own frame (Backdrop) - used in
    place of `previous`, carried by one homography from there; renewed when
    it no longer fits, or there's none yet.
    lo / hi: the frames there are to fill from."""
    f = clip.frame(t)
    h, w = f.shape[:2]
    m = clip.mask(t)
    said = {"neighbours": 0, "carried": 0, "model": 0}
    if m is None or not (m > FAINT).any():
        return f, said
    grow = fill.grow_px(f.shape)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
    hole = cv2.dilate((m > FAINT).astype(np.uint8), k) > 0
    full = cv2.dilate((m >= FULL).astype(np.uint8), k) > 0
    sh, sw, _ = track.small_size(h, w)
    hole_small = cv2.resize(hole.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST) > 0
    hole_small |= cv2.resize(hole.astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA) > 0.01
    filled = f.copy()
    todo = hole.copy()
    hi = hi if hi is not None else t + radius

    def flow_to(s):
        fl = track.fill_flow(flow.small(clip.gray(t), clip.gray(s)), hole_small)
        return track.to_size(fl, h, w, flow=True)

    b, total, weight = borrow(clip, t, hole, hole_small, k, flow, radius, lo, hi)
    x0, y0, x1, y1 = b
    want = hole[y0:y1, x0:x1]
    found = weight > 0
    said["neighbours"] = int(found.sum())
    # 2. the frame before's result, carried along the flow: alone where no
    # frame nearby showed the background, so it stays put; and as one more
    # voice where they did (CARRY_WEIGHT), so the fill doesn't flicker
    # either. With a Backdrop, only what was real there (a frame showed it,
    # some time back): what was invented comes from the Backdrop instead -
    # kept on the frame it was made on, carried by one homography, as soft
    # and as bright as this frame. Handed on frame to frame, an invention
    # slid and smeared (a mirror shot, 178 frames: nothing behind ever
    # shown); real background is refreshed as frames show it, so handing it
    # on is right - kept, it froze an old guess where frames had since shown
    # the real thing (a pan past a moving thing).
    invented_before = None
    if backdrop is not None and backdrop.invented is not None and previous is not None \
            and backdrop.invented[0] == previous[0]:
        invented_before = backdrop.invented[1]
    if previous is not None and clip.gray(previous[0]) is not None:
        part = flow_to(previous[0])[y0:y1, x0:x1]
        seen = warp_box(previous[1], part, b)
        ok = want & inside_box(part, b, h, w)
        if invented_before is not None:
            ok &= ~(warp_box(invented_before.astype(np.float32), part, b) > 0.5)
        said["carried"] = int((ok & ~found).sum())
        wc = np.where(found, np.float32(CARRY_WEIGHT), np.float32(1.0))
        total[ok] += seen[ok] * wc[ok][:, None]
        weight[ok] += wc[ok]
    have = weight > 0
    renew, fading = False, None
    invented = np.zeros((h, w), bool)
    if backdrop is not None:
        ring = ring_of(hole_small)
        hb, unlike = backdrop.reach(t, clip.gray(t), ring) if backdrop.kept is not None else (None, 1.0)
        said["unlike"] = round(unlike, 4)
        missing = want & ~have
        if missing.any():
            if hb is not None and unlike <= MISMATCH:
                seen, cover = backdrop.seen(hb, b, (h, w), (sh, sw))
                ok = missing & cover
                said["kept"] = int(ok.sum())
                total[ok] += seen[ok]
                weight[ok] += 1.0
                invented[y0:y1, x0:x1] |= ok
                have = weight > 0
            else:
                renew = True                    # invented again here (the model), and kept
                if hb is not None and backdrop.kept is not None:
                    fading = backdrop.seen(hb, b, (h, w), (sh, sw))
    region = filled[y0:y1, x0:x1]
    region[have] = total[have] / weight[have][:, None]
    todo[y0:y1, x0:x1] &= ~have
    # 3. the model, for what no frame showed
    if todo.any():
        said["model"] = int(todo.sum())
        invented |= todo
        filled = filler(filled, todo)
    if backdrop is not None:
        if renew:
            old = backdrop.kept is not None
            backdrop.keep(t, filled.copy(), clip.gray(t), ring)
            said["new"] = "again" if old else "first"
            if old and fading is not None:
                # This frame too fades from the old invention to the new, as the next FADE do (Backdrop.seen).
                o, cover = fading
                a = np.float32(FADE / (FADE + 1))
                mix = want & cover & invented[y0:y1, x0:x1]
                part = filled[y0:y1, x0:x1]
                part[mix] = part[mix] * (1 - a) + o[mix] * a
        backdrop.invented = (t, invented)
    # Put in: all the way where the mask is (grown past an object's soft
    # edge), fading out a couple of pixels past that, the mask's own
    # strength where it's feathered.
    seam = cv2.GaussianBlur(full.astype(np.float32), (0, 0), max(1.0, grow / 2.5))
    a = np.maximum(np.maximum(seam, full.astype(np.float32)), m)
    a = np.where(hole | (seam > 0.002), a, 0.0)[:, :, None]
    return (f * (1 - a) + filled * a).astype(np.float32), said


def model_filler(filler: "fill.Filler"):
    """fill.Filler as a filler for fill_frame: float BGR and a hole in,
    the frame filled out."""
    def run(img, hole):
        out, _box = filler.fill(img, hole.astype(np.float32))
        return out
    return run


def inpaint_filler(img, hole):
    """OpenCV's own inpainting as a filler - no model; for tests, and a
    stand-in where the model isn't there."""
    f8 = np.clip(img * 255 + 0.5, 0, 255).astype(np.uint8)
    out = cv2.inpaint(f8, hole.astype(np.uint8), 5, cv2.INPAINT_TELEA).astype(np.float32) / 255.0
    return np.where(hole[:, :, None], out, img)
