#!/usr/bin/env python3
"""
Build the macOS installer: dist/Buddy-<version>.pkg.

    python3 build_mac_installer.py [--keep-zip]

Steps: build buddy.zip fresh (build_buddy_zip.py, without deploying), stage
it with the Buddy.py launcher, requirements.txt and installer/mac/postinstall
as the package's scripts, then pkgbuild + productbuild (Apple's own, in
every macOS) with installer/mac/distribution.xml (version from the VERSION
file). The package has no payload of its own - postinstall does the whole
install (see it) - and like the Windows installer nothing large is bundled:
Python and the packages are downloaded only when the Mac is missing them.

--keep-zip wraps the buddy.zip that's already here instead of building it
again: the release workflow builds it once, and both installers and the
update manifests are made from that one file.

Unsigned: macOS blocks it the first time it's opened, until the user clicks
"Open Anyway" in System Settings > Privacy & Security.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAC = ROOT / "installer" / "mac"
BUILD = ROOT / "build" / "mac"
IDENTIFIER = "com.buddy.installer"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--keep-zip", action="store_true",
                        help="wrap the buddy.zip that's already here instead of building it again")
    args = parser.parse_args()
    if sys.platform != "darwin":
        sys.exit("The macOS installer builds on macOS only (pkgbuild/productbuild).")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    print(f"Buddy {version}")

    if args.keep_zip:
        if not (ROOT / "buddy.zip").is_file():
            sys.exit("--keep-zip: there's no buddy.zip here. Build it first (python3 build_buddy_zip.py).")
    else:
        subprocess.run([sys.executable, str(ROOT / "build_buddy_zip.py")], check=True, cwd=ROOT)

    shutil.rmtree(BUILD, ignore_errors=True)
    scripts = BUILD / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(MAC / "postinstall", scripts / "postinstall")
    (scripts / "postinstall").chmod(0o755)
    shutil.copy2(ROOT / "buddy.zip", scripts / "buddy.zip")
    shutil.copy2(ROOT / "Buddy.py", scripts / "Buddy.py")
    shutil.copy2(ROOT / "installer" / "requirements.txt", scripts / "requirements.txt")
    # Extended attributes (a download's quarantine flag, say) would ship as
    # ._ files. macOS's own com.apple.provenance can't be removed and still
    # does - harmless, postinstall only uses the four files by name.
    subprocess.run(["xattr", "-cr", str(scripts)], check=True)

    subprocess.run([
        "pkgbuild", "--nopayload", "--scripts", str(scripts),
        "--identifier", IDENTIFIER, "--version", version,
        str(BUILD / "Buddy-component.pkg"),
    ], check=True)

    distribution = BUILD / "distribution.xml"
    distribution.write_text(
        (MAC / "distribution.xml").read_text(encoding="utf-8").replace("@VERSION@", version),
        encoding="utf-8",
    )
    out = ROOT / "dist" / f"Buddy-{version}.pkg"
    out.parent.mkdir(exist_ok=True)
    subprocess.run([
        "productbuild", "--distribution", str(distribution),
        "--resources", str(MAC / "resources"), "--package-path", str(BUILD),
        str(out),
    ], check=True)
    print(f"Built {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
