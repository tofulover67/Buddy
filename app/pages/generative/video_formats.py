#!/usr/bin/env python3
"""
What a footage job's result is saved as, shared by the page (Buddy's
Python) and the job (the engine's) - so nothing here needs NumPy or OpenCV.

A video format is made at the end of a job from its frames (written as a
sequence while it works, so a stopped job carries on where it was): one
ffmpeg run, with the source's audio and timecode for that part of the clip.
"sequence" keeps the frames as they are instead - PNG, or TIFF when deep.

The subject (Separate) needs transparency, so it's ProRes 4444 whichever
video format is picked; with "sequence" it's PNG with transparency.
"""

from __future__ import annotations

from fractions import Fraction

FORMATS = {
    "prores_hq": {
        "label": "ProRes 422 HQ", "ext": ".mov", "alpha": False,
        "video": ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"],
        "audio": ["-c:a", "pcm_s24le"],
        "about": "High quality, 10-bit – the usual choice for Resolve",
    },
    "prores_4444": {
        "label": "ProRes 4444", "ext": ".mov", "alpha": True,
        "video": ["-c:v", "prores_ks", "-profile:v", "4", "-vendor", "apl0", "-pix_fmt", "yuva444p10le",
                  "-alpha_bits", "16"],
        "audio": ["-c:a", "pcm_s24le"],
        "about": "Highest quality, 10-bit, with transparency – bigger files",
    },
    "dnxhr_hqx": {
        "label": "DNxHR HQX", "ext": ".mov", "alpha": False,
        "video": ["-c:v", "dnxhd", "-profile:v", "dnxhr_hqx", "-pix_fmt", "yuv422p10le"],
        "audio": ["-c:a", "pcm_s24le"],
        "about": "High quality, 10-bit – Avid's intermediate",
    },
    "h264": {
        "label": "H.264", "ext": ".mp4", "alpha": False,
        "video": ["-c:v", "libx264", "-preset", "medium", "-crf", "14", "-pix_fmt", "yuv420p"],
        "audio": ["-c:a", "aac", "-b:a", "320k"],
        "even": True,                                   # 4:2:0 needs an even width and height
        "about": "Small files, 8-bit – for sharing more than for grading",
    },
    "sequence": {
        "label": "Image sequence", "ext": None, "alpha": True,
        "about": "A PNG or TIFF for every frame – no audio",
    },
}
DEFAULT = "prores_hq"
SUBJECT = "prores_4444"           # the subject's video format, whichever is picked

# ffprobe's colour matrix names -> scale's, and scale's -> the label written.
MATRICES = {"bt709": "bt709", "bt470bg": "bt601", "smpte170m": "bt601", "bt2020nc": "bt2020",
            "bt2020c": "bt2020", "fcc": "fcc", "smpte240m": "smpte240m"}
LABELS = {"bt709": "bt709", "bt601": "smpte170m", "bt2020": "bt2020nc", "fcc": "fcc", "smpte240m": "smpte240m"}
# Primaries and transfers passed on as the source says them (HLG, PQ, BT.2020 ...).
PRIMARIES = {"bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "film", "bt2020", "smpte428", "smpte431",
             "smpte432", "jedec-p22"}
TRANSFERS = {"bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "linear", "iec61966-2-4", "bt1361e",
             "iec61966-2-1", "bt2020-10", "bt2020-12", "smpte2084", "smpte428", "arib-std-b67"}
# swscale's careful rounding: its fast path read 8-bit clips 1-1.6/255 dark, a little more in green.
ACCURATE = "accurate_rnd+full_chroma_int+full_chroma_inp"


def colours(info):
    """How a clip's frames are read and its video files written, so the
    file matches the clip in Resolve: {matrix (scale's name), range ("full",
    "limited" or "" - as ffmpeg finds it), primaries, transfer ("" - not
    said)}. A matrix the file doesn't say is the one Resolve takes it to
    be: Rec.709 from HD up, BT.601 below - ffmpeg's own guess is BT.601 at
    any size, which came back with a blue cast on an untagged 4K Sony clip
    (measured in Resolve, 2026-10-07)."""
    matrix = MATRICES.get(info.get("matrix") or "")
    if not matrix:
        w, h = info.get("width") or 0, info.get("height") or 0
        matrix = "bt709" if max(w, h) >= 1280 or min(w, h) >= 720 else "bt601"
    rng = info["range"] if "range" in info else ("pc" if info.get("full_range") else "")
    return {"matrix": matrix, "range": {"pc": "full", "tv": "limited"}.get(rng, ""),
            "primaries": info.get("primaries") if info.get("primaries") in PRIMARIES else "",
            "transfer": info.get("transfer") if info.get("transfer") in TRANSFERS else ""}


def decode_filter(info):
    """The scale that turns a clip's frames into RGB as Resolve shows them."""
    c = colours(info)
    return f"scale=flags={ACCURATE}:in_color_matrix={c['matrix']}" + (f":in_range={c['range']}" if c["range"] else "")


