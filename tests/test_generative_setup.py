"""Generative's model downloads (pages/generative/engine_setup.py): a model
in several files (SAM 2: five graphs and its constants) is pinned, fetched,
checked and removed as one - each file against its own size and SHA-256."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _paths  # noqa: F401

from pages.generative import engine_setup as es


def _entry(src: Path, parts: dict) -> dict:
    """A model in parts whose files are in `src` - fetched by file:// URL."""
    out = {"label": "Test model", "use": "tests", "license": "MIT", "repo": "x/y", "revision": "0" * 40,
           "name": "test-model", "parts": []}
    for name, data in parts.items():
        (src / name.rsplit("/", 1)[-1]).write_bytes(data)
        out["parts"].append({"file": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    out["size"] = sum(p["size"] for p in out["parts"])
    return out


class PartsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.src = root / "server"
        self.src.mkdir()
        self.models = root / "models"
        self.entry = _entry(self.src, {"onnx/a.onnx": b"graph a" * 1000, "onnx/b.onnx": b"graph b" * 300,
                                       "constants.json": b'{"x": 1}'})
        patches = [mock.patch.object(es, "MODELS_DIR", self.models),
                   mock.patch.dict(es.MODELS, {"test-model": self.entry})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        real = es.model_files

        def local(mid):              # the same files, from this folder instead of Hugging Face
            files = real(mid)
            if mid == "test-model":
                for f in files:
                    f["url"] = (self.src / f["path"].name).as_uri()
            return files
        p = mock.patch.object(es, "model_files", side_effect=local)
        p.start()
        self.addCleanup(p.stop)

    def test_sam_is_pinned_file_by_file_into_a_folder_of_its_own(self):
        m = es.MODELS["sam2-tiny"]
        self.assertEqual(m["size"], sum(p["size"] for p in m["parts"]))
        self.assertEqual(es.size_mb("sam2-tiny"), 190)
        names = [p["file"].rsplit("/", 1)[-1] for p in m["parts"]]
        self.assertEqual(sorted(names), ["constants.json", "mask_decoder.onnx", "memory_attention.onnx",
                                         "memory_encoder.onnx", "pointer_tpos.onnx", "vision_encoder.onnx"])
        self.assertTrue(all(len(p["sha256"]) == 64 for p in m["parts"]))

    def test_downloaded_checked_and_ready_only_when_every_part_is(self):
        self.assertFalse(es.model_ready("test-model"))
        seen = []
        es._download_model("test-model", lambda text, pct=None: seen.append(text), lambda: False, 0, 100)
        self.assertTrue(es.model_ready("test-model"))
        folder = es.model_path("test-model")
        self.assertEqual(sorted(p.name for p in folder.iterdir() if not p.name.endswith(".verified.json")),
                         ["a.onnx", "b.onnx", "constants.json"])
        self.assertFalse([p for p in folder.iterdir() if p.name.endswith(".downloading")])
        self.assertEqual(es.disk_size()["test-model"], self.entry["size"])
        # One part changed on disk: not ready.
        (folder / "b.onnx").write_bytes(b"graph B" * 300)
        self.assertFalse(es.model_ready("test-model"))
        # Fetched again: only that part.
        seen.clear()
        es._download_model("test-model", lambda text, pct=None: seen.append(text), lambda: False, 0, 100)
        self.assertTrue(es.model_ready("test-model"))
        fetched = [t for t in seen if t.startswith("Downloading") and "(" in t and "MB)" not in t]
        self.assertTrue(fetched and all("b.onnx" in t for t in fetched), seen)
        es.remove("test-model")
        self.assertFalse(folder.exists())

    def test_a_part_that_doesnt_match_is_thrown_away(self):
        (self.src / "a.onnx").write_bytes(b"not the pinned file" * 10)
        with self.assertRaises(es.SetupError):
            es._download_model("test-model", lambda *a, **k: None, lambda: False, 0, 100)
        folder = es.model_path("test-model")
        self.assertFalse((folder / "a.onnx").exists())
        self.assertFalse((folder / "a.onnx.downloading").exists())
        self.assertFalse(es.model_ready("test-model"))


if __name__ == "__main__":
    unittest.main()


class GpuSetupTests(unittest.TestCase):
    """The graphics card add-on: offered for an RTX 30 or later, installed pinned, removed with what it compiled."""

    def test_offered_only_for_an_rtx_it_can_use(self):
        with mock.patch.object(es, "gpu_installed", return_value=False):
            for card, supported in (({"name": "NVIDIA GeForce RTX 5070 Ti", "memory_mb": 16303, "compute": 12.0}, True),
                                    ({"name": "NVIDIA GeForce RTX 3060", "memory_mb": 12288, "compute": 8.6}, True),
                                    ({"name": "NVIDIA GeForce GTX 1080", "memory_mb": 8192, "compute": 6.1}, False),
                                    (None, False)):
                with mock.patch.object(es, "nvidia_card", return_value=card):
                    got = es.gpu_status(True)
                    self.assertEqual(got["supported"], supported, card)
                    self.assertFalse(got["installed"])

    def test_installed_from_the_pinned_lock_with_hashes(self):
        lock = es.GPU_LOCK.read_text(encoding="utf-8")
        self.assertRegex(lock, r"onnxruntime-ep-nv-tensorrt-rtx-cu13==\d+\.\d+\.\d+")
        self.assertRegex(lock, r"--hash=sha256:[0-9a-f]{64}")
        from core import defender
        with mock.patch.object(es, "env_ready", return_value=(True, "")), \
                mock.patch.object(es.tes, "_run_streaming") as run, \
                mock.patch.object(defender, "scan", return_value=("clean", "")), \
                mock.patch.object(es, "gpu_installed", return_value=True):
            es.install_gpu(lambda *a, **k: None)
        args = run.call_args.args[0]
        for flag in ("--require-hashes", "--no-deps", "--only-binary"):
            self.assertIn(flag, args)
        self.assertEqual(args[-1], str(es.GPU_LOCK))
        with mock.patch.object(es, "env_ready", return_value=(False, "Not installed yet.")):
            with self.assertRaises(es.SetupError):
                es.install_gpu(lambda *a, **k: None)

    def test_removed_with_the_models_compiled_for_the_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "gpu"
            (cache / "lama-1234").mkdir(parents=True)
            (cache / "lama-1234" / "model_ctx.onnx").write_bytes(b"x")
            with mock.patch.object(es, "GPU_CACHE", cache), mock.patch.object(es, "venv_python",
                                                                             return_value=Path(tmp) / "none.exe"):
                es.remove_gpu()
            self.assertFalse(cache.exists())
