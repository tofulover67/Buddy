#!/usr/bin/env python3
"""
Text Edit - one of the Podcast tab's sub-tabs (see pages/podcast/page.py):
edit the open timeline by editing what's said in it.

The dialogue tracks are transcribed source by source (transcripts.py, the
Subtitles tool's engine, keeping "um"s and "uh"s), laid onto the timeline
from where each clip sits now (model.py), more than one mic merged into one
conversation with each person's words from their own mic (levels.py). The
view (web/) shows it as text: click a word and Resolve's playhead goes
there; drag across words and Resolve's In and Out are set on them.

Two ways to cut, a setting:
- Build an edited timeline (the default): Delete strikes words, pauses and
  fillers out in the text - struck stays readable and can be put back - and
  Build makes a NEW timeline without them (otio_cut.py). The original never
  changes; build again after more edits as often as you like.
- Cut the open timeline live: Delete sets In/Out on the words and presses
  your Ripple Delete key in Resolve (keys.py), so Resolve makes the cut,
  each one checked clip by clip and undone if it didn't come out right.

The transcript follows the timeline: an edit made in Resolve shows in the
text within a couple of seconds, with nothing re-transcribed.

Protocol:
    to the view    state, doc, playhead, job, toast, alert
    from the view  refresh, toggle_track, transcribe, cancel_transcribe, seek,
                   select, strike, restore, restore_all, remove_fillers,
                   remove_pauses, undo, redo, correct, option, save_keys, build,
                   play, switch_tab
"""

import hashlib
import os
import time

from PySide6.QtCore import QTimer

from core import atomic_io, busy_job, tool_dirs
from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveCalls
from core.resolve_worker import ResolveWorker
from core.web_page import WebToolPage

from . import keys as K
from . import levels as LV
from . import model as M
from . import otio_cut, resolve_ext, transcripts

READ_MS = 1500          # the timeline, looking for edits made in Resolve
PLAYHEAD_MS = 250
BUSY_AFTER_S = 4
HISTORY = 200
CUT_WAIT_S = 2.5        # a live cut: how long Resolve has to make it
FONTS = ("sans", "serif", "mono")
SIZES = ("small", "medium", "large", "xlarge")
DEFAULTS = {"mode": "build", "model": "auto", "language": "", "pause_over": 1.0, "back_to_resolve": False,
            "follow": True, "set_inout": True, "text_font": "sans", "text_size": "medium", "keys": {},
            "tip_seen": False}
OPTIONS = {"mode": ("build", "live"), "text_font": FONTS, "text_size": SIZES}
TOGGLES = ("back_to_resolve", "follow", "set_inout", "tip_seen")
ADVICE = ("Click once on Resolve's timeline, check that Shortcuts… matches your Resolve keyboard, and that the "
          "Auto Track Selector is on for the dialogue and picture tracks.")


class CutFailed(RuntimeError):
    """A live cut that didn't come out as it should (and was undone)."""


def _edits_file(timeline_id):
    name = hashlib.sha1(str(timeline_id).encode("utf-8")).hexdigest()[:20] + ".json"
    return os.path.join(tool_dirs.path("podcast_text"), "edits", name)


