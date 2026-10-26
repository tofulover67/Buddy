"""The Proxy tab's pure logic: naming and claiming proxy paths, video
detection, the ffmpeg command, render/part-file behaviour, and the
report lines. No Qt, no Resolve, no real ffmpeg - render_one's process
call is replaced."""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.project_setup import proxy


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def touch(self, rel, content=b"x"):
        path = os.path.join(self.dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content)
        return path


class EntryTests(unittest.TestCase):
    def _clip(self, name="A001", path="C:/media/A001.mp4", type_="video", uid="u1",
              props=None, fail_props=False):
        class Clip:
            def GetClipProperty(self, *a):
                if fail_props:
                    raise RuntimeError("bridge went away")
                return props if a == () else (props or {}).get(a[0])
            def GetName(self):
                return name
            def GetUniqueId(self):
                return uid
        return Clip()

    def test_entry_reads_path_name_and_type(self):
        clip = self._clip(props={"File Path": "C:/media/A001.mp4", "Type": "video"})
        entry = proxy._entry(clip)
        self.assertEqual(entry, {"id": "u1", "name": "A001", "path": "C:/media/A001.mp4", "type": "video", "tc": "",
                                 "codec": "", "proxy": "", "proxy_path": ""})

    def test_entry_survives_a_dead_bridge(self):
        self.assertIsNone(proxy._entry(self._clip(fail_props=True)))

    def test_entries_dedupes_by_id(self):
        clip = self._clip(props={"File Path": "p", "Type": "video"})
        entries, objects = proxy.entries([clip, clip])
        self.assertEqual(len(entries), 1)
        self.assertEqual(list(objects), ["u1"])


class IsVideoTests(unittest.TestCase):
    def entry(self, path, type_=""):
        return {"id": "x", "name": "n", "path": path, "type": type_}

    def test_video_extensions_and_types(self):
        for path in ("a.mp4", "a.MOV", "a.braw", "a.mxf"):
            self.assertTrue(proxy.is_video(self.entry(path)), path)
        self.assertTrue(proxy.is_video(self.entry("weird container", "video")))
        self.assertTrue(proxy.is_video(self.entry("weird container", "Movie")))

    def test_audio_image_and_unknown_are_not_video(self):
        for path in ("a.wav", "a.mp3", "a.jpg", "a.tif", "a.dpx", "a.exr"):
            self.assertFalse(proxy.is_video(self.entry(path)), path)
        self.assertFalse(proxy.is_video(self.entry("no extension at all")))


class ClaimTests(_Tmp):
    def test_first_proxy_takes_the_plain_name(self):
        src = self.touch("footage/A001.mp4")
        kind, dst = proxy._claim(src, {})
        self.assertEqual(kind, "render")
        self.assertEqual(dst, os.path.join(self.dir, "footage", "Proxy", "A001_proxy.mov"))

    def test_existing_file_is_adopted_once_then_numbered(self):
        src = self.touch("footage/A001.mp4")
        self.touch("footage/Proxy/A001_proxy.mov", b"old")
        claimed = {}
        first = proxy._claim(src, claimed)
        second = proxy._claim(src, claimed)
        self.assertEqual(first[0], "existing")
        self.assertEqual(second[0], "render")
        self.assertEqual(second[1], os.path.join(self.dir, "footage", "Proxy", "A001_proxy_2.mov"))

    def test_same_stem_different_folders_do_not_collide(self):
        a = self.touch("camA/A001.mp4")
        b = self.touch("camB/A001.mp4")
        claimed = {}
        _, dst_a = proxy._claim(a, claimed)
        _, dst_b = proxy._claim(b, claimed)
        self.assertNotEqual(dst_a, dst_b)

    @unittest.skipUnless(sys.platform == "win32", "Windows drive letters and shares")
    def test_a_chosen_folder_mirrors_the_source_path_on_windows(self):
        self.assertEqual(proxy.proxy_dir(r"E:\Shoot\Card A\C0001.MP4", r"D:\Proxies"), r"D:\Proxies\E\Shoot\Card A")
        self.assertEqual(proxy.proxy_dir(r"\\nas.example.com\media\Day 1\C0001.MP4", r"D:\Proxies"),
                         r"D:\Proxies\nas.example.com\media\Day 1")
        self.assertEqual(proxy.proxy_dir(r"E:\C0001.MP4", r"D:\Proxies"), r"D:\Proxies\E")
        self.assertEqual(proxy.proxy_dir(r"E:\Shoot\C0001.MP4"), r"E:\Shoot\Proxy")

    @unittest.skipIf(sys.platform == "win32", "POSIX paths")
    def test_a_chosen_folder_mirrors_the_source_path_on_a_mac(self):
        self.assertEqual(proxy.proxy_dir("/Volumes/Card A/C0001.MP4", "/Users/you/Proxies"),
                         "/Users/you/Proxies/Volumes/Card A")
        self.assertEqual(proxy.proxy_dir("/Volumes/Card A/C0001.MP4"), "/Volumes/Card A/Proxy")

    def test_in_a_chosen_folder_two_cards_clip_names_never_meet(self):
        root = os.path.join(self.dir, "Proxies")
        a = self.touch("Shoot/Card A/CLIP/C0001.MP4")
        b = self.touch("Shoot/Card B/CLIP/C0001.MP4")
        claimed = {}
        kind_a, dst_a = proxy._claim(a, claimed, root)
        kind_b, dst_b = proxy._claim(b, claimed, root)
        self.assertEqual((kind_a, kind_b), ("render", "render"))
        self.assertEqual(os.path.basename(dst_a), os.path.basename(dst_b))      # both plain names
        self.assertNotEqual(dst_a, dst_b)
        for card, dst in (("Card A", dst_a), ("Card B", dst_b)):
            self.assertTrue(dst.startswith(root + os.sep), dst)
            self.assertTrue(os.path.dirname(dst).endswith(os.path.join("Shoot", card, "CLIP")), dst)

    def test_a_chosen_folders_earlier_proxy_is_reused_and_found(self):
        root = os.path.join(self.dir, "Proxies")
        src = self.touch("Shoot/Card A/C0001.MP4")
        old = os.path.join(proxy.proxy_dir(src, root), "C0001_proxy.mov")
        os.makedirs(os.path.dirname(old))
        open(old, "wb").close()
        self.assertEqual(proxy._claim(src, {}, root), ("existing", old))
        self.assertEqual(proxy._claim(src, {})[0], "render")    # beside it, there's none
        entry = {"path": src}
        self.assertEqual(proxy.known_proxy(entry, root=root), old)
        self.assertEqual(proxy.known_proxy(entry), "")
        self.assertEqual(proxy.proxy_state({**entry, "proxy": "None"}, root=root), "original")
        # A proxy made beside the clip before is still found with a folder chosen.
        beside = self.touch("Shoot/Card B/Proxy/C0001_proxy.mov")
        self.assertEqual(proxy.known_proxy({"path": os.path.join(self.dir, "Shoot", "Card B", "C0001.MP4")},
                                           root=root), beside)

    def test_relink_picks_the_same_named_proxy_from_the_right_card(self):
        a = self.touch("new drive/Proxies/E/Shoot/Card A/C0001_proxy.mov")
        b = self.touch("new drive/Proxies/E/Shoot/Card B/C0001_proxy.mov")
        # Where they were: the old drive's Proxies folder, in this platform's own paths.
        old = os.path.join(os.sep, "old drive", "Proxies", "E", "Shoot")
        offline = [{"id": card[-1].lower(), "path": os.path.join(os.sep, "E", "Shoot", card, "C0001.MP4"),
                    "proxy_path": os.path.join(old, card, "C0001_proxy.mov")} for card in ("Card B", "Card A")]
        found = {e["id"]: path for e, path in proxy.find_relinks(offline, os.path.join(self.dir, "new drive"))}
        self.assertEqual(found, {"a": a, "b": b})

    def test_plan_splits_renders_existing_and_skips(self):
        self.touch("footage/A001.mp4")
        self.touch("footage/A002.mp4")
        self.touch("footage/Proxy/A002_proxy.mov", b"old")
        videos = [
            {"id": "1", "name": "A001", "path": os.path.join(self.dir, "footage", "A001.mp4"), "type": "video"},
            {"id": "2", "name": "A002", "path": os.path.join(self.dir, "footage", "A002.mp4"), "type": "video"},
            {"id": "3", "name": "gone", "path": os.path.join(self.dir, "footage", "missing.mp4"), "type": "video"},
            {"id": "4", "name": "nopath", "path": "", "type": "video"},
        ]
        result = proxy.plan(videos)
        self.assertEqual([t["id"] for t in result["render"]], ["1"])
        self.assertEqual([t["id"] for t in result["existing"]], ["2"])
        self.assertEqual(len(result["skipped"]), 2)
        self.assertTrue(any("offline" in s for s in result["skipped"]))
        self.assertTrue(any("no file behind" in s for s in result["skipped"]))


