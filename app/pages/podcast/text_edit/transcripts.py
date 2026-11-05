#!/usr/bin/env python3
"""
Text Edit's transcripts: each source's words, by the Subtitles tool's engine.

The engine is the Transcribe tool's (worker.py in its own venv, run through
its WorkerRun), on each source file directly - no render through Resolve -
told to keep the hesitations (--fillers, which "Remove filler words" cuts),
to give each word its probability, and to measure the level every 50 ms
(--levels, what tells one mic from another). A clip that plays only some
channels of its file (one mic of a recorder's multitrack WAV) is heard on
those channels alone (--channels).

A finished transcript is kept on disk, keyed by the file (path, size,
modified time), the channels heard and the language asked for - so opening
the panel again, or trimming in Resolve, costs nothing. Words corrected by
hand are kept in the same file ("overrides", by the word's index), until
the source is transcribed again.
"""

import hashlib
import json
import os
import tempfile

from PySide6.QtCore import QThread, Signal

from core import tool_dirs

from .model import source_key

VERSION = 1
# The models worth editing with, best first: Whisper's word times and
# probabilities, then Parakeet.
EDIT_PREFERENCE = ("large-v3", "large-v3-turbo", "distil-large-v3.5", "small", "parakeet-v3")


def cache_dir():
    return os.path.join(tool_dirs.path("podcast_text"), "transcripts")


def _key(path, channels=None, language=""):
    stat = os.stat(path)
    ident = f"{os.path.normcase(os.path.abspath(path))}|{stat.st_size}|{stat.st_mtime_ns}"
    if channels:
        ident += "|ch:" + ".".join(str(int(c)) for c in channels)
    if language:
        ident += "|lang:" + language
    return hashlib.sha1(ident.encode("utf-8")).hexdigest()[:24]


def _file(path, channels, language, folder=None):
    return os.path.join(folder or cache_dir(), _key(path, channels, language) + ".json")


def cached(path, channels=None, language="", folder=None):
    """The transcript kept for this exact source, or None."""
    try:
        with open(_file(path, channels, language, folder), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("words"), list) else None


def save(path, data, channels=None, language="", folder=None):
    try:
        target = _file(path, channels, language, folder)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(target + ".tmp", target)
        return True
    except OSError:
        return False        # an unsaved transcript is only redone next time


def correct(path, channels, language, index, text, folder=None):
    """A word fixed by hand: kept with the transcript. True if saved."""
    data = cached(path, channels, language, folder)
    if data is None or not 0 <= int(index) < len(data["words"]):
        return False
    overrides = data.setdefault("overrides", {})
    original = str(data["words"][int(index)].get("w", "")).strip()
    text = " ".join(str(text or "").split())
    if not text or text == original:
        overrides.pop(str(int(index)), None)
    else:
        overrides[str(int(index))] = text
    return save(path, data, channels, language, folder)


def words_of(result):
    """The worker's result as one list of words {s, e, w, p}."""
    words = []
    for seg in result.get("segments") or []:
        for w in seg.get("words") or []:
            text = str(w.get("word", "")).strip()
            if not text:
                continue
            words.append({"s": round(float(w["start"]), 3), "e": round(float(w["end"]), 3), "w": text,
                          "p": (round(float(w["p"]), 3) if w.get("p") is not None else None)})
    words.sort(key=lambda w: w["s"])
    return words


def installed():
    """[(model id, label)] the transcript can be made with here, best first."""
    try:
        from pages.transcribe import env_setup as es
        from pages.transcribe import jobs, plan
        settings = plan.Settings(jobs.SETTINGS_PATH)
        models = es.installed_models(settings.get("extra_models", {}))
    except Exception:  # noqa: BLE001 - the engine not set up
        return []
    return [(mid, es.MODEL_BY_ID[mid]["label"]) for mid in EDIT_PREFERENCE if mid in models]


