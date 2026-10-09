"""Dailies' page: what it sends the view on each action, the stills it keeps,
and the preview never taking the previous clip's frame for the next one's."""

import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from core import ffmpeg_log

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtMultimedia import QVideoFrame
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete
    from pages.asset_manager.player import MediaPreview
    from pages.dailies.page import DailiesPage
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class Settings(dict):
    def save(self):
        pass


class Host:
    connected = False
    controller = None
    shared_settings = {"theme": "Resolve"}

    def tool_settings(self, _tool, defaults):
        return Settings(defaults)

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")


def catalog(count=3):
    clips = {}
    for n in range(count):
        cid = f"c{n}"
        clips[cid] = {"id": cid, "name": f"Clip {n}", "bin": "Master", "bin_id": "root",
                      "path": f"C:/nowhere/{cid}.mov", "type": "Video", "duration": "00:00:10:00",
                      "metadata": {"Comments": "", "Tag": "0"}}
    return {"project_id": "p1", "project_name": "Film", "current_bin": "Master",
            "current_bin_id": "root", "current_ids": list(clips), "clips": clips, "objects": {}}


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PageMessageTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        self.page = DailiesPage(Host())
        self.addCleanup(delete, self.page)
        self.addCleanup(self.page.on_app_quitting)   # runs first: ends its threads
        self.events = []
        self.page.emit = lambda name, value=None: self.events.append((name, value))
        self.page.catalog = catalog()
        self.page.current_id = "c0"
        self.page._push()
        self.events.clear()

    def sent(self, name):
        return [value for event, value in self.events if event == name]

    def test_picking_a_clip_sends_no_tape_and_no_catalog(self):
        self.page.on_pick_clip({"id": "c1"})
        names = [name for name, _value in self.events]
        self.assertNotIn("tape", names)
        self.assertNotIn("catalog", names)
        self.assertEqual(names.count("current"), 1)
        self.assertEqual(self.sent("state")[-1]["current"], "c1")
        self.assertNotIn("clips", self.sent("state")[-1])

    def test_scrolling_crops_video_without_resizing_its_picture(self):
        self.page.on_stage_geometry({"x": 20, "y": 100, "width": 400, "height": 150,
                                     "stage": {"x": 20, "y": 50, "width": 400, "height": 200}})
        surface = self.page._surface
        if surface.covers_whole_stage():        # the QVideoWidget stand-in: masked
            self.assertEqual((self.page._video_rect.width(), self.page._video_rect.height()), (400, 200))
            clip = self.page._video_widget.mask().boundingRect()
            self.assertEqual((clip.x(), clip.y(), clip.width(), clip.height()), (0, 50, 400, 150))
        else:
            # A mask is ignored on the stacked-on-top QQuickWidget (the whole stage
            # drew over the page's bars): the widget covers only what's in view...
            rect = self.page._video_rect
            origin = self.page.view.mapTo(self.page, QPoint(20, 50))
            self.assertEqual((rect.x(), rect.y(), rect.width(), rect.height()),
                             (origin.x(), origin.y() + 50, 400, 150))
            # ...and the picture keeps the stage's size, shifted up by what's hidden.
            root = surface._root
            self.assertEqual([root.property(n) for n in ("stageWidth", "stageHeight", "cropX", "cropY")],
                             [400, 200, 0, 50])
        self.page.on_stage_geometry({"x": 20, "y": 100, "width": 0, "height": 0,
                                     "stage": {"x": 20, "y": 50, "width": 400, "height": 200}})
        self.assertTrue(self.page._video_rect.isEmpty())

    def test_nothing_is_resent_when_nothing_changed(self):
        self.page._push()
        self.assertEqual(self.events, [])

    def test_a_clip_seen_before_comes_back_with_its_still(self):
        self.page._on_frame("c0", "data:image/jpeg;base64,AAA")
        self.page.on_pick_clip({"id": "c1"})
        self.assertEqual(self.sent("current")[-1]["frame"], "")
        self.page.on_pick_clip({"id": "c0"})
        self.assertEqual(self.sent("current")[-1]["frame"], "data:image/jpeg;base64,AAA")

    def test_a_still_decoded_again_is_not_sent_again(self):
        self.page._on_frame("c0", "data:image/jpeg;base64,AAA")
        self.page._on_frame("c0", "data:image/jpeg;base64,AAA")
        self.assertEqual(len(self.sent("frame")), 1)

    def test_clips_added_to_an_empty_source_put_the_first_up(self):
        self.page.on_create_source({"name": "Day 1", "scope": "selected", "ids": ["c0"]})
        self.page.on_remove_clip({"id": "c0"})
        self.assertIsNone(self.page.current_id)
        self.page.on_add_clips({"ids": ["c2", "c1"]})
        self.assertEqual(self.page.current_id, "c2")
        self.assertEqual(self.sent("current")[-1]["clip"]["id"], "c2")

    def test_the_transcript_box_says_what_language_it_heard_and_offers_the_choice(self):
        menu = [("", "Auto-detect"), ("en", "English"), ("ja", "Japanese"), ("mixed", "Mixed: English + Japanese")]
        self.page._transcripts["c0"] = {"status": "done", "segments": [{"text": "はい"}], "message": "",
                                        "language": "ja"}
        with mock.patch("pages.dailies.transcripts.choices", return_value=menu):
            self.page._push_transcript()
        sent = self.sent("transcript")[-1]
        self.assertEqual((sent["language"], sent["heard"]), ("", "Japanese"))
        self.assertEqual([o["value"] for o in sent["languages"]], ["", "en", "ja", "mixed"])
        self.assertEqual(sent["languages"][0]["label"], "Auto-detect")

    def test_choosing_a_language_hears_the_clip_again_in_it(self):
        menu = [("", "Auto-detect"), ("en", "English")]
        self.page._transcripts["c0"] = {"status": "done", "segments": [{"text": "はい"}], "message": "", "language": "ja"}
        with mock.patch("pages.dailies.transcripts.choices", return_value=menu), \
                mock.patch.object(self.page, "_start_transcript", return_value=True) as start:
            self.page.on_transcript_language({"language": "fr"})           # not on the menu: nothing
            self.assertEqual(self.page.settings["language"], "")
            start.assert_not_called()
            self.page.on_transcript_language({"language": "en"})
            self.assertEqual(self.page.settings["language"], "en")
            self.assertEqual(self.page._transcripts, {})                  # heard as Japanese: gone
            start.assert_called_once_with("c0")
            self.assertEqual(self.sent("transcript")[-1]["language"], "en")
            self.page.on_transcript_language({"language": "en"})           # the same again: nothing
            start.assert_called_once()

    def test_mixed_languages_are_ticked_in_dailies_itself(self):
        with mock.patch("pages.dailies.transcripts.mixed_languages", return_value=[]), \
                mock.patch.object(self.page, "_start_transcript", return_value=True) as start:
            self.page._sent.pop("transcript", None)
            self.page._push_transcript()
            sent = self.sent("transcript")[-1]
            self.assertEqual(sent["languages"][-1]["label"], "Mixed languages…")
            self.assertEqual(sent["mixed"], [])
            self.assertIn({"code": "ja", "name": "Japanese"}, sent["spoken"])
            self.page.on_transcript_language({"language": "mixed"})          # nothing ticked: the page asks first
            self.assertEqual(self.page.settings["language"], "")
            start.assert_not_called()
            self.page.on_transcript_mixed({"codes": ["en"]})                 # one isn't mixed
            self.assertEqual(self.page.settings["language"], "")
            self.page.on_transcript_mixed({"codes": ["en", "ja", "zz"]})
            self.assertEqual(self.page.settings["language"], "mixed")
            self.assertEqual(self.page.settings["mixed_languages"], ["en", "ja"])
            start.assert_called_once_with("c0")
            sent = self.sent("transcript")[-1]
            self.assertEqual((sent["language"], sent["mixed"]), ("mixed", ["en", "ja"]))
            self.assertEqual(sent["languages"][-1]["label"], "Mixed: English + Japanese")
            # Ticked again with a change: heard again, even though "mixed" was current.
            self.page.on_transcript_mixed({"codes": ["en", "ja", "fr"]})
            self.assertEqual(start.call_count, 2)
            self.assertEqual(self.page.settings["mixed_languages"], ["en", "ja", "fr"])

    def test_the_subtitles_tools_mixed_set_is_the_starting_point(self):
        with mock.patch("pages.dailies.transcripts.mixed_languages", return_value=["en", "ja"]):
            self.assertEqual(self.page._mixed_languages(), ["en", "ja"])
            self.page.settings["mixed_languages"] = ["de", "fr"]
            self.assertEqual(self.page._mixed_languages(), ["de", "fr"])

    def test_the_language_chosen_reaches_the_engine_and_the_cache(self):
        self.page.settings["language"] = "en"
        with tempfile.TemporaryDirectory() as folder:
            media = os.path.join(folder, "c0.mov")
            with open(media, "wb") as handle:
                handle.write(b"one")
            self.page.catalog["clips"]["c0"]["path"] = media
            with mock.patch("pages.dailies.transcripts.engine_plan", return_value=(None, "no engine")) as plan, \
                    mock.patch("pages.dailies.transcripts.cached", return_value=None) as cached:
                self.page._start_transcript("c0")
            plan.assert_called_once_with([], "en")
            cached.assert_called_once_with(media, languages=[], language="en")
            self.page.settings["language"] = "mixed"
            with mock.patch("pages.dailies.transcripts.mixed_languages", return_value=["en", "ja"]), \
                    mock.patch("pages.dailies.transcripts.engine_plan", return_value=(None, "no engine")) as plan, \
                    mock.patch("pages.dailies.transcripts.cached", return_value=None):
                self.page._start_transcript("c0")
            plan.assert_called_once_with(["en", "ja"], "")

    def test_turning_transcription_back_on_retries_what_failed(self):
        self.page._transcripts["c0"] = {"status": "error", "segments": [], "message": "Worker crashed"}
        self.page.on_transcribe({"on": False})
        with mock.patch.object(self.page, "_start_transcript") as start:
            self.page.on_transcribe({"on": True})
        start.assert_called_once_with("c0")

    def still_up(self):
        """c0 as a still, open on screen."""
        self.page.catalog["clips"]["c0"]["type"] = "Image"
        self.page.ready_id = "c0"
        self.events.clear()

    def test_a_still_can_be_moved_along_paused(self):
        self.still_up()
        self.page.on_seek_to({"id": "c0", "ms": 2000})
        self.assertEqual(self.sent("position")[-1], {"position": 2000, "duration": 5000})
        # Played from there: what's left of its five seconds.
        self.page._play(1.0)
        self.assertAlmostEqual(self.page._still_timer.interval(), 3000, delta=100)
        self.page._pause()
        self.assertGreaterEqual(self.page._still_from, 2000)
        self.assertLess(self.page._still_from, 2200)

    def test_a_still_moved_while_playing_runs_on_from_there(self):
        self.still_up()
        self.page._play(1.0)
        self.page.on_seek_to({"id": "c0", "ms": 4000})
        self.assertTrue(self.page._still_clock.isActive())
        self.assertLessEqual(self.page._still_timer.interval(), 1000)
        self.assertEqual(self.sent("position")[-1]["position"], 4000)
        # J: backwards from there, back to the start - 4 s.
        self.page.on_shuttle({"way": -1})
        self.assertEqual(self.page.rate, -1.0)
        self.assertAlmostEqual(self.page._still_timer.interval(), 4000, delta=300)

    def test_reads_go_through_the_reader_process(self):
        asked = []
        reader = self.page._calls.reader
        with mock.patch.object(reader, "available", return_value=True), \
                mock.patch.object(reader, "ask", side_effect=lambda *a: asked.append(a) or True), \
                mock.patch.object(self.page._worker, "start") as worker:
            self.page.host.connected = True
            self.page.host.controller = object()
            self.page.on_refresh(None)
        self.assertEqual([name for name, *_ in asked], ["dailies.scan"])
        worker.assert_not_called()

    def test_the_readers_location_list_matches_the_open_bin(self):
        with mock.patch.object(self.page, "isVisible", return_value=True), \
                mock.patch.object(self.page, "on_refresh") as refresh:
            self.page.host.connected = True
            self.page._on_location(["p1", "root"], None)     # JSON from the reader's process
            refresh.assert_not_called()
            self.page._on_location(["p1", "other-bin"], None)
            refresh.assert_called_once()

    def test_catalog_only_on_request(self):
        self.page.on_catalog(None)
        self.assertEqual(sorted(c["id"] for c in self.sent("catalog")[-1]), ["c0", "c1", "c2"])

    def test_sources_carry_counts_not_every_clip_id(self):
        self.page.on_create_source({"name": "Day 1", "scope": "all"})
        source = self.sent("state")[-1]["sources"][0]
        self.assertEqual((source["name"], source["count"]), ("Day 1", 3))
        self.assertNotIn("ids", source)

    def test_the_tape_carries_each_clips_length_and_colour(self):
        self.page.catalog["clips"]["c1"]["seconds"] = 12.5
        self.page.catalog["clips"]["c1"]["metadata"]["Clip Color"] = "Teal"
        self.page._push_tape()
        rows = {row["id"]: row for row in self.sent("tape")[-1]["clips"]}
        self.assertEqual((rows["c1"]["seconds"], rows["c1"]["color"]), (12.5, "Teal"))
        # A colour picked in the notes shows on the tape before it's applied.
        self.page.on_edit({"id": "c1", "color": "Pink"})
        rows = {row["id"]: row for row in self.sent("tape")[-1]["clips"]}
        self.assertEqual(rows["c1"]["color"], "Pink")
        self.page.on_edit({"id": "c1", "color": "Not a colour"})
        self.assertEqual(self.page._drafts()["c1"]["color"], "Pink")

    def test_seeking_into_another_clip_opens_it_and_moves_there_once_ready(self):
        with mock.patch.object(self.page.preview, "seek_ms") as seek_ms:
            self.page.on_seek_to({"id": "c2", "ms": 4000})
            self.assertEqual(self.page.current_id, "c2")
            self.assertEqual(self.page._pending_seek, ("c2", 4000.0))
            seek_ms.assert_not_called()
            self.page._on_ready("c2", "video")
            seek_ms.assert_called_once_with(4000.0, "c2")
            # Within the clip that's open, it's just a seek.
            self.page.on_seek_to({"id": "c2", "ms": 1500})
            seek_ms.assert_called_with(1500.0, "c2")
        self.assertEqual(self.page.current_id, "c2")

    def test_seek_to_a_clip_not_on_the_tape_is_ignored(self):
        self.page.on_seek_to({"id": "elsewhere", "ms": 10})
        self.assertEqual(self.page.current_id, "c0")

    def test_viewer_choice_is_kept(self):
        self.page.on_viewer({"viewer": "source"})
        self.assertEqual(self.sent("state")[-1]["viewer"], "source")
        self.assertEqual(self.page.settings["viewer"], "source")
        self.page.on_viewer({"viewer": "sideways"})
        self.assertEqual(self.page.settings["viewer"], "source")

    def test_a_clip_with_no_file_says_so_instead_of_transcribing(self):
        self.page.on_pick_clip({"id": "c1"})
        transcript = self.sent("transcript")[-1]
        self.assertEqual((transcript["id"], transcript["status"]), ("c1", "error"))
        self.assertIsNone(self.page._transcript_job)

    def test_a_clip_with_a_proxy_plays_from_it_unless_told_not_to(self):
        """Play proxies (a tool setting, on by default): the proxy beside the
        source is what the deck opens, and the state says so; off, the
        original - the clip on screen opened again where it was."""
        self.assertEqual([f["key"] for f in self.page.settings_fields() if f["kind"] == "check"], ["proxies"])
        proxy = "C:/nowhere/Proxy/c0_proxy.mov"
        with mock.patch("pages.dailies.page.os.path.isfile", return_value=True), \
                mock.patch("pages.dailies.page.proxies.known_proxy", return_value=proxy), \
                mock.patch.object(self.page.preview, "open") as open_, \
                mock.patch.object(self.page.preview, "position_ms", return_value=4321):
            self.page._open_current()
            self.assertEqual(open_.call_args[0][:2], ("c0", proxy))
            self.assertTrue(self.sent("state")[-1]["proxy"])
            self.assertTrue(self.sent("state")[-1]["proxies"])
            self.page.on_setting("proxies", False, None)
            self.assertEqual(open_.call_args[0][:2], ("c0", "C:/nowhere/c0.mov"))
            self.assertEqual(self.page._pending_seek, ("c0", 4321))    # where it was
            self.assertFalse(self.page.settings["proxies"])
            self.assertFalse(self.sent("state")[-1]["proxy"])
            self.page.on_setting("proxies", True, None)
            self.assertEqual(open_.call_args[0][:2], ("c0", proxy))
        with mock.patch("pages.dailies.page.os.path.isfile", return_value=True), \
                mock.patch("pages.dailies.page.proxies.known_proxy", return_value=""), \
                mock.patch.object(self.page.preview, "open") as open_:
            self.page._open_current()                                  # no proxy: the original
            self.assertEqual(open_.call_args[0][:2], ("c0", "C:/nowhere/c0.mov"))
            self.assertFalse(self.sent("state")[-1]["proxy"])

    def test_playing_on_into_the_next_clip_moves_the_page_not_the_player(self):
        self.page.auto_play = True
        with mock.patch.object(self.page.preview, "stop") as stop,                 mock.patch.object(self.page.preview, "open") as open_:
            self.page._on_ended("c0", "c1")
        self.assertEqual((self.page.current_id, self.page.ready_id), ("c1", "c1"))
        stop.assert_not_called()
        open_.assert_not_called()
        self.assertEqual(self.page._surface_clip, "c1")     # the surface stays up
        self.assertEqual(self.sent("state")[-1]["current"], "c1")

    def test_sub_bins_join_the_current_bin_tape_when_ticked(self):
        self.page.catalog["current_ids"] = ["c0"]
        self.page.catalog["current_tree_ids"] = ["c0", "c2"]
        self.page.on_select_source({"id": "@bin"})
        self.assertEqual([r["id"] for r in self.sent("tape")[-1]["clips"]], ["c0"])
        self.page.on_sub_bins({"on": True})
        self.assertEqual([r["id"] for r in self.sent("tape")[-1]["clips"]], ["c0", "c2"])
        self.assertTrue(self.sent("state")[-1]["sub_bins"])
        self.assertTrue(self.page.settings["sub_bins"])
        self.assertEqual(self.page.current_id, "c0")        # still on the tape: stays
        self.page.on_create_source({"name": "Day 1", "scope": "bin"})
        self.assertEqual(self.page._sources()[0]["ids"], ["c0", "c2"])
        # Off again, from a clip that only a sub-bin had: back to the bin's first.
        self.page.on_select_source({"id": "@bin"})
        self.page.on_pick_clip({"id": "c2"})
        self.page.on_sub_bins({"on": False})
        self.assertEqual(self.page.current_id, "c0")
        self.assertEqual([r["id"] for r in self.sent("tape")[-1]["clips"]], ["c0"])

    def test_j_k_and_l_shuttle_like_resolve(self):
        rates = []
        with mock.patch.object(self.page, "_open_current"), \
                mock.patch.object(self.page.preview, "set_rate", side_effect=rates.append):
            self.page.on_shuttle({"way": 1})                 # L: plays
            self.assertEqual((self.page.auto_play, self.page.rate), (True, 1.0))
            for _ in range(6):                                # L L L...: 2x 4x 8x 16x, no further
                self.page.on_shuttle({"way": 1})
            self.assertEqual(self.page.rate, 16.0)
            self.assertEqual(self.sent("state")[-1]["rate"], 16.0)
            self.page.on_shuttle({"way": -1})                # J while going forwards: backwards
            self.assertEqual(self.page.rate, -1.0)
            self.page.on_shuttle({"way": -1})
            self.assertEqual(self.page.rate, -2.0)
            self.page.on_shuttle({"way": 0})                 # K: stops, and the speed is 1 again
            self.assertEqual((self.page.auto_play, self.page.rate), (False, 1.0))
            self.page.on_shuttle({"way": -1})                # J from stopped: backwards at 1x
            self.assertEqual((self.page.auto_play, self.page.rate), (True, -1.0))
            self.page.on_play(None)                           # Space pauses...
            self.page.on_play(None)                           # ...and plays at normal speed
            self.assertEqual((self.page.auto_play, self.page.rate), (True, 1.0))
        self.assertEqual(rates[:2], [1.0, 2.0])
        self.assertEqual(rates[-1], 1.0)

    def test_backwards_past_a_clips_start_goes_on_into_the_one_before(self):
        self.page.catalog["clips"]["c0"]["seconds"] = 8.0
        self.page.current_id = "c1"
        self.page.auto_play, self.page.rate = True, -4.0
        with mock.patch.object(self.page, "_show_selected") as show:
            self.page._on_at_start("c1")
        self.assertEqual((self.page.current_id, self.page.auto_play, self.page.rate), ("c0", True, -4.0))
        self.assertEqual(self.page._pending_seek, ("c0", 7999.0))     # from its last frame
        show.assert_called_once_with(open_now=True)
        # At the tape's start there's nowhere to go: it stops there.
        self.page._on_at_start("c0")
        self.assertEqual((self.page.current_id, self.page.auto_play, self.page.rate), ("c0", False, 1.0))

    def test_a_clips_shape_reaches_the_page_and_comes_back_with_it(self):
        self.page._on_shape("c0", 1080, 1920)
        self.assertEqual(self.sent("shape")[-1], {"id": "c0", "shape": [1080, 1920]})
        self.page._on_shape("c1", 1920, 1080)        # not the clip on screen: kept for later
        self.assertEqual(len(self.sent("shape")), 1)
        self.page.on_pick_clip({"id": "c1"})
        self.assertEqual(self.sent("current")[-1]["shape"], [1920, 1080])
        self.page.on_pick_clip({"id": "c2"})
        self.assertIsNone(self.sent("current")[-1]["shape"])

    def test_a_player_that_went_somewhere_unexpected_is_stopped(self):
        self.page.auto_play = True
        with mock.patch.object(self.page.preview, "stop") as stop,                 mock.patch.object(self.page, "_advance") as advance:
            self.page._on_ended("c0", "c2")     # not the clip after c0
        stop.assert_called()
        advance.assert_called_once()


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TapePlayerTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtMultimedia import QVideoSink
        from pages.dailies.tape_player import TapePlayer
        self.app = QApplication.instance() or QApplication([])

        class Surface(QObject):
            drawn = Signal(QVideoFrame)
        self.surface = Surface()
        self.sink = QVideoSink()
        self.player = TapePlayer(None, self.sink, self.surface.drawn)
        # Retired decks are deleteLater()'d, and nothing here runs an event
        # loop: deleted before this test's sink goes, not left to outlive it
        # (an access violation in a pooled thread, 1 run in 10).
        self.addCleanup(QCoreApplication.sendPostedEvents, None, QEvent.DeferredDelete)
        self.addCleanup(self.player.shutdown)
        self.ready = []
        self.player.ready.connect(lambda clip_id, kind: self.ready.append(clip_id))

    def test_the_waiting_clip_is_taken_over_not_reopened(self):
        self.player.preload("next", "C:/nowhere/next.mov", "Video")
        waiting = self.player.spare
        waiting.state = "ready"                 # as if paused on its first frame
        self.player.open("next", "C:/nowhere/next.mov", "Video", play=False)
        self.assertIs(self.player.active, waiting)
        self.assertIsNone(self.player.spare)
        self.assertEqual(self.ready, ["next"])
        self.assertTrue(waiting.attached)       # showing its frame on the surface

    def test_a_different_clip_gets_a_fresh_deck(self):
        self.player.preload("next", "C:/nowhere/next.mov", "Video")
        waiting = self.player.spare
        self.player.open("other", "C:/nowhere/other.mov", "Video")
        self.assertIsNot(self.player.active, waiting)
        self.assertEqual(self.player.active.clip_id, "other")
        self.assertIs(self.player.spare, waiting)   # still waiting for its turn

    def test_a_retired_deck_reports_nothing(self):
        self.player.open("one", "C:/nowhere/one.mov", "Video")
        deck = self.player.active
        self.player.stop()
        self.assertEqual(deck.state, "retired")
        self.player._on_status(deck, deck.player.mediaStatus())
        self.player._on_error(deck, "late error")
        self.assertEqual(self.ready, [])

    def test_backwards_steps_the_paused_deck_back_to_its_start(self):
        """This backend clamps a negative rate to 0 (Qt 6.11.2), so backwards
        is a paused deck moved back by the clock (tape_player.py)."""
        starts = []
        self.player.at_start.connect(starts.append)
        self.player.open("one", "C:/nowhere/one.mov", "Audio")
        deck = self.player.active
        deck.state = "ready"
        self.player.set_rate(-2.0)
        self.player.play()
        self.assertTrue(self.player.is_playing())
        self.assertNotEqual(deck.player.playbackState(), deck.player.PlaybackState.PlayingState)
        self.player._reverse["from"] = 1000
        with mock.patch("pages.dailies.tape_player.monotonic", return_value=self.player._reverse["at"] + 0.2), \
                mock.patch.object(deck.player, "setPosition") as set_position:
            self.player._reverse_tick()
        set_position.assert_called_once_with(600)            # 0.2 s at 2x, back from 1 s
        with mock.patch("pages.dailies.tape_player.monotonic", return_value=self.player._reverse["at"] + 0.6):
            self.player._reverse_tick()
        self.assertEqual(starts, ["one"])
        self.assertFalse(self.player.is_playing())
        self.player.set_rate(1.0)

    def test_a_sideways_stored_frame_is_measured_upright(self):
        from PySide6.QtCore import QSize
        from PySide6.QtMultimedia import QtVideo, QVideoFrameFormat
        from pages.dailies.tape_player import _shape
        fmt = QVideoFrameFormat(QSize(1920, 1080), QVideoFrameFormat.PixelFormat.Format_BGRA8888)
        self.assertEqual(_shape(QVideoFrame(fmt)), (1920, 1080))
        fmt.setRotation(QtVideo.Rotation.Clockwise90)
        self.assertEqual(_shape(QVideoFrame(fmt)), (1080, 1920))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TranscriptCacheTests(unittest.TestCase):
    def test_a_transcript_is_kept_for_that_exact_file(self):
        from pages.dailies import transcripts
        with tempfile.TemporaryDirectory() as folder:
            media = os.path.join(folder, "clip.mov")
            with open(media, "wb") as handle:
                handle.write(b"one")
            cache = os.path.join(folder, "cache")
            self.assertIsNone(transcripts.cached(media, cache))
            transcripts.save(media, {"segments": [{"start": 0, "end": 1, "text": "Hi"}]}, cache)
            self.assertEqual(transcripts.cached(media, cache)["segments"][0]["text"], "Hi")
            with open(media, "ab") as handle:
                handle.write(b"changed")   # re-recorded / replaced: a different file
            self.assertIsNone(transcripts.cached(media, cache))

    def test_a_named_language_is_kept_apart_from_a_detected_one(self):
        from pages.dailies import transcripts
        with tempfile.TemporaryDirectory() as folder:
            media = os.path.join(folder, "clip.mov")
            with open(media, "wb") as handle:
                handle.write(b"one")
            cache = os.path.join(folder, "cache")
            transcripts.save(media, {"segments": [{"text": "はい"}], "language": "ja"}, cache)      # detected (wrongly)
            transcripts.save(media, {"segments": [{"text": "Hi"}], "language": "en"}, cache, language="en")
            self.assertEqual(transcripts.cached(media, cache)["segments"][0]["text"], "はい")
            self.assertEqual(transcripts.cached(media, cache, language="en")["segments"][0]["text"], "Hi")
            self.assertIsNone(transcripts.cached(media, cache, language="fr"))

    def test_a_language_choice_is_heard_as_the_worker_needs_it(self):
        from pages.dailies import transcripts
        self.assertEqual(transcripts.heard(""), ([], ""))
        self.assertEqual(transcripts.heard("en"), ([], "en"))
        self.assertEqual(transcripts.heard("mixed", ["en", "ja"]), (["en", "ja"], ""))
        self.assertEqual(transcripts.heard("mixed", ["en", "xx"]), ([], ""))         # one real one: detect
        self.assertEqual(transcripts.heard("mixed"), ([], ""))                       # none ticked: detect
        self.assertEqual(transcripts.choices(["en", "ja"])[-1], ("mixed", "Mixed: English + Japanese"))
        self.assertEqual(transcripts.choices()[-1], ("mixed", "Mixed languages…"))   # opens the picker
        self.assertEqual(transcripts.choices()[0], ("", "Auto-detect"))
        self.assertEqual(transcripts.valid(["ja", "nope", "en"]), ["ja", "en"])
        self.assertIn({"code": "ja", "name": "Japanese"}, transcripts.spoken())
        self.assertEqual(transcripts.language_name("ja"), "Japanese")
        self.assertEqual(transcripts.language_name(""), "")

    def test_a_named_language_reaches_the_worker(self):
        from pages.dailies import transcripts
        from pathlib import Path
        whisper = {"large-v3": "C:/models/large-v3"}
        with mock.patch("pages.transcribe.env_setup.venv_python", return_value=Path(__file__)), \
                mock.patch("pages.transcribe.env_setup.installed_models", return_value=whisper):
            plan, why = transcripts.engine_plan([], "en")
            self.assertEqual((plan["language"], plan["languages"]), ("en", []))
            plan, why = transcripts.engine_plan()
            self.assertEqual(plan["language"], "")

    def test_mixed_languages_are_kept_apart_from_one_language(self):
        from pages.dailies import transcripts
        with tempfile.TemporaryDirectory() as folder:
            media = os.path.join(folder, "clip.mov")
            with open(media, "wb") as handle:
                handle.write(b"one")
            cache = os.path.join(folder, "cache")
            transcripts.save(media, {"segments": [{"text": "Hi"}]}, cache)                          # one language
            self.assertIsNone(transcripts.cached(media, cache, languages=["en", "ja"]))            # not reused
            transcripts.save(media, {"segments": [{"text": "Hi はい"}]}, cache, languages=["ja", "en"])
            self.assertEqual(transcripts.cached(media, cache, languages=["en", "ja"])["segments"][0]["text"], "Hi はい")
            self.assertEqual(transcripts.cached(media, cache)["segments"][0]["text"], "Hi")

    def test_the_transcribe_tools_mixed_languages_reach_the_worker(self):
        from pages.dailies import transcripts
        from pathlib import Path
        whisper = {"large-v3": "C:/models/large-v3"}
        with mock.patch("pages.transcribe.env_setup.venv_python", return_value=Path(__file__)),                 mock.patch("pages.transcribe.env_setup.installed_models", return_value=whisper):
            plan, why = transcripts.engine_plan(["en", "ja"])
            self.assertEqual((plan["engine"], plan["languages"]), ("whisper", ["en", "ja"]))
            # A model that can't be told each part's language still transcribes - one language.
            with mock.patch("pages.transcribe.plan.plan_for",
                            side_effect=lambda m, ms, lang, mixed=(): (None, "no") if mixed else
                            ({"engine": "parakeet", "whisper": "", "parakeet": "p", "language": lang}, None)):
                plan, why = transcripts.engine_plan(["en", "ja"])
            self.assertEqual((plan["engine"], plan["languages"]), ("parakeet", []))

    def test_no_engine_installed_is_explained(self):
        from pages.dailies import transcripts
        from pathlib import Path
        with mock.patch("pages.transcribe.env_setup.venv_python", return_value=Path("C:/no/such/python.exe")):
            plan, why = transcripts.engine_plan()
        self.assertIsNone(plan)
        self.assertIn("Subtitles", why)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PreviewStillTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.preview = MediaPreview()
        self.addCleanup(delete, self.preview)
        self.frames = []
        self.preview.frame.connect(lambda clip_id, _src: self.frames.append(clip_id))
        image = QImage(64, 36, QImage.Format_RGB32)
        image.fill(QColor("red"))
        self.frame = QVideoFrame(image)

    def test_a_frame_before_the_clip_starts_priming_is_the_previous_clips(self):
        # show() has named the next clip, but its priming play hasn't begun:
        # a frame now is the old clip's, still in the pipeline.
        self.preview._pending_id, self.preview._kind = "next", "video"
        self.preview._on_video_frame(self.frame)
        self.assertEqual(self.frames, [])
        self.preview._priming_id = "next"
        self.preview._on_video_frame(self.frame)
        self.assertEqual(self.frames, ["next"])