class TextEditPage(WebToolPage):
    tool_id = "podcast_text"
    display_name = "Text Edit"
    category = "Podcast"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def on_switch_tab(self, payload):
        self._podcast.show_tab((payload or {}).get("tab"))

    # ------------------------------------------------------------ state --

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id, dict(DEFAULTS))
        self.timeline = None            # resolve_ext.read_timeline()'s, or None
        self.problem = "Connecting…"
        self.waiting = False            # Resolve is holding our reads (playing)
        self.tokens = []
        self.version = 0
        self.edits = {}                 # this timeline's: audio, video, struck
        self.struck = set()
        self.undo_stack, self.redo_stack = [], []
        self.transcripts = {}           # source key -> data, this session
        self.levels = {}                # source key -> (hop, dB)
        self.failed = {}                # source key -> why it couldn't be transcribed
        self.job = None
        self.job_info = None
        self._media = {}
        self._sig = None
        self._playhead = None
        self._seek_next = None
        self._seeking = False
        self._presser = K.Presser()
        self._worker = ResolveWorker(self)
        self._calls = ResolveCalls(self, self._worker)
        self._read_timer = QTimer(self, interval=READ_MS)
        self._read_timer.timeout.connect(self._tick_read)
        self._read_timer.start()
        self._head_timer = QTimer(self, interval=PLAYHEAD_MS)
        self._head_timer.timeout.connect(self._tick_head)
        self._head_timer.start()

    def web_ready(self):
        self._push_state()
        self._push_doc()

    def on_shown(self):
        self._read(connect=True)

    def on_app_quitting(self):
        self._read_timer.stop()
        self._head_timer.stop()
        if self.job is not None:
            self.job.cancel()

    def on_connection_changed(self, connected):
        if connected and self.isVisible():
            self._read(connect=False)

    def working_on(self):
        return "Transcribing" if self.job is not None else None

    def ai_jobs(self):
        return [{"label": "Text Edit transcription", "where": "local", "page": "ai_transcription",
                 "detail": "Your dialogue is transcribed on this computer, by the Subtitles tool's models."}]

    def models_busy(self):
        return "Text Edit is transcribing – try again when it's done." if self.job is not None else ""

    def _keys(self):
        return K.load(self.settings.get("keys"))

    def _push_state(self):
        keys = self._keys()
        info = self.job_info or {}
        self.emit("state", {
            "connected": self.timeline is not None,
            "problem": self.problem,
            "waiting": self.waiting,
            "timeline": {"name": self.timeline["name"], "id": self.timeline["id"]} if self.timeline else None,
            "mode": self.settings.get("mode"),
            "model": self.settings.get("model"),
            "models": [{"value": "auto", "label": "Best installed"}] +
                      [{"value": mid, "label": label} for mid, label in transcripts.installed()],
            "language": self.settings.get("language"),
            "languages": self._languages(),
            "pause_over": float(self.settings.get("pause_over") or 1.0),
            "back_to_resolve": bool(self.settings.get("back_to_resolve")),
            "follow": bool(self.settings.get("follow")),
            "set_inout": bool(self.settings.get("set_inout")),
            "text_font": self.settings.get("text_font"),
            "text_size": self.settings.get("text_size"),
            "tip_seen": bool(self.settings.get("tip_seen")),
            "keys": keys, "preset": K.preset_of(keys),
            "presets": [{"value": k, "label": v["label"]} for k, v in K.PRESETS.items()],
            "preset_keys": {k: v["keys"] for k, v in K.PRESETS.items()},
            "commands": [{"id": c, "label": K.LABELS[c]} for c in K.COMMANDS],
            "keys_problem": K.available() if self.settings.get("mode") == "live" else "",
            "job": {"running": self.job is not None, "done": info.get("done", 0), "total": info.get("total", 0),
                    "fraction": info.get("fraction", 0.0), "file": info.get("file", "")},
            "can_undo": bool(self.undo_stack), "can_redo": bool(self.redo_stack),
        })

    @staticmethod
    def _languages():
        try:
            from pages.transcribe import plan
            return [{"value": code, "label": label} for label, code in plan.LANGUAGES]
        except Exception:  # noqa: BLE001
            return [{"value": "", "label": "Auto-detect"}]

    # ----------------------------------------------------------- Resolve --

    def _controller(self, connect):
        if connect:
            try:
                return self.host.ensure_connected()
            except ResolveConnectionError as exc:
                self.problem = str(exc)
                return None
        return self.host.controller if getattr(self.host, "connected", False) else None

    def _tick_read(self):
        if self.isVisible():
            self._read(connect=False)

    def _read(self, connect=False, fresh=False):
        controller = self._controller(connect)
        if controller is None:
            return self._lost(self.problem if connect else "Not connected to Resolve.")
        if self._calls.busy():
            if not self.waiting and self._calls.waiting_for() >= BUSY_AFTER_S:
                self.waiting = True
                self._push_state()
            return
        cache = self._media
        if fresh:
            cache.clear()
        self._calls.call("podcast_text.read", {"fresh": fresh},
                         lambda: resolve_ext.read_timeline(controller, cache), self._on_read)

    def _lost(self, problem):
        changed = self.timeline is not None or self.problem != problem
        self.timeline, self.problem, self.waiting = None, problem, False
        if changed:
            self.tokens = []
            self._push_state()
            self._push_doc()

    def _on_read(self, result, error):
        if error is not None:
            return self._lost(str(error) if isinstance(error, ResolveConnectionError)
                              else f"Couldn't read the timeline: {error}")
        was_waiting, self.waiting = self.waiting, False
        switched = self.timeline is None or self.timeline.get("id") != result.get("id")
        if switched:
            self._open_edits(result)
        changed = switched or result.get("sig") != self._sig
        self.timeline, self.problem, self._sig = result, "", result.get("sig")
        if switched or was_waiting:
            self._push_state()
        if changed:
            if not switched and not getattr(self, "_own_edit", False):
                # Edited in Resolve: the panel's live undo no longer lines up.
                self.undo_stack = [u for u in self.undo_stack if u.get("kind") != "live"]
                self.redo_stack = [u for u in self.redo_stack if u.get("kind") != "live"]
            self._own_edit = False
            self._rebuild()

    def _tick_head(self):
        if not self.isVisible() or self.timeline is None or self._calls.busy() or self.job_busy_resolve():
            return
        controller = self._controller(False)
        if controller is None:
            return
        self._calls.call("podcast_text.live", {}, lambda: resolve_ext.playhead(controller), self._on_head)

    def job_busy_resolve(self):
        return getattr(self, "_editing", False)

    def _on_head(self, result, error):
        if error is not None or not result or self.timeline is None:
            return
        if result.get("id") != self.timeline.get("id"):
            return self._read()
        frame = result.get("frame")
        if frame != self._playhead:
            self._playhead = frame
            self.emit("playhead", {"t": None if frame is None else frame / float(self.timeline["fps"])})

    # ------------------------------------------------- this timeline's edits --

    def _open_edits(self, timeline):
        data = atomic_io.read_json(_edits_file(timeline.get("id")), default=None)
        self.edits = data if isinstance(data, dict) else {}
        self.struck = {str(k) for k in self.edits.get("struck") or []}
        self.undo_stack, self.redo_stack = [], []

    def _save_edits(self):
        if self.timeline is None:
            return
        self.edits["struck"] = sorted(self.struck)
        self.edits["name"] = self.timeline.get("name", "")
        try:
            path = _edits_file(self.timeline["id"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            atomic_io.write_json(path, self.edits)
        except OSError:
            pass

    def _tracks(self):
        """(audio, video) track numbers in use: the person's choice, else model.default_tracks."""
        tl = self.timeline
        audio_d, video_d = M.default_tracks(tl)
        have_a = {t["index"] for t in tl["tracks"]["audio"]}
        have_v = {t["index"] for t in tl["tracks"]["video"]}
        audio = [t for t in self.edits.get("audio") or [] if t in have_a] if "audio" in self.edits else audio_d
        video = [t for t in self.edits.get("video") or [] if t in have_v] if "video" in self.edits else video_d
        return (audio or audio_d), video

    # ------------------------------------------------------- the transcript --

    def _language(self):
        return str(self.settings.get("language") or "")

    def _sources(self, audio):
        """{source key: (path, channels, item name)} on the dialogue tracks."""
        out = {}
        for item in self.timeline.get("items") or []:
            if item["kind"] == "audio" and item["track"] in audio and item.get("path") and not item.get("locked"):
                out.setdefault(M.source_key(item["path"], item.get("channels")),
                               (item["path"], item.get("channels"), item.get("name") or os.path.basename(item["path"])))
        return out

    def _load(self, src, path, channels):
        if src in self.transcripts:
            return self.transcripts[src]
        try:
            data = transcripts.cached(path, channels, self._language())
        except OSError:
            data = None
        if data is not None:
            self.transcripts[src] = data
            lv = M.level_array(data.get("levels")) if data.get("levels") else None
            if lv is not None:
                self.levels[src] = lv
        return data

    def _rebuild(self):
        if self.timeline is None:
            return self._push_doc()
        audio, video = self._tracks()
        sources = self._sources(audio)
        missing = []
        for src, (path, channels, name) in sources.items():
            if self._load(src, path, channels) is None:
                missing.append(name)
        words = M.collect_words(self.timeline, set(audio), self.transcripts)
        span = M.managed_span(self.timeline, set(audio))
        multi = len({w["tr"] for w in words}) > 1
        if multi:
            mics = LV.Mics(LV.track_levels(self.timeline, set(audio), self.levels, span[1]))
            words = LV.filter_words(words, mics)
        self.tokens = M.build_tokens(words, span) if (words or sources) else []
        self._paras = M.paragraphs(self.tokens, multi)
        self._ticks = M.edit_points(self.timeline, set(audio), self.tokens)
        self._missing = missing
        self._multi = multi
        self._audio, self._video = audio, video
        self.version += 1
        self._push_doc()

    def _track_label(self, kind, index):
        rows = self.timeline["tracks"]["audio" if kind == "audio" else "video"]
        name = next((t["name"] for t in rows if t["index"] == index), "")
        short = f"{'A' if kind == 'audio' else 'V'}{index}"
        default = f"{'Audio' if kind == 'audio' else 'Video'} {index}"
        return short, (name if name and name != default else "")

    def _track_file(self, kind, index):
        """The file a track plays - its first clip's ("A001_C003.MOV"), "+ n"
        more - which is what tells tracks apart, whatever they're named."""
        names = []
        for item in sorted((i for i in self.timeline["items"] if i["kind"] == kind and i["track"] == index
                            and i.get("path")), key=lambda i: i["start"]):
            name = os.path.basename(str(item["path"]).replace("\\", "/"))
            if name and name not in names:
                names.append(name)
        if not names:
            return ""
        return names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"

    def _push_doc(self):
        if self.timeline is None:
            return self.emit("doc", {"version": self.version, "timeline": None, "problem": self.problem})
        tl = self.timeline
        fps = float(tl["fps"])
        audio, video = getattr(self, "_audio", []), getattr(self, "_video", [])
        has_media = {(i["kind"], i["track"]) for i in tl["items"] if i.get("path")}
        tracks = {kind: [{"index": t["index"], "short": self._track_label(kind, t["index"])[0],
                          "name": self._track_label(kind, t["index"])[1],
                          "file": self._track_file(kind, t["index"]),
                          "on": t["index"] in (audio if kind == "audio" else video),
                          "empty": (kind, t["index"]) not in has_media}
                         for t in tl["tracks"][kind]] for kind in ("audio", "video")}
        speakers = {str(t): (self._track_label("audio", t)[1] or self._track_label("audio", t)[0]) for t in audio}
        tokens = []
        for tok in self.tokens:
            row = {"k": tok["k"], "s": tok["s"], "e": tok["e"]}
            if tok["k"] == "w":
                row.update(t=tok["t"], tr=tok["tr"])
                if tok.get("f"):
                    row["f"] = 1
                if tok.get("p") is not None and tok["p"] < M.LOW_CONFIDENCE and not tok.get("fixed"):
                    row["lo"] = 1
            if tok["key"] in self.struck:
                row["x"] = 1
            tokens.append(row)
        cuts = M.cut_frames(self.tokens, self.struck, fps)
        locked = [self._track_label(i["kind"], i["track"])[0] for i in tl["items"]
                  if i.get("locked") and i["kind"] == "audio" and i["track"] in audio]
        self.emit("doc", {
            "version": self.version, "timeline": {"name": tl["name"], "id": tl["id"]}, "problem": "",
            "tracks": tracks, "speakers": speakers, "multi": bool(getattr(self, "_multi", False)),
            "tokens": tokens, "paras": [list(p) for p in getattr(self, "_paras", [])],
            "ticks": getattr(self, "_ticks", []),
            "missing": list(getattr(self, "_missing", [])),
            "failed": [why for src, why in self.failed.items()],
            "fillers": sum(1 for t in self.tokens if t["k"] == "w" and t.get("f") and t["key"] not in self.struck),
            "struck": len(self.struck & {t["key"] for t in self.tokens}),
            "cut_seconds": round(M.cut_seconds(cuts, fps), 2), "cuts": len(cuts),
            "locked": sorted(set(locked)),
        })

    # ----------------------------------------------------------- actions --

    def on_refresh(self, _payload=None):
        self.transcripts.clear()
        self.levels.clear()
        self._sig = None
        self._read(connect=True, fresh=True)

    def on_option(self, payload):
        key, value = (payload or {}).get("key"), (payload or {}).get("value")
        if key in OPTIONS and value in OPTIONS[key]:
            self.settings[key] = value
        elif key in TOGGLES:
            self.settings[key] = bool(value)
        elif key == "pause_over":
            try:
                self.settings[key] = round(max(M.PAUSE_REMOVE_MIN, min(30.0, float(value))), 1)
            except (TypeError, ValueError):
                return
        elif key == "model" and isinstance(value, str):
            self.settings[key] = value
        elif key == "language" and isinstance(value, str) and len(value) <= 8:
            if value != self.settings.get("language"):
                self.settings[key] = value
                self.transcripts.clear()
                self.levels.clear()
                self.settings.save()
                self._rebuild()
        else:
            return
        self.settings.save()
        self._push_state()

    def on_toggle_track(self, payload):
        if self.timeline is None:
            return
        kind = (payload or {}).get("kind")
        index = (payload or {}).get("index")
        if kind not in ("audio", "video") or not isinstance(index, int):
            return
        audio, video = self._tracks()
        chosen = list(audio if kind == "audio" else video)
        if index in chosen:
            if kind == "audio" and len(chosen) == 1:
                return self.emit("toast", {"text": "Keep at least one dialogue track on."})
            chosen.remove(index)
        else:
            chosen = sorted(chosen + [index])
        self.edits[kind] = chosen
        self._save_edits()
        self._rebuild()

    def on_transcribe(self, payload=None):
        if self.job is not None or self.timeline is None:
            return
        force = bool((payload or {}).get("force"))
        audio, _video = self._tracks()
        wanted = []
        for src, (path, channels, _name) in self._sources(audio).items():
            if force or self._load(src, path, channels) is None:
                wanted.append((src, path, channels))
        if not wanted:
            return self.emit("toast", {"text": "Everything is already transcribed."})
        plan, why = transcripts.engine_plan(self.settings.get("model") or "auto", self._language())
        if plan is None:
            return self.emit("alert", {"title": "Can't transcribe yet", "text": why})
        for src, _path, _ch in wanted:
            self.failed.pop(src, None)
        self.job = transcripts.TranscribeQueue(wanted, plan, self._language(), self)
        self.job_info = {"done": 0, "total": len(wanted), "fraction": 0.0, "file": ""}
        self.job.progress.connect(self._on_job_progress)
        self.job.finished_one.connect(self._on_job_one)
        self.job.failed_one.connect(self._on_job_failed)
        self.job.finished.connect(self._on_job_end)
        self.job.start()
        self._push_state()

    def on_cancel_transcribe(self, _payload=None):
        if self.job is not None:
            self.job.cancel()

    def _on_job_progress(self, done, total, fraction, name):
        self.job_info = {"done": done, "total": total, "fraction": fraction, "file": name}
        self.emit("job", self.job_info)

    def _on_job_one(self, src, data):
        self.transcripts[src] = data
        lv = M.level_array(data.get("levels")) if data.get("levels") else None
        if lv is not None:
            self.levels[src] = lv
        self._rebuild()

    def _on_job_failed(self, src, why):
        self.failed[src] = why
        self.emit("toast", {"text": why, "kind": "error"})

    def _on_job_end(self):
        job, self.job, self.job_info = self.job, None, None
        if job is not None:
            job.deleteLater()
        self._push_state()
        self._rebuild()

    def _frame(self, seconds):
        return int(round(float(seconds) * float(self.timeline["fps"])))

    def _token(self, tid):
        return self.tokens[tid] if isinstance(tid, int) and 0 <= tid < len(self.tokens) else None

    def on_seek(self, payload):
        if self.timeline is None:
            return
        tok = self._token((payload or {}).get("id"))
        at = tok["s"] if tok is not None else (payload or {}).get("t")
        if not isinstance(at, (int, float)):
            return
        # A frame inside the word, never the one before it.
        frame = int(float(at) * float(self.timeline["fps"]) + 0.999)
        self._seek_next = frame
        self._send_seek()
        if self.settings.get("back_to_resolve") and not (payload or {}).get("keep_focus"):
            K.focus_resolve()

    def _send_seek(self):
        if self._seeking or self._seek_next is None or self.timeline is None:
            return
        controller = self._controller(False)
        if controller is None:
            return
        frame, self._seek_next, self._seeking = self._seek_next, None, True
        tid = self.timeline["id"]

        def done(_result, error):
            self._seeking = False
            if error is not None and self._seek_next is None:
                self.emit("toast", {"text": str(error), "kind": "error"})
            self._send_seek()

        if not self._calls.call("podcast_text.seek", {"frame": frame, "timeline_id": tid},
                                lambda: resolve_ext.seek(controller, frame, tid), done):
            self._seeking = False

    def on_select(self, payload):
        """Resolve's In/Out on the words selected (two or more)."""
        if self.timeline is None or not self.settings.get("set_inout"):
            return
        first, last = self._token((payload or {}).get("first")), self._token((payload or {}).get("last"))
        if first is None or last is None or (payload or {}).get("version") != self.version:
            return
        a, b = self._frame(min(first["s"], last["s"])), self._frame(max(first["e"], last["e"]))
        controller = self._controller(False)
        if controller is None or b <= a:
            return
        tid = self.timeline["id"]

        def done(result, error):
            if error is None and not result:
                self.emit("toast", {"text": "Resolve didn't take the In and Out – setting them needs Resolve 19.1 or later."})

        self._calls.call("podcast_text.marks", {"first": a, "end": b, "timeline_id": tid},
                         lambda: resolve_ext.set_marks(controller, a, b, tid), done)
        if self.settings.get("back_to_resolve"):
            K.focus_resolve()

    def on_play(self, _payload=None):
        if K.available():       # no keys to press here: say how
            return self.emit("toast", {"text": "Press Space in Resolve to play."})
        try:
            self._presser.focus()
            self._presser.press("Space")
        except K.KeysUnavailable as exc:
            return self.emit("toast", {"text": str(exc), "kind": "error"})
        if not self.settings.get("back_to_resolve"):
            self._focus_self()

    def _focus_self(self):
        window = self.window()
        if window is not None:
            window.raise_()
            window.activateWindow()

    def _keys_for(self, payload):
        """The keys of the tokens in payload["ids"] - None if the transcript changed since."""
        if (payload or {}).get("version") != self.version:
            self.emit("toast", {"text": "The transcript changed – try again."})
            self._push_doc()
            return None
        ids = (payload or {}).get("ids") or []
        return [self.tokens[i]["key"] for i in ids if isinstance(i, int) and 0 <= i < len(self.tokens)]

    def _strike_keys(self, keys, on):
        keys = set(keys)
        new = (self.struck | keys) if on else (self.struck - keys)
        if new == self.struck:
            return False
        self.undo_stack.append({"kind": "strike", "struck": set(self.struck)})
        del self.undo_stack[:-HISTORY]
        self.redo_stack.clear()
        self.struck = new
        self._save_edits()
        self._push_doc()
        self._push_state()
        return True

    def on_strike(self, payload):
        keys = self._keys_for(payload)
        if not keys:
            return
        if self.settings.get("mode") == "live":
            return self._live_cut(keys, "Cutting…")
        # Struck already, all of them: Delete puts them back.
        self._strike_keys(keys, on=not set(keys) <= self.struck)

    def on_restore(self, payload):
        keys = self._keys_for(payload)
        if keys:
            self._strike_keys(keys, on=False)

    def on_restore_all(self, _payload=None):
        here = {t["key"] for t in self.tokens}
        if self.struck & here:
            self._strike_keys(self.struck & here, on=False)

    def on_remove_fillers(self, _payload=None):
        keys = [k for k in M.fillers(self.tokens) if k not in self.struck]
        if not keys:
            return self.emit("toast", {"text": "No filler words to remove."})
        if self.settings.get("mode") == "live":
            return self._live_cut(keys, f"Removing {len(keys)} filler words…")
        self._strike_keys(keys, on=True)
        self.emit("toast", {"text": "Struck 1 filler word – Build to make the edited timeline." if len(keys) == 1 else
                            f"Struck {len(keys)} filler words – Build to make the edited timeline."})

    def on_remove_pauses(self, payload):
        seconds = (payload or {}).get("seconds", self.settings.get("pause_over"))
        try:
            seconds = max(M.PAUSE_REMOVE_MIN, float(seconds))
        except (TypeError, ValueError):
            return
        keys = [k for k in M.pauses_over(self.tokens, seconds) if k not in self.struck]
        if not keys:
            return self.emit("toast", {"text": f"No pauses longer than {seconds:g} s."})
        if self.settings.get("mode") == "live":
            return self._live_cut(keys, f"Removing {len(keys)} pauses…")
        self._strike_keys(keys, on=True)
        self.emit("toast", {"text": "Struck 1 pause – Build to make the edited timeline." if len(keys) == 1 else
                            f"Struck {len(keys)} pauses – Build to make the edited timeline."})

    def on_correct(self, payload):
        tok = self._token((payload or {}).get("id"))
        if tok is None or tok["k"] != "w" or (payload or {}).get("version") != self.version:
            return
        word = next((w for w in self._word_sources() if w[0] == tok["key"]), None)
        if word is None:
            return
        _key, src, idx = word
        path, channels = self._source_path(src)
        if path is None or not transcripts.correct(path, channels, self._language(), idx, (payload or {}).get("text")):
            return self.emit("toast", {"text": "Couldn't save the correction.", "kind": "error"})
        self.transcripts.pop(src, None)
        self._rebuild()

    def _word_sources(self):
        audio, _ = self._tracks()
        words = M.collect_words(self.timeline, set(audio), self.transcripts)
        return [(w["key"], w["src"], w["idx"]) for w in words]

    def _source_path(self, src):
        for item in self.timeline.get("items") or []:
            if item.get("path") and M.source_key(item["path"], item.get("channels")) == src:
                return item["path"], item.get("channels")
        return None, None

    def on_undo(self, _payload=None):
        self._step(self.undo_stack, self.redo_stack, "undo")

    def on_redo(self, _payload=None):
        self._step(self.redo_stack, self.undo_stack, "redo")

    def _step(self, stack, other, which):
        if not stack:
            return
        entry = stack.pop()
        if entry["kind"] == "strike":
            other.append({"kind": "strike", "struck": set(self.struck)})
            self.struck = set(entry["struck"])
            self._save_edits()
            self._push_doc()
        else:
            if not self._live_step(entry, which):
                stack.append(entry)
                return self._push_state()
            other.append(entry)
        self._push_state()

    def on_save_keys(self, payload):
        payload = payload or {}
        if payload.get("preset") in K.PRESETS:
            self.settings["keys"] = {"preset": payload["preset"]}
        elif isinstance(payload.get("keys"), dict):
            keys = {c: K.canonical(v) for c, v in payload["keys"].items() if c in K.COMMANDS}
            if not all(keys.get(c) for c in K.COMMANDS):
                return self.emit("toast", {"text": "Every shortcut needs a key.", "kind": "error"})
            self.settings["keys"] = {"keys": keys}
        else:
            return
        self.settings.save()
        self._push_state()
        self.emit("toast", {"text": "Shortcuts saved."})

    # ------------------------------------------------- build a new timeline --

    def on_build(self, _payload=None):
        if self.timeline is None:
            return
        fps = float(self.timeline["fps"])
        cuts = M.cut_frames(self.tokens, self.struck, fps)
        if not cuts:
            return self.emit("toast", {"text": "Strike out some words or pauses first (select them, then Delete)."})
        controller = self._controller(True)
        if controller is None:
            return self.emit("alert", {"title": "Not connected", "text": self.problem})
        audio, video = self._tracks()
        self._calls.wait_idle(3.0)
        args = {"cuts": [list(c) for c in cuts], "audio": list(audio), "video": list(video), "fps": fps,
                "name": f"{otio_cut.build_name(self.timeline['name'])} {otio_cut.SUFFIX}"}
        # The export, the cut and the import in the Resolve reader's process:
        # Buddy keeps drawing meanwhile (core/busy_job.py).
        busy_job.run(self.host, self._calls, "podcast_text.build", args,
                     lambda: resolve_ext.build_here(controller, **args),
                     lambda result, error: self._built(cuts, fps, result, error), "Building the edited timeline…")

    def _built(self, cuts, fps, result, error):
        if error is not None:      # shown; the original is untouched either way
            return self.emit("alert", {"title": "Couldn't build the timeline", "text": str(error)})
        name, warnings = result["name"], result["warnings"]
        saved = M.cut_seconds(cuts, fps)
        text = (f"Built “{name}” – 1 cut, {_duration(saved)} shorter. Your original timeline is unchanged." if len(cuts) == 1
                else f"Built “{name}” – {len(cuts)} cuts, {_duration(saved)} shorter. Your original timeline is unchanged.")
        self.emit("toast", {"text": text})
        for warning in warnings:
            self.emit("toast", {"text": warning})
        self._read(connect=False)

    # ------------------------------------------------------------ live cuts --

    def _live_cut(self, keys, message):
        """Cuts the tokens `keys` from the open timeline, last first, with
        Resolve's own Ripple Delete - each checked, any that went wrong undone."""
        why = K.available()
        if why:
            return self.emit("alert", {"title": "Can't cut live here", "text": why})
        fps = float(self.timeline["fps"])
        cuts = M.cut_frames(self.tokens, set(keys), fps)
        if not cuts:
            return
        locked = [t for t in self.timeline["tracks"]["audio"] + self.timeline["tracks"]["video"] if t.get("locked")]
        audio, video = self._tracks()
        controller = self._controller(True)
        if controller is None:
            return self.emit("alert", {"title": "Not connected", "text": self.problem})
        self._calls.wait_idle(3.0)
        self._editing = True
        self.host.set_busy(True, message)
        problem, advice, made, before_all = "", False, 0, None
        try:
            keys_ = self._keys()
            start = int(self.timeline["start"])
            current = resolve_ext.read_timeline(controller, self._media)
            if locked:
                raise RuntimeError("Unlock every track first – Resolve's Ripple Delete moves all of them.")
            before_all = M.snapshot(current, audio, video)
            self._presser.focus()
            self._presser.press(keys_["deselect"])
            for first, end in reversed(cuts):
                self.host.pump_busy(message)
                current = self._cut_one(controller, keys_, start + first, start + end, audio, video)
                made += 1
        except CutFailed as exc:
            problem, advice = str(exc), True
        except Exception as exc:  # noqa: BLE001 - what went wrong, shown
            problem = str(exc)
        finally:
            try:
                resolve_ext.clear_marks(controller)
                if made:
                    resolve_ext.seek(controller, cuts[0][0])
            except Exception:  # noqa: BLE001
                pass
            self.host.set_busy(False)
            self._editing = False
            if not self.settings.get("back_to_resolve"):
                self._focus_self()
        if made:
            self.undo_stack.append({"kind": "live", "count": made, "before": before_all,
                                    "after": M.snapshot(current, audio, video), "audio": audio, "video": video})
            del self.undo_stack[:-HISTORY]
            self.redo_stack.clear()
            self._own_edit = True
        if problem:
            if advice:
                self.emit("alert", {"title": problem, "text": ADVICE})
            else:
                self.emit("alert", {"title": "Resolve didn't make the cut", "text": problem})
            if made:
                self.emit("toast", {"text": f"{made} of {len(cuts)} cuts were made."})
        self._push_state()
        self._read(connect=False)

    _out_inclusive = True       # SetMarkInOut's Out: the last frame cut (True), or the one after

    def _cut_one(self, controller, keys_, first, end, audio, video):
        """One ripple delete of [first, end) absolute frames, checked. The timeline after."""
        tl = resolve_ext.read_timeline(controller, self._media)
        before = M.snapshot(tl, audio, video)
        expected = M.rippled(before, first, end)
        start = int(tl["start"])
        out = end if self._out_inclusive else end + 1
        if not resolve_ext.set_marks(controller, first - start, out - start):
            raise RuntimeError("Resolve didn't take the In and Out – live cuts need Resolve 19.1 or later.")
        self._presser.focus()
        self._presser.press(keys_["ripple_delete"])
        after, actual = self._wait_layout(controller, audio, video, expected)
        if M.same_layout(expected, actual):
            return after
        if self._out_inclusive and M.same_layout(M.rippled(before, first, end - 1), actual):
            # Resolve's Out is the frame after the cut, here: put it back, and again.
            self._undo_to(controller, keys_, audio, video, before)
            type(self)._out_inclusive = False
            return self._cut_one(controller, keys_, first, end, audio, video)
        if not M.same_layout(before, actual):
            self._undo_to(controller, keys_, audio, video, before)
        raise CutFailed(M.layout_problem(expected, actual, {"before": before}))

    def _wait_layout(self, controller, audio, video, wanted):
        deadline = time.monotonic() + CUT_WAIT_S
        while True:
            tl = resolve_ext.read_timeline(controller, self._media)
            snap = M.snapshot(tl, audio, video)
            if M.same_layout(snap, wanted) or time.monotonic() >= deadline:
                return tl, snap
            self.host.pump_busy()
            time.sleep(0.08)

    def _undo_to(self, controller, keys_, audio, video, wanted, presses=1, key="undo"):
        for _ in range(max(1, presses)):
            self._presser.press(keys_[key])
            _tl, snap = self._wait_layout(controller, audio, video, wanted)
            if M.same_layout(snap, wanted):
                return True
        return False

    def _live_step(self, entry, which):
        """Undo (or redo) a batch of live cuts with Resolve's own Undo."""
        why = K.available()
        controller = self._controller(True)
        if why or controller is None:
            self.emit("alert", {"title": "Can't undo here", "text": why or self.problem})
            return False
        wanted = entry["before"] if which == "undo" else entry["after"]
        self._calls.wait_idle(3.0)
        self._editing = True
        self.host.set_busy(True, "Undoing…" if which == "undo" else "Redoing…")
        try:
            self._presser.focus()
            ok = False
            for _ in range(entry["count"] * 2 + 2):
                self._presser.press(self._keys()[which])
                _tl, snap = self._wait_layout(controller, entry["audio"], entry["video"], wanted)
                if M.same_layout(snap, wanted):
                    ok = True
                    break
        except Exception as exc:  # noqa: BLE001
            ok = False
            self.emit("toast", {"text": str(exc), "kind": "error"})
        finally:
            self.host.set_busy(False)
            self._editing = False
            if not self.settings.get("back_to_resolve"):
                self._focus_self()
        if not ok:
            self.emit("alert", {"title": "Couldn't undo" if which == "undo" else "Couldn't redo",
                                "text": "Resolve's timeline didn't come back to how it was – use Undo in Resolve."})
        self._own_edit = True
        self._read(connect=False)
        return ok


def _duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f} s"
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"
