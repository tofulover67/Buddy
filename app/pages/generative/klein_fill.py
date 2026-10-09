#!/usr/bin/env python3
"""
Removal by FLUX.2 [klein] 4B (Black Forest Labs, Apache 2.0) with fal's
object-removal LoRA (Apache 2.0) - the High quality add-on's picture
model. Runs in the engine's environment, once the add-on has put PyTorch
and diffusers there (engine_setup.install_hq); fill.Filler (LaMa) is what
runs without it.

LaMa continues what's round a hole; it can't draw what isn't there, so a
big hole comes back a smear. klein redraws the scene: a street, a doorway,
a pole's base behind a person (measured on the user's FX3 footage,
2026-10-07: where LaMa left a grey smudge, klein put back the crossing,
the building and the kerb).

How it's asked: the LoRA was trained (fal, 100 pairs) on pictures with a
red box round the object and the prompt "Remove the highlighted object
from the scene", in klein's edit mode - the picture as a reference, the
whole picture drawn again. So each painted spot gets a box, drawn on a cut
round it scaled to about a megapixel; the cut comes back redrawn, and only
the painted part (and a seam's width past it) is put into the picture, as
fill.py puts LaMa's in. Masked inpainting (the latents outside the hole
kept) was tried as well: it drew a boom mic back in where one was painted
out. The 4-step distilled model beat the 50-step base (which redrew the
boom too) and is 12x faster.

The prompt's embedding is computed once (Qwen3-4B, 8 GB, not shipped) and
kept in data/klein_remove_prompt.safetensors: all 512 positions, as the
pipeline passes them - trimmed to the 19 real tokens the fill came out
different and worse.

The graphics card: the transformer (7.8 GB) stays in memory and is moved
onto the card a few blocks at a time as it runs (diffusers' group
offloading, on a stream of its own): 2.8 GB of graphics memory, as fast as
the whole model on the card (5.3 s a megapixel on an RTX 5080), so it fits
beside Resolve.
"""

from __future__ import annotations

import os

import numpy as np

import cv2

import fill

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "data", "klein_remove_prompt.safetensors")
LORA_SCALE = 1.1            # fal's own example
STEPS = 4                   # the distilled model's
SIDE = 1024                 # the model's picture: its longer side
CONTEXT = 2.0               # a cut's side, as a multiple of its spot's longer side
MIN_SIDE = 512              # ... and never less than this (picture pixels)
FAINT, FULL = fill.FAINT, fill.FULL


def merge_lora(transformer, path, scale):
    """The LoRA's weights added into the transformer's for good (W += s·BA):
    no PEFT, nothing extra at run time."""
    import torch
    from diffusers.loaders.lora_pipeline import Flux2LoraLoaderMixin
    sd = Flux2LoraLoaderMixin.lora_state_dict(path)
    sd = sd[0] if isinstance(sd, tuple) else sd
    modules = dict(transformer.named_modules())
    done = 0
    for key, a in sd.items():
        if not key.endswith(".lora_A.weight"):
            continue
        name = key[len("transformer."):-len(".lora_A.weight")]
        b = sd.get(key.replace(".lora_A.", ".lora_B."))
        mod = modules.get(name)
        if b is None or mod is None:
            raise RuntimeError(f"The removal LoRA doesn't fit this model ({name}).")
        alpha = sd.get(key.replace(".lora_A.weight", ".alpha"))
        s = scale * (float(alpha) / a.shape[0] if alpha is not None else 1.0)
        with torch.no_grad():
            w = mod.weight
            w.add_((b.float() @ a.float()).mul_(s).to(w.dtype))
        done += 1
    if not done:
        raise RuntimeError("The removal LoRA is empty.")
    return done


