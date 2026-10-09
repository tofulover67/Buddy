"""Generative's High quality add-on: its downloads (PyTorch from its own
index, two models in parts from more than one repo, every file pinned), what
it says on a computer that can't run it, FLUX.2 klein's cuts and the light
matched round them (klein_fill.py), ROSE's runs through a part - overlapping,
carried on with after a stop (rose_fill.py, with a stand-in for the model) -
the page's Quality switch, and, where the add-on is installed, both models
for real on pictures made here."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

import _paths  # noqa: F401
import numpy as np

try:
    import cv2
    _GEN = str(_paths.APP / "pages" / "generative")
    sys.path.append(_GEN)
    try:
        import fill
        import klein_fill
        import rose_fill
    finally:
        sys.path.remove(_GEN)
except ImportError:  # pragma: no cover - no OpenCV for this Python
    cv2 = None

from pages.generative import engine_setup as es

GEN = str(_paths.APP / "pages" / "generative")


def _hq_here():
    """The add-on and both models on this machine (without starting the engine's Python)."""
    site = es.VENV_DIR / "Lib" / "site-packages"
    return (site / "torch").is_dir() and (site / "diffusers").is_dir() and all(
        f["path"].exists() for m in es.HQ_MODELS for f in es.model_files(m))


HQ = _hq_here() and es.venv_python().exists()


class SetupTests(unittest.TestCase):
    def test_its_models_are_pinned_part_by_part_from_their_own_repos(self):
        self.assertEqual(es.HQ_MODELS, ["flux2-klein", "rose"])
        files = {m: es.model_files(m) for m in es.HQ_MODELS}
        root = es.model_path("flux2-klein")
        where = {str(f["path"].relative_to(root)).replace("\\", "/"): f["url"] for f in files["flux2-klein"]}
        # As diffusers wants them: a folder per part.
        self.assertIn("transformer/diffusion_pytorch_model.safetensors", where)
        self.assertIn("vae/config.json", where)
        self.assertIn("scheduler/scheduler_config.json", where)
        # The LoRA from fal's repo, the ComfyUI export (the other file there is cut short).
        lora = where["lora/flux-2-klein-4b-object-remove-lora.safetensors"]
        self.assertIn("/fal/flux-2-klein-4B-object-remove-lora/resolve/0e3f5879", lora)
        self.assertIn("comfy_converted", lora)
        self.assertTrue(where["transformer/config.json"].startswith(
            "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/resolve/e7b7dc27"))
        rose = {f["path"].name: f["url"] for f in files["rose"]}
        self.assertIn("/Kunbyte/ROSE/resolve/", rose["rose_transformer.safetensors"])
        self.assertIn("/alibaba-pai/Wan2.1-Fun-1.3B-InP/resolve/", rose["Wan2.1_VAE.pth"])
        for fs in files.values():
            for f in fs:
                self.assertEqual(len(f["sha256"]), 64)
                self.assertGreater(f["size"], 0)
        self.assertEqual(es.size_mb("flux2-klein"), 7995)
        self.assertEqual(es.size_mb("rose"), 3637)

    def test_the_lock_pins_every_wheel_and_leaves_the_engines_own_alone(self):
        text = es.HQ_LOCK.read_text(encoding="utf-8")
        self.assertIn("--extra-index-url https://download.pytorch.org/whl/cu130", text)
        reqs = [l for l in text.splitlines() if l and not l.startswith((" ", "#", "--"))]
        names = {r.split("==")[0] for r in reqs}
        self.assertTrue({"torch", "diffusers", "accelerate", "safetensors"} <= names)
        self.assertIn("torch==2.14.1+cu130 ; platform_machine == 'AMD64' and sys_platform == 'win32' \\", reqs)
        # Every requirement has its hashes on the lines after it.
        blocks = text.split("\n")
        for i, line in enumerate(blocks):
            if line and not line.startswith((" ", "#", "--")):
                self.assertTrue(blocks[i + 1].strip().startswith("--hash=sha256:"), line)
        out = es.hq_packages()
        self.assertIn("torch", out)
        self.assertNotIn("numpy", out)        # the engine's - removing the add-on keeps it
        self.assertNotIn("packaging", out)

    def test_it_says_why_a_computer_cant_have_it(self):
        def status(card, driver=600.0, installed=False):
            with mock.patch.object(es, "nvidia_card", return_value=card), \
                    mock.patch.object(es, "nvidia_driver", return_value=driver), \
                    mock.patch.object(es, "hq_installed", return_value=installed), \
                    mock.patch.object(es, "model_ready", return_value=False):
                return es.hq_status(True)
        self.assertIn("needs an NVIDIA", status(None)["why"])
        self.assertIn("older than the RTX 30", status({"name": "GTX 1080", "memory_mb": 8192, "compute": 6.1})["why"])
        self.assertIn("8 GB", status({"name": "RTX 3050", "memory_mb": 6144, "compute": 8.6})["why"])
        self.assertIn("580", status({"name": "RTX 4070", "memory_mb": 12282, "compute": 8.9}, driver=566)["why"])
        ok = status({"name": "RTX 5080", "memory_mb": 16303, "compute": 12.0}, installed=True)
        self.assertEqual((ok["supported"], ok["installed"], ok["why"]), (True, True, ""))
        self.assertFalse(status({"name": "RTX 5080", "memory_mb": 16303, "compute": 12.0}, driver=500)["installed"])

    def test_what_it_would_download_comes_from_the_status_it_has(self):
        self.assertEqual(es.hq_mb({"installed": False, "models": {}}), es.HQ_MB + 7995 + 3637)
        self.assertEqual(es.hq_mb({"installed": True, "models": {"flux2-klein": True}}), 3637)
        self.assertEqual(es.hq_mb({"installed": True, "models": {"flux2-klein": True, "rose": True}}), 0)


class FakeKlein:
    """klein's run(): the boxed part painted flat grey, and what it was given kept."""

    def __init__(self):
        self.given = []

    def run(self, rgb, seed=0, step=None):
        self.given.append(rgb.copy())
        red = (rgb[:, :, 0] > 0.9) & (rgb[:, :, 1] < 0.1) & (rgb[:, :, 2] < 0.1)
        ys, xs = np.nonzero(red)
        out = rgb.copy()
        out[ys.min():ys.max() + 1, xs.min():xs.max() + 1] = 0.5
        if step:
            step(4, 4)
        return out


@unittest.skipUnless(cv2, "OpenCV isn't installed for this Python")
class KleinTests(unittest.TestCase):
    def test_a_spot_is_cut_square_twice_its_size_and_near_spots_share_a_cut(self):
        hole = np.zeros((2160, 4096), bool)
        hole[1000:1300, 2000:2100] = True                    # 100 x 300
        ((x, y, w, h), spots), = klein_fill._cuts(hole)
        self.assertEqual((w, h), (600, 600))                 # a square twice its longer side
        self.assertEqual(spots, [(2000, 1000, 100, 300)])
        self.assertAlmostEqual(x + w / 2, 2050, delta=1)
        hole[1000:1050, 2300:2340] = True                    # near: one cut, both boxed
        cuts = klein_fill._cuts(hole)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(len(cuts[0][1]), 2)
        hole[:] = False
        hole[2100:2150, 4000:4090] = True                    # at the corner: kept inside the picture
        hole[100:120, 100:110] = True                        # far away: its own, smaller cut, after
        cuts = {spots[0]: c for c, spots in klein_fill._cuts(hole)}
        self.assertEqual(len(cuts), 2)
        x, y, w, h = cuts[(4000, 2100, 90, 50)]
        self.assertEqual((x + w, y + h, w), (4096, 2160, klein_fill.MIN_SIDE))
        self.assertEqual(cuts[(100, 100, 10, 20)][:2], (0, 0))

    def test_the_model_sees_about_a_megapixel_its_sides_multiples_of_16(self):
        self.assertEqual(klein_fill.model_size(600, 600), (1024, 1024))
        w, h = klein_fill.model_size(3000, 2160)
        self.assertEqual((w, w % 16, h % 16), (1024, 0, 0))
        self.assertAlmostEqual(h / w, 2160 / 3000, delta=0.02)

    def test_the_light_round_the_hole_is_matched(self):
        rng = np.random.default_rng(3)
        patch = cv2.GaussianBlur(rng.random((300, 300, 3), np.float32), (0, 0), 8) * 0.5 + 0.2
        region = np.zeros((300, 300), bool)
        region[100:200, 100:200] = True
        drawn = patch - np.float32([0.06, 0.02, -0.04])         # the model's: a little off everywhere
        fixed = klein_fill.match_light(drawn, patch, region)
        self.assertLess(np.abs(fixed - patch)[region].mean(), 0.006)
        self.assertTrue(np.array_equal(fixed[~region], drawn[~region]))   # outside the hole: not its job

    def test_margin_takes_the_mask_that_far_out_all_round_its_new_edge_soft(self):
        m = np.zeros((300, 300), np.float32)
        m[100:200, 100:200] = 1.0
        self.assertTrue(np.array_equal(fill.widen(m, 0), m))         # none: as it was
        got = fill.widen(m, 30)
        self.assertEqual((got[150, 71], got[150, 229]), (1.0, 1.0))  # 29 px out: all of it
        self.assertEqual(got[150, 55], 0.0)                         # past the margin and its fade: none
        self.assertTrue(0 < got[150, 65] < 1)                       # the fade
        self.assertEqual(float(fill.widen(m, 10_000)[0, 0]), 1.0)   # capped (MAX_MARGIN), not an error
        self.assertFalse(fill.widen(np.zeros((50, 50), np.float32), 20).any())

    def test_a_fast_mover_gets_shorter_runs_a_still_one_the_long_ones(self):
        h, w = 2160, 3840
        still = {i: (1800, 900, 2000, 1100) for i in range(100)}
        self.assertEqual(rose_fill.plan_runs(0, 100, still, h, w), rose_fill.windows(0, 100))
        moving = {i: (100 + 70 * i, 900, 300 + 70 * i, 1100) for i in range(60)}      # across the frame in 60
        runs = rose_fill.plan_runs(0, 60, moving, h, w)
        self.assertGreater(len(runs), len(rose_fill.windows(0, 60)))
        self.assertTrue(all(b - a in rose_fill.RUNS or b == 60 for a, b in runs))
        self.assertEqual((runs[0][0], runs[-1][1]), (0, 60))
        for (a0, b0), (a1, b1) in zip(runs, runs[1:]):
            self.assertEqual(b0 - a1, rose_fill.OVERLAP)                 # each overlapping the one before
        # Finer detail draws bigger before squeezing: a cut that fits FINE_AREA isn't cut short.
        slow = {i: (1000 + 8 * i, 900, 1300 + 8 * i, 1200) for i in range(49)}
        self.assertEqual(len(rose_fill.plan_runs(0, 49, slow, h, w, area=rose_fill.FINE_AREA)), 1)

    def test_a_glow_round_the_hole_is_met_at_its_edge_not_left_as_a_ring(self):
        # A light painted out of flat footage: its bloom is brightest right at the paint's edge and
        # gone a little way out. The model drew the whole cut without it (the wall's level).
        yy, xx = np.mgrid[0:400, 0:400].astype(np.float32)
        r = np.hypot(xx - 200, yy - 200)
        region = r < 60
        wall = np.full((400, 400, 3), 0.54, np.float32)
        patch = wall + (0.035 * np.exp(-np.clip(r - 60, 0, None) / 25))[:, :, None]   # the glow outside it
        fixed = klein_fill.match_light(wall.copy(), patch, region)
        edge_in = (r > 54) & (r < 58)
        edge_out = (r > 62) & (r < 66)
        step = abs(float(fixed[edge_in].mean() - patch[edge_out].mean()))
        self.assertLess(step, 0.004)                                   # was 0.008 averaged over a wide band: a ring
        self.assertTrue(np.array_equal(fixed[~region], wall[~region]))

    def test_a_change_running_into_the_cut_s_edge_is_not_taken_and_what_is_fades_with_its_strength(self):
        rng = np.random.default_rng(4)
        given = cv2.GaussianBlur(rng.random((240, 320, 3), np.float32), (0, 0), 6) * 0.4 + 0.3
        hole = np.zeros((240, 320), bool)
        hole[90:150, 140:180] = True
        drawn = given.copy()
        drawn[60:200, 0:120] += 0.06                     # someone beside it, drawn lighter - out to the cut's edge
        yy, xx = np.mgrid[0:240, 0:320].astype(np.float32)
        d = np.hypot(np.clip(np.abs(xx - 160) - 20, 0, None), np.clip(np.abs(yy - 120) - 30, 0, None))
        glow = (0.12 * np.exp(-d / 12)) * ~hole
        drawn -= glow[:, :, None]                         # its glow, taken out - strong at the hole, thinning out
        got = klein_fill.effect_mask(drawn, given, hole)
        self.assertFalse(got[60:200, 0:110].any())        # not the person
        self.assertGreater(got[(d > 0) & (d < 6)].mean(), 0.8)   # the glow, where it's strong
        thin = got[(d > 18) & (d < 24)]
        self.assertTrue(0 < thin.mean() < 0.8)            # ... easing off, not cut round

    def test_what_changed_next_to_the_hole_counts_redrawn_texture_and_far_changes_dont(self):
        rng = np.random.default_rng(2)
        given = cv2.GaussianBlur(rng.random((240, 320, 3), np.float32), (0, 0), 6) * 0.4 + 0.3
        hole = np.zeros((240, 320), bool)
        hole[80:140, 120:160] = True
        drawn = given + (rng.random(given.shape).astype(np.float32) - 0.5) * 0.12    # its grain and texture, anew
        shadow = np.zeros((240, 320), bool)
        shadow[140:165, 110:200] = True                                       # under it: its shadow, taken out
        drawn[shadow] += 0.15
        drawn[10:30, 10:30] -= 0.2                                            # a corner it changed by itself
        got = klein_fill.effect_mask(drawn, given, hole)
        self.assertGreater(got[shadow].mean(), 0.8)
        self.assertFalse(got[10:30, 10:30].any())
        self.assertFalse(got[hole].any())
        self.assertLess(got[~shadow].mean(), 0.02)
        self.assertFalse(klein_fill.effect_mask(drawn, given, np.zeros_like(hole)).any())

    def test_when_klein_draws_the_thing_back_it_is_asked_again_over_another_fill(self):
        class Stubborn(FakeKlein):
            def run(self, rgb, seed=0, step=None):
                if not self.given:                       # the first time: the picture as it was, the thing and all
                    self.given.append(rgb.copy())
                    return rgb.copy()
                return super().run(rgb, seed, step)

        filled = []

        def prefill(rgb, hole):
            filled.append(hole.copy())
            out = rgb.copy()
            out[hole] = 0.5
            return out

        f = klein_fill.KleinFiller.__new__(klein_fill.KleinFiller)
        f.model, f.prefill = Stubborn(), prefill
        img = np.full((900, 1600, 3), 50000, np.uint16)            # light: the stand-in's grey is a change
        mask = np.zeros((900, 1600), np.float32)
        mask[400:500, 700:760] = 1
        out, _ = f.fill(img, mask)
        self.assertEqual((len(f.model.given), len(filled)), (2, 1))
        self.assertTrue(np.allclose(f.model.given[1][filled[0]], 0.5) or (f.model.given[1][filled[0]] == 0.5).mean() > 0.9)
        f.model, filled[:] = FakeKlein(), []
        f.fill(img, mask)                                        # removed the first time: not asked again
        self.assertEqual((len(f.model.given), len(filled)), (1, 0))

    def test_a_removal_boxes_the_spot_in_red_and_changes_only_what_was_painted(self):
        f = klein_fill.KleinFiller.__new__(klein_fill.KleinFiller)
        f.model, f.prefill = FakeKlein(), None
        img = np.full((900, 1600, 3), 30000, np.uint16)
        img[:, :, 1] = 20000
        mask = np.zeros((900, 1600), np.float32)
        mask[400:500, 700:760] = 1
        out, box = f.fill(img, mask)
        self.assertEqual(out.dtype, np.uint16)
        x, y, w, h = box
        self.assertTrue(x <= 700 and y <= 400 and x + w >= 760 and y + h >= 500)
        self.assertLess(w, 120)                                  # the spot and a seam past it, not the cut
        self.assertTrue(np.array_equal(out[:350], img[:350]))
        self.assertGreater(np.abs(out[450, 730].astype(int) - img[450, 730].astype(int)).max(), 1000)
        given = f.model.given[0]
        self.assertEqual(given.shape[:2], (1024, 1024))         # a 600 x 600 cut, scaled up to the model's size
        red = (given[:, :, 0] > 0.9) & (given[:, :, 1] < 0.1)
        self.assertGreater(red.sum(), 1000)                      # the box, drawn on what the model sees
        self.assertIsNone(f.fill(img, np.zeros_like(mask))[1])     # nothing painted: nothing to do


class FakeRose:
    """ROSE's run(): masked pixels painted a flat colour per run, what it was given kept."""

    def __init__(self):
        self.calls = []

    def run(self, frames, masks, seed=0, step=None):
        self.calls.append((frames.copy(), masks.copy(), seed))
        out = frames.copy()
        out[masks] = [0.2, 0.9, 0.2]
        if step:
            step(20, 20)
        return out


def _clip(n, h=120, w=160):
    rng = np.random.default_rng(1)
    base = cv2.GaussianBlur(rng.random((h, w + n, 3), np.float32), (0, 0), 3)
    frames = {i: (np.clip(base[:, i:i + w], 0, 1) * 255).astype(np.uint8) for i in range(n)}
    masks = {}
    for i in range(n):
        m = np.zeros((h, w), np.float32)
        m[40:70, 30 + i:60 + i] = 1
        masks[i] = m
    return frames, masks


@unittest.skipUnless(cv2, "OpenCV isn't installed for this Python")
class RoseTests(unittest.TestCase):
    def test_a_part_goes_in_overlapping_runs_made_up_to_16n_plus_1(self):
        self.assertEqual(rose_fill.windows(0, 30), [(0, 30)])
        self.assertEqual(rose_fill.windows(10, 110), [(10, 59), (51, 100), (92, 110)])
        self.assertEqual([rose_fill.padded(n) for n in (1, 17, 18, 30, 49)], [17, 17, 33, 33, 49])

    def test_a_run_is_cut_round_its_masks(self):
        m = np.zeros((2160, 3840), np.float32)
        m[1000:1100, 1900:1950] = 1
        x, y, w, h = rose_fill.cut_for([m, None], 2160, 3840)
        self.assertEqual((w, h), (rose_fill.MIN_SIDE, rose_fill.MIN_SIDE))   # a small thing: at least this much round it
        self.assertTrue(x <= 1900 and x + w >= 1950)
        self.assertIsNone(rose_fill.cut_for([None, np.zeros((10, 10))], 10, 10))
        mw, mh = rose_fill.model_size(1600, 900)
        self.assertEqual((mw % 16, mh % 16), (0, 0))
        self.assertAlmostEqual(mw * mh, rose_fill.AREA, delta=rose_fill.AREA * 0.05)

    def test_real_texture_is_added_where_the_frames_round_agree_and_not_where_they_dont(self):
        rng = np.random.default_rng(4)
        truth = cv2.GaussianBlur(rng.random((200, 300, 3), np.float32), (0, 0), 1.0) * 0.5 + 0.25
        drawn = cv2.GaussianBlur(truth, (0, 0), 3)                   # ROSE's, scaled up: right, but soft
        seen = np.zeros((200, 300), bool)
        seen[:, :200] = True
        borrowed = truth.copy()
        borrowed[:, 100:200] = np.roll(truth, 40, axis=1)[:, 100:200] + 0.3     # the flow's wrong there
        texture, trust = rose_fill.add_detail(drawn, borrowed, seen, 4.0)
        fine = lambda img: cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_32F).std()
        good = slice(10, 90)
        self.assertGreater(fine((drawn + texture)[:, good]), 2 * fine(drawn[:, good]))
        self.assertLess(np.abs(drawn + texture - truth)[:, good].mean(), np.abs(drawn - truth)[:, good].mean())
        self.assertLess(trust[:, 120:180].max(), 0.05)                   # not trusted where they disagree
        self.assertEqual(float(np.abs(texture[:, 210:]).max()), 0.0)     # nothing where nothing was seen

    def test_with_effects_what_rose_changed_next_to_it_goes_in_too(self):
        frames, masks = _clip(20)

        class Shadowless(FakeRose):
            def run(self, frames, masks, seed=0, step=None):
                out = super().run(frames, masks, seed, step)
                for j in range(len(masks)):                          # it took the shadow under it out too
                    ys, xs = np.nonzero(masks[j])
                    if len(ys):
                        tall = masks.shape[1] // 6
                        out[j, ys.max() + 2:ys.max() + tall, xs.min():xs.max()] += 0.25
                return out

        def run(effects):
            written = {}
            rose_fill.Remover(Shadowless(), effects=effects, detail=False).remove(
                lambda a, b: ((i, frames[i]) for i in range(a, b)), lambda i: masks.get(i), 0, 20,
                lambda i, f: written.__setitem__(i, f))
            return written

        under = (slice(76, 84), slice(44, 64))                               # frame 10's: just past its mask and seam
        src = frames[10].astype(np.float32) / 255
        self.assertLess(np.abs(run(False)[10][under] - src[under]).mean(), 0.01)
        self.assertGreater(np.abs(run(True)[10][under] - src[under]).mean(), 0.1)

    def run_part(self, frames, masks, start, end, done=None, first=None):
        model = FakeRose()
        written = dict(done or {})
        order = []

        def write(i, f):
            order.append(i)
            written[i] = f.copy()

        def frames_of(a, b):
            for i in range(a, b):
                yield i, frames[i]

        rose_fill.Remover(model).remove(frames_of, lambda i: masks.get(i), start, end, write,
                                        lambda i: written.get(i), None, first=first)
        return model, written, order

    def test_every_frame_is_written_once_in_order_the_overlap_carried_from_the_run_before(self):
        frames, masks = _clip(70)
        masks[5] = None                                      # nothing painted on it: written as it was
        model, written, order = self.run_part(frames, masks, 0, 70)
        self.assertEqual(order, list(range(70)))
        self.assertEqual(len(model.calls), 2)
        f0, m0, _ = model.calls[0]
        f1, m1, _ = model.calls[1]
        self.assertEqual((f0.shape[0], f1.shape[0]), (49, 33))   # 49, and 29 made up to 33
        # The second run starts 8 frames back: those are given finished and masked nowhere.
        self.assertFalse(m1[:rose_fill.OVERLAP].any())
        self.assertTrue(m1[rose_fill.OVERLAP].any())
        self.assertTrue(np.array_equal(written[5], frames[5].astype(np.float32) / 255))
        green = written[20][55, 45 + 20]
        self.assertTrue(green[1] > 0.8 and green[0] < 0.3)  # painted over where the mask was
        self.assertTrue(np.allclose(written[20][5, 5], frames[20][5, 5] / 255, atol=1e-6))   # untouched elsewhere

    def test_carried_on_after_a_stop_the_run_holding_the_first_missing_frame_starts_over(self):
        frames, masks = _clip(70)
        _, whole, _ = self.run_part(frames, masks, 0, 70)
        done = {i: f for i, f in whole.items() if i < 50}
        model, again, order = self.run_part(frames, masks, 0, 70, done=done, first=50)
        self.assertEqual(order, list(range(50, 70)))             # nothing written before is written again
        self.assertEqual(len(model.calls), 1)                    # only the second run
        frames1, m1, _ = model.calls[0]
        self.assertFalse(m1[:50 - 41].any())                     # 41..49: written before, given as they were
        for i in range(50, 70):
            self.assertLess(np.abs(again[i] - whole[i]).mean(), 0.01, i)


