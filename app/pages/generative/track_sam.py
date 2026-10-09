#!/usr/bin/env python3
"""
SAM 2.1 as a video tracker (Meta, Apache 2.0): an object shown on one frame
followed through the frames after (or before) it, with SAM 2's memory - the
frame it was shown on, the six frames just before, and sixteen "object
pointers" - so it keeps to the object, and finds it again after something's
passed in front of it. Track's "Object" choice (follow()).

The model is square-zero-labs' ONNX export of transformers' Sam2VideoModel
(engine_setup.MODELS["sam2-tiny"]), five graphs and constants.json in one
folder. Their README gives the loop; it's SAM 2's own (sam2_base.py
_prepare_memory_conditioned_features), with the memory bank a fixed size:

  vision_encoder    a frame (1024 x 1024, ImageNet-normalized RGB) -> its
                    features at three scales, and their position embedding
  mask_decoder      features + points -> the mask (logits at 256 and 1024),
                    how sure it is the object's there, its object pointer
  memory_encoder    a frame's features + its mask -> 4096 memory tokens
  memory_attention  a frame's features conditioned on the memory bank:
                    7 blocks of 4096 (the prompted frame, temporal position
                    6; then frames 1 .. 6 back, position offset - 1; fewer:
                    the most recent repeated) + 16 pointers as 4 x 64 tokens
                    each (their temporal position from pointer_tpos)
  pointer_tpos      frames between a pointer's frame and this one, over 15,
                    -> its 64-d position

    sam = Sam(folder)
    f = sam.features(square(frame))         # once a frame, for every object on it
    obj, mask = sam.start(f, points, labels)        # points in the square's pixels (0 .. 1024)
    for each next frame:  mask, there = sam.step(sam.features(...), obj)

Masks come back as 0..1 at 1024 x 1024 - the square the frame was squeezed
into (square()); resize to the frame's shape to unsqueeze. Labels 1 (on the
object), 0 (not on it), 2 and 3 (a box's top-left and bottom-right).

Measured on the user's FX3 clip (4K, a plaque, push-in): 0.9 s to start,
1.5 s a frame after, on the CPU - found and kept the plaque all 49 frames.
"""

from __future__ import annotations

import collections
import concurrent.futures
import json
import os
import time

import numpy as np

import cv2

import track

SIZE = 1024
BLOCKS = 7              # memory blocks: the prompted frame + 6 recent
RECENT = BLOCKS - 1
POINTERS = 16


def square(frame: np.ndarray) -> np.ndarray:
    """A frame as SAM 2 takes it: 8-bit RGB squeezed to 1024 x 1024."""
    f = frame
    if f.dtype == np.uint16:
        f = (f >> 8).astype(np.uint8)
    return cv2.resize(f[:, :, 2::-1], (SIZE, SIZE), interpolation=cv2.INTER_AREA)