class CommandTests(unittest.TestCase):
    def test_command_shape(self):
        cmd = proxy.build_command("ffmpeg", "in.mov", "out.mov", "half", "h264")
        self.assertEqual(cmd[:7], ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", "in.mov"][:7])
        self.assertIn("-vf", cmd)
        self.assertIn("0:v:0", cmd)
        self.assertIn("0:a?", cmd)
        self.assertIn("copy", cmd)
        self.assertEqual(cmd[-1], "out.mov")
        # No -r anywhere: a proxy must keep its source's frame rate.
        self.assertNotIn("-r", cmd)

    def test_original_resolution_has_no_scale_filter(self):
        cmd = proxy.build_command("ffmpeg", "in.mov", "out.mov", "original", "prores")
        self.assertNotIn("-vf", cmd)
        self.assertIn("prores_ks", cmd)

    def test_unknown_choice_is_refused(self):
        with self.assertRaises(proxy.ProxyError):
            proxy.build_command("ffmpeg", "in", "out", "enormous", "h264")
        with self.assertRaises(proxy.ProxyError):
            proxy.build_command("ffmpeg", "in", "out", "half", "av1")


class RenderTests(_Tmp):
    def test_success_writes_part_then_renames(self):
        src = self.touch("footage/A001.mp4")
        dst = os.path.join(self.dir, "footage", "Proxy", "A001_proxy.mov")

        def fake_run(cmd):
            part = cmd[-1]
            self.assertTrue(part.endswith(proxy.PART_EXT))
            with open(part, "wb") as fh:
                fh.write(b"proxy!")
            return 0, b""
        out = proxy.render_one("ffmpeg", src, dst, "half", "h264", run=fake_run)
        self.assertEqual(out, dst)
        self.assertTrue(os.path.isfile(dst))
        self.assertFalse(os.path.exists(dst + proxy.PART_EXT))

    def test_failure_removes_the_part_file(self):
        src = self.touch("footage/A001.mp4")
        dst = os.path.join(self.dir, "footage", "Proxy", "A001_proxy.mov")

        def fake_run(cmd):
            with open(cmd[-1], "wb") as fh:
                fh.write(b"half a proxy")
            return 1, b"stderr: could not write header"
        with self.assertRaises(proxy.ProxyError) as raised:
            proxy.render_one("ffmpeg", src, dst, "half", "h264", run=fake_run)
        self.assertIn("A001.mp4", str(raised.exception))
        self.assertIn("could not write header", str(raised.exception))
        self.assertFalse(os.path.exists(dst + proxy.PART_EXT))
        self.assertFalse(os.path.exists(dst))

    def test_missing_proxy_folder_is_created(self):
        src = self.touch("footage/A001.mp4")
        dst = os.path.join(self.dir, "footage", "Proxy", "A001_proxy.mov")

        def fake_run(cmd):
            with open(cmd[-1], "wb") as fh:
                fh.write(b"p")
            return 0, b""
        proxy.render_one("ffmpeg", src, dst, "half", "h264", run=fake_run)
        self.assertTrue(os.path.isfile(dst))


    def test_a_render_killed_by_cancel_stops_and_isnt_tried_again_on_the_cpu(self):
        src = self.touch("footage/A001.mp4")
        dst = os.path.join(self.dir, "footage", "Proxy", "A001_proxy.mov")
        calls = []

        def killed(cmd):
            calls.append(cmd)
            with open(cmd[-1], "wb") as fh:
                fh.write(b"half a proxy")
            return None, b""
        with self.assertRaises(proxy.ProxyCancelled):
            proxy.render_one("ffmpeg", src, dst, "half", "h264_nvenc", run=killed)
        self.assertEqual(len(calls), 1)
        self.assertFalse(os.path.exists(dst + proxy.PART_EXT))
        self.assertTrue(issubclass(proxy.ProxyCancelled, proxy.ProxyError))


class _Report:
    """A job's report(): records what it's told, and raises once stopped
    (as the worker's does after Cancel)."""

    def __init__(self):
        self.calls, self.stop = [], False

    def stopped(self):
        return self.stop

    def __call__(self, stage, done=0, total=1):
        if self.stop:
            raise RuntimeError("cancelled")
        self.calls.append((stage, done, total))


class RenderManyTests(_Tmp):
    def targets(self, *names):
        return [{"id": n, "path": self.touch(f"footage/{n}.mp4"), "tc": "01:00:00:00",
                 "dst": os.path.join(self.dir, "footage", "Proxy", f"{n}_proxy.mov")} for n in names]

    def test_two_render_at_once_and_results_keep_the_run_order(self):
        both_in = threading.Barrier(2, timeout=5)

        def run(cmd):
            name = os.path.basename(cmd[cmd.index("-i") + 1])
            if name in ("A.mp4", "B.mp4"):
                both_in.wait()          # times out unless A and B are rendering together
            if name == "B.mp4":
                return 1, b"B is broken"
            open(cmd[-1], "wb").close()
            return 0, b""
        report = _Report()
        results = proxy.render_many("ffmpeg", self.targets("A", "B", "C"), "half", "h264", report, run=run)
        self.assertEqual([(r["id"], r["ok"]) for r in results], [("A", True), ("B", False), ("C", True)])
        self.assertIn("B is broken", results[1]["error"])
        self.assertEqual(report.calls[-1][1:], (3, 3))
        self.assertTrue(all(stage.startswith("Rendering ") for stage, _d, _t in report.calls), report.calls)

    def test_one_at_a_time_when_asked(self):
        inside, most = [0], [0]
        lock = threading.Lock()

        def run(cmd):
            with lock:
                inside[0] += 1
                most[0] = max(most[0], inside[0])
            time.sleep(0.05)
            with lock:
                inside[0] -= 1
            open(cmd[-1], "wb").close()
            return 0, b""
        proxy.render_many("ffmpeg", self.targets("A", "B", "C"), "half", "h264", _Report(), run=run, at_once=1)
        self.assertEqual(most[0], 1)

    def test_cancel_starts_nothing_more_and_unwinds_the_job(self):
        report, started = _Report(), []

        def run(cmd):
            started.append(cmd)
            report.stop = True          # Cancel lands during the first renders
            return None, b""            # ... which it kills
        with mock.patch.object(proxy, "terminate_active") as kill:
            with self.assertRaises(RuntimeError):
                proxy.render_many("ffmpeg", self.targets("A", "B", "C", "D"), "half", "h264", report, run=run)
        self.assertLessEqual(len(started), 2)
        kill.assert_called()
        self.assertFalse(any(os.path.exists(t["dst"]) for t in self.targets("A", "B", "C", "D")))

    def test_nothing_to_render(self):
        report = _Report()
        self.assertEqual(proxy.render_many("ffmpeg", [], "half", "h264", report), [])
        self.assertEqual(report.calls, [])


class TerminateTests(unittest.TestCase):
    def test_a_process_killed_by_terminate_active_reports_no_exit_code(self):
        class P:
            returncode = -9

            def communicate(self):
                proxy.terminate_active()        # Cancel, while this render is being waited on
                return b"", b"killed"

            def kill(self):
                pass
        with mock.patch.object(proxy.subprocess, "Popen", return_value=P()):
            self.assertEqual(proxy._run_process(["ffmpeg"]), (None, b"killed"))
        self.assertFalse(proxy._ACTIVE)
        self.assertFalse(proxy._KILLED)

    def test_terminate_kills_registered_processes(self):
        class P:
            def __init__(self):
                self.killed = False
            def kill(self):
                self.killed = True
        p = P()
        with mock.patch.object(proxy, "_ACTIVE", {p}):
            self.assertEqual(proxy.terminate_active(), 1)
        self.assertTrue(p.killed)


class ScopeTests(unittest.TestCase):
    def test_scope_labels(self):
        self.assertEqual(proxy.scope_label("selection"), "the Media Pool selection")
        self.assertEqual(proxy.scope_label("bin", "Footage"), "bin 'Footage'")
        self.assertEqual(proxy.scope_label("bin", "Footage", recursive=True), "bin 'Footage' and its sub-bins")
        self.assertEqual(proxy.scope_label("bin", "Master"), "Master")
        self.assertEqual(proxy.scope_label("timeline", None, "Cut 1"), "every clip on 'Cut 1'")
        self.assertEqual(proxy.scope_label("timeline", None, "Cut 1", timeline_selection=True),
                         "the selection on 'Cut 1'")
        self.assertEqual(proxy.scope_label("timeline"), "the current timeline")
        self.assertEqual(proxy.scope_label("all"), "every clip in the project")

    class Item:
        def __init__(self, media):
            self._media = media

        def GetMediaPoolItem(self):
            return self._media

    class Media:
        def __init__(self, uid, path):
            self.uid, self.props = uid, {"File Path": path, "Type": "video"}

        def GetClipProperty(self, *a):
            return self.props if a == () else self.props.get(a[0])

        def GetName(self):
            return self.uid

        def GetUniqueId(self):
            return self.uid

    def _timeline(self, selected, tracks, can_select=True):
        class Timeline:
            def GetTrackCount(self, kind):
                return len(tracks) if kind == "video" else 0

            def GetItemListInTrack(self, kind, index):
                return tracks[index - 1]
        if can_select:
            Timeline.GetSelectedClips = lambda self: selected
        return Timeline()

    def test_current_timeline_takes_its_selection_first(self):
        a, b = self.Media("a", "C:/m/a.mp4"), self.Media("b", "C:/m/b.mp4")
        timeline = self._timeline([self.Item(a), self.Item(None)], [[self.Item(a), self.Item(b)]])
        entries, objects, from_selection = proxy.timeline_clips(timeline)
        self.assertTrue(from_selection)
        self.assertEqual([e["path"] for e in entries], ["C:/m/a.mp4"])
        self.assertIs(objects["a"], a)

    def test_current_timeline_is_every_clip_when_nothing_is_selected(self):
        a, b = self.Media("a", "C:/m/a.mp4"), self.Media("b", "C:/m/b.mp4")
        tracks = [[self.Item(a), self.Item(None)], [self.Item(b), self.Item(a)]]
        for can_select in (True, False):   # False: a Resolve older than 21.0.4
            entries, _objects, from_selection = proxy.timeline_clips(self._timeline([], tracks, can_select))
            self.assertFalse(from_selection)
            self.assertEqual([e["path"] for e in entries], ["C:/m/a.mp4", "C:/m/b.mp4"])

    def test_pool_selection_filters_timelines_and_none(self):
        class Clip:
            def GetClipProperty(self, *a):
                return {"Type": "video", "File Path": "p"} if a == () else {"Type": "video", "File Path": "p"}.get(a[0])
            def GetName(self):
                return "c"
            def GetUniqueId(self):
                return "id"
        class TimelinePseudoClip(Clip):
            def GetClipProperty(self, *a):
                return {"Type": "Timeline"} if a == () else "Timeline"
        class Pool:
            def GetSelectedClips(self):
                return [Clip(), None, TimelinePseudoClip()]
        class Project:
            def GetMediaPool(self):
                return Pool()
        entries, _objects = proxy.selected_pool(Project())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "id")

    def test_pool_selection_reports_old_resolve(self):
        class Pool:
            def GetSelectedClips(self):
                raise RuntimeError("no such method")
        class Project:
            def GetMediaPool(self):
                return Pool()
        with self.assertRaises(proxy.ProxyError) as raised:
            proxy.selected_pool(Project())
        self.assertIn("could not read the Media Pool selection", str(raised.exception))


