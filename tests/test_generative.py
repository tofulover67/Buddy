"""Generative: where saves go, finding a still in Resolve and swapping it,
and - where the engine is installed on this machine - the engine itself:
Clean Up changes only what's painted, the bit depth survives a save, undo
puts the picture back exactly; Cut Out saves the subject with its matte as
transparency and Separate a background with the subject gone."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import _paths  # noqa: F401
import numpy as np

from pages.generative import engine_setup as es
from pages.generative import resolve_ext
from pages.generative.page import clean_name, layer_ext, saved_name


class CleanNameTests(unittest.TestCase):
    def test_beside_the_original_and_never_over_a_file(self):
        taken = {r"C:\shots\a_clean.png", r"C:\shots\a_clean2.png"}
        self.assertEqual(clean_name(r"C:\shots\b.png", taken.__contains__), r"C:\shots\b_clean.png")
        self.assertEqual(clean_name(r"C:\shots\a.png", taken.__contains__), r"C:\shots\a_clean3.png")

    def test_layers_are_named_after_the_picture_in_a_format_with_transparency(self):
        free = lambda _p: False  # noqa: E731
        self.assertEqual(saved_name(r"C:\s\cat.jpg", "_subject", layer_ext(r"C:\s\cat.jpg", "8-bit"), free),
                         r"C:\s\cat_subject.png")
        self.assertEqual(layer_ext("a.png", "16-bit"), ".tif")
        self.assertEqual(layer_ext("a.exr", "32-bit float"), ".exr")
        self.assertEqual(saved_name(r"C:\s\cat.jpg", "_background", None, free), r"C:\s\cat_background.jpg")


# ------------------------------------------------------------------ Resolve --

class FakePoolItem:
    def __init__(self, uid, name, path, kind="Still", fps=None):
        self.uid, self.name, self.path, self.kind, self.fps = uid, name, path, kind, fps
        self.replaced = []

    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return self.name

    def GetClipProperty(self, key):
        return {"Type": self.kind, "File Path": self.path, "FPS": self.fps}.get(key, "")

    def ReplaceClip(self, path):
        self.replaced.append(path)
        self.path = path
        return True


class FakeTimelineItem:
    """source: (first frame of the file used, length in timeline frames, record start) - or None:
    a Resolve that won't say."""

    def __init__(self, pool_item, source=None, track="video"):
        self.pool_item, self.source, self.track = pool_item, source, track

    def GetMediaPoolItem(self):
        return self.pool_item

    def GetTrackTypeAndIndex(self):
        return [self.track, 1]

    def GetSourceStartFrame(self):
        if self.source is None:
            raise AttributeError("GetSourceStartFrame")
        return self.source[0]

    def GetDuration(self):
        return self.source[1] if self.source else None

    def GetStart(self):
        return self.source[2] if self.source else None


class FakeTimeline:
    def __init__(self, item, fps="24", playhead="01:00:00:00", picked=()):
        self.item, self.fps, self.playhead, self.picked = item, fps, playhead, list(picked)

    def GetCurrentVideoItem(self):
        return self.item

    def GetSelectedClips(self):
        return self.picked

    def GetSetting(self, key):
        return self.fps if key == "timelineFrameRate" else ""

    def GetCurrentTimecode(self):
        return self.playhead


class FakeFolder:
    def __init__(self, clips, subs=(), name="Master"):
        self.clips, self.subs, self.name = list(clips), list(subs), name

    def GetName(self):
        return self.name

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.subs


class FakePool:
    def __init__(self, root, selected=()):
        self.root, self.selected, self.current = root, list(selected), root
        self.imported_into = None

    def GetRootFolder(self):
        return self.root

    def GetSelectedClips(self):
        return self.selected

    def GetCurrentFolder(self):
        return self.current

    def SetCurrentFolder(self, folder):
        self.current = folder
        return True

    def SetSelectedClip(self, item):
        self.picked = item
        return True

    def ImportMedia(self, paths):
        self.imported_into = self.current
        self.imported = paths
        first = paths[0]["FilePath"] if isinstance(paths[0], dict) else paths[0]
        return [FakePoolItem("new", os.path.basename(first).replace("_%06d", ""), first)]


class FakeProject:
    def __init__(self, timeline, pool):
        self.timeline, self.pool = timeline, pool

    def GetCurrentTimeline(self):
        return self.timeline

    def GetMediaPool(self):
        return self.pool


