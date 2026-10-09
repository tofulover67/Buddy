"""Opens every tool page - every tab, and Color Palette's generators - at a
range of window sizes, plus the shell's header and rail (with dual view off
and on) and the Settings window with each tool's section, and reports any
UI that overlaps other UI: a box whose content spills over its neighbour,
or a page that scrolls sideways.

Run by test_layout_overlaps.py in a process of its own, with HOME pointed
at a throwaway folder before anything is imported - some modules work out
their data folders at import time, so only a fresh process keeps every
page off the real settings and data. macOS only: Windows' home folder isn't
HOME, and the check below stops it there. Prints one JSON object:
{"problems": [[where, what, over, sizes], ...], "checked": [...]}.

    python layout_sweep.py <theme> [<theme> ...]
"""

import json
import os
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="buddy-layout-")
os.environ["HOME"] = HOME
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _paths  # noqa: E402,F401

from unittest import mock  # noqa: E402

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QTimer  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
assert os.path.expanduser("~") == HOME, "must never run against the real home folder"

TOOL_SIZES = ((1300, 900), (1000, 700), (800, 560), (1300, 460), (620, 700))
SHELL_SIZES = ((1400, 900), (1100, 700), (900, 600), (760, 520))
SETTINGS_SIZES = ((560, 720), (480, 460))

# Every pair of siblings where one's painted extent (its box, plus whatever
# of its content spills out, unless it clips) overlaps the other's box by
# more than 8px each way. Ignores what isn't drawn: display none, hidden,
# zero-sized, out of flow (overlays position themselves on purpose), the
# inside of an SVG, and a closed <details>' content (laid out, not drawn) -
# and runs of text within a sentence (display: inline).
FIND = r"""(() => {
  const collapsed = n => { const d = n.parentElement && n.parentElement.closest('details:not([open])');
    return !!d && !(n.closest('summary') && n.closest('summary').parentElement === d); };
  const vis = n => { const cs = getComputedStyle(n); const r = n.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0 && !collapsed(n); };
  const clips = n => { const cs = getComputedStyle(n); return cs.overflowX !== 'visible' || cs.overflowY !== 'visible'; };
  // Real boxes only: an inline element wrapping over two lines reports the
  // rectangle around both, which "overlaps" the words next to it.
  const inflow = n => { const cs = getComputedStyle(n); return !/absolute|fixed/.test(cs.position) && cs.display !== 'inline'; };
  const cache = new Map();
  function extent(n) {
    if (cache.has(n)) return cache.get(n);
    const r = n.getBoundingClientRect(); let e = {l: r.left, t: r.top, r: r.right, b: r.bottom};
    if (!clips(n)) for (const c of n.children) { if (!vis(c) || getComputedStyle(c).position === 'fixed') continue;
      const x = extent(c); e = {l: Math.min(e.l, x.l), t: Math.min(e.t, x.t), r: Math.max(e.r, x.r), b: Math.max(e.b, x.b)}; }
    cache.set(n, e); return e;
  }
  const name = n => n.tagName.toLowerCase() + (n.id ? '#' + n.id : '') +
    (typeof n.className === 'string' && n.className.trim() ? '.' + n.className.trim().split(/\s+/).join('.') : '');
  const out = [];
  for (const p of document.querySelectorAll('body, body *')) {
    if (!vis(p) || p.closest('svg')) continue;
    const kids = [...p.children].filter(c => vis(c) && inflow(c));
    for (let i = 0; i < kids.length; i++) for (let j = 0; j < kids.length; j++) {
      if (i === j) continue;
      const a = extent(kids[i]), b = kids[j].getBoundingClientRect();
      const w = Math.min(a.r, b.right) - Math.max(a.l, b.left), h = Math.min(a.b, b.bottom) - Math.max(a.t, b.top);
      if (w > 8 && h > 8) out.push([name(kids[i]), name(kids[j])]);
    }
  }
  const root = document.querySelector('.page') || document.documentElement;
  return {overlaps: out.slice(0, 20), sideways: root.scrollWidth - root.clientWidth,
          elements: document.querySelectorAll('body *').length};
})()"""

TAB_BUTTONS = "[...document.querySelectorAll('[role=tab]')].filter(n => n.offsetParent)"


class Settings(dict):
    def save(self):
        pass


