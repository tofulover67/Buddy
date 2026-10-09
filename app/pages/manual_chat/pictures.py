"""Pictures sent to Ask Buddy's model with a question - no Qt here.

Whatever the user picks, pastes or drops is turned upright, scaled to fit
MAX_SIDE and MAX_PIXELS (Claude's high-resolution limit - 2576 px, 3.75 MP;
models that work smaller scale it down themselves) and saved again as a
JPEG: the one format every provider and local server reads (llama.cpp
can't read WebP). Full colour detail (no chroma subsampling) at the first
quality step, so small coloured text in a screenshot stays readable.
Saving it again leaves the metadata - GPS, camera - behind. Transparency
is flattened onto white.

Two more copies go to the chat: a small preview (sharp at twice its drawn
size) and a view at up to VIEW_SIDE that opens larger when the preview is
clicked - kept beside the conversations (chat_store.py), not in them.

Only the question they're sent with carries the pictures; later turns
remember that there was one (conversation.py), so a long chat doesn't pay
for them again and again.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

try:
    from PIL import Image, ImageOps
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

MAX_PICTURES = 4             # per question
MAX_SIDE = 2576
MAX_PIXELS = 3_750_000
QUALITIES = (90, 82, 72, 62)
MAX_BYTES = 3 * 1024 * 1024  # every provider takes this (Claude's limit is 5 MB)
MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_INPUT_PIXELS = 80_000_000
PREVIEW_SIDE = 320           # drawn at up to 160 x 120 - sharp on a high-DPI screen
VIEW_SIDE = 1920
MIME = "image/jpeg"


class PictureError(Exception):
    """Why a picture can't be sent - shown to the user as it is."""


@dataclass
class Picture:
    data: str        # base64 JPEG, as the providers take it
    w: int
    h: int
    preview: str     # a small data: URL for the chat
    view: bytes = b""    # a JPEG to open larger

    def for_model(self) -> dict:
        return {"mime": MIME, "data": self.data}


def _jpeg(image, quality: int) -> bytes:
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality, optimize=True,       # no exif=: no metadata
               subsampling=0 if quality >= 90 else 2)
    return out.getvalue()


def _fit(image, side: int, pixels: int = 0):
    scale = min(1.0, side / max(image.size))
    if pixels and image.width * image.height * scale * scale > pixels:
        scale = (pixels / (image.width * image.height)) ** 0.5
    if scale >= 1.0:
        return image
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)


def prepare(data: bytes) -> Picture:
    """The picture as the model gets it. Raises PictureError."""
    if not AVAILABLE:
        raise PictureError("Sending pictures needs the Pillow package – reinstalling Buddy adds it.")
    if len(data) > MAX_INPUT_BYTES:
        raise PictureError(f"That file is over {MAX_INPUT_BYTES // (1024 * 1024)} MB – pick a smaller picture.")
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > MAX_INPUT_PIXELS:
            raise PictureError("That picture is too big to send – crop or shrink it first.")
        image.seek(0)
        image.load()
    except PictureError:
        raise
    except Exception:
        raise PictureError("That file isn't a picture Buddy can read – try a PNG, JPEG or WebP.") from None
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, (255, 255, 255))
        flat.paste(image, mask=image.getchannel("A"))
        image = flat
    else:
        image = image.convert("RGB")
    image = _fit(image, MAX_SIDE, MAX_PIXELS)
    for quality in QUALITIES:
        out = _jpeg(image, quality)
        if len(out) <= MAX_BYTES:
            break
    else:
        raise PictureError("That picture couldn't be made small enough to send.")
    preview = _jpeg(_fit(image, PREVIEW_SIDE), 82)
    view = out if max(image.size) <= VIEW_SIDE and len(out) <= 1024 * 1024 else _jpeg(_fit(image, VIEW_SIDE), 85)
    return Picture(base64.b64encode(out).decode("ascii"), image.width, image.height,
                   f"data:{MIME};base64,{base64.b64encode(preview).decode('ascii')}", view)