class ReportTests(unittest.TestCase):
    def test_render_report(self):
        lines = proxy.render_report([{"id": "a", "ok": True}, {"id": "b", "ok": False, "error": "boom"}])
        self.assertEqual(lines, [("Rendered 1 of 2 proxies.", "success"), ("boom", "error")])
        self.assertEqual(proxy.render_report([{"id": "a", "ok": True}]), [("Rendered 1 proxy.", "success")])

    def test_link_report(self):
        lines = proxy.link_report(2, ["Bad Clip"], 3)
        self.assertEqual(lines[0], ("Linked 2 clips to their proxies.", "success"))
        self.assertEqual(lines[1], ('Resolve wouldn\'t link "Bad Clip".', "error"))
        self.assertEqual(lines[2], ("3 clips already had proxy files – linked instead of rendering.", "info"))
        self.assertEqual(proxy.link_report(1, [], 0), [("Linked 1 clip to its proxy.", "success")])
        self.assertEqual(proxy.link_report(0, [], 1),
                         [("1 clip already had a proxy file – linked to it instead of rendering.", "info")])


class TimecodeTests(unittest.TestCase):
    """Resolve only links a proxy whose timecode matches the clip's."""

    def test_clean_and_compare(self):
        self.assertEqual(proxy.clean_timecode(" 19:45:12:08 "), "19:45:12:08")
        self.assertEqual(proxy.clean_timecode("01:00:00;02"), "01:00:00;02")   # drop frame
        for bad in ("", None, "19:45:12", "-y -i evil", "19:45:12:08 -f"):
            self.assertEqual(proxy.clean_timecode(bad), "", bad)
        self.assertTrue(proxy.same_timecode("01:00:00;02", "01:00:00:02"))
        self.assertTrue(proxy.same_timecode("", "00:00:00:00"))   # no timecode reads as zero
        self.assertFalse(proxy.same_timecode("", "19:45:12:08"))

    def test_the_entry_carries_resolves_start_timecode(self):
        class Clip:
            def GetClipProperty(self, *a):
                return {"File Path": "C:/m/A.mp4", "Clip Name": "A.mp4", "Type": "Video", "Start TC": "19:45:12:08"}

            def GetUniqueId(self):
                return "u"
        self.assertEqual(proxy._entry(Clip())["tc"], "19:45:12:08")

    def test_the_command_stamps_it(self):
        cmd = proxy.build_command("ffmpeg", "in.mp4", "out.mov", "half", "h264", "19:45:12:08")
        self.assertEqual(cmd[cmd.index("-timecode") + 1], "19:45:12:08")
        self.assertEqual(cmd[-1], "out.mov")
        self.assertNotIn("-timecode", proxy.build_command("ffmpeg", "in.mp4", "out.mov", "half", "h264", "bogus"))

    def test_reading_a_files_timecode(self):
        with mock.patch.object(proxy, "_ffprobe_path", return_value="ffprobe"), \
             mock.patch.object(proxy.subprocess, "run", return_value=mock.Mock(
                 returncode=0, stdout=b'{"streams": [{"tags": {}}, {"tags": {"timecode": "19:45:12:08"}}]}')):
            self.assertEqual(proxy.file_timecode("ffmpeg", "x.mov"), "19:45:12:08")
        with mock.patch.object(proxy, "_ffprobe_path", return_value=None), \
             mock.patch.object(proxy.subprocess, "run", return_value=mock.Mock(
                 returncode=1, stderr=b"  Metadata:\n    timecode        : 01:00:00;02\n")):
            self.assertEqual(proxy.file_timecode("ffmpeg", "x.mov"), "01:00:00;02")
        with mock.patch.object(proxy, "_ffprobe_path", return_value=None), \
             mock.patch.object(proxy.subprocess, "run", side_effect=OSError("gone")):
            self.assertEqual(proxy.file_timecode("ffmpeg", "x.mov"), "")

    def test_link_report_says_what_was_redone_and_refused(self):
        lines = proxy.link_report(2, [], 0, redone=2, refused_existing=["B.mp4"])
        texts = [t for t, _tone in lines]
        self.assertIn("2 proxy files already there had the wrong timecode – rendered again.", texts)
        self.assertTrue(any("B.mp4" in t and "delete it" in t for t in texts), texts)


