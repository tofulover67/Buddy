#!/usr/bin/env python3
"""
The Proxy tab's rules, without Qt: which clips a scope covers, where each
clip's proxy file goes, the ffmpeg command that renders it, and what a
run reports.

Proxies are .mov files written into a "Proxy" folder beside each source -
the same convention Resolve's own Generate Proxy Media uses, so a project
stays portable - or into a folder chosen on the tab, under each source's
own folder path (proxy_dir). Then they're linked in Resolve with
MediaPoolItem.LinkProxyMedia, the call behind the manual "Relink Proxy
Media". Source files are never
touched: the proxy is a new file, and only Resolve's own proxy toggle ever
swaps a clip onto it.

Only video clips are transcoded - an audio-only clip never plays from a
proxy, so it has nothing to gain. The audio inside a video clip is
stream-copied rather than re-encoded, all audio streams included, so a
clip keeps its sound (and a multicam angle keeps its tracks) when Resolve
plays the proxy.

Resolve only links a proxy whose timecode matches the clip's, and ffmpeg
doesn't carry a source's timecode over by itself - a Sony camera keeps it
in its own metadata track (rtmd), which a proxy doesn't copy. So every
render is stamped with the clip's start timecode as Resolve reads it
(-timecode), which is exactly what the link is checked against.

A clip whose proxy file already exists on disk is linked, not re-rendered
- delete the file to force a re-render - unless its timecode doesn't
match the clip's: then it's one Resolve would refuse, and it's rendered
again in its place. Renders write to a ".buddy-part"
file beside the destination and only rename it into place once ffmpeg
succeeds, so a cancelled or failed run never leaves a half-written proxy
where Resolve could link it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .ffmpeg_utils import _ffprobe_path, _no_feedback_cursor_kwargs
from .importer import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from .resolve_ext import _is_real_media_clip

PROXY_DIRNAME = "Proxy"
PROXY_SUFFIX = "_proxy"
PROXY_EXT = ".mov"
PART_EXT = ".buddy-part.mov"

# Resolution is a pure scale of the picture; the frame rate is never
# touched (a proxy must match its source's rate to be linkable), so
# "original" is simply no scale filter at all. The trunc()*2 forms force
# even dimensions, which libx264 requires.
# cuda_vf: the same on the graphics card (the GPU formats), which also
# turns the picture into the 8-bit 4:2:0 NVENC takes.
RESOLUTIONS = {
    "original": {"label": "Original", "vf": None, "cuda_vf": "scale_cuda=format=yuv420p"},
    "half": {"label": "Half", "vf": "scale=trunc(iw/4)*2:trunc(ih/4)*2",
             "cuda_vf": "scale_cuda=trunc(iw/4)*2:trunc(ih/4)*2:format=yuv420p"},
    "quarter": {"label": "Quarter", "vf": "scale=trunc(iw/8)*2:trunc(ih/8)*2",
                "cuda_vf": "scale_cuda=trunc(iw/8)*2:trunc(ih/8)*2:format=yuv420p"},
}
# The formats offered, in the order the dropdown lists them. The long-GOP
# ones (H.264, H.265) make small files but take more work to decode while
# scrubbing; the intra-frame ones (ProRes, DNxHR, CineForm) are heavier on
# disk and light to play. Every one takes stream-copied audio, so a clip's
# sound survives the swap unchanged - and every one was checked to link in
# Resolve Studio 21.1 (half-size FX3 H.264 4:2:2 source, 2026-09-30).
# "h264" and "prores" keep their ids from before there were more, so a
# saved choice still means the same format.
_PRORES = ["-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", "-vendor", "apl0", "-profile:v"]
_DNXHR = ["-c:v", "dnxhd", "-pix_fmt", "yuv422p", "-profile:v"]
CODECS = {
    "h264": {"label": "H.264", "hint": "Small files that play anywhere.",
             "args": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                      "-pix_fmt", "yuv420p"]},
    "h265": {"label": "H.265 (HEVC)", "hint": "The smallest files, but the heaviest to decode while scrubbing.",
             "args": ["-c:v", "libx265", "-preset", "fast", "-crf", "26", "-pix_fmt", "yuv420p",
                      "-tag:v", "hvc1", "-x265-params", "log-level=error"]},
    "prores": {"label": "ProRes 422 Proxy", "hint": "Light to play back, larger files – Resolve's usual proxy format.",
               "args": _PRORES + ["0"]},
    "prores_lt": {"label": "ProRes 422 LT", "hint": "Better picture than Proxy, about twice the size.",
                  "args": _PRORES + ["1"]},
    "prores_422": {"label": "ProRes 422", "hint": "Close to the original's quality – large files.",
                   "args": _PRORES + ["2"]},
    "dnxhr_lb": {"label": "DNxHR LB", "hint": "Avid's low-bandwidth format: light to play, modest files.",
                 "args": _DNXHR + ["dnxhr_lb"]},
    "dnxhr_sq": {"label": "DNxHR SQ", "hint": "Avid's standard quality – larger files.",
                 "args": _DNXHR + ["dnxhr_sq"]},
    "dnxhr_hq": {"label": "DNxHR HQ", "hint": "Avid's high quality – large files.",
                 "args": _DNXHR + ["dnxhr_hq"]},
    "cineform": {"label": "CineForm", "hint": "GoPro's intra-frame format: light to play, between LB and SQ in size.",
                 "args": ["-c:v", "cfhd", "-quality", "medium", "-pix_fmt", "yuv422p10le"]},
    # The graphics card's formats (Windows): the first of NVIDIA, AMD and
    # Intel's encoders (GPU_ENCODERS) that starts on this PC, and the "cpu"
    # format on a PC with none of them (render_plan). The ids are NVIDIA's
    # encoders' names from when there was only NVIDIA, so a saved choice
    # still means the same format.
    "h264_nvenc": {"label": "H.264 (GPU)",
                   "hint": "Rendered on the graphics card – much faster, and leaves the CPU free for Resolve.",
                   "gpu": ["h264_nvenc", "h264_amf", "h264_qsv"], "cpu": "h264"},
    "hevc_nvenc": {"label": "H.265 (GPU)",
                   "hint": "Smaller files than H.264, rendered on the graphics card.",
                   "gpu": ["hevc_nvenc", "hevc_amf", "hevc_qsv"], "cpu": "h265"},
}

# The encoders behind the GPU formats, in the order they're tried.
# NVIDIA's ("cuda"): the clip is decoded, scaled and encoded on the card,
# so the CPU stays free for Resolve: a minute of 4K on an RTX 5080 took
# 1-3 s of CPU time, not 100-230 s, and a drone's H.265 rendered 4x faster
# (2026-09-30). A source the card can't decode (ProRes, DNxHR, BRAW, 4:2:2
# before the RTX 50s) is decoded on the CPU and still encoded on the card.
# AMD's and Intel's: decoded and scaled on the CPU, encoded on the card -
# ffmpeg's Windows decoder (d3d11va) takes the first graphics card, which
# on a PC with an NVIDIA card as well is the wrong one, and AMD's encoder
# then won't start. AMD's rate control is constant QP: a Ryzen 9000's
# Radeon refuses QVBR (2026-10-26). Decoding is most of the work with 4K
# 4:2:2 H.264 either way: a 20 s clip took 52 s of CPU time on the Radeon,
# 63 s with libx264. Intel's are untested on Intel hardware.
GPU_ENCODERS = {
    "h264_nvenc": {"vendor": "NVIDIA", "cuda": True,
                   "args": ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "28", "-b:v", "0"]},
    "h264_amf": {"vendor": "AMD",
                 "args": ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp",
                          "-qp_i", "24", "-qp_p", "26", "-qp_b", "28"]},
    "h264_qsv": {"vendor": "Intel",
                 "args": ["-c:v", "h264_qsv", "-preset", "faster", "-global_quality", "25"]},
    "hevc_nvenc": {"vendor": "NVIDIA", "cuda": True,
                   "args": ["-c:v", "hevc_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "30", "-b:v", "0",
                            "-tag:v", "hvc1"]},
    "hevc_amf": {"vendor": "AMD",
                 "args": ["-c:v", "hevc_amf", "-quality", "speed", "-rc", "cqp",
                          "-qp_i", "26", "-qp_p", "28", "-qp_b", "30", "-tag:v", "hvc1"]},
    "hevc_qsv": {"vendor": "Intel",
                 "args": ["-c:v", "hevc_qsv", "-preset", "faster", "-global_quality", "27", "-tag:v", "hvc1"]},
}


def codec_choices(platform=None):
    """The dropdown's entries, in order: [{id, label, hint}]. The GPU ones
    only on Windows - a Mac's encoders are VideoToolbox, not these."""
    platform = platform or sys.platform
    return [{"id": key, "label": c["label"], "hint": c["hint"]} for key, c in CODECS.items()
            if platform == "win32" or not c.get("gpu")]