class FakeController:
    def __init__(self, project):
        self.project = project

    def current_project(self):
        return self.project


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.still = FakePoolItem("s1", "Poster", r"C:\stills\poster.png")
        self.video = FakePoolItem("v1", "Interview", r"C:\footage\a.mov", kind="Video + Audio")
        self.other = FakePoolItem("s2", "Logo", r"C:\stills\logo.tif")
        self.bin = FakeFolder([self.still], name="Stills")
        self.pool = FakePool(FakeFolder([self.video, self.other], [self.bin]))

    def controller(self, under_playhead=None, selected=(), source=None, **timeline):
        self.pool.selected = list(selected)
        timeline = FakeTimeline(FakeTimelineItem(under_playhead, source) if under_playhead else None, **timeline)
        return FakeController(FakeProject(timeline, self.pool))

    def test_the_still_under_the_playhead_first(self):
        got = resolve_ext.find(self.controller(self.still, [self.other]))
        self.assertEqual((got["id"], got["where"], got["path"]), ("s1", "timeline", r"C:\stills\poster.png"))

    def test_footage_under_the_playhead_comes_with_the_part_used_and_the_frame_at_the_playhead(self):
        self.video.fps = "24"
        # Used from the file's frame 100 for 48 frames, put at 01:00:00:00; the playhead 10 frames in.
        got = resolve_ext.find(self.controller(self.video, [self.other], source=(100, 48, 86400),
                                               playhead="01:00:00:10"))
        self.assertEqual((got["id"], got["where"], got["still"]), ("v1", "timeline", False))
        self.assertEqual((got["range"], got["frame"]), ([100, 148], 110))
        # A 48 fps file on a 24 fps timeline: two of its frames a timeline frame.
        self.video.fps = "48"
        got = resolve_ext.find(self.controller(self.video, source=(100, 48, 86400), playhead="01:00:00:10"))
        self.assertEqual((got["range"], got["frame"]), ([100, 196], 120))
        # The playhead past the clip's end: its last frame.
        got = resolve_ext.find(self.controller(self.video, source=(100, 48, 86400), playhead="01:00:10:00"))
        self.assertEqual(got["frame"], 195)

    def test_footage_resolve_wont_describe_is_the_whole_clip(self):
        got = resolve_ext.find(self.controller(self.video))
        self.assertEqual((got["id"], got["still"]), ("v1", False))
        self.assertNotIn("range", got)
        self.assertNotIn("frame", got)
        got = resolve_ext.find(self.controller(None, [self.video]))
        self.assertEqual((got["id"], got["where"]), ("v1", "pool"))

    def test_nothing_to_open_says_so(self):
        with self.assertRaisesRegex(resolve_ext.CleanupError, "playhead over a clip"):
            resolve_ext.find(self.controller())

    def test_a_clip_dragged_off_the_timeline_is_the_one_selected_there(self):
        # The drag says nothing of which clip it is, but pressing on a clip to drag it selects it (Resolve
        # 21.1, 2026-10-07): the selected one opens, not the one under the playhead.
        self.video.fps = "24"
        dragged = FakeTimelineItem(self.video, source=(100, 48, 86400 + 240))     # 10 s on, the playhead isn't on it
        audio = FakeTimelineItem(self.still, source=(0, 48, 86400), track="audio")  # its linked audio, selected with it
        got = resolve_ext.find(self.controller(self.still, picked=[audio, dragged]), selected=True)
        self.assertEqual((got["id"], got["where"], got["range"]), ("v1", "timeline", [100, 148]))
        self.assertEqual((got["frame"], got["others"]), (100, 0))               # its first frame, not the nearest
        got = resolve_ext.find(self.controller(self.still, picked=[dragged], playhead="01:00:10:05"), selected=True)
        self.assertEqual(got["frame"], 105)                                       # the playhead on it: that frame
        two = resolve_ext.find(self.controller(None, picked=[dragged, FakeTimelineItem(self.other)]), selected=True)
        self.assertEqual((two["id"], two["others"]), ("v1", 1))
        with self.assertRaisesRegex(resolve_ext.CleanupError, "which clip that was"):
            resolve_ext.find(self.controller(self.still, picked=[audio]), selected=True)

    def test_only_a_drag_off_resolves_timeline_counts_as_a_clip(self):
        from PySide6.QtCore import QMimeData

        from core.web_page import _is_resolve_clip
        mime = QMimeData()
        self.assertFalse(_is_resolve_clip(mime))
        mime.setData('application/x-qt-windows-mime;value="Timeline/Item"', b"SourcePointer")
        self.assertTrue(_is_resolve_clip(mime))
        other = QMimeData()
        other.setData('application/x-qt-windows-mime;value="Timeline/Items"', b"x")
        self.assertFalse(_is_resolve_clip(other))
        self.assertFalse(_is_resolve_clip(None))

    def test_a_sequence_goes_in_as_one_clip_beside_the_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            pattern = os.path.join(tmp, "poster_clean_%06d.png")
            Path(pattern.replace("%06d", "000100")).write_bytes(b"x")
            got = resolve_ext.add_sequence(self.controller(), "s1", pattern, 100, 147)
            self.assertEqual(got, {"name": "poster_clean.png", "bin": "Stills"})
            self.assertEqual(self.pool.imported, [{"FilePath": pattern, "StartIndex": 100, "EndIndex": 147}])
            self.assertIs(self.pool.imported_into, self.bin)
            self.assertIs(self.pool.current, self.bin)          # left showing, the new clip selected
            self.assertEqual(self.pool.picked.uid, "new")
            with self.assertRaisesRegex(resolve_ext.CleanupError, "frames aren't there"):
                resolve_ext.add_sequence(self.controller(), "s1", pattern, 5, 9)

    def test_swap_finds_the_clip_in_a_bin_and_checks_it_took(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "poster_clean.png")
            Path(path).write_bytes(b"x")
            self.assertEqual(resolve_ext.swap(self.controller(), "s1", path), {"name": "Poster"})
            self.assertEqual(self.still.replaced, [path])
            self.still.ReplaceClip = lambda _p: True          # says yes, changes nothing
            other = os.path.join(tmp, "again.png")
            Path(other).write_bytes(b"x")
            with self.assertRaisesRegex(resolve_ext.CleanupError, "still uses the old file"):
                resolve_ext.swap(self.controller(), "s1", other)
            with self.assertRaisesRegex(resolve_ext.CleanupError, "isn't in the Media Pool"):
                resolve_ext.swap(self.controller(), "gone", path)

    def test_add_goes_into_the_originals_bin_and_shows_it(self):
        # Put away in a bin that wasn't showing, it was missed (2026-10-07): that bin's shown now,
        # the new clip selected in it.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "A001_clean_38-93.mov")
            Path(path).write_bytes(b"x")
            got = resolve_ext.add(self.controller(), "s1", path)
            self.assertEqual(got, {"name": "A001_clean_38-93.mov", "bin": "Stills"})
            self.assertEqual(self.pool.imported, [path])
            self.assertIs(self.pool.imported_into, self.bin)
            self.assertIs(self.pool.current, self.bin)
            self.assertEqual(self.pool.picked.uid, "new")
            # The original gone from the pool: the bin that's showing.
            got = resolve_ext.add(self.controller(), "gone", path)
            self.assertEqual(got["bin"], "Stills")
            with self.assertRaisesRegex(resolve_ext.CleanupError, "isn't there any more"):
                resolve_ext.add(self.controller(), "s1", os.path.join(tmp, "missing.mov"))


