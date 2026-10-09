"""core/tool_dirs.py: each tool's folder moves from the home directory into
~/.buddy/tools once, is read from its older place until then, and an older
backup's names land in the new place (core/backup.py)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _paths  # noqa: F401
from core import backup, settings_store, tool_dirs


class _Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.home.mkdir()
        for target, name in ((tool_dirs, "home"), (backup, "home")):
            p = mock.patch.object(target, name, lambda: str(self.home) if target is tool_dirs else self.home)
            p.start()
            self.addCleanup(p.stop)

    def write(self, rel, data=b"x"):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class MoveTests(_Home):
    def test_a_tools_folder_in_home_moves_into_buddys_tools_folder_once(self):
        self.write(".games/settings.json", b"{}")
        self.write(".games/saved/one.json", b"1")
        self.assertEqual(tool_dirs.path("games"), str(self.home / ".games"))            # until it's moved
        self.assertEqual(tool_dirs.migrate("games"), "moved")
        new = self.home / ".buddy" / "tools" / "games"
        self.assertEqual(tool_dirs.path("games"), str(new))
        self.assertEqual((new / "saved" / "one.json").read_bytes(), b"1")
        self.assertFalse((self.home / ".games").exists())
        self.assertEqual(tool_dirs.migrate("games"), "")                                # nothing more to do

    def test_the_new_place_wins_once_it_exists_and_nothing_is_moved_over_it(self):
        self.write(".buddy/tools/games/settings.json", b"new")
        self.write(".games/settings.json", b"old")
        self.assertEqual(tool_dirs.migrate("games"), "")
        self.assertEqual(tool_dirs.path("games"), str(self.home / ".buddy" / "tools" / "games"))
        self.assertEqual((self.home / ".games" / "settings.json").read_bytes(), b"old")  # left alone

    def test_a_tool_with_nothing_yet_gets_the_new_place(self):
        self.assertEqual(tool_dirs.path("games"), str(self.home / ".buddy" / "tools" / "games"))
        self.assertEqual(tool_dirs.migrate("games"), "")

    def test_the_lone_settings_file_becomes_its_tools_settings_json(self):
        old = self.write(".figma_fusion_importer_settings.json", b'{"a": 1}')
        self.assertEqual(tool_dirs.file_path("svg_importer"), str(old))
        self.assertEqual(tool_dirs.migrate("svg_importer"), "moved")
        new = self.home / ".buddy" / "tools" / "svg_importer" / "settings.json"
        self.assertEqual(tool_dirs.file_path("svg_importer"), str(new))
        self.assertEqual(new.read_bytes(), b'{"a": 1}')
        self.assertFalse(old.exists())

    def test_every_known_bucket_at_once_says_what_moved(self):
        self.write(".davinci_time_tracker/entries.json", b"[]")
        self.write(".color_palette_manager/palettes.json", b"[]")
        self.write(".not_buddys/x", b"x")
        moved = tool_dirs.migrate_all()
        self.assertEqual(moved, {"davinci_time_tracker": "moved", "color_palette_manager": "moved"})
        self.assertTrue((self.home / ".buddy/tools/davinci_time_tracker/entries.json").is_file())
        self.assertTrue((self.home / ".not_buddys/x").is_file())                      # not Buddy's: untouched

    def test_a_folder_that_cant_move_is_read_where_it_is(self):
        self.write(".games/settings.json", b"{}")
        with mock.patch.object(tool_dirs.shutil, "move", side_effect=OSError("held open")):
            self.assertIn("held open", tool_dirs.migrate("games"))
        self.assertEqual(tool_dirs.path("games"), str(self.home / ".games"))

    def test_tool_settings_moves_its_own_bucket_and_reads_there(self):
        self.write(".plain/settings.json", b'{"option": "kept"}')
        s = settings_store.ToolSettings("plain", {"option": "default"})     # tool_dirs.home() is patched
        self.assertEqual(s.get("option"), "kept")
        self.assertEqual(Path(s._path), self.home / ".buddy" / "tools" / "plain" / "settings.json")
        self.assertFalse((self.home / ".plain").exists())


class RelocatedTests(unittest.TestCase):
    def test_an_older_backups_names_land_in_the_tools_folder(self):
        self.assertEqual(tool_dirs.relocated(".plain/settings.json"), ".buddy/tools/plain/settings.json")
        self.assertEqual(tool_dirs.relocated(".davinci_time_tracker/auto_backups/b.json"),
                         ".buddy/tools/davinci_time_tracker/auto_backups/b.json")
        self.assertEqual(tool_dirs.relocated(".figma_fusion_importer_settings.json"),
                         ".buddy/tools/svg_importer/settings.json")
        self.assertEqual(tool_dirs.relocated(".buddy/settings.json"), ".buddy/settings.json")
        self.assertEqual(tool_dirs.relocated(".buddy/tools/games/settings.json"), ".buddy/tools/games/settings.json")
        self.assertEqual(tool_dirs.relocated("Documents/x.txt"), "Documents/x.txt")       # refused later
        self.assertEqual(tool_dirs.relocated(".ssh"), ".ssh")


class BackupTests(_Home):
    TOOLS = ["plain", "buddy_network"]

    def test_a_backup_reads_the_new_place_and_leaves_its_secrets_out(self):
        self.write(".buddy/settings.json")
        self.write(".buddy/tools/plain/settings.json")
        self.write(".buddy/tools/buddy_network/settings.json")
        self.write(".buddy/tools/buddy_network/identity.json")
        self.write(".buddy/tools/buddy_network/keys.json")
        self.write(".buddy/tools/davinci_time_tracker/entries.json")
        self.write(".buddy/tools/davinci_time_tracker/auto_backups/b.json")
        self.write(".plain/old.json")                                                   # not moved: still read
        self.assertEqual(sorted(n for _p, n in backup.files(self.TOOLS)), [
            ".buddy/settings.json", ".buddy/tools/buddy_network/settings.json",
            ".buddy/tools/davinci_time_tracker/entries.json", ".buddy/tools/plain/settings.json",
            ".plain/old.json",
        ])

    def test_an_older_backup_restores_into_the_tools_folder(self):
        path = self.home.parent / "old.zip"
        import json
        import zipfile
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(backup.MANIFEST, json.dumps({"app": "Buddy", "format": 1, "made": "2026-10-01 12:00"}))
            z.writestr(".plain/settings.json", b"plain")
            z.writestr(".figma_fusion_importer_settings.json", b"svg")
            z.writestr(".buddy_network/keys.json", b"secret")
            z.writestr(".buddy/settings.json", b"shared")
        backup.stage_restore(path, self.TOOLS)
        done = backup.apply_pending(self.TOOLS)
        self.assertEqual(done["failed"], [])
        self.assertEqual((self.home / ".buddy/tools/plain/settings.json").read_bytes(), b"plain")
        self.assertEqual((self.home / ".buddy/tools/svg_importer/settings.json").read_bytes(), b"svg")
        self.assertEqual((self.home / ".buddy/settings.json").read_bytes(), b"shared")
        self.assertFalse((self.home / ".plain").exists())
        self.assertFalse((self.home / ".buddy/tools/buddy_network/keys.json").exists())  # never restored


if __name__ == "__main__":
    unittest.main()
