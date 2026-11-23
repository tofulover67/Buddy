#!/usr/bin/env python3
"""
Ask Buddy - a grounded chat page over the Resolve manual and the user's
actual open project. A web page (core/web_page.py): the
view is web/index.html + chat.js; everything it shows is decided here.

Protocol (see core/web_page.py for the mechanism):
    to the view    transcript, append, controls, status, thinking, stream,
                   suggestions, compose, proposal, revert, offer, toast,
                   pictures, chat_list
    from the view  send, stop, regenerate, edit_last, copy, new_chat,
                   prev_chat, next_chat, export, apply_proposal,
                   discard_proposal, revert_change, open_tool, open_manual,
                   attach_image,
                   paste_image, remove_picture, search_chats, select_chat,
                   rename_chat, pin_chat, delete_chat, delete_all_chats,
                   run_checkup, explain_clip

An answer streams: the worker hands over each piece as the model writes it
(delta), the page keeps the draft and sends it to the view, rendered, a few
times a second (stream). Stop lets go of the worker at once - the request
it's in ends at the next piece, or when the server answers - and whatever
it says after that is ignored (_on_done looks at who sent it).

Pictures (pictures.py) can go with a question - picked, pasted or dropped -
for a model that can see them; only that question carries them.

Threading: agent.ask() makes several blocking HTTPS round trips, so it runs
on a QThread worker and the page renders what the finished signal carries.

The worker must NEVER call host.ensure_connected(). That looks like a plain
Resolve call but it is a shell method: on a cold connection it runs
_connect(), which sets status_label's text and stylesheet and can raise a
QMessageBox parented to the main window. Doing that off the GUI thread
gives "QObject::setParent: Cannot set parent, new parent is in a different
thread" and takes the app down. So the controller is resolved HERE, on the
GUI thread, before the worker starts, and the worker only ever touches the
already-built controller object (plain Python, no Qt affinity).

A page that is open while Resolve is started later therefore needs the
header's Connect button pressed once - which is what the
project_state tool tells the user when there is no connection.
"""

from __future__ import annotations

import base64
import os
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QBuffer, QEventLoop, QIODevice, QStandardPaths, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QFileDialog

from core.atomic_io import CorruptFileError
from core.i18n import tr, tr_filter
from core.message_dialog import confirm
from core.resolve_bridge import ResolveConnectionError
from core.tools_kb import get_tool
from core.web_page import WebToolPage

from . import actions, chat_store, embedder, local_llama, manual_pdf, pictures
from .agent import AgentResult, ManualAgent
from .ask_folder import instructions_for_prompt
from .config import (
    DEFAULTS,
    default_data_paths,
    history_limit,
    llm_client_from_settings,
    max_steps_limit,
    migrate_legacy_settings,
)
from .conversation import (
    BUDDY,
    ERROR,
    MAX_SOURCES,
    YOU,
    ChatSessions,
    block_view,
    followups_view,
    help_text,
    hide_followups,
    is_help,
    md_to_html,
    shuffled_pool,
    sources_from,
    suggestions,
    unverified_pages,
)
from .retrieval import TIER_HYBRID, TIER_NONE, ManualRetriever
from .resolve_ext import project_checkup, resolve_info
from .settings_panel import ChatSettingsMixin

PICTURE_FILTER = "Pictures (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.tif *.tiff);;All files (*)"
PICTURE_ENDINGS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")
MANUAL_POLL_MS = 15     # how often a question waiting for the manual looks whether it's loaded
STREAM_MS = 70          # how often an answer being written is redrawn
EXPORT_FILTER = "Markdown (*.md);;Text files (*.txt)"

# Offered under a project check-up, by what it found. Buddy's own words, so
# they're translated (the model's follow-ups never are).
CHECKUP_FOLLOWUPS = {
    "frame_rate": "How should I handle clips whose frame rate doesn't match the timeline?",
    "upscaled": "How can I make upscaled clips look sharper?",
    "missing_path": "How do I relink missing media?",
    "flag": "Flag the clips that don't match the timeline",
    "clean": "What should I check before I render?",
}


class _AgentWorker(QThread):
    done = Signal(object)
    progress = Signal(str)
    delta = Signal(object)      # a piece of the answer as it's written; None: clear what came so far

    def __init__(self, agent, question, history, images=None, stream=False, summary="", fold=0):
        super().__init__()
        self._agent, self._question, self._history = agent, question, history
        self._images, self._stream = images, stream
        self._summary, self.fold = summary, fold
        self._stop = False

    def stop(self):
        """Stop when it next can (agent.ask's `cancelled`). Only a flag: the
        thread is never killed, it's let go of."""
        self._stop = True

    def run(self):
        """Always emits exactly once. ask() is written not to raise, but if
        it ever does, a silent thread death would leave the page stuck
        "thinking" with no way back - so the failure becomes a visible
        answer instead."""
        try:
            result = self._agent.ask(
                self._question, self._history, progress=self.progress.emit, images=self._images,
                on_text=self.delta.emit if self._stream else None, cancelled=lambda: self._stop,
                summary=self._summary, fold=self.fold,
            )
        except Exception as e:  # noqa: BLE001 - surfaced in the transcript
            result = AgentResult(error=f"Unexpected failure: {e!r}")
        self.done.emit(result)


class _CheckupWorker(QThread):
    done = Signal(object)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def run(self):
        try:
            self.done.emit(project_checkup(self.controller))
        except Exception as exc:  # shown in the chat
            self.done.emit({"error": str(exc)})