_GPU_CHECKED = {}   # (ffmpeg path, encoder) -> True: it encoded here (asked once)
_GPU_PROBLEM = {}   # (ffmpeg path, encoder) -> why it didn't, the last time it was tried

NO_NVIDIA = "it needs an NVIDIA graphics card."
# Why an encoder didn't start when this PC hasn't got the hardware for it -
# the one reason that isn't worth a word while another card's encoder works.
NO_HARDWARE = {
    "NVIDIA": NO_NVIDIA,
    "AMD": "it needs AMD Radeon graphics.",
    "Intel": "it needs Intel graphics with Quick Sync.",
}
NO_GPU = "it needs an NVIDIA, AMD or Intel graphics card that can encode video."
_NO_DEVICE = {
    "NVIDIA": r"Cannot load nvcuda|Cannot load nvEncodeAPI|No capable devices found|No NVENC capable devices"
              r"|Could not dynamically load CUDA|cuInit\(0\) failed",
    # amfrt64.dll is AMD's driver; CreateComponent fails on a Radeon without
    # this encoder (the AV1 one on a Ryzen 9000's, 2026-10-26).
    "AMD": r"amfrt64|Failed to load AMF|AMF failed to initiali|CreateComponent\(|No such device",
    # ffmpeg 9 on a PC with no Intel graphics: "Error creating a MFX session: -9".
    "Intel": r"MFX session|mfx implementation|libmfx|libvpl|No device available",
}


# The NVIDIA cards that get no new drivers, and the last driver series
# each has: by CUDA compute capability, Kepler (3.x) stops at 470;
# Maxwell, Pascal and Volta (5.0-7.0: the GTX 750 and 900s, the GTX 10s,
# Titan V) at 580. Turing (7.5: GTX 16, RTX 20) and later still get them.
# Telling one of these to update past its last series sends its owner
# looking for a driver that doesn't exist; an ffmpeg built for an older
# NVENC (headers 13.0 want driver 570) does work on a 580 card.
LAST_DRIVER = {
    470: "this NVIDIA card gets no drivers newer than the 470 series, and this ffmpeg needs a newer one. "
         "An older ffmpeg build that works with driver 470 will use the card – set its path in Settings.",
    580: "this NVIDIA card gets no drivers newer than the 580 series, and this ffmpeg needs a newer one. "
         "An older ffmpeg build that works with driver 580 will use the card – set its path in Settings.",
}


