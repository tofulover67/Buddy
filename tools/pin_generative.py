"""Pins Generative's downloads: the engine's Python packages to exact
versions with each wheel's SHA-256, and checks the model pin against
Hugging Face. Run it to move to newer packages - a deliberate change,
reviewed in the diff - never at a user's install.

    py -3 tools/pin_generative.py packages   app/pages/generative/locks/engine.txt (needs uv)
    py -3 tools/pin_generative.py hq         app/pages/generative/locks/hq-nvidia.txt (needs uv)
    py -3 tools/pin_generative.py model      prints the model's current commit, size and SHA-256

Packages: uv resolves NumPy, OpenCV (headless) and ONNX Runtime for every
platform Buddy runs on at once (Windows x64, both Macs; Python 3.10-3.14).
The model pins live in engine_setup.MODELS; `model` says whether each
is still what its repo's latest commit has.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "app"))
sys.path.insert(0, str(REPO / "tools"))
from pages.generative import engine_setup as es  # noqa: E402
from pin_transcribe import get_json  # noqa: E402

# onnxruntime's last release with Intel Mac wheels, and with Python 3.10
# wheels, is 1.23.2: both branches spelled out, or uv settles on 1.23.2
# everywhere - which has no Python 3.14 wheels at all.
ONNXRUNTIME = [
    "onnxruntime<1.24 ; (sys_platform == 'darwin' and platform_machine == 'x86_64') or python_version < '3.11'",
    "onnxruntime>=1.24 ; (sys_platform != 'darwin' or platform_machine != 'x86_64') and python_version >= '3.11'",
]


def pin_packages():
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv isn't installed (py -3 -m pip install uv).")
    es.LOCK.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp) / "engine.in"
        wanted = [p for p in es.PACKAGES if p != "onnxruntime"] + ONNXRUNTIME
        req.write_text("".join(p + "\n" for p in wanted), encoding="utf-8")
        env = dict(os.environ, UV_CUSTOM_COMPILE_COMMAND="py -3 tools/pin_generative.py packages")
        subprocess.run([uv, "pip", "compile", str(req), "--universal", "--generate-hashes",
                        "--python-version", "3.10", "--only-binary", ":all:", "--no-header",
                        "--no-annotate", "-o", str(es.LOCK)], check=True, env=env)
    lines = [l for l in es.LOCK.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.lstrip().startswith("--hash")]
    print(f"{len(lines)} packages -> {es.LOCK}")


# The High quality add-on: PyTorch's CUDA 13 build (its own index) and
# diffusers, Windows x64 only, resolved with the engine's own pins as
# constraints so installing it never changes what the engine has.
HQ = ["torch==2.14.1+cu130", "diffusers==0.41.0", "safetensors", "accelerate"]
HQ_INDEX = "https://download.pytorch.org/whl/cu130"
HQ_HEADER = """# Generative's High quality add-on (engine_setup.install_hq): PyTorch (CUDA 13,
# from PyTorch's own index) and diffusers, for FLUX.2 klein (klein_fill.py) and
# ROSE (rose_fill.py) on an NVIDIA RTX card, Windows only. Resolved by uv with
# the engine's own pins as constraints (so nothing the engine has is changed),
# every wheel's SHA-256; installed with --require-hashes --only-binary :all:
# --no-deps. Re-pin: py -3 tools/pin_generative.py hq
# PyTorch's CUDA builds come from its own index; the hashes pin every file either way.
--extra-index-url {index}
"""


def pin_hq():
    import re
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv isn't installed (py -3 -m pip install uv).")
    win = " ; sys_platform == 'win32' and platform_machine == 'AMD64'"
    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp) / "hq.in"
        req.write_text("".join(p + win + "\n" for p in HQ), encoding="utf-8")
        cons = Path(tmp) / "engine.txt"
        pins = []
        for line in es.LOCK.read_text(encoding="utf-8").splitlines():
            line = line.strip().rstrip("\\").strip()
            if re.match(r"^[A-Za-z0-9_.-]+==", line):
                pins.append(line)
        cons.write_text("\n".join(pins) + "\n", encoding="utf-8")
        out = Path(tmp) / "hq.txt"
        subprocess.run([uv, "pip", "compile", str(req), "-c", str(cons), "--universal", "--generate-hashes",
                        "--python-version", "3.10", "--only-binary", ":all:", "--no-header", "--no-annotate",
                        "--extra-index-url", HQ_INDEX, "--index-strategy", "unsafe-best-match", "-o", str(out)],
                       check=True)
        es.HQ_LOCK.write_text(HQ_HEADER.format(index=HQ_INDEX) + out.read_text(encoding="utf-8"), encoding="utf-8")
    lines = [l for l in es.HQ_LOCK.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.lstrip().startswith(("--", "#"))]
    print(f"{len(lines)} packages -> {es.HQ_LOCK}")


def check_model():
    """Each model in engine_setup.MODELS (each part of one in parts): still
    what its repo's latest commit has?"""
    for mid, m in es.MODELS.items():
        if any("repo" in p for p in m.get("parts", [])):
            print(f"{mid}: parts from more than one repo - check each by hand")
            continue
        latest = get_json(f"https://huggingface.co/api/models/{m['repo']}/revision/main")["sha"]
        tree = {}
        for folder in {"", *(p["file"].rsplit("/", 1)[0] for p in m.get("parts", []) if "/" in p["file"])} | (
                {m["file"].rsplit("/", 1)[0]} if "/" in m.get("file", "") else set()):
            for e in get_json(f"https://huggingface.co/api/models/{m['repo']}/tree/{latest}/{folder}".rstrip("/")):
                tree[e.get("path")] = e
        print(f"{mid}: pinned {m['revision']}, latest {latest}")
        for p in m.get("parts") or [{"file": m["file"], "size": m["size"], "sha256": m["sha256"]}]:
            e = tree.get(p["file"])
            if e is None:
                print(f"  {p['file']}: not in the latest commit")
                continue
            oid = (e.get("lfs") or {}).get("oid")
            same = oid == p["sha256"] if oid else e.get("size") == p["size"]
            print(f"  {p['file']}: {'unchanged' if same else 'CHANGED - review before re-pinning'}")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "packages"
    if what == "packages":
        pin_packages()
    elif what == "hq":
        pin_hq()
    elif what == "model":
        check_model()
    else:
        raise SystemExit(__doc__)
