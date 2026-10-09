"""core/mov_atoms.py and core/media_source.py: a Sony camera file's 'chnl'
box, which Qt's FFmpeg stops at, is found at the end of the file and read
as 'free' through the player's device - so the clip plays as it is."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest

import _paths  # noqa: F401
from core import mov_atoms

try:
    from PySide6.QtCore import QIODevice
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtWidgets import QApplication
    from core import media_source
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


def application():
    """The suite's QApplication (a QCoreApplication here would stop the page
    tests after this module from making theirs)."""
    return QApplication.instance() or QApplication([])


def atom(kind, payload=b"", *children):
    body = payload + b"".join(children)
    return struct.pack(">I4s", 8 + len(body), kind) + body


def sony_like(mdat=b"\0" * 1000):
    """ftyp, mdat, then a moov whose sound track has an ipcm entry with a
    chnl box - the moov at the end, as the camera writes it."""
    ipcm = atom(b"ipcm", b"\0" * 28, atom(b"chnl", b"\0\0\0\0\x01\x02\0\0\0\0\0\0\0\0"), atom(b"pcmC", b"\0\0\0\0\0\x18"))
    stsd_sound = atom(b"stsd", b"\0\0\0\0\0\0\0\x01", ipcm)
    stsd_video = atom(b"stsd", b"\0\0\0\0\0\0\0\x01", atom(b"avc1", b"\0" * 78, atom(b"avcC", b"\x01")))
    track = lambda stsd: atom(b"trak", b"", atom(b"mdia", b"", atom(b"minf", b"", atom(b"stbl", b"", stsd))))
    moov = atom(b"moov", b"", atom(b"mvhd", b"\0" * 100), track(stsd_video), track(stsd_sound))
    return atom(b"ftyp", b"XAVC\0\0\0\0XAVCmp42iso6") + atom(b"mdat", mdat) + moov


class _Files(unittest.TestCase):
    def setUp(self):
        mov_atoms._cache.clear()

    def write(self, data, name="clip.MP4"):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path


class AtomTests(_Files):
    def test_the_chnl_box_of_a_sound_entry_is_found_at_the_files_end(self):
        data = sony_like()
        path = self.write(data)
        spots = mov_atoms.unreadable_spots(path)
        self.assertEqual(len(spots), 1)
        self.assertEqual(data[spots[0]:spots[0] + 4], b"chnl")
        self.assertGreater(spots[0], 1000)                      # in the moov, after the mdat
        self.assertEqual(mov_atoms.unreadable_spots(path), spots)   # remembered

    def test_a_file_qt_reads_whole_has_no_spots(self):
        plain = atom(b"ftyp", b"isom") + atom(b"moov", b"", atom(b"trak", b"", atom(b"mdia", b"", atom(b"minf", b"",
                     atom(b"stbl", b"", atom(b"stsd", b"\0" * 8, atom(b"mp4a", b"\0" * 28, atom(b"esds", b"\0"))))))))
        self.assertEqual(mov_atoms.unreadable_spots(self.write(plain)), [])
        self.assertEqual(mov_atoms.unreadable_spots(self.write(b"not a movie at all", "x.mp4")), [])
        self.assertEqual(mov_atoms.unreadable_spots(self.write(sony_like(), "clip.wav")), [])   # not MP4/MOV
        self.assertEqual(mov_atoms.unreadable_spots(os.path.join(tempfile.gettempdir(), "no-such.mp4")), [])

    def test_the_spot_reads_as_free_whatever_read_it_falls_in(self):
        data = sony_like()
        spot = mov_atoms.unreadable_spots(self.write(data))[0]
        self.assertEqual(mov_atoms.patched(data, 0, [spot])[spot:spot + 4], b"free")
        across = mov_atoms.patched(data[spot - 2:spot + 2], spot - 2, [spot])    # a read ending inside it
        self.assertEqual(across, data[spot - 2:spot] + b"fr")
        after = mov_atoms.patched(data[spot + 3:spot + 8], spot + 3, [spot])      # one starting inside it
        self.assertEqual(after, b"e" + data[spot + 4:spot + 8])
        self.assertEqual(mov_atoms.patched(data[:spot], 0, [spot]), data[:spot])  # a read ending before it
        self.assertEqual(mov_atoms.patched(b"", spot, [spot]), b"")

    def test_a_truncated_or_odd_moov_never_raises(self):
        data = sony_like()
        self.assertIsInstance(mov_atoms.unreadable_spots(self.write(data[:-20])), list)     # moov cut short
        self.assertIsInstance(mov_atoms.unreadable_spots(self.write(data[:-200])), list)    # cut before the box
        self.assertEqual(mov_atoms.unreadable_spots(self.write(b"\xff\xff\xff\xff" + data[4:])), [])   # a size past the end
        self.assertEqual(mov_atoms.unreadable_spots(self.write(data[:12])), [])


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class DeviceTests(_Files):
    def test_the_player_reads_the_file_with_the_spot_as_free(self):
        data = sony_like()
        path = self.write(data)
        spot = mov_atoms.unreadable_spots(path)[0]
        device = media_source.PatchedFile(path, [spot])
        self.assertTrue(device.open(QIODevice.ReadOnly))
        self.assertFalse(device.isSequential())
        self.assertEqual(device.size(), len(data))
        self.assertEqual(bytes(device.read(12)), data[:12])
        self.assertTrue(device.seek(spot - 3))
        self.assertEqual(bytes(device.read(10)), data[spot - 3:spot] + b"free" + data[spot + 4:spot + 7])
        self.assertTrue(device.seek(0))
        whole = bytes(device.readAll())
        self.assertEqual(len(whole), len(data))
        self.assertEqual(whole[spot:spot + 4], b"free")
        self.assertNotIn(b"chnl", whole)
        device.close()

    def test_open_source_patches_only_what_needs_it(self):
        app = application()
        player = QMediaPlayer()
        self.assertTrue(media_source.open_source(player, self.write(sony_like())))
        self.assertIsInstance(player.findChild(QIODevice), media_source.PatchedFile)
        self.assertEqual(player.source().scheme(), "file")
        plain = QMediaPlayer()
        self.assertFalse(media_source.open_source(plain, self.write(b"RIFF", "a.wav")))
        self.assertIsNone(plain.findChild(QIODevice))
        self.assertTrue(plain.source().isLocalFile())
        del app  # noqa: F841 - the suite's, kept

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not on PATH")
    def test_a_real_pcm_in_mp4_file_opens_through_the_device(self):
        """FFmpeg writes 24-bit PCM into MP4 as Sony does: ipcm with chnl."""
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "pcm.mp4")
        run = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                              "-c:a", "pcm_s24be", path], capture_output=True, text=True)
        if run.returncode or not os.path.exists(path):
            self.skipTest(f"ffmpeg couldn't write PCM into MP4 here: {run.stderr.strip()[:120]}")
        spots = mov_atoms.unreadable_spots(path)
        with open(path, "rb") as handle:
            data = handle.read()
        if not spots:
            self.skipTest("this ffmpeg writes no chnl box")
        self.assertEqual(data[spots[0]:spots[0] + 4], b"chnl")
        app = application()
        player = QMediaPlayer()
        self.assertTrue(media_source.open_source(player, path))
        seen = []
        player.mediaStatusChanged.connect(lambda status: seen.append(status))
        for _ in range(200):
            app.processEvents()
            if any(s in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.InvalidMedia) for s in seen):
                break
            import time
            time.sleep(0.02)
        self.assertIn(QMediaPlayer.MediaStatus.LoadedMedia, seen, player.errorString())
        self.assertEqual(len(player.audioTracks()), 1)
        player.setSource(media_source.QUrl())
        del app  # noqa: F841 - the suite's, kept


if __name__ == "__main__":
    unittest.main()