class FfmpegLogTests(unittest.TestCase):
    def test_finds_windows_and_mac_layouts(self):
        with tempfile.TemporaryDirectory() as folder:
            os.makedirs(os.path.join(folder, "Qt", "lib"))
            for name in ("avutil-59.dll", os.path.join("Qt", "lib", "libavutil.59.dylib"), "avcodec-61.dll"):
                open(os.path.join(folder, name), "w").close()
            found = [os.path.basename(p) for p in ffmpeg_log.avutil_candidates(folder)]
        self.assertEqual(sorted(found), ["avutil-59.dll", "libavutil.59.dylib"])

    def test_left_alone_when_debugging_playback(self):
        with mock.patch("ctypes.CDLL") as cdll:
            self.assertIsNone(ffmpeg_log.silence({"QT_FFMPEG_DEBUG": "1"}))
        cdll.assert_not_called()

    def test_frames_reach_the_screen_as_pictures_unless_asked_otherwise(self):
        """Shared GPU textures froze Dailies at a cut (core/ffmpeg_log.cpu_frames)."""
        env = {}
        self.assertEqual(ffmpeg_log.cpu_frames(env), "1")
        self.assertEqual(env["QT_DISABLE_HW_TEXTURES_CONVERSION"], "1")
        env = {"QT_DISABLE_HW_TEXTURES_CONVERSION": "0"}
        self.assertEqual(ffmpeg_log.cpu_frames(env), "0")


if __name__ == "__main__":
    unittest.main()
