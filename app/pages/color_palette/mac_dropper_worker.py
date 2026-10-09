#!/usr/bin/env python3
"""
Color Palette's screen dropper on macOS - a process of its own, started by
screen_dropper.py's ScreenColorDropper.

It snapshots every screen once, shows each snapshot full-screen with a
magnifier under the cursor, and prints the color clicked on. Esc or a
right-click cancels.

Why a separate process: the system's NSColorSampler can leave its
full-screen loupe up with no click reaching it and nothing able to close
it - a hard reboot is the only way out. A
full-screen window that can take the mouse away can't be allowed to
outlive a hang, so this one runs where the kernel can end it: SIGALRM is
armed below before anything else happens, and its default action ends the
process whatever Python or Qt is doing - taking its windows with it.
screen_dropper.py kills it from outside too, a little later.

Reading the screen needs Screen Recording, which macOS asks the user to
grant to DaVinci Resolve (the app this was started from). Without it the
snapshot would only show the desktop wallpaper, so it stops and says so
instead.

Output, one line on stdout: "@@DROPPER color #RRGGBB", "@@DROPPER cancel"
or "@@DROPPER no-permission".
"""

import ctypes
import ctypes.util
import os
import signal
import sys

HARD_LIMIT_S = 20   # the kernel ends this process after this, whatever happens
SOFT_LIMIT_MS = 15000   # the picker cancels itself after this
SELF_TEST = "--self-test" in sys.argv   # shows the overlay briefly, then cancels

signal.alarm(4 if SELF_TEST else HARD_LIMIT_S)

MARK = "@@DROPPER "
SAMPLE_SPAN = 15
ZOOM = 11
BAR_HEIGHT = 24


def emit(message):
    sys.stdout.write(MARK + message + "\n")
    sys.stdout.flush()


def screen_recording_allowed():
    """Whether this process may read the screen. When it may not, asks
    macOS to prompt for it (once - later requests do nothing) and says no:
    the grant only takes effect after Resolve restarts."""
    cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
    cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    if cg.CGPreflightScreenCaptureAccess():
        return True
    if not SELF_TEST:
        cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        cg.CGRequestScreenCaptureAccess()
    return False


def hex_at(image, x, y):
    """The snapshot's color at device pixel (x, y), or None off the image."""
    if not (0 <= x < image.width() and 0 <= y < image.height()):
        return None
    return image.pixelColor(x, y).name().upper()


def main():
    if not screen_recording_allowed() and not SELF_TEST:
        emit("no-permission")
        return

    from PySide6.QtCore import QPoint, QRect, Qt, QTimer
    from PySide6.QtGui import QColor, QColorSpace, QCursor, QFont, QPainter, QPen
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication(sys.argv[:1])
    done = []

    def finish(hex_color):
        if done:
            return
        done.append(True)
        emit(f"color {hex_color}" if hex_color else "cancel")
        for window in overlays:
            window.hide()
        app.quit()

    class Overlay(QWidget):
        def __init__(self, screen, image):
            # Not Qt.Tool: macOS hides a tool window whenever its app isn't
            # the active one.
            super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.image = image
            self.ratio = image.width() / max(1, screen.geometry().width())
            self.setGeometry(screen.geometry())
            self.setCursor(Qt.CrossCursor)
            self.setMouseTracking(True)
            self.cursor_pos = self.mapFromGlobal(QCursor.pos())

        def _device(self, pos):
            return int(pos.x() * self.ratio), int(pos.y() * self.ratio)

        def mouseMoveEvent(self, event):
            self.cursor_pos = event.position().toPoint()
            self.update()

        def mousePressEvent(self, event):
            if event.button() == Qt.LeftButton:
                finish(hex_at(self.image, *self._device(event.position().toPoint())))
            else:
                finish(None)

        def keyPressEvent(self, event):
            if event.key() == Qt.Key_Escape:
                finish(None)

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.drawImage(self.rect(), self.image)
            if not self.rect().contains(self.cursor_pos):
                return
            # The magnifier, same look as the Windows dropper's loupe.
            cx, cy = self._device(self.cursor_pos)
            half = SAMPLE_SPAN // 2
            zoom_px = SAMPLE_SPAN * ZOOM
            w, h = zoom_px + 2, zoom_px + BAR_HEIGHT + 2
            x, y = self.cursor_pos.x() + 24, self.cursor_pos.y() + 24
            if x + w > self.width():
                x = self.cursor_pos.x() - 24 - w
            if y + h > self.height():
                y = self.cursor_pos.y() - 24 - h
            painter.setPen(QPen(QColor("#f0f0f0"), 1))
            painter.setBrush(QColor("#1e1e1e"))
            painter.drawRect(x, y, w - 1, h - 1)
            painter.setPen(Qt.NoPen)
            for dy in range(-half, half + 1):
                for dx in range(-half, half + 1):
                    color = hex_at(self.image, cx + dx, cy + dy) or "#000000"
                    painter.setBrush(QColor(color))
                    painter.drawRect(x + 1 + (dx + half) * ZOOM, y + 1 + (dy + half) * ZOOM, ZOOM, ZOOM)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.drawRect(x + 1 + half * ZOOM, y + 1 + half * ZOOM, ZOOM, ZOOM)
            sampled = hex_at(self.image, cx, cy) or "#000000"
            bar = QRect(x + 1, y + 1 + zoom_px, w - 2, BAR_HEIGHT)
            painter.fillRect(bar, QColor(sampled))
            light = QColor(sampled).lightnessF() > 0.5
            painter.setPen(QColor("#000000" if light else "#ffffff"))
            font = QFont()
            font.setBold(True)
            font.setPixelSize(13)
            painter.setFont(font)
            painter.drawText(bar, Qt.AlignCenter, sampled)

    # Every snapshot first, before any overlay exists to show up in one.
    # In sRGB, the palette's color space, when the display reports its own.
    snapshots = []
    for screen in app.screens():
        image = screen.grabWindow(0).toImage()
        if image.colorSpace().isValid() and image.colorSpace() != QColorSpace(QColorSpace.SRgb):
            image.convertToColorSpace(QColorSpace(QColorSpace.SRgb))
        snapshots.append((screen, image))

    overlays = [Overlay(screen, image) for screen, image in snapshots]
    for window in overlays:
        window.show()
        window.raise_()
    under_cursor = next((w for w in overlays if w.geometry().contains(QCursor.pos())), overlays[0])
    under_cursor.activateWindow()

    QTimer.singleShot(1500 if SELF_TEST else SOFT_LIMIT_MS, lambda: finish(None))
    app.exec()


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.stdout.flush()
        os._exit(0)   # nothing left for Qt teardown to hang on