class Klein:
    """The loaded model; run() as often as wanted."""

    def __init__(self, folder: str):
        import torch
        from diffusers import AutoencoderKLFlux2, FlowMatchEulerDiscreteScheduler, Flux2Transformer2DModel
        from safetensors.torch import load_file
        self.torch = torch
        self.device = torch.device("cuda")
        tr = Flux2Transformer2DModel.from_pretrained(os.path.join(folder, "transformer"), torch_dtype=torch.bfloat16)
        merge_lora(tr, os.path.join(folder, "lora", "flux-2-klein-4b-object-remove-lora.safetensors"), LORA_SCALE)
        tr.eval()
        tr.enable_group_offload(self.device, torch.device("cpu"), offload_type="block_level",
                                num_blocks_per_group=2, use_stream=True)
        self.transformer = tr
        self.vae = AutoencoderKLFlux2.from_pretrained(os.path.join(folder, "vae"),
                                                      torch_dtype=torch.bfloat16).to(self.device).eval()
        self.scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(os.path.join(folder, "scheduler"))
        self.prompt = load_file(PROMPT)["remove"][None].to(self.device, torch.bfloat16)
        self.vae_scale = 2 ** (len(self.vae.config.block_out_channels) - 1)

    # The pipeline's own steps (diffusers' Flux2KleinPipeline, Apache 2.0) -
    # here so that transformers (its text encoder's library) isn't needed.

    @staticmethod
    def _patchify(x):
        b, c, h, w = x.shape
        return x.view(b, c, h // 2, 2, w // 2, 2).permute(0, 1, 3, 5, 2, 4).reshape(b, c * 4, h // 2, w // 2)

    @staticmethod
    def _unpatchify(x):
        b, c, h, w = x.shape
        return x.reshape(b, c // 4, 2, 2, h, w).permute(0, 1, 4, 2, 5, 3).reshape(b, c // 4, h * 2, w * 2)

    @staticmethod
    def _pack(x):
        b, c, h, w = x.shape
        return x.reshape(b, c, h * w).permute(0, 2, 1)

    def _ids(self, h, w, t):
        torch = self.torch
        return torch.cartesian_prod(torch.tensor([t]), torch.arange(h), torch.arange(w), torch.arange(1))[None]

    @staticmethod
    def _mu(seq_len, steps):
        a1, b1 = 8.73809524e-05, 1.89833333
        a2, b2 = 0.00016927, 0.45666666
        if seq_len > 4300:
            return float(a2 * seq_len + b2)
        m200, m10 = a2 * seq_len + b2, a1 * seq_len + b1
        a = (m200 - m10) / 190.0
        return float(a * steps + m200 - 200.0 * a)

    def run(self, rgb: np.ndarray, seed: int = 0, step=None) -> np.ndarray:
        """rgb: H x W x 3 float 0..1 (H, W multiples of 16), the red box
        drawn on it -> the picture drawn again with what's boxed removed,
        float 0..1."""
        torch = self.torch
        dev = self.device
        h, w = rgb.shape[:2]
        with torch.no_grad():
            image = torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1)))[None].to(dev, torch.bfloat16) * 2 - 1
            bn_mean = self.vae.bn.running_mean.view(1, -1, 1, 1).to(dev, torch.bfloat16)
            bn_std = torch.sqrt(self.vae.bn.running_var.view(1, -1, 1, 1) + self.vae.config.batch_norm_eps).to(dev, torch.bfloat16)
            ref = self._patchify(self.vae.encode(image).latent_dist.mode())
            ref = (ref - bn_mean) / bn_std
            lh, lw = ref.shape[2], ref.shape[3]
            ref_ids = self._ids(lh, lw, 10).to(dev)
            ref = self._pack(ref)
            g = torch.Generator(dev).manual_seed(seed)
            ch = self.transformer.config.in_channels // 4
            noise = torch.randn((1, ch * 4, 2 * (h // (self.vae_scale * 2)) // 2, 2 * (w // (self.vae_scale * 2)) // 2),
                                generator=g, device=dev, dtype=torch.bfloat16)
            lat_ids = self._ids(noise.shape[2], noise.shape[3], 0).to(dev)
            latents = self._pack(noise)
            txt_ids = torch.cartesian_prod(torch.arange(1), torch.arange(1), torch.arange(1),
                                           torch.arange(self.prompt.shape[1]))[None].to(dev)
            sigmas = np.linspace(1.0, 1 / STEPS, STEPS)
            if getattr(self.scheduler.config, "use_flow_sigmas", False):
                sigmas = None
            self.scheduler.set_timesteps(sigmas=sigmas, device=dev, mu=self._mu(latents.shape[1], STEPS))
            if hasattr(self.scheduler, "set_begin_index"):
                self.scheduler.set_begin_index(0)
            timesteps = self.scheduler.timesteps
            for i, t in enumerate(timesteps):
                ts = t.expand(1).to(latents.dtype)
                pred = self.transformer(hidden_states=torch.cat([latents, ref], 1).to(torch.bfloat16),
                                        timestep=ts / 1000, guidance=None, encoder_hidden_states=self.prompt,
                                        txt_ids=txt_ids, img_ids=torch.cat([lat_ids, ref_ids], 1),
                                        return_dict=False)[0][:, :latents.shape[1]]
                latents = self.scheduler.step(pred, t, latents, return_dict=False)[0]
                if step:
                    step(i + 1, len(timesteps))
            out = latents.reshape(1, noise.shape[2], noise.shape[3], -1).permute(0, 3, 1, 2)
            out = self._unpatchify(out * bn_std + bn_mean)
            img = self.vae.decode(out, return_dict=False)[0]
            img = ((img.float() + 1) / 2).clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
        return img


def model_size(cw: int, ch: int) -> tuple[int, int]:
    """A cut's size for the model: its longer side 1024 (scaled up or
    down), its own shape, multiples of 16."""
    s = SIDE / max(cw, ch)
    return max(256, int(round(cw * s / 16)) * 16), max(256, int(round(ch * s / 16)) * 16)


def _square(mx, my, side, w, h):
    cw, ch = min(side, w), min(side, h)
    x = int(np.clip(mx - cw / 2, 0, w - cw))
    y = int(np.clip(my - ch / 2, 0, h - ch))
    return [x, y, x + cw, y + ch]


def _cuts(hole: np.ndarray) -> list[tuple[tuple[int, int, int, int], list]]:
    """The cuts that fill `hole`: [((x, y, w, h), [the spots' boxes in it])],
    biggest first. Each spot: a square twice its longer side round it (kept
    inside the picture). The shape matters - a tall cut with a person a
    quarter of its width came back with the person drawn again (and a boom
    mic with its pole); a square with the spot half its side, removed.
    Cuts that would overlap become one (one model run, every spot boxed)."""
    h, w = hole.shape
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(hole.astype(np.uint8), connectivity=8)
    cuts = []
    for i in range(1, count):
        x, y, bw, bh = (int(v) for v in stats[i, :4])
        side = max(MIN_SIDE, int(max(bw, bh) * CONTEXT))
        cuts.append(_square(x + bw / 2, y + bh / 2, side, w, h) + [[(x, y, bw, bh)]])
    merged = True
    while merged:
        merged = False
        for i in range(len(cuts)):
            for j in range(i + 1, len(cuts)):
                a, b = cuts[i], cuts[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    cuts[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), a[4] + b[4]]
                    del cuts[j]
                    merged = True
                    break
            if merged:
                break
    out = [((c[0], c[1], c[2] - c[0], c[3] - c[1]), c[4]) for c in cuts]
    return sorted(out, key=lambda c: -(c[0][2] * c[0][3]))


def _srgb(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def _linear(x):
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4))


def match_light(filled: np.ndarray, patch: np.ndarray, region: np.ndarray) -> np.ndarray:
    """The model draws the whole cut again, a little lighter or darker,
    warmer or cooler than it was (flat log footage most). Its fill is
    brought to the picture: the difference between the two in a band round
    the hole, spread smoothly over the hole. Worked out on a copy at most
    LIGHT_SIDE across and scaled back: the correction is smooth, and at a
    4K cut's size its blurs took seconds a frame (rose_fill puts one in
    every frame)."""
    if not region.any():
        return filled
    h, w = region.shape
    s = LIGHT_SIDE / max(h, w)
    if s < 1:
        size = (max(1, round(w * s)), max(1, round(h * s)))
        small = _light_correction(cv2.resize(filled, size, interpolation=cv2.INTER_AREA),
                                  cv2.resize(np.ascontiguousarray(patch, dtype=np.float32), size, interpolation=cv2.INTER_AREA),
                                  cv2.resize(region.astype(np.float32), size, interpolation=cv2.INTER_AREA) > 0.5)
        if small is None:
            return filled
        corr = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        corr = _light_correction(filled, patch, region)
        if corr is None:
            return filled
    return filled + np.where(region[:, :, None], corr, 0)


LIGHT_SIDE = 512


def _light_correction(filled, patch, region):
    """match_light's correction, everywhere (h x w x 3), or None when there's
    no ring round the hole to tell it from. The difference is read in a thin
    ring just outside the hole and carried in across it as a smooth membrane
    (_membrane), so the fill meets the picture at the hole's edge whatever
    the light does there. A wide band averaged instead (to 2026-10-08) missed
    a glow: a light tube painted out of the user's log footage, its bloom
    8/255 over the wall right at the paint's edge and near nothing a band's
    width out - the fill came out at the wall's level, a ring round it."""
    area = float(region.sum())
    if area < 1:
        return None
    width = int(np.clip(np.sqrt(area) * 0.04, 2, 6))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * width + 1, 2 * width + 1))
    ring = (cv2.dilate(region.astype(np.uint8), k) > 0) & ~region
    if ring.sum() < 30:
        return None
    # Steadied along the ring: a pixel's misregistration across an edge there is noise, not light.
    r = ring.astype(np.float32)
    diff = ((patch - filled) * r[:, :, None]).astype(np.float32)
    steady = cv2.GaussianBlur(diff, (0, 0), 1.5) / np.maximum(cv2.GaussianBlur(r, (0, 0), 1.5), 1e-4)[:, :, None]
    return np.clip(_membrane(steady, ring), -0.15, 0.15).astype(np.float32)