class Sam:
    def __init__(self, folder: str, threads: int = 0, preparing=None):
        """On the graphics card when there's one (gpu.py) - preparing() is
        said before it's compiled for it, the first time."""
        import gpu
        with open(os.path.join(folder, "constants.json"), encoding="utf-8") as fh:
            c = json.load(fh)
        self.mean = np.array(c["image_mean"], np.float32).reshape(1, 3, 1, 1)
        self.std = np.array(c["image_std"], np.float32).reshape(1, 3, 1, 1)
        self.tpos = np.array(c["memory_temporal_positional_encoding"], np.float32).reshape(BLOCKS, -1)
        s = {n: gpu.session(os.path.join(folder, n + ".onnx"), threads, preparing)
             for n in ("vision_encoder", "mask_decoder", "memory_encoder", "memory_attention", "pointer_tpos")}
        self.vision, self.decoder, self.encoder = s["vision_encoder"], s["mask_decoder"], s["memory_encoder"]
        self.attention, self.pointer_tpos = s["memory_attention"], s["pointer_tpos"]

    def features(self, sq: np.ndarray) -> dict:
        """A square() frame's features."""
        x = (sq.astype(np.float32).transpose(2, 0, 1)[None] / 255.0 - self.mean) / self.std
        names = [o.name for o in self.vision.get_outputs()]
        return dict(zip(names, self.vision.run(None, {"pixel_values": np.ascontiguousarray(x, np.float32)})))

    def _decode(self, f, cond_feats, points, labels):
        names = [o.name for o in self.decoder.get_outputs()]
        return dict(zip(names, self.decoder.run(None, {
            "feats0": f["feats0"], "feats1": f["feats1"], "feats2_cond": cond_feats,
            "input_points": np.asarray(points, np.float32).reshape(1, 1, -1, 2),
            "input_labels": np.asarray(labels, np.int32).reshape(1, 1, -1)})))

    def _remember(self, f, d, binarize):
        names = [o.name for o in self.encoder.get_outputs()]
        got = dict(zip(names, self.encoder.run(None, {
            "feats2": f["feats2"], "high_res_mask": d["high_res_mask"][:, :1].astype(np.float32),
            "object_score_logits": d["object_score_logits"].reshape(1, 1).astype(np.float32),
            "binarize": np.array(1.0 if binarize else 0.0, np.float32)})))
        return {"tokens": got["memory_tokens"], "pos": got["memory_pos"],
                "pointer": d["object_pointer"].reshape(-1)[:256].astype(np.float32)}

    @staticmethod
    def _mask(d):
        logits = d["high_res_mask"][0, 0]
        return (1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))).astype(np.float32)

    def start(self, f: dict, points, labels) -> tuple[dict, np.ndarray]:
        """An object shown on this frame: (its state for step(), its mask 0..1)."""
        d = self._decode(f, f["feats2_no_mem"], points, labels)
        obj = {"cond": self._remember(f, d, True), "recent": [], "n": 0,
               "score": float(d["object_score_logits"].reshape(-1)[0])}
        return obj, self._mask(d)

    def step(self, f: dict, obj: dict) -> tuple[np.ndarray, bool]:
        """The object on the next frame (forwards or backwards - whichever way
        it's going): (its mask 0..1, whether it's there at all)."""
        obj["n"] += 1
        cond, recent = obj["cond"], obj["recent"]
        # The memory: the prompted frame, then the frames 1 .. 6 back.
        blocks = [(cond["tokens"], cond["pos"] + self.tpos[BLOCKS - 1])]
        for back, m in enumerate(reversed(recent[-RECENT:]), start=1):
            blocks.append((m["tokens"], m["pos"] + self.tpos[back - 1]))
        while len(blocks) < BLOCKS:
            blocks.append(blocks[-1])                         # the most recent, again
        # The pointers: the prompted frame's, then up to 15 frames back.
        ptrs = [(obj["n"], cond["pointer"])]
        for back, m in enumerate(reversed(recent[-(POINTERS - 1):]), start=1):
            ptrs.append((back, m["pointer"]))
        while len(ptrs) < POINTERS:
            ptrs.append(ptrs[-1])
        diffs = np.array([p[0] for p in ptrs], np.float32) / (POINTERS - 1)
        ppos = self.pointer_tpos.run(None, {"normalized_diffs": diffs})[0]               # [16, 64]
        ptok = np.stack([p[1] for p in ptrs]).reshape(POINTERS * 4, 1, -1)              # each: 4 tokens of 64
        ppos = np.repeat(ppos, 4, axis=0).reshape(POINTERS * 4, 1, -1)
        memory = np.concatenate([b[0] for b in blocks] + [ptok], axis=0).astype(np.float32)
        memory_pos = np.concatenate([b[1] for b in blocks] + [ppos], axis=0).astype(np.float32)
        flat = lambda a: np.ascontiguousarray(a.reshape(1, a.shape[1], -1).transpose(2, 0, 1))   # noqa: E731
        cond_feats = self.attention.run(None, {
            "current_vision_features": flat(f["feats2"]),
            "current_vision_position_embeddings": flat(f["vision_pos_embed"]),
            "memory": memory, "memory_pos": memory_pos})[0]
        d = self._decode(f, cond_feats, np.zeros((1, 2), np.float32), np.array([-1], np.int32))
        recent.append(self._remember(f, d, False))
        del recent[:-(POINTERS - 1)]
        obj["score"] = float(d["object_score_logits"].reshape(-1)[0])
        return self._mask(d), obj["score"] > 0


# ------------------------------------------------- following paint with it --

COVERS = 0.8            # this much of the object inside the paint: the paint covers it (a person) - else it's on it (a sign)
MIN_SHARE = 0.25        # the object must hold at least this much of the paint, or it isn't what was painted


def prompts(mask: np.ndarray) -> tuple[np.ndarray, list]:
    """SAM's prompt for what's painted (a small mask): the box round it, and
    a point deep inside each part of it - in the square's pixels."""
    sh, sw = mask.shape[:2]
    inside = (mask > 0.5).astype(np.uint8)
    ys, xs = np.nonzero(inside)
    k = np.array([SIZE / sw, SIZE / sh], np.float32)
    pts = [[xs.min(), ys.min()], [xs.max(), ys.max()]]
    labels = [2, 3]
    n, parts = cv2.connectedComponents(inside)
    for i in range(1, n):
        part = (parts == i).astype(np.uint8)
        if part.sum() < 9:
            continue
        d = cv2.distanceTransform(part, cv2.DIST_L2, 5)
        y, x = np.unravel_index(int(np.argmax(d)), d.shape)
        pts.append([x, y])
        labels.append(1)
    return np.asarray(pts, np.float32) * k, labels