class FormatTests(unittest.TestCase):
    def test_every_format_builds_a_command(self):
        self.assertIn("h264", proxy.CODECS)
        self.assertIn("prores", proxy.CODECS)   # saved choices from before there were more still work
        for key, codec in proxy.CODECS.items():
            cmd = proxy.build_command("ffmpeg", "in.mp4", "out.mov", "half", key)
            self.assertIn("-c:v", cmd, key)
            self.assertTrue(codec["label"] and codec["hint"], key)
        for key in proxy.GPU_ENCODERS:
            cmd = proxy.build_command("ffmpeg", "in.mp4", "out.mov", "half", key)
            self.assertEqual(cmd[cmd.index("-c:v") + 1], key)
        for key, codec in proxy.CODECS.items():
            self.assertTrue(set(codec.get("gpu", ())) <= set(proxy.GPU_ENCODERS), key)
        self.assertEqual([c["id"] for c in proxy.codec_choices("win32")], list(proxy.CODECS))
        # A Mac's encoders are VideoToolbox's.
        self.assertNotIn("h264_nvenc", [c["id"] for c in proxy.codec_choices("darwin")])

    def test_dnxhr_and_prores_profiles(self):
        cmd = proxy.build_command("ffmpeg", "in.mp4", "out.mov", "half", "dnxhr_sq")
        self.assertEqual(cmd[cmd.index("-profile:v") + 1], "dnxhr_sq")
        cmd = proxy.build_command("ffmpeg", "in.mp4", "out.mov", "half", "prores_lt")
        self.assertEqual(cmd[cmd.index("-profile:v") + 1], "1")


