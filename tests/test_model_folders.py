"""Where each tool's models are kept (core/model_folders.py): Buddy's own
folder or one picked, what a change moves (only Buddy's models - never
another app's in a shared folder), moving on one drive and between two,
what "where Buddy keeps things" lists (core/buddy_folders.py), and the
Settings window's side of both. Temporary folders throughout: never the
real settings, models or ~/.buddy."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _paths  # noqa: F401
from core import buddy_folders, model_folders, tool_dirs
from pages.generative import engine_setup as ges
from pages.manual_chat import local_llama as ll
from pages.transcribe import env_setup as tes

try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    import core.settings_dialog as settings_dialog
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


class _Temp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.buddy = self.root / ".buddy"
        tools = copy.deepcopy(model_folders.TOOLS)
        for tool, t in tools.items():
            t["default"] = self.buddy / tool / "models"
        for p in [mock.patch.object(model_folders, "TOOLS", tools),
                  mock.patch.object(model_folders, "BUDDY_DIR", self.buddy),
                  mock.patch.object(model_folders, "CONFIG", self.buddy / "model_folders.json"),
                  # set_folder() writes into the loaded modules: put back after.
                  mock.patch.object(tes, "MODELS_DIR", tools["transcribe"]["default"]),
                  mock.patch.object(ges, "MODELS_DIR", tools["generative"]["default"]),
                  mock.patch.object(ll, "MODELS_DIR", tools["manual_chat"]["default"])]:
            p.start()
            self.addCleanup(p.stop)

    def write(self, path, data=b"x"):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class FolderTests(_Temp):
    def test_its_own_folder_until_one_is_picked(self):
        self.assertEqual(model_folders.folder("transcribe"), self.buddy / "transcribe" / "models")
        self.assertFalse(model_folders.is_custom("transcribe"))
        mine = self.root / "AI models"
        self.assertEqual(model_folders.set_folder("transcribe", mine), mine)
        self.assertTrue(model_folders.is_custom("transcribe"))
        self.assertEqual(tes.MODELS_DIR, mine)                     # applied to the loaded module at once
        self.assertEqual(tes.buddy_model_dir("small"), mine / "small")
        self.assertFalse(model_folders.is_custom("generative"))   # one tool's choice is its own
        self.assertEqual(json.loads(model_folders.CONFIG.read_text(encoding="utf-8")), {"transcribe": str(mine)})
        model_folders.set_folder("transcribe", None)              # back to Buddy's
        self.assertEqual(tes.MODELS_DIR, self.buddy / "transcribe" / "models")
        self.assertEqual(json.loads(model_folders.CONFIG.read_text(encoding="utf-8")), {})

    def test_a_damaged_or_odd_file_means_buddys_folders(self):
        self.write(model_folders.CONFIG, b"{not json")
        self.assertEqual(model_folders.folder("generative"), self.buddy / "generative" / "models")
        self.write(model_folders.CONFIG, json.dumps({"generative": "relative/path", "transcribe": 3}).encode())
        self.assertEqual(model_folders.folder("generative"), self.buddy / "generative" / "models")
        self.assertEqual(model_folders.folder("transcribe"), self.buddy / "transcribe" / "models")

    def test_what_folders_it_takes(self):
        self.assertIn("whole", model_folders.check_folder("transcribe", "relative"))
        self.assertIn("already", model_folders.check_folder("transcribe", self.buddy / "transcribe" / "models"))
        self.assertIn("engines", model_folders.check_folder("transcribe", self.buddy / "generative" / "venv" / "x"))
        self.assertIn("outside", model_folders.check_folder("transcribe", self.buddy / "transcribe" / "models" / "in"))
        self.assertEqual(model_folders.check_folder("transcribe", self.root / "new" / "deep"), "")
        self.assertTrue((self.root / "new" / "deep").is_dir())     # made, and written to
        self.assertEqual(os.listdir(self.root / "new" / "deep"), [])
        with mock.patch("tempfile.mkstemp", side_effect=PermissionError(13, "Access is denied")):
            self.assertIn("can't write", model_folders.check_folder("transcribe", self.root / "locked"))


class OwnModelsTests(_Temp):
    def test_subtitles_moves_its_model_folders_only(self):
        where = self.root / "shared"
        small = self.write(where / "small" / "model.bin").parent
        nllb = self.write(where / "nllb-600m" / "model.bin").parent
        self.write(where / "someone-elses" / "model.bin")
        self.write(where / "small.downloading" / "model.bin")      # half a download stays
        self.assertEqual(sorted(tes.own_models(where)), sorted([small, nllb]))

    def test_generative_moves_its_files_and_their_checks(self):
        where = self.root / "shared"
        lama = self.write(where / ges.MODELS["lama"]["name"])
        stamp = self.write(where / (ges.MODELS["lama"]["name"] + ".verified.json"))
        sam = self.write(where / ges.MODELS["sam2-tiny"]["name"] / "constants.json").parent
        self.write(where / "comfy_model.safetensors")
        self.write(where / (ges.MODELS["birefnet"]["name"] + ".downloading"))
        self.assertEqual(sorted(ges.own_models(where)), sorted([lama, stamp, sam]))

    def test_generative_remove_everything_spares_other_apps_files(self):
        ges.MODELS_DIR = self.root / "shared"
        lama = self.write(ges.model_path("lama"))
        theirs = self.write(ges.MODELS_DIR / "comfy_model.safetensors")
        with mock.patch.object(ges, "VENV_DIR", self.root / "venv"):
            ges.remove()
        self.assertFalse(lama.exists())
        self.assertTrue(theirs.exists())

    def test_ask_buddy_takes_every_gguf_from_its_own_folder_but_only_its_own_from_a_shared_one(self):
        own = self.root / "own"
        a = self.write(own / ll.EMBEDDING_GEMMA.name, b"GGUF")
        b = self.write(own / "dropped-in.gguf", b"GGUF")
        cache = self.write(own / ll.VERIFIED_FILE, b"[]")
        self.assertEqual(sorted(ll.own_models(own, True)), sorted([a, b, cache]))
        shared = self.root / "lmstudio"
        mine = self.write(shared / ll.EMBEDDING_GEMMA.name, b"GGUF")
        self.write(shared / "qwen3-8b-Q4_K_M.gguf", b"GGUF")
        self.assertEqual(ll.own_models(shared, False), [mine])

    def test_ask_buddy_finds_models_in_a_shared_folders_subfolders(self):
        ll.MODELS_DIR = self.root / "lmstudio"
        top = self.write(ll.MODELS_DIR / "top.gguf", b"GGUF")
        nested = self.write(ll.MODELS_DIR / "lmstudio-community" / "Qwen3-GGUF" / "qwen3.gguf", b"GGUF")
        self.write(ll.MODELS_DIR / "a" / "b" / "c" / "too-deep.gguf", b"GGUF")
        self.assertEqual(ll.model_files(), [nested, top])

    def test_own_models_asks_the_tools_module(self):
        model_folders.set_folder("transcribe", self.root / "picked")
        small = self.write(self.root / "picked" / "small" / "model.bin").parent
        self.assertEqual(model_folders.own_models("transcribe"), [small])
        self.assertEqual(model_folders.own_models("generative"), [])          # not there yet


class MoveTests(_Temp):
    def setUp(self):
        super().setUp()
        self.src = self.root / "from"
        self.dest = self.root / "to"
        self.file = self.write(self.src / "lama.onnx", b"a" * 3000)
        self.folder = self.write(self.src / "small" / "model.bin", b"b" * 5000).parent
        self.write(self.src / "small" / "sub" / "config.json", b"{}")

    def check_moved(self):
        self.assertEqual((self.dest / "lama.onnx").read_bytes(), b"a" * 3000)
        self.assertEqual((self.dest / "small" / "model.bin").read_bytes(), b"b" * 5000)
        self.assertTrue((self.dest / "small" / "sub" / "config.json").is_file())
        self.assertFalse(self.file.exists())
        self.assertFalse(self.folder.exists())
        self.assertEqual([p.name for p in self.dest.iterdir() if model_folders.MOVING in p.name], [])

    def test_on_one_drive_a_rename(self):
        said = []
        with mock.patch.object(model_folders, "_copy") as copy_:
            self.assertEqual(model_folders.move([self.file, self.folder], self.dest, lambda t, p: said.append(p)), [])
        copy_.assert_not_called()
        self.check_moved()
        self.assertEqual(said[-1], 100)

    def test_between_drives_a_copy_then_the_old_one_deleted(self):
        said = []
        with mock.patch.object(model_folders, "_one_drive", return_value=False):
            self.assertEqual(model_folders.move([self.file, self.folder], self.dest, lambda t, p: said.append(p)), [])
        self.check_moved()
        self.assertEqual(said, sorted(said))                      # the bar only goes forward
        self.assertGreater(len(said), 3)

    def test_a_copy_cut_short_leaves_the_original_and_nothing_half_made(self):
        with mock.patch.object(model_folders, "_one_drive", return_value=False), \
                mock.patch("shutil.copystat", side_effect=OSError(28, "No space left on device")):
            problems = model_folders.move([self.file], self.dest)
        self.assertEqual(len(problems), 1)
        self.assertIn("still in", problems[0])
        self.assertTrue(self.file.exists())
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_one_in_the_new_folder_already_is_kept_and_the_old_left(self):
        self.write(self.dest / "lama.onnx", b"theirs")
        problems = model_folders.move([self.file], self.dest)
        self.assertIn("already", problems[0])
        self.assertEqual((self.dest / "lama.onnx").read_bytes(), b"theirs")
        self.assertTrue(self.file.exists())

    def test_an_old_copy_that_wont_delete_is_reported(self):
        with mock.patch.object(model_folders, "_one_drive", return_value=False), \
                mock.patch.object(Path, "unlink", side_effect=PermissionError(13, "in use")):
            problems = model_folders.move([self.file], self.dest)
        self.assertIn("couldn't be deleted", problems[0])
        self.assertTrue((self.dest / "lama.onnx").is_file())


class PlacesTests(_Temp):
    def test_what_where_lists(self):
        for name in ("settings.json", "crash.log", "settings.json.bak"):
            self.write(self.buddy / name, b"{}")
        self.write(self.buddy / "web-history" / "default.json", b"{}")
        self.write(self.buddy / "mystery" / "x", b"1")
        self.write(self.buddy / "transcribe" / "venv" / "python.exe", b"1" * 10)
        self.write(self.root / ".plain" / "settings.json", b"{}")
        model_folders.set_folder("generative", self.root / "D-drive models")
        with mock.patch.object(buddy_folders, "BUDDY_DIR", self.buddy), \
                mock.patch.object(buddy_folders, "ENGINES", [("Subtitles' engine", "it", self.buddy / "transcribe" / "venv"),
                                                              ("Gone", "not there", self.buddy / "nope")]), \
                mock.patch.object(Path, "home", return_value=self.root), \
                mock.patch.object(tool_dirs, "home", return_value=str(self.root)), \
                mock.patch.dict(os.environ, {"HF_HOME": str(self.root / "hf")}):
            groups = buddy_folders.places([("plain", "Plain"), ("nothing", "Nothing")])
        by = {g["title"]: g["rows"] for g in groups}
        self.assertEqual([r["label"] for r in by["Settings and logs"]],
                         ["Settings", "Models folders", "Crash log"])     # no .bak
        self.assertTrue(by["Settings and logs"][0]["file"])
        models = {r["label"]: r for r in by["Models"]}
        self.assertEqual(models["Generative models"]["path"], str(self.root / "D-drive models"))
        self.assertEqual(models["Generative models"]["chip"], "Your folder")
        self.assertEqual(models["Subtitles models"]["chip"], "")
        self.assertNotIn("Hugging Face cache", models)                         # none here
        self.assertEqual([r["label"] for r in by["AI engines"]], ["Subtitles' engine"])
        self.assertEqual([r["label"] for r in by["In Buddy's folder"]], ["mystery", "Subtitles", "Web history"])
        self.assertEqual([r["label"] for r in by["Each tool's settings and data"]], ["Plain"])
        ids = [r["id"] for g in groups for r in g["rows"]]
        self.assertEqual(len(ids), len(set(ids)))
        sizes = buddy_folders.sizes(groups)
        self.assertEqual(sizes[str(self.buddy / "transcribe" / "venv")], 10)


def _wait_for(condition, ms=5000):
    loop = QEventLoop()
    for _ in range(ms // 20):
        if condition():
            return True
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return condition()


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class SettingsWindowTests(_Temp):
    """The Model library's "Where models are kept" and the header's ⓘ -
    the dialog's methods without its window."""

    def setUp(self):
        super().setUp()
        self.app = QApplication.instance() or QApplication([])
        self.page = mock.Mock(tool_id="transcribe", display_name="Subtitles")
        self.page.models_busy.return_value = ""
        self.d = mock.Mock()
        self.d.tool.return_value = self.page
        self.d.tools.return_value = [self.page]
        self.d.main_window.backups = None
        self.d._places = {}
        self.d._pending = None
        for name in ("_folder_fields", "_change_models_folder", "_answer_move", "_finish_models_folder",
                     "_show_where", "_where_sized", "on_action"):
            setattr(self.d, name, getattr(settings_dialog.SettingsDialog, name).__get__(self.d))
        self.addCleanup(settings_dialog._MOVING.clear)

    def asked(self):
        return next(c.args[1] for c in self.d.emit.call_args_list if c.args[0] == "ask")

    def test_a_row_for_each_tool(self):
        model_folders.set_folder("manual_chat", self.root / "lm")
        heading, hint, rows = self.d._folder_fields()
        self.assertEqual(heading["text"], "Where models are kept")
        by = {r["id"]: r for r in rows["rows"]}
        self.assertEqual(set(by), {"models-folder-transcribe", "models-folder-generative", "models-folder-manual_chat"})
        ask = by["models-folder-manual_chat"]
        self.assertEqual((ask["note"], ask["chip"]["text"], ask["owner"]), (str(self.root / "lm"), "Your folder", "shell"))
        self.assertIn("models_default:manual_chat", [a["action"] for a in ask["actions"]])
        self.assertNotIn("models_default:transcribe", [a["action"] for a in by["models-folder-transcribe"]["actions"]])
        settings_dialog._MOVING.update(tool="transcribe", text="Moving small …", pct=40)
        fields = self.d._folder_fields()
        self.assertEqual((fields[-1]["kind"], fields[-1]["value"]), ("progress", 40))
        self.assertTrue(all(a["disabled"] for r in fields[2]["rows"] for a in r["actions"] if a["label"] == "Change…"))

    def test_changing_it_asks_then_moves_buddys_models(self):
        old = self.buddy / "transcribe" / "models"
        self.write(old / "small" / "model.bin", b"m" * 2000)
        new = self.root / "D models"
        with mock.patch.object(settings_dialog.QFileDialog, "getExistingDirectory", return_value=str(new)):
            self.d.on_action({"section": "shell", "action": "models_folder:transcribe"})
        ask = self.asked()
        self.assertEqual([b["action"] for b in ask["buttons"]], ["", "models_move:no", "models_move:yes"])
        self.assertIn(str(old), ask["text"])
        self.assertEqual(tes.MODELS_DIR, old)                     # nothing changes until they answer
        self.d.on_action({"section": "shell", "action": "models_move:yes"})
        self.page.models_moving.assert_called_once()
        self.assertTrue(_wait_for(lambda: self.page.models_moved.called))
        self.assertEqual(tes.MODELS_DIR, new)
        self.assertTrue((new / "small" / "model.bin").is_file())
        self.assertFalse((old / "small").exists())
        self.assertEqual(settings_dialog._MOVING, {})

    def test_dont_move_leaves_them_and_changes_the_folder(self):
        old = self.buddy / "transcribe" / "models"
        self.write(old / "small" / "model.bin")
        new = self.root / "elsewhere"
        with mock.patch.object(settings_dialog.QFileDialog, "getExistingDirectory", return_value=str(new)):
            self.d.on_action({"section": "shell", "action": "models_folder:transcribe"})
        self.d.on_action({"section": "shell", "action": "models_move:no"})
        self.assertTrue(_wait_for(lambda: self.page.models_moved.called))
        self.assertEqual(tes.MODELS_DIR, new)
        self.assertTrue((old / "small" / "model.bin").is_file())

    def test_a_busy_tool_or_a_bad_folder_changes_nothing(self):
        self.page.models_busy.return_value = "Subtitles is busy – try again when it's done."
        with mock.patch.object(settings_dialog.QFileDialog, "getExistingDirectory") as pick:
            self.d.on_action({"section": "shell", "action": "models_folder:transcribe"})
        pick.assert_not_called()
        self.d.ui.status.assert_called_with("Subtitles is busy – try again when it's done.", "")
        self.page.models_busy.return_value = ""
        with mock.patch.object(settings_dialog.QFileDialog, "getExistingDirectory",
                               return_value=str(self.buddy / "generative" / "venv")):
            self.d.on_action({"section": "shell", "action": "models_folder:transcribe"})
        self.d.alert_soon.assert_called_once()
        self.page.models_moving.assert_not_called()

    def test_the_where_list_then_its_sizes(self):
        with mock.patch.object(buddy_folders, "BUDDY_DIR", self.buddy):
            self.write(self.buddy / "settings.json", b"{}")
            with mock.patch.object(settings_dialog, "_OPEN", [self.d]):
                self.d.on_action({"section": "shell", "action": "where"})
                first = self.d.emit.call_args_list[0].args
                self.assertEqual(first[0], "where")
                self.assertNotIn("sizes", first[1])
                self.assertTrue(_wait_for(lambda: self.d.emit.call_count >= 2))
        sized = self.d.emit.call_args_list[1].args[1]
        self.assertEqual(sized["sizes"][str(self.buddy / "settings.json")], "1 KB")
        place = next(r for g in sized["groups"] for r in g["rows"] if r["label"] == "Settings")
        with mock.patch.object(settings_dialog, "open_in_files") as opened:
            self.d.on_action({"section": "shell", "action": f"open_place:{place['id']}"})
        opened.assert_called_once_with(str(self.buddy / "settings.json"))


if __name__ == "__main__":
    unittest.main()