def last_driver_series(compute):
    """The last driver series a card of this compute capability ("6.1")
    gets - 470 or 580 - or None for one that still gets new drivers (or a
    capability that can't be read)."""
    try:
        capability = float(compute)
    except (TypeError, ValueError):
        return None
    if capability < 5:
        return 470
    return 580 if capability < 7.5 else None


def gpu_problem(stderr, driver="", vendor="NVIDIA", last=None):
    """What ffmpeg's error on the test encode means, in words. A build
    newer than the driver is the usual one on a PC that does have an NVIDIA
    card: ffmpeg 9 wants driver 610 or newer (NVENC 13.1) and says so.
    last: the card's last driver series (last_driver_series), when it gets
    no new ones - then updating is no answer once ffmpeg needs more."""
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")
    needed = re.search(r"minimum required Nvidia driver for nvenc is ([\d.]+)", text, re.I)
    if vendor == "NVIDIA" and (needed or re.search(r"does not support the required nvenc API version", text, re.I)):
        if last in LAST_DRIVER:
            need = int(float(needed.group(1))) if needed else None
            have = re.match(r"\d+", driver or "")
            # A card short of its own last series can still update to it.
            if (need is not None and need > last) or (need is None and (not have or int(have.group()) >= last)):
                return LAST_DRIVER[last]
        version = f" {needed.group(1)}" if needed else ""
        have = f", and this PC has {driver}" if driver else ""
        return (f"the NVIDIA driver is older than this ffmpeg needs – driver{version} or newer{have}. "
                f"Update it in the NVIDIA app or from nvidia.com.")
    if re.search(r"Unknown encoder|Encoder not found|Requested encoder", text, re.I):
        return f"this ffmpeg was built without {vendor}'s encoders (a \"full\" build has them)."
    if re.search(_NO_DEVICE[vendor], text, re.I):
        return NO_HARDWARE[vendor]
    lines = [line.split("] ", 1)[-1].strip() for line in text.splitlines() if line.strip()]
    return f"{vendor}'s encoder didn't start ({lines[0][:160].rstrip('.')})." if lines else NO_HARDWARE[vendor]


