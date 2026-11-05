#!/usr/bin/env python3
"""
Auto Switch - one of the Podcast tab's sub-tabs (pages/podcast/page.py):
cuts between the cameras of a podcast's timeline by who's speaking.

The open timeline - usually the stacked one Podcast Sync builds, a video
track per camera and an audio track per mic - is read (resolve_ext), every
mic's file measured (Audio Assistant's peaks.py, its cache shared), and
who talks when worked out (talk.py). Where more than one person spoke into
a mic, their voices are told apart on this computer (voice_worker.py in the
speech engine, cluster.py).

The view is three steps. 1: each camera (its file and a still of it)
gets the synced audio it's heard through. 2: the voices found are named
and each given a camera. 3: the cameras left - a wide, a second angle, a
random cutaway, or not used - and Build. The numbers (shortest shot,
reaction shots, cutaway timings...) are behind Cut settings.

The cameras come from a saved setup (rig.py), kept by name and found again
on each timeline (by track names, then in order; a timeline's own choices
are kept with it). A camera with audio paired or a voice given is a
speaker's camera; the rest have the use step 3 gives them. The cut plan
(cutplan.py) shows as a strip, and Build makes a NEW timeline, "<name>
(Auto Switch)": every camera still on its own track, razored at each
switch, the clips not on screen turned off - so any shot can be swapped by
turning another on - and a camera heard through paired audio has its own
sound cut the same and turned off. The open timeline is never changed.

Protocol:
    to the view    state, setup, voices, plan, cut, toast, alert
    from the view  analyse, people, map, name, cut, seek, build, cancel,
                   download, engine, setup_pick, setup_new, setup_copy,
                   setup_rename, setup_delete, cam, switch_tab
"""

import os
import time
import zlib

import numpy as np

from core import busy_job
from core.resolve_reader import ResolveCalls
from core.resolve_worker import ResolveWorker
from core.settings_dialog import refresh_open
from core import settings_form as sf
from core.web_page import WebToolPage
from pages.audio_assistant.peaks import LOUD_BLOCK_S, PeakLoader, fold as peaks_fold

from . import cutplan, jobs, resolve_ext, rig, talk as talk_mod

DEFAULTS = {"options": dict(cutplan.DEFAULTS), "timelines": {}, "setups": {}, "setup": ""}
MOST_TIMELINES = 30         # the pinned cameras etc. kept for this many timelines
PEOPLE = ("1", "auto", "2", "3", "4", "5", "6")
HOLD_S = 0.5
WRITE_WAIT_S = 5.0
PREVIEW_RUNS = 4000         # each voice's talk drawn as at most this many runs


def _short_name(name, kind, index):
    default = f"{'Audio' if kind == 'audio' else 'Video'} {index}"
    letter = "A" if kind == "audio" else "V"
    return f"{letter}{index}" if not name or name == default else f"{letter}{index} {name}"


def _files(clips):
    """The file a track plays - its first clip's ("A001.MOV"), "+ n" more."""
    names = []
    for clip in clips:
        path = (clip.get("media") or {}).get("path") or clip.get("file") or clip.get("name") or ""
        name = os.path.basename(str(path).replace("\\", "/")) or str(clip.get("name") or "")
        if name and name not in names:
            names.append(name)
    if not names:
        return ""
    return names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"


def _words(text):
    return {w for w in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if len(w) > 1}


