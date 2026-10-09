#!/usr/bin/env python3
"""
The Zoom tab's player: the one selected clip, playing in Buddy - just the
stretch of its file the timeline uses (zoom_resolve.clip_info's "start",
"rate" and "duration"), with sound, looping at the clip's end. Asset
Manager's MediaPreview plays it (Qt Multimedia's FFmpeg backend reads camera
H.264 / HEVC that the web view can't) and hands each frame on as a JPEG the
page draws itself - so the page can zoom it exactly as Resolve will and put
the framing's handles over it. A still is its image, shown once.

An adjustment clip (kind "adjust") has no picture of its own: its "parts"
(zoom_resolve.under) are the clips under it, played in turn by _Sequence on
a clock of its own - each video part's file followed along (loaded at a cut
to another file, just moved within the same one), a still shown, black
where nothing is under it.

Times to and from the page are seconds from the clip's first frame.
"""

import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer

from pages.asset_manager.player import MediaPreview, image_data_url

FRAME_MAX = 1280        # longest side of a frame sent to the page: room to zoom in
TICK_MS = 40            # an adjustment clip's clock
DRIFT_S = 0.25          # a part's file this far off the clock is moved back to it
SETTLE_S = 0.6          # ... but not this soon after a move: the player's position lags a seek


class ClipPlayer(QObject):
    frame = Signal(str, str)            # clip id, data: URL
    time = Signal(str, float, bool)     # clip id, seconds into the clip, playing
    ready = Signal(str)                 # clip id: its first frame is up
    failed = Signal(str, str)           # clip id, why

    def __init__(self, parent=None):
        super().__init__(parent)
        self.media = MediaPreview(self, frame_max=FRAME_MAX)
        self.media.frame.connect(self._on_frame)
        self.media.ready.connect(self._on_ready)
        self.media.position.connect(self._on_position)
        self.media.playing.connect(self._on_playing)
        self.media.failed.connect(self._on_failed)
        self.clip = None
        self._loaded = False
        self._seeking = False
        self._seq = None            # _Sequence, for an adjustment clip

    @staticmethod
    def same(a, b):
        """The same clip, its file and stretch (an adjustment clip: what's
        under it) unchanged."""
        keys = ("id", "path", "start", "rate", "duration", "parts")
        return bool(a and b) and all(a.get(k) == b.get(k) for k in keys)

    def load(self, clip):
        """Shows `clip` (zoom_resolve.clip_info / adjust_info), unless it's
        the one showing."""
        if self.same(self.clip, clip):
            return
        self.stop()
        self.clip = dict(clip)
        if clip.get("kind") == "adjust":
            self._seq = _Sequence(self, self.clip)
            self._seq.start()
            return
        if clip.get("kind") == "image":
            image = QImage(clip.get("path") or "")
            if image.isNull():
                return self.failed.emit(clip["id"], "Buddy couldn't read this still.")
            self._loaded = True
            self.frame.emit(clip["id"], image_data_url(image, max_side=FRAME_MAX))
            self.ready.emit(clip["id"])
            return
        self.media.show(clip["id"], clip["path"], "Video")

    def stop(self):
        if self._seq is not None:
            self._seq.end()
            self._seq = None
        self.media.stop()
        self.clip = None
        self._loaded = False

    def _ms(self, t):
        c = self.clip
        return int(round((c["start"] + max(0.0, min(t, c["duration"])) * c["rate"]) * 1000))

    def _clip_time(self, position_ms):
        c = self.clip
        return (position_ms / 1000.0 - c["start"]) / (c["rate"] or 1.0)

    def seek(self, t):
        if self._seq is not None:
            return self._seq.seek(t)
        if self.clip is None or not self._loaded or self.clip["kind"] != "video":
            return
        self.media.seek_ms(self._ms(t))

    def toggle(self):
        if self._seq is not None:
            return self._seq.toggle()
        if self.clip is None or not self._loaded or self.clip["kind"] != "video":
            return
        player = self.media.player
        if player.playbackState() == QMediaPlayer.PlayingState:
            player.pause()
            return
        if self._clip_time(player.position()) >= self.clip["duration"] - 0.05:
            self.media.seek_ms(self._ms(0.0))
        player.setPlaybackRate(self.clip["rate"] if 0.1 <= self.clip["rate"] <= 4 else 1.0)
        player.play()

    def pause(self):
        if self._seq is not None:
            return self._seq.pause()
        self.media.pause()

    def shutdown(self):
        if self._seq is not None:
            self._seq.end()
        self.media.shutdown()

    # ------------------------------------------------------------- events --

    def _on_ready(self, clip_id, _kind):
        if self._seq is not None:
            return self._seq.media_ready(clip_id)
        if self.clip is None or clip_id != self.clip["id"]:
            return
        self._loaded = True
        self.media.seek_ms(self._ms(0.0))
        self.ready.emit(clip_id)

    def _on_frame(self, clip_id, url):
        if self._seq is not None:
            return self._seq.media_frame(clip_id, url)
        # Not before it's loaded: the first frame decoded is the FILE's first,
        # before the seek to where the clip starts in it.
        if self.clip is not None and clip_id == self.clip["id"] and self._loaded:
            self.frame.emit(clip_id, url)

    def _on_failed(self, clip_id, why):
        if self._seq is not None:
            return self._seq.media_failed(clip_id)
        self.failed.emit(clip_id, why)

    def _on_position(self, position_ms, _duration):
        if self._seq is not None:
            return                  # an adjustment clip's clock keeps its time
        if self.clip is None or not self._loaded or self.clip["kind"] != "video":
            return
        t = self._clip_time(position_ms)
        playing = self.media.player.playbackState() == QMediaPlayer.PlayingState
        if playing and t >= self.clip["duration"]:
            # The clip's end: round again from its start.
            self.media.seek_ms(self._ms(0.0))
            t = 0.0
        self.time.emit(self.clip["id"], max(0.0, min(t, self.clip["duration"])), playing)

    def _on_playing(self, playing):
        if self._seq is not None:
            return
        if self.clip is not None and self._loaded and self.clip["kind"] == "video":
            t = self._clip_time(self.media.player.position())
            self.time.emit(self.clip["id"], max(0.0, min(t, self.clip["duration"])), playing)