def nvidia_driver(run=None, query="driver_version"):
    """The installed NVIDIA driver's version ("596.36"), or "" - for saying
    how far behind it is. query="compute_cap": the card's CUDA compute
    capability ("6.1" on a GTX 1080) instead, for last_driver_series."""
    try:
        result = (run or subprocess.run)(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader"],
                                         capture_output=True, timeout=10, **_no_feedback_cursor_kwargs())
    except (subprocess.SubprocessError, OSError):
        return ""
    out = (result.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    return out[0].strip() if result.returncode == 0 and out else ""


def gpu_available(ffmpeg_path, encoder, run=None):
    """Whether this ffmpeg can encode with `encoder` (a GPU_ENCODERS key)
    on this PC - a build includes every vendor's encoders whatever card
    there is to run them (or a driver too old for it), so it's tried, with
    the settings a proxy uses, on a tenth of a second of black. A yes is
    kept for the session; a no is tried again next run, so updating the
    driver works without restarting Buddy. Why not: _GPU_PROBLEM."""
    key = (ffmpeg_path, encoder)
    if _GPU_CHECKED.get(key):
        return True
    vendor = GPU_ENCODERS[encoder]["vendor"]
    run = run or subprocess.run
    try:
        result = run([ffmpeg_path, "-hide_banner", "-v", "error", "-f", "lavfi",
                      "-i", "color=black:s=256x144:d=0.1", *GPU_ENCODERS[encoder]["args"],
                      "-pix_fmt", "yuv420p", "-f", "null", "-"],
                     capture_output=True, timeout=30, **_no_feedback_cursor_kwargs())
    except (subprocess.SubprocessError, OSError) as exc:
        _GPU_PROBLEM[key] = f"{vendor}'s encoder didn't start ({exc})."
        return False
    if result.returncode == 0:
        _GPU_CHECKED[key] = True
        _GPU_PROBLEM.pop(key, None)
        return True
    stderr = getattr(result, "stderr", b"") or b""
    driver, last = "", None
    if b"nvenc API" in stderr or b"Nvidia driver" in stderr:
        driver = nvidia_driver(run)
        last = last_driver_series(nvidia_driver(run, "compute_cap"))
    _GPU_PROBLEM[key] = gpu_problem(stderr, driver, vendor, last)
    return False


def render_plan(ffmpeg_path, codec):
    """(what a run really renders - a CODECS or GPU_ENCODERS key - and what
    to tell the user, or ""). A GPU format renders on the first of its
    encoders that starts here, and becomes its CPU format when none does.
    An encoder that didn't start for want of the hardware isn't mentioned
    while another works, nor one this ffmpeg was built without (the PC may
    not have that card at all); one that failed for any other reason (a
    driver too old) is, from ffmpeg's own error."""
    encoders = CODECS.get(codec, {}).get("gpu")
    if not encoders:
        return codec, ""
    problems, missing = [], []
    for encoder in encoders:
        vendor = GPU_ENCODERS[encoder]["vendor"]
        if gpu_available(ffmpeg_path, encoder):
            if not problems:
                return encoder, ""
            other, why = problems[0]
            return encoder, (f"Couldn't encode on the {other} graphics card: {why} "
                             f"This run used the {vendor} graphics instead.")
        why = _GPU_PROBLEM.get((ffmpeg_path, encoder)) or NO_HARDWARE[vendor]
        if why.startswith("this ffmpeg was built without"):
            missing.append((vendor, why))
        elif why != NO_HARDWARE[vendor]:
            problems.append((vendor, why))
    fallback = CODECS[codec]["cpu"]
    why = (problems or missing or [("", NO_GPU)])[0][1]
    return fallback, (f"Couldn't encode {CODECS[codec]['label']} on this PC: {why} "
                      f"This run used {CODECS[fallback]['label']} on the CPU instead.")
DEFAULT_RESOLUTION = "half"
DEFAULT_CODEC = "h264"

# Where proxies go: a "Proxy" folder beside each source, or a folder chosen
# on the tab (proxy_dir).
LOCATIONS = ("beside", "folder")
DEFAULT_LOCATION = "beside"

# What a run can target. "timeline" is the clips selected on the open
# timeline, or every clip on it when none are (or when this Resolve can't
# say - Timeline.GetSelectedClips is 21.0.4 and later).
SCOPES = ("selection", "timeline", "bin", "all")
DEFAULT_SCOPE = "selection"

SCOPE_LABELS = {
    "selection": "the Media Pool selection",
    "timeline": "the current timeline",
    "bin": "the open bin",
    "all": "every clip in the project",
}


def scope_label(scope, bin_name=None, timeline_name=None, recursive=False, timeline_selection=False):
    """How to name the scope in the log and the job label: a plain phrase,
    with the live bin or timeline name when there is one.
    timeline_selection: the timeline run took only its selected clips."""
    if scope == "bin":
        name = (bin_name or "").strip()
        if not name:
            return "the open bin"
        what = "Master" if name == "Master" else f"bin '{name}'"
        if recursive:
            what += " and its sub-bins"
        return what
    if scope == "timeline" and timeline_name:
        return f"the selection on '{timeline_name}'" if timeline_selection else f"every clip on '{timeline_name}'"
    return SCOPE_LABELS.get(scope, scope)


INFO_TEXT = (
    "Renders lightweight copies of the project's video clips with ffmpeg and "
    "links them in Resolve as proxy media - the same workflow as Resolve's own "
    "Generate Proxy Media, for when a machine struggles to play the originals "
    "back.\n\n"
    "Proxies are .mov files written into a 'Proxy' folder beside each source "
    "(Resolve's own convention) – or, if you choose a folder, into it under "
    "each source's own folder path, so clips of the same name never clash – "
    "then linked with the same call the manual relink uses. Source files are never touched, and each clip's audio is "
    "stream-copied, so nothing changes but the playback load.\n\n"
    "Which clips: the Media Pool selection, the current timeline (the clips "
    "selected on it, or all of them when none are), the open bin (optionally "
    "with its sub-bins), or every clip in the project. Only video files are transcoded - audio-only clips "
    "never play from a proxy.\n\n"
    "Resolution and format are chosen here. H.264 and H.265 make the smallest "
    "files but take more work to decode while scrubbing; ProRes, DNxHR and "
    "CineForm are larger on disk and much lighter to play. Each proxy carries "
    "its clip's timecode, which Resolve needs to link it. A "
    "clip whose proxy file already exists is linked, not re-rendered - unless "
    "the file's timecode is wrong, when it's rendered again; delete the file "
    "to render it again anyway. ffmpeg is needed - install it or set its "
    "path in Settings."
)


class ProxyError(ValueError):
    """A run-stopping problem, worded to be read by a person."""


class ProxyCancelled(ProxyError):
    """The render was killed by Cancel (terminate_active) - not a failure to retry."""


# ------------------------------------------------------------- timecode

# "19:45:12:08", or "19:45:12;08" for drop frame - Resolve's Start TC and
# what ffmpeg's -timecode takes.
_TIMECODE = re.compile(r"\d{2}:\d{2}:\d{2}[:;.]\d{2,3}")
NO_TIMECODE = "00:00:00:00"   # what Resolve reads for a file that has none


def clean_timecode(raw):
    """A start timecode ffmpeg can stamp, or "" if raw isn't one."""
    text = str(raw or "").strip()
    return text if _TIMECODE.fullmatch(text) else ""


def same_timecode(a, b):
    """Whether two start timecodes are the same frame. Drop-frame ";" and
    "." separators count as ":", and a file with no timecode at all starts
    at 00:00:00:00, as Resolve reads it."""
    def norm(tc):
        return (clean_timecode(tc) or NO_TIMECODE).replace(";", ":").replace(".", ":")
    return norm(a) == norm(b)


def file_timecode(ffmpeg_path, path, timeout=30):
    """A media file's start timecode - from any stream's tags or the
    container's (a proxy's tmcd track, a Sony clip's rtmd) - or "" when it
    has none or can't be read. ffprobe when it ships beside ffmpeg; else
    `ffmpeg -i`, which prints the same tags to stderr."""
    ffprobe = _ffprobe_path(ffmpeg_path)
    try:
        if ffprobe:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "stream_tags=timecode:format_tags=timecode",
                 "-of", "json", path],
                capture_output=True, timeout=timeout, **_no_feedback_cursor_kwargs())
            doc = json.loads(result.stdout or b"{}") if result.returncode == 0 else {}
            for tags in [s.get("tags") or {} for s in doc.get("streams") or []] + [
                    (doc.get("format") or {}).get("tags") or {}]:
                tc = clean_timecode(tags.get("timecode"))
                if tc:
                    return tc
            return ""
        result = subprocess.run([ffmpeg_path, "-hide_banner", "-i", path], capture_output=True,
                                timeout=timeout, **_no_feedback_cursor_kwargs())
    except (subprocess.SubprocessError, ValueError, OSError):
        return ""
    found = re.search(r"^\s*timecode\s*:\s*(\S+)", (result.stderr or b"").decode("utf-8", "replace"),
                      re.MULTILINE)
    return clean_timecode(found.group(1)) if found else ""


# ------------------------------------------------------------- collecting