def _membrane(values: np.ndarray, known: np.ndarray) -> np.ndarray:
    """values (h x w x c) where `known`, filled in smoothly everywhere else:
    each level of an image pyramid fills what the finer one doesn't know
    (push-pull) - the known values exactly where they are, and between them a
    smooth blend of all of them round about."""
    k = known.astype(np.float32)
    v = values.astype(np.float32) * k[:, :, None]
    levels = [(v, k)]
    while min(k.shape[:2]) > 4:
        v, k = cv2.pyrDown(v), cv2.pyrDown(k)
        if v.ndim == 2:
            v = v[:, :, None]
        levels.append((v, k))
    v, k = levels[-1]
    mean = v.sum(axis=(0, 1)) / max(float(k.sum()), 1e-6)
    est = np.where(k[:, :, None] > 1e-6, v / np.maximum(k, 1e-6)[:, :, None], mean).astype(np.float32)
    for v, k in reversed(levels[:-1]):
        up = cv2.pyrUp(est, dstsize=(k.shape[1], k.shape[0]))
        if up.ndim == 2:
            up = up[:, :, None]
        a = np.clip(k * 2, 0, 1)[:, :, None]
        est = (v / np.maximum(k, 1e-6)[:, :, None] * a + up * (1 - a)).astype(np.float32)
    return est


