"""
Silences the FFmpeg that Qt Multimedia plays media with - and, on Windows,
keeps its GPU-decoded frames off the shared textures that froze Buddy
(cpu_frames()).

Qt's FFmpeg backend leaves FFmpeg's own logging at its default level, and
FFmpeg's default logger writes to stderr: every file a preview opens prints
its whole stream dump ("Input #0, mov,mp4,..." and a line per stream - nine
of them for a Sony MXF), plus warnings about perfectly playable camera files
("infe version < 2 is not implemented", "Missing key frame while searching
for timestamp"). In a console that is a screenful per clip, and none of it is
anything a user could act on - a file that really can't play still fails
through QMediaPlayer's own error.

The level is a process-wide setting inside the avutil library PySide6
ships, so setting it once through ctypes covers every player made after.
Qt only touches it when QT_FFMPEG_DEBUG is set, which is left alone here so
the logging can still be had when debugging playback.
"""

import ctypes
import glob
import os

AV_LOG_QUIET = -8

# Where PySide6's wheels keep avutil: beside the Qt DLLs on Windows, in
# Qt/lib on macOS and Linux.
_PATTERNS = ("avutil-*.dll", os.path.join("Qt", "lib", "libavutil*.dylib"),
             os.path.join("Qt", "lib", "libavutil.so*"))


def avutil_candidates(pyside_dir):
    """The avutil libraries in a PySide6 folder, newest version first."""
    found = []
    for pattern in _PATTERNS:
        found.extend(glob.glob(os.path.join(pyside_dir, pattern)))
    return sorted(set(found), reverse=True)


def cpu_frames(environ=os.environ):
    """Has Qt's FFmpeg hand each decoded frame over as an ordinary picture
    instead of a Direct3D texture shared with the screen. Decoding still runs
    on the GPU; only the frame's way to the screen changes.

    A shared texture is guarded by a keyed mutex that the drawing waits on.
    At a cut in Dailies (pages/dailies/tape_player.py) the finished clip's
    player is torn down while the screen still draws that clip's last frame,
    and the drawing waited on its mutex for good: Buddy froze with Qt Quick's
    render of the video surface in NtGdiDdDDIAcquireKeyedMutex2 (py-spy of the
    hung process, Qt 6.11.2, 2026-10-05). Without the shared texture there is
    no mutex to wait on.

    Must be set before the first player loads Qt's FFmpeg plugin. A value
    already in the environment is left alone (QT_DISABLE_HW_TEXTURES_CONVERSION=0
    turns the shared textures back on). Returns the value in force."""
    return environ.setdefault("QT_DISABLE_HW_TEXTURES_CONVERSION", "1")


def silence(environ=os.environ):
    """Turns FFmpeg's logging off. Returns the library it was set in, or None
    (no PySide6 FFmpeg to quiet, or QT_FFMPEG_DEBUG asked for the logging).
    Never raises: a noisy console is no reason to stop Buddy starting."""
    if environ.get("QT_FFMPEG_DEBUG"):
        return None
    try:
        import PySide6
        for path in avutil_candidates(os.path.dirname(PySide6.__file__)):
            try:
                library = ctypes.CDLL(path)
                library.av_log_set_level(ctypes.c_int(AV_LOG_QUIET))
                return path
            except (OSError, AttributeError):
                continue
    except Exception:
        pass
    return None