def choices():
    """For the view: [{id, label, about, ext}], in the order shown."""
    return [{"id": k, "label": v["label"], "about": v["about"], "ext": v["ext"] or ""} for k, v in FORMATS.items()]


def known(fmt):
    return fmt if fmt in FORMATS else DEFAULT


def is_video(fmt):
    return FORMATS[known(fmt)]["ext"] is not None


def for_kind(fmt, kind):
    """The format `kind` ("clean", "background", "subject") is saved as."""
    fmt = known(fmt)
    if kind == "subject" and is_video(fmt) and not FORMATS[fmt]["alpha"]:
        return SUBJECT
    return fmt


# --------------------------------------------------------------- timecode --

def _drop(fps):
    """Frames dropped a minute for drop-frame timecode (29.97, 59.94), else 0."""
    rate = round(float(fps))
    return 2 * rate // 30 if rate in (30, 60) and Fraction(fps).denominator == 1001 else 0


def tc_to_frames(tc, fps):
    parts = tc.replace(";", ":").replace(".", ":").split(":")
    h, m, s, f = (int(p) for p in parts)
    rate = round(float(fps))
    frames = ((h * 60 + m) * 60 + s) * rate + f
    drop = _drop(fps) if (";" in tc or "." in tc) else 0
    if drop:
        minutes = h * 60 + m
        frames -= drop * (minutes - minutes // 10)
    return frames


def frames_to_tc(n, fps, drop_frame=False):
    rate = round(float(fps))
    drop = _drop(fps) if drop_frame else 0
    n = max(0, int(n))
    if drop:
        per_10 = rate * 600 - drop * 9
        per_min = rate * 60 - drop
        tens, rest = divmod(n, per_10)
        n += drop * 9 * tens + (drop * ((rest - drop) // per_min) if rest > drop else 0)
    f = n % rate
    s = n // rate % 60
    m = n // (rate * 60) % 60
    h = n // (rate * 3600) % 24
    sep = ";" if drop else ":"
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:02d}"


def tc_add(tc, frames, fps):
    """The timecode `frames` on from `tc` (as the source writes it), or ""."""
    if not tc:
        return ""
    try:
        return frames_to_tc(tc_to_frames(tc, fps) + int(frames), fps, ";" in tc or "." in tc)
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------- command --

def encode_command(ffmpeg, pattern, first, count, fps, out, fmt, *, kind="clean", source=None, at=0.0,
                   timecode="", info=None, audio=True):
    """ffmpeg's arguments to make `out` from the sequence `pattern`
    (name_%06d.png, frames first .. first + count - 1) at `fps` ("num/den"),
    with the source's audio from `at` seconds for as long (when `audio`) and
    its timecode. `info` is the source's probe: its colours are written
    back as colours() read them - always as limited range, which is what
    ffmpeg labels ProRes and Resolve then believes (Sony A7 IV full-range
    clips came back 6/255 darker in the shadows, 9/255 brighter in the
    highlights when their values were copied as they were, 2026-10-07)."""
    spec = FORMATS[for_kind(fmt, kind)]
    fps = Fraction(fps)
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-y", "-framerate", f"{fps.numerator}/{fps.denominator}",
           "-start_number", str(int(first)), "-i", pattern]
    with_audio = bool(audio and source and kind != "subject")
    if with_audio:
        cmd += ["-ss", f"{at:.6f}", "-t", f"{float(count / fps):.6f}", "-i", source]   # a Fraction takes .6f only from 3.12
    cmd += ["-map", "0:v:0"]
    if with_audio:
        cmd += ["-map", "1:a?"]                   # every audio track, each kept as it is
    c = colours(info or {})
    pix = spec["video"][spec["video"].index("-pix_fmt") + 1]
    labels = {"colorspace": LABELS[c["matrix"]], "range": "tv"}
    if c["primaries"]:
        labels["color_primaries"] = c["primaries"]
    if c["transfer"]:
        labels["color_trc"] = c["transfer"]
    # Labelled on the frames (setparams): ffmpeg 9 dropped -color_primaries / -color_trc given on their own.
    chain = [f"scale=flags={ACCURATE}:out_color_matrix={c['matrix']}:out_range=limited", f"format={pix}",
             "setparams=" + ":".join(f"{k}={v}" for k, v in labels.items())]
    if spec.get("even"):
        chain.insert(0, "pad=ceil(iw/2)*2:ceil(ih/2)*2")
    cmd += ["-vf", ",".join(chain), "-frames:v", str(int(count))]
    cmd += spec["video"]
    cmd += ["-colorspace", labels["colorspace"], "-color_range", "tv"]
    if with_audio:
        cmd += spec["audio"]
    if timecode:
        cmd += ["-timecode", timecode]
    cmd += ["-progress", "pipe:1", "-nostats", out]
    return cmd