def _entry(clip):
    """{id, name, path, type, tc} for a MediaPoolItem, or None if the clip
    can't be described at all. tc: its start timecode as Resolve reads it
    ("" if it gave none) - what a proxy has to match to be linked."""
    try:
        props = clip.GetClipProperty() or {}
    except Exception:
        return None
    path = str(props.get("File Path") or "")
    name = str(props.get("Clip Name") or "")
    if not name:
        try:
            name = str(clip.GetName() or "")
        except Exception:
            name = ""
    try:
        clip_id = str(clip.GetUniqueId() or "")
    except Exception:
        clip_id = ""
    if not clip_id:
        clip_id = name or path
    return {"id": clip_id, "name": name or os.path.basename(path) or "Untitled",
            "path": path, "type": str(props.get("Type") or ""),
            "codec": str(props.get("Video Codec") or ""),
            "tc": clean_timecode(props.get("Start TC")),
            # Its proxy as Resolve has it: "None", or the proxy's size.
            "proxy": str(props.get("Proxy") or ""),
            "proxy_path": str(props.get("Proxy Media Path") or "")}


def entries(clips):
    """Deduped entries for `clips` plus the objects behind them, so the
    link pass can find each clip again after the worker finishes:
    ([{id, name, path, type}], {id: clip})."""
    result, objects, seen = [], {}, set()
    for clip in clips:
        entry = _entry(clip)
        if entry is None or entry["id"] in seen:
            continue
        seen.add(entry["id"])
        result.append(entry)
        objects[entry["id"]] = clip
    return result, objects


def is_video(entry):
    """Whether a clip's media is a video file. By extension first - a
    .braw or .mp4 is video whatever the Type property says, and an image
    extension (a still, or one frame of a sequence) is never video -
    then by Resolve's own Type value ("video" or "movie")."""
    path = entry["path"]
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return True
    if ext in IMAGE_EXTENSIONS:
        return False
    return entry["type"].strip().lower() in ("video", "movie")


def selected_pool(project):
    """The Media Pool's selection. Raises ProxyError on a Resolve old
    enough not to read the selection at all."""
    pool = project.GetMediaPool()
    try:
        clips = pool.GetSelectedClips()
    except Exception as exc:
        raise ProxyError(
            "This Resolve version could not read the Media Pool selection. "
            "Update Resolve to use the selected clips here."
        ) from exc
    clips = [c for c in (clips or []) if _is_real_media_clip(c)]
    return entries(clips)


def timeline_clips(timeline):
    """The current timeline's clips: the ones selected on it, or every
    clip on its video tracks when none are - or when this Resolve can't
    read a timeline selection (GetSelectedClips is 21.0.4+; dir() is the
    truth on Resolve's wrapper objects - hasattr always answers yes).
    Mapped back to their Media Pool items - a timeline item is not itself
    a MediaPoolItem, and items with none (titles, generators) have nothing
    to proxy. Returns (entries, objects, from_selection)."""
    items = []
    if "GetSelectedClips" in dir(timeline):
        items = list(timeline.GetSelectedClips() or [])
    from_selection = bool(items)
    if not items:
        for index in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
            items.extend(timeline.GetItemListInTrack("video", index) or [])
    media = []
    for item in items:
        try:
            clip = item.GetMediaPoolItem()
        except Exception:
            clip = None
        if clip is not None:
            media.append(clip)
    found, objects = entries(media)
    return found, objects, from_selection


# ------------------------------------------------------------------ plan

def proxy_dir(src, root=None):
    """The folder src's proxy goes in: a 'Proxy' folder beside it - or,
    with root (a folder chosen on the tab), root followed by src's own
    folder path, so two cards' C0001.MP4 never meet and a later run finds
    each one's proxy again: E:\\Shoot\\Card A\\C0001.MP4 under D:\\Proxies
    goes in D:\\Proxies\\E\\Shoot\\Card A, /Volumes/Card A/C0001.MP4 under
    /Users/you/Proxies in /Users/you/Proxies/Volumes/Card A."""
    folder = os.path.dirname(src) or "."
    if not root:
        return os.path.join(folder, PROXY_DIRNAME)
    drive, rest = os.path.splitdrive(os.path.abspath(folder))
    # "E:" -> "E"; a share's "\\server\share" -> "server", "share".
    parts = [p for p in re.split(r"[\\/]+", drive.replace(":", "") + "/" + rest) if p]
    return os.path.join(root, *parts)


def _claim(src, claimed, root=None):
    """The .mov path this source's proxy should use (in proxy_dir), and
    whether that's a render or an existing file to adopt.

    `claimed` tracks, per (folder, lowercase stem), which proxy names this
    run has handed out and whether the stem's first file was adopted. The
    first clip of a stem may adopt a proxy that already exists on disk - a
    previous run's output, so relink rather than re-render. Any LATER clip
    with the same stem must not: an existing file under its number might
    be another clip's proxy, so it moves on to the next free name instead.

    Returns ("render", path) or ("existing", path)."""
    folder = proxy_dir(src, root)
    stem = os.path.splitext(os.path.basename(src))[0]
    base = stem + PROXY_SUFFIX
    state = claimed.setdefault((os.path.normcase(folder), base.lower()), {"adopted": False, "names": set()})
    n = 1
    while True:
        name = f"{base}{PROXY_EXT}" if n == 1 else f"{base}_{n}{PROXY_EXT}"
        key = name.lower()
        if key in state["names"]:
            n += 1
            continue
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            state["names"].add(key)
            return "render", path
        if n == 1 and not state["adopted"]:
            state["adopted"] = True
            state["names"].add(key)
            return "existing", path
        n += 1