class GpuTests(unittest.TestCase):
    def test_the_gpu_command_decodes_and_scales_on_the_card(self):
        cmd = proxy.build_command("ffmpeg", "in.mp4", "out.mov", "half", "h264_nvenc", "01:00:00:00", gpu_decode=True)
        self.assertEqual(cmd[cmd.index("-hwaccel") + 1], "cuda")
        self.assertLess(cmd.index("-hwaccel"), cmd.index("-i"))
        self.assertIn("scale_cuda=trunc(iw/4)*2:trunc(ih/4)*2:format=yuv420p", cmd)
        self.assertNotIn("-pix_fmt", cmd)
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(cmd[cmd.index("-timecode") + 1], "01:00:00:00")
        original = proxy.build_command("ffmpeg", "in.mp4", "out.mov", "original", "hevc_nvenc", gpu_decode=True)
        self.assertIn("scale_cuda=format=yuv420p", original)

    def test_without_gpu_decode_the_cpu_hands_nvenc_420(self):
        cmd = proxy.build_command("ffmpeg", "in.mov", "out.mov", "quarter", "h264_nvenc")
        self.assertNotIn("-hwaccel", cmd)
        self.assertIn("scale=trunc(iw/8)*2:trunc(ih/8)*2", cmd)
        self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "yuv420p")

    def test_a_source_the_card_cant_decode_goes_again_on_the_cpu(self):
        with tempfile.TemporaryDirectory() as folder:
            dst = os.path.join(folder, "Proxy", "A_proxy.mov")
            calls = []

            def run(cmd):
                calls.append(cmd)
                if "-hwaccel" in cmd:
                    return 1, b"Impossible to convert between the formats"
                open(cmd[-1], "wb").close()
                return 0, b""
            proxy.render_one("ffmpeg", "A.mov", dst, "half", "h264_nvenc", run=run)
            self.assertEqual(["-hwaccel" in c for c in calls], [True, False])
            self.assertTrue(os.path.isfile(dst))
            # A CPU format is only ever tried once.
            calls.clear()
            proxy.render_one("ffmpeg", "A.mov", dst, "half", "h264", run=run)
            self.assertEqual(len(calls), 1)

    # What each vendor's encoder says on a PC without the hardware (ffmpeg 9).
    NO_DEVICE = {"nvenc": b"[hevc_nvenc @ 01] Cannot load nvcuda.dll",
                 "amf": b"[hevc_amf @ 01] CreateComponent(AMFVideoEncoderHW_HEVC) failed with error 30",
                 "qsv": b"[hevc_qsv @ 01] Error creating a MFX session: -9."}

    def _encoders(self, works=(), errors=None):
        """A subprocess.run for the test encodes: the encoders in `works`
        start, the rest fail with errors[suffix] or their no-device line."""
        errors = {**self.NO_DEVICE, **(errors or {})}

        def run(cmd, **_kw):
            if cmd[0] == "nvidia-smi":
                return mock.Mock(returncode=0, stdout=b"580.97\n")
            encoder = cmd[cmd.index("-c:v") + 1]
            if encoder in works:
                return mock.Mock(returncode=0)
            return mock.Mock(returncode=1, stderr=errors[encoder.split("_")[1]])
        return mock.Mock(side_effect=run)

    def test_the_amd_and_intel_encoders_decode_on_the_cpu(self):
        for encoder in ("h264_amf", "hevc_qsv"):
            cmd = proxy.build_command("ffmpeg", "in.mov", "out.mov", "half", encoder, gpu_decode=True)
            self.assertNotIn("-hwaccel", cmd)
            self.assertIn("scale=trunc(iw/4)*2:trunc(ih/4)*2", cmd)
            self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "yuv420p")
            self.assertEqual(cmd[cmd.index("-c:v") + 1], encoder)
        self.assertIn("cqp", proxy.build_command("ffmpeg", "in.mov", "out.mov", "half", "hevc_amf"))

    def test_an_amd_or_intel_render_goes_once_on_the_cpu(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []

            def run(cmd):
                calls.append(cmd)
                open(cmd[-1], "wb").close()
                return 0, b""
            proxy.render_one("ffmpeg", "A.mov", os.path.join(folder, "Proxy", "A_proxy.mov"), "half", "hevc_amf",
                             run=run)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("-hwaccel", calls[0])

    def test_the_gpu_format_takes_the_first_card_that_encodes(self):
        """An AMD or Intel card renders the GPU format when there's no
        NVIDIA one - without a word, since a missing card isn't news."""
        proxy._GPU_CHECKED.clear()
        run = self._encoders(works={"hevc_amf"})
        with mock.patch.object(proxy.subprocess, "run", run):
            self.assertEqual(proxy.render_plan("ffmpeg", "hevc_nvenc"), ("hevc_amf", ""))
        tried = [c.args[0][c.args[0].index("-c:v") + 1] for c in run.call_args_list]
        self.assertEqual(tried, ["hevc_nvenc", "hevc_amf"])
        proxy._GPU_CHECKED.clear()
        with mock.patch.object(proxy.subprocess, "run", self._encoders(works={"h264_qsv"})):
            self.assertEqual(proxy.render_plan("ffmpeg", "h264_nvenc"), ("h264_qsv", ""))
        proxy._GPU_CHECKED.clear()

    def test_an_nvidia_card_that_wont_start_is_named_when_another_card_renders(self):
        """A GTX 1080 stays on driver 580 - too old for ffmpeg 9 - beside a
        Ryzen's Radeon: the Radeon renders, and the note says why not NVIDIA."""
        proxy._GPU_CHECKED.clear()
        old = b"[hevc_nvenc @ 01] The minimum required Nvidia driver for nvenc is 610.00 or newer\n"
        with mock.patch.object(proxy.subprocess, "run", self._encoders(works={"hevc_amf"}, errors={"nvenc": old})):
            used, note = proxy.render_plan("ffmpeg", "hevc_nvenc")
        self.assertEqual(used, "hevc_amf")
        self.assertIn("Couldn't encode on the NVIDIA graphics card: the NVIDIA driver is older", note)
        self.assertIn("this PC has 580.97", note)
        self.assertIn("This run used the AMD graphics instead.", note)
        proxy._GPU_CHECKED.clear()

    def test_a_build_without_one_vendor_is_only_named_when_nothing_else_is_wrong(self):
        proxy._GPU_CHECKED.clear()
        no_amf = {"amf": b"Unknown encoder 'hevc_amf'"}
        with mock.patch.object(proxy.subprocess, "run", self._encoders(works={"hevc_qsv"}, errors=no_amf)):
            self.assertEqual(proxy.render_plan("ffmpeg", "hevc_nvenc"), ("hevc_qsv", ""))
        proxy._GPU_CHECKED.clear()
        with mock.patch.object(proxy.subprocess, "run", self._encoders(errors=no_amf)):
            self.assertIn("built without AMD's encoders", proxy.render_plan("ffmpeg", "hevc_nvenc")[1])
        proxy._GPU_CHECKED.clear()
        odd = {**no_amf, "qsv": b"[hevc_qsv @ 01] Something odd.\n"}
        with mock.patch.object(proxy.subprocess, "run", self._encoders(errors=odd)):
            self.assertIn("Intel's encoder didn't start (Something odd)", proxy.render_plan("ffmpeg", "hevc_nvenc")[1])
        proxy._GPU_CHECKED.clear()

    def _nvidia_smi(self, driver, compute):
        def run(cmd, **_kw):
            if cmd[0] == "nvidia-smi":
                answer = compute if "--query-gpu=compute_cap" in cmd else driver
                return mock.Mock(returncode=0, stdout=answer.encode() + b"\n")
            encoder = cmd[cmd.index("-c:v") + 1]
            if encoder.endswith("_nvenc"):
                return mock.Mock(returncode=1, stderr=(
                    b"[hevc_nvenc @ 01] Driver does not support the required nvenc API version. Required: 13.1 Found: 13.0\n"
                    b"[hevc_nvenc @ 01] The minimum required Nvidia driver for nvenc is 610.00 or newer\n"))
            return mock.Mock(returncode=1, stderr=self.NO_DEVICE[encoder.split("_")[1]])
        return run

    def test_a_card_whose_drivers_stop_at_580_isnt_told_to_update(self):
        """A GTX 1080 (compute 6.1) on its last driver, 580, with ffmpeg 9:
        no driver will do, so the note says what will."""
        proxy._GPU_CHECKED.clear()
        with mock.patch.object(proxy.subprocess, "run", side_effect=self._nvidia_smi("580.97", "6.1")):
            used, note = proxy.render_plan("ffmpeg", "hevc_nvenc")
        self.assertEqual(used, "h265")
        self.assertNotIn("Update it", note)
        self.assertIn("gets no drivers newer than the 580 series", note)
        self.assertIn("An older ffmpeg build that works with driver 580 will use the card – set its path in Settings.",
                      note)
        self.assertIn("This run used H.265 (HEVC) on the CPU instead.", note)
        proxy._GPU_CHECKED.clear()

    def test_a_card_that_still_gets_drivers_is_told_to_update(self):
        proxy._GPU_CHECKED.clear()
        with mock.patch.object(proxy.subprocess, "run", side_effect=self._nvidia_smi("596.36", "8.9")):  # an RTX 40
            note = proxy.render_plan("ffmpeg", "hevc_nvenc")[1]
        self.assertIn("Update it in the NVIDIA app", note)
        proxy._GPU_CHECKED.clear()

    def test_the_last_driver_series_by_card(self):
        self.assertEqual([proxy.last_driver_series(c) for c in ("3.5", "5.2", "6.1", "7.0", "7.5", "12.0", "", None)],
                         [470, 580, 580, 580, None, None, None, None])
        too_old = b"The minimum required Nvidia driver for nvenc is 610.00 or newer"
        self.assertIn("580 series", proxy.gpu_problem(too_old, "580.97", last=580))
        # A 580 card on an older driver than its last, with an ffmpeg that wants 570: updating does work.
        wants_570 = b"The minimum required Nvidia driver for nvenc is 570.00 or newer"
        self.assertIn("Update it", proxy.gpu_problem(wants_570, "553.50", last=580))
        # No version in the error: the card's own driver says whether updating could help.
        api_only = b"Driver does not support the required nvenc API version. Required: 13.1 Found: 13.0"
        self.assertIn("580 series", proxy.gpu_problem(api_only, "580.97", last=580))
        self.assertIn("Update it", proxy.gpu_problem(api_only, "553.50", last=580))

    def test_each_vendor_says_what_it_lacks(self):
        self.assertEqual(proxy.gpu_problem(self.NO_DEVICE["amf"], vendor="AMD"), "it needs AMD Radeon graphics.")
        self.assertEqual(proxy.gpu_problem(self.NO_DEVICE["qsv"], vendor="Intel"),
                         "it needs Intel graphics with Quick Sync.")
        self.assertIn("built without Intel's encoders", proxy.gpu_problem(b"Unknown encoder 'hevc_qsv'", vendor="Intel"))
        self.assertIn("AMD's encoder didn't start (encoder->Init() failed with error 4)",
                      proxy.gpu_problem(b"[hevc_amf @ 01] encoder->Init() failed with error 4\n", vendor="AMD"))
        # Only NVIDIA's encoder asks for a driver version.
        self.assertNotIn("NVIDIA driver", proxy.gpu_problem(b"minimum required Nvidia driver for nvenc is 610",
                                                            vendor="AMD"))

    def test_the_test_encode_uses_the_proxy_settings(self):
        """An encoder that starts but refuses a proxy's settings (QVBR on a
        Radeon that hasn't got it) mustn't count as one that works."""
        proxy._GPU_CHECKED.clear()
        run = mock.Mock(return_value=mock.Mock(returncode=0))
        self.assertTrue(proxy.gpu_available("ffmpeg", "hevc_amf", run=run))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("-rc") + 1], "cqp")
        self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "yuv420p")
        proxy._GPU_CHECKED.clear()

    def test_no_gpu_renders_the_cpu_format_and_says_so(self):
        proxy._GPU_CHECKED.clear()
        run = self._encoders()
        with mock.patch.object(proxy.subprocess, "run", run):
            used, note = proxy.render_plan("ffmpeg", "hevc_nvenc")
            self.assertEqual(used, "h265")
            self.assertIn("needs an NVIDIA, AMD or Intel graphics card", note)
            self.assertIn("This run used H.265 (HEVC) on the CPU instead.", note)
            proxy.render_plan("ffmpeg", "hevc_nvenc")
        self.assertEqual(run.call_count, 6)   # a no is asked again: a driver may have been updated since
        self.assertEqual(proxy.render_plan("ffmpeg", "prores"), ("prores", ""))
        proxy._GPU_CHECKED.clear()
        ok = mock.Mock(return_value=mock.Mock(returncode=0))
        with mock.patch.object(proxy.subprocess, "run", ok):
            self.assertEqual(proxy.render_plan("ffmpeg", "h264_nvenc"), ("h264_nvenc", ""))
            proxy.render_plan("ffmpeg", "h264_nvenc")
        self.assertEqual(ok.call_count, 1)    # a yes is kept
        proxy._GPU_CHECKED.clear()

    def test_a_driver_too_old_for_ffmpeg_is_named_not_a_missing_card(self):
        """ffmpeg 9 on driver 596 (an RTX 5070 Ti, 2026-10-08): the card is
        there, the driver is behind - the note says which and what to do."""
        proxy._GPU_CHECKED.clear()
        err = (b"[hevc_nvenc @ 0001] Driver does not support the required nvenc API version. Required: 13.1 Found: 13.0\n"
               b"[hevc_nvenc @ 0001] The minimum required Nvidia driver for nvenc is 610.00 or newer\n")

        def run(cmd, **_kw):
            if cmd[0] == "nvidia-smi":
                return mock.Mock(returncode=0, stdout=b"596.36\n")
            return mock.Mock(returncode=1, stderr=err)
        with mock.patch.object(proxy.subprocess, "run", side_effect=run):
            used, note = proxy.render_plan("ffmpeg", "hevc_nvenc")
        self.assertEqual(used, "h265")
        self.assertIn("driver 610.00 or newer, and this PC has 596.36", note)
        self.assertNotIn("needs an NVIDIA graphics card", note)
        self.assertIn("This run used H.265 (HEVC) on the CPU instead.", note)
        self.assertIn("built without NVIDIA's encoders", proxy.gpu_problem(b"Unknown encoder 'hevc_nvenc'"))
        self.assertIn("(Some odd failure)", proxy.gpu_problem(b"[hevc_nvenc @ 01] Some odd failure.\n"))
        proxy._GPU_CHECKED.clear()