# ------------------------------------------------------------------- engine --

ENGINE = es.venv_python().exists() and es.model_path("lama").exists()
MATTE = ENGINE and es.model_path("birefnet-lite").exists()


class PageTests(unittest.TestCase):
    def test_no_method_is_hidden_by_an_attribute_the_web_page_sets(self):
        # WebSurface's self._ready = False once hid a _ready() here: Remove
        # painted called a bool and did nothing.
        import inspect
        import re

        from pages.generative.page import GenerativePage
        own = {n for n, v in vars(GenerativePage).items() if callable(v)}
        for base in GenerativePage.__mro__[1:]:
            if base.__module__.startswith("core."):
                sets = set(re.findall(r"\bself\.(\w+)\s*=(?!=)", inspect.getsource(base)))
                self.assertFalse(own & sets, f"{base.__name__} sets these as attributes")


try:
    import cv2  # noqa: F401
    from pages.generative import matte
except ImportError:
    matte = None


@unittest.skipUnless(matte, "OpenCV isn't installed for this Python")
class ShapeTests(unittest.TestCase):
    """Rectangles and ellipses drawn as the view draws them."""

    def test_a_rectangle_covers_the_pixels_whose_centres_are_inside_whichever_corner_comes_first(self):
        for pts in ([10.2, 20, 30, 40.7], [30, 40.7, 10.2, 20]):
            paint = np.zeros((60, 60), np.uint8)
            self.assertTrue(matte.draw_stroke(paint, {"shape": "rect", "points": pts}, 255))
            want = np.zeros((60, 60), np.uint8)
            want[20:41, 10:30] = 255
            self.assertTrue(np.array_equal(paint, want))

    def test_an_ellipse_fills_its_box(self):
        paint = np.zeros((200, 300), np.uint8)
        matte.draw_stroke(paint, {"shape": "ellipse", "points": [50, 40, 250, 160]}, 255)
        ys, xs = np.nonzero(paint)
        self.assertAlmostEqual((paint > 0).sum() / (np.pi * 100 * 60), 1, delta=0.03)
        self.assertAlmostEqual(xs.mean(), 149.5, delta=0.5)
        self.assertAlmostEqual(ys.mean(), 99.5, delta=0.5)
        self.assertEqual((xs.min(), xs.max(), ys.min(), ys.max()), (50, 249, 40, 159))

    def test_a_turned_shape_is_its_box_turned_about_its_middle_clockwise(self):
        # As the view's canvas turns it (generative.js turnAbout): x' = cx + dx cos - dy sin, y' = cy + dx sin + dy cos.
        def turned(x, y, cx, cy, deg):
            a = np.radians(deg)
            return cx + (x - cx) * np.cos(a) - (y - cy) * np.sin(a), cy + (x - cx) * np.sin(a) + (y - cy) * np.cos(a)

        yy, xx = np.mgrid[0:200, 0:300].astype(np.float64)
        for kind in ("rect", "ellipse"):
            for deg in (30, 90, -45):
                with self.subTest(kind=kind, angle=deg):
                    paint = np.zeros((200, 300), np.uint8)
                    self.assertTrue(matte.draw_stroke(paint, {"shape": kind, "points": [70, 70, 230, 130],
                                                              "angle": deg}, 255))
                    # Each pixel's centre turned back into the box's own frame: inside it, or not.
                    bx, by = turned(xx + 0.5, yy + 0.5, 150, 100, -deg)
                    if kind == "rect":
                        want = (bx >= 70) & (bx <= 230) & (by >= 70) & (by <= 130)
                    else:
                        want = ((bx - 150) / 80) ** 2 + ((by - 100) / 30) ** 2 <= 1
                    got = paint > 0
                    self.assertGreater((got & want).sum() / (got | want).sum(), 0.97)
                    # Clockwise: turned 90°, the long side stands up.
                    if deg == 90:
                        ys, xs = np.nonzero(got)
                        self.assertGreater(np.ptp(ys), 2 * np.ptp(xs))
        # Not turned: just as before.
        plain, zero = np.zeros((60, 60), np.uint8), np.zeros((60, 60), np.uint8)
        matte.draw_stroke(plain, {"shape": "rect", "points": [10, 10, 40, 30]}, 255)
        matte.draw_stroke(zero, {"shape": "rect", "points": [10, 10, 40, 30], "angle": 0}, 255)
        self.assertTrue(np.array_equal(plain, zero))

    def test_a_click_draws_nothing(self):
        paint = np.zeros((20, 20), np.uint8)
        for kind in ("rect", "ellipse"):
            self.assertFalse(matte.draw_stroke(paint, {"shape": kind, "points": [5, 5, 5, 5]}, 255))
        self.assertFalse(paint.any())

    def test_a_feathered_rectangle_is_soft_by_its_own_size_and_a_brush_by_its_own(self):
        rect = {"shape": "rect", "points": [20, 20, 80, 60], "feather": 100, "keep": True}
        self.assertEqual(matte.stroke_radius(rect), 20)
        self.assertEqual(matte.stroke_radius({"size": 30}), 15)
        out = matte.apply_strokes(np.zeros((80, 100), np.float32), [rect])
        self.assertGreater(float(out[40, 50]), 0.95)                     # its middle: all subject
        self.assertTrue(0.3 < float(out[40, 20]) < 0.7)                  # its edge: half
        self.assertGreater(float(out[40, 14]), 0.05)                     # fading out past it

    def test_removes_mask_takes_shapes_and_erasers(self):
        import importlib
        worker = importlib.import_module("pages.generative.worker")
        mask = worker.mask_from([
            {"shape": "rect", "points": [0, 0, 50, 50]},
            {"shape": "ellipse", "erase": True, "points": [10, 10, 40, 40]},
            {"size": 4, "points": [70, 70, 90, 70]},
        ], (100, 100))
        self.assertTrue(mask[2, 2] and not mask[25, 25] and mask[70, 80])
        self.assertFalse(mask[2, 60])

    def test_a_lasso_fills_its_outline_closed_back_to_where_it_began(self):
        paint = np.zeros((100, 100), np.uint8)
        # An L, its last point not back at its first.
        lasso = {"shape": "lasso", "points": [10, 10, 60, 10, 60, 40, 30, 40, 30, 90, 10, 90]}
        self.assertTrue(matte.draw_stroke(paint, lasso, 255))
        self.assertTrue(paint[20, 20] and paint[80, 20] and paint[20, 50])
        self.assertFalse(paint[70, 50])                                      # the L's corner is outside
        self.assertEqual(matte.stroke_radius(lasso), 25)                     # half its box's shorter side
        self.assertFalse(matte.draw_stroke(paint, {"shape": "lasso", "points": [5, 5, 50, 50]}, 255))

    def test_finding_the_subject_inside_an_outline_looks_only_there(self):
        import importlib
        worker = importlib.import_module("pages.generative.worker")
        engine = worker.Engine("", tempfile.mkdtemp(), lambda _msg: None)
        engine.img = np.full((300, 400, 3), 120, np.uint8)
        engine.img[50:250, 250:350] = 200                                   # something on the right
        seen = []

        class Model:
            def find(self, part):                                         # says the whole of what it's shown
                seen.append(part.shape)
                return np.ones(part.shape[:2], np.float32)

        engine._model = lambda _mid: Model()
        engine.loaded["birefnet-lite"] = True
        region = [{"shape": "rect", "points": [240, 40, 360, 260]}]
        engine.do_matte(1, "birefnet-lite", region)
        self.assertEqual(seen, [(220 + 2 * 28, 120 + 2 * 28, 3)])         # the box with an eighth of 220 round it
        self.assertEqual(float(engine.matte[150, 300]), 1.0)
        self.assertEqual(float(engine.matte[150, 100]), 0.0)                # outside what was drawn: never kept
        self.assertEqual(float(engine.matte[150, 237]), 0.0)                # nor just round it
        with self.assertRaises(worker.Fail):
            engine.do_matte(2, "birefnet-lite", [{"shape": "rect", "points": [5, 5, 5, 5]}])

    def test_an_outline_used_as_it_is_is_the_cut_out(self):
        import importlib
        worker = importlib.import_module("pages.generative.worker")
        engine = worker.Engine("", tempfile.mkdtemp(), lambda _msg: None)
        engine.img = np.full((200, 200, 3), 90, np.uint8)
        result = engine.do_outline(1, [{"shape": "ellipse", "points": [50, 50, 150, 150], "feather": 40},
                                       {"shape": "rect", "erase": True, "points": [90, 90, 110, 110]}])
        self.assertEqual(result["model"], "outline")
        self.assertGreater(float(engine.matte[70, 100]), 0.99)
        self.assertLess(float(engine.matte[100, 100]), 0.01)                # erased out of the middle
        self.assertTrue(0.3 < float(engine.matte[100, 50]) < 0.7)          # feathered at its edge
        self.assertEqual(float(engine.matte[5, 5]), 0.0)
        engine.close_files()

    def test_removes_mask_is_as_soft_as_its_feather(self):
        # A removal takes all of what's inside a feathered shape and blends out past it: half in, half out
        # left a ghost of what was painted over (a 290 px ellipse at 100%: a quarter of it replaced all the way).
        import importlib
        worker = importlib.import_module("pages.generative.worker")
        ellipse = {"shape": "ellipse", "points": [70, 70, 230, 230]}         # radius 80 round (150, 150)
        hard = worker.mask_from([ellipse], (300, 300))
        self.assertEqual(set(np.unique(hard)), {0.0, 1.0})
        soft = worker.mask_from([dict(ellipse, feather=100)], (300, 300))
        self.assertTrue((soft[hard > 0] > 0.999).all())                      # all of it, all the way
        self.assertAlmostEqual(float(soft[150, 270]), 0.5, delta=0.08)       # halfway out its fade (2 x 40 px)
        self.assertTrue(0.0 < float(soft[150, 299]) < 0.1)                   # nearly gone at its end
        # A drawn outline (Separate's) keeps its edge half in, half out.
        centred = worker.mask_from([dict(ellipse, feather=100)], (300, 300), outward=False)
        self.assertAlmostEqual(float(centred[150, 230]), 0.5, delta=0.08)
        self.assertLess(float(centred[150, 200]), 0.99)

    def test_a_deep_picture_is_shown_without_bands_or_a_darker_cast(self):
        # Cut to 8 bits (>> 8) a soft 10-bit gradient showed bands and came out ~0.5/255 dark (2026-10-08).
        import importlib
        worker = importlib.import_module("pages.generative.worker")
        ramp = np.repeat(np.tile(np.linspace(4000, 6000, 640), (64, 1))[:, :, None], 3, axis=2).astype(np.uint16)
        view = worker.to_view(ramp).astype(float)
        truth = ramp.astype(float) / 257
        self.assertLess(abs(float((view - truth).mean())), 0.05)                    # no cast
        self.assertLess(float(np.abs(view.mean(axis=0) - truth.mean(axis=0)).max()), 0.4)   # every column: no bands
        self.assertLessEqual(float(np.abs(view - truth).max()), 1.5)                # each pixel off a step at most
        edges = np.array([[0, 65535]], np.uint16)
        self.assertEqual(worker.to_view(edges)[0, :, 0].tolist(), [0, 255])         # black and white stay exact
        self.assertEqual(worker.to_view(np.zeros((4, 4, 3), np.uint8)).dtype, np.uint8)