class _Sequence:
    """An adjustment clip's parts played in turn through the ClipPlayer's one
    MediaPreview. The time is the clock's; the file of the part under it
    follows along (moved back when it drifts past DRIFT_S)."""

    def __init__(self, owner, clip):
        self.owner, self.clip, self.media = owner, clip, owner.media
        self.parts = [p for p in clip.get("parts") or [] if p.get("to", 0) > p.get("from", 0)]
        self.t = 0.0
        self.playing = False
        self.since = (0.0, 0.0)     # (clock, t) when it last started or moved
        self.part = -2              # the part on show (-1: nothing under the clip; -2: none yet)
        self.path = None            # the file in the MediaPreview (kept, paused, over stills and gaps)
        self.loaded = False         # ... ready to play
        self.settle = 0.0           # no drift checks before this (monotonic)
        self.clock = QTimer(owner)
        self.clock.setInterval(TICK_MS)
        self.clock.setTimerType(Qt.PreciseTimer)
        self.clock.timeout.connect(self._tick)

    def start(self):
        self._enter(0.0)
        self.owner.ready.emit(self.clip["id"])
        self._say()

    def end(self):
        self.clock.stop()

    # ------------------------------------------------------------ control --

    def seek(self, t):
        self.t = max(0.0, min(float(t), self.clip["duration"]))
        self.since = (time.monotonic(), self.t)
        self._enter(self.t, moved=True)
        self._say()

    def toggle(self):
        if self.playing:
            return self.pause()
        if self.t >= self.clip["duration"] - 0.05:
            self.t = 0.0
            self._enter(0.0, moved=True)
        self.playing = True
        self.since = (time.monotonic(), self.t)
        self.clock.start()
        self._play_media()
        self._say()

    def pause(self):
        if not self.playing:
            return
        self.playing = False
        self.clock.stop()
        self.media.pause()
        self._say()

    # -------------------------------------------------------------- parts --

    def _index_at(self, t):
        for i, p in enumerate(self.parts):
            if p["from"] <= t < p["to"]:
                return i
        return -1

    def _ms(self, i, t):
        p = self.parts[i]
        return int(round((p["start"] + (t - p["from"]) * p["rate"]) * 1000))

    def _media_id(self, path):
        """The MediaPreview's id for a file: one per file, so parts of the
        same file share it and move without a reload."""
        return f'{self.clip["id"]}|{path}'

    def _enter(self, t, moved=False):
        """Shows what's under the clip at `t`: its part's file moved there
        (loaded first when it's another file), a still, or black."""
        i = self._index_at(t)
        if i == self.part and not moved:
            return
        changed, self.part = i != self.part, i
        p = self.parts[i] if i >= 0 else None
        if p is None or p["kind"] == "image":
            if changed:
                self.media.pause()      # the file stays loaded: back to it is just a move
                image = QImage(p["path"]) if p else QImage()
                self._show(image_data_url(image, max_side=FRAME_MAX) if not image.isNull() else _black(self.clip))
            return
        if self.path == p["path"]:
            if self.loaded:             # another stretch of the same file, or a seek: just move
                self._move(i, t)
                self._play_media()
            return                      # still loading: media_ready moves it to the clock's time
        self.path, self.loaded = p["path"], False
        self.media.show(self._media_id(p["path"]), p["path"], "Video")

    def _move(self, i, t):
        self.media.seek_ms(self._ms(i, t))
        self.settle = time.monotonic() + SETTLE_S

    def _video(self):
        """The part on show, when it's a video part."""
        return self.parts[self.part] if self.part >= 0 and self.parts[self.part]["kind"] == "video" else None

    def _play_media(self):
        if self.playing and self.loaded and self._video():
            rate = self._video()["rate"]
            self.media.player.setPlaybackRate(rate if 0.1 <= rate <= 4 else 1.0)
            self.media.player.play()

    def media_ready(self, media_id):
        if media_id != self._media_id(self.path):
            return
        self.loaded = True
        if self._video():
            self._move(self.part, self.t)
            self._play_media()

    def media_frame(self, media_id, url):
        if self.loaded and self._video() and media_id == self._media_id(self.path):
            self._show(url)

    def media_failed(self, media_id):
        if self.path is not None and media_id == self._media_id(self.path):
            self._show(_black(self.clip))   # this part won't play: the rest still do

    def _show(self, url):
        self.owner.frame.emit(self.clip["id"], url)

    def _say(self):
        self.owner.time.emit(self.clip["id"], self.t, self.playing)

    # -------------------------------------------------------------- clock --

    def _tick(self):
        now = time.monotonic()
        self.t = self.since[1] + (now - self.since[0])
        if self.t >= self.clip["duration"]:
            # The clip's end: round again from its start.
            self.t, self.since = 0.0, (now, 0.0)
            self._enter(0.0, moved=True)
        elif self._index_at(self.t) != self.part:
            self._enter(self.t)
        elif self.loaded and self._video() and now >= self.settle:
            p = self._video()
            at = p["from"] + (self.media.player.position() / 1000.0 - p["start"]) / (p["rate"] or 1.0)
            if abs(at - self.t) > DRIFT_S:
                self._move(self.part, self.t)
        self._say()


def _black(clip):
    """A black frame the clip's shape: nothing under the adjustment clip."""
    w, h = clip.get("width") or 16, clip.get("height") or 9
    k = 64 / max(w, h)
    image = QImage(max(1, round(w * k)), max(1, round(h * k)), QImage.Format_RGB32)
    image.fill(0)
    return image_data_url(image, max_side=64)
