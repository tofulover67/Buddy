#!/usr/bin/env python3
"""
Removal through footage by ROSE ("Remove Objects with Side Effects",
Kunbyte, Apache 2.0) - the High quality add-on's footage model. Runs in the
engine's environment once the add-on has put PyTorch and diffusers there
(engine_setup.install_hq); video_fill.py (neighbours, a kept invention,
LaMa) is what runs without it.

ROSE is a video model: Wan 2.1 Fun 1.3B InP (Alibaba PAI, Apache 2.0)
trained to take something out of a whole clip at once, and with it what it
does to the scene round it - its shadow, its reflection, the light it
throws. It sees the clip with the thing still in it and a mask of where it
is, and draws the clip again without it, every frame together. Ranked
first by people on VOR-Bench (2026-09) of ten removal models; on the
user's FX3 footage (2026-10-07) - a person against a pole on a panning
shot - the fill held still through the pan, where video_fill's kept
invention (one homography) broke against the parallax.

What it's given, as its own pipeline gives it (checked against ROSE's code
on the same inputs: the transformer agrees to 0.1%, fp16's rounding):
  - the clip, not masked - so it can find the side effects;
  - the mask, 1 where to remove;
  - the empty prompt's embedding (umT5, 11 GB, not shipped:
    data/rose_empty_prompt.safetensors - one token);
  - image features of zero (ROSE runs without a reference image, so its
    4.7 GB CLIP isn't needed either).
No guidance: with an empty prompt both halves of it are the same.

It draws at about 480 x 720 (AREA), so each run is a cut round where the
masks are in that run's frames, scaled to that and back; what comes back
is put in only where the mask is (and a seam past it), with grain matched
to the picture round it, as fill.py does. A big cut on 4K comes back soft,
so (2026-10-07, measured on the user's 4K pan with a made-up thing moving
across, the truth known):
  - detail: the frames round each one (DETAIL_OFFSETS away) show some of
    what's behind; their fine texture goes over ROSE's frame where the two
    agree (add_detail). Sharpness 0.48 -> 0.54 of the truth's, no worse
    anywhere - limited by how much of the hole any frame shows (a wide,
    slow thing: about 40%);
  - fine: ROSE at about 720 x 1280 (FINE_AREA) for a cut bigger than AREA:
    0.48 -> 0.60 (0.64 with detail) - a fence's bars come back - in about
    5x the time. Its VAE works in tiles (0.3/255 apart from whole frames),
    which took 720p's peak from 11.7 GB to 2.3 GB, so it fits beside Resolve.
And with effects, what ROSE changed next to the hole goes in too - the
thing's shadow, its reflection (effect_masks; on ROSE's own example clips:
the swan's reflection, the boat's, a tennis player's shadow).

A run is WINDOW frames. A longer part goes in runs that overlap by
OVERLAP: each later run is given the frames the one before finished as
they are (masked nowhere), so it carries on from them, and the overlap
fades from the old run's frames to the new's.

20 steps: the same picture as 30 (0.2 / 255 apart on average), in two
thirds of the time. The transformer (3 GB) is moved onto the card a few
blocks at a time (group offloading): 4 GB of graphics memory at most, 49
frames in about 50 s on an RTX 5080.
"""

from __future__ import annotations

import os

import numpy as np

import cv2

import fill
import footage
import klein_fill

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "data", "rose_empty_prompt.safetensors")
STEPS = 20
WINDOW = 49                 # frames a run (16n + 1, as ROSE was trained)
OVERLAP = 8                 # frames a run shares with the one before
AREA = 480 * 720            # the model's frame: about this many pixels
FINE_AREA = 720 * 1280      # ... with "fine", for a cut bigger than AREA
ROOM = 0.25                 # a cut's room round the masks, each side, as a share of their extent
MIN_SIDE = 256              # a cut's shorter side, at least (picture pixels)
FAINT, FULL = fill.FAINT, fill.FULL
DETAIL_FROM = 1.3           # ROSE's frame scaled up more than this: texture is borrowed from the frames round it
DETAIL_OFFSETS = (1, 2, 4, 8, 12)   # ... from the frames these many either side
DETAIL_SIGMA = 0.6          # what ROSE's size loses: detail finer than this many of its pixels (times the scale)
DETAIL_AGREE = 0.12         # borrowed and ROSE's apart by this much (0..1) once smoothed: not trusted (0.05 trusted 2%: ROSE itself is ~0.04 off the truth)