class Host:
    """What a page asks of the shell: never connected to Resolve."""

    def __init__(self, theme):
        self.theme = theme
        self.controller, self.connected = None, False
        self.shared_settings = Settings(theme=theme)
        self.tools = {}

    def tool_settings(self, tool_id, defaults=None):
        return self.tools.setdefault(tool_id, Settings(defaults or {}))

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens(self.theme)

    def ensure_connected(self):
        raise RuntimeError("no Resolve in the layout sweep")

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def js(view, expression):
    out, loop = [], QEventLoop()
    view.page().runJavaScript(f"JSON.stringify({expression})", 0, lambda value: (out.append(value), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    return json.loads(out[0]) if out and out[0] else None


def wait_ready(surface):
    for _ in range(200):
        wait(50)
        if getattr(surface, "_ready", False):
            break
    wait(200)


class Report:
    def __init__(self):
        self.problems, self.checked = {}, []

    def check(self, where, view, size):
        result = js(view, FIND)
        if result and result["elements"] and (result["overlaps"] or result["sideways"] > 1):
            # GitHub's Mac can still be showing the last size's layout (0.5.0
            # failed that way, once): a real problem is still there a moment later.
            wait(600)
            result = js(view, FIND)
        if not result or not result["elements"]:
            self.problems.setdefault((where, "NOTHING RENDERED", ""), []).append(size)
            return
        self.checked.append(f"{where} {size}")
        for a, b in result["overlaps"]:
            self.problems.setdefault((where, a, b), []).append(size)
        if result["sideways"] > 1:
            self.problems.setdefault((where, "SCROLLS SIDEWAYS", f"{result['sideways']}px"), []).append(size)


def sweep_tools(theme, report):
    import registry

    import test_network_page as network_fakes
    from pages.buddy_network import page as network_page

    mock.patch.object(network_page, "NetworkClient", network_fakes.FakeClient).start()
    for _category, cls in registry.REGISTRY:
        if getattr(cls, "is_placeholder", False) or not hasattr(cls, "web_dir"):
            continue
        host = network_fakes.Host() if cls.tool_id == "buddy_network" else Host(theme)
        if cls.tool_id == "buddy_network":
            host.shared_settings = Settings(theme=theme)
            host.theme_tokens = Host(theme).theme_tokens
        page = cls(host)
        page.resize(*TOOL_SIZES[0])
        page.show()
        wait_ready(page)
        states = [(tab, None) for tab in (js(page.view, f"{TAB_BUTTONS}.map(n => n.textContent.trim())") or [""])]
        if cls.tool_id == "color_palette":
            from pages.color_palette import generators
            states += [("Generators", gid) for gid in generators.IDS]
        for tab, generator in states:
            if generator:
                page.on_tab({"tab": "generators"})
                page.on_generator({"id": generator})
                label = f"Generators/{generator}"
            elif tab:
                js(page.view, f"{TAB_BUTTONS}.find(n => n.textContent.trim() === {json.dumps(tab)}).click()")
                label = tab
            else:
                label = ""
            wait(250)
            for size in TOOL_SIZES:
                page.resize(*size)
                wait(120)
                report.check(f"{cls.tool_id}[{label}]" if label else cls.tool_id, page.view, f"{size[0]}x{size[1]}")
        page.hide()
        page.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def sweep_shell(theme, report):
    import registry
    import core.shell_window as shell_window
    from core.settings_dialog import SettingsDialog

    for patch in (mock.patch.object(shell_window, "resolve_connect", side_effect=RuntimeError("no Resolve")),
                  mock.patch.object(shell_window.AnnouncementChecker, "start", lambda self: None),
                  mock.patch.object(shell_window.QSystemTrayIcon, "isSystemTrayAvailable", return_value=False)):
        patch.start()
    win = shell_window.ShellWindow(app, registry.REGISTRY)
    win.shared_settings["theme"] = theme
    win.apply_theme()
    win.resize(*SHELL_SIZES[0])
    win.show()
    # A slow Mac (the release build's runner) can take longer than any fixed
    # pause to load these pages, and a view checked before then is blank.
    for chrome in ((win.taskbar,) if win._layout == "desktop" else (win.header, win.rail)):
        wait_ready(chrome)
    for dual in (False, True):
        if win._layout == "desktop" and dual:
            continue
        if win._layout != "desktop":
            win.header.on_split({"on": dual})
        wait(250)
        for size in SHELL_SIZES:
            win.resize(*size)
            wait(200)
            tag = f"{size[0]}x{size[1]}"
            if win._layout == "desktop":
                report.check("taskbar", win.taskbar.view, tag)
            else:
                suffix = " (dual view)" if dual else ""
                report.check("header" + suffix, win.header.view, tag)
                report.check("rail" + suffix, win.rail.view, tag)
    if win._layout != "desktop":
        win.header.on_split({"on": False})
    for tool_id, page in win.pages.items():
        if getattr(page, "is_placeholder", False):
            continue
        dialog = SettingsDialog(win, win.shared_settings, lambda: None, page)
        dialog.show()
        wait_ready(dialog)
        for size in SETTINGS_SIZES:
            dialog.resize(*size)
            wait(120)
            report.check(f"settings[{tool_id}]", dialog.view, f"{size[0]}x{size[1]}")
        dialog.hide()
        dialog.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    win.hide()
    win.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def main(themes):
    report = Report()
    for theme in themes:
        tools, shell = Report(), Report()
        sweep_tools(theme, tools)
        sweep_shell(theme, shell)
        for part in (tools, shell):
            for (where, what, over), sizes in part.problems.items():
                report.problems.setdefault((f"{theme}: {where}", what, over), []).extend(sizes)
            report.checked += [f"{theme}: {c}" for c in part.checked]
    print(json.dumps({"problems": [[w, a, b, s] for (w, a, b), s in sorted(report.problems.items())],
                      "checked": report.checked}))


if __name__ == "__main__":
    main(sys.argv[1:] or ["Resolve"])
