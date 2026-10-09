"""No UI overlaps other UI, anywhere, at any window size: every tool (every
tab, and Color Palette's generators), the shell's header and rail with dual
view off and on, and the Settings window with each tool's section - resized
from roomy down to small, in every theme (each has its own fonts and
spacing). An overlap is a box whose content spills over its neighbour, or a
page that scrolls sideways; tests/layout_sweep.py finds them.

The sweep runs in a process of its own with HOME pointed at a throwaway
folder, so no page ever touches the real settings or data (see there).

macOS only. On Windows the home folder isn't HOME, and the real tools reach
past it - the registry, %LOCALAPPDATA%, system-wide hot keys - where a
throwaway folder protects nothing."""

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import PySide6.QtWebEngineWidgets  # noqa: F401
    HAVE_WEB = True
except ImportError:  # pragma: no cover
    HAVE_WEB = False

THEMES = ("Resolve", "Default", "Retro", "SaaS", "Nova", "Offworld", "Desktop")
# Themes swept side by side, one process per group: a sweep mostly waits for
# pages to load and lay out, so three at once take about as long as one.
GROUPS = (THEMES[0::3], THEMES[1::3], THEMES[2::3])


@unittest.skipUnless(sys.platform == "darwin", "macOS only")
@unittest.skipUnless(HAVE_WEB, "PySide6 with QtWebEngine not installed")
class LayoutOverlapTests(unittest.TestCase):
    def test_nothing_overlaps_at_any_size_in_any_theme(self):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        runs = [subprocess.Popen([sys.executable, os.path.join(HERE, "layout_sweep.py"), *group],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=HERE)
                for group in GROUPS]
        problems, checked = [], []
        for group, run in zip(GROUPS, runs):
            out, err = run.communicate(timeout=1800)
            lines = [line for line in out.splitlines() if line.startswith("{")]
            self.assertTrue(lines, f"the sweep of {', '.join(group)} didn't finish:\n{err[-3000:]}")
            result = json.loads(lines[-1])
            problems += result["problems"]
            checked += result["checked"]
        self.assertGreater(len(checked), 1000, "the sweep looked at almost nothing")
        report = "\n".join(f"  {where}: {what} over {over}  at {', '.join(sizes)}"
                           for where, what, over, sizes in problems)
        self.assertFalse(problems, f"UI overlaps:\n{report}")


if __name__ == "__main__":
    unittest.main()