class ManualChatPage(ChatSettingsMixin, WebToolPage):
    tool_id = "manual_chat"
    display_name = "Ask Buddy"
    category = "Ask"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    file_drops = True   # pictures dragged in go with the next question (pictures.py)
    _manual_loaded = Signal(object, int)    # (ManualRetriever, generation), from the loading thread

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id, dict(DEFAULTS))
        migrate_legacy_settings(self.settings)
        self._read_declined = None            # the address whose project-reading question was answered no
        self._retriever = None
        self._manual_thread = None          # loading the manual (on_shown), or None
        self._manual_gen = 0                # a reload's load replaces an older one still running
        self._manual_loaded.connect(self._on_manual_loaded)
        self._worker = None
        self._stopped = []                  # workers let go of by Stop, until their thread ends
        self._draft = ""                    # the answer so far, while it streams
        self._stream_timer = QTimer(self)
        self._stream_timer.setSingleShot(True)
        self._stream_timer.timeout.connect(self._push_stream)
        self._sending = False
        self._thinking = ""
        self._pending_question = ""
        self._proposal = None
        # "ready", "busy" (applying: both buttons off) or "failed" (Apply
        # stays off - re-running a half-applied change is not something to
        # offer with one click - but Discard still works).
        self._proposal_state = "ready"
        self._proposal_note = ""
        # The change applied last, while Revert can still put it back
        # (actions.revert): this run only - it holds Resolve's own objects.
        self._applied = None
        self._revert_busy = False
        self._chat_query = ""
        self._chat_store_error = ""
        try:
            self.chats, warnings = chat_store.load(self.ask_folder)
            self._chat_store_error = " ".join(warnings)
            self.chats.start_fresh()        # a launch opens on a new chat; the saved ones are in the list
        except (OSError, ValueError, TypeError, CorruptFileError) as exc:
            self.chats = ChatSessions()
            self._chat_store_error = f"Saved chats could not be read: {exc}. Saving is disabled to protect the file."
        self._checkup_worker = None
        self._suggestion_pool = shuffled_pool()     # this run's pick - a new chat keeps it
        self.pictures = []              # pictures.Picture, waiting to go with the next question
        self._views = {}                # sent pictures' larger copies, by file name - this run's, saved or not
        self._pending_pictures = 0

    def working_on(self):
        if self._sending:
            return "Answering"
        return "Checking the project" if self._checkup_worker is not None else None

    def web_ready(self):
        self._push_pictures()
        self._push_transcript()
        self._push_controls()
        self._push_chat_list()
        self._push_status()
        self._push_proposal()
        self._push_revert()
        self._push_offer()
        self.emit("thinking", {"on": self._sending, "message": self._thinking})
        self._push_stream()

    # ------------------------------------------------------------- wiring

    def on_shown(self):
        """Load the manual lazily - not at app start for people who never
        open this page - and on a thread of its own: reading and indexing it
        takes ~0.4 s, and this is the page Buddy opens on, so the window
        would otherwise wait for it at every launch. A question sent before
        it's done waits for it (_load_manual_now)."""
        if self._retriever is not None or self._manual_thread is not None:
            return
        gen, args = self._manual_gen, self._manual_args()
        self._manual_thread = threading.Thread(target=self._load_manual, args=(gen, args), daemon=True,
                                               name="ask-buddy-manual")
        self._manual_thread.start()

    def _manual_args(self):
        """What ManualRetriever is made from, read here on the UI thread."""
        bundle = self.settings.get("bundle_dir") or ""
        text = self.settings.get("text_path") or ""
        if not bundle and not text:
            bundle, text = default_data_paths()
        values = getattr(self.settings, "values", None)     # ToolSettings - or a plain dict
        return bundle or None, text or None, dict(values if isinstance(values, dict) else self.settings)

    @staticmethod
    def _make_retriever(bundle, text, settings):
        return ManualRetriever(bundle, text, embedder.from_settings(settings))

    def _load_manual(self, gen, args):
        try:
            retriever = self._make_retriever(*args)
        except Exception:               # noqa: BLE001 - read again when a question needs it
            retriever = None
        try:
            self._manual_loaded.emit(retriever, gen)
        except RuntimeError:
            pass                        # Buddy closing: the page is gone

    def _on_manual_loaded(self, retriever, gen):
        if gen != self._manual_gen:
            return                      # a reload started since: its own load is coming
        self._manual_thread = None
        if self._retriever is None and retriever is not None:
            self._retriever = retriever
            self._push_status()

    def _load_manual_now(self):
        """The manual, before a question goes: the load under way (a moment
        at most), or one made here - which raises, as on_shown used to."""
        thread = self._manual_thread
        if thread is not None:
            self.host.set_busy(True, "Loading the Resolve manual…")
            try:
                self._wait_for(thread)
            finally:
                self.host.set_busy(False)
            QApplication.sendPostedEvents(self)       # its result, if it's been sent
        if self._retriever is None:
            self._manual_gen += 1                     # whatever's still coming is dropped
            self._manual_thread = None
            self._retriever = self._make_retriever(*self._manual_args())
            self._push_status()

    @staticmethod
    def _wait_for(thread):
        """Until the manual's loading thread ends, with the UI thread free
        meanwhile - not join(), which held it: the busy overlay's spinner
        stood still, and a slow load could get the window marked "Not
        Responding". (The overlay keeps clicks off.)"""
        if not thread.is_alive():
            return
        loop = QEventLoop()
        poll = QTimer(interval=MANUAL_POLL_MS)
        poll.timeout.connect(lambda: thread.is_alive() or loop.quit())
        poll.start()
        if thread.is_alive():
            loop.exec()
        poll.stop()
        thread.join()                 # over by now: only tidies up

    def _status_text(self) -> str:
        """The line under the composer - only when there's something to
        know: the manual search isn't at its best (and why), or there is no
        manual. With semantic search working it says nothing; that it works
        is not news, and Settings > AI shows how it's set up."""
        if self._retriever is None or self._retriever.tier == TIER_HYBRID:
            return ""
        note = self._retriever.describe_tier()
        if self._retriever.tier == TIER_NONE:
            note += "  Build it from the manual PDF in Settings > AI > Manual search > Rebuild from PDF…"
        return note

    def on_app_quitting(self):
        # Buddy's own llama.cpp, if semantic search started it.
        local_llama.stop_all()
        # Settings' check of the search model (a second or so) and its ask
        # for a local server's models (LOCAL_LIST_TIMEOUT at most) - a
        # QThread destroyed while it runs takes the app down with it.
        # Likewise Set up's download, Test connection, a question on its way
        # and the check-up: each a QThread of this page's.
        jobs = [getattr(self, name, None) for name in (
            "_pinned_job", "_list_job", "_embed_job", "_llm_job", "_ending_job", "_worker", "_checkup_worker")]
        for job in jobs + list(self._stopped):
            if job is None or not hasattr(job, "wait"):
                continue
            if isinstance(getattr(job, "cancel", None), bool):
                job.cancel = True           # _EmbedJob: stops between files
            if isinstance(job, _AgentWorker):
                job.stop()                  # a question on its way: no need to hear the answer out
            job.wait(10000)

    def _reload_manual(self, _result=None):
        """Pick up a freshly built bundle - or a newly chosen embedder -
        without restarting Buddy."""
        self._retriever = None
        self._manual_gen += 1
        self._manual_thread = None
        if self.isVisible():
            self.on_shown()

    def _build_agent(self, prefetch_focus=False) -> ManualAgent:
        llm = llm_client_from_settings(self.settings)
        # Read here, on every build, rather than cached on the page. The
        # agent is rebuilt per send, so revoking consent in Settings takes
        # effect on the very next message instead of the next launch.
        allow_writes = bool(self.settings.get("allow_project_writes", False))
        allow_reads = self.project_reads_allowed(llm)
        controller = self.host.controller if getattr(self.host, "connected", False) else None
        edition = resolve_info(controller) if controller else None
        # host.registry rather than importing registry.py - that module
        # imports this page, so importing it back would be a cycle.
        return ManualAgent(
            self._retriever,
            llm,
            connect_resolve=self._resolve_source(),
            registry=getattr(self.host, "registry", None),
            allow_writes=allow_writes,
            max_steps=max_steps_limit(self.settings),
            # From the file each time, like the consent above: an edit in
            # Settings or a text editor applies to the next question.
            instructions=instructions_for_prompt(self.ask_folder),
            edition=edition,
            prefetch_focus=prefetch_focus,
            allow_reads=allow_reads,
            followups=bool(self.settings.get("suggest_followups", True)),
        )

    def _ask_to_read_project(self):
        """Once, before a question to a cloud model with Resolve connected:
        may it read the project? Not asked again for that address if the
        answer was no (Settings has the switch)."""
        llm = llm_client_from_settings(self.settings)
        if (not getattr(self.host, "connected", False) or self.project_reads_allowed(llm)
                or self._read_declined == llm.destination or llm.validate()):
            return
        where = llm.label + ("" if llm.destination in (llm.provider, llm.label.lower()) else f" ({llm.destination})")
        if confirm(self.window(), "Let Ask Buddy read your project?",
                   tr("To answer questions about your project, Ask Buddy reads its settings, timeline, clip and "
                      "marker names and sends what it reads to {where} along with your question. Allow that? "
                      "You can change it any time in Settings > AI.").replace("{where}", where),
                   ok="Allow", cancel="Not now"):
            self._save("project_read_consent", llm.destination)
        else:
            self._read_declined = llm.destination

    def _resolve_source(self):
        """A zero-arg callable handing the worker the shell's EXISTING
        controller, captured now, on the GUI thread.

        Deliberately does not connect: ensure_connected() touches widgets
        (see this module's docstring), and a probe on every message would
        also cost about a second whenever Resolve is closed.
        """
        controller = self.host.controller if getattr(self.host, "connected", False) else None

        def source():
            if controller is None:
                raise RuntimeError(
                    "Buddy is not connected to DaVinci Resolve. Press "
                    "Connect in Buddy's header (with Resolve "
                    "running and a project open), then ask again."
                )
            return controller

        return source

    # ------------------------------------------------------------ the view

    def _suggestions(self) -> dict:
        """The buttons under the conversation: a new chat's starters, or
        the questions offered with its last message. `raw` ones are the
        model's own words, which the view leaves untranslated."""
        if self.chats.is_empty():
            return {"items": suggestions(self._suggestion_pool, bool(getattr(self.host, "connected", False))),
                    "raw": False}
        if self._sending or not self.settings.get("suggest_followups", True):
            return {"items": [], "raw": False}
        return followups_view(self.chats.blocks[-1] if self.chats.blocks else None)

    def _push_suggestions(self):
        self.emit("suggestions", self._suggestions())

    def _push_transcript(self):
        offered = self._suggestions()
        self.emit("transcript", {
            "blocks": [block_view(i, b) for i, b in enumerate(self.chats.blocks)],
            "suggestions": offered["items"],
            "suggestions_raw": offered["raw"],
        })

    def _append(self, who, body, trace=None, error=False, copyable=False, images=None, raw=False, views=None,
                **more):
        block = self.chats.add(who, body, trace=trace, error=error, copyable=copyable, images=images, raw=raw,
                               views=views, **more)
        self.emit("append", block_view(len(self.chats.blocks) - 1, block))
        self._persist_chats()
        return block

    def _saving_disabled(self):
        return bool(self._chat_store_error and "Saving is disabled" in self._chat_store_error)

    def _persist_chats(self, forget_backup=False):
        if self._saving_disabled():
            return
        try:
            chat_store.save(self.ask_folder, self.chats, forget_backup=forget_backup)
        except OSError as exc:
            self._toast(f"Could not save conversations: {exc}")

    def _push_chat_list(self):
        self.emit("chat_list", {"items": self.chats.summaries(self._chat_query),
                                "current": self.chats.index, "query": self._chat_query})

    def _push_controls(self):
        chats = self.chats
        turn = None if self._sending else chats.last_turn()
        self.emit("controls", {
            "sending": self._sending,
            # Only a question to the model can be stopped; a check-up is over in a moment.
            "can_stop": self._sending and self._worker is not None,
            # The last question and its answer, by block: where Edit and Regenerate go.
            "redo": {"q": turn[0], "a": turn[1]} if turn else None,
            "turns": chats.turns,
            "limit": history_limit(self.settings),
            "index": chats.index,
            "total": len(chats.chats),
            # Same _sending guard everywhere: swapping the transcript out
            # from under a reply in flight would land it in the wrong chat.
            "can_prev": chats.index > 0 and not self._sending,
            "can_next": chats.index < len(chats.chats) - 1 and not self._sending,
            "can_new": not self._sending and not (chats.is_empty() and chats.index == len(chats.chats) - 1),
            "connected": bool(getattr(self.host, "connected", False)),
            "title": chats.current.title,
        })
        self._push_chat_list()

    def _push_status(self):
        note = self._status_text()
        if self._chat_store_error:
            note = f"{note}  {self._chat_store_error}".strip()
        self.emit("status", {"text": note})

    def _toast(self, text):
        self.emit("toast", {"text": text})

    # ------------------------------------------------------------ sending

    # ----------------------------------------------------------- pictures

    def on_attach_image(self, _payload=None):
        paths, _chosen = QFileDialog.getOpenFileNames(self, tr("Add pictures"), "", tr_filter(PICTURE_FILTER))
        self._add_picture_files(paths)

    def on_files_dropped(self, paths):
        pictures_ = [p for p in paths if p.lower().endswith(PICTURE_ENDINGS)]
        if not pictures_:
            self._toast("Only pictures can be added – PNG, JPEG, WebP, GIF, BMP or TIFF.")
        self._add_picture_files(pictures_)

    def on_paste_image(self, _payload=None):
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        files = [u.toLocalFile() for u in (mime.urls() if mime is not None and mime.hasUrls() else [])
                 if u.isLocalFile() and u.toLocalFile().lower().endswith(PICTURE_ENDINGS)]
        if files:
            self._add_picture_files(files)
            return
        image = clipboard.image()
        if not image.isNull():
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            image.save(buffer, "PNG")
            self._add_picture(bytes(buffer.data()))

    def _add_picture_files(self, paths):
        for path in paths:
            try:
                if os.path.getsize(path) > pictures.MAX_INPUT_BYTES:
                    raise pictures.PictureError(f"{os.path.basename(path)} is too big to send.")
                with open(path, "rb") as f:
                    data = f.read()
            except pictures.PictureError as exc:
                self._toast(str(exc))
                continue
            except OSError as exc:
                self._toast(f"Couldn't read {os.path.basename(path)}: {exc.strerror or exc}")
                continue
            if not self._add_picture(data):
                break

    def _add_picture(self, data: bytes) -> bool:
        if len(self.pictures) >= pictures.MAX_PICTURES:
            self._toast(f"Up to {pictures.MAX_PICTURES} pictures per question.")
            return False
        try:
            self.pictures.append(pictures.prepare(data))
        except pictures.PictureError as exc:
            self._toast(str(exc))
            return True
        self._push_pictures()
        return True

    def on_remove_picture(self, payload):
        index = (payload or {}).get("index")
        if isinstance(index, int) and 0 <= index < len(self.pictures):
            del self.pictures[index]
            self._push_pictures()

    def _push_pictures(self):
        try:
            llm = llm_client_from_settings(self.settings)
            where = "stay on this PC" if llm.local else f"go to {llm.label} with your question"
        except Exception:  # noqa: BLE001 - settings half filled in
            where = "go to your AI provider with your question"
        self.emit("pictures", {
            "available": pictures.AVAILABLE,
            "items": [{"preview": p.preview, "label": f"{p.w} x {p.h}"} for p in self.pictures],
            "note": f"Pictures {where}. The model has to be one that can see images.",
        })

    def _keep_view(self, data: bytes) -> str:
        """A sent picture's larger copy: on disk beside the conversations
        (unless saving them is off), and in memory for this run."""
        name = chat_store.picture_name(data)
        self._views[name] = data
        if not self._saving_disabled():
            try:
                chat_store.save_picture(self.ask_folder, data)
            except OSError:
                pass                    # it still opens this run
        return name

    def _sent_picture(self, payload):
        """(larger JPEG bytes, or the preview's data: URL) for a picture in
        the open chat - payload {i: block, n: picture}."""
        try:
            block = self.chats.blocks[int((payload or {}).get("i"))]
            n = int((payload or {}).get("n"))
            preview = block["images"][n]
        except (TypeError, ValueError, IndexError, KeyError):
            return None, ""
        views = block.get("views") or []
        name = views[n] if n < len(views) else ""
        data = self._views.get(name)
        if data is None:
            path = chat_store.picture_path(self.ask_folder, name)
            try:
                data = path.read_bytes() if path is not None else None
            except OSError:
                data = None
        return data, preview

    def on_open_picture(self, payload):
        """A sent picture, larger - an older chat's (from before the larger
        copies were kept) opens its preview."""
        data, preview = self._sent_picture(payload)
        if data is None and not preview:
            return
        url = f"data:{pictures.MIME};base64,{base64.b64encode(data).decode('ascii')}" if data else preview
        self.emit("lightbox", {"i": (payload or {}).get("i"), "n": (payload or {}).get("n"), "url": url})

    def on_save_picture(self, payload):
        data, preview = self._sent_picture(payload)
        if data is None:
            if not preview:
                return
            data = base64.b64decode(preview.split(",", 1)[1])
        downloads = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation) or str(Path.home())
        default_name = f"Buddy Picture {datetime.now().strftime('%Y-%m-%d %H%M%S')}.jpg"
        path, _filter = QFileDialog.getSaveFileName(
            self, tr("Save picture"), str(Path(downloads) / default_name), tr_filter("JPEG pictures (*.jpg)"))
        if not path:
            return
        try:
            Path(path).write_bytes(data)
        except OSError as exc:
            self._toast(f"Could not save the picture: {exc.strerror or exc}")
            return
        self._toast(f"Saved to {path}")

    # ------------------------------------------------------------ sending

    def on_send(self, payload):
        """A question from the composer. With {"replace": true} it takes
        the place of the last one (Edit): that turn comes off the
        conversation first, and its pictures go with the new wording."""
        question = str((payload or {}).get("text") or "").strip()
        if (not question and not self.pictures) or self._sending:
            return
        if not question:
            question = "Take a look at this picture." if len(self.pictures) == 1 else "Take a look at these pictures."
        replaced = self._drop_last_turn() if (payload or {}).get("replace") else None

        # Answered here, before the retriever is even loaded: "help" should
        # be instant and free, and it must describe what Buddy can actually
        # do rather than what a model guesses it can do.
        if is_help(question):
            self._append(YOU, question, raw=True)
            self._append(BUDDY, help_text(bool(self.settings.get("allow_project_writes", False))),
                         copyable=True)
            self._push_controls()
            return

        self.achieve("questions", 1)
        sent, self.pictures = self.pictures, []
        if sent or replaced is None:
            previews, views = [p.preview for p in sent], [self._keep_view(p.view) for p in sent]
            images = [p.for_model() for p in sent]
        else:
            previews, views, images = self._pictures_again(replaced)
        self._ask(question, images, previews, views, focus=bool((payload or {}).get("focus")))

    def on_regenerate(self, _payload=None):
        """The last question, asked again: its answer comes off the
        conversation (and out of what the model is re-sent) first."""
        if self._sending:
            return
        question = self._drop_last_turn()
        if question is None:
            return
        previews, views, images = self._pictures_again(question)
        self._ask(question["body"], images, previews, views)

    def on_edit_last(self, _payload=None):
        """Edit was pressed on the last question: its words go to the
        composer. Nothing is removed until the new wording is sent
        (on_send's replace) - cancelling leaves the conversation as it was."""
        turn = None if self._sending else self.chats.last_turn()
        if turn is not None:
            self.emit("compose", {"text": self.chats.blocks[turn[0]]["body"], "replace": True})

    def _drop_last_turn(self):
        """The last turn off the conversation, and what hung on it: the
        change it proposed, the tool it offered. Its question's block, or
        None when the conversation doesn't end on a question to the model."""
        question = self.chats.drop_last_turn()
        if question is None:
            return None
        self._hide_proposal()
        self.chats.current.offer = None
        self._push_offer()
        self._push_transcript()
        return question

    def _pictures_again(self, block):
        """(previews, views, pictures for the model) of a question being
        asked again, from the larger copies kept when it was first sent. A
        picture whose copy is gone (saving was off, an older chat) isn't
        sent again, and isn't shown as if it had been."""
        previews, views, images = [], [], []
        for n, name in enumerate(block.get("views") or []):
            data = self._views.get(name)
            if data is None:
                path = chat_store.picture_path(self.ask_folder, name)
                try:
                    data = path.read_bytes() if path is not None else None
                except OSError:
                    data = None
            if data is None or n >= len(block.get("images") or []):
                continue
            previews.append(block["images"][n])
            views.append(name)
            images.append({"mime": pictures.MIME, "data": base64.b64encode(data).decode("ascii")})
        return previews, views, images

    def _ask(self, question, images, previews, views, focus=False):
        """Put the question in the conversation and start the worker on it."""
        if self._retriever is None:
            self._load_manual_now()
        self._ask_to_read_project()

        self._pending_question = question
        self._pending_pictures = len(previews)
        self._append(YOU, question, images=previews, raw=True, views=views, asked=True)
        self._push_pictures()
        self._draft = ""
        self._set_sending(True)

        chat = self.chats.current
        self._worker = _AgentWorker(self._build_agent(prefetch_focus=focus), question,
                                    self.chats.history_for_model(), images=images or None,
                                    stream=bool(self.settings.get("stream_answers", True)),
                                    summary=chat.summary, fold=self.chats.to_fold())
        self._worker.done.connect(self._on_done)
        self._worker.progress.connect(self._on_progress)
        self._worker.delta.connect(self._on_delta)
        self._worker.finished.connect(self._clear_worker)
        self._worker.start()
        self._push_controls()               # Stop, now there is something to stop

    def _from_a_stopped_worker(self) -> bool:
        """True for a signal from a worker Stop has let go of."""
        sender = self.sender()
        return isinstance(sender, _AgentWorker) and sender is not self._worker

    def _on_progress(self, message: str):
        """Queued from the worker thread - runs on the GUI thread."""
        if self._sending and not self._from_a_stopped_worker():
            self._thinking = message
            self.emit("thinking", {"on": True, "message": message})

    def _on_delta(self, piece):
        """A piece of the answer as the model writes it (None: what came so
        far led to a tool call - clear it). Drawn at most every STREAM_MS."""
        if not self._sending or self._from_a_stopped_worker():
            return
        self._draft = "" if piece is None else self._draft + str(piece)
        if not self._stream_timer.isActive():
            self._stream_timer.start(STREAM_MS)

    def _push_stream(self):
        draft = hide_followups(self._draft) if self._sending else ""
        self.emit("stream", {"html": md_to_html(draft) if draft.strip() else ""})

    def _end_stream(self) -> str:
        """The draft, taken off the screen."""
        draft, self._draft = self._draft, ""
        self._stream_timer.stop()
        self.emit("stream", {"html": ""})
        return draft

    def on_stop(self, _payload=None):
        """Stop the answer on its way. What had been written stays, marked
        as stopped - and out of what the model is re-sent, where half an
        answer would read as a whole one."""
        worker = self._worker
        if worker is None or not self._sending:
            return
        worker.stop()
        self._worker = None
        self._stopped.append(worker)        # kept until its thread ends (_clear_worker)
        draft = hide_followups(self._end_stream()).strip()
        self._set_sending(False)
        if draft:
            self._append(BUDDY, draft, copyable=True, raw=True, answer=True, stopped=True)
        else:
            self._append(BUDDY, "Stopped before there was an answer.", answer=True, stopped=True)
        self._push_controls()
        self._push_suggestions()

    def _clear_worker(self):
        """done fires before finished, so _on_done has already run by now."""
        worker = self.sender() if isinstance(self.sender(), _AgentWorker) else self._worker
        if worker is self._worker:
            self._worker = None
        if worker in self._stopped:
            self._stopped.remove(worker)
        if worker is not None:
            worker.deleteLater()

    def _set_sending(self, sending: bool):
        self._sending = sending
        self._thinking = "Thinking…" if sending else ""
        self.emit("thinking", {"on": sending, "message": self._thinking})
        self._push_controls()
        if not sending:
            self._push_status()

    def _pages_read(self, passages) -> set:
        """Every manual page the model has been handed in this conversation:
        this answer's, and the earlier answers' (a follow-up may rightly
        cite a page it was given two questions ago)."""
        pages = {p.page for p in passages if isinstance(getattr(p, "page", None), int)}
        for block in self.chats.blocks:
            pages.update(s["page"] for s in block.get("sources") or [])
        return pages

    def _on_done(self, result):
        if self._from_a_stopped_worker():
            return                          # Stop was pressed: nobody is waiting for this
        worker = self._worker
        self._end_stream()
        self._set_sending(False)
        if result.summary is not None:
            self.chats.folded(result.summary, getattr(worker, "fold", 0))
        if result.cancelled:
            return
        if result.error:
            error = result.error
            if self._pending_pictures:
                error += ("\n\nThis question had a picture. If the model can't read images, choose one that "
                          "can in Settings > AI > Chat assistant (a \"vision\" model – most recent GPT, Claude and "
                          "Gemini models can; for Ollama, e.g. gemma3 or llava) – or ask without it.")
            self._append(ERROR, error, error=True, copyable=True)
            self._push_controls()
            return

        trace = [f"{e.name}: {e.summary}" for e in result.events]
        block = self.chats.add(
            BUDDY, result.answer, trace=trace, copyable=True, raw=True, answer=True,
            sources=sources_from(result.passages)[:MAX_SOURCES],
            unverified=unverified_pages(result.answer, self._pages_read(result.passages)),
            followups=result.followups)
        self.emit("append", block_view(len(self.chats.blocks) - 1, block))
        self.chats.record_turn(self._pending_question, result.answer, history_limit(self.settings),
                               pictures=self._pending_pictures)
        self._persist_chats()
        self._push_controls()
        self._push_suggestions()

        if result.proposed_action is not None:
            # Consent may have been revoked while this answer was in flight.
            if bool(self.settings.get("allow_project_writes", False)):
                self._show_proposal(result.proposed_action)
            else:
                self._append(
                    BUDDY,
                    "Project changes were turned off while I was answering, "
                    "so the proposed change was dropped – nothing was changed.",
                )

        if result.offered_tool:
            tool_id, reason = result.offered_tool
            tool = get_tool(tool_id, getattr(self.host, "registry", None))
            offer = {"tool_id": tool_id, "label": f"Open {tool.name if tool else tool_id}", "reason": reason}
            if result.offered_prefill:
                offer["prefill"] = result.offered_prefill
            if result.offered_handoff:
                offer["handoff"] = result.offered_handoff
            self._set_offer(offer)

    def _set_offer(self, offer):
        self.chats.current.offer = offer
        self._persist_chats()
        self._push_offer()

    def _push_offer(self):
        self.emit("offer", self.chats.current.offer)

    def on_open_tool(self, payload):
        """The offered tool, in front - started with what the model handed
        over for it, when it's a tool that takes that (ToolPage.prefill, or
        ToolPage.handoff for a bin list or a tab to open on).
        The text comes from the offer kept here, never from the view."""
        tool_id = (payload or {}).get("tool_id")
        if not tool_id:
            return
        offer = self.chats.current.offer or {}
        handoff = offer.get("handoff") if offer.get("tool_id") == tool_id else None
        if handoff:
            # More than words: the shell hands it to the tool (ToolPage.handoff).
            if not self.host.switch_tool(tool_id, payload=handoff):
                self._toast("The tool is open, but it couldn't take what Ask Buddy handed over.")
            return
        self.host.switch_tool(tool_id)
        text = offer.get("prefill") if offer.get("tool_id") == tool_id else ""
        page = (getattr(self.host, "pages", None) or {}).get(tool_id)
        if text and page is not None and not getattr(page, "is_placeholder", False):
            try:
                page.prefill(text)
            except Exception as exc:  # noqa: BLE001 - the tool is open either way
                self._toast(f"Couldn't hand that over to the tool: {exc}")

    def on_open_manual(self, payload):
        """A citation was clicked: the manual PDF, at its page."""
        try:
            page = int((payload or {}).get("page"))
        except (TypeError, ValueError):
            return
        if self._retriever is not None and self._retriever.bundle_dir:
            bundle = self._retriever.bundle_dir
        else:
            bundle = self.settings.get("bundle_dir") or default_data_paths()[0]
        pdf = manual_pdf.manual_pdf(bundle, self.settings.get("manual_pdf") or "")
        if pdf is None:
            path, _chosen = QFileDialog.getOpenFileName(
                self, tr("Where is the DaVinci Resolve manual PDF?"), "", tr_filter("PDF files (*.pdf)"))
            if not path:
                return
            pdf = Path(path)
            self._save("manual_pdf", str(pdf))
        try:
            at_page = manual_pdf.open_at_page(pdf, page)
        except OSError as exc:
            self._toast(f"Couldn't open the manual: {exc.strerror or exc}")
            return
        if not at_page:
            self._toast(f"Opened the manual - go to page {page}.")

    # ------------------------------------------------------ conversations

    def on_connection_changed(self, connected):
        # The project questions are only offered while there's a project to read.
        if self.chats.is_empty():
            self._push_transcript()
        self._push_controls()
        self._push_status()

    def _switched(self, forget_backup=False):
        """After the live conversation changes. Any pending proposal is
        dropped: it belongs to the conversation that produced it, and an
        Apply button left live while the transcript explaining it has been
        swapped out would be a good way to apply something to the wrong
        project state."""
        self._pending_question = ""
        self._hide_proposal()
        self._forget_applied()
        self._push_offer()
        self._push_transcript()
        self._push_controls()
        self._push_status()
        self._persist_chats(forget_backup=forget_backup)

    def on_prev_chat(self, _payload):
        if not self._sending and self.chats.go(self.chats.index - 1):
            self._switched()

    def on_next_chat(self, _payload):
        if not self._sending and self.chats.go(self.chats.index + 1):
            self._switched()

    def on_new_chat(self, _payload):
        """Start a fresh conversation, keeping the current one to go back
        to. The proposal is cleared FIRST and unconditionally, even when no
        new chat gets made: New chat leaving a live Apply button on screen
        would let a proposal be applied from a conversation the user
        believes they have closed."""
        if self._sending:
            return
        self._hide_proposal()
        if self.chats.new_chat():
            self._switched()

    def on_search_chats(self, payload):
        self._chat_query = str((payload or {}).get("query") or "")[:200]
        self._push_chat_list()

    def on_select_chat(self, payload):
        if self._sending:
            return
        try:
            index = int((payload or {}).get("index"))
        except (TypeError, ValueError):
            return
        if self.chats.go(index):
            self._switched()

    def on_rename_chat(self, payload):
        if self._sending:
            return
        if self.chats.rename(self.chats.index, (payload or {}).get("title") or ""):
            self._persist_chats()
            self._push_controls()

    def on_pin_chat(self, payload):
        """Pinned conversations stay at the top of Chats."""
        try:
            index = int((payload or {}).get("index"))
        except (TypeError, ValueError):
            return
        if self.chats.pin(index, bool((payload or {}).get("on"))):
            self._persist_chats()
            self._push_chat_list()

    def on_delete_chat(self, payload):
        """One conversation, from Chats (the view has already confirmed).
        Deleting the open one swaps in the next, like switching does."""
        if self._sending:
            return
        try:
            index = int((payload or {}).get("index"))
        except (TypeError, ValueError):
            return
        was = self.chats.current
        if not self.chats.delete(index):
            return
        if self.chats.current is not was:
            self._switched(forget_backup=True)
        else:
            self._persist_chats(forget_backup=True)
            self._push_controls()

    def on_delete_all_chats(self, _payload):
        """Every conversation, from Chats (the view has already confirmed).
        The files go first - including a damaged one that disabled saving -
        so nothing is left on disk to come back at the next launch."""
        if self._sending:
            return
        try:
            chat_store.erase(self.ask_folder)
        except OSError as exc:
            self._toast(f"Could not delete the saved conversations: {exc}")
            return
        self._chat_store_error = ""
        self._chat_query = ""
        self.chats.delete_all()
        self._switched()

    def on_explain_clip(self, _payload):
        if not self._sending:
            self.on_send({"text": "Explain the selected timeline clip, or the video clip at the playhead. Search the manual for the relevant settings and explain any mismatch or limitation you actually found.",
                          "focus": True})

    def on_run_checkup(self, _payload):
        if self._sending or self._checkup_worker is not None:
            return
        controller = self.host.controller if getattr(self.host, "connected", False) else None
        if controller is None:
            self._toast("Connect Buddy to Resolve to check the open project.")
            return
        self._append(YOU, "Check my project", raw=True)
        self._set_sending(True)
        self._checkup_worker = _CheckupWorker(controller)
        self._checkup_worker.done.connect(self._on_checkup_done)
        self._checkup_worker.finished.connect(self._clear_checkup_worker)
        self._checkup_worker.start()

    def _clear_checkup_worker(self):
        worker, self._checkup_worker = self._checkup_worker, None
        if worker is not None:
            worker.deleteLater()

    def _on_checkup_done(self, report):
        self._set_sending(False)
        if report.get("error"):
            self._append(ERROR, "Project checkup failed: " + report["error"], error=True)
            return
        if not report.get("open"):
            answer = report.get("reason") or "No project is open."
            self._append(BUDDY, answer)
            self.chats.record_turn("Check my project", answer, history_limit(self.settings))
            self._persist_chats()
            self._push_controls()
            return
        fmt = report.get("timeline_format") or {}
        edition = (report.get("resolve") or {}).get("product") or "edition unknown"
        lines = [f"**Project checkup: {report.get('project') or 'Untitled'}**",
                 f"Resolve: {edition}. Current timeline: {(report.get('timeline') or {}).get('name') or 'none'}.",
                 f"Scanned {report['scanned_clips']} video clips. Timeline: {fmt.get('fps', '?')} fps, {fmt.get('width', '?')} × {fmt.get('height', '?')}."]
        settings = report.get("settings") or {}
        if settings.get("Color science"):
            lines.append(f"Color science: {settings['Color science']}.")
        issues = report.get("issues") or []
        if issues:
            lines += ["", f"**{len(issues)} findings to review**"]
            lines += [f"- {i['location']} {i['clip']}: {'; '.join(i['details'])}" for i in issues[:50]]
            if len(issues) > 50:
                lines.append(f"- And {len(issues) - 50} more clips.")
        else:
            lines += ["", "No format mismatches or missing local source paths found in the scanned clips."]
        if report.get("recommendations"):
            lines += ["", "**Suggested next steps**"]
            lines += [f"- {item}" for item in report["recommendations"]]
        if report.get("truncated"):
            lines += ["", report["truncated"]]
        lines += ["", "This check covers current timeline video formats and local source paths. It does not verify audio, render settings, proxy availability, or color accuracy."]
        answer = "\n".join(lines)
        self._append(BUDDY, answer, copyable=True, followups=self._checkup_followups(issues), own_followups=True)
        self.chats.record_turn("Check my project", answer, history_limit(self.settings))
        self._persist_chats()
        self._push_controls()
        self._push_suggestions()
        if any(issue.get("kind") == "missing_path" for issue in issues):
            tool = get_tool("media_relink", getattr(self.host, "registry", None))
            if tool and tool.is_available:
                self._set_offer({"tool_id": "media_relink", "label": "Open Media Relink",
                                 "reason": "Some source file paths could not be found on this computer."})

    def _checkup_followups(self, issues) -> list:
        """What to ask next about what the check-up found - one click each."""
        details = " ".join(d for issue in issues for d in issue.get("details") or [])
        asks = []
        if "frame rate" in details:
            asks.append(CHECKUP_FOLLOWUPS["frame_rate"])
        if "upscaled" in details:
            asks.append(CHECKUP_FOLLOWUPS["upscaled"])
        if any(issue.get("kind") == "missing_path" for issue in issues):
            asks.append(CHECKUP_FOLLOWUPS["missing_path"])
        # A change to the project is only offered where those are switched on.
        if (any(issue.get("kind") == "format" for issue in issues)
                and self.settings.get("allow_project_writes", False)):
            asks.append(CHECKUP_FOLLOWUPS["flag"])
        return asks[:3] or [CHECKUP_FOLLOWUPS["clean"]]

    def _on_history_limit_changed(self):
        self.chats.trim(history_limit(self.settings))
        self._push_controls()

    # ---------------------------------------------------- copy and export

    def on_copy(self, payload):
        try:
            block = self.chats.blocks[int((payload or {}).get("i"))]
        except (TypeError, ValueError, IndexError):
            return
        if block["copyable"]:
            QApplication.clipboard().setText(block["body"])
            self._toast("Copied to clipboard")

    def on_export(self, _payload):
        """Save the whole current conversation, as Markdown (the answers
        as written, with their sources) or as plain text - whichever the
        file's ending says. The file dialog starts in the user's Downloads
        folder, with a timestamped default name, and lets them pick a
        different place."""
        downloads = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation) or str(Path.home())
        default_name = f"Buddy Chat {datetime.now().strftime('%Y-%m-%d %H%M%S')}.md"
        path, _filter = QFileDialog.getSaveFileName(
            self, tr("Export conversation"), str(Path(downloads) / default_name), tr_filter(EXPORT_FILTER)
        )
        if not path:
            return
        plain = path.lower().endswith(".txt")
        try:
            Path(path).write_text(self.chats.export_text() if plain else self.chats.export_markdown(),
                                  encoding="utf-8")
        except OSError as e:
            self._toast(f"Could not save the file: {e}")
            return
        self._toast(f"Exported to {path}")

    # ------------------------------------------------- proposed changes

    def _hide_proposal(self):
        self._proposal = None
        self._push_proposal()

    def _show_proposal(self, proposal):
        """Show what would change. Applying is a separate, explicit act.

        The card lists every affected item rather than a count, because the
        failure this exists to catch is the model picking the wrong items -
        and "add 12 markers" looks identical whether the 12 are right or
        wrong.
        """
        self._proposal = proposal
        self._proposal_note = ""
        self._proposal_state = "ready"
        self._push_proposal()

    def _push_proposal(self):
        proposal = self._proposal
        if proposal is None:
            self.emit("proposal", None)
            return
        destructive = bool(getattr(proposal, "destructive", False))
        if self._proposal_note:
            note = self._proposal_note
        elif actions.will_be_revertible(proposal):
            note = "Nothing has been changed yet. Afterwards, Revert last change can put it back."
        elif destructive:
            note = "Nothing has been changed yet. Buddy cannot undo this – use Resolve's own Edit > Undo."
        else:
            note = "Nothing has been changed yet."
        self.emit("proposal", {
            "title": proposal.summary,
            "destructive": destructive,
            "reason": proposal.reason or "",
            # Warnings go ABOVE the item list: they say what happens to
            # things the user did NOT name, which is what decides whether
            # Apply is safe.
            "warnings": list(getattr(proposal, "warnings", None) or []),
            "details": list(proposal.details),
            "note": note,
            "apply_label": "Apply anyway" if destructive else "Apply to my project",
            "state": self._proposal_state,
        })

    def on_discard_proposal(self, _payload):
        if self._proposal is None:
            return
        self._append(BUDDY, "Discarded – nothing was changed.")
        self._hide_proposal()

    def _on_writes_revoked(self):
        # A card already on screen was proposed under the consent just
        # withdrawn, so it goes too (Apply also re-checks the setting).
        self._forget_applied()              # a revert is a change to the project too
        if self._proposal is not None:
            self._hide_proposal()
            self._append(
                BUDDY,
                "Project changes were turned off, so the pending change was cancelled – nothing was changed.",
            )

    def on_apply_proposal(self, _payload):
        """Run the proposal. The ONLY place in this page that writes.

        Runs on the GUI thread: these are short calls, and the worker
        thread must never touch Resolve on this page's behalf (see
        _resolve_source). The card goes busy first so a double-click
        cannot apply the same change twice.
        """
        proposal = self._proposal
        if proposal is None or self._proposal_state != "ready":
            return
        # Consent is re-read here, not trusted from when the card appeared:
        # revoking it must stop a change that is already on screen.
        if not bool(self.settings.get("allow_project_writes", False)):
            self._append(
                BUDDY,
                "Project changes are turned off in Settings, so the proposed "
                "change was cancelled – nothing was changed.",
            )
            self._hide_proposal()
            return
        self._proposal_state = "busy"
        self._push_proposal()

        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError as e:
            self._proposal_state = "ready"
            self._proposal_note = f"Could not reach Resolve: {e}"
            self._push_proposal()
            return

        lines = []
        self.host.set_busy(True, "Applying the change…")
        try:
            outcome = actions.execute(controller, proposal, log=lines.append)
        except Exception as e:  # noqa: BLE001 - shown to the user
            self._proposal_state = "failed"
            self._proposal_note = f"Failed: {e}"
            self._append(BUDDY, f"The change failed: {e}", error=True)
            self._push_proposal()
            return
        finally:
            self.host.set_busy(False)

        detail = "\n".join(f"- {line}" for line in lines)
        self._append(BUDDY, f"**Applied.** {outcome}" + (f"\n\n{detail}" if detail else ""),
                     copyable=True)
        self._hide_proposal()
        self._applied = proposal if actions.can_revert(proposal) else None
        self._push_revert()

    # ------------------------------------------------ revert last change

    def _forget_applied(self):
        if self._applied is not None:
            self._applied = None
            self._push_revert()

    def _push_revert(self):
        applied = self._applied
        self.emit("revert", None if applied is None else {"summary": applied.summary, "busy": self._revert_busy})

    def on_revert_change(self, _payload=None):
        """Put back what the last applied change set (actions.revert) -
        the other place this page writes, and as guarded as Apply: the
        consent is read again, it runs on the GUI thread, and the button
        goes busy first so a double-click can't run it twice."""
        applied = self._applied
        if applied is None or self._revert_busy or self._sending:
            return
        if not bool(self.settings.get("allow_project_writes", False)):
            self._forget_applied()
            self._append(BUDDY, "Project changes are turned off in Settings, so nothing was reverted.")
            return
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError as e:
            self._toast(f"Could not reach Resolve: {e}")
            return
        self._revert_busy = True
        self._push_revert()
        lines = []
        self.host.set_busy(True, "Reverting the change…")
        try:
            outcome = actions.revert(controller, applied, log=lines.append)
        except actions.ActionError as e:
            # The project or timeline is another one now: the button stays,
            # for when the right one is open again.
            self._revert_busy = False
            self._push_revert()
            self._append(BUDDY, f"Nothing was reverted: {e}", error=True)
            return
        except Exception as e:  # noqa: BLE001 - shown to the user
            self._revert_busy = False
            self._applied = None
            self._push_revert()
            self._append(BUDDY, f"The revert failed part-way: {e}", error=True)
            return
        finally:
            self.host.set_busy(False)
        self._revert_busy = False
        self._applied = None
        self._push_revert()
        detail = "\n".join(f"- {line}" for line in lines)
        self._append(BUDDY, f"**Reverted.** {outcome}" + (f"\n\n{detail}" if detail else ""), copyable=True)

    # ------------------------------------------------------------ settings

    def _save(self, key, value):
        """ToolSettings holds values in memory until save() is called, and
        the shared dialog has no per-field apply - so persist on edit."""
        self.settings[key] = value
        self.settings.save()