# The transformer as diffusers builds it (Wan 2.1 Fun 1.3B InP's shape).
TRANSFORMER = {"patch_size": (1, 2, 2), "num_attention_heads": 12, "attention_head_dim": 128, "in_channels": 36,
               "out_channels": 16, "text_dim": 4096, "freq_dim": 256, "ffn_dim": 8960, "num_layers": 30,
               "cross_attn_norm": True, "qk_norm": "rms_norm_across_heads", "eps": 1e-6, "image_dim": 1280,
               "added_kv_proj_dim": 1536, "rope_max_seq_len": 1024}


class Rose:
    """The loaded model; run() as often as wanted."""

    def __init__(self, folder: str):
        import torch
        from diffusers import AutoencoderKLWan, FlowMatchEulerDiscreteScheduler, WanTransformer3DModel
        from diffusers.loaders.single_file_utils import convert_wan_transformer_to_diffusers, convert_wan_vae_to_diffusers
        from safetensors.torch import load_file
        self.torch = torch
        self.device = torch.device("cuda")
        sd = convert_wan_transformer_to_diffusers(load_file(os.path.join(folder, "rose_transformer.safetensors")))
        with torch.device("meta"):
            tr = WanTransformer3DModel(**TRANSFORMER)
        tr.load_state_dict({k: v.to(torch.float16) for k, v in sd.items()}, strict=True, assign=True)
        # Built without weights (meta) to load in seconds: the rotary embedding's tables aren't weights - made again.
        from diffusers.models.transformers.transformer_wan import WanRotaryPosEmbed
        tr.rope = WanRotaryPosEmbed(TRANSFORMER["attention_head_dim"], TRANSFORMER["patch_size"],
                                    TRANSFORMER["rope_max_seq_len"])
        tr.eval()
        tr.enable_group_offload(self.device, torch.device("cpu"), offload_type="block_level",
                                num_blocks_per_group=2, use_stream=True)
        self.transformer = tr
        vsd = convert_wan_vae_to_diffusers(torch.load(os.path.join(folder, "Wan2.1_VAE.pth"), map_location="cpu",
                                                      weights_only=True))
        with torch.device("meta"):
            vae = AutoencoderKLWan()
        vae.load_state_dict({k: v.to(torch.float16) for k, v in vsd.items()}, strict=True, assign=True)
        self.vae = vae.to(self.device).eval()
        self.vae.enable_tiling()            # 720p's peak 11.7 GB -> 2.3 GB, 0.3/255 apart
        self.mean = torch.tensor(vae.config.latents_mean, device=self.device).view(1, -1, 1, 1, 1)
        self.std = torch.tensor(vae.config.latents_std, device=self.device).view(1, -1, 1, 1, 1)
        self.scheduler = FlowMatchEulerDiscreteScheduler(num_train_timesteps=1000, shift=5.0, use_dynamic_shifting=False)
        e = load_file(PROMPT)["empty"].to(self.device, torch.float16)              # (tokens, 4096)
        self.context = torch.cat([e, e.new_zeros(512 - e.shape[0], e.shape[1])])[None]
        self.clip = torch.zeros(1, 257, 1280, device=self.device, dtype=torch.float16)

    def run(self, frames: np.ndarray, masks: np.ndarray, seed: int = 0, step=None) -> np.ndarray:
        """frames: F x H x W x 3 RGB float 0..1 (F = 4n + 1; H, W multiples
        of 16); masks: F x H x W, True where to remove -> the frames drawn
        again with it gone, F x H x W x 3 float 0..1."""
        torch = self.torch
        dev = self.device
        n, h, w = masks.shape
        with torch.no_grad():
            video = torch.from_numpy(np.ascontiguousarray(frames.transpose(3, 0, 1, 2)))[None].to(dev, torch.float16) * 2 - 1
            z = self.vae.encode(video).latent_dist.mode()
            seen = ((z.float() - self.mean) / self.std).to(torch.float16)
            # The mask as ROSE's pipeline makes it: the first frame four times over, then four frames to a latent
            # frame, 1 where to keep, scaled to the latents' size (the first latent frame on its own).
            m = torch.from_numpy(masks.astype(np.float32))[None, None].to(dev)
            m = torch.cat([m[:, :, :1].repeat(1, 1, 4, 1, 1), m[:, :, 1:]], dim=2)
            m = m.view(1, m.shape[2] // 4, 4, h, w).transpose(1, 2)
            keep = 1 - m
            lf, lh, lw = seen.shape[2:]
            first = torch.nn.functional.interpolate(keep[:, :, :1], size=(1, lh, lw), mode="trilinear", align_corners=False)
            parts = [first]
            if lf > 1:
                parts.append(torch.nn.functional.interpolate(keep[:, :, 1:], size=(lf - 1, lh, lw), mode="trilinear",
                                                             align_corners=False))
            y = torch.cat([torch.cat(parts, dim=2).to(torch.float16), seen], dim=1)
            g = torch.Generator(dev).manual_seed(seed)
            latents = torch.randn((1, 16, lf, lh, lw), generator=g, device=dev, dtype=torch.float16)
            self.scheduler.set_timesteps(STEPS, device=dev)
            timesteps = self.scheduler.timesteps
            for i, t in enumerate(timesteps):
                with torch.autocast("cuda", dtype=torch.float16):
                    pred = self.transformer(hidden_states=torch.cat([latents, y], 1), timestep=t.expand(1),
                                            encoder_hidden_states=self.context, encoder_hidden_states_image=self.clip,
                                            return_dict=False)[0]
                latents = self.scheduler.step(pred.float(), t, latents.float(), return_dict=False)[0].to(torch.float16)
                if step:
                    step(i + 1, len(timesteps))
            out = self.vae.decode((latents.float() * self.std + self.mean).to(torch.float16), return_dict=False)[0]
            out = ((out.float() + 1) / 2).clamp(0, 1)[0].permute(1, 2, 3, 0).cpu().numpy()
        return out


def model_size(cw: int, ch: int, area: int = AREA) -> tuple[int, int]:
    s = (area / (cw * ch)) ** 0.5
    return max(64, int(round(cw * s / 16)) * 16), max(64, int(round(ch * s / 16)) * 16)


def cut_for(masks: list, h: int, w: int) -> tuple[int, int, int, int] | None:
    """One cut for a run: round every frame's mask, with room, kept inside
    the picture - None when nothing's masked."""
    any_ = None
    for m in masks:
        if m is not None:
            hit = m > FAINT
            any_ = hit if any_ is None else (any_ | hit)
    if any_ is None or not any_.any():
        return None
    ys, xs = np.nonzero(any_)
    return _cut_round(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, h, w)


def _cut_round(x0: int, y0: int, x1: int, y1: int, h: int, w: int) -> tuple[int, int, int, int]:
    """cut_for's cut round a box (x0, y0, x1, y1): with room, inside the picture, not too thin."""
    room = int(max(x1 - x0, y1 - y0) * ROOM) + 16
    x0, y0, x1, y1 = max(0, x0 - room), max(0, y0 - room), min(w, x1 + room), min(h, y1 + room)
    # Not thinner than MIN_SIDE.
    for lo, hi, size in ((0, 2, w), (1, 3, h)):
        b = [x0, y0, x1, y1]
        if b[hi] - b[lo] < min(MIN_SIDE, size):
            mid = (b[lo] + b[hi]) / 2
            b[lo] = int(np.clip(mid - MIN_SIDE / 2, 0, size - min(MIN_SIDE, size)))
            b[hi] = b[lo] + min(MIN_SIDE, size)
        x0, y0, x1, y1 = b
    return x0, y0, x1 - x0, y1 - y0


def windows(start: int, end: int) -> list[tuple[int, int]]:
    """The runs covering start .. end - 1: [(first, end)], each overlapping
    the one before by OVERLAP."""
    out = []
    a = start
    while True:
        b = min(end, a + WINDOW)
        out.append((a, b))
        if b >= end:
            return out
        a = b - OVERLAP


RUNS = (WINDOW, 33, 17)     # a run's frames, longest first (16n + 1, as ROSE was trained)
SPREAD = 1.6                # a run's cut this many times the biggest of its frames' own: shorter


def plan_runs(start: int, end: int, boxes: dict, h: int, w: int, area: int = AREA) -> list[tuple[int, int]]:
    """The runs covering start .. end - 1, as windows() - but shorter where
    the thing moves a lot. A run is one cut round all of its frames' masks,
    squeezed to the model's size: something crossing the picture in 49
    frames makes that cut huge and ROSE's frames soft, when each frame's own
    is small. So a run is 49 frames unless its cut is bigger than the model
    draws (`area`) and SPREAD times the biggest of its frames' own cuts -
    then 33, then 17. boxes: frame -> (x0, y0, x1, y1) of its mask, or None."""
    def cut_area(lo, hi):
        got = [boxes.get(i) for i in range(lo, hi) if boxes.get(i)]
        if not got:
            return 0, 0
        x0, y0 = min(b[0] for b in got), min(b[1] for b in got)
        x1, y1 = max(b[2] for b in got), max(b[3] for b in got)
        _, _, cw, ch = _cut_round(x0, y0, x1, y1, h, w)
        own = max(c[2] * c[3] for c in (_cut_round(*b, h, w) for b in got))
        return cw * ch, own

    out = []
    a = start
    while True:
        for n in RUNS:
            b = min(end, a + n)
            whole, own = cut_area(a, b)
            if n == RUNS[-1] or whole <= area or whole <= SPREAD * own:
                break
        out.append((a, b))
        if b >= end:
            return out
        a = b - OVERLAP


def mask_boxes(mask_of, start: int, end: int) -> tuple[dict, tuple[int, int] | None]:
    """frame -> (x0, y0, x1, y1) round its mask (None: nothing masked), for
    plan_runs - and the masks' (h, w), None with none at all."""
    out, shape = {}, None
    for i in range(start, end):
        m = mask_of(i)
        if m is None:
            out[i] = None
            continue
        shape = m.shape[:2]
        ys, xs = np.nonzero(m > FAINT)
        out[i] = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else None
    return out, shape


def padded(n: int) -> int:
    """Frames a run of n is made up to: 16k + 1 (the last frame repeated)."""
    return max(17, ((n - 1 + 15) // 16) * 16 + 1)


class Remover:
    """A part of a clip with ROSE, a run at a time.

        Remover(model).remove(frames_of, mask_of, start, end, write, done_frame, progress, first)

    frames_of(a, b) -> (number, frame) for a .. b - 1 in order (BGR, any
    depth) - read twice a run, so a run's 4K frames are never all held at
    once; mask_of(i) -> full-size 0..1 or None; write(i, the finished frame,
    float BGR 0..1); done_frame(i) -> a frame written before (a stopped job
    carried on), float BGR, or None. first: the first frame not written yet -
    the run holding it starts over, the frames before it in that run given to
    the model as they were written."""

    def __init__(self, model: Rose, effects: bool = False, detail: bool = True, fine: bool = False):
        """effects: also put in what ROSE changed round the hole - the
        removed thing's shadow, its reflection (effect_masks). detail: where
        ROSE's frame is scaled up, the real texture the frames round it show
        is put back over it, where they agree with ROSE (add_detail). fine:
        a cut bigger than AREA is drawn at FINE_AREA - sharper, ~5x slower."""
        self.model = model
        self.effects = effects
        self.detail = detail
        self.fine = fine
        self.flow = None
        self.runs = []                   # the runs remove() makes: [(first, end)], set as it starts

    def remove(self, frames_of, mask_of, start, end, write, done_frame=None, progress=None, first=None):
        first = start if first is None else first
        # Shorter runs where the thing moves a lot (plan_runs) - planned from every frame's mask, the same
        # each time for the same masks, so a job carried on after a stop picks up the run it was in.
        boxes, shape = mask_boxes(mask_of, start, end)
        runs = windows(start, end) if shape is None else \
            plan_runs(start, end, boxes, *shape, area=FINE_AREA if self.fine else AREA)
        self.runs = runs
        held = {}                        # frame -> finished (float16, full size): the overlap, not written yet
        for k, (a, b) in enumerate(runs):
            if b <= first:
                continue
            lead = {i for i in range(a, min(b, first)) if i not in held}     # written before: the model's lead-in
            self._run(k, len(runs), a, b, frames_of, mask_of, lead, held, write, done_frame, end, progress)

    def _run(self, k, total, a, b, frames_of, mask_of, lead, held, write, done_frame, end, progress):
        """One run, a .. b - 1. The frames already finished (held: the run
        before's overlap; lead: written before) are given to the model as they
        are, masked nowhere, so it carries on from them."""
        last = b >= end
        fixed = set(held) | lead

        def finish(i, done):
            if i in held:
                # The overlap: from the run before's frame to this run's, across it.
                pos = (i - a + 1) / (OVERLAP + 1)
                done = held.pop(i).astype(np.float32) * (1 - pos) + done * pos
            if last or i < b - OVERLAP:
                write(i, done)
            else:
                held[i] = done.astype(np.float16)

        # 1. Where to cut: round every frame's mask (not the finished frames').
        h = w = 0
        boxes = []
        for i in range(a, b):
            m = None if i in fixed else mask_of(i)
            if m is not None:
                h, w = m.shape
                boxes.append(m > FAINT)
        cut = cut_for(boxes, h, w) if boxes else None
        del boxes
        if cut is None:                  # nothing to remove in this run: the frames as they are
            for i, f in frames_of(a, b):
                if i not in lead:
                    finish(i, footage.to_float(f)[:, :, :3])
            return
        # 2. The model's frames: the cut, scaled to its size.
        cx, cy, cw, ch = cut
        mw, mh = model_size(cw, ch, FINE_AREA if self.fine and cw * ch > AREA else AREA)
        n = b - a
        grow = fill.grow_px((h, w))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1,) * 2)
        frames = np.zeros((padded(n), mh, mw, 3), np.float32)
        hole = np.zeros((padded(n), mh, mw), bool)
        shrink = cv2.INTER_AREA if mw < cw else cv2.INTER_CUBIC
        for i, f in frames_of(a, b):
            j = i - a
            given = held[i] if i in held else (done_frame(i) if i in lead and done_frame else None)
            given = footage.to_float(f)[:, :, :3] if given is None else given
            frames[j] = cv2.resize(np.ascontiguousarray(given[cy:cy + ch, cx:cx + cw, :3], dtype=np.float32),
                                   (mw, mh), interpolation=shrink)[:, :, ::-1]
            if i not in fixed:
                m = mask_of(i)
                if m is not None:
                    mm = cv2.dilate((m[cy:cy + ch, cx:cx + cw] > FAINT).astype(np.uint8), kernel)
                    hole[j] = cv2.resize(mm.astype(np.float32), (mw, mh), interpolation=cv2.INTER_AREA) > 0.01
        frames[n:] = frames[n - 1]
        hole[n:] = hole[n - 1]
        say = (lambda s, t: progress(k, total, s, t)) if progress else None
        out = self.model.run(np.clip(frames, 0, 1), hole, seed=k, step=say)[:n]
        effect = effect_masks(out, frames[:n], hole[:n]) if self.effects else None
        self.last_effect = effect          # what was taken for its effects (for a look, in tests)
        del frames, hole
        # 3. Put in, frame by frame - with the frames round each at hand when its texture is borrowed from them.
        sharpen = self.detail and cw > mw * DETAIL_FROM
        reach = max(DETAIL_OFFSETS) if sharpen else 0
        near = {}                        # frame -> (as read, small grey, small hidden-by-the-thing) while in reach
        if sharpen:
            import track
            if self.flow is None:
                self.flow = track.Flow()

        def keep(i, f):
            if not sharpen:
                return
            m = mask_of(i)
            small = track.gray_small(f)
            hidden = None
            if m is not None:
                sh, sw = small.shape
                hidden = cv2.resize(cv2.dilate((m > FAINT).astype(np.uint8), kernel), (sw, sh),
                                    interpolation=cv2.INTER_AREA) > 0
            near[i] = (f, small, hidden)

        def put(i):
            if i in lead:
                return
            f = near[i][0] if sharpen else held_frames.pop(i)
            src = footage.to_float(f)[:, :, :3]
            m = mask_of(i)
            if m is None:
                return finish(i, src)
            got = self._borrow(near, i, m) if sharpen else None
            fx = None if effect is None else effect[i - a]
            finish(i, self._put(src, out[i - a][:, :, ::-1], m, cut, (mw, mh), i, got, fx))

        held_frames = {}
        pending = []
        for i, f in frames_of(a, b):
            if sharpen:
                keep(i, f)
            else:
                held_frames[i] = f
            pending.append(i)
            while pending and pending[0] + reach <= i:
                put(pending.pop(0))
                lo = (pending[0] if pending else i + 1) - reach
                for old in [x for x in near if x < lo]:
                    del near[old]
        while pending:
            put(pending.pop(0))

    def _borrow(self, near, i, m):
        """What the frames round frame i show where its hole is: each one
        DETAIL_OFFSETS away, moved into place along the flow (worked out at
        the small size, filled in across the hole from round it - the hole's
        own movement is the removed thing's), where it isn't hidden by the
        thing there too; the nearer weighing more. Far ones see behind a big,
        slow thing that near ones never do. Returns ((x0, y0, x1, y1), their
        average there, where any showed it) - or None."""
        import track
        f, gray, _ = near[i]
        h, w = m.shape
        grow = fill.grow_px((h, w))
        hole = cv2.dilate((m > FAINT).astype(np.uint8),
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))) > 0
        ys, xs = np.nonzero(hole)
        if not len(xs):
            return None
        x0, y0, x1, y1 = max(0, xs.min() - 2), max(0, ys.min() - 2), min(w, xs.max() + 3), min(h, ys.max() + 3)
        sh, sw = gray.shape
        hole_small = cv2.resize(hole.astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA) > 0.01
        sx, sy = w / sw, h / sh
        gx, gy = np.meshgrid(np.arange(x0, x1, dtype=np.float32), np.arange(y0, y1, dtype=np.float32))
        smx, smy = (gx + 0.5) / sx - 0.5, (gy + 0.5) / sy - 0.5          # each box pixel at the small size
        want = hole[y0:y1, x0:x1]
        scale = 255.0 if f.dtype == np.uint8 else 65535.0 if f.dtype == np.uint16 else 1.0
        total = np.zeros(want.shape + (3,), np.float32)
        weight = np.zeros(want.shape, np.float32)
        for d in DETAIL_OFFSETS:
            for s_ in (i - d, i + d):
                if s_ not in near:
                    continue
                fs, gs, hid = near[s_]
                fl = track.fill_flow(self.flow.small(gray, gs), hole_small)
                fx = cv2.remap(fl[:, :, 0], smx, smy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE) * sx
                fy = cv2.remap(fl[:, :, 1], smx, smy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE) * sy
                mx, my = gx + fx, gy + fy
                inside = (mx >= 0) & (mx <= w - 1) & (my >= 0) & (my <= h - 1)
                ok = want & inside
                if hid is not None:
                    ok &= cv2.remap(hid.astype(np.uint8), (mx + 0.5) / sx - 0.5, (my + 0.5) / sy - 0.5,
                                    cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=1) == 0
                if not ok.any():
                    continue
                seen = cv2.remap(np.ascontiguousarray(fs[:, :, :3]), mx, my, cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE).astype(np.float32) / scale
                wd = np.float32(1.0 / d)
                total[ok] += seen[ok] * wd
                weight[ok] += wd
        found = weight > 0
        if not found.any():
            return None
        avg = np.zeros_like(total)
        avg[found] = total[found] / weight[found][:, None]
        return (x0, y0, x1, y1), avg, found

    @staticmethod
    def _put(frame, drawn, mask, cut, size, seed, borrowed=None, effect=None):
        """ROSE's frame put into the picture: its cut scaled back up, the
        light matched round the hole, the real texture the frames round it
        show added where they agree with it (borrowed - add_detail), grain
        put back where they don't, only where the mask is (and a seam's
        width past it) - and, with effect (ROSE's changes round the hole, at
        its size, 0..1), there too."""
        cx, cy, cw, ch = cut
        mw, mh = size
        h, w = frame.shape[:2]
        grow = fill.grow_px((h, w))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
        weight = np.clip(mask.astype(np.float32), 0, 1)
        hole = cv2.dilate((weight > FAINT).astype(np.uint8), k) > 0
        full = cv2.dilate((weight >= FULL).astype(np.uint8), k) > 0
        patch = frame[cy:cy + ch, cx:cx + cw]
        region = hole[cy:cy + ch, cx:cx + cw]
        extra = None
        if effect is not None and effect.any():
            extra = np.clip(cv2.resize(effect.astype(np.float32), (cw, ch), interpolation=cv2.INTER_LINEAR), 0, 1)
            # Carried on into the hole's soft edge: ROSE drew all of its hole, but a feathered mask lets the
            # picture through at its edge - and with what's taken round it, a ring of the old light stood
            # between the two (a light tube's bloom, 2026-10-08: 145/255 between 140 inside and out).
            inward = cv2.dilate(extra, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (4 * grow + 3,) * 2))
            extra = np.where(region, inward, extra).astype(np.float32)
            if not (extra > 0.02).any():
                extra = None
        changed = region if extra is None else (region | (extra > 0.02))
        solid = region & full[cy:cy + ch, cx:cx + cw]
        seam = cv2.GaussianBlur(solid.astype(np.float32), (0, 0), max(1.0, grow / 2.5))
        big = cv2.resize(drawn.astype(np.float32), (cw, ch), interpolation=cv2.INTER_CUBIC if mw < cw else cv2.INTER_AREA)
        # Lit to match wherever any of it shows, its seam's fade past the hole too (as surface_fill.put).
        big = klein_fill.match_light(big, patch, changed | (seam > 0.002))
        factor = cw / mw
        texture = trust = None
        if borrowed is not None:
            (x0, y0, x1, y1), avg, seen = borrowed
            nb = np.zeros_like(big)
            sn = np.zeros(big.shape[:2], bool)
            nb[y0 - cy:y1 - cy, x0 - cx:x1 - cx] = avg
            sn[y0 - cy:y1 - cy, x0 - cx:x1 - cx] = seen
            texture, trust = add_detail(big, nb, sn & changed, factor)
        if factor > 1.4:
            grain = fill._regrain(big, patch, changed, grow, factor, seed=seed) - big
            big = big + (grain if trust is None else grain * (1 - trust)[:, :, None])
        if texture is not None:
            big = big + texture
        alpha = np.maximum(np.maximum(seam, solid), region * weight[cy:cy + ch, cx:cx + cw])
        alpha = np.where(region | (seam > 0.002), alpha, 0.0)
        if extra is not None:
            alpha = np.maximum(alpha, extra)
        alpha = alpha[:, :, None].astype(np.float32)
        out = frame.copy()
        out[cy:cy + ch, cx:cx + cw] = patch * (1 - alpha) + big * alpha
        return out