def plan(videos, root=None):
    """Where each video clip's proxy goes - beside it, or under root (see
    proxy_dir). `videos` is the deduped entry list of one scope's video
    clips; returns
    {"render": [{id, name, path, dst}], "existing": [{id, name, path, dst}],
     "skipped": [sentence, ...]}.

    Skips are finished English sentences (they go straight into the
    Activity log) rather than codes, so the reason reads the same
    everywhere it's shown."""
    render, existing, skipped = [], [], []
    claimed = {}
    for entry in sorted(videos, key=lambda e: ((e["name"] or "").lower(), e["path"] or "")):
        src = entry["path"]
        if not src:
            skipped.append(f"Skipped \"{entry['name']}\" – no file behind this clip.")
            continue
        if not os.path.isfile(src):
            skipped.append(f"Skipped \"{entry['name']}\" – the file is offline.")
            continue
        kind, dst = _claim(src, claimed, root)
        target = {**entry, "dst": dst}
        (render if kind == "render" else existing).append(target)
    return {"render": render, "existing": existing, "skipped": skipped}


# --------------------------------------------------------------- rendering

_ACTIVE = set()
_KILLED = set()         # killed by terminate_active: their exit is a cancel, not a failure
_ACTIVE_LOCK = threading.Lock()


def terminate_active():
    """Kills every proxy render currently running. Returns how many.

    Called on cancel (and on quit): without it, stopping a batch leaves
    the current ffmpeg still reading and encoding media - the work is
    abandoned, but the reading isn't. Safe from any thread: killing a
    process the owning thread is blocked on just makes its wait return,
    and every caller already treats a killed render as a failed clip."""
    with _ACTIVE_LOCK:
        running = list(_ACTIVE)
        _KILLED.update(running)
    for process in running:
        try:
            process.kill()
        except Exception:
            pass
    return len(running)


def build_command(ffmpeg_path, src, out_path, resolution, codec, timecode="", gpu_decode=False):
    """The ffmpeg invocation that renders one proxy. No -r anywhere: the
    frame rate is left exactly as the source plays it, and -timecode
    stamps the clip's start timecode - both are what make a proxy
    linkable. -map 0:a? keeps every audio stream (a multicam angle's
    tracks all survive) and is optional, so a video with no sound at all
    still renders.

    codec: a CODECS key, or a GPU_ENCODERS one (what render_plan picked).
    gpu_decode (NVIDIA's encoders only): decode and scale on the graphics
    card too; without it the CPU decodes and scales, and hands the encoder
    8-bit 4:2:0 frames."""
    try:
        encoder = GPU_ENCODERS.get(codec)
        codec_args = list(encoder["args"] if encoder else CODECS[codec]["args"])
        cuda = bool(encoder and encoder.get("cuda") and gpu_decode)
        vf = RESOLUTIONS[resolution]["cuda_vf" if cuda else "vf"]
    except KeyError as exc:
        raise ProxyError("Unknown proxy resolution or codec.") from exc
    cmd = [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y"]
    if cuda:
        cmd += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    elif encoder:
        codec_args += ["-pix_fmt", "yuv420p"]
    cmd += ["-i", src]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-map", "0:v:0", "-map", "0:a?", "-c:a", "copy"]
    cmd += codec_args
    timecode = clean_timecode(timecode)
    if timecode:
        cmd += ["-timecode", timecode]
    cmd.append(out_path)
    return cmd


def _run_process(cmd):
    """Runs one ffmpeg to completion. No timeout by design: a legitimate
    render of a long file on a slow machine can take hours, and
    cancellation (terminate_active) is the safety valve that always works."""
    process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               **_no_feedback_cursor_kwargs())
    with _ACTIVE_LOCK:
        _ACTIVE.add(process)
    try:
        _out, err = process.communicate()
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(process)
            killed = process in _KILLED
            _KILLED.discard(process)
    return (None if killed else process.returncode), err


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _last_error_line(stderr_bytes):
    """ffmpeg's most useful line - the last non-empty one on stderr -
    kept short enough to read in a log."""
    text = (stderr_bytes or b"").decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1][:160]


def render_one(ffmpeg_path, src, dst, resolution, codec, run=None, timecode=""):
    """Renders one proxy to dst, or raises ProxyError with a readable
    reason. `run` (used by the tests) replaces the real ffmpeg call.

    The encode writes to '<dst>.buddy-part.mov' and renames it into place
    only on success, so a cancelled or failed run never leaves a
    half-written proxy that a later run could adopt."""
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
    except OSError as exc:
        raise ProxyError(
            f"Couldn't create the 'Proxy' folder beside \"{os.path.basename(src)}\": {exc}"
        ) from exc
    part = dst + PART_EXT
    run = run or _run_process
    code, err = 1, b""
    if GPU_ENCODERS.get(codec, {}).get("cuda"):
        # All on the graphics card first; a source it can't decode fails
        # at once, and goes again decoded on the CPU.
        code, err = run(build_command(ffmpeg_path, src, part, resolution, codec, timecode, gpu_decode=True))
    if code is None:                    # killed by Cancel: not a source the card can't take
        _unlink(part)
        raise ProxyCancelled("Cancelled.")
    if code != 0:
        code, err = run(build_command(ffmpeg_path, src, part, resolution, codec, timecode))
    if code is None:
        _unlink(part)
        raise ProxyCancelled("Cancelled.")
    if code != 0:
        _unlink(part)
        detail = _last_error_line(err)
        raise ProxyError(f"ffmpeg couldn't render \"{os.path.basename(src)}\""
                         + (f": {detail}" if detail else ""))
    os.replace(part, dst)
    return dst


# Two proxies render side by side: one ffmpeg keeps neither the CPU nor the
# card busy - decoding a frame waits on reading, encoding on decoding. Four
# 20 s clips of 4K 4:2:2 H.264, Ryzen 9 9900X with an RTX 5070 Ti
# (2026-10-26): decoded on the CPU for NVENC 16.8 s one at a time, 11.7 s
# two at once (four at once: 10.7 s); libx264 17.9 s to 13.7 s. More than
# two buys little, and leaves less of the PC for Resolve.
RENDERS_AT_ONCE = 2


