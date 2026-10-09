#!/usr/bin/env python3
"""
How things move from frame to frame, for Generative's footage: optical
flow (OpenCV's DIS - dense, no model, fast on the CPU), masks carried
along it from the frames they were painted on, and mattes held steady so
a cut-out doesn't flicker. Runs in the engine's environment, like fill.py.

Flow is worked out on a small grey copy of each frame (FLOW_SIDE on its
longer side): it's the movement that matters, and DIS finds it as well at
that size, many times faster. Masks are carried at that size too - a
clip part's worth of them fits in memory where full-size ones wouldn't -
and scaled up to the frame's size only when that frame is worked on.

A flow between frames a and b is, for each pixel p of a, how far it moved:
p in a is p + flow(p) in b. warp(b, flow) is then b drawn where things are
in a - b brought back to line up with a.

Masks painted on a few frames (keyframes) are carried frame to frame, each
step moved as what's inside it moved (see movement()); between two
keyframes each frame takes both, weighted by how near each keyframe is - so
a mask drifts least where it was painted, and another keyframe is how it's
corrected.
"""

from __future__ import annotations

import numpy as np

import cv2

FLOW_SIDE = 960


def small_size(h: int, w: int, side: int = FLOW_SIDE) -> tuple[int, int, float]:
    """(height, width, scale) of the small copy of an h x w frame."""
    k = min(1.0, side / max(h, w))
    return max(1, round(h * k)), max(1, round(w * k)), k


def gray_small(frame: np.ndarray, side: int = FLOW_SIDE) -> np.ndarray:
    """The frame as 8-bit grey, at most `side` on its longer side."""
    h, w = frame.shape[:2]
    sh, sw, k = small_size(h, w, side)
    f = frame
    if f.dtype == np.uint16:
        f = (f >> 8).astype(np.uint8)
    elif f.dtype != np.uint8:
        f = np.clip(f * 255.0, 0, 255).astype(np.uint8)
    g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f
    return cv2.resize(g, (sw, sh), interpolation=cv2.INTER_AREA) if k < 1.0 else g


def to_size(a: np.ndarray, h: int, w: int, flow: bool = False) -> np.ndarray:
    """A small mask or flow back at a frame's size (a flow's lengths scaled with it)."""
    sh, sw = a.shape[:2]
    if (sh, sw) == (h, w):
        return a
    out = cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)
    if flow:
        out = out * np.array([w / sw, h / sh], np.float32)
    return out


class Flow:
    """DIS optical flow between small grey frames (gray_small)."""

    def __init__(self, preset: int = cv2.DISOPTICAL_FLOW_PRESET_MEDIUM):
        self.dis = cv2.DISOpticalFlow_create(preset)

    def small(self, ga: np.ndarray, gb: np.ndarray) -> np.ndarray:
        """h x w x 2 float32 at the small frames' size: p in a is p + flow(p) in b."""
        return self.dis.calc(ga, gb, None).astype(np.float32)

    def full(self, ga: np.ndarray, gb: np.ndarray, h: int, w: int) -> np.ndarray:
        """The same at an h x w frame's size."""
        return to_size(self.small(ga, gb), h, w, flow=True)


def _grid(h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    return np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))


def warp(img: np.ndarray, flow: np.ndarray, border=cv2.BORDER_CONSTANT, value=0) -> np.ndarray:
    """img (frame b) drawn where things are in frame a, given a's flow to b."""
    h, w = flow.shape[:2]
    gx, gy = _grid(h, w)
    return cv2.remap(img, gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_LINEAR,
                     borderMode=border, borderValue=value)


def landed_inside(flow: np.ndarray, margin: float = 0.0) -> np.ndarray:
    """Which of a's pixels land inside frame b."""
    h, w = flow.shape[:2]
    gx, gy = _grid(h, w)
    x, y = gx + flow[..., 0], gy + flow[..., 1]
    return (x >= margin) & (x <= w - 1 - margin) & (y >= margin) & (y <= h - 1 - margin)


FLOW_MARGIN = 12          # px (small size): how far round a hole its own movement smears into the flow