@unittest.skipUnless(cv2, "OpenCV isn't installed for this Python")
class PageQualityTests(unittest.TestCase):
    """The page's Quality: High only where the add-on and its model are there."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication, Qt
        from PySide6.QtWidgets import QApplication
        from shiboken6 import delete
        from pages.generative.page import GenerativePage, _same_job
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from test_generative_video import _Host
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        # The engine clears its work folder as it starts: never the real one, which a running Buddy's
        # job reads its tracking from (a test run took one's, 2026-10-07).
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        patch = mock.patch.object(es, "WORK_DIR", __import__("pathlib").Path(work.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.page = GenerativePage(_Host())
        self.addCleanup(delete, self.page)
        self.addCleanup(self.page.on_app_quitting)
        self.events = []
        self.page.emit = lambda name, value=None: self.events.append((name, value))
        self.same_job = _same_job

    def hq(self, **h):
        self.page.engine = {"checking": False, "env": True, "models": {"lama": True}, "detail": "", "gpu": {},
                            "hq": h}

    def text(self):
        (p,) = self.page.settings_pages()
        return json.dumps(p, ensure_ascii=False)

    def test_high_is_offered_used_and_switched_only_where_it_can_run(self):
        self.hq(supported=False, why="It needs an NVIDIA RTX graphics card.")
        self.assertFalse(self.page._hq("pictures") or self.page._hq("footage"))
        self.assertIn("Not available", self.text())
        self.assertNotIn("generative-rose", json.dumps(self.page.ai_models()))
        self.hq(supported=True, card="NVIDIA GeForce RTX 5080", installed=False, models={})
        view = self.page._hq_view()
        self.assertTrue(view["hint"])
        self.assertAlmostEqual(view["gb"], (es.HQ_MB + 7995 + 3637) / 1000, delta=0.05)
        self.assertIn("hq_install", self.text())
        self.assertIn("generative-rose", json.dumps(self.page.ai_models()))
        self.page.on_dismiss_hq_hint()
        self.assertFalse(self.page._hq_view()["hint"])
        # Installed with only klein: High for pictures, not footage.
        self.hq(supported=True, card="RTX 5080", installed=True, models={"flux2-klein": True, "rose": False})
        self.assertEqual((self.page._hq("pictures"), self.page._hq("footage")), (True, False))
        self.hq(supported=True, card="RTX 5080", installed=True, models={"flux2-klein": True, "rose": True})
        text = self.text()
        self.assertIn("use_hq", text)
        self.assertIn("hq_remove", text)
        self.assertTrue(self.page._hq("footage"))
        self.page.on_hq({"on": False})                          # Standard: asked for with each removal, nothing restarts
        self.assertEqual((self.page._hq("pictures"), self.page._hq_view()["on"]), (False, False))
        self.page.on_setting("use_hq", True, None)
        self.assertTrue(self.page._hq("pictures"))

    def test_a_picture_is_removed_with_what_quality_says(self):
        self.hq(supported=True, card="RTX 5080", installed=True, models={"flux2-klein": True, "rose": True})
        self.page.doc = {"kind": "picture"}
        asked = []
        stroke = {"strokes": [{"size": 10, "points": [1, 1, 5, 5]}]}
        with mock.patch.object(self.page.client, "ask", side_effect=lambda do, args, *a: asked.append((do, args))), \
                mock.patch.object(self.page, "_push"):
            self.page.on_remove(stroke)
            self.page.busy = None
            self.page.on_hq({"on": False})
            self.page.on_remove(stroke)
        self.assertEqual([a for _do, a in asked], [dict(stroke, hq=True, margin=0), dict(stroke, hq=False, margin=0)])   # no effects for pictures
        # Footage's switches: its shadow and reflection (on to start with), finer detail (off).
        self.page.doc = None
        view = self.page._hq_view()
        self.assertEqual((view["effects"], view["fine"]), (True, False))
        self.page.on_hq_effects({"on": False})
        self.page.on_hq_fine({"on": True})
        view = self.page._hq_view()
        self.assertEqual((view["effects"], view["fine"]), (False, True))

    def test_a_job_made_the_other_way_is_not_carried_on_with(self):
        a = {"op": "remove", "source": "x", "start": 0, "end": 9, "keyframes": {}, "format": ".png", "video_format": "prores_hq"}
        self.assertTrue(self.same_job(a, dict(a, engine="standard")))     # jobs from before Quality: Standard
        self.assertFalse(self.same_job(dict(a, engine="rose"), dict(a, engine="standard")))
        rose = dict(a, engine="rose", effects=True, fine=False)
        self.assertTrue(self.same_job(rose, dict(rose)))
        self.assertFalse(self.same_job(rose, dict(rose, effects=False)))   # its shadow taken or not: another job
        self.assertFalse(self.same_job(rose, dict(rose, fine=True)))


def _run_engine(code, timeout=900):
    """A script in the engine's Python, from its folder, as the jobs run."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    out = subprocess.run([str(es.venv_python()), "-c", textwrap.dedent(code)], cwd=GEN, capture_output=True,
                         text=True, timeout=timeout, env=env)
    if out.returncode != 0:
        raise AssertionError(out.stdout[-2000:] + out.stderr[-3000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


@unittest.skipUnless(HQ and cv2, "The High quality add-on isn't installed on this machine")
class RealModelTests(unittest.TestCase):
    """Both models for real (on the graphics card): a red thing on a made-up scene removed."""

    def test_klein_takes_the_red_thing_out_of_a_picture(self):
        got = _run_engine(f"""
            import json, os, sys, numpy as np, cv2
            sys.path.insert(0, os.getcwd())
            import klein_fill
            h, w = 720, 1280
            y, x = np.mgrid[0:h, 0:w].astype(np.float32)
            clean = np.dstack([0.35 + 0.25 * x / w, 0.45 + 0.2 * y / h, np.full_like(x, 0.55)])
            clean[(y.astype(int) // 40) % 2 == 0] *= 0.9                     # stripes: a floor's boards
            img = clean.copy()
            cv2.circle(img, (640, 380), 70, (0.1, 0.1, 0.95), -1)            # a red disc (BGR)
            mask = np.zeros((h, w), np.float32); cv2.circle(mask, (640, 380), 78, 1.0, -1)
            out, box = klein_fill.KleinFiller(os.path.expanduser("~/.buddy/generative/models/flux2-klein-4b")).fill(
                (img * 255).astype(np.uint8), mask)
            o = out.astype(np.float32) / 255
            m = mask > 0
            print(json.dumps({{"red": float((o[m][:, 2] - o[m][:, 1]).mean()),
                               "err": float(np.abs(o - clean)[m].mean()),
                               "outside": float(np.abs(o - img)[~m & (np.abs(y - 380) > 120)].max())}}))
        """)
        self.assertLess(got["red"], 0.1, got)           # it was 0.85 more red than green
        self.assertLess(got["err"], 0.08, got)
        self.assertLess(got["outside"], 1.5 / 255, got)  # nothing far from it changed

    def test_rose_takes_a_moving_red_square_out_of_a_clip(self):
        got = _run_engine("""
            import json, os, sys, numpy as np, cv2
            sys.path.insert(0, os.getcwd())
            import rose_fill
            n, h, w = 17, 288, 512
            rng = np.random.default_rng(2)
            world = cv2.GaussianBlur(rng.random((h, w + 2 * n, 3), np.float32), (0, 0), 5) * 0.6 + 0.2
            clean = [world[:, 2 * i:2 * i + w].copy() for i in range(n)]
            frames, masks = [], []
            for i in range(n):
                f = clean[i].copy(); f[110:170, 150 + 6 * i:210 + 6 * i] = (0.1, 0.1, 0.95)
                frames.append((f * 255).astype(np.uint8))
                m = np.zeros((h, w), np.float32); m[104:176, 144 + 6 * i:216 + 6 * i] = 1; masks.append(m)
            out = {}
            rose_fill.Remover(rose_fill.Rose(os.path.expanduser("~/.buddy/generative/models/rose"))).remove(
                lambda a, b: ((i, frames[i]) for i in range(a, b)), lambda i: masks[i], 0, n,
                lambda i, f: out.__setitem__(i, f))
            red = [float((out[i][masks[i] > 0][:, 2] - out[i][masks[i] > 0][:, 1]).mean()) for i in range(n)]
            err = [float(np.abs(out[i] - clean[i])[masks[i] > 0].mean()) for i in range(n)]
            print(json.dumps({"n": len(out), "red": max(red), "err": max(err)}))
        """)
        self.assertEqual(got["n"], 17)
        self.assertLess(got["red"], 0.1, got)
        self.assertLess(got["err"], 0.12, got)


if __name__ == "__main__":
    unittest.main()
