#!/usr/bin/env python3
"""
A real Python interpreter for running Buddy's helper scripts in a child
process.

Launched from Resolve's Scripts menu, sys.executable isn't Python at all
but Resolve's script host (fuscript on macOS). It embeds the same Python
Buddy is running on, and it does run a .py file handed to it - but it
always exits 0 whatever the script returns, and prints its own banner to
stdout. Either one breaks a helper whose result is its exit code or its
output (core/resolve_probe_worker.py would report Resolve reachable even
with Resolve closed). So on a Mac helpers run on the interpreter the host
embeds, found under sys.exec_prefix - the same version, with the same
packages.
"""

import os
import sys


def standalone_python():
    """sys.executable when that's already a Python binary; otherwise the
    one the host embeds; sys.executable again if none can be found."""
    exe = sys.executable
    if os.path.basename(exe).lower().startswith("python"):
        return exe
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    for candidate in (
        os.path.join(sys.exec_prefix, "bin", f"python{version}"),   # macOS / Linux
        os.path.join(sys.exec_prefix, "bin", "python3"),
        os.path.join(sys.exec_prefix, "python.exe"),                # Windows
    ):
        if os.path.isfile(candidate):
            return candidate
    return exe
