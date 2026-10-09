"""Invalid settings must preserve the file and let Buddy start normally."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _paths  # noqa: F401
from core import atomic_io, settings_store, tool_dirs


class SettingsRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.shared = self.folder / "shared" / "settings.json"
        for patcher in (
            mock.patch.object(settings_store, "BUDDY_DIR", str(self.shared.parent)),
            mock.patch.object(settings_store, "SHARED_SETTINGS_PATH", str(self.shared)),
            mock.patch.object(tool_dirs, "home", return_value=str(self.folder)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_wrong_root_types_start_with_defaults_and_preserve_the_file(self):
        for value in ([], [1, 2], "text", 42, True, None):
            for kind in ("shared", "tool"):
                with self.subTest(value=value, kind=kind):
                    path = (self.shared if kind == "shared"
                            else self.folder / ".buddy" / "tools" / "test_tool" / "settings.json")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(value), encoding="utf-8")
                    settings = (settings_store.SharedSettings() if kind == "shared"
                                else settings_store.ToolSettings("test_tool", {"option": "default"}))
                    self.assertEqual(len(settings.load_warnings), 1)
                    self.assertFalse(path.exists())
                    self.assertEqual(settings.get("theme") if kind == "shared" else settings.get("option"),
                                     "Resolve" if kind == "shared" else "default")
                    kept = list(path.parent.glob("settings.json.corrupt-*"))
                    self.assertTrue(any(json.loads(p.read_text(encoding="utf-8")) == value for p in kept))

    def test_wrong_root_type_recovers_the_previous_settings(self):
        atomic_io.write_json(str(self.shared), {"theme": "Retro", "subtheme": "Custom"})
        atomic_io.write_json(str(self.shared), [])
        for _ in range(2):
            settings = settings_store.SharedSettings()
            self.assertEqual(settings.get("theme"), "Retro")
            self.assertEqual(settings.get("subtheme"), "Custom")
            self.assertEqual(len(settings.load_warnings), 1)


if __name__ == "__main__":
    unittest.main()