class AutoSwitchPage(WebToolPage):
    tool_id = "podcast_switch"
    display_name = "Auto Switch"
    category = "Podcast"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def on_switch_tab(self, payload):
        self._podcast.show_tab((payload or {}).get("tab"))

    # ------------------------------------------------------------ state --

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id, {k: (dict(v) if isinstance(v, dict) else v)
                                                               for k, v in DEFAULTS.items()})
        # Before setups, the wide's options were global: kept to start a first setup's wide with.
        saved = self.settings.get("options")
        self._legacy = {k: saved[k] for k in ("crosstalk", "quiet", "cutaway") if isinstance(saved, dict) and k in saved}
        self.opts = cutplan.options(saved)
        self.setups = rig.clean_setups(self.settings.get("setups"))
        self.data = None             # the timeline read: Audio Assistant's audio data + "video"
        self.problem = ""
        self.stage = ""              # "", reading, measuring, listening, grouping, building, downloading
        self.stage_text = ""
        self.progress = -1.0
        self._media_cache = {}
        self._peaks = {}             # file key -> peaks.decode()'s result (None: unreadable)
        self._peak_errors = {}
        self._audio = {}             # clip peaks key -> {"loud", "block"}
        self._waiting = set()        # file keys still decoding for this analysis
        self.talk = None
        self.prints = {}             # mic track -> {"windows", "prints"} (voices told apart)
        self.labels = {}             # mic track -> each print's voice
        self.voices = []             # [{"id", "track", "label", "row", "share", "sample"}]
        self.shots = []
        self._job = None
        self._group = None
        self._download = None
        self._download_text = ""
        self._worker = ResolveWorker(self)
        self._calls = ResolveCalls(self, self._worker)
        self._loader = PeakLoader(self)
        self._loader.ready.connect(self._on_peaks)
        self._loader.progress.connect(self._on_peak_progress)

    def web_ready(self):
        self._push_cut()
        self._push_state()
        if self.data is not None:
            self._push_setup()
            self._push_voices()
            self._push_plan()

    def on_shown(self):
        """The timeline's read as the tab opens - no button to press first."""
        if self.data is None and not self.stage:
            self.on_analyse()

    def working_on(self):
        if self.stage == "building":
            return "Building the timeline"
        if self.stage in ("listening", "grouping"):
            return "Telling the voices apart"
        if self._download is not None:
            return "Downloading the voice model"
        return None

    def on_app_quitting(self):
        for job in (self._job, self._download):
            if job is not None:
                job.cancel()
                job.wait(3000)
        self._loader.shutdown()

    def _push_state(self):
        self.emit("state", {
            "connected": bool(getattr(self.host, "connected", True)),
            "timeline": (self.data or {}).get("name", ""),
            "problem": self.problem,
            "stage": self.stage,
            "text": self.stage_text,
            "progress": self.progress,
            "engine": jobs.engine_installed(),
            "model": jobs.model_installed(),
            "downloading": self._download is not None,
            "download_text": self._download_text,
            "needs_voices": self._needs_voices(),
        })

    def _set_stage(self, stage, text="", progress=-1.0):
        self.stage, self.stage_text, self.progress = stage, text, progress
        self._push_state()

    # -------------------------------------------------- per-timeline memory --

    def _memory(self):
        """This timeline's choices: {"mics": {track: bool}, "people": {track:
        str}, "cameras": {voice id: camera id or None}, "names": {voice id:
        name}, "setup": setup id, "tracks": {camera id: video track or None},
        "audio": {camera id: [mic tracks]}} - an older one's "cameras" held
        tracks, and "wide"."""
        tid = (self.data or {}).get("id") or "none"
        timelines = self.settings.get("timelines")
        if not isinstance(timelines, dict):
            timelines = {}
            self.settings["timelines"] = timelines
        mem = timelines.get(tid)
        if not isinstance(mem, dict):
            mem = {"mics": {}, "people": {}, "cameras": {}, "names": {}, "tracks": {}, "audio": {},
                   "used": time.time()}
            timelines[tid] = mem
            if len(timelines) > MOST_TIMELINES:
                oldest = sorted(timelines, key=lambda k: timelines[k].get("used", 0))
                for old in oldest[:len(timelines) - MOST_TIMELINES]:
                    timelines.pop(old, None)
        for key in ("mics", "people", "cameras", "names", "tracks", "audio"):
            if not isinstance(mem.get(key), dict):
                mem[key] = {}
        mem["used"] = time.time()
        return mem

    def _save(self):
        self.settings["setups"] = self.setups
        self.settings.save()

    # ------------------------------------------------------------ setups --

    def _rig_mics(self):
        return [{"index": t["index"], "raw_name": t["raw_name"], "camera_sound": t["camera_sound"]}
                for t in self._mic_tracks()]

    def _setup_id(self):
        """The setup this timeline uses - a first one made from it if there's none."""
        mem = self._memory()
        for sid in (mem.get("setup"), self.settings.get("setup")):
            if sid in self.setups:
                mem["setup"] = sid
                return sid
        if self.setups:
            sid = next(iter(self.setups))
        else:
            sid = rig.new_setup_id(self.setups)
            self.setups[sid] = self._setup_from_timeline("My setup", legacy=True)
        mem["setup"] = self.settings["setup"] = sid
        return sid

    def _setup_from_timeline(self, name, legacy=False):
        mem = self._memory()
        wide = mem.get("wide") if legacy and isinstance(mem.get("wide"), int) else None
        setup = rig.from_timeline(self.data.get("video", []) if self.data else [], self._rig_mics(), name, wide)
        if legacy and self._legacy:
            setup["rules"] = rig.clean_rules(dict(setup["rules"], **self._legacy))
        return setup

    def _setup(self):
        return self.setups[self._setup_id()]

    def _placed(self):
        """The setup's cameras on this timeline: each camera's own fields
        plus "track" (or None: not on this timeline), "audio" (mic tracks)
        and "of_track" (an angle's speaker, as a track)."""
        if self.data is None:
            return []
        setup = self._setup()
        where = rig.place(setup, self.data.get("video", []), self._rig_mics(), self._memory())
        # Cut settings are the setup's, for every camera of a role.
        out = [dict(cam, **setup["rules"], **where[cam["id"]]) for cam in setup["cameras"]]
        track_of = {c["id"]: c["track"] for c in out}
        for cam in out:
            cam["of_track"] = track_of.get(cam["of"])
        return out

    def _settle_roles(self):
        """A camera with audio paired or a voice given is a speaker's; one
        that's neither any more is a wide if it's named like one, else not
        used - until step 3 says otherwise."""
        if self.data is None or self.talk is None:
            return
        pinned = {v for v in self._memory()["cameras"].values() if v is not None}
        names = {t["index"]: f"{t['name']} {t['file']}" for t in self._video_tracks()}
        for cam in self._placed():
            own = self._camera(cam["id"])
            if cam["audio"] or cam["id"] in pinned:
                own["role"] = "speaker"
            elif own["role"] == "speaker":
                own["role"] = "wide" if rig.looks_wide(names.get(cam["track"], "") + " " + cam["name"]) else "off"

    def _camera(self, cam_id):
        return next((c for c in self._setup()["cameras"] if c["id"] == cam_id), None)

    # ------------------------------------------------------------ reading --

    def _controller(self):
        try:
            return self.host.ensure_connected()
        except Exception as exc:  # noqa: BLE001 - Resolve isn't there: said in the view
            self.problem = str(exc) or "Not connected to Resolve."
            return None

    def on_analyse(self, _payload=None):
        if self.stage in ("reading", "measuring", "listening", "grouping", "building"):
            return
        controller = self._controller()
        if controller is None:
            return self._push_state()
        self.problem = ""
        self._set_stage("reading", "Reading the timeline")
        cache = self._media_cache
        if not self._calls.call("podswitch.read", {"fresh": True}, lambda: resolve_ext.read(controller, cache),
                                self._on_read):
            self._set_stage("")
            self.emit("toast", {"text": "Resolve is still busy – try again in a moment."})

    def _on_read(self, result, error):
        if error is not None:
            self.problem = f"Couldn't read the timeline: {error}"
            return self._set_stage("")
        data = result[0] if isinstance(result, tuple) else result
        if self.data is None or data.get("id") != self.data.get("id"):
            self.prints, self.labels = {}, {}
        self.data = data
        mem = self._memory()
        mics = self._mic_tracks()
        for track in mics:
            key = str(track["index"])
            if key not in mem["mics"]:
                mem["mics"][key] = not track["camera_sound"] and track["has_audio"]
        used = [t for t in mics if mem["mics"].get(str(t["index"]))]
        for track in mics:
            key = str(track["index"])
            if mem["people"].get(key) not in PEOPLE:
                mem["people"][key] = "auto" if len(used) == 1 and track in used else "1"
        if not [t for t in self.data.get("video", []) if t["clips"]]:
            self.problem = "This timeline has no video clips to switch between."
        self._save()
        self._push_setup()
        self._measure_files()

    def _mic_tracks(self):
        out = []
        for track in (self.data or {}).get("tracks", []):
            clips = [c for c in track["clips"] if not c.get("transition") and c.get("media")]
            out.append({
                "index": track["index"],
                "name": _short_name(track.get("name", ""), "audio", track["index"]),
                "raw_name": str(track.get("name") or ""),
                "has_audio": bool(clips),
                "file": _files(clips),
                "camera_sound": bool(clips) and all(resolve_ext.is_camera_sound(c["media"]["path"]) for c in clips),
                "clips": clips,
            })
        return out

    def _used_mics(self):
        mem = self._memory()
        return [t for t in self._mic_tracks() if mem["mics"].get(str(t["index"])) and t["has_audio"]]

    def _measure_files(self):
        self._waiting = set()
        for track in self._used_mics():
            for clip in track["clips"]:
                key = clip["media"]["key"]
                if key not in self._peaks:
                    self._waiting.add(key)
        if not self._waiting:
            return self._measured()
        self._set_stage("measuring", "Measuring the mics", 0.0)
        for track in self._used_mics():
            for clip in track["clips"]:
                key = clip["media"]["key"]
                if key in self._waiting:
                    self._loader.want(key, clip["media"]["path"])

    def _on_peak_progress(self, key, fraction):
        if self.stage == "measuring":
            left = self._loader.pending()
            self._set_stage("measuring", f"Measuring the mics ({left} to go)" if left > 1 else "Measuring the mics",
                            fraction)

    def _on_peaks(self, key, result, error):
        self._peaks[key] = result
        if error:
            self._peak_errors[key] = error
        self._audio = {k: v for k, v in self._audio.items() if k.split(":")[0] != key}
        self._waiting.discard(key)
        if self.stage == "measuring" and not self._waiting:
            self._measured()

    def _audio_of(self, clip):
        media, pk = clip.get("media"), clip.get("peaks")
        if not media or not pk:
            return None
        if pk not in self._audio:
            result = self._peaks.get(media["key"])
            if not result:
                return None
            folded, _why = peaks_fold(result, clip.get("channels"))
            if folded is None:
                return None
            self._audio[pk] = {"loud": np.frombuffer(folded[1], dtype=np.float32), "block": LOUD_BLOCK_S}
        return self._audio[pk]

    def _measured(self):
        if self.data is None:
            return self._set_stage("")
        mics = [{"index": t["index"], "clips": t["clips"]} for t in self._used_mics()]
        self.talk = talk_mod.mics_talking(mics, self._audio_of, self.data["fps"], HOLD_S)
        unread = [k for k in {c["media"]["key"] for t in self._used_mics() for c in t["clips"]}
                  if not self._peaks.get(k)]
        if unread:
            self.emit("toast", {"text": f"{len(unread)} mic file(s) couldn't be read and are left out."})
        self._set_stage("")
        self._listen_or_group()

    # ------------------------------------------------------------- voices --

    def _shared_mics(self):
        """Mic tracks set to more than one person (or to find out)."""
        mem = self._memory()
        return [t for t in self._used_mics() if mem["people"].get(str(t["index"]), "1") != "1"]

    def _needs_voices(self):
        if self.data is None:
            return False
        return bool(self._shared_mics()) and not (jobs.engine_installed() and jobs.model_installed())

    def _listen_or_group(self):
        shared = [t for t in self._shared_mics() if t["index"] not in self.prints]
        if shared:
            if not jobs.engine_installed() or not jobs.model_installed():
                self._make_voices()
                return self._push_state()
            row_of = {index: row for row, index in enumerate(self.talk.order)}
            clips = {}
            for track in shared:
                if track["index"] not in row_of:
                    continue
                spans = talk_mod.file_spans(self.talk, row_of[track["index"]], track["clips"], self.data["fps"])
                clips[track["index"]] = [{"id": c["id"], "path": c["media"]["path"], "spans": spans[c["id"]]}
                                         for c in track["clips"] if c["id"] in spans]
            clips = {k: v for k, v in clips.items() if v}
            if clips:
                self._set_stage("listening", "Telling the voices apart", 0.0)
                self._job = jobs.VoiceJob(clips, self)
                self._job.progressed.connect(self._on_listen_progress)
                self._job.succeeded.connect(self._on_listened)
                self._job.failed.connect(self._on_listen_failed)
                self._job.start()
                return
        self._group_voices()

    def _on_listen_progress(self, text, fraction):
        self._set_stage("listening", text or self.stage_text, fraction if fraction >= 0 else self.progress)

    def _on_listened(self, result):
        self._finish_job()
        self.prints.update(result)
        self._group_voices()

    def _on_listen_failed(self, error):
        self._finish_job()
        self._set_stage("")
        if error:
            self.emit("alert", {"title": "Couldn't tell the voices apart", "text": error})
        self._make_voices()

    def _finish_job(self):
        job, self._job = self._job, None
        if job is not None:
            job.wait(2000)
            job.deleteLater()

    def _group_voices(self):
        mem = self._memory()
        wanted = {}
        for track in self._shared_mics():
            if track["index"] in self.prints:
                value = mem["people"].get(str(track["index"]), "auto")
                wanted[track["index"]] = None if value == "auto" else int(value)
        if not wanted:
            return self._make_voices()
        self._set_stage("grouping", "Grouping the voices")
        self._group = jobs.GroupJob({t: self.prints[t] for t in wanted}, wanted, self)
        self._group.succeeded.connect(self._on_grouped)
        self._group.failed.connect(self._on_group_failed)
        self._group.start()

    def _end_group(self):
        group, self._group = self._group, None
        if group is not None:
            group.wait(2000)
            group.deleteLater()

    def _on_grouped(self, labels):
        self._end_group()
        self.labels.update(labels)
        self._set_stage("")
        self._make_voices()

    def _on_group_failed(self, error):
        self._end_group()
        self._set_stage("")
        self.emit("alert", {"title": "Couldn't group the voices", "text": error})
        self._make_voices()

    def _make_voices(self):
        """self.voices from the talk: a voice per mic - or, on a shared mic
        whose voices are told apart, one per voice found."""
        self.voices = []
        talk, fps = self.talk, self.data["fps"]
        if talk is None or not talk.length:
            self._push_voices()
            return self._replan()
        mem = self._memory()
        names = {t["index"]: t for t in self._mic_tracks()}
        shared = {t["index"] for t in self._shared_mics()}
        total = max(1, int(talk.talk.any(axis=0).sum()))
        for row, index in enumerate(talk.order):
            track = names[index]
            labels = self.labels.get(index)
            if index in shared and labels is not None and index in self.prints and len(labels):
                rows = talk_mod.voices_on_mic(talk, row, track["clips"], self.prints[index]["windows"], labels,
                                              fps, HOLD_S)
                for k, voice_row in enumerate(rows):
                    self.voices.append(self._voice(f"{index}:{k}", index, f"{track['name']} · Voice {k + 1}",
                                                   voice_row, total))
            else:
                self.voices.append(self._voice(str(index), index, track["name"], talk.talk[row], total))
        self.voices = [v for v in self.voices if v["share"] > 0 or v["id"].count(":") == 0]
        self._default_cameras(mem)
        self._settle_roles()
        self._push_setup()
        self._push_voices()
        self._replan()

    def _voice(self, vid, track, label, row, total):
        runs = talk_mod.runs_seconds(self.talk, row)
        longest = max(runs, key=lambda r: r[1] - r[0], default=None)
        fps = self.data["fps"]
        sample = int(round((longest[0] + min(0.5, (longest[1] - longest[0]) / 2)) * fps)) if longest else None
        return {"id": vid, "track": track, "label": label, "row": np.asarray(row, dtype=bool),
                "share": round(float(np.asarray(row).sum()) / total, 3), "sample": sample}

    def _video_tracks(self):
        """The video tracks with clips: their file (the first clip's, and how
        many more), a still of it, and a label to show them by."""
        out = []
        for t in (self.data or {}).get("video", []):
            if not t["clips"]:
                continue
            file = _files(t["clips"])
            out.append({"index": t["index"], "name": _short_name(t.get("name", ""), "video", t["index"]),
                        "file": file, "label": f"{file} · V{t['index']}", "still": t.get("still") or ""})
        return out

    def _default_cameras(self, mem):
        """Voices with no camera yet: the speaker camera their mic is linked
        to (a shared mic's voices, its cameras in turn), else the one named
        like their mic, else the next free speaker camera. An older Auto
        Switch's camera (a track) becomes the camera on that track."""
        placed = [c for c in self._placed() if c["track"] is not None]
        speakers = [c for c in placed if c["role"] in ("speaker", "off")]
        on_track = {c["track"]: c["id"] for c in placed}
        for vid, value in list(mem["cameras"].items()):
            if isinstance(value, int) and not isinstance(value, bool):
                mem["cameras"][vid] = on_track.get(value)
        taken = {v for v in mem["cameras"].values() if v is not None}
        linked = {}
        for voice in self.voices:
            if voice["id"] in mem["cameras"]:
                continue
            mine = [c["id"] for c in speakers if voice["track"] in c["audio"]]
            k = linked.get(voice["track"], 0)
            match = mine[k % len(mine)] if mine else None
            linked[voice["track"]] = k + 1
            if match is None:
                mic_words = rig.words(voice["label"].split("·")[0]) - {f"a{voice['track']}"}
                match = next((c["id"] for c in speakers if c["id"] not in taken
                              and mic_words & (rig.words(c["name"]) - {f"v{c['track']}"})), None)
            if match is None:
                match = next((c["id"] for c in speakers if c["id"] not in taken), None)
            mem["cameras"][voice["id"]] = match
            if match is not None:
                taken.add(match)
        self._save()

    # -------------------------------------------------------------- plan --

    def _span(self):
        cams = self._camera_tracks()
        clips = [c for t in (self.data or {}).get("video", []) if t["index"] in cams for c in t["clips"]]
        if not clips:
            return None
        return min(c["start"] for c in clips), max(c["end"] for c in clips)

    def _camera_tracks(self):
        return sorted({c["track"] for c in self._placed() if c["track"] is not None})

    def _voice_cams(self, placed):
        """Each voice's camera, as a track (None: no speaker camera here)."""
        mem = self._memory()
        speaker = {c["id"]: c["track"] for c in placed if c["role"] == "speaker" and c["track"] is not None}
        return [speaker.get(mem["cameras"].get(v["id"])) for v in self.voices]

    def _replan(self):
        self.shots = []
        if self.data is None or self.talk is None:
            return self._push_plan()
        span = self._span()
        if span is None:
            return self._push_plan()
        placed = self._placed()
        seed = zlib.crc32(str(self.data.get("id", "")).encode())
        self.shots = cutplan.plan([v["row"] for v in self.voices], self._voice_cams(placed), placed, self.talk,
                                  self.data["fps"], span, self.opts, seed)
        self._push_plan()

    # -------------------------------------------------------------- view --

    def _push_setup(self):
        tracks = {t["index"]: t for t in self._video_tracks()}
        cams = []
        for cam in self._placed():
            track = tracks.get(cam["track"])
            cams.append({k: cam[k] for k in ("id", "name", "role", "track", "audio", "of", "person")} | {
                "file": track["file"] if track else "", "label": track["label"] if track else cam["name"],
                "still": track["still"] if track else ""})
        self.emit("setup", {
            "audio": [{"index": t["index"], "label": f"{t['file']} · A{t['index']}"}
                      for t in self._mic_tracks() if t["has_audio"] and not t["camera_sound"]],
            "cameras": cams,
            "setups": [{"id": k, "name": v["name"]} for k, v in self.setups.items()],
            "setup": self._setup_id() if self.data else "",
        })

    def _voice_name(self, voice, mem):
        """What a voice is called: the name given, or its camera's person's."""
        given = mem["names"].get(voice["id"])
        if given:
            return given
        cam = self._camera(mem["cameras"].get(voice["id"])) if self.data else None
        alone = cam is not None and list(mem["cameras"].values()).count(cam["id"]) == 1
        return cam["person"] if alone else ""

    def _push_voices(self):
        mem = self._memory()
        mics = {t["index"]: t for t in self._mic_tracks()}
        groups = []
        for v in self.voices:
            if not groups or groups[-1]["index"] != v["track"]:
                mic = mics.get(v["track"], {})
                groups.append({"index": v["track"], "label": f"{mic.get('file', '')} · A{v['track']}",
                               "people": mem["people"].get(str(v["track"]), "1")})
        self.emit("voices", {"mics": groups, "voices": [
            {"id": v["id"], "track": v["track"], "which": v["label"].split(" · ")[1] if " · " in v["label"] else "",
             "name": self._voice_name(v, mem), "share": v["share"], "sample": v["sample"],
             "camera": mem["cameras"].get(v["id"])} for v in self.voices]})

    def _push_cut(self):
        rules = self._setup()["rules"] if self.data else rig.clean_setup({})["rules"]
        self.emit("cut", dict(self.opts, **rules))

    def _push_plan(self):
        span = self._span() if self.data else None
        if not self.shots or span is None:
            return self.emit("plan", {"shots": [], "rows": []})
        fps = self.data["fps"]
        rows = []
        for v in self.voices:
            runs = talk_mod.runs_seconds(self.talk, v["row"])[:PREVIEW_RUNS]
            rows.append({"id": v["id"], "label": self._voice_name(v, self._memory()) or v["label"],
                         "runs": [[int(round(a * fps)), int(round(b * fps))] for a, b in runs]})
        self.emit("plan", {"span": list(span), "fps": fps, "start": self.data["start"],
                           "shots": [s.as_dict() for s in self.shots], "rows": rows,
                           "summary": cutplan.summary(self.shots, fps)})

    # ----------------------------------------------------------- actions --

    def on_people(self, payload):
        payload = payload or {}
        value = str(payload.get("value"))
        if value not in PEOPLE:
            return
        mem = self._memory()
        mem["people"][str(payload.get("track"))] = value
        # Its voices are new: their cameras are chosen again.
        prefix = f"{payload.get('track')}"
        mem["cameras"] = {k: v for k, v in mem["cameras"].items() if k != prefix and not k.startswith(prefix + ":")}
        self._save()
        self._push_setup()
        if self.talk is not None and not self.stage:
            self._listen_or_group()

    def on_map(self, payload):
        """A voice's camera (step 2) - any camera; it's a speaker's camera then."""
        payload = payload or {}
        camera = payload.get("camera")
        if self.data is None:
            return
        mem = self._memory()
        mem["cameras"][str(payload.get("voice"))] = camera if self._camera(camera) is not None else None
        self._settle_roles()
        self._changed()

    def on_name(self, payload):
        """A voice's name (step 2): kept for this timeline, and as its
        camera's person, so the next episode on the setup starts with it."""
        payload = payload or {}
        vid = str(payload.get("voice"))
        if self.data is None or not any(v["id"] == vid for v in self.voices):
            return
        name = " ".join(str(payload.get("name") or "").split())[:rig.MAX_NAME]
        mem = self._memory()
        mem["names"][vid] = name
        cam = self._camera(mem["cameras"].get(vid))
        if cam is not None and list(mem["cameras"].values()).count(cam["id"]) == 1:
            cam["person"] = name
        self._changed()

    def on_cut(self, payload):
        """One of Cut settings: a global option, or a rule for a role's cameras."""
        payload = payload or {}
        key, value = payload.get("key"), payload.get("value")
        if key in cutplan.DEFAULTS:
            self.opts = cutplan.options(dict(self.opts, **{key: value}))
            self.settings["options"] = dict(self.opts)
        elif key in rig.NUMBERS or key in rig.FLAGS:
            if self.data is None:
                return
            setup = self._setup()
            setup["rules"] = rig.clean_rules(dict(setup["rules"], **{key: value}))
        else:
            return
        self._save()
        self._push_cut()
        self._replan()

    def _changed(self, pins=False):
        """The setup changed: saved, shown, planned again. pins: voices
        pinned to a camera that's no longer a speaker's lose it, and voices
        with none are pinned again."""
        if self.data is None:
            return self._save()
        mem = self._memory()
        if pins:
            people = {c["id"] for c in self._setup()["cameras"] if c["role"] in ("speaker", "off")}
            mem["cameras"] = {k: v for k, v in mem["cameras"].items() if v in people}
            self._default_cameras(mem)
            self._settle_roles()
        self._save()
        self._push_setup()
        self._push_voices()
        self._replan()

    def _switch_setup(self, sid):
        mem = self._memory()
        mem["setup"] = self.settings["setup"] = sid
        for key in ("cameras", "names", "tracks", "audio"):   # another setup's camera ids
            mem[key] = {}
        self._changed(pins=True)

    def _name(self, payload, fallback):
        return " ".join(str((payload or {}).get("name") or "").split())[:rig.MAX_NAME] or fallback

    def on_setup_pick(self, payload):
        sid = (payload or {}).get("id")
        if sid in self.setups and self.data is not None and sid != self._setup_id():
            self._switch_setup(sid)

    def on_setup_new(self, payload):
        """A new setup, started from the open timeline's tracks."""
        if self.data is None or len(self.setups) >= rig.MAX_SETUPS:
            return
        sid = rig.new_setup_id(self.setups)
        self.setups[sid] = self._setup_from_timeline(self._name(payload, f"Setup {len(self.setups) + 1}"))
        self._switch_setup(sid)

    def on_setup_copy(self, payload):
        """The open setup again under a new name - same cameras, same choices here."""
        if self.data is None or len(self.setups) >= rig.MAX_SETUPS:
            return
        current = self._setup()
        sid = rig.new_setup_id(self.setups)
        self.setups[sid] = rig.clean_setup(dict(current, name=self._name(payload, current["name"])))
        mem = self._memory()
        mem["setup"] = self.settings["setup"] = sid
        self._changed()

    def on_setup_rename(self, payload):
        if self.data is not None:
            self._setup()["name"] = self._name(payload, self._setup()["name"])
            self._changed()

    def on_setup_delete(self, _payload=None):
        if self.data is None or len(self.setups) < 2:
            return
        sid = self._setup_id()
        self.setups.pop(sid, None)
        for mem in (self.settings.get("timelines") or {}).values():
            if isinstance(mem, dict) and mem.get("setup") == sid:
                mem.pop("setup", None)
        self._switch_setup(next(iter(self.setups)))

    def _track_name(self, index):
        track = next((t for t in (self.data or {}).get("video", []) if t["index"] == index), None)
        return str(track.get("name") or "") if track else ""

    def on_cam(self, payload):
        """One of a camera's fields: audio (step 1: the mic tracks it's heard
        through), role and of (step 3: what it's for, an angle's speaker
        camera), name or track."""
        payload = payload or {}
        cam, key, value = self._camera(payload.get("id")) if self.data else None, payload.get("key"), payload.get("value")
        if cam is None:
            return
        mem = self._memory()
        pins = False
        if key == "track":
            valid = {t["index"] for t in self._video_tracks()}
            track = value if value in valid else None
            for other in self._placed():                    # one camera to a track
                if other["id"] != cam["id"] and track is not None and other["track"] == track:
                    mem["tracks"][other["id"]] = None
            mem["tracks"][cam["id"]] = track
            cam["track_name"] = self._track_name(track)
            pins = True
        elif key == "audio":
            mics = {t["index"]: t for t in self._mic_tracks()}
            tracks = sorted({a for a in value if a in mics}) if isinstance(value, list) else []
            before = set(next((c["audio"] for c in self._placed() if c["id"] == cam["id"]), []))
            mem["audio"][cam["id"]] = tracks
            cam["audio_names"] = [mics[a]["raw_name"] for a in tracks if mics[a]["raw_name"]]
            if tracks:
                cam["role"] = "speaker"
            # The voices of a mic just linked are this camera's now (a speaker's);
            # its other voices are pinned again - and a linked mic is listened to.
            added = {str(a) for a in set(tracks) - before}
            mem["cameras"] = {k: v for k, v in mem["cameras"].items()
                              if v != cam["id"] and not (cam["role"] == "speaker" and k.split(":")[0] in added)}
            listen = [a for a in tracks if mics[a]["has_audio"] and not mem["mics"].get(str(a))]
            for a in listen:
                mem["mics"][str(a)] = True
            if listen:                                       # measured, then its voices are pinned
                self._save()
                self._push_setup()
                return None if self.stage else self._measure_files()
            pins = True
        elif key in ("name", "role", "of"):
            fresh = rig.clean_camera(dict(cam, **{key: value}))
            if key == "of" and (value == cam["id"] or self._camera(value) is None):
                fresh["of"] = None
            if key == "role" and fresh["role"] != "speaker":       # not a person's camera: no voices
                mem["cameras"] = {k: v for k, v in mem["cameras"].items() if v != cam["id"]}
                mem["audio"][cam["id"]] = []
                cam["audio_names"] = []
            cam.update(fresh)
        else:
            return
        self._changed(pins)

    def on_seek(self, payload):
        try:
            frame = int((payload or {}).get("frame"))
        except (TypeError, ValueError):
            return
        controller = self._controller()
        if controller is None:
            return self._push_state()
        tid = (self.data or {}).get("id", "")

        def here():
            from pages.audit.resolve_ext import goto
            return goto(controller, frame, tid)

        self._calls.call("audit.goto", {"frame": frame, "timeline_id": tid}, here, self._on_seeked)

    def _on_seeked(self, _result, error):
        if error is not None:
            self.emit("toast", {"text": str(error)})

    def on_build(self, _payload=None):
        if not self.shots or self.stage:
            return
        controller = self._controller()
        if controller is None:
            return self._push_state()
        if not self._calls.wait_idle(WRITE_WAIT_S):
            return self.emit("toast", {"text": "Resolve is still busy – try again in a moment."})
        self._set_stage("building", "Building the timeline")
        args = {"shots": [s.as_dict() for s in self.shots], "cameras": self._camera_tracks(),
                "timeline_id": self.data.get("id", ""),
                "muted": [c["track"] for c in self._placed() if c["track"] is not None and c["audio"]]}
        # In the Resolve reader's process: hundreds of calls, and Buddy keeps
        # drawing meanwhile (core/busy_job.py).
        busy_job.run(self.host, self._calls, "podswitch.build", args,
                     lambda: resolve_ext.build_here(controller, **args), self._on_built,
                     "Building the Auto Switch timeline…")

    def _on_built(self, result, error):
        self._set_stage("")
        if error is not None:      # said, and nothing in the open timeline changed
            return self.emit("alert", {"title": "Couldn't build the timeline", "text": str(error)})
        lines = list(result["warnings"])
        if result["failed"]:
            lines.append(f"{result['failed']} of {result['wanted']} clips not on screen couldn't be turned off – "
                         "turn them off by hand (D on the timeline).")
        if result.get("sound_failed"):
            lines.append(f"{result['sound_failed']} pieces of camera sound couldn't be turned off – turn them off "
                         "by hand (D on the timeline).")
        text = (f"“{result['name']}” is open: {len(self.shots)} shots, every camera on its own track with the "
                f"clips not on screen turned off. Your timeline is as it was.")
        if result.get("sound_off"):
            text = (f"“{result['name']}” is open: {len(self.shots)} shots, every camera on its own track with the "
                    f"clips not on screen turned off; the cameras heard through their mics have their own sound cut "
                    f"the same and turned off. Your timeline is as it was.")
        if lines:
            self.emit("alert", {"title": "Auto Switch timeline made", "text": text + "\n\n" + "\n".join(lines)})
        else:
            self.emit("toast", {"text": text})

    def on_cancel(self, _payload=None):
        if self._job is not None:
            self._job.cancel()
        if self.stage == "measuring":
            self._waiting = set()
            self._set_stage("")

    def on_engine(self, _payload=None):
        """The speech engine isn't installed: Settings' Transcription page installs it."""
        opener = getattr(self.host, "open_settings", None)
        if opener is not None:
            opener("transcribe")

    def on_download(self, _payload=None):
        if self._download is not None or not jobs.engine_installed():
            return
        self._download_text = "Starting the download"
        self._download = jobs.DownloadJob(self)
        self._download.progressed.connect(self._on_download_progress)
        self._download.succeeded.connect(self._on_downloaded)
        self._download.failed.connect(self._on_download_failed)
        self._download.start()
        self._push_state()
        refresh_open("podcast")

    def _on_download_progress(self, text):
        self._download_text = text.strip()[:160]
        self._push_state()

    def _end_download(self):
        job, self._download = self._download, None
        if job is not None:
            job.wait(2000)
            job.deleteLater()
        self._download_text = ""
        refresh_open("podcast")

    def _on_downloaded(self, _folder):
        self._end_download()
        self._push_state()
        self.emit("toast", {"text": "Voice model downloaded."})
        if self.talk is not None and not self.stage:
            self._listen_or_group()

    def _on_download_failed(self, error):
        self._end_download()
        self._push_state()
        if error:
            self.emit("alert", {"title": "Couldn't download the voice model", "text": error})

    # ---------------------------------------------------------- settings --

    def _model_row(self):
        installed = jobs.model_installed()
        busy = self._download is not None
        entry = jobs.es.VOICE_MODELS[0]
        if busy:
            actions = [{"label": "Stop", "action": "podcast_switch_stop"}]
        elif installed:
            actions = []
        else:
            actions = [{"label": f"Download ({int(entry['size_gb'] * 1000)} MB)", "action": "podcast_switch_download",
                        "disabled": not jobs.engine_installed(),
                        "tip": "" if jobs.engine_installed() else "Install the transcription engine first."}]
        return {"id": jobs.MODEL_ID, "label": entry["label"], "kind": "Voices",
                "sub": ["Voice prints", "Auto Switch"], "fit": entry["fit"],
                "size": f"{int(entry['size_gb'] * 1000)} MB", "bytes": int(entry["size_gb"] * 1e9) if installed else 0,
                "where": "local" if installed else "missing",
                "note": str(jobs.model_folder()) if installed else "",
                "chip": ({"text": "Downloading…", "tone": ""} if busy else
                         {"text": "Installed", "tone": "ok"} if installed else {"text": "Not downloaded", "tone": ""}),
                "actions": actions}

    def settings_pages(self):
        return [sf.page("podcast_switch_voices", "ai", "Voices", [
            sf.hint("Auto Switch tells apart people who spoke into the same mic with a small voice-print model, "
                    "WeSpeaker's ResNet34 (CC-BY 4.0). It runs on this computer, in the transcription engine, "
                    "and nothing is uploaded. A mic with one person on it needs no model."),
            sf.models([dict(self._model_row(), sub=[self._model_row()["fit"]])]),
        ], "Voices for Podcast's Auto Switch, on this computer.")]

    def ai_models(self):
        return [self._model_row()]

    def ai_jobs(self):
        return [{"label": "Telling voices apart", "where": "local", "page": "podcast_switch_voices",
                 "detail": "Auto Switch makes voice prints of the mics more than one person spoke into, on this "
                           "computer."}]

    def models_busy(self):
        if self._download is not None or self.stage == "listening":
            return "Auto Switch is using the voice model – try again when it's done."
        return ""

    def on_settings_action(self, action, ui):
        if action == "podcast_switch_download":
            self.on_download()
        elif action == "podcast_switch_stop" and self._download is not None:
            self._download.cancel()
