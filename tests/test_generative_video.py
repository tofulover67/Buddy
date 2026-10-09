"""Generative on footage, the engine's side: reading a clip's frames exactly
(either reader, 10-bit kept), sequences written frame by frame, masks
carried along the flow from the frames they were painted on, mattes held
steady, removal filled from neighbouring frames (and steadier than one frame
at a time), and a whole job that picks up where it was stopped - on clips
made here, no footage needed."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
import numpy as np

try:
    import cv2
    # The engine's modules import each other by bare name (they run from
    # their own folder). Its folder goes on the path only while they load,
    # and last: Transcribe has a worker.py too, which other tests import.
    _GEN = str(_paths.APP / "pages" / "generative")
    sys.path.append(_GEN)
    try:
        import footage
        import track
        import video_fill
    finally:
        sys.path.remove(_GEN)
except ImportError:  # pragma: no cover - no OpenCV for this Python
    cv2 = None

from pages.generative import engine_setup as es

ENGINE = es.venv_python().exists() and es.model_path("lama").exists()
JOB = str(_paths.APP / "pages" / "generative" / "video_job.py")
H, W, N = 144, 192, 30


def world():
    rng = np.random.default_rng(4)
    a = cv2.GaussianBlur(rng.random((H, W + 3 * N), np.float32), (0, 0), 3)
    b = cv2.GaussianBlur(rng.random((H, W + 3 * N), np.float32), (0, 0), 6)
    out = np.dstack([a, b, a * 0.5 + 0.3])
    out = (out - out.min()) / (out.max() - out.min())
    out[:, :, 1] += 0.15 * np.sin(np.arange(W + 3 * N) / 7.0)[None, :]
    return np.clip(out, 0, 1).astype(np.float32)


WORLD = world() if cv2 else None


def sticker():
    """The thing to remove: red, with a pattern of its own (flow follows texture, not a flat colour)."""
    rng = np.random.default_rng(9)
    t = cv2.GaussianBlur(rng.random((30, 24), np.float32), (0, 0), 1.2)
    t = (t - t.min()) / (t.max() - t.min())
    return np.dstack([0.05 + 0.3 * t, 0.05 + 0.3 * t, 0.45 + 0.55 * t]).astype(np.float32)   # its brightness varies


STICKER = sticker() if cv2 else None


def background(t, pan=2):
    return WORLD[:, pan * t: pan * t + W].copy()


def square(t, speed=3):
    m = np.zeros((H, W), np.float32)
    x = 30 + speed * t
    m[55:85, x:x + 24] = 1
    return m


def shot(pan=2, speed=3):
    """{t: 8-bit frame}: a textured background panning under a patterned red square moving the other way."""
    out = {}
    for t in range(N):
        f = background(t, pan)
        x = 30 + speed * t
        f[55:85, x:x + 24] = STICKER
        out[t] = (f * 255 + 0.5).astype(np.uint8)
    return out


def numbered(i, depth=8):
    """A frame that says its own number: a flat patch at 20 + 7 * i."""
    top = 255 if depth == 8 else 65535
    f = np.full((H, W, 3), top // 4, np.uint8 if depth == 8 else np.uint16)
    v = (20 + 7 * i) * (1 if depth == 8 else 257)
    f[16:112, 16:176] = v
    return f


def number_of(frame):
    v = float(frame[40:90, 40:150].mean()) / (1 if frame.dtype == np.uint8 else 257)
    return int(round((v - 20) / 7))


@unittest.skipUnless(cv2, "OpenCV isn't installed for this Python")
class FootageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.ffmpeg = footage.find_ffmpeg()

    def tearDown(self):
        self.tmp.cleanup()

    def mp4(self, frames, name="clip.avi"):
        path = os.path.join(self.dir, name)
        out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 24, (W, H))
        for t in sorted(frames):
            out.write(frames[t])
        out.release()
        return path

    def test_both_readers_land_on_the_frame_asked_for(self):
        path = self.mp4({i: numbered(i) for i in range(N)})
        infos = [footage.probe(path)]
        if self.ffmpeg:
            infos.append(footage.probe(path, self.ffmpeg))
        for info in infos:
            with self.subTest(reader=info["reader"]):
                self.assertEqual((info["width"], info["height"], info["frames"], info["fps"]), (W, H, N, "24/1"))
                r = footage.Reader(info, self.ffmpeg)
                self.assertEqual([number_of(f) for _, f in r.frames(10, 5)], [10, 11, 12, 13, 14])
                self.assertEqual([n for n, _ in r.frames(10, 5)], [10, 11, 12, 13, 14])
                self.assertEqual(number_of(r.frame(0)), 0)
                self.assertEqual(number_of(r.frame(N - 1)), N - 1)
                self.assertEqual(len(list(r.frames(N - 3, 10))), 3)        # the clip ends where it ends
                self.assertEqual(list(r.frames(N + 5, 3)), [])

    def test_ten_bit_footage_keeps_its_depth_through_ffmpeg(self):
        if not self.ffmpeg:
            self.skipTest("no ffmpeg on this machine")
        seq = footage.Sequence(self.dir, "src", ".png")
        ramp = (np.linspace(0, 65535, W)[None, :, None] * np.ones((H, 1, 3))).astype(np.uint16)
        for i in range(6):
            seq.write(i, ramp)
        mov = os.path.join(self.dir, "ten.mov")
        subprocess.run([self.ffmpeg, "-v", "error", "-y", "-framerate", "24", "-i", seq.pattern(),
                        "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", mov], check=True,
                       **footage._QUIET)
        info = footage.probe(mov, self.ffmpeg)
        self.assertEqual((info["depth"], info["frames"], info["codec"]), (16, 6, "prores"))
        f = footage.Reader(info, self.ffmpeg).frame(3)
        self.assertEqual(f.dtype, np.uint16)
        row = f[H // 2, :, 1].astype(int)
        self.assertGreater(len(np.unique(row)), 150)                    # finer than 8-bit steps
        self.assertTrue(np.any(row % 257))
        self.assertEqual(footage.Reader(footage.probe(mov), None).frame(3).dtype, np.uint8)   # OpenCV: 8-bit

    def test_a_clip_stored_on_its_side_is_read_upright(self):
        # A phone's vertical video: stored 192 x 144, tagged to be turned a quarter. ffmpeg stands its frames up
        # as it decodes them; read as 192 x 144 they were stripes of nonsense (2026-10-07).
        if not self.ffmpeg:
            self.skipTest("no ffmpeg on this machine")
        flat = self.mp4({i: shot()[i] for i in range(6)})
        for turn in (90, -90, 180):
            with self.subTest(turn=turn):
                path = os.path.join(self.dir, f"turned{turn}.mp4")
                subprocess.run([self.ffmpeg, "-v", "error", "-y", "-i", flat, "-c:v", "libx264", "-qp", "0",
                                "-pix_fmt", "yuv444p", os.path.join(self.dir, "plain.mp4")], check=True,
                               **footage._QUIET)
                subprocess.run([self.ffmpeg, "-v", "error", "-y", "-display_rotation:v:0", str(turn), "-i",
                                os.path.join(self.dir, "plain.mp4"), "-c", "copy", path], check=True, **footage._QUIET)
                info = footage.probe(path, self.ffmpeg)
                upright = (H, W) if turn == 180 else (W, H)
                self.assertEqual((info["height"], info["width"]), upright)
                f = footage.Reader(info, self.ffmpeg).frame(3)
                self.assertEqual(f.shape[:2], upright)
                png = subprocess.run([self.ffmpeg, "-v", "error", "-ss", f"{3 / 24:.6f}", "-i", path, "-frames:v", "1",
                                      "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True,
                                     **footage._QUIET).stdout
                own = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
                self.assertLess(float(np.abs(own.astype(int) - f.astype(int)).mean()), 1.0)   # as ffmpeg shows it
                self.assertEqual(footage.Reader(footage.probe(path), None).frame(3).shape[:2], upright)   # OpenCV too

    def test_a_full_range_clip_comes_back_looking_the_same(self):
        # Read, written as ProRes (limited range), read again: the same picture, not just the same numbers.
        if not self.ffmpeg:
            self.skipTest("no ffmpeg on this machine")
        from pages.generative import video_formats as vf
        flat = self.mp4({i: shot()[i] for i in range(6)})
        src = os.path.join(self.dir, "full.mp4")
        subprocess.run([self.ffmpeg, "-v", "error", "-y", "-i", flat, "-vf", "setparams=range=pc", "-c:v", "libx264",
                        "-qp", "0", "-pix_fmt", "yuvj420p", "-color_range", "pc", src], check=True, **footage._QUIET)
        info = footage.probe(src, self.ffmpeg)
        self.assertEqual(info["range"], "pc")
        seq = footage.Sequence(os.path.join(self.dir, "frames"), "c", ".png")
        for i, f in footage.Reader(info, self.ffmpeg).frames(0, 6):
            seq.write(i, f)
        out = os.path.join(self.dir, "c.mov")
        cmd = vf.encode_command(self.ffmpeg, seq.pattern(), 0, 6, info["fps"], out, "prores_hq", info=info, audio=False)
        footage.encode(cmd, 6, out, lambda d: None)
        back = footage.probe(out, self.ffmpeg)
        self.assertEqual(back["range"], "tv")
        # Both read 16-bit: ffmpeg's 16-bit RGB comes out ~0.4/255 under its 8-bit RGB (writing undoes it).
        a = footage.Reader({**info, "depth": 16}, self.ffmpeg).frame(3).astype(float) / 257
        b = footage.Reader(back, self.ffmpeg).frame(3).astype(float) / 257
        self.assertLess(float(np.abs((b - a).mean(axis=(0, 1))).max()), 0.5)   # no shift in any channel
        self.assertLess(float(np.percentile(np.abs(b - a), 99)), 2.5)

    def test_a_sequence_is_written_whole_and_knows_what_it_has(self):
        seq = footage.Sequence(os.path.join(self.dir, "out"), "A001_clean", ".tif")
        for i in (120, 121, 123):
            seq.write(i, numbered(i - 120, 16))
        self.assertEqual(seq.first_missing(120, 125), 122)
        self.assertEqual(seq.first_missing(120, 122), 122)
        back = seq.read(121)
        self.assertEqual((back.dtype, number_of(back)), (np.uint16, 1))
        self.assertTrue(seq.path(123).endswith("A001_clean_000123.tif"))
        self.assertFalse([n for n in os.listdir(seq.folder) if ".part" in n])
        with self.assertRaises(footage.FootageError):
            footage.Sequence(self.dir, "x", ".jpg")


@unittest.skipUnless(cv2, "OpenCV isn't installed for this Python")
class TrackTests(unittest.TestCase):
    def iou(self, a, b):
        a, b = a > 0.5, b > 0.5
        return (a & b).sum() / max(1, (a | b).sum())

    def test_a_mask_follows_what_it_was_painted_on(self):
        frames = shot()
        grays = {t: track.gray_small(f) for t, f in frames.items()}
        one = track.carry_masks(grays, {0: square(0)}, 0, N)
        self.assertGreater(self.iou(one[15], square(15)), 0.75)
        self.assertGreater(self.iou(one[N - 1], square(N - 1)), 0.6)
        # A second keyframe at the end: the middle is nearer right.
        two = track.carry_masks(grays, {0: square(0), N - 1: square(N - 1)}, 0, N)
        self.assertGreater(self.iou(two[15], square(15)), self.iou(one[15], square(15)) - 0.02)
        self.assertGreater(self.iou(two[N - 1], square(N - 1)), 0.99)
        # Backward from a keyframe in the middle too.
        mid = track.carry_masks(grays, {20: square(20)}, 0, N)
        self.assertGreater(self.iou(mid[8], square(8)), 0.6)
        with self.assertRaises(ValueError):
            track.carry_masks(grays, {}, 0, N)

    def test_a_push_in_with_a_blurred_jolt_doesnt_drift(self):
        # Paint on a surface while the camera pushes in and turns a little, with a quick sideways jolt
        # motion-blurred over a few frames (the user's FX3 plaque): frame to frame only, the mask is left
        # where the blur threw it; matched to the keyframe, it's found again once the picture's sharp.
        rng = np.random.default_rng(3)
        tex = cv2.GaussianBlur(rng.random((900, 1600), np.float32), (0, 0), 2.0)
        tex = ((tex - tex.min()) / (tex.max() - tex.min()) * 255).astype(np.float32)
        h, w, n = 360, 640, 48

        def at(t):              # 0.6 % bigger and 0.06 degrees more a frame; 20 px sideways a frame over 24 .. 30
            dx = 20 * min(max(t - 24, 0), 6)
            return cv2.getRotationMatrix2D((800, 450), 0.06 * t, 1.006 ** t) + np.float32([[0, 0, 320 - 800 + dx],
                                                                                           [0, 0, 180 - 450]])
        streak = np.zeros((31, 31), np.float32)
        streak[15, :] = 1 / 31
        noise = np.random.default_rng(1)
        grays = {}
        for t in range(n):
            g = cv2.warpAffine(tex, at(t), (w, h), flags=cv2.INTER_LINEAR)
            if 25 <= t <= 30:
                g = cv2.filter2D(g, -1, streak)
            grays[t] = np.clip(g + noise.normal(0, 3, (h, w)), 0, 255).astype(np.uint8)
        paint = np.zeros((h, w), np.float32)
        paint[150:210, 250:400] = 1

        def truth(t):           # the painted area where frame t shows it
            m = np.vstack([at(t), [0, 0, 1]]) @ np.linalg.inv(np.vstack([at(0), [0, 0, 1]]))
            return cv2.warpAffine(paint, m[:2], (w, h), flags=cv2.INTER_LINEAR)

        def worst_edge(a, b):
            ea = cv2.Canny((a > 0.5).astype(np.uint8) * 255, 50, 150) > 0
            far = cv2.distanceTransform((cv2.Canny((b > 0.5).astype(np.uint8) * 255, 50, 150) == 0)
                                        .astype(np.uint8), cv2.DIST_L2, 5)
            return float(far[ea].max())

        anchored = track.carry_masks(grays, {0: paint}, 0, n)
        chained = track.carry_masks(grays, {0: paint}, 0, n, anchor=False)
        self.assertLessEqual(worst_edge(anchored[n - 1], truth(n - 1)), 1.5)        # seen: 1
        self.assertGreater(worst_edge(chained[n - 1], truth(n - 1)), 8)             # seen: 17
        self.assertGreater(self.iou(anchored[n - 1], truth(n - 1)), 0.97)
        self.assertLessEqual(worst_edge(anchored[20], truth(20)), 1.5)              # before the jolt too

    def test_a_steadied_matte_flickers_less_and_still_follows(self):
        rng = np.random.default_rng(2)
        frames = shot(pan=0)                     # still background; the square moves 3 px a frame
        flow = track.Flow()
        prev, prev_gray, last_raw, raw_d, steady_d, follow = None, None, None, [], [], []
        shift = np.float32([[1, 0, 3], [0, 1, 0]])
        for t in range(N):
            truth = square(t)
            edge = cv2.dilate(truth, np.ones((5, 5))) - cv2.erode(truth, np.ones((5, 5)))
            # A model's flicker: its soft edge a little different each frame.
            raw = np.clip(cv2.GaussianBlur(truth, (0, 0), 1.0) + edge * rng.uniform(-0.12, 0.12, truth.shape),
                          0, 1).astype(np.float32)
            gray = track.gray_small(frames[t])
            out = raw if prev is None else track.steady(raw, track.line_up(prev, prev_gray, gray, flow))
            if last_raw is not None:
                # Flicker: the change at the square's edge from frame to frame, the last moved as the square did.
                raw_d.append(np.abs(raw - track.move(last_raw, shift))[edge > 0].mean())
                steady_d.append(np.abs(out - track.move(prev, shift))[edge > 0].mean())
            follow.append(self_iou(out, truth))
            prev, prev_gray, last_raw = out, gray, raw
        self.assertLess(np.mean(steady_d), 0.7 * np.mean(raw_d))
        self.assertGreater(min(follow), 0.9)                   # it hasn't lagged behind the square
        # A real change - the matte suddenly takes in more - comes through as it is.
        bigger = cv2.dilate(square(N - 1), np.ones((9, 9)))
        out = track.steady(bigger, prev)
        self.assertTrue(np.allclose(out[(bigger > 0) & (prev < 0.01)], 1.0))

    def test_the_flow_behind_a_removed_thing_is_rebuilt_smooth(self):
        # cv2.inpaint once rebuilt a pan of -3 px with ~2 px jumps column to
        # column, which combed every edge copied through it.
        hole = np.zeros((270, 480), bool)
        hole[90:210, 200:240] = True
        pan = np.zeros((270, 480, 2), np.float32)
        pan[..., 0] = -3
        junk = pan.copy()
        junk[hole] = [4, 1]                              # the removed thing's own movement
        out = track.fill_flow(junk, hole)
        self.assertLess(float(np.abs(out[hole] - pan[hole]).max()), 1e-3)
        yy, xx = np.mgrid[0:270, 0:480].astype(np.float32)
        zoom = np.dstack([(xx - 240) * 0.02, (yy - 135) * 0.02]).astype(np.float32)
        junk = zoom.copy()
        junk[hole] = [4, 4]
        self.assertLess(float(np.abs(track.fill_flow(junk, hole)[hole] - zoom[hole]).max()), 0.5)

    def test_a_things_movement_is_found_from_what_is_inside_it(self):
        frames = shot()
        g10, g11 = track.gray_small(frames[10]), track.gray_small(frames[11])
        m = track.movement(g10, g11, square(10))
        self.assertIsNotNone(m)
        centre = m @ np.float32([30 + 3 * 10 + 12, 70, 1])                # where its middle went
        self.assertAlmostEqual(float(centre[0]) - (30 + 3 * 10 + 12), 3, delta=0.3)   # +3, not the pan's -2
        self.assertAlmostEqual(float(centre[1]) - 70, 0, delta=0.3)
        self.assertIsNone(track.movement(g10, g11, np.zeros((H, W), np.float32)))
        flat = np.full((H, W), 128, np.uint8)
        self.assertIsNone(track.movement(flat, flat, square(10)))         # nothing to follow: None


def self_iou(a, b):
    a, b = a > 0.5, b > 0.5
    return (a & b).sum() / max(1, (a | b).sum())


@unittest.skipUnless(cv2, "OpenCV isn't installed for this Python")
class FillTests(unittest.TestCase):
    def run_fill(self, frames, masks, filler=None):   # None: inpaint_filler (not a default: no video_fill without OpenCV)
        filler = filler or video_fill.inpaint_filler
        clip = video_fill.Clip(lambda i: frames.get(i), lambda i: masks.get(i))
        flow, prev, outs, said = track.Flow(), None, {}, {}
        for t in range(N):
            out, s = video_fill.fill_frame(clip, t, prev, filler, flow, lo=0, hi=N - 1)
            outs[t], said[t], prev = out, s, (t, out)
        return outs, said

    def test_a_removal_fills_from_the_frames_round_it(self):
        frames = shot()
        outs, said = self.run_fill(frames, {t: square(t) for t in range(N)})
        err_t, err_one = [], []
        for t in range(N):
            sq = square(t) > 0
            err_t.append(np.abs(outs[t][sq] - background(t)[sq]).mean())
            alone = video_fill.inpaint_filler(footage.to_float(frames[t]),
                                              cv2.dilate(sq.astype(np.uint8), np.ones((7, 7))) > 0)
            err_one.append(np.abs(alone[sq] - background(t)[sq]).mean())
        self.assertLess(np.mean(err_t), 0.5 * np.mean(err_one))
        self.assertLess(np.mean(err_t), 0.04)
        self.assertEqual(said[N // 2]["model"], 0)                 # all seen in other frames
        self.assertGreater(said[N // 2]["neighbours"], 0)
        outside = square(10) == 0
        far = cv2.erode(outside.astype(np.uint8), np.ones((15, 15))) > 0
        self.assertTrue(np.allclose(outs[10][far], footage.to_float(frames[10])[far]))   # the rest untouched

    def test_what_had_to_be_invented_holds_still(self):
        # A locked-off shot of a square that doesn't move: no frame shows
        # what's behind it - filled once by the model, then carried.
        frames = shot(pan=0, speed=0)
        calls = []

        def counting(img, hole):
            calls.append(int(hole.sum()))
            return video_fill.inpaint_filler(img, hole)

        outs, said = self.run_fill(frames, {t: square(0) for t in range(N)}, counting)
        self.assertEqual(len(calls), 1)
        self.assertGreater(said[5]["carried"], 0)
        sq = square(0) > 0
        drift = max(np.abs(outs[t][sq] - outs[0][sq]).max() for t in range(1, N))
        self.assertLess(drift, 0.02)                               # no flicker frame to frame

    def _fixed_hole(self, gain_end=1.0, n=30):
        """A hole fixed in the frame (a reflection of the camera, dirt on the lens) over a scene drifting
        a third of a pixel a frame behind it - as the user's mirror shot did, 9 px in 178 frames: next to
        nothing behind it ever shown - optionally darkening to gain_end."""
        world = cv2.resize(WORLD, (W * 2, H * 2), interpolation=cv2.INTER_CUBIC)
        frames, mask = {}, np.zeros((H, W), np.float32)
        mask[40:110, 120:170] = 1
        for t in range(n):
            m = np.float32([[1, 0, -20 - 0.3 * t], [0, 1, -40 - 0.1 * t]])
            f = cv2.warpAffine(world, m, (W, H), flags=cv2.INTER_CUBIC) * (1 + (gain_end - 1) * t / (n - 1))
            frames[t] = (np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8)
        return frames, mask

    def _fill_all(self, frames, mask, backdrop):
        clip = video_fill.Clip(lambda i: frames.get(i), lambda i: mask if i in frames else None)
        flow, prev, outs = track.Flow(), None, {}
        for t in sorted(frames):
            out, _ = video_fill.fill_frame(clip, t, prev, video_fill.inpaint_filler, flow, lo=0, hi=len(frames) - 1,
                                           backdrop=backdrop)
            outs[t], prev = out, (t, out)
        return outs

    def test_what_was_invented_keeps_its_detail_where_handing_it_on_smeared_it(self):
        # Nothing behind the hole is ever shown: all of it invented on the first frame. Handed on frame to frame
        # along the drifting scene, each step's resampling softened it (the user's mirror shot, 178 frames);
        # kept on its frame and carried by one homography (Backdrop), it holds its detail.
        frames, mask = self._fixed_hole()
        m = mask > 0.5

        def detail(img):
            g = cv2.cvtColor((np.clip(img, 0, 1) * 255).astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
            return float(np.abs(cv2.Laplacian(g, cv2.CV_32F))[m].mean())

        handed = self._fill_all(frames, mask, None)
        kept = self._fill_all(frames, mask, video_fill.Backdrop())
        self.assertGreater(detail(kept[29]), 0.85 * detail(kept[1]))
        self.assertGreater(detail(kept[29]), detail(handed[29]) * 1.1)

    def test_what_was_invented_follows_the_light(self):
        # The same shot, once with the light steady and once darkening to 70%: what was invented is as much
        # darker frame by frame as the picture got. (Lucas-Kanade took the darkening for movement too, and
        # followed the shot the wrong way, 13 px off by the end, until it was followed on local contrast.)
        steady, mask = self._fixed_hole(gain_end=1.0)
        darker, _ = self._fixed_hole(gain_end=0.7)
        m = mask > 0.5
        a = self._fill_all(steady, mask, video_fill.Backdrop())
        b = self._fill_all(darker, mask, video_fill.Backdrop())
        for t in (10, 29):
            self.assertAlmostEqual(float(b[t][m].mean()) / float(a[t][m].mean()), 1 - 0.3 * t / 29, delta=0.03)

    def test_a_frame_with_nothing_painted_comes_back_as_it_was(self):
        frames = shot()
        clip = video_fill.Clip(lambda i: frames.get(i), lambda i: None)
        out, said = video_fill.fill_frame(clip, 3, None, video_fill.inpaint_filler, track.Flow())
        self.assertTrue(np.array_equal(out, footage.to_float(frames[3])))
        self.assertEqual(sum(said.values()), 0)


@unittest.skipUnless(cv2, "OpenCV isn't installed for this Python")
class JobTests(unittest.TestCase):
    """video_job.py as Buddy will run it - its own process - with OpenCV's
    inpainting standing in for the model."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        frames = shot()
        self.clip = os.path.join(self.dir, "shot.avi")
        out = cv2.VideoWriter(self.clip, cv2.VideoWriter_fourcc(*"MJPG"), 24, (W, H))
        for t in range(N):
            out.write(frames[t])
        out.release()

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, python=sys.executable, **spec):
        spec = {"op": "remove", "source": self.clip, "start": 4, "end": 20, "out": os.path.join(self.dir, "out"),
                "name": "shot", "filler": "opencv",
                "keyframes": {"4": [{"shape": "rect", "points": [30 + 12 - 3, 52, 30 + 12 + 24 + 3, 88]}]}, **spec}
        path = os.path.join(self.dir, "job.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        p = subprocess.run([str(python), JOB, path], capture_output=True, text=True, timeout=600)
        lines = [json.loads(ln[len("@@BUDDY@@"):]) for ln in p.stdout.splitlines() if ln.startswith("@@BUDDY@@")]
        return lines, p

    def tracked(self, folder, frames, bad=()):
        """Track's masks for frames (16-bit, a band across the sticker's path) - frames in bad unreadable."""
        os.makedirs(folder, exist_ok=True)
        out = {}
        for i in frames:
            path = os.path.join(folder, f"trackmask_{i}.png")
            if i in bad:
                open(path, "w").close()
            else:
                m = np.zeros((H, W), np.uint16)
                m[50:90, 20:170] = 65535
                cv2.imwrite(path, m)
            out[str(i)] = path
        return {"masks": out}

    def test_a_mask_that_cant_be_read_stops_the_job_rather_than_leave_its_frame(self):
        lines, p = self.job(tracked=self.tracked(os.path.join(self.dir, "engine"), range(4, 20), bad={9}))
        self.assertIn("Frame 9's tracking is gone", lines[-1].get("error", ""), p.stdout[-800:] + p.stderr[-800:])

    def test_a_removal_job_writes_every_frame_and_picks_up_where_it_stopped(self):
        lines, p = self.job()
        self.assertTrue(lines and lines[-1].get("done"), p.stdout[-800:] + p.stderr[-800:])
        seq = footage.Sequence(os.path.join(self.dir, "out"), "shot_clean", ".png")
        self.assertEqual(seq.first_missing(4, 20), 20)
        self.assertFalse(seq.has(3) or seq.has(20))
        self.assertTrue(any(ln.get("stage") == "Tracking the mask" for ln in lines))
        # The square's gone from a frame far from the keyframe.
        f = seq.read(16).astype(np.float32) / 255.0
        sq = square(16) > 0
        self.assertLess(np.abs(f[sq] - background(16)[sq]).mean(), 0.08)
        # Stopped part-way: the frames written stay, the rest are made.
        kept = {i: os.path.getmtime(seq.path(i)) for i in range(4, 12)}
        for i in range(12, 20):
            os.remove(seq.path(i))
        lines, p = self.job()
        self.assertTrue(lines[-1].get("done"), p.stderr[-800:])
        self.assertEqual(seq.first_missing(4, 20), 20)
        self.assertEqual({i: os.path.getmtime(seq.path(i)) for i in range(4, 12)}, kept)
        frames_done = [ln["frame"] for ln in lines if ln.get("stage") == "Removing"]
        self.assertEqual(frames_done, list(range(12, 20)))

    def test_tracks_masks_are_used_as_they_are(self):
        # Masks for every frame (here: exactly where the square is, 16-bit, at tracking's small size).
        sh, sw, _ = track.small_size(H, W)
        masks = {}
        for t in range(4, 20):
            m = cv2.resize((square(t) > 0).astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA)
            m = cv2.dilate(m, np.ones((5, 5), np.uint8))
            path = os.path.join(self.dir, f"mask_{t}.png")
            cv2.imwrite(path, (m * 65535).astype(np.uint16))
            masks[str(t)] = path
        lines, p = self.job(tracked={"masks": masks})
        self.assertTrue(lines and lines[-1].get("done"), p.stdout[-800:] + p.stderr[-800:])
        stages = {ln.get("stage") for ln in lines}
        self.assertIn("Using the tracking", stages)
        self.assertNotIn("Tracking the mask", stages)
        f = footage.Sequence(os.path.join(self.dir, "out"), "shot_clean", ".png").read(16).astype(np.float32) / 255.0
        sq = square(16) > 0
        self.assertLess(np.abs(f[sq] - background(16)[sq]).mean(), 0.08)
        # One missing: tracked again, as before.
        os.remove(masks["9"])
        lines, _ = self.job(tracked={"masks": masks}, out=os.path.join(self.dir, "out2"))
        self.assertIn("Tracking the mask", {ln.get("stage") for ln in lines})

    def test_a_video_file_is_made_with_the_sources_audio_and_timecode(self):
        ff = footage.find_ffmpeg()
        if not ff:
            self.skipTest("no ffmpeg on this machine")
        # The shot with two mono audio tracks and a timecode, as camera files have.
        clip = os.path.join(self.dir, "shot.mov")
        subprocess.run([ff, "-v", "error", "-y", "-i", self.clip,
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=2",
                        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "prores_ks", "-c:a", "pcm_s24le",
                        "-timecode", "10:00:00:20", "-shortest", clip], check=True)
        lines, p = self.job(source=clip, video_format="prores_hq", tag="_4-19")
        self.assertTrue(lines and lines[-1].get("done"), p.stdout[-800:] + p.stderr[-800:])
        out = os.path.join(self.dir, "out", "shot_clean_4-19.mov")
        self.assertEqual(lines[-1]["outputs"], {"clean": out})
        self.assertIn("Writing the video file", {ln.get("stage") for ln in lines})
        made = footage.probe(out, ff)
        self.assertEqual((made["codec"], made["frames"], made["audio"], made["timecode"]),
                         ("prores", 16, 2, "10:00:01:00"))                   # 4 frames on from 10:00:00:20
        # Its frames are the ones removed from, and the frames they were made from are gone.
        f = footage.to_float(footage.Reader(made, ff).frame(12))               # the clip's frame 16
        sq = square(16) > 0
        self.assertLess(np.abs(f[sq] - background(16)[sq]).mean(), 0.1)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "out", ".work", "frames")))
        # Run again: it's there, so nothing's done again.
        lines, _ = self.job(source=clip, video_format="prores_hq", tag="_4-19")
        self.assertTrue(lines[-1].get("done"))
        self.assertFalse({"Removing", "Writing the video file"} & {ln.get("stage") for ln in lines})

    def test_a_job_says_what_it_needs(self):
        lines, _ = self.job(keyframes={})
        self.assertIn("Paint over what to remove", lines[-1]["error"])
        lines, _ = self.job(start=50, end=60)
        self.assertIn("no frames", lines[-1]["error"])
        lines, _ = self.job(source=os.path.join(self.dir, "nope.mov"))
        self.assertIn("no file", lines[-1]["error"])

    @unittest.skipUnless(ENGINE, "Generative's engine isn't installed on this machine")
    def test_the_engine_removes_with_the_model(self):
        lines, p = self.job(python=es.venv_python(), filler="lama", start=0, end=8,
                            keyframes={"0": [{"shape": "rect", "points": [27, 52, 57, 88]}]})
        self.assertTrue(lines and lines[-1].get("done"), p.stdout[-800:] + p.stderr[-800:])
        seq = footage.Sequence(os.path.join(self.dir, "out"), "shot_clean", ".png")
        self.assertEqual(seq.first_missing(0, 8), 8)
        f = seq.read(4).astype(np.float32) / 255.0
        sq = square(4) > 0
        self.assertLess(np.abs(f[sq] - background(4)[sq]).mean(), 0.1)


    @unittest.skipUnless(ENGINE and (es.model_path("birefnet").exists() or es.model_path("birefnet-lite").exists()),
                         "no subject model downloaded on this machine")
    def test_the_engine_separates_the_subject_and_its_background(self):
        model = "birefnet-lite" if es.model_path("birefnet-lite").exists() else "birefnet"
        clip = os.path.join(self.dir, "disc.avi")
        out = cv2.VideoWriter(clip, cv2.VideoWriter_fourcc(*"MJPG"), 24, (W, H))
        yy, xx = np.mgrid[0:H, 0:W]
        for t in range(4):
            f = background(t)
            f[(xx - (60 + 6 * t)) ** 2 + (yy - 72) ** 2 < 30 ** 2] = [0.95, 0.75, 0.2]
            out.write((f * 255 + 0.5).astype(np.uint8))
        out.release()
        lines, p = self.job(python=es.venv_python(), op="separate", source=clip, start=0, end=2, model=model,
                            keyframes={}, filler="lama")
        self.assertTrue(lines and lines[-1].get("done"), p.stdout[-800:] + p.stderr[-800:])
        folder = os.path.join(self.dir, "out")
        sub = footage.Sequence(folder, "shot_subject", ".png").read(1)
        self.assertEqual(sub.shape[2], 4)                                  # on transparency
        disc = (xx - 66) ** 2 + (yy - 72) ** 2 < 24 ** 2
        far = (xx - 66) ** 2 + (yy - 72) ** 2 > 40 ** 2
        self.assertGreater(float(sub[:, :, 3][disc].mean()), 240)
        self.assertLess(float(sub[:, :, 3][far].mean()), 15)
        self.assertTrue(footage.Sequence(folder, "shot_background", ".png").has(1))
        self.assertTrue(footage.Sequence(os.path.join(folder, ".work"), "shot_matte", ".png").has(1))
        # Cut out: the subject the same way, and no background made.
        cut = os.path.join(self.dir, "cut")
        lines, p = self.job(python=es.venv_python(), op="cutout", source=clip, start=0, end=2, model=model,
                            keyframes={}, filler="lama", out=cut)
        self.assertTrue(lines and lines[-1].get("done"), p.stdout[-800:] + p.stderr[-800:])
        self.assertEqual(list(lines[-1]["outputs"]), ["subject"])
        self.assertTrue(footage.Sequence(cut, "shot_subject", ".png").has(1))
        self.assertFalse(footage.Sequence(cut, "shot_background", ".png").has(0))
        # As video files: H.264 picked, the subject's ProRes 4444 all the same - its transparency kept.
        ff = footage.find_ffmpeg()
        if not ff:
            return
        lines, p = self.job(python=es.venv_python(), op="separate", source=clip, start=0, end=2, model=model,
                            keyframes={}, filler="lama", video_format="h264", out=os.path.join(self.dir, "out2"))
        self.assertTrue(lines and lines[-1].get("done"), p.stdout[-800:] + p.stderr[-800:])
        outs = lines[-1]["outputs"]
        self.assertEqual({k: os.path.basename(v) for k, v in outs.items()},
                         {"subject": "shot_subject.mov", "background": "shot_background.mp4"})
        sub = footage.probe(outs["subject"], ff)
        self.assertEqual((sub["codec"], sub["alpha"], sub["frames"]), ("prores", True, 2))
        a = footage.Reader(sub, ff, alpha=True).frame(1)[:, :, 3].astype(np.float32) / 65535
        self.assertGreater(float(a[disc].mean()), 0.9)
        self.assertLess(float(a[far].mean()), 0.06)
        self.assertEqual(footage.probe(outs["background"], ff)["codec"], "h264")
        self.assertFalse(any(f.startswith("shot_matte") for f in os.listdir(os.path.join(self.dir, "out2", ".work"))))