class StatusTests(_Tmp):
    def entry(self, name, proxy_value="None", proxy_path="", path=""):
        return {"id": name, "name": name, "path": path or f"C:/m/{name}", "type": "Video", "tc": "",
                "proxy": proxy_value, "proxy_path": proxy_path}

    def test_linked_offline_and_none(self):
        there = self.touch("Proxy/A_proxy.mov")
        linked = self.entry("A.mp4", "1920x1080", there)
        offline = self.entry("B.mp4", "1920x1080", os.path.join(self.dir, "Proxy", "gone.mov"))
        none = self.entry("C.mp4")
        # Resolve still says "1920x1080" for a proxy whose file has gone.
        self.assertEqual([proxy.proxy_state(e) for e in (linked, offline, none)], ["linked", "offline", "none"])
        report = proxy.status_report([linked, none, offline])
        self.assertEqual(report["counts"], {"linked": 1, "offline": 1, "original": 0, "none": 1})
        self.assertEqual([r["state"] for r in report["rows"]], ["offline", "none", "linked"])   # to fix first
        self.assertIn(there, report["rows"][2]["detail"])
        self.assertEqual(proxy.status_line(report["counts"]), "Proxies: 1 linked, 1 offline, 1 without one.")

    def test_a_clip_with_a_proxy_on_disk_but_none_linked_plays_the_original(self):
        """Use camera originals took it off (set aside), or Buddy's or
        Resolve's own proxy sits beside the source unlinked."""
        src = self.touch("day1/A.mp4")
        aside = self.touch("elsewhere/A_small.mov")
        entry = self.entry("A.mp4", path=src)
        self.assertEqual(proxy.proxy_state(entry), "none")
        self.assertEqual(proxy.known_proxy(entry, {proxy.set_aside_key(src): aside}), aside)
        self.assertEqual(proxy.proxy_state(entry, {proxy.set_aside_key(src): aside}), "original")
        resolves = self.touch("day1/Proxy/A.mov")                 # Generate Proxy Media's name
        self.assertEqual(proxy.known_proxy(entry), resolves)
        buddys = self.touch("day1/Proxy/A_proxy.mov")             # Buddy's own comes first
        self.assertEqual(proxy.known_proxy(entry), buddys)
        report = proxy.status_report([entry])
        self.assertEqual((report["counts"]["original"], report["rows"][0]["detail"]), (1, buddys))
        self.assertIn("1 on the camera original with a proxy ready.", proxy.status_line(report["counts"]))

    def test_clip_types_are_codecs_in_a_few_words(self):
        for codec, path, kind in (("H.264 High 4:2:2 L5.1", "a.MP4", "H.264 4:2:2"),
                                  ("H.264 Main L4.0", "a.MP4", "H.264"),
                                  ("H.265 Main 10 L5.0", "a.MP4", "H.265 10-bit"),
                                  ("H.265 Main 4:2:2 10 L5.0", "a.MP4", "H.265 4:2:2 10-bit"),
                                  ("Apple ProRes RAW HQ", "a.MOV", "ProRes RAW HQ"),
                                  ("XAVC Intra 4:2:2 L5.1", "a.MXF", "XAVC Intra 4:2:2"),
                                  ("Avid DNxHR LB", "a.mov", "DNxHR LB"),
                                  ("", "a.braw", "BRAW"), ("", "", "Other")):
            self.assertEqual(proxy.clip_type({"codec": codec, "path": path}), kind, codec)

    def test_playback_modes_are_resolves_proxy_handling(self):
        self.assertEqual(proxy.PLAYBACK_MODES, {"proxies": "1", "originals": "0", "offline": "2"})
        self.assertEqual(proxy.playback_mode("2"), "offline")
        self.assertEqual(proxy.playback_mode(0), "originals")
        self.assertIsNone(proxy.playback_mode(None))
        self.assertIsNone(proxy.playback_mode("7"))

    def test_a_long_list_is_capped_but_counted(self):
        with mock.patch.object(proxy, "STATUS_ROWS_MAX", 2):
            report = proxy.status_report([self.entry(f"{i}.mp4") for i in range(5)])
        self.assertEqual((len(report["rows"]), report["more"], report["counts"]["none"]), (2, 3, 5))

    def test_offline_proxies_are_found_by_name(self):
        moved = self.touch("new drive/day 1/A_proxy.mov")
        renamed = self.touch("new drive/B_proxy.mov")
        a = self.entry("A.mp4", "960x540", "D:/old/Proxy/A_proxy.mov")
        b = self.entry("B.mp4", "960x540", "D:/old/Proxy/B_custom.mov", path="D:/old/B.mp4")   # found by Buddy's name
        c = self.entry("C.mp4", "960x540", "D:/old/Proxy/C_proxy.mov")
        found = dict((e["id"], path) for e, path in proxy.find_relinks([a, b, c], os.path.join(self.dir, "new drive")))
        self.assertEqual(found, {"A.mp4": moved, "B.mp4": renamed})
        texts = [t for t, _ in proxy.relink_report(2, ["X.mp4"], 1)]
        self.assertEqual(texts, ["Relinked 2 offline proxies.", "Resolve wouldn't link the proxy found for \"X.mp4\".",
                                 "1 offline proxy wasn't in that folder."])


if __name__ == "__main__":
    unittest.main()
