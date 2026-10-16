"""Backing Buddy up and restoring it (core/backup.py, core/backup_runner.py,
Settings > General > Backup): what goes in and what's left out (models,
engines, web sign-ins, Buddy Network's keys, other apps' folders), the zip
and its manifest, a restore that only writes Buddy's own folders and keeps
what was there, automatic backups and pruning. A temporary home throughout:
never the real ~/.buddy."""

import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _paths  # noqa: F401
from core import backup
from core import settings_form as sf

try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from core.backup_runner import BackupRunner
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

TOOLS = ["plain", "buddy_network", "transcribe"]


class Mem(dict):
    saved = 0

    def save(self):
        self.saved += 1


class _Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        p = mock.patch.object(backup, "home", lambda: self.home)
        p.start()
        self.addCleanup(p.stop)
        self.kept = [
            ".buddy/settings.json", ".buddy/transcribe/transcripts/clip.json", ".buddy/transcribe/output/a.srt",
            ".buddy/ask_buddy/conversations.json", ".buddy/ask_buddy/instructions.md", ".buddy/web-history/default.json",
            ".buddy/dailies/transcripts/d.json", ".plain/settings.json", ".buddy_network/settings.json",
            ".buddy_network/saved_chats/abc.json", ".davinci_time_tracker/entries.json",
            ".color_palette_manager/palettes.json", ".buddy_web/settings.json",
            ".figma_fusion_importer_settings.json",
        ]
        self.left = [
            ".buddy/settings.json.bak", ".buddy/crash.log", ".buddy/crash_trail.log.1", ".buddy/model_folders.json",
            ".buddy/transcribe/venv/python.exe", ".buddy/transcribe/models/small/model.bin",
            ".buddy/generative/models/lama.onnx", ".buddy/generative/gpu/x.engine",
            ".buddy/ask_buddy/models/e.gguf", ".buddy/ask_buddy/llama.cpp/b1/llama-server.exe",
            ".buddy/web/Cookies", ".buddy/web/sign-ins.dat", ".buddy/web-profiles/2/sign-ins.dat",
            ".buddy/web-filters/easylist.txt", ".buddy/manual/bundle/x.json", ".buddy/audio_assistant/peaks/p.bin",
            ".buddy_network/identity.json", ".buddy_network/identity.json.bak", ".buddy_network/keys.json",
            ".davinci_time_tracker/auto_backups/b.json", ".davinci_design/huge.bin", ".ssh/id_rsa",
            ".buddy/settings.json.tmp", ".plain/x.corrupt-1",
            ".buddy/audio_assistant/play_along/song-1.wav", ".buddy/audio_assistant/preview/fx-1.wav",
        ]
        for rel in self.kept + self.left:
            self.write(rel, rel.encode())

    def write(self, rel, data=b"x"):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class WhatGoesInTests(_Home):
    def test_settings_and_what_was_made_in_models_engines_and_secrets_out(self):
        got = sorted(name for _p, name in backup.files(TOOLS))
        self.assertEqual(got, sorted(self.kept))

    def test_a_bak_read_in_place_of_a_damaged_file_goes_in_under_its_name(self):
        # atomic_io.read_json() set the damaged file aside: the .bak is all there is.
        (self.home / ".buddy/settings.json").rename(self.home / ".buddy/settings.json.corrupt-1")
        (self.home / ".figma_fusion_importer_settings.json").rename(
            self.home / ".figma_fusion_importer_settings.json.corrupt-1")
        self.write(".figma_fusion_importer_settings.json.bak", b"recovered")
        self.write(".buddy/notes/tess.json.bak", b"recovered")
        self.write(".buddy_network/keys.json.bak")          # never: the primary's left out too
        os.remove(self.home / ".buddy_network/keys.json")
        got = dict((name, p.name) for p, name in backup.files(TOOLS))
        self.assertEqual(got[".buddy/settings.json"], "settings.json.bak")
        self.assertEqual(got[".figma_fusion_importer_settings.json"], ".figma_fusion_importer_settings.json.bak")
        self.assertEqual(got[".buddy/notes/tess.json"], "tess.json.bak")
        self.assertNotIn(".buddy_network/keys.json", got)
        self.assertFalse(any(n.endswith((".bak", ".corrupt-1")) for n in got))

    def test_the_folder_backups_go_in_is_never_backed_up(self):
        self.write(".buddy/backups/Buddy backup 2026-10-01 1200.zip")
        names = [n for _p, n in backup.files(TOOLS, leave_out=self.home / ".buddy" / "backups")]
        self.assertFalse(any("backups/" in n for n in names))

    def test_the_zip_holds_them_and_says_what_it_is(self):
        dest = self.home.parent / "out" / "b.zip"
        said = []
        m = backup.make(dest, TOOLS, "1.13.0", lambda t, p: said.append(p))
        self.assertEqual((m["files"], m["version"]), (len(self.kept), "1.13.0"))
        self.assertEqual(said[-1], 100)
        self.assertFalse(dest.with_name("b.zip.part").exists())
        with zipfile.ZipFile(dest) as z:
            self.assertEqual(sorted(n for n in z.namelist() if n != backup.MANIFEST), sorted(self.kept))
            self.assertEqual(z.read(".buddy/settings.json"), b".buddy/settings.json")
        self.assertEqual(backup.read_manifest(dest)["files"], len(self.kept))

    def test_a_file_dated_before_1980_is_included_not_dropped_or_fatal(self):
        old = self.write(".buddy/ancient.json", b"old")
        os.utime(old, (0, 0))
        dest = self.home.parent / "out" / "b.zip"
        backup.make(dest, TOOLS)
        with zipfile.ZipFile(dest) as z:
            self.assertEqual(z.read(".buddy/ancient.json"), b"old")

    def test_a_failed_write_leaves_nothing_half_made(self):
        dest = self.home.parent / "out" / "b.zip"
        with mock.patch("json.dumps", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                backup.make(dest, TOOLS)
        self.assertEqual(list(dest.parent.iterdir()), [])


class RestoreTests(_Home):
    def zip_with(self, names, manifest=None):
        path = self.home.parent / "in.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(backup.MANIFEST, json.dumps(manifest or {"app": "Buddy", "format": 1, "made": "2026-10-01 12:00"}))
            for name, data in names.items():
                z.writestr(name, data)
        return path

    def test_not_a_backup_or_from_a_newer_buddy(self):
        junk = self.home.parent / "junk.zip"
        junk.write_bytes(b"not a zip")
        with self.assertRaisesRegex(backup.BackupError, "isn't a Buddy backup"):
            backup.read_manifest(junk)
        with self.assertRaisesRegex(backup.BackupError, "isn't a Buddy backup"):
            backup.read_manifest(self.zip_with({}, {"app": "Other", "format": 1}))
        with self.assertRaisesRegex(backup.BackupError, "newer Buddy"):
            backup.read_manifest(self.zip_with({}, {"app": "Buddy", "format": 99}))

    def test_staged_then_put_in_place_at_start_what_was_there_kept(self):
        path = self.zip_with({".buddy/settings.json": b"restored", ".plain/settings.json": b"plain restored",
                              ".buddy/new/thing.json": b"new"})
        self.assertEqual(backup.stage_restore(path, TOOLS)["made"], "2026-10-01 12:00")
        self.assertEqual((self.home / ".buddy/settings.json").read_bytes(), b".buddy/settings.json")   # not yet
        self.assertEqual(backup.pending()["made"], "2026-10-01 12:00")
        done = backup.apply_pending(TOOLS, "1.13.0")
        self.assertEqual((done["files"], done["failed"]), (3, []))
        self.assertEqual((self.home / ".buddy/settings.json").read_bytes(), b"restored")
        self.assertEqual((self.home / ".buddy/new/thing.json").read_bytes(), b"new")
        self.assertTrue((self.home / ".buddy/ask_buddy/conversations.json").is_file())   # not in it: left alone
        self.assertIsNone(backup.pending())
        self.assertFalse((self.home / ".buddy" / backup.PENDING).exists())
        with zipfile.ZipFile(self.home / ".buddy" / backup.BEFORE) as z:
            self.assertEqual(z.read(".buddy/settings.json"), b".buddy/settings.json")     # how it was
        self.assertIsNone(backup.apply_pending(TOOLS))                                     # once

    def test_a_restore_writes_only_buddys_own_folders(self):
        evil = {"../outside.txt": b"x", ".ssh/authorized_keys": b"x", "/abs.txt": b"x", "C:/win.txt": b"x",
                ".buddy/../.ssh/k": b"x", ".buddy/web/Cookies": b"x", ".buddy_network/keys.json": b"x",
                ".davinci_design/x": b"x", ".plain/ok.json": b"fine"}
        backup.stage_restore(self.zip_with(evil), TOOLS)
        staged = self.home / ".buddy" / backup.PENDING / "home"
        # An older backup's tool folder lands where the tools keep things now.
        self.assertEqual([p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()],
                         [".buddy/tools/plain/ok.json"])
        backup.apply_pending(TOOLS)
        self.assertFalse((self.home.parent / "outside.txt").exists())
        self.assertEqual((self.home / ".ssh/id_rsa").read_bytes(), b".ssh/id_rsa")
        self.assertEqual((self.home / ".buddy_network/keys.json").read_bytes(), b".buddy_network/keys.json")

    def test_a_before_restore_backup_that_fails_oddly_doesnt_stop_the_restore(self):
        backup.stage_restore(self.zip_with({".buddy/settings.json": b"restored"}), TOOLS)
        with mock.patch.object(backup, "make", side_effect=ValueError("ZIP does not support timestamps")):
            done = backup.apply_pending(TOOLS, "1.14.0")
        self.assertEqual((done["files"], done["failed"]), (1, []))
        self.assertEqual((self.home / ".buddy/settings.json").read_bytes(), b"restored")
        self.assertIsNone(backup.pending())

    def test_a_pending_restore_can_be_called_off(self):
        backup.stage_restore(self.zip_with({".buddy/settings.json": b"restored"}), TOOLS)
        backup.cancel_pending()
        self.assertIsNone(backup.apply_pending(TOOLS))
        self.assertEqual((self.home / ".buddy/settings.json").read_bytes(), b".buddy/settings.json")


class AutomaticTests(unittest.TestCase):
    def test_prune_keeps_the_newest_and_touches_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            names = [backup.PREFIX + f"2026-10-0{d} 1200.zip" for d in range(1, 8)]
            for n in names + ["Buddy backup mine.zip", "notes.txt", backup.PREFIX + "2026-10-08 1200 (2).zip"]:
                (folder / n).write_bytes(b"x")
            gone = backup.prune(folder, 5)
            self.assertEqual([p.name for p in gone], names[:3])
            left = sorted(p.name for p in folder.iterdir())
            self.assertIn("Buddy backup mine.zip", left)
            self.assertIn("notes.txt", left)
            self.assertEqual(len(backup.auto_backups(folder)), 5)

    def test_when_one_is_due(self):
        now = time.mktime(time.strptime("2026-10-08 12:00", "%Y-%m-%d %H:%M"))
        self.assertTrue(backup.due(None, 1, now))
        self.assertTrue(backup.due("garbage", 1, now))
        self.assertFalse(backup.due("2026-10-08 09:00", 1, now))
        self.assertTrue(backup.due("2026-10-07 12:30", 1, now))       # an hour's grace
        self.assertFalse(backup.due("2026-10-03 12:00", 7, now))
        self.assertTrue(backup.due("2026-10-01 12:00", 7, now))


class SettingsPageTests(unittest.TestCase):
    def test_the_page_and_its_settings(self):
        shared = Mem()
        page = sf.backup_page(shared, {"running": None, "error": "", "pending": None, "folder": "D:\\Backups"})
        self.assertEqual((page["id"], page["group"]), ("backup", "general"))
        by = {f.get("key"): f for f in page["fields"] if f.get("key")}
        self.assertFalse(by["auto_backup"]["value"])                             # off until turned on
        self.assertTrue(all(by[k]["disabled"] for k in ("auto_backup_every", "auto_backup_folder", "auto_backup_keep")))
        self.assertEqual(by["auto_backup_folder"]["browse"], "backup_browse")
        actions = [a["action"] for f in page["fields"] if f["kind"] == "buttons" for a in f["items"]]
        self.assertEqual(actions, ["backup_now", "backup_restore", "backup_open_folder"])
        self.assertEqual(sf.apply_shell(shared, "auto_backup", True), "backup")
        self.assertEqual(sf.apply_shell(shared, "auto_backup_every", "weekly"), "backup")
        self.assertIsNone(sf.apply_shell(shared, "auto_backup_every", "hourly"))
        self.assertEqual(sf.apply_shell(shared, "auto_backup_keep", 20), "backup")
        self.assertIsNone(sf.apply_shell(shared, "auto_backup_keep", 7))
        self.assertIsNone(sf.apply_shell(shared, "auto_backup_folder", "relative\\path"))
        self.assertEqual(sf.apply_shell(shared, "auto_backup_folder", os.path.abspath("x")), "backup")
        self.assertEqual(shared["auto_backup_folder"], os.path.abspath("x"))
        page = sf.backup_page(shared, {"running": {"text": "Backing up a", "pct": 40, "auto": True}, "error": "",
                                       "pending": {"made": "2026-10-01 12:00"}, "folder": "x"})
        kinds = [f["kind"] for f in page["fields"]]
        self.assertIn("progress", kinds)
        self.assertFalse(next(f for f in page["fields"] if f.get("key") == "auto_backup_every")["disabled"])
        actions = [a["action"] for f in page["fields"] if f["kind"] == "buttons" for a in f["items"]]
        self.assertIn("backup_cancel_restore", actions)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class RunnerTests(_Home):
    def setUp(self):
        super().setUp()
        self.app = QApplication.instance() or QApplication([])
        self.folder = self.home.parent / "Backups"
        self.shared = Mem(auto_backup=True, auto_backup_folder=str(self.folder), auto_backup_keep=5)
        self.runner = BackupRunner(self.shared, TOOLS)

    def wait(self, ms=5000):
        loop = QEventLoop()
        for _ in range(ms // 20):
            if self.runner.running is None:
                return
            QTimer.singleShot(20, loop.quit)
            loop.exec()

    def test_off_means_none(self):
        self.shared["auto_backup"] = False
        self.runner.check()
        self.assertIsNone(self.runner.running)

    def test_an_automatic_one_written_remembered_and_pruned(self):
        self.folder.mkdir()
        for d in range(1, 7):
            (self.folder / (backup.PREFIX + f"2026-09-0{d} 1200.zip")).write_bytes(b"x")
        self.runner.check()
        self.assertIsNotNone(self.runner.running)
        self.assertFalse(self.runner.back_up(None))                  # one at a time
        self.wait()
        self.assertEqual(self.runner.error, "")
        self.assertTrue(self.shared["last_auto_backup"])
        self.assertTrue(Path(self.shared["last_auto_backup_file"]).is_file())
        self.assertEqual(len(backup.auto_backups(self.folder)), 5)  # the oldest two went
        self.assertTrue(Path(self.shared["last_auto_backup_file"]) in backup.auto_backups(self.folder))
        self.runner.check()
        self.assertIsNone(self.runner.running)                        # not due again today

    def test_a_folder_it_cant_write_is_said(self):
        self.shared["auto_backup_folder"] = str(self.home / ".buddy" / "settings.json" / "sub")   # under a file
        self.runner.check()
        self.wait()
        self.assertIn("Couldn't back up", self.runner.error)
        self.assertNotIn("last_auto_backup", self.shared)

    def test_back_up_now_says_where(self):
        got = []
        self.runner.back_up(self.home.parent / "mine.zip", lambda r, e: got.append((r, e)))
        self.wait()
        result, error = got[0]
        self.assertEqual(error, "")
        self.assertEqual(result["files"], len(self.kept))
        self.assertNotIn("last_auto_backup", self.shared)              # a manual one isn't the automatic one


if __name__ == "__main__":
    unittest.main()