def render_many(ffmpeg_path, targets, resolution, codec, report, run=None, at_once=RENDERS_AT_ONCE):
    """Renders every target's proxy ({id, path, dst, tc}), `at_once` at a
    time, and returns [{id, ok, error?}] in the targets' order.

    report(stage, done, total) is the job's, and is only ever called on
    this thread: after Cancel it raises, and the renders not yet begun see
    report.stopped() and never start. Cancel has already killed the ones
    running; anything that slipped in between is killed on the way out."""
    total = len(targets)
    results = [None] * total
    running = []            # the file names rendering now, for the progress line
    lock = threading.Lock()
    stopped = getattr(report, "stopped", lambda: False)

    def one(index):
        target = targets[index]
        if stopped():
            return
        name = os.path.basename(target["path"])
        with lock:
            running.append(name)
        try:
            render_one(ffmpeg_path, target["path"], target["dst"], resolution, codec,
                       run=run, timecode=target.get("tc", ""))
            results[index] = {"id": target["id"], "ok": True}
        except ProxyError as exc:
            results[index] = {"id": target["id"], "ok": False, "error": str(exc)}
        finally:
            with lock:
                running.remove(name)

    if not targets:
        return []
    pool = ThreadPoolExecutor(max_workers=max(1, min(at_once, total)))
    try:
        pending = {pool.submit(one, index) for index in range(total)}
        done, stage, shown = 0, f"Rendering {os.path.basename(targets[0]['path'])}", None
        while pending:
            with lock:
                if running:
                    stage = "Rendering " + ", ".join(running)
            if (stage, done) != shown:
                report(stage, done, total)
                shown = (stage, done)
            finished, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in finished:
                future.result()     # a bug in here, not a clip that failed: let it out
            done += len(finished)
        report(stage, total, total)     # a Cancel during the last render still unwinds the job
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        terminate_active()
        raise
    finally:
        pool.shutdown(wait=True)
    return results


# ------------------------------------------------------------ status

STATUS_ROWS_MAX = 3000  # rows Proxy management lists (and can act on); the counts cover them all

# Proxy management's "for every clip" switch: the project setting behind
# Resolve's Playback > Proxy Handling (perfProxyMediaMode; the scripting
# README: 0 disabled, 1 when available, 2 when source not available).
PLAYBACK_MODES = {"proxies": "1", "originals": "0", "offline": "2"}
PLAYBACK_LABELS = {"proxies": "proxies", "originals": "camera originals",
                   "offline": "camera originals, proxies only where the original is offline"}


def playback_mode(value):
    """The PLAYBACK_MODES key for a perfProxyMediaMode value, or None."""
    value = str(value if value is not None else "").strip()
    return next((key for key, v in PLAYBACK_MODES.items() if v == value), None)


def clip_type(entry):
    """What kind of footage a clip is, for Proxy management's filter - its
    codec in a few words, the way it decides how heavy playback is:
    "H.264 4:2:2", "H.265 10-bit", "ProRes RAW HQ", "XAVC Intra 4:2:2".
    The file's extension when Resolve names no codec."""
    codec = re.sub(r"\s+L\d+(\.\d+)?$", "", (entry.get("codec") or "").strip())   # the level: no help here
    codec = re.sub(r"^(Apple|Avid|Sony|Blackmagic|GoPro)\s+", "", codec)
    h26x = re.match(r"^(H\.26[45])\b(.*)$", codec)
    if h26x:
        rest = h26x.group(2)
        parts = [h26x.group(1)]
        if "4:2:2" in rest:
            parts.append("4:2:2")
        elif "4:4:4" in rest:
            parts.append("4:4:4")
        if re.search(r"\b10\b", rest):
            parts.append("10-bit")
        return " ".join(parts)
    if codec:
        return codec
    ext = os.path.splitext(entry.get("path") or "")[1].lstrip(".").upper()
    return ext or "Other"


def set_aside_key(path):
    """How a source is filed in the set-aside list: its path, case and
    slashes evened out (Windows paths)."""
    return os.path.normcase(os.path.normpath(path)) if path else ""


def known_proxy(entry, set_aside=None, root=None):
    """A proxy file on disk for a clip that has none linked: the one taken
    off it with Use camera originals (set_aside: {set_aside_key(source):
    proxy path}), else Buddy's own - under root when a folder is chosen
    (proxy_dir), else beside the source - else Resolve's (Generate Proxy
    Media writes Proxy/<name>.mov). "" if there's none."""
    src = entry.get("path") or ""
    if not src:
        return ""
    remembered = (set_aside or {}).get(set_aside_key(src))
    if remembered and os.path.isfile(remembered):
        return remembered
    stem = os.path.splitext(os.path.basename(src))[0]
    candidates = [os.path.join(proxy_dir(src, root), f"{stem}{PROXY_SUFFIX}{PROXY_EXT}")] if root else []
    beside = proxy_dir(src)
    candidates += [os.path.join(beside, f"{stem}{PROXY_SUFFIX}{PROXY_EXT}"), os.path.join(beside, f"{stem}{PROXY_EXT}")]
    for path in candidates:
        if os.path.isfile(path) and os.path.normcase(path) != os.path.normcase(src):
            return path
    return ""


def proxy_state(entry, set_aside=None, root=None):
    """"linked", "offline", "original" (no proxy linked, but one on disk
    to link - set aside, or made and never linked) or "none". Resolve goes
    on reporting a proxy whose file has gone as linked (tested: 21.1), so
    Buddy looks for the file itself."""
    path = entry.get("proxy_path") or ""
    if not path and entry.get("proxy", "None") in ("", "None"):
        return "original" if known_proxy(entry, set_aside, root) else "none"
    return "linked" if path and os.path.isfile(path) else "offline"