def engine_plan(model="auto", language=""):
    """(plan, None) - what the worker runs - or (None, why it can't here).
    model "auto": the best model for editing installed (EDIT_PREFERENCE)."""
    try:
        from pages.transcribe import env_setup as es
        from pages.transcribe import jobs, plan
    except Exception as exc:  # noqa: BLE001
        return None, f"Transcription isn't available here: {exc}"
    if not es.venv_python().exists():
        return None, ("Transcription isn't set up on this computer yet – install it on the "
                      "Subtitles tool's Setup tab.")
    settings = plan.Settings(jobs.SETTINGS_PATH)
    models = es.installed_models(settings.get("extra_models", {}))
    usable = [mid for mid in EDIT_PREFERENCE if mid in models]
    if not usable:
        return None, "Get a transcription model on the Subtitles tool's Setup tab."
    chosen = model if model in usable else usable[0]
    if model == "auto" and chosen == "parakeet-v3" and language and language not in es.PARAKEET_LANGS:
        return None, "Get a Whisper model on the Subtitles tool's Setup tab for this language."
    result, why = plan.plan_for(chosen, models, str(language or ""))
    if result is None and model != "auto":
        result, why = plan.plan_for(usable[0], models, str(language or ""))
    if result is not None and result.get("engine") == "resolve":
        return None, "Choose a Whisper or Parakeet model for Text Edit."
    return result, why


class TranscribeQueue(QThread):
    """Transcribes sources one after another: [(source key, path, channels)].
    progress(done sources, total, fraction of this one, its name);
    finished_one(source key, data); failed_one(source key, message). cancel()
    stops it; what's done stays done."""

    progress = Signal(int, int, float, str)
    finished_one = Signal(str, dict)
    failed_one = Signal(str, str)

    def __init__(self, sources, plan, language="", parent=None, folder=None):
        super().__init__(parent)
        self.sources = list(sources)
        self._plan = plan
        self._language = str(language or "")
        self._folder = folder
        self._run = None
        self._cancelled = False
        self.current = ""

    def cancel(self):
        self._cancelled = True
        if self._run is not None:
            self._run.cancel()

    def run(self):
        total = len(self.sources)
        for index, (src, path, channels) in enumerate(self.sources):
            if self._cancelled:
                return
            self.current = os.path.basename(path)
            self.progress.emit(index, total, 0.0, self.current)
            try:
                data = self._one(path, channels, lambda f, i=index: self.progress.emit(i, total, f, self.current))
            except Exception as exc:  # noqa: BLE001 - shown in the panel
                if self._cancelled:
                    return
                self.failed_one.emit(src, _friendly(path, str(exc)))
                continue
            if self._cancelled:
                return
            if data is not None:
                save(path, data, channels, self._language, self._folder)
                self.finished_one.emit(src, data)
        self.progress.emit(total, total, 0.0, "")

    def _one(self, path, channels, report):
        from pages.transcribe.jobs import WorkerRun
        from pages.transcribe.resolve_ext import RenderCancelled
        stem = os.path.join(tempfile.gettempdir(), f"buddy_text_edit_{os.getpid()}_{id(self)}")
        out, lv = stem + ".json", stem + ".levels.json"
        plan = self._plan
        args = ["transcribe", "--audio", path, "--engine", plan["engine"], "--model", plan["whisper"],
                "--parakeet", plan["parakeet"], "--device", "auto", "--language", plan["language"],
                "--fillers", "--levels", lv, "--out", out]
        if channels:
            args += ["--channels", ",".join(str(int(c)) for c in channels)]

        def on_message(message):
            if message.get("type") == "progress" and not self._cancelled:
                total = float(message.get("total") or 0)
                if total > 0:
                    report(min(1.0, float(message.get("done") or 0) / total))

        try:
            self._run = WorkerRun()
            if self._cancelled:
                return None
            error = self._run.run(args, on_message)
            if self._cancelled:
                return None
            if error:
                raise RuntimeError(error)
            with open(out, encoding="utf-8") as handle:
                result = json.load(handle)
            levels = None
            try:
                with open(lv, encoding="utf-8") as handle:
                    levels = json.load(handle)
            except (OSError, ValueError):
                pass
            return {"v": VERSION, "language": result.get("language") or self._language or "",
                    "engine": result.get("engine", ""), "duration": result.get("duration", 0),
                    "words": words_of(result), "levels": levels, "overrides": {},
                    "source": source_key(path, channels)}
        except RenderCancelled:
            return None
        finally:
            for f in (out, lv):
                try:
                    os.remove(f)
                except OSError:
                    pass


RAW_CAMERA = (".braw", ".r3d", ".ari", ".arx", ".crm", ".nev")


def _friendly(path, message):
    if os.path.splitext(path)[1].lower() in RAW_CAMERA:
        return (f"{os.path.basename(path)} is camera RAW, which only Resolve can read – transcribe its "
                "proxy, or a WAV of its sound, instead.")
    return f"{os.path.basename(path)}: {message}"
