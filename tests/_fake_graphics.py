"""A Resolve small enough for Data Graphics' tests (core/fusion_place.py,
pages/data_graphics/place.py): a project with one timeline whose video tracks hold
clips, a Media Pool with Buddy's Text+ template in its bin, and clips that take a
.comp file - behaving the way Studio 21.1.2 was measured to (AppendToTimeline lands on
the frame asked and leaves the playhead at the clip's end; ImportFusionComp replaces
the comp, whose CustomData reads back with GetData)."""

import re


def timecode(frame, fps=24):
    f = int(frame)
    return f"{f // (fps * 3600):02d}:{f // (fps * 60) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"


class Comp:
    def __init__(self, data=None, text=""):
        self.data, self.text = dict(data or {}), text

    def GetData(self, key):
        return self.data.get(key)


class Item:
    def __init__(self, start, end, name="Text+", comp=None):
        self.start, self.end, self.name = start, end, name
        self.comp = comp or Comp()
        self.takes_comps = True

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end

    def GetName(self):
        return self.name

    def GetFusionCompCount(self):
        return 1

    def GetFusionCompByIndex(self, _index):
        return self.comp

    def ImportFusionComp(self, path):
        if not self.takes_comps:
            return None
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        custom = re.search(r"CustomData = \{(.*?)\},\n", text).group(1)
        self.comp = Comp(dict(re.findall(r'(\w+) = "([^"]*)"', custom)), text)
        return self.comp


class Clip:
    def __init__(self, kind="Fusion Title", fps=24.0):
        self.kind, self.fps = kind, fps

    def GetClipProperty(self, name):
        return {"Type": self.kind, "FPS": self.fps}.get(name, "")

    def GetName(self):
        return "Text+"


class Folder:
    def __init__(self, name, clips=(), folders=()):
        self.name, self.clips, self.folders = name, list(clips), list(folders)

    def GetName(self):
        return self.name

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.folders


class Timeline:
    def __init__(self, fps=24, size=(1920, 1080), start=86400, name="Edit"):
        self.fps, self.size, self.start, self.name = fps, size, start, name
        self.tracks = {1: []}
        self.playhead = start
        self.takes_comps = True
        self.adds_tracks = True

    def GetName(self):
        return self.name

    def GetSetting(self, key):
        return {"timelineFrameRate": str(self.fps), "timelineResolutionWidth": str(self.size[0]),
                "timelineResolutionHeight": str(self.size[1]), "timelineDropFrameTimecode": "0"}.get(key, "")

    def GetStartFrame(self):
        return self.start

    def GetCurrentTimecode(self):
        return timecode(self.playhead, self.fps)

    def SetCurrentTimecode(self, text):
        h, m, s, f = (int(p) for p in text.split(":"))
        self.playhead = ((h * 60 + m) * 60 + s) * self.fps + f
        return True

    def GetTrackCount(self, kind):
        return len(self.tracks) if kind == "video" else 0

    def GetItemListInTrack(self, _kind, index):
        return list(self.tracks.get(index, []))

    def AddTrack(self, _kind):
        if not self.adds_tracks:
            return False
        self.tracks[len(self.tracks) + 1] = []
        return True

    def DeleteClips(self, items, _ripple=False):
        for track in self.tracks.values():
            track[:] = [it for it in track if it not in items]
        return True

    def put(self, track, start, end, name="Interview.mov", data=None):
        """A clip already on the timeline - someone's footage, or (with data) Buddy's."""
        while len(self.tracks) < track:
            self.tracks[len(self.tracks) + 1] = []
        item = Item(start, end, name, Comp(data))
        self.tracks[track].append(item)
        return item


class Pool:
    def __init__(self, timeline, template=True):
        self.timeline = timeline
        self.root = Folder("Master", folders=[Folder("Buddy Text+", [Clip()])] if template else [])
        self.appended = []

    def GetRootFolder(self):
        return self.root

    def GetCurrentFolder(self):
        return self.root

    def SetCurrentFolder(self, _folder):
        return True

    def ImportFolderFromFile(self, _path):
        return False

    def AppendToTimeline(self, rows):
        made = []
        for row in rows:
            self.appended.append(dict(row))
            length = row["endFrame"] - row["startFrame"]
            item = Item(row["recordFrame"], row["recordFrame"] + length)
            item.takes_comps = self.timeline.takes_comps
            self.timeline.tracks[row["trackIndex"]].append(item)
            self.timeline.playhead = item.end            # where Resolve leaves it
            made.append(item)
        return made


class Project:
    def __init__(self, timeline=None, template=True):
        self.timeline = timeline
        self.pool = Pool(timeline, template)

    def GetMediaPool(self):
        return self.pool

    def GetCurrentTimeline(self):
        return self.timeline


class _Fonts:
    def __init__(self, fonts):
        self.FontManager = self
        self.fonts = fonts

    def GetFontList(self):
        return self.fonts


class _Resolve:
    def __init__(self, fonts):
        self.fonts = fonts

    def Fusion(self):
        return _Fonts(self.fonts)


class Controller:
    def __init__(self, project=None, fonts=None):
        self.project = project
        self.resolve = _Resolve(fonts if fonts is not None else {
            "Arial": {"Regular": "arial.ttf", "Bold": "arialbd.ttf"}, "Inter": {"Black": "Inter-Black.ttf"}})

    def current_project(self):
        return self.project


def studio(**timeline):
    """(controller, timeline) with a timeline open."""
    tl = Timeline(**timeline)
    return Controller(Project(tl)), tl
