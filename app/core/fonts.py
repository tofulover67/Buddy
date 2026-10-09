#!/usr/bin/env python3
"""Fonts, and font sizes, that come out the same on every platform Buddy
runs on."""

import sys


def ui_points(points):
    """A point size that renders at the same pixel size as `points` does on
    Windows. Buddy's sizes are defined there, where a point is 96/72 pixels
    (9.75pt = 13px); macOS counts 72 points per inch, so there the same
    number is a quarter smaller. (Qt ignores QT_FONT_DPI on macOS.)
    Pixel sizes need no help - they're pixels everywhere. Qt only: in the
    web pages a CSS pt is 96/72 px on every platform already."""
    return points * 96 / 72 if sys.platform == "darwin" else points

