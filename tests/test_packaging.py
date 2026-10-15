"""The installer installs every package Buddy imports (installer/
requirements.txt), and the zip carries the non-Python files the app needs.

A new third-party import that isn't added to requirements.txt works on a
machine where it happens to be installed and fails on every fresh install -
this catches it here instead."""

import ast
import re
import sys
import unittest
from pathlib import Path

import _paths

ROOT = _paths.APP.parent

# Import name -> the pip package that provides it.
PROVIDED_BY = {"PySide6": "pyside6", "PIL": "pillow", "fitz": "pymupdf", "pymupdf": "pymupdf",
               "pymupdf4llm": "pymupdf4llm", "numpy": "numpy", "openpyxl": "openpyxl",
               "pynput": "pynput", "cryptography": "cryptography", "AppKit": "pyobjc-framework-cocoa",
               "objc": "pyobjc-framework-cocoa"}    # PyObjC's own module, which the Cocoa package brings
# Not pip packages: Resolve's own scripting module, and modules that run
# inside the transcription engine's private environment, which Buddy's own
# Setup window installs (pages/transcribe/env_setup.py).
NOT_FROM_PIP = {"DaVinciResolveScript", "faster_whisper", "ctranslate2", "onnx_asr",
                "tokenizers", "huggingface_hub", "env_setup",
                "av",   # PyAV, which faster-whisper brings: worker.py decodes with it
                # Generative's engine environment (pages/generative/engine_setup.py), and its
                # graphics card add-on (engine_setup.install_gpu)
                "cv2", "onnxruntime", "onnxruntime_ep_nv_tensorrt_rtx",
                # and its High quality add-on (engine_setup.install_hq)
                "torch", "diffusers", "safetensors"}


def third_party_imports():
    local = {p.name for p in _paths.APP.iterdir() if p.is_dir()} | {p.stem for p in _paths.APP.rglob("*.py")}
    found = {}
    for f in _paths.APP.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level
                     else [])
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top not in local and top not in NOT_FROM_PIP:
                    found.setdefault(top, str(f.relative_to(ROOT)))
    return found


class PackagingTests(unittest.TestCase):
    def test_installer_requirements_cover_every_import(self):
        reqs = set()
        for line in (ROOT / "installer" / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line:
                reqs.add(re.split(r"[<>=!~ ]", line)[0].lower())
        for module, where in third_party_imports().items():
            self.assertIn(module, PROVIDED_BY,
                          f"{where} imports {module!r}: add its pip package to installer/requirements.txt "
                          "and PROVIDED_BY here")
            self.assertIn(PROVIDED_BY[module], reqs, f"{module} ({where}) isn't in installer/requirements.txt")

    def test_zip_carries_runtime_assets(self):
        import build_buddy_zip as b
        for suffix in (".json", ".ttf", ".txt", ".drb", ".html", ".css", ".js",
                       ".safetensors"):     # Generative's prompt embeddings (pages/generative/data/)
            self.assertIn(suffix, b.INCLUDE_SUFFIXES)

    def test_installer_checks_what_requirements_install(self):
        iss = (ROOT / "installer" / "Buddy.iss").read_text(encoding="utf-8")
        check = re.search(r"-c \"import (PySide6[^\"]+)\"", iss).group(1)
        for module in ("PySide6.QtWidgets", "PySide6.QtMultimedia", "PySide6.QtWebEngineWidgets", "PIL", "numpy", "openpyxl",
                       "pynput", "pymupdf", "pymupdf4llm", "cryptography"):
            self.assertIn(module, check)

    def test_mac_installer_pins_the_same_python_as_windows(self):
        iss = (ROOT / "installer" / "Buddy.iss").read_text(encoding="utf-8")
        postinstall = (ROOT / "installer" / "mac" / "postinstall").read_text(encoding="utf-8")
        windows = re.search(r'#define PythonVersion "([\d.]+)"', iss).group(1)
        mac = re.search(r'PY_VERSION="([\d.]+)"', postinstall).group(1)
        self.assertEqual(mac, windows, "installer/mac/postinstall and installer/Buddy.iss pin different Pythons")
        self.assertRegex(postinstall, r'PY_SHA256="[0-9a-f]{64}"')


if __name__ == "__main__":
    unittest.main()