def fill_flow(flow: np.ndarray, hole: np.ndarray, margin: int = FLOW_MARGIN) -> np.ndarray:
    """The flow inside `hole` replaced by what's round it. Inside a painted
    object the flow is the object's own movement; what's behind it moved
    with what's round it - which is what finding the background needs. DIS
    smooths the object's movement out past its edge too (its patches are 8
    px, then smoothed), so the flow is rebuilt from a ring `margin` pixels
    out. Best given at the small size: flow inside a hole is smooth.

    Rebuilt as a weighted average of the flow round it (normalized
    convolution, as wide as the hole is deep): smooth inside, and exactly
    the surroundings' movement when that's the same all round - a pan. Not
    cv2.inpaint: on float flow it left the rebuilt movement jumping by ~2 px
    from one column to the next, which combed every edge copied through it."""
    if not hole.any():
        return flow
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1))
    hs = cv2.dilate(hole.astype(np.uint8), k)
    known = (hs == 0).astype(np.float32)
    if not known.any():
        return flow
    depth = float(cv2.distanceTransform(hs, cv2.DIST_L2, 5).max())
    sigma = max(2.0, depth)
    # Worked out on a grid sigma / 3 coarser: what comes out is a wide, smooth
    # average either way, and blurring at full size with a sigma this wide was
    # a third of a 4K footage frame's time (1.1 s of 3.5, 2026-10-07). Area
    # averages of flow * known over known stay exact for a pan.
    h, w = flow.shape[:2]
    f = max(1, int(sigma // 3))
    sw, sh = max(1, -(-w // f)), max(1, -(-h // f))
    small_known = cv2.resize(known, (sw, sh), interpolation=cv2.INTER_AREA) if f > 1 else known
    den = cv2.GaussianBlur(small_known, (0, 0), sigma / f)
    out = flow.copy()
    for c in range(2):
        weighted = flow[..., c] * known
        small = cv2.resize(weighted, (sw, sh), interpolation=cv2.INTER_AREA) if f > 1 else weighted
        num = cv2.GaussianBlur(small, (0, 0), sigma / f)
        fallback = float(weighted.sum() / known.sum())
        filled = np.where(den > 1e-4, num / np.maximum(den, 1e-4), fallback).astype(np.float32)
        if f > 1:
            filled = cv2.resize(filled, (w, h), interpolation=cv2.INTER_LINEAR)
        out[..., c] = np.where(hs > 0, filled, flow[..., c])
    return out


# ------------------------------------------------------ a thing's movement --
#
# Dense flow finds where everything goes, but at a small thing's edge - and
# inside one moving against its surroundings - it lags (seen: 2.3 px for a
# square that moved 3), and carried frame after frame the lag adds up. So a
# masked thing is followed as trackers do: points with texture inside it,
# each followed closely (Lucas-Kanade, checked forward and back), and one
# movement for all of them - shift, turn and scale - that most agree on.
# Dense flow is what's left for a mask with nothing in it to follow.

LK = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))


def local_contrast(g: np.ndarray, sigma: float = 6.0) -> np.ndarray:
    """Each pixel against its surroundings (how far from their mean, in their
    spread), as 8-bit: what's left of a picture once its brightness and
    contrast are taken out - the same however the light changes."""
    f = g.astype(np.float32)
    d = f - cv2.GaussianBlur(f, (0, 0), sigma)
    spread = np.sqrt(cv2.GaussianBlur(d * d, (0, 0), sigma)) + 2.0
    return np.clip(d / spread * 40 + 128, 0, 255).astype(np.uint8)


def movement(ga: np.ndarray, gb: np.ndarray, where: np.ndarray, at_least: int = 6) -> np.ndarray | None:
    """2 x 3: how what's in `where` (a mask on frame a) moved to frame b -
    or None when there's too little in it to follow."""
    inside = (where > 0.5).astype(np.uint8)
    if inside.sum() < 16:
        return None
    # Points well inside: at its edge a point is half what's behind it, which moves its own way.
    area = cv2.erode(inside, np.ones((5, 5), np.uint8))
    if area.sum() < 16:
        area = inside
    pts = cv2.goodFeaturesToTrack(ga, maxCorners=300, qualityLevel=0.01, minDistance=3, mask=area, blockSize=5)
    if pts is None or len(pts) < at_least:
        return None
    # Lucas-Kanade takes a change of brightness for movement: a shot darkening 1% a frame was followed the wrong
    # way, 13 px off in 29 frames - and evening the frames' brightness out first overshot instead. When the
    # light changes between the two (the whole frame's brightness or contrast), it's followed on their local
    # contrast (local_contrast()), which a change of light doesn't touch: right within a few % at any
    # brightness. With the light steady, on the frames as they are - a hair more exact.
    ma, mb, sa, sb = float(ga.mean()), float(gb.mean()), float(ga.std()), float(gb.std())
    if ma > 1e-3 and sa > 1e-3 and (abs(mb / ma - 1) > 0.003 or abs(sb / sa - 1) > 0.005):
        ga, gb = local_contrast(ga), local_contrast(gb)
    nxt, ok, _ = cv2.calcOpticalFlowPyrLK(ga, gb, pts, None, **LK)
    back, ok2, _ = cv2.calcOpticalFlowPyrLK(gb, ga, nxt, None, **LK)
    good = (ok.ravel() == 1) & (ok2.ravel() == 1) & (np.linalg.norm((back - pts).reshape(-1, 2), axis=1) < 0.5)
    if good.sum() < at_least:
        return None
    m, inliers = cv2.estimateAffinePartial2D(pts[good], nxt[good], method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if m is None or inliers is None or inliers.sum() < at_least:
        return None
    return m.astype(np.float32)


def move(img: np.ndarray, m: np.ndarray, size: tuple[int, int] | None = None) -> np.ndarray:
    """img drawn where a movement m (frame a to b) takes it."""
    h, w = size or img.shape[:2]
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def scaled_movement(m: np.ndarray, k: float) -> np.ndarray:
    """A movement found on small frames, for frames 1 / k times the size."""
    out = m.copy()
    out[:, 2] /= k
    return out


# ---------------------------------------------------- matched to the keyframe --
#
# Followed frame to frame only, each step's small error stays in the next -
# seen on a push-in past a plaque (the user's FX3 clip, 2026-10-07): 5 px
# off at 4K after 48 frames, turned and scaled a little less than the
# plaque, and the end of its text came out from under the paint. So each
# frame is also matched straight to the keyframe it's carried from (SIFT
# features inside the paint, one homography): nothing to add up. The step
# from the frame before is the guess it's checked against, and what's used
# when the match is unsure (motion blur: there, 2 frames of 48).

ANCHOR_FEATURES = 3000
ANCHOR_MIN = 25           # matches that agree, to trust a frame matched straight to its keyframe
ANCHOR_AGREE = 3.0        # px (small size), or 3% of the mask's size: how far from the guess it may land


class Anchor:
    """A keyframe's features inside its mask, to match later frames to directly."""

    def __init__(self, gray: np.ndarray, mask: np.ndarray, region: np.ndarray | None = None, refine: bool = True):
        """region: where to match (SAM 2's object) - the paint itself when None.
        refine False: no ECC (video_fill.Backdrop: its box holds the hole, whose
        content moves its own way)."""
        self.polish = refine
        self.sift = cv2.SIFT_create(ANCHOR_FEATURES)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        inside = (mask > 0.5).astype(np.uint8)
        area = inside if region is None else (region > 0.5).astype(np.uint8)
        self.kp, self.desc = self.sift.detectAndCompute(gray, cv2.dilate(area, np.ones((7, 7), np.uint8)))
        ys, xs = np.nonzero(inside)
        if len(xs):
            x0, x1, y0, y1 = float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())
            self.corners = np.float32([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]])
            self.reach = max(ANCHOR_AGREE, 0.03 * float(np.hypot(x1 - x0, y1 - y0)))
            # What ECC lines up (refine()): the keyframe round the paint.
            x, y, w, h = cv2.boundingRect(cv2.dilate(area, np.ones((9, 9), np.uint8)))
            self.patch = gray[y:y + h, x:x + w].astype(np.float32)
            self.at = np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], np.float64)
        else:
            self.corners = None

    def refine(self, gray: np.ndarray, h: np.ndarray) -> np.ndarray:
        """The match lined up to a fraction of a pixel (ECC on the keyframe
        round the paint): features land to about a pixel at the small size -
        4 at 4K - and that was what was left (FX3: 5 px at frame 48, 1.4
        refined). As it was, if ECC doesn't settle or wanders off."""
        warp = (h @ self.at).astype(np.float32)
        try:
            _, warp = cv2.findTransformECC(self.patch, gray.astype(np.float32), warp, cv2.MOTION_HOMOGRAPHY,
                                           (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5), None, 3)
        except cv2.error:
            return h
        out = warp.astype(np.float64) @ np.linalg.inv(self.at)
        a = cv2.perspectiveTransform(self.corners, h)[0]
        b = cv2.perspectiveTransform(self.corners, out)[0]
        return out if np.all(np.isfinite(b)) and float(np.abs(a - b).max()) < 2.0 else h

    def match(self, gray: np.ndarray, where: np.ndarray, guess: np.ndarray | None) -> np.ndarray | None:
        """3 x 3: keyframe -> this frame, matched straight - or None when it
        isn't sure, or lands too far from `guess` (the step from the frame
        before) and isn't three times as sure as it needs to be: then it's
        the guess that's gone wrong (through motion blur, seen: 16 px off,
        and kept there without this). No guess: only a match twice as sure
        is taken."""
        if self.corners is None or self.desc is None or len(self.kp) < ANCHOR_MIN:
            return None
        region = cv2.dilate((where > 0.5).astype(np.uint8), np.ones((31, 31), np.uint8))
        kp, desc = self.sift.detectAndCompute(gray, region)
        if desc is None or len(kp) < ANCHOR_MIN:
            return None
        good = [p[0] for p in self.matcher.knnMatch(self.desc, desc, k=2)
                if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
        need = ANCHOR_MIN if guess is not None else 2 * ANCHOR_MIN
        if len(good) < need:
            return None
        src = np.float32([self.kp[m.queryIdx].pt for m in good])
        dst = np.float32([kp[m.trainIdx].pt for m in good])
        h, inliers = cv2.findHomography(src, dst, cv2.RANSAC, 1.0)
        if h is None or inliers is None or int(inliers.sum()) < need:
            return None
        if guess is not None and int(inliers.sum()) < 3 * ANCHOR_MIN:
            a = cv2.perspectiveTransform(self.corners, h)[0]
            b = cv2.perspectiveTransform(self.corners, guess)[0]
            if float(np.abs(a - b).max()) > self.reach:
                return None
        return self.refine(gray, h) if self.polish else h


def _carry_from(k: int, frames: list, grays: dict, mask: np.ndarray, flow: "Flow", anchor: bool, tick,
                obj: dict | None = None) -> dict:
    """The keyframe k's mask carried through `frames` (in order, away from k):
    moved by one homography from k - stepped frame to frame, matched
    straight to k - or, once nothing in it can be followed, along the dense
    flow from there on (as carry_step). With `obj` (track_sam.objects()'s,
    for k): what's followed and matched is SAM 2's object on each frame,
    not the paint - so what's round it can't pull the paint along - and
    when the paint covers the object, the object grown by the paint's reach
    is added, so a thing changing shape stays covered."""
    out = {}
    h, w = mask.shape[:2]
    found = (obj or {}).get("masks", {})

    def region(t, fallback):
        r = found.get(t)
        return r if r is not None and (r > 0.5).sum() >= 16 else fallback

    a = Anchor(grays[k], mask, region(k, None)) if anchor else None
    total = np.eye(3, dtype=np.float64)         # k -> the last frame, while it's known
    m, prev = mask, k
    for t in frames:
        if total is not None:
            step = movement(grays[prev], grays[t], region(prev, m))
            guess = None if step is None else np.vstack([step, [0, 0, 1]]).astype(np.float64) @ total
            straight = a.match(grays[t], region(t, m), guess) if a is not None else None
            if straight is not None:
                total = straight
            elif guess is not None:
                total = guess
            else:
                total = None
                m = carry(m, flow.small(grays[t], grays[prev]))
            if total is not None:
                m = np.clip(cv2.warpPerspective(mask, total, (w, h), flags=cv2.INTER_LINEAR,
                                                borderMode=cv2.BORDER_CONSTANT, borderValue=0), 0.0, 1.0)
        else:
            # Lost (blur, or nothing to follow for a while): back to the keyframe as soon as it's found again.
            again = a.match(grays[t], region(t, m), None) if a is not None else None
            if again is not None:
                total = again
                m = np.clip(cv2.warpPerspective(mask, total, (w, h), flags=cv2.INTER_LINEAR,
                                                borderMode=cv2.BORDER_CONSTANT, borderValue=0), 0.0, 1.0)
            else:
                m = carry_step(m, grays[prev], grays[t], flow)
        out[t] = m.astype(np.float32)
        if obj and obj.get("covers") and found.get(t) is not None:
            out[t] = np.maximum(out[t], grown(found[t], obj["margin"], obj["ramp"]))
        prev = t
        tick()
    return out


def grown(found: np.ndarray, margin: float, ramp: float) -> np.ndarray:
    """An object mask grown by `margin` px, its edge fading over `ramp` px -
    the paint's reach past what it covers, kept round it as it changes shape."""
    if not (found > 0.5).any():
        return np.zeros_like(found, np.float32)
    d = cv2.distanceTransform((found <= 0.5).astype(np.uint8), cv2.DIST_L2, 5)
    return np.clip(1.0 - (d - margin) / ramp, 0.0, 1.0).astype(np.float32)


# -------------------------------------------------------- carried masks --

def carry(mask: np.ndarray, flow_back: np.ndarray) -> np.ndarray:
    """A mask on one frame, carried to the next along dense flow: flow_back
    is the next frame's flow to this one."""
    return np.clip(warp(mask.astype(np.float32), flow_back), 0.0, 1.0)


def carry_step(mask: np.ndarray, g_from: np.ndarray, g_to: np.ndarray, flow: "Flow") -> np.ndarray:
    """A mask on one frame carried to another (next or before): moved as
    what's in it moved, or along the dense flow if nothing in it can be followed."""
    m = movement(g_from, g_to, mask)
    if m is not None:
        return np.clip(move(mask.astype(np.float32), m), 0.0, 1.0)
    return carry(mask, flow.small(g_to, g_from))


def carry_masks(grays: dict, keyframes: dict, start: int, end: int, flow: Flow | None = None,
                progress=None, anchor: bool = True, objects: dict | None = None) -> dict:
    """Masks for every frame start .. end - 1, at the small size, from those
    painted on a few of them. grays: number -> gray_small frame (the run);
    keyframes: number -> small float 0..1 mask. Before the first keyframe
    and after the last, carried from it alone; between two, each frame
    mixes both, the nearer weighing more. Each frame is matched straight to
    the keyframe it's carried from (Anchor) unless `anchor` is False - then
    frame to frame only. objects: track_sam.objects()'s, for following
    SAM 2's object rather than the paint (Track's "Object"). progress(done,
    total) if given."""
    flow = flow or Flow()
    keys = sorted(k for k in keyframes if start <= k < end)
    if not keys:
        raise ValueError("No mask painted in that part of the clip.")
    total, done = 2 * (end - start), [0]

    def tick():
        done[0] += 1
        if progress:
            progress(done[0], total)

    forward, backward = {}, {}
    for n, k in enumerate(keys):                     # forward, to the next keyframe or the end
        stop = keys[n + 1] if n + 1 < len(keys) else end
        forward[k] = keyframes[k].astype(np.float32)
        forward.update(_carry_from(k, list(range(k + 1, stop)), grays, forward[k], flow, anchor, tick,
                                   (objects or {}).get(k)))
    for n, k in enumerate(keys):                     # backward, to the keyframe before or the start
        stop = keys[n - 1] if n > 0 else start - 1
        backward[k] = keyframes[k].astype(np.float32)
        backward.update(_carry_from(k, list(range(k - 1, stop, -1)), grays, backward[k], flow, anchor, tick,
                                    (objects or {}).get(k)))
    out = {}
    for t in range(start, end):
        before = max((k for k in keys if k <= t), default=None)
        after = min((k for k in keys if k >= t), default=None)
        if before is None:
            out[t] = backward[t]
        elif after is None or before == after:
            out[t] = forward[t]
        else:
            wa = (t - before) / (after - before)      # forward came from `before`, backward from `after`
            out[t] = forward[t] * (1 - wa) + backward[t] * wa
    return out


# --------------------------------------------------------- steady mattes --

def steady(matte: np.ndarray, before: np.ndarray | None, hold: float = 0.6, tolerance: float = 0.2) -> np.ndarray:
    """A frame's matte held steady against the last one's, already lined up
    with this frame (line_up()): where the two nearly agree it leans `hold`
    of the way to the last, so edges stop flickering; where they don't -
    something really moved, or the model changed its mind for good - this
    frame's is kept as it is."""
    if before is None:
        return matte
    agree = np.clip(1.0 - np.abs(matte - before) / tolerance, 0.0, 1.0) * hold
    return (matte * (1 - agree) + before * agree).astype(np.float32)


def line_up(previous: np.ndarray, g_prev: np.ndarray, g_now: np.ndarray, flow: "Flow") -> np.ndarray:
    """The last frame's matte (full size) moved to where its subject is now:
    by the subject's movement (followed on the small frames), or along the
    dense flow when it has nothing to follow. Outside the frame: nothing (0)."""
    h, w = previous.shape[:2]
    sh, sw = g_prev.shape[:2]
    small = cv2.resize(previous.astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA)
    m = movement(g_prev, g_now, small)
    if m is not None:
        return np.clip(move(previous.astype(np.float32), scaled_movement(m, sw / w)), 0, 1)
    return np.clip(warp(previous.astype(np.float32), flow.full(g_now, g_prev, h, w)), 0, 1)