@unittest.skipUnless(ENGINE, "Clean Up's engine isn't installed on this machine")
class EngineTests(unittest.TestCase):
    """worker.py in the engine's own Python, as Buddy runs it."""

    def run_worker(self, requests, work):
        lines = "".join(json.dumps(dict(r, id=i)) + "\n" for i, r in enumerate(requests, 1))
        out = subprocess.run([str(es.venv_python()), str(es.WORKER), "--models", str(es.MODELS_DIR), "--work", work],
                             input=lines, capture_output=True, text=True, timeout=300)
        replies = {}
        for line in out.stdout.splitlines():
            if line.startswith("@@BUDDY@@"):
                msg = json.loads(line[len("@@BUDDY@@"):])
                if "ok" in msg:
                    replies[msg["id"]] = msg
        return replies

    def read(self, path, code="img"):
        """A picture read in the engine's Python, as an .npy beside it."""
        npy = path + ".npy"
        subprocess.run([str(es.venv_python()), "-c",
                        "import sys, numpy as np, cv2; np.save(sys.argv[2], cv2.imread(sys.argv[1], cv2.IMREAD_UNCHANGED))",
                        path, npy], check=True, timeout=60)
        return np.load(npy)

    def test_only_the_painted_part_changes_at_16_bit_and_undo_puts_it_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            rng = np.random.default_rng(3)
            h, w = 600, 900
            yy, xx = np.mgrid[0:h, 0:w]
            img = np.stack([xx * 60, yy * 90, (xx + yy) * 30], axis=2).astype(np.uint16)
            img += rng.integers(0, 400, img.shape, dtype=np.uint16)
            img[250:350, 400:430] = [60000, 1000, 1000]            # a pole to remove
            src = os.path.join(tmp, "plate.png")
            np.save(src + ".src.npy", img)
            subprocess.run([str(es.venv_python()), "-c",
                            "import sys, numpy as np, cv2; cv2.imwrite(sys.argv[2], np.load(sys.argv[1]))",
                            src + ".src.npy", src], check=True, timeout=60)
            stroke = {"size": 50, "points": [415, 245, 415, 355]}
            r = self.run_worker([
                {"do": "open", "args": {"path": src}},
                {"do": "fill", "args": {"strokes": [stroke]}},
                {"do": "save", "args": {"path": os.path.join(tmp, "filled.png")}},
                {"do": "undo"},
                {"do": "save", "args": {"path": os.path.join(tmp, "undone.png")}},
                {"do": "fill", "args": {"strokes": []}},
                {"do": "open", "args": {"path": os.path.join(tmp, "nope.psd")}},
            ], os.path.join(tmp, "work"))
            self.assertEqual(r[1]["result"]["depth"], "16-bit")
            self.assertTrue(r[2]["ok"], r[2])
            x, y, bw, bh = r[2]["result"]["box"]
            filled = self.read(os.path.join(tmp, "filled.png"))
            self.assertEqual(filled.dtype, np.uint16)
            outside = np.ones((h, w), bool)
            outside[y:y + bh, x:x + bw] = False
            self.assertTrue(np.array_equal(filled[outside], img[outside]))
            # The pole (bright in the first channel) is gone from where it was.
            self.assertLess(int(filled[260:340, 405:425, 0].max()), 30000)
            self.assertTrue(np.array_equal(self.read(os.path.join(tmp, "undone.png")), img))
            self.assertEqual(r[6]["error"], "Paint over what to remove first.")
            self.assertIn("can't open .psd", r[7]["error"])

    def test_feather_blends_further_into_the_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            rng = np.random.default_rng(5)
            img = rng.integers(60, 200, (400, 400, 3), dtype=np.uint8)
            img[180:220, 150:250] = [250, 20, 20]
            src = os.path.join(tmp, "spot.png")
            np.save(src + ".npy", img)
            subprocess.run([str(es.venv_python()), "-c",
                            "import sys, numpy as np, cv2; cv2.imwrite(sys.argv[2], np.load(sys.argv[1]))",
                            src + ".npy", src], check=True, timeout=60)
            stroke = {"size": 60, "points": [150, 200, 250, 200]}
            r = self.run_worker([
                {"do": "open", "args": {"path": src}},
                {"do": "fill", "args": {"strokes": [stroke]}},
                {"do": "undo"},
                {"do": "fill", "args": {"strokes": [dict(stroke, feather=100)]}},
            ], os.path.join(tmp, "work"))
            hard, soft = r[2]["result"]["box"], r[4]["result"]["box"]
            self.assertGreater(soft[2], hard[2] + 10)     # wider: it fades out further
            self.assertGreater(soft[3], hard[3] + 10)

    def test_a_feathered_ellipse_fades_the_removal_into_the_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            rng = np.random.default_rng(11)
            h = w = 400
            img = rng.integers(60, 200, (h, w, 3), dtype=np.uint8)
            yy, xx = np.mgrid[0:h, 0:w]
            r = np.hypot(xx - 199.5, yy - 199.5)
            img[r < 80] = [250, 20, 20]                         # a disc to remove, bright in the first channel
            src = os.path.join(tmp, "disc.png")
            np.save(src + ".npy", img)
            subprocess.run([str(es.venv_python()), "-c",
                            "import sys, numpy as np, cv2; cv2.imwrite(sys.argv[2], np.load(sys.argv[1]))",
                            src + ".npy", src], check=True, timeout=60)
            ellipse = {"shape": "ellipse", "points": [120, 120, 280, 280]}
            r_ = self.run_worker([
                {"do": "open", "args": {"path": src}},
                {"do": "fill", "args": {"strokes": [ellipse]}},
                {"do": "save", "args": {"path": os.path.join(tmp, "hard.png")}},
                {"do": "undo"},
                {"do": "fill", "args": {"strokes": [dict(ellipse, feather=100)]}},
                {"do": "save", "args": {"path": os.path.join(tmp, "soft.png")}},
            ], os.path.join(tmp, "work"))
            self.assertTrue(r_[2]["ok"] and r_[5]["ok"], r_)
            hard, soft = self.read(os.path.join(tmp, "hard.png")), self.read(os.path.join(tmp, "soft.png"))
            # Feathered, all of what's inside still goes - the feather fades out past the ellipse (half in,
            # half out had left the disc's edge half there: a ghost of what was removed).
            near_edge = (r > 62) & (r < 76)                      # inside the disc, inside the ellipse
            self.assertLess(float(hard[near_edge][:, 0].mean()), 180)                       # hard: gone
            self.assertLess(float(soft[near_edge][:, 0].mean()), 180)                       # soft: gone too
            centre = r < 25
            self.assertLess(float(soft[centre][:, 0].mean()), 180)                          # gone in the middle
            past = (r > 86) & (r < 96)                           # the picture past the ellipse: blended into
            soft_diff = np.abs(soft[past].astype(int) - img[past].astype(int)).mean()
            hard_diff = np.abs(hard[past].astype(int) - img[past].astype(int)).mean()
            self.assertGreater(soft_diff, hard_diff + 2)
            self.assertGreater(r_[5]["result"]["box"][2], r_[2]["result"]["box"][2] + 20)

    def test_a_rectangle_removes_what_it_covers(self):
        with tempfile.TemporaryDirectory() as tmp:
            rng = np.random.default_rng(7)
            img = rng.integers(60, 200, (400, 400, 3), dtype=np.uint8)
            img[150:230, 120:280] = [250, 20, 20]
            src = os.path.join(tmp, "block.png")
            np.save(src + ".npy", img)
            subprocess.run([str(es.venv_python()), "-c",
                            "import sys, numpy as np, cv2; cv2.imwrite(sys.argv[2], np.load(sys.argv[1]))",
                            src + ".npy", src], check=True, timeout=60)
            r = self.run_worker([
                {"do": "open", "args": {"path": src}},
                {"do": "fill", "args": {"strokes": [{"shape": "rect", "points": [116, 146, 284, 234]}]}},
                {"do": "save", "args": {"path": os.path.join(tmp, "filled.png")}},
            ], os.path.join(tmp, "work"))
            self.assertTrue(r[2]["ok"], r[2])
            filled = self.read(os.path.join(tmp, "filled.png"))
            self.assertLess(int(filled[150:230, 120:280, 0].mean()), 200)   # the red block's gone
            x, y, bw, bh = r[2]["result"]["box"]
            self.assertLess(abs(x - 116) + abs(y - 146), 30)                # and only around it changed
            self.assertLess(abs(x + bw - 284) + abs(y + bh - 234), 30)

    @unittest.skipUnless(MATTE, "BiRefNet lite isn't downloaded on this machine")
    def test_cut_out_and_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A bright disc on a striped wall: an obvious subject.
            h, w = 512, 640
            yy, xx = np.mgrid[0:h, 0:w]
            img = np.where(((xx // 16) % 2)[:, :, None] == 0, 90, 130).astype(np.uint8).repeat(3, axis=2)
            disc = (xx - 320) ** 2 + (yy - 256) ** 2 < 140 ** 2
            img[disc] = [40, 160, 240]
            src = os.path.join(tmp, "disc.png")
            np.save(src + ".npy", img)
            subprocess.run([str(es.venv_python()), "-c",
                            "import sys, numpy as np, cv2; cv2.imwrite(sys.argv[2], np.load(sys.argv[1]))",
                            src + ".npy", src], check=True, timeout=60)
            r = self.run_worker([
                {"do": "open", "args": {"path": src}},
                {"do": "save_cutout", "args": {"path": os.path.join(tmp, "early.png")}},
                {"do": "matte", "args": {"model": "birefnet-lite"}},
                {"do": "save_cutout", "args": {"path": os.path.join(tmp, "disc_subject.jpg")}},
                {"do": "background"},
                {"do": "save_background", "args": {"path": os.path.join(tmp, "disc_background.png")}},
            ], os.path.join(tmp, "work"))
            self.assertEqual(r[2]["error"], "Find the subject first.")
            self.assertTrue(r[3]["ok"], r[3])
            self.assertTrue(0.1 < r[3]["result"]["coverage"] < 0.4, r[3]["result"])
            # Asked for a JPEG: saved as PNG, which keeps transparency.
            self.assertTrue(r[4]["result"]["path"].endswith("disc_subject.png"))
            cut = self.read(r[4]["result"]["path"])
            self.assertEqual(cut.shape, (h, w, 4))
            self.assertGreater(int(cut[256, 320, 3]), 240)          # the disc kept
            self.assertLess(int(cut[20, 20, 3]), 15)                # the wall gone
            self.assertTrue(r[5]["ok"], r[5])
            bg = self.read(r[6]["result"]["path"])
            # Where the disc was is wall-coloured now, not blue.
            self.assertLess(int(bg[256, 320, 0]), 170)
            self.assertEqual(bg.shape, img.shape)


if __name__ == "__main__":
    unittest.main()
