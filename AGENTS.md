# Working on Buddy

For anyone changing this repo - AI assistants included. The README says what
Buddy is and how it's built; this is what to keep true while changing it.

## Nothing personal, nothing secret

This repo is public. Nothing in it - code, comments, tests, docs, commit
messages - may hold personal information or secrets.

- **The only personal name is the owner's, Trevor Siebe** (the licence and
  copyright lines). No other real person, client, company or project is named.
- **No email addresses, phone numbers, postal addresses, account names or chat
  handles.** Examples and tests use made-up ones: `tess@example.com`, people
  called Tess, Sam or Olive.
- **No API keys, tokens, passwords, private keys or signing keys** - not in
  tests, not in comments, not "temporarily". Tests use obvious fakes. A real
  key lives in the user's settings or on the server, never here.
- **No real paths or machines:** no `C:\Users\<name>` or `/Users/<name>`, no
  drive and folder layout copied from someone's computer, no computer names,
  no server addresses. Write `D:\Footage\A001_0001.MXF`, `/Users/you/Movies`,
  `chat.example.com`.
- **No real footage or project data:** clip names off a camera card, timeline
  or project names, transcripts of real recordings, screenshots of real work.
  Make the fixture up, in the same shape.
- **Commits** are made with the GitHub account's `noreply` address, never a
  personal one. Check `git log --format='%an %ae %cn %ce'` before pushing.

What is meant to be public and stays: the owner's name, the chat server's
address (`chat.trevorsiebe.com`), this repo's address, and the authors named
inside the bundled fonts' own licence files and `THIRD_PARTY_NOTICES.md`.

If you find something that breaks these rules, take it out and say so.

## One codebase, two platforms

The same files run on Windows and on macOS, and one `buddy.zip` ships to both.
A change is made once and has to be right on each.

- **Python:** `sys.platform == "darwin"` / `sys.platform == "win32"`. A module
  only one platform has or can import (`winreg`, `core/crash_log.py`,
  `core/gpu_adapter.py`, `core/web_flags.py`; AppKit,
  `pages/time_tracker/mac_hotkey.py`,
  `pages/color_palette/mac_dropper_worker.py`) is imported under that test or
  inside the function that uses it, never at the top of a shared module.
- **Pages** (`*.html`, `*.js`) are written once, with key names the Windows
  way: "Ctrl+Z", "Alt". On a Mac `app/web/buddy.js` respells them as the page
  loads ("⌘Z", "Option"); text a script writes later goes through
  `Buddy.keys(...)`. For wording that isn't a key's name there is
  `Buddy.IS_MAC`, the `html.mac` class in CSS, and `data-mac-text`,
  `data-mac-title`, `data-mac-placeholder` in HTML.
- **Text shown to the user** that differs by platform ("the tray" / "the menu
  bar", "the Recycle Bin" / "the Trash") is two whole strings chosen by
  platform, and each needs its translations in `app/core/translations/`.
- **What Ask Buddy is told about the tools** (`app/core/tools_kb.json`) says
  each side in place: `[[win:...]]` and `[[mac:...]]`.
- **A tool not yet checked on a Mac** is a placeholder there: `mac=True` in
  `app/registry.py` turns it on.

## Tests

`python tests/run_suite.py` runs everything, in a throwaway `BUDDY_HOME`. Set
`QT_QPA_PLATFORM=offscreen` so no windows open. GitHub runs the suite on
Windows and on macOS for every push (`.github/workflows/tests.yml`); a test
that can only pass on one platform says so with `unittest.skipUnless`.
`tests/test_packaging.py` fails if Buddy imports a package that isn't in
`installer/requirements.txt`.

## Releasing

Changing `VERSION` on `main` publishes a release with both installers, once
the tests pass on both platforms (`.github/workflows/release.yml`). Push the
changes first, then the version bump as its own commit.
