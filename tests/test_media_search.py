"""Media search (core/media_search): the index, a file cut into the pieces
that are embedded, the thread that fills the index, and what the model needs
on disk. No network, no Qt, nothing run: the model is stood in for by one
whose vectors say what colour a picture is or how high a sound."""

import hashlib
import io
import math
import os
import shutil
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import _paths  # noqa: F401
from core.media_search import extract, indexer, model
from core.media_search import store as st
from pages.manual_chat import embedder as emb
from pages.manual_chat import local_llama as ll


def vec(*head):
    """A vector of the index's size, pointing along its first few numbers."""
    return list(head) + [0.0] * (st.DIM - len(head))


def picture(path, colour, size=(64, 48)):
    from PIL import Image
    Image.new("RGB", size, colour).save(path)
    return str(path)


def jpeg(colour):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (64, 36), colour).save(out, "JPEG")
    return out.getvalue()


def tone(path, seconds=1.0, rate=8000, freq=440.0, level=9000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(level * math.sin(2 * math.pi * freq * i / rate)))
                               for i in range(int(rate * seconds))))
    return str(path)


class _Temp(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def file(self, name, data=b"x"):
        path = self.dir / name
        path.write_bytes(data)
        return str(path)


class StoreTests(_Temp):
    def setUp(self):
        super().setUp()
        self.store = st.Store(self.dir / "index" / "index.sqlite", model="m1")

    def test_nothing_is_written_until_the_index_is_used(self):
        self.assertFalse((self.dir / "index").exists())
        self.assertEqual(self.store.counts(), (0, 0))
        self.assertTrue((self.dir / "index" / "index.sqlite").exists())

    def test_the_nearest_file_comes_first_by_its_nearest_piece(self):
        beach, city = self.file("beach.mov"), self.file("city.mov")
        self.store.put(beach, "video", [st.Piece(st.KIND_FRAME, vec(1, 0), 0, 2), st.Piece(st.KIND_FRAME, vec(0, 1), 2, 2)])
        self.store.put(city, "video", [st.Piece(st.KIND_FRAME, vec(0.6, 0.8), 0, 2)])
        hits = self.store.search(vec(0, 1))
        self.assertEqual([(os.path.basename(h.path), h.start) for h in hits], [("beach.mov", 2.0), ("city.mov", 0.0)])
        self.assertAlmostEqual(hits[0].score, 1.0, places=3)
        self.assertEqual(self.store.counts(), (2, 3))

    def test_only_the_files_and_kinds_asked_for_are_found(self):
        still, sound = self.file("logo.png"), self.file("whoosh.wav")
        self.store.put(still, "image", [st.Piece(st.KIND_IMAGE, vec(1, 0))])
        self.store.put(sound, "audio", [st.Piece(st.KIND_SOUND, vec(1, 0), 0, 1)])
        self.assertEqual(len(self.store.search(vec(1, 0))), 2)
        self.assertEqual([h.kind for h in self.store.search(vec(1, 0), kinds=st.PICTURES)], [st.KIND_IMAGE])
        self.assertEqual([h.path for h in self.store.search(vec(1, 0), paths=[sound])], [st.key(sound)])
        self.assertEqual(self.store.search(vec(1, 0), paths=[]), [])

    def test_a_shot_is_found_once_but_a_clip_several_times(self):
        clip = self.file("A001.mov")
        self.store.put(clip, "video", [st.Piece(st.KIND_FRAME, vec(1, 0.0), 0, 2), st.Piece(st.KIND_FRAME, vec(1, 0.1), 2, 2),
                                       st.Piece(st.KIND_FRAME, vec(1, 0.2), 60, 2)])
        self.assertEqual([h.start for h in self.store.moments(vec(1, 0), apart=8)], [0.0, 60.0])

    def test_a_file_that_changed_or_another_models_is_new_again(self):
        clip = self.file("A001.mov")
        self.assertEqual(self.store.state([clip]), {st.key(clip): "new"})
        self.store.put(clip, "video", [st.Piece(st.KIND_FRAME, vec(1), 0, 2)])
        self.assertEqual(self.store.state([clip]), {st.key(clip): "done"})
        self.assertEqual(st.Store(self.store.path, model="m2").state([clip]), {st.key(clip): "new"})
        self.assertEqual(st.Store(self.store.path, model="m2").search(vec(1)), [])      # nor found with it
        Path(clip).write_bytes(b"longer now")
        self.assertEqual(self.store.state([clip]), {st.key(clip): "new"})
        self.assertEqual(self.store.state([self.dir / "gone.mov"]), {st.key(self.dir / "gone.mov"): "new"})

    def test_a_file_that_could_not_be_read_is_remembered_with_why(self):
        clip = self.file("broken.mov")
        self.store.put(clip, "video", [], problem="ffmpeg found no picture in it.")
        self.assertEqual(self.store.state([clip]), {st.key(clip): "failed"})
        self.assertEqual(self.store.problems([clip]), {st.key(clip): "ffmpeg found no picture in it."})
        self.assertEqual(self.store.search(vec(1)), [])

    def test_putting_a_file_again_replaces_what_was_there_and_forgetting_removes_it(self):
        clip = self.file("A001.mov")
        self.store.put(clip, "video", [st.Piece(st.KIND_FRAME, vec(1, 0), 0, 2)])
        self.assertEqual(self.store.search(vec(0, 1))[0].score, 0.0)
        self.store.put(clip, "video", [st.Piece(st.KIND_FRAME, vec(0, 1), 4, 2)])
        self.assertEqual([(h.start, round(h.score, 2)) for h in self.store.search(vec(0, 1))], [(4.0, 1.0)])
        self.assertEqual(self.store.counts(), (1, 1))
        self.store.forget([clip])
        self.assertEqual(self.store.counts(), (0, 0))
        self.assertEqual(self.store.search(vec(0, 1)), [])

    def test_an_index_in_a_folder_of_the_persons_keeps_no_write_ahead_log(self):
        own = st.Store(self.dir / "Job" / st.INDEX_NAME, model="m1", shared=True)
        own.put(self.file("A001.mov"), "video", [st.Piece(st.KIND_FRAME, vec(1), 0, 2)])
        self.assertEqual(sorted(p.name for p in (self.dir / "Job").iterdir()), [st.INDEX_NAME])     # one file, to copy whole
        self.assertEqual(len(own.search(vec(1))), 1)

    def test_entries_follow_their_files_to_another_drive_letter(self):
        # Indexed where the footage was (D:), then the drive turns up somewhere else.
        there, here = self.dir / "D" / "Footage", self.dir / "E" / "Footage"
        there.mkdir(parents=True)
        old = there / "A001_0001.mov"
        old.write_bytes(b"the footage")
        self.store.put(old, "video", [st.Piece(st.KIND_FRAME, vec(1, 0), 4, 2)])
        os.rename(self.dir / "D", self.dir / "E")
        new = here / "A001_0001.mov"
        self.assertEqual(self.store.state([new]), {st.key(new): "new"})
        self.assertEqual(self.store.adopt([new]), 1)
        self.assertEqual(self.store.state([new]), {st.key(new): "done"})
        self.assertEqual([(h.path, h.start) for h in self.store.search(vec(1, 0), paths=[new])], [(st.key(new), 4.0)])
        self.assertEqual(self.store.adopt([new]), 0)                    # once

    def test_only_the_same_file_is_taken_over_and_never_from_a_file_that_is_still_there(self):
        a, b = self.dir / "a", self.dir / "b"
        a.mkdir()
        b.mkdir()
        original = a / "A001_0001.mov"
        original.write_bytes(b"the footage")
        self.store.put(original, "video", [st.Piece(st.KIND_FRAME, vec(1), 0, 2)])
        copy = b / "A001_0001.mov"
        shutil.copy2(original, copy)
        self.assertEqual(self.store.adopt([copy]), 0)                   # the original is still where it was
        original.unlink()
        other = b / "A001_0002.mov"
        other.write_bytes(b"the footage")
        longer = self.dir / "A001_0001.mov"
        longer.write_bytes(b"another take, longer")
        self.assertEqual(self.store.adopt([other, longer]), 0)          # another name; another size
        self.assertEqual(self.store.adopt([copy]), 1)

    def test_a_projects_entries_are_copied_to_another_index_not_made_again(self):
        mine, theirs, both = self.file("mine.mov"), self.file("theirs.mov"), self.file("both.mov")
        self.store.put(mine, "video", [st.Piece(st.KIND_FRAME, vec(1, 0), 0, 2), st.Piece(st.KIND_FRAME, vec(0, 1), 2, 2)])
        self.store.put(theirs, "video", [st.Piece(st.KIND_FRAME, vec(1, 0), 0, 2)])
        self.store.put(both, "video", [st.Piece(st.KIND_FRAME, vec(1, 0), 0, 2)])
        own = st.Store(self.dir / "Job" / st.INDEX_NAME, model="m1", shared=True)
        own.put(both, "video", [st.Piece(st.KIND_FRAME, vec(0, 1), 8, 2)])         # it has this one already
        self.assertEqual(own.take_from(self.store, [mine, both]), 1)
        self.assertEqual(own.counts(), (2, 3))                                      # mine's two pieces, both's own one
        self.assertEqual(own.state([mine, theirs]), {st.key(mine): "done", st.key(theirs): "new"})
        self.assertEqual([h.start for h in own.search(vec(0, 1), paths=[both])], [8.0])    # its own entry, kept
        self.assertEqual(self.store.counts(), (3, 4))                               # nothing taken out of the other

    def test_a_files_own_vector_finds_others_like_it(self):
        a, b, c = self.file("a.png"), self.file("b.png"), self.file("c.png")
        self.store.put(a, "image", [st.Piece(st.KIND_IMAGE, vec(1, 0))])
        self.store.put(b, "image", [st.Piece(st.KIND_IMAGE, vec(0.9, 0.1))])
        self.store.put(c, "image", [st.Piece(st.KIND_IMAGE, vec(0, 1))])
        like = self.store.vector_of(a)
        self.assertEqual([os.path.basename(h.path) for h in self.store.search(like, paths=[b, c])], ["b.png", "c.png"])
        self.assertIsNone(self.store.vector_of(self.dir / "never.png"))


class ExtractTests(_Temp):
    def test_what_each_file_is(self):
        self.assertEqual([extract.kind_of(n) for n in ("a.JPG", "b.mov", "c.wav", "d.braw", "notes.txt")],
                         [extract.IMAGE, extract.VIDEO, extract.AUDIO, None, None])

    def test_a_still_is_sent_small(self):
        from PIL import Image
        data = extract.picture(picture(self.dir / "big.png", "red", (1600, 900)))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual((image.format, max(image.size)), ("JPEG", model.PICTURE_SIDE))
        with self.assertRaises(extract.Unreadable):
            extract.picture(self.file("not-a-picture.png", b"nothing like one"))

    def test_a_long_clip_is_looked_at_further_apart(self):
        times, step = extract.frame_times(10)
        self.assertEqual((times, step), ([1.0, 3.0, 5.0, 7.0, 9.0], 2.0))
        times, step = extract.frame_times(3600)
        self.assertEqual((len(times), step), (extract.MAX_FRAMES, 12.0))
        self.assertEqual(extract.frame_times(0), ([0.0], 2.0))            # a length nobody knows: its first frame

    def test_frames_that_look_the_same_are_one_piece(self):
        # Ten seconds: red, red, red, blue, blue. No ffprobe here, so each frame is read exactly.
        shown = {1.0: "red", 3.0: "red", 5.0: "red", 7.0: "blue", 9.0: "blue"}
        with mock.patch.object(extract, "frame", lambda _ffmpeg, _path, at, side=0: jpeg(shown[at])):
            pieces = list(extract.frames("ffmpeg", "clip.mov", 10))
        # Each from the moment its frame is from, until the next picture or the end.
        self.assertEqual([(start, span) for start, span, _jpeg in pieces], [(1.0, 6.0), (7.0, 3.0)])

    def test_a_keyframe_stands_for_the_moments_after_it_from_where_it_really_is(self):
        # Keyframes at 0, 4.5 and 8 seconds; the picture changes at each.
        keys = [(0.0, "red"), (4.5, (0, 0, 255)), (8.0, "green")]

        def key_frame(_ffmpeg, _path, at, origin, side=0):
            when, colour = [k for k in keys if k[0] <= at][-1]
            return when, jpeg(colour)

        with mock.patch.object(extract, "picture_start", lambda *_a: 0.0), \
                mock.patch.object(extract, "key_frame", key_frame), \
                mock.patch.object(extract, "frame", lambda *_a, **_k: self.fail("no exact frame was needed")):
            pieces = list(extract.frames("ffmpeg", "clip.mp4", 10))
        self.assertEqual([(start, span) for start, span, _jpeg in pieces], [(0.0, 4.5), (4.5, 3.5), (8.0, 2.0)])

    def test_a_keyframe_too_far_back_gives_way_to_the_exact_frame(self):
        asked = []

        def exact(_ffmpeg, _path, at, side=0):
            asked.append(at)
            return jpeg("red")

        with mock.patch.object(extract, "picture_start", lambda *_a: 0.0), \
                mock.patch.object(extract, "key_frame", lambda *_a, **_k: (0.0, jpeg("red"))), \
                mock.patch.object(extract, "frame", exact):
            list(extract.frames("ffmpeg", "screen recording.mp4", 60))
        # One keyframe, at the start: it stands for the first ten seconds, not the minute.
        self.assertEqual(sorted(asked), [float(at) for at in range(11, 60, 2)])

    def test_the_keyframe_is_asked_for_where_the_files_picture_starts(self):
        ran = []

        def run(cmd, **_k):
            ran.append(cmd)
            return mock.Mock(returncode=0, stdout=jpeg("red"),
                             stderr=b"[Parsed_showinfo_0 @ 0000] n:   0 pts:  96096 pts_time:4.004 duration: 1001\n")

        with mock.patch.object(extract.subprocess, "run", run):
            when, data = extract.key_frame("ffmpeg", "A001_0001.MXF", 3.0, origin=1.001)
        self.assertAlmostEqual(when, 3.003)                         # by the clip's own clock, from its first frame
        cmd, = ran
        self.assertEqual(cmd[cmd.index("-ss") + 1], "4.001000")
        self.assertLess(cmd.index("-noaccurate_seek"), cmd.index("-i"))
        self.assertIn("-copyts", cmd)
        self.assertEqual(data[:2], b"\xff\xd8")

    def test_footage_needs_ffmpeg_and_says_when_it_has_no_picture(self):
        with self.assertRaises(extract.NeedsFfmpeg):
            list(extract.frames(None, "clip.mov", 10))
        with mock.patch.object(extract, "frame", lambda *_a, **_k: None), self.assertRaises(extract.Unreadable):
            list(extract.frames("ffmpeg", "clip.mov", 10))

    def test_sound_windows(self):
        self.assertEqual(extract.window_starts(12), [0.0])
        self.assertEqual(extract.window_starts(75), [0.0, 30.0, 60.0])
        starts = extract.window_starts(600)
        self.assertEqual((len(starts), starts[0], starts[-1]), (extract.MAX_WINDOWS, 0.0, 570.0))

    def test_a_plain_wav_is_read_without_ffmpeg_as_16k_mono(self):
        path = tone(self.dir / "beep.wav", seconds=1.0, rate=8000)
        (start, span, data), = extract.sounds(None, path)
        with wave.open(io.BytesIO(data)) as w:
            self.assertEqual((w.getnchannels(), w.getframerate(), w.getnframes()), (1, model.SOUND_RATE, model.SOUND_RATE))
        self.assertEqual((start, span), (0.0, 1.0))

    def test_silence_is_left_out_and_other_sound_needs_ffmpeg(self):
        self.assertEqual(list(extract.sounds(None, tone(self.dir / "silent.wav", level=0))), [])
        with self.assertRaises(extract.NeedsFfmpeg):
            list(extract.sounds(None, self.file("song.mp3")))


class _FakeModel:
    """Vectors that say what was shown: a picture's average colour, a
    sound's length; a description's by the colour it names."""

    SearchError = model.SearchError

    def __init__(self):
        self.fail = None
        self.pictures = 0

    def embed_pictures(self, jpegs):
        from PIL import Image
        if self.fail:
            raise model.SearchError(self.fail)
        self.pictures += len(jpegs)
        out = []
        for data in jpegs:
            with Image.open(io.BytesIO(data)) as image:
                r, g, b = image.convert("RGB").resize((1, 1)).getpixel((0, 0))
            out.append(vec(r / 255, g / 255, b / 255))
        return out

    def embed_sounds(self, wavs):
        return [vec(0, 0, 0, 1) for _ in wavs]


def wait(index, seconds=10):
    end = time.time() + seconds
    while index.status()["running"] and time.time() < end:
        time.sleep(0.01)
    return index.status()


class IndexerTests(_Temp):
    def setUp(self):
        super().setUp()
        self.model = _FakeModel()
        self.store = st.Store(self.dir / "index.sqlite", model="fake")
        self.index = indexer.Indexer(self.store, self.model, ffmpeg=lambda: None)
        self.addCleanup(self.index.stop, 2)

    def test_stills_and_wavs_go_in_and_are_found(self):
        red, blue = picture(self.dir / "red.png", "red"), picture(self.dir / "blue.png", (0, 0, 255))
        beep = tone(self.dir / "beep.wav")
        heard = []
        self.index.listen(heard.append)
        self.assertEqual(self.index.add([red, blue, beep, self.file("notes.txt")]), 3)        # a text file isn't media
        status = wait(self.index)
        self.assertEqual((status["done"], status["total"], status["failed"], status["error"]), (3, 3, 0, ""))
        self.assertTrue(heard and heard[-1]["running"] is False)
        self.assertEqual(os.path.basename(self.store.search(vec(0, 0, 1))[0].path), "blue.png")
        self.assertEqual(self.store.search(vec(0, 0, 0, 1))[0].kind, st.KIND_SOUND)

    def test_what_is_already_in_is_passed_over(self):
        red = picture(self.dir / "red.png", "red")
        self.index.add([red])
        wait(self.index)
        self.assertEqual(self.index.add([red]), 0)
        self.assertEqual(self.model.pictures, 1)
        picture(red, "green", (80, 80))                                 # changed: looked at again
        self.assertEqual(self.index.add([red]), 1)
        wait(self.index)
        self.assertEqual(self.model.pictures, 2)

    def test_a_file_that_cannot_be_read_is_noted_and_not_tried_again(self):
        bad = self.file("broken.png", b"not a picture")
        self.index.add([bad])
        self.assertEqual(wait(self.index)["failed"], 1)
        self.assertIn("Couldn't read the picture", self.store.problems([bad])[st.key(bad)])
        self.assertEqual(self.index.add([bad]), 0)

    def test_footage_waits_for_ffmpeg_rather_than_failing(self):
        clip = self.file("A001_0001.mov")
        self.index.add([clip])
        status = wait(self.index)
        self.assertEqual((status["needs_ffmpeg"], status["failed"]), (1, 0))
        self.assertEqual(self.store.state([clip]), {st.key(clip): "new"})       # tried again once there's an ffmpeg

    def test_footage_goes_in_frame_by_frame_with_where_each_is(self):
        clip = self.file("A001_0001.mov")
        shown = {1.0: "red", 3.0: "red", 5.0: (0, 0, 255)}
        index = indexer.Indexer(self.store, self.model, ffmpeg=lambda: "ffmpeg")
        with mock.patch.object(extract, "frame", lambda _f, _p, at, side=0: jpeg(shown[at])):
            index.add([clip], {clip: 6.0})
            wait(index)
        self.assertEqual(self.model.pictures, 2)                        # the second red frame isn't embedded again
        hit, = self.store.moments(vec(0, 0, 1), limit=1)
        self.assertEqual((hit.kind, hit.start, hit.span), (st.KIND_FRAME, 5.0, 1.0))

    def test_files_go_into_the_index_they_were_queued_for(self):
        red, blue = picture(self.dir / "red.png", "red"), picture(self.dir / "blue.png", (0, 0, 255))
        own = st.Store(self.dir / "Job" / st.INDEX_NAME, model="fake", shared=True)
        self.index.add([red])
        self.index.add([blue, red], store=own)                         # red wanted there too
        wait(self.index)
        self.assertEqual(self.store.counts(), (1, 1))
        self.assertEqual(own.counts(), (2, 2))

    def test_an_index_that_cannot_be_written_stops_the_run_and_says_where(self):
        own = st.Store(self.dir / "Job" / st.INDEX_NAME, model="fake", shared=True)
        own.state([])                                                   # made
        with mock.patch.object(own, "put", side_effect=OSError("the drive is gone")):
            self.index.add([picture(self.dir / "red.png", "red")], store=own)
            status = wait(self.index)
        self.assertIn("Couldn't write the index in", status["error"])
        self.assertIn("the drive is gone", status["error"])

    def test_when_the_model_stops_the_queue_is_dropped_and_it_says_why(self):
        self.model.fail = "llama.cpp stopped answering."
        self.index.add([picture(self.dir / "a.png", "red"), picture(self.dir / "b.png", "red")])
        status = wait(self.index)
        self.assertEqual((status["error"], status["done"]), ("llama.cpp stopped answering.", 0))
        self.assertEqual(self.store.counts(), (0, 0))


class _LlamaRoot(_Temp):
    """local_llama's folders under a temp dir; no graphics card build for
    this machine unless a test says there is (card)."""

    def setUp(self):
        super().setUp()
        for name, value in {"ROOT": self.dir, "RUNTIME_DIR": self.dir / "llama.cpp" / ll.RELEASE,
                            "GPU_RUNTIME_DIR": self.dir / "llama.cpp" / (ll.RELEASE + "-vulkan"),
                            "MODELS_DIR": self.dir / "models", "LOG_PATH": self.dir / "llama.cpp" / "server.log",
                            "_VERIFIED": {}, "_LOADED": set(), "_GPU_DEVICE": {}, "_SERVERS": {},
                            "gpu_runtime_download": lambda: None}.items():
            patcher = mock.patch.object(ll, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in {"settings_path": lambda: str(self.dir / "media_search" / "settings.json"),
                            "_GPU_FAILED": [""]}.items():
            patcher = mock.patch.object(model, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def card(self, listing="  Vulkan0: NVIDIA GeForce RTX 5070 Ti (15995 MiB, 15227 MiB free)\n"):
        """A graphics card build for this machine, installed, on a PC whose devices are `listing`."""
        build = ll.Download("https://example.invalid/vulkan.zip", "vulkan.zip", 32_000_000, "0" * 64,
                            "llama.cpp for the graphics card")
        patcher = mock.patch.object(ll, "gpu_runtime_download", lambda: build)
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = ll.GPU_RUNTIME_DIR
        folder.mkdir(parents=True)
        exe = folder / ("llama-server.exe" if os.name == "nt" else "llama-server")
        exe.write_bytes(b"MZ")
        ll._GPU_DEVICE[str(exe)] = ll.pick_device(listing)      # what asking it (--list-devices) would say
        return build, exe

    def set_up(self):
        """The model's files and this release's llama.cpp, in place."""
        self.small_downloads()
        self.runtime(ll.RELEASE)
        ll.ensure_models_dir()
        for item in ll.MEDIA_SEARCH_FILES:
            (ll.MODELS_DIR / item.name).write_bytes(b"GGUF " + item.name.encode())

    def runtime(self, release):
        folder = self.dir / "llama.cpp" / release
        folder.mkdir(parents=True)
        exe = folder / ("llama-server.exe" if os.name == "nt" else "llama-server")
        exe.write_bytes(b"MZ")
        return exe

    def small_downloads(self):
        """The model's two files as a few bytes each, so they can be 'there'."""
        items = []
        for item in ll.MEDIA_SEARCH_FILES:
            data = b"GGUF " + item.name.encode()
            items.append((ll.Download(item.url, item.name, len(data), hashlib.sha256(data).hexdigest(), item.label), data))
        patcher = mock.patch.object(ll, "MEDIA_SEARCH_FILES", tuple(i for i, _d in items))
        patcher.start()
        self.addCleanup(patcher.stop)
        for (item, _data), name in zip(items, ("EMBEDDING_GEMMA_2", "EMBEDDING_GEMMA_2_MEDIA")):
            patcher = mock.patch.object(ll, name, item)
            patcher.start()
            self.addCleanup(patcher.stop)
        return items


class ModelTests(_LlamaRoot):
    def test_what_is_missing_until_everything_is_there(self):
        items = self.small_downloads()
        self.assertEqual([i.label for i in model.missing()], ["llama.cpp", *(i.label for i, _d in items)])
        self.assertEqual(model.ready(), (False, "Searching by description isn't set up yet."))
        self.runtime(ll.RELEASE)
        ll.ensure_models_dir()
        for item, data in items:
            (ll.MODELS_DIR / item.name).write_bytes(data)
        self.assertEqual(model.missing(), [])
        self.assertEqual(model.ready(), (True, ""))
        (ll.MODELS_DIR / items[1][0].name).write_bytes(b"cut short")           # half a download
        self.assertEqual([i.name for i in model.missing()], [items[1][0].name])

    def test_set_up_fetches_only_what_is_missing_and_checks_it(self):
        items = self.small_downloads()
        self.runtime(ll.RELEASE)
        ll.ensure_models_dir()
        (ll.MODELS_DIR / items[0][0].name).write_bytes(items[0][1])           # the model is here already
        served = {item.url: data for item, data in items}
        asked = []

        class Response(io.BytesIO):
            def __init__(self, data):
                super().__init__(data)
                self.headers = {"Content-Length": str(len(data))}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()

        def urlopen(request, timeout=0):
            asked.append(request.full_url)
            return Response(served[request.full_url])

        with mock.patch.object(ll.urllib.request, "urlopen", urlopen):
            model.install()
            self.assertEqual(asked, [items[1][0].url])                          # only its second half
            self.assertEqual(model.missing(), [])
            model.install()
            self.assertEqual(len(asked), 1)                                     # nothing left to fetch
        served[items[1][0].url] = b"something else entirely"
        (ll.MODELS_DIR / items[1][0].name).unlink()
        with mock.patch.object(ll.urllib.request, "urlopen", urlopen), self.assertRaises(ll.LlamaError):
            model.install()                                                     # not the pinned file: refused
        self.assertFalse((ll.MODELS_DIR / items[1][0].name).exists())

    def test_an_older_llama_cpp_runs_the_manuals_model_but_not_this_one(self):
        self.small_downloads()
        old = self.runtime("b11312")
        self.assertEqual(ll.server_exe(), old)                  # Ask Buddy's search carries on with it
        self.assertFalse(ll.runtime_current())
        self.assertEqual(ll.runtime_dir().name, "b11312")
        self.assertIn("llama.cpp", [i.label for i in model.missing()])
        new = self.runtime(ll.RELEASE)
        self.assertEqual(ll.server_exe(), new)
        self.assertTrue(ll.runtime_current())

    def test_installing_this_release_removes_the_older_one(self):
        import zipfile
        self.runtime("b11312")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("llama-server.exe" if os.name == "nt" else "llama-server", b"MZ")
        data = buf.getvalue()
        item = ll.Download("https://example.invalid/llama.zip", "llama.zip", len(data), hashlib.sha256(data).hexdigest(), "llama.cpp")

        class Response(io.BytesIO):
            headers = {"Content-Length": str(len(data))}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()

        with mock.patch.object(ll, "runtime_download", lambda: item), \
                mock.patch.object(ll.urllib.request, "urlopen", lambda *_a, **_k: Response(data)), \
                mock.patch.object(ll, "defender_scan", lambda _folder: (ll.SCAN_SKIPPED, "")):
            exe, _note = ll.install_runtime()
        self.assertEqual(exe.parent.name, ll.RELEASE)
        self.assertFalse((self.dir / "llama.cpp" / "b11312").exists())

    def test_the_manuals_search_is_never_guessed_to_use_this_model(self):
        ll.ensure_models_dir()
        (ll.MODELS_DIR / ll.EMBEDDING_GEMMA_2.name).write_bytes(b"GGUF")
        (ll.MODELS_DIR / ll.EMBEDDING_GEMMA_2_MEDIA.name).write_bytes(b"GGUF")
        self.assertIsNone(emb.chosen_model_file({}))
        (ll.MODELS_DIR / ll.EMBEDDING_GEMMA.name).write_bytes(b"GGUF")
        self.assertEqual(emb.chosen_model_file({}).name, ll.EMBEDDING_GEMMA.name)
        picked = emb.BuddyEmbedder(ll.MODELS_DIR / ll.EMBEDDING_GEMMA_2.name)      # picked by hand: theirs to pick...
        self.assertNotEqual(picked.model_name, "embeddinggemma")                   # ... but not called the manual's model
        self.assertEqual({p.name for p in ll.own_models(ll.MODELS_DIR, False)},        # a change of folder takes all three
                         {ll.EMBEDDING_GEMMA.name, ll.EMBEDDING_GEMMA_2.name, ll.EMBEDDING_GEMMA_2_MEDIA.name})

    def test_the_pinned_files_are_checked_against_sha256(self):
        for item in ll.MEDIA_SEARCH_FILES:
            self.assertRegex(item.sha256, r"^[0-9a-f]{64}$")
            self.assertRegex(item.url, r"^https://huggingface\.co/ggml-org/[^/]+/resolve/[0-9a-f]{40}/")   # one revision

    def test_pictures_and_sound_go_to_one_server_beside_its_second_half(self):
        self.small_downloads()
        self.runtime(ll.RELEASE)
        ll.ensure_models_dir()
        for item in ll.MEDIA_SEARCH_FILES:
            (ll.MODELS_DIR / item.name).write_bytes(b"GGUF " + item.name.encode())
        asked = []

        class Server:
            def post(self, path, body, timeout):
                asked.append((path, body))
                return {"data": [{"index": i, "embedding": [float(i)]} for i in reversed(range(len(body["input"])))]}

        with mock.patch.object(ll, "server_for",
                               lambda model_path, mmproj=None, gpu=False: asked.append((model_path.name, mmproj.name)) or Server()):
            self.assertEqual(model.embed_texts(["a  red\nkite"]), [[0.0]])
            self.assertEqual(model.embed_pictures([b"jpeg"] * 5), [[0.0], [1.0], [2.0], [3.0], [0.0]])     # four a request
            model.embed_sounds([b"wav"])
        self.assertEqual(asked[0], (ll.EMBEDDING_GEMMA_2.name, ll.EMBEDDING_GEMMA_2_MEDIA.name))
        self.assertEqual(asked[1], ("/embeddings", {"input": ["task: search result | query: a red kite"]}))
        part = asked[3][1]["input"][0]["content"][0]
        self.assertEqual((part["type"], part["image_url"]["url"]), ("image_url", "data:image/jpeg;base64,anBlZw=="))
        self.assertEqual(asked[-1][1]["input"][0]["content"][0]["input_audio"], {"data": "d2F2", "format": "wav"})

    # ------------------------------------------------- the graphics card --

    def test_which_device_is_a_card_of_its_own(self):
        both = ("Available devices:\n"
                "  Vulkan0: AMD Radeon(TM) Graphics (48794 MiB, 46354 MiB free)\n"
                "  Vulkan1: NVIDIA GeForce RTX 5070 Ti (15995 MiB, 15227 MiB free)\n")
        self.assertEqual(ll.pick_device(both), ("Vulkan1", "NVIDIA GeForce RTX 5070 Ti"))
        self.assertEqual(ll.pick_device("  Vulkan0: AMD Radeon RX 7900 XT (20000 MiB, 19000 MiB free)\n")[0], "Vulkan0")
        self.assertIsNone(ll.pick_device("  Vulkan0: Intel(R) UHD Graphics 770 (16000 MiB, 15000 MiB free)\n"))
        self.assertIsNone(ll.pick_device("Available devices:\n"))

    def test_the_card_is_an_extra_download_never_a_needed_one(self):
        self.set_up()
        self.assertEqual((model.ready(), model.extras(), model.gpu()), ((True, ""), [], ""))    # no build for this machine
        build = ll.Download("https://example.invalid/vulkan.zip", "vulkan.zip", 32_000_000, "0" * 64, "card")
        with mock.patch.object(ll, "gpu_runtime_download", lambda: build):
            self.assertEqual(model.extras(), [build])
            self.assertEqual(model.ready(), (True, ""))                 # it works without
            self.assertEqual(model.missing(), [])
            model.set_use_gpu(False)
            self.assertEqual(model.extras(), [])                        # switched off in Settings: not offered

    def test_it_runs_on_the_card_when_there_is_one(self):
        self.set_up()
        _build, exe = self.card()
        self.assertEqual((model.extras(), model.gpu()), ([], "NVIDIA GeForce RTX 5070 Ti"))
        with mock.patch.object(ll.subprocess, "Popen") as popen, \
                mock.patch.object(ll.LlamaServer, "_wait_ready", lambda *_a: None), mock.patch.object(ll, "_JOB", mock.Mock()), \
                mock.patch.object(ll.LlamaServer, "post", lambda s, path, body, timeout: s.ensure() and {"data": [
                    {"index": i, "embedding": [1.0]} for i in range(len(body["input"]))]}):
            popen.return_value.poll.return_value = None
            popen.return_value.pid = 1
            model.embed_texts(["a red kite"])
            args = popen.call_args[0][0]
            for server in ll._SERVERS.values():
                server.process = None
        self.assertEqual(args[0], str(exe))                             # the Vulkan build, not the CPU one
        self.assertEqual(args[args.index("--device") + 1], "Vulkan0")   # that card only
        self.assertEqual(args[args.index("-ngl") + 1], "99")
        model.set_use_gpu(False)
        self.assertEqual(model.gpu(), "")

    def test_only_the_processors_own_graphics_means_the_processor(self):
        self.set_up()
        self.card("  Vulkan0: AMD Radeon(TM) Graphics (48794 MiB, 46354 MiB free)\n")
        self.assertEqual(model.gpu(), "")

    def test_a_card_that_cannot_run_it_gives_way_to_the_processor_for_the_session(self):
        self.set_up()
        self.card()
        asked = []

        def post(server, path, body, timeout):
            asked.append("card" if "--device" in server.extra else "processor")
            if "--device" in server.extra:
                raise ll.LlamaError("llama.cpp stopped while loading – out of memory on the card")
            return {"data": [{"index": 0, "embedding": [1.0]}]}

        with mock.patch.object(ll.LlamaServer, "post", post), mock.patch.object(ll.LlamaServer, "stop", lambda s: None):
            self.assertEqual(model.embed_texts(["a red kite"]), [[1.0]])
            self.assertEqual(asked, ["card", "processor"])
            self.assertIn("out of memory", model.gpu_note())
            self.assertEqual(model.gpu(), "")
            model.embed_texts(["again"])
            self.assertEqual(asked, ["card", "processor", "processor"])     # not tried again this session
            model.set_use_gpu(True)                                         # asked for again in Settings: tried again
            self.assertEqual((model.gpu_note(), model.gpu()), ("", "NVIDIA GeForce RTX 5070 Ti"))

    def test_the_card_build_is_pinned_like_the_rest(self):
        for item in ll.GPU_RUNTIMES.values():
            self.assertRegex(item.sha256, r"^[0-9a-f]{64}$")
            self.assertIn(f"/download/{ll.RELEASE}/", item.url)             # the same release as the CPU build

    def test_llama_cpp_is_started_with_the_second_half(self):
        ll.ensure_models_dir()
        gguf, half = ll.MODELS_DIR / "embeddinggemma-2.gguf", ll.MODELS_DIR / "mmproj-embeddinggemma-2.gguf"
        gguf.write_bytes(b"GGUF")
        half.write_bytes(b"GGUF")
        exe = self.runtime(ll.RELEASE)
        with mock.patch.object(ll, "_SERVERS", {}), mock.patch.object(ll.subprocess, "Popen") as popen, \
                mock.patch.object(ll.LlamaServer, "_wait_ready", lambda *_a: None), mock.patch.object(ll, "_JOB", mock.Mock()):
            popen.return_value.poll.return_value = None
            popen.return_value.pid = 1
            server = ll.server_for(gguf, mmproj=half)
            self.assertIsNot(server, ll.server_for(gguf))                   # the text-only server is another
            self.assertIs(server, ll.server_for(gguf, mmproj=half))
            server.ensure()
            args = popen.call_args[0][0]
            server.process = None
        self.assertEqual(args[0], str(exe))
        self.assertEqual(args[args.index("--mmproj") + 1], str(half))
        self.assertIn("--embedding", args)
        self.assertIn("--offline", args)


if __name__ == "__main__":
    unittest.main()
