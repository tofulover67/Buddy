# Buddy

A single PySide6 desktop app that hosts a set of DaVinci Resolve tools, plus a
chat agent that answers questions about Resolve from the official reference
manual and can read — and, if you let it, change — your open project. It runs
on Windows and macOS.

Buddy lives in Resolve's **Workspace > Scripts** menu as a single entry, in
place of a folder of one-tool scripts. Its screens are web pages (HTML/CSS/JS
in QtWebEngine) driven from Python.

## Status

Buddy's integrated tools include the following:

- Animation (formerly Text Animator; Previews puts motion presets on the clips selected in Resolve - stills, video, Text+ - with the In on each clip's first frame and the Out on its last, and an Editor for presets of your own. 112 presets in packs - Essentials, Text, Loops (repeating to each clip's end), Camera (moves over the whole clip), Classic and Magic - with a Direction to turn the ones that travel, and channels beyond position, scale and rotation: blur, squash and stretch, reveal masks (wipes, iris, split, blinds), letter spacing, trails, a light sweep, colour split and 3D flips. Animate by plays a preset line by line, word by word or letter by letter on a Text+, a stagger apart in the order chosen, through Fusion's text Follower (`pages/text_animator/units.py`). Titles puts a word highlight - a box, a colour change or a pop - on Text+ captions, timed from Transcribe's word timings, with a strip to retime words. Zoom plays the selected clip (or an adjustment clip and everything under it) in Buddy with a track of zooms to edit - 13 kinds, from punch-ins to crash, impact and follow-pan zooms - copied between clips, previewed with the clip's grade, and put on as a Fusion Transform (`pages/text_animator/zoom.py`). Transitions puts Buddy's own Fusion transitions on the cuts between selected clips - motion, light and blur, wipes, glitch, paper and grunge, distortions, split screen and 3D - with a feel, length, amount, blur and colour (`pages/text_animator/transition.py`). Font styling and the older Text+ tools are on Subtitles)
- Ask Buddy (chat agent: questions about Resolve answered from the Reference Manual, with citations. Can read your project; can change it only with your consent. See Ask Buddy below)
- Audio Assistant (new; an easier alternative to the Fairlight page for a clip's audio. Its Timeline tab mirrors the audio tracks with each clip's waveform, and sets clip volume, pan, fades, loudness to a target, Voice Isolation and the Dialogue Leveler, and a volume curve drawn on the clip that becomes Resolve keyframes. Buddy's own effects - rumble, hum, noise, de-esser, clarity, gate - heard before and after, then baked into a WAV beside the media; ducking under speech and riding a voice as editable curves; beat markers; the whole mix to a loudness target under a true-peak ceiling; an automixer for podcasts and interviews (each mic up only while its person talks, opening just before they start); and Fit to length, which ends a song on its own ending at the length wanted by cutting or repeating whole bars where the edit can't be heard)
- Audit (new; checks the open timeline before delivery - missing media and fonts, black gaps, flash frames, things turned off, unfinished titles, to-do markers, hard-to-read subtitles, frame rates, enlarged pictures - with one-click fixes, and Check sound: the mix's clipping, true peaks and loudness against a delivery target, and each clip's recording for clipping, dropouts, one-sided stereo, phase and pops)
- Automation (new; named lists of steps from Buddy's tools that run one after another while you do something else - Create bins, Import a folder, Make proxies, Put the bin on a timeline, Sync, Sync external audio, Collapse and Shift from Project Setup, Transcribe the timeline from Subtitles, Check the timeline from Audit, and Save a version / Remove gaps from Command Center. Each step keeps its options; some - the folder to import, say - can be asked when it runs instead. A failed step stops the run and says why, with Resume from that step, and a notification says when it's finished. See `pages/automation/` and each tool's `automation.py`)
- Asset Manager (a library of the images, audio and video you reuse, with per-project lists)
- Buddy Network (an anonymous chat between Buddy users; see Buddy Network below)
- Color Palette Manager (palettes, generators, colours from images and contrast checks. Never touches your project)
- Command Center (new; system-wide hot keys - or a Run button - for markers, jumping between them, copying the timecode or the frame, clip colours, Animation presets, saving a timeline version and removing gaps (experimental). Hot keys are Windows-only for now (`core/hotkeys.py`): a Mac has the Run buttons, and hot keys there need a version of their own (macOS asks for permission); see `pages/command_center/actions.py`)
- Essentials (new; a calculator that also does timecode, data rate, aspect ratio, a countdown timer, notes kept per project and a world clock; see `pages/essentials/`)
- Dailies (named source tapes, continuous Media Pool review, notes and metadata. Windows only for now - on a Mac it's a placeholder until it's been checked there)
- Games (new; Pong, Snake, Minesweeper, Solitaire, Falling blocks, 2048, Breakout, Sudoku, Checkers and Chess - Checkers and Chess against the computer or a friend; each pauses itself when you look away and keeps its best scores. Never touches your project; see `pages/games/`)
- Generative (new; takes things out of stills on this computer, nothing uploaded. Remove: paint over what shouldn't be there and it's filled with what would be behind it, or remove the background - the subject is found by itself and saved on transparency, its edge fixed with Add and Subtract brushes. Separate: the subject and a clean background with it taken out, as two layers. Brush size and feather, Alt + scroll wheel for the size; stills open from a file or from Resolve, and can be swapped back into their Media Pool clip. See Generative below and `pages/generative/`)
- Image Importer (images into a bin straight from the clipboard: a copied image, copied files, or an image URL)
- Media Manager (two tabs: Batch Clip Renamer renames a bin's clips, or just the selected ones, by sequential numbering or find & replace; Media Relink relinks offline clips, and relocates media that's still online)
- Project Setup (a bin structure from a list, folder-tree import, bins to timelines, multicam sync, and proxy media rendered with ffmpeg - H.264, H.265, ProRes, DNxHR or CineForm - and linked in Resolve)
- Marker Manager (two tabs: Stills Exporter places markers, grabs stills from them on the Color page, and exports them as image files; YouTube Chapters makes a YouTube chapter list from the timeline's markers)
- SVG Importer (SVG artwork and Lottie animations as Fusion nodes, copied for pasting into the Fusion page)
- Time Tracker (tracks time against whichever project is open, with idle detection)
- Subtitles (formerly Transcribe; subtitles from the timeline's dialogue, and translations, run locally - Transcribe, Translate, Subtitle Conversion and the Text+ styling, layout, word-by-word and animation tabs included)
- Web (new; a light browser - tabs (drag to reorder), private tabs (Ctrl+Shift+N), YouTube quality, search, new-tab shortcuts, a pause button on a tab that plays sound, Google sign-in, sign-ins saved encrypted with the Windows account (`pages/web/cookie_vault.py`), a Downloads window (searchable, filter by kind, size and date) whose files drag into Resolve and land in a Downloads bin of the Media Pool, ad and tracker blocking with EasyList, EasyPrivacy and uBlock Origin's filter lists (`pages/web/filters.py`). Background tabs sleep after a while, except ones playing sound, kept awake or on a never-sleep site. Its sound ducks while Resolve plays (Windows; `core/audio_sessions.py`), and a speaker beside "Web" in the sidebar shows when it plays. Built on the Chromium Buddy already ships, which has no H.264/AAC decoder, so a video that fails there is offered to Buddy's own player (`pages/web/video_window.py`, Qt Multimedia's FFmpeg); DRM video still can't play. Windows only for now - on a Mac it's a placeholder until it's been run there; see `pages/web/`)

The sidebar can be reordered and tools hidden from Settings.

The bug button in the header (and on the desktop layout's taskbar) sends a
bug report - what went wrong, up to six screenshots, and Buddy's, the
system's and Resolve's versions - to the Buddy Network server, with Buddy
Network on or off. The owner reads them in Buddy Network's Admin panel
(Bugs tab). See `core/bug_report.py` and `server/bugs.py`.

## On a Mac

Buddy is the same app on Windows and macOS, from the same `buddy.zip`. What
differs on a Mac:

- **Not there yet.** Dailies and Web are placeholders, Command Center has its
  Run buttons but no system-wide hot keys, and Generative has no High quality
  add-on (it needs an NVIDIA graphics card). Subtitles and Generative run on
  the CPU there; their sections say what hasn't been tested on a Mac yet.
- **Windows only.** "Start Buddy automatically when Resolve starts", and the
  installer's Start menu and desktop shortcuts.
- **The menu bar.** Where this file says the system tray, a Mac has Buddy's
  icon in the menu bar.
- **Keys.** Shortcuts are written the Windows way here. On a Mac, Buddy shows
  ⌘ for Ctrl and Option for Alt.
- **Where things are.** Buddy's own files are in `~/.buddy` on both. Resolve's
  Scripts folder, where `Buddy.py` and `buddy.zip` go, is
  `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility`
  on a Mac and
  `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility`
  on Windows.
- **Smaller things.** An update quits Buddy instead of restarting it, a backup
  holds your AI providers' API keys as typed, and a manual citation opens in a
  browser where it can - each is covered in its section below.

## Background behavior (Time Tracker)

Time Tracker's port made Buddy itself a background app, not just a one-shot
action tool:

- **System tray.** Closing the window's `[X]` minimizes Buddy to the tray
  (the menu bar on a Mac) instead of quitting — tracking keeps running.
  Right-click the tray icon (on a Mac, click the menu bar icon) to reopen or
  quit for real. Turn this off in Settings > Window if you'd rather the `[X]`
  just quit, like every other tool here.
- **Single instance.** Relaunching from Resolve's Scripts menu while Buddy is
  already running (e.g. minimized to tray) just raises the existing window
  instead of starting a second one polling Resolve in parallel.
- **Start Buddy automatically when Resolve starts** (opt-in, Settings >
  Window; Windows only for now): a small background watcher waits for
  Resolve's process to appear, then launches Buddy straight into the tray so
  tracking is already running by the time you're in a project.

These three are shell-level (`core/shell_window.py`,
`core/single_instance.py`, `core/startup_manager.py` +
`core/resolve_watcher.py`), not specific to Time Tracker — any future
background tool gets them for free.

## Installing (for users)

Download the installer for your computer from the
[Releases](https://github.com/tofulover67/Buddy/releases) page:
`BuddySetup-<version>.exe` for Windows, `Buddy-<version>.pkg` for macOS.
Either one puts Buddy in DaVinci Resolve's **Workspace > Scripts** menu, and
installs what Buddy runs on only if the computer is missing it: Python 3.13
from python.org (checksum-verified) and Buddy's Python packages, downloaded
while installing (up to ~330 MB, so it needs internet). Then open Resolve and
pick **Workspace > Scripts > Buddy**.

**Windows.** Double-click `BuddySetup-<version>.exe` and click through. No
admin rights needed.

It can also add a Start menu entry (on by default) and a desktop shortcut
(off by default) that open the same Buddy without going through Resolve.
Either way it's one app: opening it a second way brings up the copy already
running.

Windows may show "Windows protected your PC" because the installer isn't
code-signed: **More info > Run anyway**. Uninstall from Settings > Apps; it
removes Buddy and leaves Python, its packages and your `~/.buddy` settings.

**macOS.** Open `Buddy-<version>.pkg` and click through. The Python it
looks for, and installs if the Mac has none at 3.10 or newer, is python.org's:
the one Resolve runs scripts with. The .pkg is unsigned, so macOS blocks it
the first time it's opened: allow it with **Open Anyway** in System Settings >
Privacy & Security. The install log is `/Library/Logs/Buddy-install.log`.
There's no uninstaller: [manual_install.txt](manual_install.txt) says what to
delete.

**Which Resolve works which way:**

| Resolve | Workspace > Scripts | Start menu / desktop shortcut (Windows) |
| --- | --- | --- |
| Studio | Yes | Yes |
| Free, up to 21.0.4 | Yes | Opens, but can't reach Resolve |
| Free, 21.1 and later | No | Opens, but can't reach Resolve |

- Only **DaVinci Resolve Studio** lets Buddy connect from outside Resolve
  (a shortcut, or "Start Buddy automatically when Resolve starts" - both
  Windows only). Turn on
  **Preferences > System > General > "External scripting using" > Local**.
- The free version runs Buddy only from **Workspace > Scripts**, and only up
  to **21.0.4**. From **21.1** on, only Studio supports Python scripts.
- Where Buddy can't reach Resolve, it still opens, and the tools that don't
  use Resolve still work. The ones that do say they can't reach it.
- In the free version, open Buddy from Workspace > Scripts first. If a copy
  started from a shortcut is already running, the menu only brings that one
  up, so quit it from the system tray first.

Rather not run the installer? [manual_install.txt](manual_install.txt) walks
through the same steps by hand, on Windows or a Mac - every feature, Buddy
Network included.

## Updates

Once installed, Buddy updates itself. Once a day it looks at the latest GitHub
release, and when there's a newer one an **Update** button appears in the
header: it shows what changed, downloads `buddy.zip` (and `Buddy.py`, if that
changed) from the release, checks each is exactly the size and SHA-256 the
release's manifest says (`buddy-update-windows.json` or
`buddy-update-mac.json`), keeps the old one, and restarts Buddy.
On a Mac it quits instead, to be reopened from Workspace > Scripts - a Buddy
started any other way can't connect to the free Resolve.
Settings > Updates turns the daily check off, checks now, or rolls
back to the version the last update replaced. A release that needs a package
Buddy doesn't have yet sends you to its installer instead. Updates aren't
signed: they're trusted as far as GitHub and the account publishing the
releases are (see `app/core/updater.py`, which says where a signature would go).

## Backup, and where Buddy keeps things

Settings > General > Backup writes one .zip of Buddy's settings and what's been
made in it - every tool's settings, Ask Buddy's chats and instructions,
transcripts and subtitles, palettes, time entries, Web's bookmarks and history.
Models and AI engines (downloaded again), web sign-ins and browsing data, and
the Buddy Network account (Account > Move to another PC moves it) are left out.
AI providers' API keys go in as Buddy keeps them: on Windows locked to the
Windows account, so on another PC they're typed in again; on a Mac as typed,
so keep the file private.
Restore puts a backup back the next time Buddy starts (on a Mac, quit and
reopen it from Workspace > Scripts), keeping what was there as
`before-restore.zip`. Automatic backups (off until turned on) go to a folder of
your choosing every day or week, keeping the newest few. See `core/backup.py`.

Settings > AI > Model library > Where models are kept moves a tool's models
(Subtitles, Generative, Ask Buddy) to a folder of your choosing - a bigger
drive, or one other apps share - and offers to move the ones already there;
only Buddy's own model files are ever moved or removed (`core/model_folders.py`).
The ⓘ in the Settings window's corner lists every folder and file Buddy keeps,
with its size and an Open button (`core/buddy_folders.py`).

Every tool's settings and data live in `~/.buddy/tools/<name>/` (`core/tool_dirs.py`).
Older Buddys kept each tool's folder in the home directory itself -
`~/.command_center`, `~/.davinci_time_tracker`, `~/.color_palette_manager` -
and the first start of a newer Buddy moves each one into `~/.buddy/tools`,
under the name it had. A folder that can't be moved is read from where it is,
and the ⓘ lists it as an older place. Older backups restore into the new place.

`BUDDY_HOME=<folder>` in the environment makes Buddy keep everything under that
folder instead of the home directory - `~/.buddy`, the tools' folders and
automatic backups - so a build can be tried, or the tests run, without touching
your own settings. `tests/run_suite.py` sets it to a temporary folder by itself.

## Building the installers

```bash
python build_installer.py        # Windows: dist/BuddySetup-<version>.exe
python3 build_mac_installer.py   # macOS:   dist/Buddy-<version>.pkg
```

Each builds `buddy.zip` and wraps it with the `Buddy.py` launcher, using the
version in `VERSION` (`--keep-zip` wraps the `buddy.zip` that's already there
instead). The Windows one needs Inno Setup 6 or 7 and writes a ~3 MB .exe.
The macOS one builds on a Mac only, with `pkgbuild` and `productbuild`, which
every macOS has.

## Releasing

Change the number in `VERSION` on `main` and commit - GitHub's web editor is
fine. GitHub Actions (`.github/workflows/release.yml`) then runs the tests on
Windows and on a Mac (`tests.yml`, which also runs on every push and pull
request), builds `buddy.zip` once and both installers from it - the Windows
one on a clean Windows machine, the macOS one on a clean Mac - and publishes
release `v<version>` with `BuddySetup-<version>.exe` and `Buddy-<version>.pkg`
attached - and `buddy.zip`, `Buddy.py` and the two update manifests,
`buddy-update-windows.json` and `buddy-update-mac.json`
(`build_update_manifest.py`), which running Buddys update from. Pushes that
don't change `VERSION` release nothing, and an existing version is never
overwritten. It packages what is on `main`, so push changes before bumping.
The installer scripts are `installer/Buddy.iss` (Windows) and
`installer/mac/postinstall` (macOS); the packages they install are
`installer/requirements.txt` (a test fails if Buddy imports a package that
isn't listed there). The lower bounds in that file are security floors - the
installer upgrades anything older, and an update that needs more than a
computer has sends it to the installer.
`python tools/check_dependencies.py` checks them against PyPI's advisory data
(the release workflow runs it as a warning); raise a floor when it reports one.
The workflow builds with a read-only token and publishes from a separate job,
and every action it uses is pinned to a commit.

## Requirements

- Windows 10 or 11, or macOS
- DaVinci Resolve — only needed for the tools that talk to it. Studio works
  from Workspace > Scripts and from outside Resolve; the free version only
  from Workspace > Scripts, and only up to 21.0.4 (see
  [Installing](#installing-for-users))
- Python 3.10+ - the one Resolve runs scripts with: on Windows the one the
  `py` launcher finds, on a Mac one from
  [python.org](https://www.python.org/downloads/)
- PySide6 (including QtWebEngine and QtMultimedia), plus Pillow, NumPy,
  openpyxl, pynput, PyMuPDF and cryptography for some tools

```bash
pip install -r installer/requirements.txt
```

On a Mac: `python3 -m pip install -r installer/requirements.txt`.

## Running it

For development, straight from the source tree:

```bash
python app/main.py
```

(`python3` on a Mac, here and in the commands below.) The tools that talk to
Resolve need Buddy launched from Resolve's Scripts menu, unless it's Resolve
Studio: the free Resolve only accepts the connection it hands to a script it
started.

## Deploying to Resolve

```bash
python build_buddy_zip.py --deploy
```

That builds `buddy.zip` and copies it, with the `Buddy.py` launcher stub, into
Resolve's Scripts folder. On Windows:

```
%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility
```

On a Mac:

```
~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility
```

Restart Resolve (on a Mac, quit it and reopen it) and **Buddy** appears under
Workspace > Scripts. Re-run the command after any code change — Resolve loads
the zip, not the source tree. On a Mac, double-clicking
`Deploy to Resolve.command` in Finder installs the packages and then runs the
same command.

PySide6 has to be installed for the interpreter *Resolve* uses, which is not
always the one on your PATH. If it isn't, the launcher says so and names the
exact interpreter. On a Mac it can name Resolve's own script runner instead of
a Python: the packages go in python.org's Python there
([manual_install.txt](manual_install.txt), step 2).

## Subtitles

Turns the current timeline's dialogue into subtitles, and translates them, locally - nothing is
sent to the internet (unless you choose AI translation with a cloud model). Transcribe Timeline renders the timeline's audio mix,
runs Whisper (faster-whisper) on it, builds readable subtitles from the word
timings (42 characters a line, two lines, breaking at sentences and
clauses), and puts them on subtitle track 1 as well as saving an SRT in
`~/.buddy/transcribe/output/`. Its Subtitle Conversion tab turns them into
Text+ clips, and the tabs after it style, place and animate them.

Font styling starts from a look - ready-made ones for wide and for vertical
(9:16) timelines - then Text, Outline, Shadow and Box tabs to adjust it, each
switched on or off, over a stand-in picture. A look you make can be saved by
name and picked again in any project (Saved looks). The page follows the
timeline's shape: a vertical one gets a tall preview, with the buttons of
Instagram, TikTok or YouTube Shorts shaded over it.

- **One-time setup** (Subtitles > Setup): installs the engine into its
  own environment in `~/.buddy/transcribe/`, built from the Python Buddy runs
  on. About 1.45 GB with an NVIDIA GPU (mostly CUDA libraries), about 110 MB
  without. Then a model - downloaded, or reused from a folder another app
  already has:
  - Whisper Large v3 (3.1 GB), Large v3 Turbo (1.6 GB), Small (0.48 GB):
    every language.
  - Distil-Whisper Large v3.5 (1.51 GB, MIT): English only, about three
    times faster than Large v3 on a GPU.
  - NVIDIA Parakeet v3 (0.67 GB, CC-BY 4.0): 25 European languages, runs on
    the CPU at ~35x real time, punctuates by itself (English "uh"/"um" are
    dropped). No hotwords.
  - **Auto** (with Parakeet and a Whisper model installed): detects the
    language, then uses Parakeet where it can and Whisper everywhere else.
- **Hardware**: runs on the GPU with an NVIDIA card, and on the CPU on any
  other Windows PC or Mac. The CPU path is tested on Windows; Macs are not
  tested yet.
- **Your project**: Resolve's API has no way to read render settings, so they
  are saved as a temporary preset before the audio render and loaded back
  after; the render job is removed. Resolve only lets scripts place subtitles
  on subtitle track 1, so if that track already has subtitles Buddy asks
  before replacing them, or keeps just the SRT file.
- **Translate**: Translate Last Transcript (or Translate an SRT File...)
  writes one SRT per chosen language to a folder you pick - or
  automatically after each transcription. Three engines, picked under
  Translate > Model:
  - **NLLB-200** (Meta), locally, through the same engine: 200 languages.
    Get a model in Set up... (600M 0.62 GB, 1.3B 1.38 GB recommended,
    3.3B 3.36 GB). Licensed CC-BY-NC 4.0 - non-commercial use only.
  - **MADLAD-400 3B** (Google), locally: about 180 of those languages
    (2.95 GB, Nextcloud's CTranslate2 conversion). Licensed Apache 2.0, so
    fine for client and commercial work. Best with an NVIDIA GPU; on a CPU
    it's a few seconds per batch of sentences.
  - **AI translation**: the model Ask Buddy is set up with (Claude, Gemini,
    an OpenAI-compatible service, or a local Ollama). It translates 40
    sentences at a time with the lines before them, so names and tone stay
    consistent, and it's given the Names and terms field as a glossary.
    Needs no local engine. A cloud model gets the transcript's text under
    your own key (and may charge for it) - Buddy asks once per provider,
    and never sends it during an automatic translation until you have.
  Whole sentences are translated, then re-cut into timed subtitles; Chinese
  and Japanese are wrapped by character with native punctuation. Import the
  files onto other subtitle tracks yourself (File > Import > Subtitle) -
  scripts can only place subtitles on track 1.

## Generative

Changes pictures and footage with models that run on this computer -
nothing is uploaded. It starts by asking what you want to do; then open a
file (or press From Resolve: the still or clip under the playhead, or the one
selected in the Media Pool - or drag a clip off Resolve's timeline onto the
page; Resolve won't let Media Pool clips be dragged out). The original is
never changed. The tools sit on the picture, the panel beside it says what's
next, and one Export button holds every way out: Save, Save as, Replace in
Resolve, Add to the Media Pool. Footage comes back in its own colours: read
and written with the clip's colour matrix and range, the way Resolve shows
it, and labelled to match.

- **Remove an object**: paint over what shouldn't be there and press
  Remove: it's filled with what would be behind it. Margin takes the removal
  further past the paint. Export saves `<name>_clean` beside the original, at
  its bit depth.
- **Cut out the subject**: found by itself as the picture opens and shown
  cut out, with Keep and Take away brushes to fix its edge; hair and fur keep
  their own colours at the edge, not the old background's. Exported as
  `<name>_subject` on transparency.
- **Split into layers**: the subject, plus the picture with the subject
  taken out - two layers to stack in Resolve (`_subject`, `_background`).
- **Footage** works for all three, in numbered steps: paint it on a frame or
  two, follow it through the shot, run it, export.
- **One-time downloads**, each when a task is first picked, into
  `~/.buddy/generative/`: an engine (NumPy, OpenCV, ONNX Runtime, ~70 MB)
  built from the Python Buddy runs on, then the models - LaMa for removal
  (Samsung AI Center, Apache 2.0; OpenCV's 93 MB export) and BiRefNet for
  the subject (Zheng Peng et al., MIT; Standard 115 MB, or Best 490 MB with
  finer hair). Every file is pinned and checked against its published hash
  (`tools/pin_generative.py`).
- **Hardware**: everything runs on the CPU - about a second per removal and
  3-7 s to find a subject on a desktop CPU. Not yet tested on a Mac.
- **Quality: High** (the High quality add-on, NVIDIA RTX 30 or later with
  8 GB, driver 580+; it needs an NVIDIA graphics card, so it isn't offered on
  a Mac): removal that draws what's behind again instead of
  continuing what's round it. Pictures use FLUX.2 klein 4B (Black Forest
  Labs, Apache 2.0) with fal's object-removal LoRA (Apache 2.0), about 7 s;
  footage uses ROSE (Kunbyte, Apache 2.0, on Wan 2.1 Fun 1.3B), which
  redraws the whole part without the object in overlapping 49-frame runs,
  about a minute per two seconds of footage. Footage has two more switches:
  **Also its shadow and reflection** (on to start with) puts in what ROSE
  changed next to the object - its shadow on the ground, its reflection in
  water - and **Finer detail** runs ROSE at 720p instead of 480p, sharper on
  4K at about 5x the time. Where frames nearby show the real background, its
  texture goes over ROSE's softer one. A one-time download of about 13.7 GB (PyTorch and diffusers into
  the engine, pinned in `locks/hq-nvidia.txt`, and the two models, every
  file hash-checked). Both models are fed to the card a few blocks at a time,
  so they need 3-5 GB of graphics memory and run beside Resolve. The two
  prompts they run with are computed once and shipped in
  `pages/generative/data/`, so their 8 GB and 11 GB text encoders are never
  downloaded.

## Ask Buddy

The chat page answers Resolve questions from the DaVinci Resolve 21 Reference
Manual and cites chapter and page, so you can check it. Type `help` in the
chat for the current capability list — it is generated from the code, so it
cannot drift.

Conversations are saved locally in `~/.buddy/ask_buddy/conversations.json`.
Use **Chats** to search past messages or rename a conversation. Click a manual
citation to open the manual PDF at that page - the PDF the bundle was built
from (Buddy asks where it is once, for a bundle built before that was
recorded). On a Mac, Preview can't be sent to a page, so it opens in Chrome,
Edge or Brave if one is installed, or in Preview with the page to go to.
Image questions keep small preview thumbnails in saved chats.

**Model providers** (bring your own key, set in Settings): Google Gemini,
Anthropic Claude, OpenAI, OpenRouter, Groq, Azure OpenAI, any
OpenAI-compatible endpoint (LM Studio…), or Ollama running locally. Keys are
kept with Buddy's settings in your home folder: on Windows locked to the
Windows account (`core/secrets_store.py`), on a Mac as typed.

**Manual data.** Retrieval reads a bundle in `~/.buddy/manual/bundle/`. Buddy
runs fine without it and says so in the status line; answers are then
ungrounded and carry no citations.

To build or refresh it when a new manual comes out, download the Reference
Manual PDF, then either open **Settings > Ask Buddy > Rebuild from PDF...**,
drop the PDF in and press Build, or run:

```bash
python build_manual_bundle.py DavinciManual.pdf
```

A full manual takes about 8 minutes. The builder needs `pymupdf` and
`pymupdf4llm` (`pip install pymupdf pymupdf4llm`, for the interpreter Resolve
uses if you build from inside Buddy), and Ollama running with `embeddinggemma`
pulled for semantic search. Without Ollama it builds a keyword-only bundle
and says so. The previous bundle is kept as `bundle.previous`, and a stopped
or failed build leaves the current one untouched.

**Reading your project.** With Resolve running, it can read your project and
timeline settings, which edition you have, what is on the timeline, any clips
whose frame rate or resolution does not match it, and your markers. Mismatches
are computed in Python rather than left to the model. What it reads goes to
your AI provider with the question, so with a cloud provider it asks first
(once, for that provider's address; Settings > AI has the switch) and answers
from the manual alone if you say no - a server on your own computer or network
needs no permission.
The detected Free or Studio edition is included with every question while
Buddy is connected to Resolve; without a connection, the edition is marked
unknown. **Check project** scans the current timeline for frame-rate and
upscaling mismatches and missing local source paths, then suggests next
steps. **Explain clip** focuses on the selected timeline clip or the video
clip under the playhead before answering.

### Changing your project

Off by default, and behind two independent gates:

1. **Consent.** A checkbox in Settings, which opens a dialog explaining the
   risk and asks you to type a sentence out in full. Consent is never cached —
   unticking the box revokes it and re-ticking asks again.
2. **Per-action approval.** The model only ever *proposes*. Buddy reads your
   project, builds a card listing every item that would actually change, and
   waits for you to press Apply. Actions that move or destroy work you did not
   name are flagged, and say what they will do before you press anything.

Even with consent on, nothing reaches your project without that second click.

## Buddy Network

A text chat between Buddy users: public rooms, your own rooms, direct
messages (end-to-end encrypted) and a buddies list. Anonymous by design: no
email or sign-up, and the server keeps no IP addresses. Buddy connects to the
public server by default; `server/README.md` covers running your own.

## Layout

```
app/
  main.py            entry point
  core/              shell window, theming, settings, Resolve bridge,
                     tool knowledge base, write-consent gate, web page host
  web/               shared web runtime (buddy.js, buddy.css) and the shell's pages
  pages/             one package per tool: Python logic plus its web/ view;
                     base.py defines the ToolPage contract
  registry.py        which tools exist, and which are real
Buddy.py             launcher stub for Resolve's Scripts menu
build_buddy_zip.py   builds buddy.zip, and deploys it with --deploy
build_installer.py   builds the Windows installer (installer/, dist/)
build_mac_installer.py  builds the macOS installer (installer/mac/, dist/)
build_manual_bundle.py  builds the manual bundle from the PDF (same as the
                     Settings button; pages/manual_chat/bundle_builder.py)
Deploy to Resolve.command  on a Mac: installs the packages and deploys, by double-click
manual_install.txt   installing by hand, on Windows or a Mac
server/              the Buddy Network server
tests/               automated tests (see Tests)
docs/RESOLVE_API_GUIDE.md  Resolve scripting API traps, for anyone working on the code
```

Themes live in `core/theme.py` as a Theme → Subtheme hierarchy: **Default**
(DaVinci Resolve's own look - colours, control sizes and the Open Sans type
matched to Resolve 21's UI, so Buddy sits in Resolve as if it were part
of it; the default for new installs) with DaVinci and the colour variants
Blue, Teal, Green, Yellow, Orange, Purple and Pink (Resolve's greys washed
toward the colour, which takes the place of Resolve's red), **Don't be evil** (dark,
softly rounded), **Retro** (flat, square) with dark
(Mulberry - its default - and Licorice) and light paper (Peach, Bubblegum)
palettes, **Modern** (soft-rounded product-dashboard look: a navy-teal glow behind slate cards and
one vivid accent) with Midnight, Aurora, Graphite and the light Daylight,
**Nova** (Modern's gradient look with rounder, roomier shapes) with Neon,
Nebula and Ember, **Off-world** (a terminal: square hairline boxes and
monospace type) with Amber, Green and Ice, and **Desktop** (tools in floating
windows instead of the rail's panes) with Paper, Mint, Sky, Dusk, Arcade and
Cocoa - each theme also has a Custom palette. (Settings files store the original
keys `Resolve` / `Default` / `Retro` / `SaaS`; the names shown are labels.)
Open Sans ships in `app/assets/fonts/` under the SIL Open Font License
(`OFL.txt` beside it). Status colours follow the palette's
lightness, so a dark subtheme gets dark-surface status text.

## Tests

```
python -m unittest discover tests
```

Most tests are plain Python - no Qt, Resolve or models needed (`pytest tests`
works too). The ones that render pages need PySide6 with QtWebEngine and skip
without it. `tests/test_layout_overlaps.py` opens every tool, tab and window
at a range of sizes in every theme and fails if any UI overlaps other UI - it
takes a few minutes, and runs on a Mac only (it's skipped on Windows). `python tests/run_suite.py` is how GitHub Actions runs
them, on Windows and on a Mac (`.github/workflows/tests.yml`).

The Subtitles ones cover what can go subtly wrong without
anything looking broken: subtitle wrapping and timing (seeded randomized
runs checking no lost words, overlaps or over-long lines), translation
re-timing, Chinese/Japanese line breaking, SRT parsing, the language tables,
model-folder identification, and the transcription worker's text clean-up.
Run them after changing anything in `app/pages/transcribe/`.

## Notes

Working on a tool that talks to Resolve? Read
[`docs/RESOLVE_API_GUIDE.md`](docs/RESOLVE_API_GUIDE.md) first: it covers the
scripting API's silent failures and the Fusion node-graph traps that
Blackmagic's reference doesn't mention.

`buddy.zip` is not tracked — it is a build artifact, rebuilt by the command
above.

## License

Free to use for anything, including paid work, and free to change and share.
What you can't do is sell it: charging for Buddy or a modified copy, or for a
product or service whose value comes mainly from it, isn't allowed. The
[licence](LICENSE) is MIT with the Commons Clause, which is source-available
rather than OSI "open source". The Open Sans font keeps its own SIL Open Font
License (`app/assets/fonts/OFL.txt`). Some Animation presets are modelled on
animate.css and Magic Animations, both MIT; their notices are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