def add_detail(drawn: np.ndarray, borrowed: np.ndarray, seen: np.ndarray, factor: float):
    """The texture ROSE's scaled-up frame lacks, from what the frames round
    it show (borrowed, where seen): their fine detail - what's lost at ROSE's
    size - added over ROSE's frame, where the two agree once that detail is
    set aside. Where they don't (the flow's wrong there, something moved
    through), ROSE's frame as it is. Returns (the texture to add, how much
    it was trusted 0..1); ROSE's tones and light are kept either way, so
    there's no seam where the borrowing starts or stops."""
    sigma = max(1.0, DETAIL_SIGMA * factor)
    m = seen.astype(np.float32)
    # In from where it stops being seen (its edge is unreliable) by about a blur's width.
    m = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=max(1, int(sigma)))
    if not m.any():
        return np.zeros_like(drawn), np.zeros(drawn.shape[:2], np.float32)
    g = cv2.GaussianBlur(m, (0, 0), sigma)
    low = cv2.GaussianBlur(borrowed * m[:, :, None], (0, 0), sigma) / np.maximum(g, 1e-4)[:, :, None]
    fine = np.where(m[:, :, None] > 0, borrowed - low, 0)
    differ = np.abs(low - cv2.GaussianBlur(drawn, (0, 0), sigma)).mean(axis=2)
    differ = cv2.GaussianBlur(differ, (0, 0), sigma * 2)
    trust = np.clip(1 - differ / DETAIL_AGREE, 0, 1) * np.clip(cv2.GaussianBlur(m, (0, 0), sigma), 0, 1)
    return (fine * trust[:, :, None]).astype(np.float32), trust.astype(np.float32)


def effect_masks(drawn: np.ndarray, given: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """klein_fill.effect_mask for each of a run's frames (at ROSE's size),
    held steady over three frames, 0..1. Measured on ROSE's own example
    clips: the swan's reflection, the boat's, a tennis player's shadow on
    the clay - each taken, and the hills and ripples round them left."""
    n = len(drawn)
    found = np.stack([klein_fill.effect_mask(drawn[j], given[j], hole[j]) for j in range(n)]).astype(np.float32)
    steady = np.zeros_like(found)
    for j in range(n):
        steady[j] = found[max(0, j - 1):j + 2].mean(axis=0)
        steady[j] = cv2.GaussianBlur(cv2.dilate(steady[j], np.ones((3, 3), np.uint8)), (0, 0), 1.5)
    return np.clip(steady * 1.5, 0, 1)