EFFECT_OVER = 4.0           # a change round the hole counts at this many times the cut's typical difference
EFFECT_MIN = 0.03           # ... and never under this (0..1)
EFFECT_SMOOTH = 2.5         # (both smoothed this much, at the model's size, before they're compared)
EFFECT_REACH = 1.5          # ... within this many times the hole's size of it
EFFECT_WEAK = 0.5           # what's taken fades out down to this share of a change that counts


def effect_mask(drawn: np.ndarray, given: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Where a model that draws the whole cut again changed the scene round
    the hole, at its size (0..1): the removed thing's shadow, its
    reflection, the light it threw - what footage's "Also its shadow and
    reflection" puts in too (rose_fill.effect_masks). Not used for pictures:
    klein's LoRA wasn't trained to take effects out, and one frame on its own
    (no neighbours to steady it) left blocky patches in a boat's reflection. It draws everything a little differently (its
    VAE, its grain, fine texture - leaves, ripples - drawn anew), so the two
    are compared smoothed (EFFECT_SMOOTH), and a change counts only well
    above the typical difference away from the hole (EFFECT_OVER) and only
    next to it (within EFFECT_REACH of its size): something changed across
    the picture is the model's doing, not the removed thing's.

    How much is taken follows how big the change is, fading out to nothing
    at EFFECT_WEAK of what counts - a glow taken in full where it's strong
    and eased off where it thins, not cut round in crescents. And a change
    that runs into the cut's own edge is left: the thing's shadow doesn't end
    in a straight line, but what's taken would (seen 2026-10-08: ROSE drew a
    person beside the hole 7/255 lighter, it was taken, and a line ran down
    their face where the cut ended)."""
    area = float(hole.sum())
    if area < 4:
        return np.zeros(hole.shape, np.float32)
    diff = np.abs(cv2.GaussianBlur(drawn, (0, 0), EFFECT_SMOOTH)
                  - cv2.GaussianBlur(given, (0, 0), EFFECT_SMOOTH)).mean(axis=2).astype(np.float32)
    reach = int(max(6, np.sqrt(area) * EFFECT_REACH))
    # Within `reach` of the hole: from the distance to it, not a dilate by a disc that wide - a person's
    # hole at ROSE's size made that disc ~750 px across, 2 s a frame on one core, a run's 49 frames
    # minutes with nothing said after its last step (2026-10-08). The same pixels to the odd one at the edge.
    near = cv2.distanceTransform((~hole).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE) <= reach
    far = ~near
    floor = float(np.median(diff[far])) if far.sum() > 100 else float(np.median(diff[~hole]))
    counts = max(EFFECT_MIN, floor * EFFECT_OVER)
    weak = counts * EFFECT_WEAK
    cand = (diff > weak) & ~hole
    count, labels, stats, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), connectivity=8)
    # The parts of 6 px or more where the change counts near the hole, not at the cut's edge - all at
    # once, not a pass over the picture for each.
    wanted = stats[:, cv2.CC_STAT_AREA] >= 6
    wanted[0] = False
    touching = np.zeros(count, bool)
    touching[np.unique(labels[cand & (diff > counts) & near])] = True
    border = np.zeros(hole.shape, bool)
    border[:2, :] = border[-2:, :] = border[:, :2] = border[:, -2:] = True
    at_edge = np.zeros(count, bool)
    at_edge[np.unique(labels[cand & border])] = True
    at_edge[0] = False
    keep = (wanted & touching & ~at_edge)[labels] & near
    strength = np.clip((diff - weak) / max(counts - weak, 1e-6), 0, 1)
    return np.where(keep, strength, 0).astype(np.float32)


REDRAWN = 0.05              # klein's hole this close to what was there (0..1 on average): it drew the thing back


def lama_prefill(filler):
    """A prefill for KleinFiller from fill.Filler (LaMa): (rgb 0..1, hole) -> rgb with the hole filled."""
    def run(rgb, hole):
        out, _ = filler.fill(np.ascontiguousarray(rgb[:, :, ::-1]), hole.astype(np.float32))
        return np.ascontiguousarray(out[:, :, ::-1])
    return run


class KleinFiller:
    """fill.Filler's fill(), by klein: (picture, mask) -> (filled, box)."""

    def __init__(self, folder: str, prefill=None):
        """prefill: (rgb, hole) -> rgb with the hole filled some other way
        (lama_prefill) - used to ask again when klein draws the thing back."""
        self.model = Klein(folder)
        self.prefill = prefill

    def fill(self, img: np.ndarray, mask: np.ndarray, progress=None) -> tuple[np.ndarray, tuple | None]:
        """As fill.Filler.fill: img H x W (x 1, 3 or 4), BGR, any depth;
        mask 0..1 (feathered) or bool. Returns (the filled picture, the box
        that changed (x, y, w, h)) - (img, None) for an empty mask."""
        weight = np.clip(mask.astype(np.float32), 0.0, 1.0)
        if not (weight > FAINT).any():
            return img, None
        h, w = weight.shape
        grow = fill.grow_px(img.shape)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
        hole = cv2.dilate((weight > FAINT).astype(np.uint8), kernel) > 0
        full = cv2.dilate((weight >= FULL).astype(np.uint8), kernel) > 0
        dtype = img.dtype
        linear = dtype == np.float32
        gray = img.ndim == 2 or img.shape[2] == 1
        fl, scale = fill._as_float(img)
        if gray:
            fl = fl.reshape(h, w)
            color = np.repeat(fl[:, :, None], 3, axis=2)
        else:
            color = fl[:, :, :3].copy()
        cuts = _cuts(hole)
        box = [w, h, 0, 0]
        for n, ((cx, cy, cw, ch), spots) in enumerate(cuts):
            patch = color[cy:cy + ch, cx:cx + cw]
            region = hole[cy:cy + ch, cx:cx + cw]
            mw, mh = model_size(cw, ch)
            view = _srgb(patch) if linear else patch
            small = cv2.resize(np.clip(view, 0, 1).astype(np.float32), (mw, mh),
                               interpolation=cv2.INTER_AREA if mw < cw else cv2.INTER_CUBIC)
            plain = np.ascontiguousarray(small[:, :, ::-1])
            small_hole = cv2.resize(region.astype(np.float32), (mw, mh), interpolation=cv2.INTER_AREA) > 0.01
            sx, sy = mw / cw, mh / ch
            pad = max(6, mw // 128)
            line = max(3, mw // 220)

            def ask(rgb, n=n):
                for x, y, bw, bh in spots:
                    x0, y0 = int((x - cx) * sx) - pad, int((y - cy) * sy) - pad
                    x1, y1 = int((x - cx + bw) * sx) + pad, int((y - cy + bh) * sy) + pad
                    cv2.rectangle(rgb, (max(0, x0), max(0, y0)), (min(mw - 1, x1), min(mh - 1, y1)), (1.0, 0.0, 0.0), line)

                def step(i, total):
                    if progress:
                        progress(n * total + i - 1, len(cuts) * total)
                return self.model.run(rgb, step=step)

            out = ask(plain.copy())
            # The LoRA (trained on 100 pairs) sometimes draws the thing back - a person or an animal that fills
            # its box (a tennis player, a swan, 2026-10-07): the hole hardly changes then (4-9/255 on average,
            # against 30-45 for a removal). Asked again with the hole filled by LaMa first, it never sees the
            # thing; it took the tennis player out that way.
            if self.prefill is not None and float(np.abs(out - plain)[small_hole].mean()) < REDRAWN:
                out = ask(np.ascontiguousarray(self.prefill(plain.copy(), small_hole)))
            out = out[:, :, ::-1]
            big = cv2.resize(out, (cw, ch), interpolation=cv2.INTER_CUBIC if mw < cw else cv2.INTER_AREA)
            if linear:
                big = _linear(np.clip(big, 0, 1))
            big = match_light(big.astype(np.float32), patch, region)
            if cw > mw * 1.4:
                big = fill._regrain(big, patch, region, grow, cw / mw)
            # Put in as fill.Filler does: all the way where the mask is, a seam's width past it, the
            # mask's own strength where it's feathered.
            solid = region & full[cy:cy + ch, cx:cx + cw]
            seam = cv2.GaussianBlur(solid.astype(np.float32), (0, 0), max(1.0, grow / 2.5))
            alpha = np.maximum(np.maximum(seam, solid), region * weight[cy:cy + ch, cx:cx + cw])
            alpha = np.where(region | (seam > 0.002), alpha, 0.0)[:, :, None].astype(np.float32)
            color[cy:cy + ch, cx:cx + cw] = patch * (1 - alpha) + big * alpha
            ys, xs = np.nonzero(alpha[:, :, 0] > 0.002)
            if len(xs):
                box = [min(box[0], cx + xs.min()), min(box[1], cy + ys.min()),
                       max(box[2], cx + xs.max() + 1), max(box[3], cy + ys.max() + 1)]
        x0, y0, x1, y1 = box
        if x1 <= x0:
            return img, None
        out = fl.copy()
        if gray:
            out = color.mean(axis=2).reshape(img.shape)
        else:
            out[:, :, :3] = color
        box = (int(x0), int(y0), int(x1 - x0), int(y1 - y0))
        result = img.copy()
        bx, by, bw, bh = box
        result[by:by + bh, bx:bx + bw] = fill._back(out[by:by + bh, bx:bx + bw], scale, dtype)
        return result, box