def status_report(videos, set_aside=None, root=None):
    """What Proxy management lists for a scope's video clips:
    {"counts": {linked, offline, original, none}, "rows": [{id, name, type,
    state, detail}], "more": how many rows weren't listed}. Offline first,
    then none, then the set aside, then linked - the ones that need
    something done at the top."""
    order = {"offline": 0, "none": 1, "original": 2, "linked": 3}
    rows = []
    for entry in videos:
        state = proxy_state(entry, set_aside, root)
        detail = {"linked": f"{entry.get('proxy') or ''}  {entry.get('proxy_path') or ''}".strip(),
                  "offline": entry.get("proxy_path") or "", "none": "",
                  "original": known_proxy(entry, set_aside, root)}[state]
        rows.append({"id": entry["id"], "name": entry["name"], "type": clip_type(entry),
                     "state": state, "detail": detail})
    rows.sort(key=lambda r: (order[r["state"]], r["name"].lower()))
    counts = {state: sum(1 for r in rows if r["state"] == state) for state in order}
    return {"counts": counts, "rows": rows[:STATUS_ROWS_MAX], "more": max(0, len(rows) - STATUS_ROWS_MAX)}


def status_line(counts):
    """The Activity log's one-line summary of a status check."""
    line = (f"Proxies: {counts['linked']} linked, {counts['offline']} offline, "
            f"{counts['none']} without one.")
    if counts.get("original"):
        line = line[:-1] + f", {counts['original']} on the camera original with a proxy ready."
    return line


RELINK_WALK_MAX = 200_000   # files looked at in the chosen folder before giving up


def _same_tail(a, b):
    """How many folders at the ends of two folder paths are the same."""
    def parts(path):
        return [p.lower() for p in re.split(r"[\\/]+", path or "") if p][::-1]
    n = 0
    for x, y in zip(parts(a), parts(b)):
        if x != y:
            break
        n += 1
    return n


def find_relinks(offline, folder, walk=os.walk):
    """Where each offline proxy has gone: [(entry, new path)] for the ones
    found in `folder` or below, by the proxy's own file name (or, failing
    that, the name Buddy would give it). Two cards' C0001_proxy.mov can
    both be there, so of the files with the right name, the one whose
    folders end the most like the old file's wins. Runs on the worker - a
    big drive takes a while to walk."""
    wanted = {}
    for entry in offline:
        names = {os.path.basename(entry.get("proxy_path") or "")}
        stem = os.path.splitext(os.path.basename(entry.get("path") or ""))[0]
        if stem:
            names.add(f"{stem}{PROXY_SUFFIX}{PROXY_EXT}")
        for name in names - {""}:
            wanted.setdefault(name.lower(), []).append(entry)
    found, seen = {}, 0      # id -> (how alike its folders are, entry, path)
    for root, dirs, files in walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            seen += 1
            for entry in wanted.get(name.lower(), ()):
                score = _same_tail(root, os.path.dirname(entry.get("proxy_path") or ""))
                if entry["id"] not in found or score > found[entry["id"]][0]:
                    found[entry["id"]] = (score, entry, os.path.join(root, name))
        # Done early only once every proxy has turned up somewhere that
        # looks like where it was (two folders alike).
        if seen >= RELINK_WALK_MAX or (len(found) == len(offline) and all(s >= 2 for s, _e, _p in found.values())):
            break
    return [(entry, path) for _score, entry, path in found.values()]


def relink_report(relinked, refused, missing):
    lines = []
    if relinked == 1:
        lines.append(("Relinked 1 offline proxy.", "success"))
    elif relinked:
        lines.append((f"Relinked {relinked} offline proxies.", "success"))
    for name in refused:
        lines.append((f"Resolve wouldn't link the proxy found for \"{name}\".", "error"))
    if missing == 1:
        lines.append(("1 offline proxy wasn't in that folder.", "warn"))
    elif missing:
        lines.append((f"{missing} offline proxies weren't in that folder.", "warn"))
    return lines


# --------------------------------------------------------------- reporting

def render_report(results):
    """Activity lines for the worker's per-clip results, in run order:
    one summary line, then a line per failure. A killed render (cancel)
    never reaches here - the job reports itself cancelled instead."""
    ok = [r for r in results if r["ok"]]
    lines = []
    if ok:
        if len(ok) == 1 and len(results) == 1:
            lines.append(("Rendered 1 proxy.", "success"))
        else:
            lines.append((f"Rendered {len(ok)} of {len(results)} proxies.", "success"))
    for r in results:
        if not r["ok"]:
            lines.append((str(r["error"]), "error"))
    return lines


def link_report(linked, refused, existing, redone=0, refused_existing=()):
    """Activity lines for the link pass: linked count, then one line per
    clip Resolve refused, then how many files already existed - and how
    many of those had the wrong timecode and were rendered again."""
    lines = []
    if linked == 1:
        lines.append(("Linked 1 clip to its proxy.", "success"))
    elif linked:
        lines.append((f"Linked {linked} clips to their proxies.", "success"))
    for name in refused:
        lines.append((f"Resolve wouldn't link \"{name}\".", "error"))
    for name in refused_existing:
        lines.append((f"Resolve wouldn't link the proxy file already there for \"{name}\" – delete it from "
                      "the Proxy folder to render it again.", "error"))
    if redone == 1:
        lines.append(("1 proxy file already there had the wrong timecode – rendered again.", "info"))
    elif redone:
        lines.append((f"{redone} proxy files already there had the wrong timecode – rendered again.", "info"))
    if existing == 1:
        lines.append(("1 clip already had a proxy file – linked to it instead of rendering.", "info"))
    elif existing:
        lines.append((f"{existing} clips already had proxy files – linked instead of rendering.", "info"))
    return lines