OBJECT_CHECK = r'''
import json, sys
import numpy as np
import cv2
sys.path.insert(0, sys.argv[1])
import track_sam
# A ball moving left to right, squashing and stretching, over a scene panning the other way.
rng = np.random.default_rng(5)
scene = cv2.GaussianBlur(rng.random((400, 1200, 3), np.float32), (0, 0), 4)
scene = ((scene - scene.min()) / (scene.max() - scene.min()) * 180 + 40).astype(np.uint8)
H, W, N = 360, 640, 20
frames, truth = {}, {}
yy, xx = np.mgrid[0:H, 0:W]
for t in range(N):
    f = scene[20:20 + H, 300 - 10 * t:300 - 10 * t + W].copy()
    cx, cy = 150 + 16 * t, 180 + 25 * np.sin(t / 3)
    rx, ry = 60 * (1 + 0.3 * np.sin(t / 2)), 60 * (1 - 0.3 * np.sin(t / 2))
    ball = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1
    shade = np.clip(1.2 - 0.6 * np.hypot(xx - cx + 20, yy - cy + 20) / 60, 0.4, 1.2)
    f[ball] = (np.array([30, 120, 240]) * shade[ball, None]).clip(0, 255).astype(np.uint8)
    frames[t], truth[t] = f, ball
paint = cv2.dilate(truth[0].astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))).astype(np.float32)
sam = track_sam.Sam(sys.argv[2])
out = {}
for name, s in (("standard", None), ("object", sam)):
    small, how = track_sam.follow(((i, frames[i]) for i in range(N)), {0: paint}, 0, N, (H, W), s)
    left = [float((truth[i] & (cv2.resize(small[i], (W, H)) < 0.5)).sum()) / truth[i].sum() for i in range(N)]
    out[name] = {"how": how, "worst": max(left), "late": float(np.mean(left[10:]))}
print(json.dumps(out))
'''


