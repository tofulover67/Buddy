#!/usr/bin/env python3
"""Animation > Titles' contact sheet: real hooks x every look, in a landscape and a
portrait frame, drawn by the titles renderer with the real fonts into one PNG. Look at
it after any change to the composer's scoring or a look - it's how the weights are tuned
and how a layout regression shows up at a glance.

    py -3 tools/titles_contact_sheet.py [out.png] [--look hormozi] [--terms]

--terms prints each cell's choice (composer.describe) as well.
"""

import argparse
import os
import sys

# Not the offscreen platform: it sees no system fonts (the labels need Arial).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen  # noqa: E402

from pages.text_animator.titles import composer, looks, measure, render  # noqa: E402

HOOKS = [
    "3 secrets nobody tells you",
    "STOP",
    "This changes everything",
    "You are doing it wrong",
    "The best way to learn to code",
    "Why you should never quit your job",
    "I tried this for 30 days",
    "Don't buy a camera before watching this",
    "The truth about passive income",
    "How I made $10,000 in 30 days with one simple trick nobody uses",
    "5 editing mistakes beginners make",
    "Nobody talks about this",
    "the secret to the perfect morning routine",
    "This is why you're always tired",
    "I wish I knew this at 20",
    "Watch this before your next interview",
    "99% of people get this wrong",
    "The only color grading tutorial you need",
    "Stop wasting money on gear",
    "Here's what happened when I quit social media for a year",
    "My biggest regret",
    "Free",
    "Is this the end of YouTube?",
    "Why does nobody use this feature in DaVinci Resolve",
    "Das solltest du niemals tun",
    "Nadie te cuenta esto",
    "Ce que personne ne vous dit",
    "Größter Fehler beim Schneiden",
    "İstanbul'da 3 gizli yer",
    "One word",
]

LANGS = {"İstanbul'da 3 gizli yer": "tr"}
CELL_H = 216            # each frame drawn this tall
GAP = 10
LABEL_W = 300


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out", nargs="?", default="titles_contact_sheet.png")
    parser.add_argument("--look", action="append", help="only these looks (repeatable)")
    parser.add_argument("--terms", action="store_true", help="print each cell's choice")
    args = parser.parse_args()
    QGuiApplication.instance() or QGuiApplication([])
    measure.load_bundled()
    ids = args.look or list(looks.LOOKS)
    frames = [(1920, 1080), (1080, 1920)]
    widths = [CELL_H * w / h for w, h in frames]
    col_w = sum(widths) + GAP * len(frames)
    sheet = QImage(int(LABEL_W + col_w * len(ids) + GAP), int(30 + (CELL_H + GAP) * len(HOOKS)),
                   QImage.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#0b0c10"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.Antialiasing)
    label = QFont("Arial")
    label.setPixelSize(13)
    p.setFont(label)
    p.setPen(QColor("#c9ccd6"))
    for i, look in enumerate(ids):
        p.drawText(QRectF(LABEL_W + i * col_w, 6, col_w, 20), Qt.AlignLeft, looks.get(look)["label"])
    for row, hook in enumerate(HOOKS):
        y = 30 + row * (CELL_H + GAP)
        p.setPen(QColor("#c9ccd6"))
        p.drawText(QRectF(8, y, LABEL_W - 16, CELL_H), Qt.TextWordWrap, hook)
        for i, look in enumerate(ids):
            x = LABEL_W + i * col_w
            for frame, w in zip(frames, widths):
                doc = composer.new_doc(hook, look)
                doc["lang"] = LANGS.get(hook, "")
                layout = composer.compose(doc, frame, measure.measure, measure.metrics, ink=measure.ink)
                if args.terms:
                    print(f"--- {hook!r} {frame}\n{composer.describe(layout)}")
                p.save()
                p.translate(x, y)
                p.setClipRect(QRectF(0, 0, w, CELL_H))
                render.background(p, w, CELL_H)
                scale = CELL_H / frame[1]
                p.scale(scale, scale)
                sx0, sy0, sx1, sy1 = layout["safe"]
                p.setPen(QPen(QColor(255, 255, 255, 40), 1 / scale))
                p.drawRect(QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0))
                render.draw(p, layout)
                p.restore()
                p.setPen(QColor("#7d8291"))
                small = QFont("Arial")
                small.setPixelSize(10)
                p.setFont(small)
                p.drawText(QRectF(x + 3, y + CELL_H - 14, w, 14), Qt.AlignLeft,
                           f"{len(layout['lines'])}L {layout['score']:.1f}")
                p.setFont(label)
                x += w + GAP
    p.end()
    sheet.save(args.out)
    print(f"Wrote {args.out} ({sheet.width()}x{sheet.height()})")


if __name__ == "__main__":
    main()