def objects(sam: Sam, squares, keyframes: dict, start: int, end: int, size: tuple, progress=None) -> dict:
    """SAM 2's object for each keyframe's paint, followed away from it - as
    track.carry_masks' `objects`: {k: {"masks": {t: small 0..1}, "covers":
    bool, "margin": px, "ramp": px}} - leaving out a keyframe whose paint
    isn't on (or over) one object, which is then followed as before.
    squares(t) -> square() of frame t; size: the small masks' (h, w)."""
    sh, sw = size
    keys = sorted(k for k in keyframes if start <= k < end)
    small = lambda m: cv2.resize(m, (sw, sh), interpolation=cv2.INTER_AREA)  # noqa: E731
    out = {}
    total = 2 * (end - start) + len(keys)
    done = [0]

    def tick():
        done[0] += 1
        if progress:
            progress(done[0], total)

    for n, k in enumerate(keys):
        paint = keyframes[k]
        if not (paint > 0.5).any():
            tick()
            continue
        pts, labels = prompts(paint)
        obj, m = sam.start(sam.features(squares(k)), pts, labels)
        tick()
        o = small(m)
        found, painted = o > 0.5, paint > 0.5
        both = float((found & painted).sum())
        if obj["score"] <= 0 or not found.any() or both < MIN_SHARE * painted.sum():
            continue                                   # not an object that was painted: as before
        covers = both >= COVERS * found.sum()
        margin, ramp = _margin(paint, found) if covers else (0.0, 1.0)
        masks = {k: o}
        later = keys[n + 1] if n + 1 < len(keys) else end
        earlier = keys[n - 1] if n > 0 else start - 1
        for frames in (range(k + 1, later), range(k - 1, earlier, -1)):
            state = obj if frames.step > 0 else sam.start(sam.features(squares(k)), pts, labels)[0]
            for t in frames:
                m, there = sam.step(sam.features(squares(t)), state)
                masks[t] = small(m) if there else np.zeros((sh, sw), np.float32)
                tick()
        out[k] = {"masks": masks, "covers": bool(covers), "margin": margin, "ramp": ramp}
    return out


def _margin(paint: np.ndarray, found: np.ndarray) -> tuple[float, float]:
    """How far the paint reaches past the object (px, at the masks' size -
    the median round its edge), and how soft its edge is (px)."""
    outside = cv2.distanceTransform((~found).astype(np.uint8), cv2.DIST_L2, 5)
    edge = cv2.Canny((paint > 0.5).astype(np.uint8) * 255, 50, 150) > 0
    margin = float(np.median(outside[edge])) if edge.any() else 0.0
    soft = float(((paint > 0.02) & (paint < 0.98)).sum()) / max(1.0, float(edge.sum()))
    return margin, max(1.0, soft)


# ------------------------------------------------------------ one Track run --

def _square_jpeg(frame):
    ok, buf = cv2.imencode(".jpg", square(frame)[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf


def follow(frames, keyframes: dict, start: int, end: int, size: tuple, sam: Sam | None = None,
           progress=None, flow=None) -> tuple[dict, dict]:
    """Track, start to finish: `frames` ((number, frame) in order, start ..
    end - 1) read once; keyframes {number: full-size 0..1 mask}. With `sam`,
    SAM 2's object under each keyframe's paint is followed too (objects()),
    from a small JPEG of each frame kept for it (tracking back needs the
    frames again). Returns ({number: small mask}, {keyframe: "covers" | "on"
    | "none"} - how SAM 2 took each, empty without it).
    progress(detail, 0..1)."""
    h, w = size
    n = max(1, end - start)
    grays, squares = {}, {}
    say = progress or (lambda *_: None)
    # Reading is most of a Standard track and the start of an Object one: its own share of the bar,
    # said a frame count at a time (seen: 50 s of "Reading the frames" with the bar barely moving).
    read = 0.2 if sam is not None else 0.5
    # SAM's square of each frame (a resize and a JPEG, 20 ms) made on other cores while the next
    # frame decodes - one after the other, a vertical 1080p clip read at 36 fps, not 145.
    pool = concurrent.futures.ThreadPoolExecutor(max(2, min(6, (os.cpu_count() or 4) // 2))) if sam else None
    pending = collections.deque()
    shown = 0.0
    try:
        for i, f in frames:
            grays[i] = track.gray_small(f)
            if pool is not None:
                pending.append((i, pool.submit(_square_jpeg, f)))
                while len(pending) > 24:                      # frames held for it: a few dozen at most
                    j, job = pending.popleft()
                    squares[j] = job.result()
            done = i - start + 1
            if time.monotonic() - shown > 0.2 or done == n:
                shown = time.monotonic()
                say(f"Reading the frames – {done} of {n}", read * done / n)
        while pending:
            j, job = pending.popleft()
            squares[j] = job.result()
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
    if not grays:
        raise ValueError("There are no frames in that part of the clip.")
    end = max(grays) + 1
    sh, sw, _ = track.small_size(h, w)
    small = {k: cv2.resize(m, (sw, sh), interpolation=cv2.INTER_AREA) for k, m in keyframes.items()
             if start <= k < end}
    found, how = None, {}
    share = read
    if sam is not None:
        share = 0.8
        sq = lambda t: cv2.imdecode(squares[t], cv2.IMREAD_COLOR)[:, :, ::-1]  # noqa: E731
        found = objects(sam, sq, small, start, end, (sh, sw),
                        progress=lambda d, t: say("Following the object", read + (0.8 - read) * d / max(t, 1)))
        how = {k: ("covers" if found[k]["covers"] else "on") if k in found else "none" for k in small}
    carried = track.carry_masks(grays, small, start, end, flow or track.Flow(), objects=found,
                                progress=lambda d, t: say("Frame to frame", share + (0.95 - share) * d / max(t, 1)))
    return carried, how