@unittest.skipUnless(cv2 and es.venv_python().exists() and es.model_ready("sam2-tiny"),
                     "SAM 2 isn't downloaded on this machine")
class ObjectTrackTests(unittest.TestCase):
    """Track's Object (SAM 2, track_sam.py) in the engine's own Python."""

    def test_a_thing_moving_on_its_own_stays_covered(self):
        p = subprocess.run([str(es.venv_python()), "-c", OBJECT_CHECK, _GEN, str(es.model_path("sam2-tiny"))],
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        got = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(got["object"]["how"], {"0": "covers"})        # the paint covers the ball
        self.assertLess(got["object"]["worst"], 0.02)                  # the ball, covered on every frame
        self.assertLess(got["object"]["late"], got["standard"]["late"])


GPU_CHECK = r'''
import json, os, sys, tempfile
import numpy as np
sys.path.insert(0, sys.argv[1])
import gpu
model, want = sys.argv[2], sys.argv[3]
os.environ["BUDDY_GENERATIVE_GPU"] = want
gpu.CACHE = tempfile.mkdtemp()
said = []
s = gpu.session(model, preparing=lambda: said.append(1))
feed = {"normalized_diffs": np.linspace(0, 1, 16).astype(np.float32)}
out = s.run(None, feed)[0]
ref = gpu._cpu(model).run(None, feed)[0]
again = gpu.session(model, preparing=lambda: said.append(2))
print(json.dumps({"providers": s.get_providers(), "compiled": gpu.compiled(model), "said": said,
                  "diff": float(np.abs(out - ref).max()), "why": gpu.why(), "card": gpu.name(),
                  "kept": sorted(os.listdir(gpu.CACHE))}))
'''


@unittest.skipUnless(es.venv_python().exists() and es.model_ready("sam2-tiny"),
                     "the engine (with SAM 2's small graphs) isn't on this machine")
class GpuTests(unittest.TestCase):
    """gpu.py in the engine's own Python: on the card when there's the add-on and an RTX, compiled once and kept;
    on the CPU otherwise, saying why."""

    def check(self, want):
        model = str(es.model_path("sam2-tiny") / "pointer_tpos.onnx")
        p = subprocess.run([str(es.venv_python()), "-c", GPU_CHECK, _GEN, model, want], capture_output=True,
                           text=True, timeout=600)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_switched_off_it_runs_on_the_cpu(self):
        got = self.check("0")
        self.assertEqual(got["providers"], ["CPUExecutionProvider"])
        self.assertFalse(got["compiled"])
        self.assertEqual(got["said"], [])

    def test_on_the_card_compiled_once_and_kept(self):
        if not es.gpu_status()["installed"]:
            got = self.check("1")
            self.assertEqual(got["providers"], ["CPUExecutionProvider"])          # no add-on: the CPU, and why
            self.assertTrue(got["why"])
            return
        got = self.check("1")
        self.assertEqual(got["providers"], ["nv_tensorrt_rtx", "CPUExecutionProvider"])
        self.assertTrue(got["compiled"])
        self.assertEqual(got["said"], [1])            # prepared the first time only, kept for the second
        self.assertLess(got["diff"], 1e-2)
        self.assertTrue(got["card"].startswith("NVIDIA"))
        self.assertEqual(len([k for k in got["kept"] if k.startswith("pointer_tpos-")]), 1)
        self.assertFalse([k for k in got["kept"] if ".building-" in k])


@unittest.skipUnless(cv2, "OpenCV isn't installed for this Python")
class EngineFootageTests(unittest.TestCase):
    """worker.py's footage requests, in this process (no model needed)."""

    def setUp(self):
        import importlib
        self.worker = importlib.import_module("pages.generative.worker")   # by its package: see the imports above
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.clip = os.path.join(self.dir, "shot.avi")
        out = cv2.VideoWriter(self.clip, cv2.VideoWriter_fourcc(*"MJPG"), 24, (W, H))
        for t in range(N):
            out.write(numbered(t))
        out.release()
        self.engine = self.worker.Engine("", os.path.join(self.dir, "work"), lambda _m: None)

    def tearDown(self):
        self.engine.close_files()
        self.tmp.cleanup()

    def test_footage_opens_at_its_first_frame_and_shows_any(self):
        got = self.engine.do_video_open(self.clip)
        self.assertEqual((got["frames"], got["width"], got["index"], got["show"]), (N, W, 0, "source"))
        self.assertEqual(number_of(cv2.imread(got["preview"])), 0)
        self.assertEqual(number_of(cv2.imread(self.engine.do_video_frame(17)["preview"])), 17)
        self.assertEqual(self.engine.do_video_frame(999)["index"], N - 1)            # kept inside the clip
        with self.assertRaises(self.worker.Fail):
            self.engine.do_video_open(os.path.join(self.dir, "still.png"))

    def test_a_jobs_frames_are_shown_and_a_frame_not_made_shows_the_clips(self):
        self.engine.do_video_open(self.clip)
        seq = footage.Sequence(os.path.join(self.dir, "out"), "shot_clean", ".png")
        seq.write(5, numbered(25))
        got = self.engine.do_video_frame(5, "clean", seq.pattern())
        self.assertEqual((number_of(cv2.imread(got["preview"])), got.get("missing")), (25, None))
        got = self.engine.do_video_frame(6, "clean", seq.pattern())
        self.assertEqual((number_of(cv2.imread(got["preview"])), got["missing"]), (6, True))
        with self.assertRaises(self.worker.Fail):
            self.engine.do_video_frame(5, "nonsense", seq.pattern())

    def test_a_finished_video_file_is_looked_through_from_its_first_frame(self):
        self.engine.do_video_open(self.clip)
        ff = footage.find_ffmpeg()
        if not ff:
            self.skipTest("no ffmpeg on this machine")
        made = os.path.join(self.dir, "shot_clean_10-19.mov")    # the clip's frames 10 .. 19
        subprocess.run([ff, "-v", "error", "-y", "-ss", f"{10 / 24:.6f}", "-i", self.clip, "-frames:v", "10",
                        "-c:v", "prores_ks", made], check=True)
        got = self.engine.do_video_frame(14, "clean", made, 10)
        self.assertEqual((number_of(cv2.imread(got["preview"])), got.get("missing")), (14, None))
        got = self.engine.do_video_frame(25, "clean", made, 10)      # past its end: the clip's own
        self.assertEqual((number_of(cv2.imread(got["preview"])), got["missing"]), (25, True))

    def test_tracking_gives_a_mask_picture_a_frame_and_the_masks_themselves(self):
        self.engine.do_video_open(self.clip)
        got = self.engine.do_video_track(1, {"3": [{"shape": "rect", "points": [40, 40, 90, 90]}]}, 0, 10)
        self.assertEqual(sorted(got["masks"]), list(range(10)))
        m = cv2.imread(got["masks"][3], cv2.IMREAD_UNCHANGED)
        self.assertEqual(m.shape[2], 4)                            # white, see-through where it isn't painted
        self.assertGreater(int(m[60, 60, 3]), 200)
        self.assertLess(int(m[10, 10, 3]), 10)
        # ...and for the job, the mask as it is: 16-bit grey.
        self.assertEqual(sorted(got["exact"]), list(range(10)))
        e = cv2.imread(got["exact"][3], cv2.IMREAD_UNCHANGED)
        self.assertEqual((e.dtype, e.ndim), (np.uint16, 2))
        self.assertGreater(int(e[60, 60]), 60000)
        self.assertEqual(int(e[10, 10]), 0)
        with self.assertRaises(self.worker.Fail):
            self.engine.do_video_track(2, {"25": [{"shape": "rect", "points": [1, 1, 9, 9]}]}, 0, 10)


class FormatTests(unittest.TestCase):
    """video_formats.py: what a result's saved as, its timecode, ffmpeg's command."""

    def test_timecode_counts_on_from_the_sources(self):
        from pages.generative.video_formats import tc_add
        from fractions import Fraction
        self.assertEqual(tc_add("01:46:48:20", 12, Fraction(24000, 1001)), "01:46:49:08")
        self.assertEqual(tc_add("23:59:59:24", 1, 25), "00:00:00:00")
        # Drop-frame: 00:01:00;00 doesn't exist - after 00:00:59;29 comes 00:01:00;02...
        self.assertEqual(tc_add("00:00:59;29", 1, Fraction(30000, 1001)), "00:01:00;02")
        self.assertEqual(tc_add("00:09:59;29", 1, Fraction(30000, 1001)), "00:10:00;00")   # ...but every tenth minute's kept
        self.assertEqual(tc_add("00:59:59;29", 1, Fraction(30000, 1001)), "01:00:00;00")
        self.assertEqual(tc_add("00:00:00;00", 107892, Fraction(30000, 1001)), "01:00:00;00")   # an hour of 29.97
        self.assertEqual(tc_add("", 5, 24), "")
        self.assertEqual(tc_add("nonsense", 5, 24), "")

    def test_the_subject_keeps_its_transparency(self):
        from pages.generative import video_formats as vf
        self.assertEqual(vf.for_kind("h264", "subject"), "prores_4444")
        self.assertEqual(vf.for_kind("h264", "background"), "h264")
        self.assertEqual(vf.for_kind("sequence", "subject"), "sequence")
        self.assertEqual(vf.known("nonsense"), vf.DEFAULT)
        self.assertEqual([c["id"] for c in vf.choices()][-1], "sequence")

    def test_the_command_carries_the_audio_the_timecode_and_the_colours(self):
        from pages.generative import video_formats as vf
        hlg = {"width": 3840, "height": 2160, "matrix": "bt2020nc", "range": "tv", "primaries": "bt2020",
               "transfer": "arib-std-b67"}
        cmd = vf.encode_command("ffmpeg", "C:/o/a_%06d.png", 38, 56, "24000/1001", "C:/o/a.mov", "prores_hq",
                                source="C:/f/a.mxf", at=1.585, timecode="01:46:50:10", info=hlg)
        s = " ".join(cmd)
        self.assertIn("-start_number 38 -i C:/o/a_%06d.png", s)
        self.assertIn("-ss 1.585000 -t 2.335667 -i C:/f/a.mxf", s)
        self.assertIn("-map 0:v:0 -map 1:a?", s)                         # every audio track
        self.assertIn("out_color_matrix=bt2020:out_range=limited,format=yuv422p10le,"
                      "setparams=colorspace=bt2020nc:range=tv:color_primaries=bt2020:color_trc=arib-std-b67", s)
        self.assertIn("-colorspace bt2020nc -color_range tv", s)
        self.assertIn("-timecode 01:46:50:10", s)
        self.assertEqual(cmd[-1], "C:/o/a.mov")
        sub = " ".join(vf.encode_command("ffmpeg", "p_%06d.png", 0, 4, "24/1", "s.mov", "h264", kind="subject",
                                         source="a.mxf"))
        self.assertNotIn("a.mxf", sub)                                   # the subject has no sound
        self.assertIn("yuva444p10le", sub)
        h264 = " ".join(vf.encode_command("ffmpeg", "p_%06d.png", 0, 4, "24/1", "c.mp4", "h264"))
        self.assertIn("pad=ceil(iw/2)*2:ceil(ih/2)*2", h264)
        self.assertIn("out_color_matrix=bt601", h264)                     # nothing known: small, so BT.601

    def test_colours_are_read_and_written_as_resolve_shows_the_clip(self):
        # A Sony A7 IV clip: full range, no matrix said. Resolve shows it as full-range Rec.709; written back
        # as the same values labelled limited and BT.601, its shadows came back 6/255 darker, its highlights
        # 9/255 brighter, and bluish (measured in Resolve, 2026-10-07).
        from pages.generative import video_formats as vf
        sony = {"width": 3840, "height": 2160, "matrix": "", "range": "pc", "primaries": "", "transfer": ""}
        self.assertEqual(vf.colours(sony), {"matrix": "bt709", "range": "full", "primaries": "", "transfer": ""})
        self.assertIn("in_color_matrix=bt709:in_range=full", vf.decode_filter(sony))
        s = " ".join(vf.encode_command("ffmpeg", "p_%06d.png", 0, 4, "30000/1001", "c.mov", "prores_hq", info=sony))
        self.assertIn("out_color_matrix=bt709:out_range=limited", s)
        self.assertIn("setparams=colorspace=bt709:range=tv", s)
        self.assertNotIn("color_trc", s)                                 # nothing said, nothing made up
        self.assertEqual(vf.colours({"width": 720, "height": 480})["matrix"], "bt601")
        self.assertEqual(vf.colours({"width": 1080, "height": 1920})["matrix"], "bt709")   # upright phone video
        self.assertEqual(vf.colours({"full_range": True})["range"], "full")                # a job from before
        self.assertEqual(vf.colours({"transfer": "made-up"})["transfer"], "")


class JobFolderTests(unittest.TestCase):
    def test_the_same_job_goes_back_in_its_folder_and_another_never_writes_over_it(self):
        from pages.generative.page import _same_job, job_folder
        spec = {"op": "remove", "source": "C:/f/A001.mov", "start": 0, "end": 48, "keyframes": {"0": [1]},
                "format": ".png", "model": None, "out": "x"}
        held = {}
        exists = held.__contains__
        first = job_folder("C:/f/A001.mov", "remove", 0, 48, spec, exists=exists, read=held.get)
        self.assertEqual(first.replace("\\", "/"), "C:/f/Buddy Generative/A001 remove 0-47")
        held[first] = spec
        self.assertEqual(job_folder("C:/f/A001.mov", "remove", 0, 48, dict(spec, out="y"), exists=exists,
                                    read=held.get), first)               # stopped part-way: carried on with
        other = dict(spec, keyframes={"0": [2]})
        second = job_folder("C:/f/A001.mov", "remove", 0, 48, other, exists=exists, read=held.get)
        self.assertEqual(second, first + " 2")
        self.assertFalse(_same_job(None, spec))
        self.assertEqual(job_folder("C:/f/A001.mov", "remove", 0, 48, spec, root="D:/out", exists=lambda _p: False)
                         .replace("\\", "/"), "D:/out/A001 remove 0-47")

    def test_a_stopped_job_is_found_beside_its_clip_with_how_far_it_got(self):
        from pages.generative.page import FOLDER, job_progress, stopped_job
        with tempfile.TemporaryDirectory() as tmp:
            clip = os.path.join(tmp, "A001.mov")

            def job(name, **kw):
                out = os.path.join(tmp, FOLDER, name)
                os.makedirs(os.path.join(out, ".work", "frames"))
                spec = dict({"op": "remove", "source": clip, "start": 10, "end": 20, "name": "A001", "tag": "_10-19",
                             "video_format": "prores_hq", "out": "moved from here"}, **kw)
                with open(os.path.join(out, ".work", "job.json"), "w", encoding="utf-8") as fh:
                    json.dump(spec, fh)
                return out, spec

            self.assertIsNone(stopped_job(clip))                         # no folder at all
            out, spec = job("A001 remove 10-19")
            for i in range(10, 14):
                open(os.path.join(out, ".work", "frames", f"A001_clean_{i:06d}.png"), "w").close()
            found = stopped_job(clip)
            self.assertEqual((found["done"], found["of"], found["spec"]["out"]), (4, 10, out))   # where it is now
            open(os.path.join(out, "A001_clean_10-19.mov"), "w").close()  # its file made: finished
            self.assertIsNone(stopped_job(clip))
            self.assertIsNone(stopped_job(os.path.join(tmp, "B002.mov")))  # another clip's
            # A sequence: finished once every frame of every kind is there.
            seq = dict(spec, op="separate", video_format="sequence", format=".tif", out=tmp)
            for i in range(10, 20):
                open(os.path.join(tmp, f"A001_subject_{i:06d}.tif"), "w").close()
            self.assertEqual(job_progress(seq), (5, 10, False))           # the background's still to do
            for i in range(10, 20):
                open(os.path.join(tmp, f"A001_background_{i:06d}.tif"), "w").close()
            self.assertEqual(job_progress(seq), (10, 10, True))
            cut = dict(seq, op="cutout")
            self.assertEqual(job_progress(cut), (10, 10, True))          # a cut-out makes only the subject

    def test_footage_is_told_from_pictures_and_times_are_said_plainly(self):
        from pages.generative.page import _duration, is_footage
        self.assertTrue(is_footage("A001.MOV") and is_footage("b.mxf") and is_footage("c.mp4"))
        self.assertFalse(is_footage("still.png") or is_footage("plate.exr"))
        self.assertEqual([_duration(s) for s in (5, 89, 600, 7200)], ["5 s", "89 s", "10 min", "2.0 h"])


@unittest.skipUnless(cv2 and es.venv_python().exists(), "Generative's engine isn't installed on this machine")
class RunnerTests(unittest.TestCase):
    """video_runner.VideoRun: a job in its own process, reported on the UI thread, stopped by ending it."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        # A whole QApplication, not a QCoreApplication: the page tests after this build widgets on whichever
        # application is there first, and on a QCoreApplication that killed the test run (exit 127).
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.clip = os.path.join(self.dir, "shot.avi")
        out = cv2.VideoWriter(self.clip, cv2.VideoWriter_fourcc(*"MJPG"), 24, (W, H))
        for t, f in shot().items():
            out.write(f)
        out.release()

    def tearDown(self):
        self.tmp.cleanup()

    def run_job(self, stop_after=None, **spec):
        from PySide6.QtCore import QEventLoop, QTimer
        from pages.generative.video_runner import VideoRun
        spec = {"op": "remove", "source": self.clip, "start": 0, "end": 12, "name": "shot", "filler": "opencv",
                "out": os.path.join(self.dir, "out"),
                "keyframes": {"0": [{"shape": "rect", "points": [27, 52, 57, 88]}]}, **spec}
        run, steps, ended, loop = VideoRun(spec), [], [], QEventLoop()

        def step(s):
            steps.append(s)
            if stop_after and len([x for x in steps if x.get("stage") == "Removing"]) >= stop_after:
                run.stop()

        run.progressed.connect(step)
        run.finished.connect(lambda r, e: (ended.append((r, e)), loop.quit()))
        self.assertTrue(run.start())
        QTimer.singleShot(120000, loop.quit)
        loop.exec()
        return ended, steps

    def test_a_job_reports_its_frames_and_what_it_made(self):
        ended, steps = self.run_job()
        result, error = ended[0]
        self.assertIsNone(error)
        self.assertTrue(result["done"])
        self.assertEqual([s["frame"] for s in steps if s.get("stage") == "Removing"], list(range(12)))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "out", ".work", "job.json")))

    def test_its_tracking_is_kept_beside_it_so_the_engines_copies_can_go(self):
        engine = os.path.join(self.dir, "engine")
        masks = {}
        os.makedirs(engine)
        for i in range(12):
            m = np.zeros((H, W), np.uint16)
            m[50:90, 20:170] = 65535
            masks[str(i)] = os.path.join(engine, f"trackmask_{i}.png")
            cv2.imwrite(masks[str(i)], m)
        ended, _ = self.run_job(tracked={"masks": masks})
        self.assertIsNone(ended[0][1])
        work = os.path.join(self.dir, "out", ".work")
        with open(os.path.join(work, "job.json"), encoding="utf-8") as fh:
            kept = json.load(fh)["tracked"]["masks"]
        self.assertEqual(sorted(kept, key=int), [str(i) for i in range(12)])
        self.assertTrue(all(os.path.dirname(v) == os.path.join(work, "tracked") and os.path.isfile(v)
                            for v in kept.values()))
        # The engine started again (it clears its folder): the job carried on from its job.json still has them.
        import shutil
        shutil.rmtree(engine)
        seq = footage.Sequence(os.path.join(self.dir, "out"), "shot_clean", ".png")
        for i in range(6, 12):
            os.remove(seq.path(i))
        with open(os.path.join(work, "job.json"), encoding="utf-8") as fh:
            spec = json.load(fh)
        ended, steps = self.run_job(**spec)
        self.assertIsNone(ended[0][1])
        self.assertIn("Using the tracking", [s.get("stage") for s in steps])
        self.assertEqual(seq.first_missing(0, 12), 12)

    def test_stopping_ends_it_and_what_was_done_stays(self):
        ended, _ = self.run_job(stop_after=3)
        self.assertEqual(ended[0], (None, "Stopped."))
        seq = footage.Sequence(os.path.join(self.dir, "out"), "shot_clean", ".png")
        self.assertGreaterEqual(seq.first_missing(0, 12), 3)
        self.assertLess(seq.first_missing(0, 12), 12)
        ended, _ = self.run_job()                       # again: carries on
        self.assertIsNone(ended[0][1])
        self.assertEqual(seq.first_missing(0, 12), 12)


class _Settings(dict):
    def save(self):
        pass


class _Host:
    connected = False
    controller = None
    shared_settings = {"theme": "Resolve"}

    def tool_settings(self, _tool, defaults):
        return _Settings(defaults)

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")


@unittest.skipUnless(cv2 and ENGINE, "Generative's engine isn't installed on this machine")
class PageFootageTests(unittest.TestCase):
    """The page itself with the real engine: footage opened, looked through, removed from as a job."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication, Qt
        from PySide6.QtWidgets import QApplication
        from shiboken6 import delete
        from pages.generative.page import GenerativePage
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
        self.page.engine = {"checking": False, "env": True, "models": {"lama": True}, "detail": ""}
        self.page.tab = "remove"                         # the task, picked first
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clip = os.path.join(self.tmp.name, "A001.avi")
        out = cv2.VideoWriter(self.clip, cv2.VideoWriter_fourcc(*"MJPG"), 24, (W, H))
        for t, f in shot().items():
            out.write(f)
        out.release()

    def until(self, test, seconds=120):
        from PySide6.QtCore import QEventLoop, QTimer
        import time
        end = time.monotonic() + seconds
        while not test() and time.monotonic() < end:
            loop = QEventLoop()
            QTimer.singleShot(50, loop.quit)
            loop.exec()
        return test()

    def view(self):
        return [v for n, v in self.events if n == "state"][-1]["doc"]

    def test_a_removal_stopped_part_way_is_carried_on_even_after_the_clip_was_closed(self):
        self.page.settings["video_format"] = "sequence"
        self.page._open_any(self.clip, None)
        self.assertTrue(self.until(lambda: self.page.doc and not self.page.busy))
        self.assertIsNone(self.view()["stopped"])
        keys = {"0": [{"shape": "rect", "points": [27, 52, 57, 88]}]}
        self.page.on_video_track({"keyframes": keys, "sig": "a"})
        self.assertTrue(self.until(lambda: self.page.doc.get("track") and not self.page.busy))
        step = self.page._job_step

        def stop_soon(s):                                # stopped a few frames in
            step(s)
            if s.get("frame") is not None and s["frame"] >= 2 and self.page.run is not None:
                self.page.on_video_stop()
        self.page._job_step = stop_soon
        self.page.on_video_run({"keyframes": keys, "sig": "a"})
        self.assertTrue(self.until(lambda: self.page.run is None, 300))
        self.page._job_step = step
        stopped = self.view()["stopped"]
        self.assertEqual((stopped["op"], stopped["of"], stopped["first"], stopped["last"]), ("remove", N, 0, N - 1))
        self.assertTrue(0 < stopped["done"] < N, stopped)
        self.assertIn("Carry on", [v for n, v in self.events if n == "toast"][-1]["text"])
        # Closed and opened again - the paint and tracking gone from the view.
        self.page.on_close()
        self.assertTrue(self.until(lambda: not self.page.client.busy()))
        self.page._open_any(self.clip, None)
        self.assertTrue(self.until(lambda: self.page.doc and not self.page.busy))
        self.assertEqual(self.view()["stopped"], stopped)
        self.page.on_video_resume()
        self.assertTrue(self.view()["running"])
        self.assertTrue(self.until(lambda: self.page.run is None, 300))
        doc = self.view()
        self.assertEqual((doc["finished"], doc["stopped"], doc["results_range"]), (True, None, [0, N]))
        seq = footage.Sequence(self.page.doc["out"], "A001_clean", ".png")
        self.assertEqual(seq.first_missing(0, N), N)

    def test_footage_opens_is_looked_through_and_has_a_removal_run_on_it(self):
        self.page._open_any(self.clip, None, {"frame": 9})
        self.assertTrue(self.until(lambda: self.page.doc and self.page.doc["frame"] == 9))
        doc = self.view()
        self.assertEqual((doc["kind"], doc["frames"], doc["range"], doc["fps"]), ("video", N, [0, N], 24.0))
        self.page.on_video_range({"range": [4, 16]})
        self.assertEqual(self.view()["range"], [4, 16])
        for i in (10, 11, 12, 13):                       # a scrub: only the last one wanted is shown in the end
            self.page.on_video_frame({"index": i})
        self.assertTrue(self.until(lambda: self.page.doc["frame"] == 13 and not self.page.client.busy()))
        keys = {"4": [{"shape": "rect", "points": [27 + 12, 52, 57 + 12, 88]}]}
        # Step 3 before step 2: Track first.
        self.page.on_video_run({"keyframes": keys, "sig": "a"})
        self.assertIsNone(self.page.run)
        self.assertEqual(self.events[-1], ("toast", {"text": "Track it first."}))
        # Step 2: Track - a mask picture a frame, and the 16-bit masks the job will use.
        self.page.on_video_track({"keyframes": keys, "sig": "a"})
        self.assertTrue(self.until(lambda: self.page.doc.get("track") and not self.page.busy))
        doc = self.view()
        self.assertEqual((doc["track_sig"], sorted(map(int, doc["tracked"]))), ("a", list(range(4, 16))))
        # The paint changed since (the view's summary of it differs): Track again first.
        self.page.on_video_run({"keyframes": keys, "sig": "b"})
        self.assertIsNone(self.page.run)
        self.assertIn("changed since", self.events[-1][1]["text"])
        # Step 3, as tracked: ProRes 422 HQ (the default) with the tracking as it is.
        self.assertEqual(doc["video_format"], "prores_hq")
        self.page.on_video_run({"keyframes": keys, "sig": "a"})
        self.assertTrue(self.view()["running"])
        spec = self.page.doc["spec"]
        self.assertEqual((spec["video_format"], sorted(map(int, spec["tracked"]["masks"]))),
                         ("prores_hq", list(range(4, 16))))
        self.page.on_video_range({"range": [0, 5]})       # not while it runs
        self.assertEqual(self.page.doc["range"], [4, 16])
        self.assertTrue(self.until(lambda: self.page.run is None, 300))
        doc = self.view()
        self.assertEqual((doc["results"], doc["results_range"], doc["show"], doc["finished"]),
                         (["clean"], [4, 16], "clean", True))
        self.assertEqual(doc["files"], {"clean": "A001_clean_4-15.mov"})
        out = self.page.doc["out"]
        self.assertTrue(out.replace("\\", "/").endswith("Buddy Generative/A001 remove 4-15"))
        made = footage.probe(os.path.join(out, "A001_clean_4-15.mov"), footage.find_ffmpeg())
        self.assertEqual((made["codec"], made["frames"]), ("prores", 12))
        self.assertFalse(os.path.exists(os.path.join(out, ".work", "frames")))   # the frames it was made from: gone
        self.page.on_video_frame({"index": 8})            # looked through in the file
        self.assertTrue(self.until(lambda: self.page.doc["frame"] == 8 and self.page.doc.get("shown") == "clean"))
        self.page.on_video_frame({"index": 20})          # outside what was made: the clip's own
        self.assertTrue(self.until(lambda: self.page.doc["frame"] == 20 and self.page.doc.get("shown") == "source"))

    def test_settings_offer_the_graphics_card_only_where_it_can_be_used(self):
        def page_text(gpu):
            self.page.engine = {"checking": False, "env": True, "models": {}, "detail": "", "gpu": gpu}
            (p,) = self.page.settings_pages()
            self.assertEqual(p["id"], "ai_generative")
            return json.dumps(p, ensure_ascii=False)
        self.assertIn("runs on the processor", page_text({}))
        self.assertIn("older than the RTX 30", page_text({"card": "NVIDIA GeForce GTX 1080", "supported": False}))
        offer = page_text({"card": "NVIDIA GeForce RTX 5070 Ti", "supported": True, "installed": False})
        self.assertIn("gpu_install", offer)
        self.assertNotIn("use_gpu", offer)
        have = page_text({"card": "NVIDIA GeForce RTX 5070 Ti", "supported": True, "installed": True})
        self.assertIn("use_gpu", have)
        self.assertIn("gpu_remove", have)
        # The start screen's offer: once, until "Not now".
        self.assertTrue(self.page._gpu_view()["on"])
        self.page.engine["gpu"]["installed"] = False
        self.assertTrue(self.page._gpu_view()["hint"])
        self.page.on_dismiss_gpu_hint()
        self.assertFalse(self.page._gpu_view()["hint"])
        # Off: the engine starts again without it.
        self.page.engine["gpu"]["installed"] = True
        with mock.patch.object(self.page.client, "stop") as stop:
            self.page.on_setting("use_gpu", False, None)
        stop.assert_called_once()
        self.assertFalse(self.page.client.use_gpu)
        self.assertFalse(self.page._gpu_view()["on"])

    def test_tracked_forward_only_those_frames_are_removed_from(self):
        self.page._open_any(self.clip, None, {})
        self.assertTrue(self.until(lambda: self.page.doc and not self.page.client.busy()))
        self.page.on_video_range({"range": [4, 16]})
        self.page.on_video_direction({"id": "forward"})
        keys = {"8": [{"shape": "rect", "points": [27 + 24, 52, 57 + 24, 88], "angle": 20}]}
        self.page.on_video_track({"keyframes": keys, "sig": "f"})
        self.assertTrue(self.until(lambda: self.page.doc.get("track") and not self.page.busy))
        self.assertEqual(self.view()["track_span"], [8, 16])
        self.assertEqual(sorted(map(int, self.view()["tracked"])), list(range(8, 16)))
        self.page.on_video_run({"keyframes": keys, "sig": "f"})
        spec = self.page.doc["spec"]
        self.assertEqual((spec["start"], spec["end"]), (8, 16))
        self.assertTrue(spec["out"].replace("\\", "/").endswith("A001 remove 8-15"))
        self.assertTrue(self.until(lambda: self.page.run is None, 300))
        self.assertEqual(self.view()["results_range"], [8, 16])

    def test_in_and_out_come_from_the_timeline(self):
        self.page._open_any(self.clip, None, {})
        self.assertTrue(self.until(lambda: self.page.doc and not self.page.client.busy()))
        self.page.host.ensure_connected = lambda: object()
        answers = []
        self.page._calls.call = lambda name, args, run, done: (done(answers.pop(0), None), True)[1]
        # The clip under the playhead is this one, used from frame 5 for 9 frames, the playhead 2 frames in.
        answers.append({"name": "A001.avi", "path": self.clip, "id": "c1", "where": "timeline", "still": False,
                        "range": [5, 14], "frame": 7})
        self.page.on_video_from_timeline()
        self.assertEqual(self.page.doc["range"], [5, 14])
        self.assertEqual(self.page.doc["source"]["id"], "c1")
        self.assertTrue(self.until(lambda: self.page.doc["frame"] == 7))
        self.assertIn("frames 5–13", [v for n, v in self.events if n == "toast"][-1]["text"])
        # Another clip under the playhead: said, nothing changed.
        answers.append({"name": "B002.mov", "path": os.path.join(self.tmp.name, "B002.mov"), "id": "c2",
                        "where": "timeline", "still": False, "range": [0, 3], "frame": 0})
        self.page.on_video_from_timeline()
        self.assertEqual(self.page.doc["range"], [5, 14])
        self.assertIn("“B002.mov”, not this one", [v for n, v in self.events if n == "toast"][-1]["text"])

    def test_track_goes_forward_back_or_both_from_the_painted_frames(self):
        self.page.doc = {"kind": "video", "range": [10, 100], "frames": 120}
        self.page._push = lambda: None                        # nothing to show: only the spans are looked at
        for direction, painted, want in (("both", ["40"], [10, 100]), ("forward", ["40"], [40, 100]),
                                         ("backward", ["40"], [10, 41]), ("forward", ["60", "40"], [40, 100]),
                                         ("backward", ["60", "40"], [10, 61]), ("forward", ["5"], None)):
            self.page.on_video_direction({"id": direction})
            self.assertEqual(self.page._span({k: [1] for k in painted}), want, (direction, painted))
        self.page.on_video_direction({"id": "sideways"})
        self.assertEqual(self.page._direction(), "forward")               # not one: left as it was
        self.page.doc = None

    def test_the_format_picked_is_kept_and_needs_ffmpeg_for_a_video_file(self):
        self.page.on_video_format({"id": "h264"})
        self.assertEqual(self.page.settings["video_format"], "h264")
        self.page.on_video_format({"id": "nonsense"})
        self.assertEqual(self.page.settings["video_format"], "h264")
        self.page.doc = {"kind": "video", "ffmpeg": False}
        self.assertEqual(self.page._video_format(), "sequence")      # no ffmpeg: frames it is
        self.page.doc["ffmpeg"] = True
        self.assertEqual(self.page._video_format(), "h264")
        self.page.doc = None



@unittest.skipUnless(cv2, "OpenCV isn't installed for this Python")
class PageTaskTests(unittest.TestCase):
    """The task, picked first: nothing opens or drops before it, and it changes with a picture open."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication, Qt
        from PySide6.QtWidgets import QApplication
        from shiboken6 import delete
        from pages.generative.page import GenerativePage
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self.page = GenerativePage(_Host())
        self.addCleanup(delete, self.page)
        self.addCleanup(self.page.on_app_quitting)
        self.events = []
        self.page.emit = lambda name, value=None: self.events.append((name, value))
        self.page.engine = {"checking": False, "env": True, "models": {"lama": True, "birefnet-lite": True}, "detail": ""}

    def test_nothing_opens_or_drops_before_a_task_is_picked(self):
        self.assertIsNone(self.page.tab)
        self.assertFalse(self.page.accepts_drops())
        with mock.patch.object(self.page, "_open_any") as opened:
            self.page.on_files_dropped(["C:/x/still.png"])
            self.page.on_open_file()
        opened.assert_not_called()
        self.assertEqual(self.events[-1], ("toast", {"text": "Pick what you want to do first."}))
        self.page.on_tab({"id": "remove"})
        self.assertEqual((self.page.tab, self.page.accepts_drops()), ("remove", True))
        with mock.patch.object(self.page, "_open_any") as opened:
            self.page.on_files_dropped(["C:/x/still.png"])
        opened.assert_called_once_with("C:/x/still.png", None)

    def test_the_task_changes_with_a_picture_open_and_its_subject_is_found_then(self):
        self.page._push = lambda: None
        self.page.on_tab({"id": "remove"})
        self.page.doc = {"kind": "picture", "matte": None}
        with mock.patch.object(self.page, "on_find_subject") as find:
            self.page.on_tab({"id": "nonsense"})
            self.page.on_tab({"id": None})                       # back to the choice: not with something open
            self.assertEqual(self.page.tab, "remove")
            find.assert_not_called()
            self.page.on_tab({"id": "cutout"})                   # its first step, at once
            self.assertEqual(self.page.tab, "cutout")
            find.assert_called_once()
            self.page.doc["matte"] = {"matte": "m.png"}
            self.assertTrue(self.page._cutout())                 # Cut out's saves are the cut-out
            self.page.on_tab({"id": "separate"})                 # found already: kept
            find.assert_called_once()
            self.assertFalse(self.page._cutout())
        self.page.busy = {"kind": "matte"}
        self.page.on_tab({"id": "remove"})                       # never mid-work
        self.assertEqual(self.page.tab, "separate")
        self.page.busy = None
        self.page.doc = None
        self.page.on_tab({"id": None})
        self.assertIsNone(self.page.tab)

    def test_each_task_needs_its_own_models(self):
        self.page.engine["models"] = {"lama": True}
        self.assertEqual({t: self.page._missing(t) for t in ("remove", "cutout", "separate")},
                         {"remove": [], "cutout": ["matte"], "separate": ["matte"]})
        self.assertEqual(self.page._missing(), [])                # no task: nothing asked for


if __name__ == "__main__":
    unittest.main()
