"""A panel as a Fusion comp - the text of a .comp file (core/fusion_comp.py). Pure: no
Resolve, no Qt.

Everything is baked: clock.pose() is sampled on every frame of the clip and what changes
is written as keys (thinned to where it turns, or strays from a straight line by more
than a hair - _thin), straight between them. Nothing in the comp looks at the cursor.

The flow, bottom to top, merged in order onto a clear `Canvas` into MediaOut1:

    Window_*            the window's chrome (styles.chrome): its frame, shadow, bar and
                        bar buttons, one static layer
    <Page>_Title*       what of the chrome differs from page to page (the bar's title when
                        the panel has none of its own), faded as its page is
    <Page>Canvas ...    each page that ever shows: its boxes' prims merged in order onto a
    <Page>On            canvas of its own, put on by a Merge whose Center is the page's
                        slide (dx), its Blend the page's alpha and its EffectMask the
                        window's inside (WindowInside, WindowInsideBody)
    Cursor*             the recorded cursor, a keyed path, smaller while its button is down

Inside a page, a box that never moves is drawn straight into the page's chain. A box
the clock moves - a build-on, a hover or press, a match glide - is put on by a Merge of
its own (<Page>_<Box>On): its Blend the box's alpha, its Size the box's scale and its
Center the box's move, with what makes up for the Merge sizing about the frame's middle
rather than the box's. A box carries what is in it (clock.carried; the poses are local):
a moving box with anything in it, drawn as more than one picture, or glowing, is put
together - its own prims, then everything in it - on a canvas of its own
(<Page>_<Box>Canvas), its glow under it (<Page>_<Box>GlowOn), and a moving box inside it
is a canvas inside that canvas. Its own prims fade by its "own" there. Only a match
glide that stretches (sx != sy) adds a Transform (<Page>_<Box>Stretch).

A page arriving over one merged after it - a change back to an earlier page - is put on
a second time, above every page (<Page>OnTop), on the frames it does, and not by its own
Merge then: the pages stack as pose() has them, back to front.

Each prim (styles.py) is a mask and a Background, as Design draws them: a rect a
RectangleMask (its shadow the same moved down and softened, its stroke the same with
Solid 0 and a BorderWidth), an ellipse an EllipseMask, a line or poly a PolylineMask, a
wedge an EllipseMask ring swept as Graphs' pie, text a Text+ set as Design sets it.
Neighbouring prims of one opaque colour with nothing else to them share a Background
(their masks united, PaintMode Merge). What a chart draws on is keyed in its masks: a
line's WriteLength, an area's wipe, a bar's height, a progress bar's width, a slice's
sweep - all from pose's "draw".

An image box's picture (styles' "image" prim) is a Loader written as text (never
AddTool: docs/RESOLVE_API_GUIDE.md, section 5), put on a clear Background by a Merge -
its Size the box's width over the crop's (a Merge's Size 1 is the picture's own px), its
Center the box's middle (the crop is the picture's middle) - and cropped to the box, its
corners rounded, by a RectangleMask on that Merge's EffectMask: Interactions' image
shapes (pages/interactions/comp.py _loader, _fill) and Design's (_image). That Merge's
output is one picture like any prim's, so an image box that builds on or answers the
cursor moves as every box does.

Typing: a line that types is a Text+ whose StyledText is keyed (fusion_comp.text_spline)
to the letters shown on each frame - the line's first `shown` letters. The letters
appear in place because the line is set from its START: styles.py lays a typing line
out left-aligned at the x where the whole line starts by the solver's letter-width rule
(solve.typing), and the Text+ is HorizontalLeftCenterRight -1 there, so a letter added
at its end moves none before it. This was chosen over keeping every letter in the text
and hiding the ones not yet typed: nothing in this repo has measured a per-letter
opacity on a Text+ (Character Level Styling, a Follower's Opacity, Write On), while a
left-set Text+ and a keyed StyledText are both measured (guide, section 5). The cost: a
centred or right-aligned line that types is placed by the rule's width, not the face's,
so when it has all its letters it can sit a few px off where the same line not typing
would - look at a centred heading that types, fully typed, beside one that doesn't.
A field that types draws its placeholder (faded out by its Merge's Blend from the frame
its first letter shows) and its typed line. The caret is a RectangleMask keyed to step
with the letters (clock.caret on each frame), its Background in the letters' colour,
blinking by its Merge's Blend.
"""

from core import fusion_comp as fc
from pages.data_graphics import text_look
from pages.design.comp import SOFT_EDGE_PER_SIGMA, face_key
from pages.interactions.comp import CURSOR_DIP, CURSOR_LOOKS, GLOW_REACH, GLOW_SOFT, _lifted, _loader

from . import clock, model, solve, styles

TAG = "BuddyPanel"                  # CustomData key on the comp: the panel's id
COLUMN, ROW = 110, 33
PX_TOL = 0.25                       # a place or a length is keyed to within this many px of the frame
ALPHA_TOL = 0.002                   # an alpha or a glow, to within this
SCALE_TOL = 0.0002                  # a scale, to within this
REST = clock.REST                   # how a box the pose doesn't list looks
# A soft shadow (a rect's "shadow", its blur in panel px as a canvas's shadowBlur: a
# Gaussian of half that): black at this strength, this share of its blur down.
SHADOW_COLOUR = "#00000059"
SHADOW_DROP = 0.25
# A glow reaches and softens as Interactions' does at the panel's starting zoom, and
# grows with the panel.
GLOW_ZOOM = model.DEFAULTS["zoom"]
# What a prim's tools are called after its box's name: Dashboard_Card_Rect, ..._Label.
KIND_NAMES = {"rect": "Rect", "ellipse": "Ellipse", "line": "Line", "poly": "Poly", "wedge": "Slice", "text": "Label",
              "image": "Picture", "caret": "Caret"}


# ------------------------------------------------------------------- keys --

def _thin(values, tol):
    """[(frame, value)] keys for a value on every frame, or None when it never changes
    by more than tol. Kept: the first and last frame, every frame it turns (goes up,
    down or holds after doing otherwise) and as few between as keep a straight line
    between keys within tol of every frame."""
    if not values or max(values) - min(values) <= tol:
        return None
    pts = fc.straight(list(enumerate(values)))

    def way(a, b):
        d = b[1] - a[1]
        return 0 if abs(d) <= 1e-9 else (1 if d > 0 else -1)
    anchors = [0] + [i for i in range(1, len(pts) - 1) if way(pts[i - 1], pts[i]) != way(pts[i], pts[i + 1])] \
        + [len(pts) - 1]

    def fits(i, j):
        (f0, v0), (f1, v1) = pts[i], pts[j]
        return all(abs(pts[k][1] - (v0 + (v1 - v0) * (pts[k][0] - f0) / (f1 - f0))) <= tol for k in range(i + 1, j))
    out = [pts[0]]
    for a, b in zip(anchors, anchors[1:]):
        i = a
        while i < b:
            j = i + 1
            while j < b and fits(i, j + 1):
                j += 1
            out.append(pts[j])
            i = j
    return out


def _held(values):
    """A list with its gaps (None) filled from the nearest frame that has a value."""
    out = list(values)
    last = None
    for i, v in enumerate(out):
        if v is None:
            out[i] = last
        else:
            last = v
    nxt = None
    for i in range(len(out) - 1, -1, -1):
        if values[i] is not None:
            nxt = values[i]
        elif out[i] is None:
            out[i] = nxt
    return out


def _label(text, fallback):
    """A name as a tool's name starts: its words' letters and digits joined by _, never a digit first."""
    words = ["".join(ch for ch in word if ch.isascii() and ch.isalnum()) for word in str(text or "").split()]
    base = "_".join(w[:1].upper() + w[1:] for w in words if w)[:40]
    if not base or base[0].isdigit():
        base = fallback + base
    return base


# ------------------------------------------------------------------ units --

class _Units:
    """Panel px (from the window's top left) on the frame: zoom x frame width / 1920
    larger, the window's middle at x, y % of the frame (CONTRACT.md, Units)."""

    def __init__(self, spec, frame, height):
        self.frame = frame
        fw, fh = frame
        self.k = solve.scale(spec, frame)
        self.ox = spec["x"] / 100.0 * fw - spec["width"] * self.k / 2.0
        self.oy = spec["y"] / 100.0 * fh - height * self.k / 2.0

    def px(self, x, y):
        """Frame px, top-down."""
        return self.ox + x * self.k, self.oy + y * self.k

    def point(self, x, y):
        """0..1 across and up: a Center."""
        fx, fy = self.px(x, y)
        return fx / self.frame[0], 1 - fy / self.frame[1]

    def rel(self, x, y):
        """An offset from the frame's middle as shares of its width and height, y up: a polyline's point."""
        fx, fy = self.px(x, y)
        return fx / self.frame[0] - 0.5, 0.5 - fy / self.frame[1]

    def wide(self, length):
        """A length as a share of the frame's width."""
        return length * self.k / self.frame[0]

    def high(self, length):
        return length * self.k / self.frame[1]


# ------------------------------------------------------------ the samples --

def _sample(spec, frame, fps, comp_frames, solved):
    """What pose() says on every frame, by what it's said of: {"pages": {page id:
    {"alpha", "dx", "shown", "boxes": {box id: {key: [per frame]}}}}, "draw": {box id:
    [...]}, "cursor": [(x, y) panel px], "down": [...]}. A frame a page isn't showing
    holds its boxes as they were when it last showed (or will first), so they are keyed
    only where they're seen."""
    clk = clock.clock(spec, fps, frame)
    last = max(0, int(clk.get("total") or comp_frames) - 1)
    poses = [clock.pose(spec, clk, min(f, last)) for f in range(comp_frames)]
    pages = {}
    for page in spec["pages"]:
        pid = page["id"]
        ids = [b["id"] for b in solved[pid]["boxes"]]
        alpha, dx, boxes = [], [], {bid: {key: [] for key in REST} for bid in ids}
        for pose in poses:
            entry = next((e for e in pose.get("pages") or [] if e.get("page") == pid), None)
            alpha.append(float(entry.get("alpha", 1.0)) if entry else 0.0)
            dx.append(float(entry.get("dx", 0.0)) if entry else None)
            said = (entry.get("boxes") or {}) if entry else None
            for bid in ids:
                look = said.get(bid) if said is not None else None
                for key, rest in REST.items():
                    boxes[bid][key].append(None if said is None else float((look or {}).get(key, rest)))
        shown = any(a > ALPHA_TOL for a in alpha)
        pages[pid] = {"alpha": alpha, "dx": _held(dx) if shown else [0.0] * comp_frames, "shown": shown,
                      "boxes": {bid: {key: _held(v) if shown else [REST[key]] * comp_frames for key, v in looks.items()}
                                for bid, looks in boxes.items()}}
    draw, typed, carets = {}, {bid: [None] * comp_frames for bid in clk.get("typed") or {}}, {}
    said = {bid: [None] * comp_frames for bid in clk.get("said") or {}}
    for f, pose in enumerate(poses):
        for bid, words in (pose.get("said") or {}).items():
            said[bid][f] = str(words)
        for bid, share in (pose.get("draw") or {}).items():
            draw.setdefault(bid, [None] * comp_frames)[f] = float(share)
        for bid, shown in (pose.get("typed") or {}).items():
            typed.setdefault(bid, [None] * comp_frames)[f] = int(shown)
        for bid, rect in (pose.get("caret") or {}).items():
            carets.setdefault(bid, [None] * comp_frames)[f] = list(rect)
    return {"pages": pages, "draw": {bid: _held(v) for bid, v in draw.items()},
            # Letters shown, held where its page doesn't show (0 where it never does).
            "typed": {bid: _held(v) if any(s is not None for s in v) else [0] * comp_frames for bid, v in typed.items()},
            "caret": carets,            # {box id: [x, y, w, h] where it shows, else None, per frame}
            # What a counting text says, held where its page doesn't show (never shown: nothing here).
            "said": {bid: _held(v) for bid, v in said.items() if any(s is not None for s in v)},
            "order": [[entry.get("page") for entry in pose.get("pages") or []] for pose in poses],
            "cursor": [tuple(pose.get("cursor") or (0.0, 0.0)) for pose in poses],
            "down": [1.0 if pose.get("down") else 0.0 for pose in poses]}


def _moves(looks):
    """Whether a box's looks ever leave their rest."""
    return any(abs(v - REST[key]) > _TOL[key] for key, values in looks.items() for v in values)


_TOL = {"alpha": ALPHA_TOL, "glow": ALPHA_TOL, "own": ALPHA_TOL, "scale": SCALE_TOL, "sx": SCALE_TOL, "sy": SCALE_TOL, "dx": 0.05, "dy": 0.05}


def _plain(prim):
    """A prim that is only an opaque fill: neighbours of one colour may share a Background."""
    return (prim["kind"] in ("rect", "ellipse") and len(prim.get("fill") or "") in (7, 9)
            and (len(prim["fill"]) == 7 or prim["fill"][7:].lower() == "ff") and not prim.get("stroke")
            and not prim.get("shadow") and not prim.get("draw") and prim["w"] > 0 and prim["h"] > 0)


def _pictures(prim):
    """How many pictures a prim is merged on as."""
    kind = prim["kind"]
    if kind == "rect":
        return (1 if prim.get("shadow") else 0) + (1 if prim.get("fill") else 0) + (1 if prim.get("stroke") else 0)
    if kind in ("ellipse", "poly"):
        return (1 if prim.get("fill") else 0) + (1 if prim.get("stroke") and prim.get("stroke_w") else 0)
    return 1


# --------------------------------------------------------------- the comp --

class _Comp:
    def __init__(self, spec, frame, fps, comp_frames, metrics):
        self.spec, self.frame, self.comp_frames = spec, frame, comp_frames
        self.metrics = metrics or {}
        self.solved = solve.solve_all(spec)
        self.height = self.solved[spec["start"]]["h"]
        self.u = _Units(spec, frame, self.height)
        self.tools, self.names, self.stems = [], set(), set()
        self.col = 0
        self.samples = _sample(spec, frame, fps, comp_frames, self.solved)
        self.tol_x, self.tol_y = PX_TOL / frame[0], PX_TOL / frame[1]

    # Names and tools.
    def add(self, text):
        name = text.lstrip("\t").split(" = ", 1)[0]
        if name in self.names:
            raise ValueError(f"two tools named {name}")
        self.names.add(name)
        self.tools.append(text)

    def stem(self, base):
        """A name no tool's starts with yet, to name a piece's tools by."""
        stem, n = base, 1
        while stem in self.stems or any(name.startswith(stem) for name in self.names):
            n += 1
            stem = f"{base}{n}"
        self.stems.add(stem)
        return stem

    def column(self):
        self.col += 1
        return COLUMN * self.col

    def keyed(self, tool, name, values, tol):
        """The input `name` of `tool`: a plain Input when it holds, else wired to a spline of its keys."""
        if not isinstance(values, (list, tuple)):
            return fc.input_(name, values)
        keys = _thin(values, tol)
        if keys is None:
            return fc.input_(name, values[0])
        self.add(fc.spline(f"{tool}{name}", keys))
        return fc.wired(name, f"{tool}{name}")

    def keyed_point(self, tool, name, xs, ys, pos):
        """A Point input: plain when it holds, else an XYPath of its keys."""
        xs = xs if isinstance(xs, (list, tuple)) else [xs] * self.comp_frames
        ys = ys if isinstance(ys, (list, tuple)) else [ys] * self.comp_frames
        kx, ky = _thin(xs, self.tol_x), _thin(ys, self.tol_y)
        if kx is None and ky is None:
            return fc.input_(name, (xs[0], ys[0]))
        path = f"{tool}{name}"
        # fusion_comp.path, less the spline of a side that holds: a plain X or Y instead.
        sides = []
        for side, keys, values in (("X", kx, xs), ("Y", ky, ys)):
            if keys is None:
                sides.append(fc.input_(side, values[0]))
            else:
                self.add(fc.spline(f"{path}{side}", keys))
                sides.append(fc.wired(side, f"{path}{side}"))
        self.add(fc.block(path, "XYPath", sides, pos, "\t\t\tShowKeyPoints = false,\n\t\t\tDrawMode = \"ModifyOnly\",\n"))
        return fc.wired(name, path)

    def sized(self):
        return [fc.input_("MaskWidth", self.frame[0]), fc.input_("MaskHeight", self.frame[1])]

    def merge(self, name, under, top, pos, extra=()):
        self.add(fc.merge(name, under, top, pos, extra))
        return name

    # Masks.
    def rect_mask(self, name, x, y, w, h, radius, pos, line=0.0, soft=0.0, extra=()):
        """A RectangleMask over panel px x, y (top left), w, h - each a number or a list a
        frame (then keyed)."""
        n = self.comp_frames
        lists = any(isinstance(v, list) for v in (x, y, w, h))
        if lists:
            xs, ys, ws, hs = ([v] * n if not isinstance(v, list) else v for v in (x, y, w, h))
            cx = [self.u.point(a + b / 2, c + d / 2)[0] for a, b, c, d in zip(xs, ws, ys, hs)]
            cy = [self.u.point(a + b / 2, c + d / 2)[1] for a, b, c, d in zip(xs, ws, ys, hs)]
            corner = [min(1.0, radius / (min(b, d) / 2)) if radius > 0 and min(b, d) > 0 else 0.0 for b, d in zip(ws, hs)]
            inputs = [self.keyed_point(name, "Center", cx, cy, (pos[0] - 55, pos[1])),
                      self.keyed(name, "Width", [self.u.wide(v) for v in ws], self.tol_x),
                      self.keyed(name, "Height", [self.u.high(v) for v in hs], self.tol_y),
                      self.keyed(name, "CornerRadius", corner, 0.002)]
        else:
            inputs = [fc.input_("Center", self.u.point(x + w / 2, y + h / 2)), fc.input_("Width", self.u.wide(w)),
                      fc.input_("Height", self.u.high(h))]
            if radius > 0 and min(w, h) > 0:
                inputs.append(fc.input_("CornerRadius", min(1.0, radius / (min(w, h) / 2))))
        if line > 0:
            inputs += [fc.input_("Solid", 0), fc.input_("BorderWidth", self.u.wide(line))]
        if soft > 0:
            inputs.append(fc.input_("SoftEdge", self.u.wide(soft) * SOFT_EDGE_PER_SIGMA))
        self.add(fc.block(name, "RectangleMask", [*inputs, *self.sized(), *extra], pos))
        return name

    def ellipse_mask(self, name, x, y, w, h, pos, line=0.0, extra=()):
        # An ellipse's Width and Height are both shares of the frame's WIDTH.
        inputs = [fc.input_("Center", self.u.point(x + w / 2, y + h / 2)), fc.input_("Width", self.u.wide(w)),
                  fc.input_("Height", self.u.wide(h))]
        if line > 0:
            inputs += [fc.input_("Solid", 0), fc.input_("BorderWidth", self.u.wide(line))]
        self.add(fc.block(name, "EllipseMask", [*inputs, *self.sized(), *extra], pos))
        return name

    def poly_mask(self, name, points, closed, pos, line=0.0, cap=1, extra=()):
        inputs = [fc.input_("Polyline", fc.polyline([self.u.rel(x, y) for x, y in points], closed=closed))]
        if line > 0:
            inputs += [fc.input_("BorderWidth", self.u.wide(line)), fc.input_("CapStyle", cap), fc.input_("JoinStyle", 1)]
        self.add(fc.block(name, "PolylineMask", [*inputs, *self.sized(), *extra], pos, '\t\t\tDrawMode = "ModifyOnly",\n'))
        return name

    def paint(self, name, colour, mask, pos):
        self.add(fc.solid(name, colour, self.frame, self.comp_frames, pos, extra=[fc.mask(mask)]))
        return name

    # Prims.
    def share(self, bid):
        """A box's drawn-on share on every frame, or None when it's always whole."""
        values = self.samples["draw"].get(bid)
        if values is None or all(abs(v - 1.0) <= 1e-6 for v in values):
            return None
        return values

    def prim(self, owner, p):
        """A prim's tools: [the pictures to merge on, bottom first]."""
        kind = p["kind"]
        stem = self.stem(f"{owner}_{KIND_NAMES.get(kind, 'Shape')}")
        x = self.column()
        out = []
        if kind == "rect":
            if p["w"] <= 0 or p["h"] <= 0:
                return []
            bx, by, bw, bh, r = p["x"], p["y"], p["w"], p["h"], p.get("radius") or 0.0
            share = self.share(p["box"]) if p.get("draw") in ("up", "across") else None
            if share is not None:
                if p["draw"] == "up":               # a bar, growing up from its foot
                    by, bh = [p["y"] + p["h"] * (1 - s) for s in share], [p["h"] * s for s in share]
                else:                               # a progress bar's fill, across from its left
                    bw = [p["w"] * s for s in share]
            if p.get("shadow"):
                blur = p["shadow"]
                drop = blur * SHADOW_DROP
                sy = [v + drop for v in by] if isinstance(by, list) else by + drop
                self.rect_mask(f"{stem}ShadowMask", bx, sy, bw, bh, r, (x, 0), soft=blur / 2)
                out.append(self.paint(f"{stem}Shadow", SHADOW_COLOUR, f"{stem}ShadowMask", (x, ROW)))
            if p.get("fill"):
                self.rect_mask(f"{stem}Mask", bx, by, bw, bh, r, (x, 2 * ROW))
                out.append(self.paint(f"{stem}Fill", p["fill"], f"{stem}Mask", (x, 3 * ROW)))
            if p.get("stroke") and p.get("stroke_w"):
                self.rect_mask(f"{stem}StrokeMask", bx, by, bw, bh, r, (x, 4 * ROW), line=p["stroke_w"])
                out.append(self.paint(f"{stem}Stroke", p["stroke"], f"{stem}StrokeMask", (x, 5 * ROW)))
            return out
        if kind == "ellipse":
            if p.get("fill"):
                self.ellipse_mask(f"{stem}Mask", p["x"], p["y"], p["w"], p["h"], (x, 2 * ROW))
                out.append(self.paint(f"{stem}Fill", p["fill"], f"{stem}Mask", (x, 3 * ROW)))
            if p.get("stroke") and p.get("stroke_w"):
                self.ellipse_mask(f"{stem}StrokeMask", p["x"], p["y"], p["w"], p["h"], (x, 4 * ROW), line=p["stroke_w"])
                out.append(self.paint(f"{stem}Stroke", p["stroke"], f"{stem}StrokeMask", (x, 5 * ROW)))
            return out
        if kind == "line":
            # A canvas's line ends square at its points (butt).
            self.poly_mask(f"{stem}Mask", [(p["x1"], p["y1"]), (p["x2"], p["y2"])], False, (x, 2 * ROW),
                           line=p.get("stroke_w") or 1.0, cap=0)
            return [self.paint(f"{stem}Fill", p["stroke"], f"{stem}Mask", (x, 3 * ROW))]
        if kind == "poly":
            return self.poly(stem, p, x)
        if kind == "wedge":
            return self.wedge(stem, p, x)
        if kind == "text":
            made = self.text(stem, p, x)
            return [made] if made else []
        if kind == "image":
            return self.image(stem, p, x)
        if kind == "caret":
            return self.caret(stem, p, x)
        return []

    def image(self, stem, p, x):
        """A picture filling its box and cropped to it (styles' "crop": the picture's
        middle, with the box's shape): a Loader, put on a clear Background by a Merge
        sized so the crop is the box's width and centred on the box, the box's rounded
        rectangle on its EffectMask (Interactions' _fill, Design's _image)."""
        if p["w"] <= 0 or p["h"] <= 0 or p["crop"][2] <= 0:
            return []
        self.rect_mask(f"{stem}Mask", p["x"], p["y"], p["w"], p["h"], p.get("radius") or 0.0, (x, 2 * ROW))
        self.add(_loader(f"{stem}Image", p["path"], (x - 55, ROW)))
        self.add(fc.clear(f"{stem}Back", self.frame, self.comp_frames, (x + 55, ROW)))
        # A Merge's Size 1 is the picture's own px: the crop's width becomes the box's.
        size = p["w"] * self.u.k / p["crop"][2]
        self.add(fc.merge(f"{stem}Fill", f"{stem}Back", f"{stem}Image", (x, 3 * ROW), extra=[
            fc.input_("Center", self.u.point(p["x"] + p["w"] / 2, p["y"] + p["h"] / 2)), fc.input_("Size", size),
            fc.mask(f"{stem}Mask")]))
        return [f"{stem}Fill"]

    def caret(self, stem, p, x):
        """A box's caret: a RectangleMask keyed to where clock.caret() says on each frame
        (it steps with the letters), painted the letters' colour. Whether it shows - its
        blink - is the Blend of the Merge that puts it on (visible())."""
        rects = self.samples["caret"].get(p["box"])
        if not rects or all(r is None for r in rects):
            return []
        held = _held(rects)
        self.rect_mask(f"{stem}Mask", [r[0] for r in held], [r[1] for r in held], [r[2] for r in held],
                       [r[3] for r in held], 0.0, (x, 2 * ROW))
        return [self.paint(f"{stem}Fill", p["fill"], f"{stem}Mask", (x, 3 * ROW))]

    def visible(self, p):
        """Whether a prim shows, on every frame, where that isn't always: a field's
        placeholder until its first letter, a caret while it stands and blinks on. None
        for a prim that always shows."""
        if p.get("placeholder"):
            typed = self.samples["typed"].get(p["box"])
            return None if typed is None else [1.0 if shown == 0 else 0.0 for shown in typed]
        if p["kind"] == "caret":
            rects = self.samples["caret"].get(p["box"]) or []
            return [0.0 if r is None else 1.0 for r in rects] or None
        return None

    def poly(self, stem, p, x):
        out, pts = [], p["points"]
        if len(pts) < 2:
            return out
        share = self.share(p["box"])
        if p.get("fill") and p.get("closed"):
            extra = []
            if p.get("draw") == "across" and share is not None:
                # The room under a chart's line wipes on with it, left to right across the
                # points' width (Graphs' area; the view clips it the same way).
                left, right = min(px for px, _py in pts), max(px for px, _py in pts)
                top, bottom = min(py for _px, py in pts), max(py for _px, py in pts)
                self.rect_mask(f"{stem}Wipe", left, top - 1, [(right - left) * s for s in share], bottom - top + 2, 0.0,
                               (x - 55, ROW))
                extra = [fc.input_("PaintMode", fc.fuid("Multiply")), fc.mask(f"{stem}Wipe")]
            self.poly_mask(f"{stem}Mask", pts, True, (x, 2 * ROW), extra=extra)
            out.append(self.paint(f"{stem}Fill", p["fill"], f"{stem}Mask", (x, 3 * ROW)))
        if p.get("stroke") and p.get("stroke_w"):
            extra = []
            if p.get("draw") is True and share is not None:
                # Drawn on by its length (WriteLength, in px along it, as Graphs' line).
                extra = [self.keyed(f"{stem}StrokeMask", "WriteLength", share, 0.001)]
            self.poly_mask(f"{stem}StrokeMask", pts, bool(p.get("closed")), (x, 4 * ROW), line=p["stroke_w"], extra=extra)
            out.append(self.paint(f"{stem}Stroke", p["stroke"], f"{stem}StrokeMask", (x, 5 * ROW)))
        return out

    def wedge(self, stem, p, x):
        """A slice: an EllipseMask ring turned to start at 12 o'clock, swept clockwise
        over [from, to] (Graphs' pie: WritePosition 1 - end, WriteLength end - start)."""
        a, b = p["from"] / 360.0, p["to"] / 360.0
        share = self.share(p["box"]) if p.get("draw") else None
        ends = [min(b, max(a, s)) for s in share] if share is not None else b
        r, hole = p["r"], p.get("hole") or 0.0
        start = [1 - e for e in ends] if share is not None else 1 - b
        length = [e - a for e in ends] if share is not None else b - a
        inputs = [fc.input_("Center", self.u.point(p["cx"], p["cy"])), fc.input_("Width", self.u.wide(r + hole)),
                  fc.input_("Height", self.u.wide(r + hole)), fc.input_("Solid", 0),
                  fc.input_("BorderWidth", self.u.wide(r - hole)), fc.input_("CapStyle", 0), fc.input_("Angle", 90),
                  self.keyed(f"{stem}Mask", "WritePosition", start, 0.0005),
                  self.keyed(f"{stem}Mask", "WriteLength", length, 0.0005)]
        self.add(fc.block(f"{stem}Mask", "EllipseMask", [*inputs, *self.sized()], (x, 2 * ROW)))
        return [self.paint(f"{stem}Fill", p["fill"], f"{stem}Mask", (x, 3 * ROW))]

    def text(self, stem, p, x):
        """A line of text as a Text+, its letters `size` panel px, its baseline on y - set
        as Design's text is (Size = letter size / (em x width), the face's baseline)."""
        style = self.spec["bold_face"] if p.get("bold") else self.spec["face"]
        metrics = text_look.clean_metrics(self.metrics.get(face_key(self.spec["font"], style)))
        look = {"font": self.spec["font"], "style": style, "colour": p.get("fill") or "#ffffff"}
        fx, fy = self.u.px(p["x"], p["y"])
        keyed, words = None, p["text"]
        if p.get("type"):
            # A line that types: its first letters on each frame - set from its start (styles
            # lays it out left-aligned), so a letter added moves none before it.
            first, last = p["type"]
            typed = self.samples["typed"].get(p["box"]) or [last] * self.comp_frames
            shown = [p["text"][:max(0, min(last - first, n - first))] for n in typed]
            keys = [(f, s) for f, s in enumerate(shown) if f == 0 or s != shown[f - 1]]
            if not any(shown):
                return None                     # it never shows a letter
            if len(keys) > 1:
                keyed = f"{stem}Typed"
                self.add(fc.text_spline(keyed, keys))
            words = shown[0]
        elif p["box"] in self.samples.get("said", {}):
            # A text that counts up: what it says on each frame, keyed as a typing line's is.
            shown = self.samples["said"][p["box"]]
            keys = [(f, s) for f, s in enumerate(shown) if f == 0 or s != shown[f - 1]]
            if len(keys) > 1:
                keyed = f"{stem}Counted"
                self.add(fc.text_spline(keyed, keys))
            words = shown[0]
        self.add(text_look.text_plus(stem, self.frame, self.comp_frames, look, metrics, p["size"] * self.u.k,
                                     fx, fy, (x, 3 * ROW), text=words, keyed=keyed, align=p.get("align") or "left"))
        return stem

    def run(self, owner, prims, under, own=None):
        """Neighbouring opaque fills of one colour: their masks united, one Background."""
        stem = self.stem(f"{owner}_Shapes")
        x = self.column()
        last = None
        for i, p in enumerate(prims, 1):
            extra = [] if last is None else [fc.input_("PaintMode", fc.fuid("Merge")), fc.mask(last)]
            name = f"{stem}Mask{i}"
            if p["kind"] == "ellipse":
                self.ellipse_mask(name, p["x"], p["y"], p["w"], p["h"], (x + 22 * (i - 1), 2 * ROW), extra=extra)
            else:
                self.rect_mask(name, p["x"], p["y"], p["w"], p["h"], p.get("radius") or 0.0, (x + 22 * (i - 1), 2 * ROW),
                               extra=extra)
            last = name
        self.paint(f"{stem}Fill", prims[0]["fill"], last, (x, 3 * ROW))
        return self.merge(f"{stem}On", under, f"{stem}Fill", (x, 330), self.blend(f"{stem}On", own))

    def put(self, owner, p, under, own=None):
        """A prim merged on: the tool now on top. own: its box's "own" on every frame
        (a box whose own prims don't show while what's in it does), else None. A prim
        that shows only on some frames (visible()) is faded by that as well."""
        shows = self.visible(p)
        if shows is not None:
            own = shows if own is None else [a * b for a, b in zip(own, shows)]
        for i, picture in enumerate(self.prim(owner, p)):
            under = self.merge(f"{picture}On", under, picture, (COLUMN * self.col, 330 + 11 * i),
                               self.blend(f"{picture}On", own))
        return under

    def blend(self, tool, values):
        """[a Merge's Blend] keyed from values, or [] for none or one that is always whole."""
        if values is None:
            return []
        made = self.keyed(tool, "Blend", values, ALPHA_TOL)
        return [] if made == fc.input_("Blend", 1.0) else [made]


class _Page:
    """One page's boxes: where they are, what's over what, how each one moves."""

    def __init__(self, comp, page):
        self.comp, self.page = comp, page
        self.id = page["id"]
        self.placed = {b["id"]: b for b in comp.solved[self.id]["boxes"]}
        self.looks = comp.samples["pages"][self.id]["boxes"]
        self.moving = {bid for bid, looks in self.looks.items() if _moves(looks)}
        self.label = _label(page["name"], "Page")
        self.names = {}
        self.drawn = _with_carets(styles.drawn(comp.spec, self.id, comp.solved), comp.samples["caret"])

    def owner(self, bid):
        """The name a box's tools start with: <Page>_<Box>."""
        if bid not in self.names:
            box = self.placed.get(bid)
            self.names[bid] = self.comp.stem(f"{self.label}_{_label(box['name'] if box else bid, 'Box')}") \
                if bid else self.label
        return self.names[bid]

    def chain(self, bid):
        """A box and the boxes it is in, itself first."""
        out = []
        while bid and bid in self.placed:
            out.append(bid)
            bid = self.placed[bid].get("parent") or ""
        return out

    def mover(self, bid, within):
        """The outermost moving box a prim of box `bid` is put on with, inside box
        `within` ("" for the page) - or None when nothing round it moves."""
        found = None
        for b in self.chain(bid):
            if b == within:
                break
            if b in self.moving:
                found = b
        return found

    def layer(self, prims, under, within="", own=None):
        """Prims (back to front) merged on in order onto `under`: the tool now on top. A
        moving box's prims and everything in it are put on together where its first one
        is (clock.carried: a box carries what's in it). within: the moving box these are
        the subtree of; own: its "own" on every frame, which its own prims fade by."""
        comp = self.comp
        done = set()
        fades = own is not None and any(abs(v - 1.0) > ALPHA_TOL for v in own)
        i = 0
        while i < len(prims):
            if i in done:
                i += 1
                continue
            p = prims[i]
            box = self.mover(p["box"], within)
            if box is not None:
                members = [j for j in range(i, len(prims)) if j not in done and box in self.chain(prims[j]["box"])]
                done.update(members)
                under = self.group(box, [prims[j] for j in members], under)
                i += 1
                continue
            mine = fades and p["box"] == within
            run, j = [p], i + 1
            if _plain(p):
                while (j < len(prims) and j not in done and _plain(prims[j]) and prims[j]["fill"] == p["fill"]
                       and self.mover(prims[j]["box"], within) is None and (prims[j]["box"] == within) == (p["box"] == within)):
                    run.append(prims[j])
                    j += 1
            if len(run) > 1:
                under = comp.run(self.owner(p["box"]), run, under, own if mine else None)
            else:
                under = comp.put(self.owner(p["box"]), p, under, own if mine else None)
            i = j
        return under

    def shape(self, bid):
        """(kind, x, y, w, h, radius, colour) of the shape a box is drawn as - its
        background - for its glow, or None for a box drawn as nothing of its own (a
        table's row, a list's item): it grows and lifts but has no shape to glow, as in
        the view (measured in Resolve, 2026-11-25: glowing its bare place put a soft
        slab behind the row that the preview never drew)."""
        b = self.placed[bid]
        for p in self.drawn:
            if p["box"] == bid and p["kind"] in ("rect", "ellipse", "image") and abs(p["x"] - b["x"]) < 0.6 \
                    and abs(p["y"] - b["y"]) < 0.6:
                return ("ellipse" if p["kind"] == "ellipse" else "rect", p["x"], p["y"], p["w"], p["h"], p.get("radius") or 0.0,
                        (p.get("fill") or p.get("stroke") or self.comp.spec["accent"])[:7])
        return None

    def group(self, bid, prims, under):
        """A moving box's prims - on a canvas of their own over its glow, when there is
        more than one picture - put on by a Merge whose Blend is the box's alpha and whose
        Size and Center size it (scale) about its middle and move it (dx, dy). A Merge
        sizes its foreground about the foreground's own middle (the frame's), so its
        Center makes up the difference: (1 - s) x the box's middle + s x 0.5 + the move.
        A "match" glide that stretches (sx != sy) is sized by a Transform before it
        instead (Size across, Aspect the height over that), the Merge only moving it."""
        comp, u = self.comp, self.comp.u
        looks = self.looks[bid]
        if max(looks["alpha"]) <= ALPHA_TOL:
            return under                                    # never seen
        n = self.owner(bid)
        x = comp.column()
        b = self.placed[bid]
        mx, my = u.point(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
        glows = max(looks["glow"]) > ALPHA_TOL and self.shape(bid) is not None
        aspect = [(sy / sx if sx > 0 else 1.0) for sx, sy in zip(looks["sx"], looks["sy"])]
        stretched = any(abs(a - 1.0) > SCALE_TOL for a in aspect)
        sizes = [s * a for s, a in zip(looks["scale"], looks["sx"])]
        alone = all(p["box"] == bid for p in prims)          # nothing in it is drawn
        alphas = [a * o for a, o in zip(looks["alpha"], looks["own"])] if alone else looks["alpha"]

        def put(top):
            extra = []
            if stretched:
                pos = (comp.column(), 264)
                comp.add(fc.block(f"{n}Stretch", "Transform", [
                    fc.feed("Input", top), fc.input_("Pivot", (mx, my)), comp.keyed(f"{n}Stretch", "Size", sizes, SCALE_TOL),
                    comp.keyed(f"{n}Stretch", "Aspect", aspect, SCALE_TOL)], pos))
                top, by = f"{n}Stretch", [1.0] * len(sizes)
            else:
                by = sizes
                size = comp.keyed(f"{n}On", "Size", by, SCALE_TOL)
                if size != fc.input_("Size", 1.0):
                    extra.append(size)
            cx = [(1 - s) * mx + s * 0.5 + u.wide(d) for s, d in zip(by, looks["dx"])]
            cy = [(1 - s) * my + s * 0.5 - u.high(d) for s, d in zip(by, looks["dy"])]
            centre = comp.keyed_point(f"{n}On", "Center", cx, cy, (comp.column(), 297))
            if centre != fc.input_("Center", (0.5, 0.5)):
                extra.append(centre)
            extra += comp.blend(f"{n}On", alphas)
            return comp.merge(f"{n}On", under, top, (comp.column(), 330), extra)
        if alone and not glows and len(prims) == 1 and _pictures(prims[0]) == 1 and comp.visible(prims[0]) is None:
            # One picture with nothing in it: its own Merge puts it on.
            pictures = comp.prim(n, prims[0])
            return put(pictures[0]) if pictures else under
        comp.add(fc.clear(f"{n}Canvas", comp.frame, comp.comp_frames, (x, 198)))
        top = f"{n}Canvas"
        if glows:
            kind, gx, gy, gw, gh, radius, colour = self.shape(bid)
            grow = GLOW_REACH * u.frame[0] / u.k * comp.spec["zoom"] / GLOW_ZOOM       # panel px
            soft = [fc.input_("SoftEdge", GLOW_SOFT * comp.spec["zoom"] / GLOW_ZOOM)]
            if kind == "ellipse":
                comp.ellipse_mask(f"{n}GlowMask", gx - grow, gy - grow, gw + 2 * grow, gh + 2 * grow, (x, 132), extra=soft)
            else:
                comp.rect_mask(f"{n}GlowMask", gx - grow, gy - grow, gw + 2 * grow, gh + 2 * grow,
                               radius + grow if radius > 0 else 0.0, (x, 132), extra=soft)
            comp.paint(f"{n}Glow", _lifted(colour), f"{n}GlowMask", (x, 165))
            top = comp.merge(f"{n}GlowOn", top, f"{n}Glow", (x, 231),
                             [comp.keyed(f"{n}GlowOn", "Blend", [min(1.0, max(0.0, g)) for g in looks["glow"]], ALPHA_TOL)])
        # Alone, its "own" is in the group's alpha already; else its own prims fade by it.
        return put(self.layer(prims, top, within=bid, own=None if alone else looks["own"]))


def _with_carets(prims, carets):
    """A page's prims with a caret prim ({"box", "kind": "caret", "fill"}) after the last
    line of each box whose caret ever shows - drawn, and carried, as the box's own."""
    out = list(prims)
    for bid in carets:
        last = max((i for i, p in enumerate(out) if p["box"] == bid and p["kind"] == "text"), default=None)
        if last is None:
            continue
        lines = [p for p in out if p["box"] == bid and p.get("type")]
        colour = (lines[-1] if lines else out[last])["fill"]
        out.insert(last + 1, {"box": bid, "kind": "caret", "fill": colour})
    return out


def _differs(chromes):
    """Of each page's chrome (lists of prims), the indices that differ between pages."""
    first = chromes[0]
    if any(len(c) != len(first) for c in chromes):
        return None
    return {i for i in range(len(first)) if any(c[i] != first[i] for c in chromes[1:])}


def _inside(comp):
    """The window's inside as a mask the pages are kept to: the room its pages have
    (solve's "area") cut to the window's body (the smallest of the chrome's filled
    rectangles holding all of it, with its rounded corners)."""
    ax, ay, aw, ah = comp.solved[comp.spec["start"]]["area"]
    body = None
    for p in styles.chrome(comp.spec, comp.spec["start"]):
        if p["kind"] == "rect" and p.get("fill") and p["x"] <= ax + 0.01 and p["y"] <= ay + 0.01 \
                and p["x"] + p["w"] >= ax + aw - 0.01 and p["y"] + p["h"] >= ay + ah - 0.01:
            if body is None or p["w"] * p["h"] < body["w"] * body["h"]:
                body = p
    comp.rect_mask("WindowInside", ax, ay, aw, ah, 0.0, (-110, 396))
    if body is None:
        return "WindowInside"
    comp.rect_mask("WindowInsideBody", body["x"], body["y"], body["w"], body["h"], body.get("radius") or 0.0, (-110, 429),
                   extra=[fc.input_("PaintMode", fc.fuid("Multiply")), fc.mask("WindowInside")])
    return "WindowInsideBody"


def _over(comp, shown):
    """{page id: [whether it is over a page after it in `shown`, per frame]} for the pages
    that ever are - pose() draws the page arriving over the one being left, and the comp's
    pages are merged in one order. Without this a change back to an earlier page would
    stack the other way, which a cross-fade shows wherever both pages draw (half way:
    0.5 of the one on top and 0.25 of the other, not the reverse)."""
    rank = {page["id"]: i for i, page in enumerate(shown)}
    out = {}
    for f, order in enumerate(comp.samples["order"]):
        for k, pid in enumerate(order):
            if pid in rank and any(rank.get(below, -1) > rank[pid] for below in order[:k]):
                out.setdefault(pid, [False] * comp.comp_frames)[f] = True
    return out


def _cursor(comp, under):
    """The cursor on top of everything: a pointer whose tip, or a dot whose middle, is
    where the clock says - a pointer CURSOR_DIP smaller while its button is down (as
    Interactions draws it)."""
    spec, u = comp.spec, comp.u
    look = CURSOR_LOOKS[spec["cursor"]]
    fw, fh = comp.frame
    size = spec["cursor_size"] / 100 * fh
    x = comp.column()
    points = [u.point(cx, cy) for cx, cy in comp.samples["cursor"]]
    where = comp.keyed_point("Cursor", "Path", [p[0] for p in points], [p[1] for p in points], (x, -66))
    where = where.replace("Path = ", "Center = ", 1)
    sized = comp.sized()
    if spec["cursor"] == "dot":
        width = look["width"] * size / fw
        comp.add(fc.block("CursorEdgeMask", "EllipseMask", [where, fc.input_("Width", width * 1.3),
                                                          fc.input_("Height", width * 1.3), *sized], (x, 0)))
        comp.add(fc.block("CursorMask", "EllipseMask", [where, fc.input_("Width", width), fc.input_("Height", width),
                                                      *sized], (x, 66)))
    else:
        outline = fc.polyline([(ax * size / fw, -ay * size / fh) for ax, ay in look["points"]], closed=True)
        dip = comp.keyed("Cursor", "Dip", [1 - CURSOR_DIP * d for d in comp.samples["down"]], SCALE_TOL)
        dip = dip.replace("Dip = ", "Size = ", 1)
        comp.add(fc.block("CursorEdgeMask", "PolylineMask", [where, fc.input_("Polyline", outline), dip,
                                                           fc.input_("BorderWidth", look["edge_w"] * size / fw), *sized],
                          (x, 0), '\t\t\tDrawMode = "ModifyOnly",\n'))
        comp.add(fc.block("CursorMask", "PolylineMask", [where, fc.input_("Polyline", outline), dip, *sized], (x, 66),
                          '\t\t\tDrawMode = "ModifyOnly",\n'))
    comp.paint("CursorEdge", look["edge"], "CursorEdgeMask", (x, 33))
    comp.paint("CursorFill", look["fill"], "CursorMask", (x, 99))
    under = comp.merge("CursorEdgeOn", under, "CursorEdge", (x, 330))
    return comp.merge("CursorOn", under, "CursorFill", (x + 55, 330))


def comp_text(spec, frame, fps, comp_frames, metrics=None):
    """The comp's text for a clip of comp_frames frames. metrics: {"Family|Style":
    {"em", "baseline"}} of the faces the text is set in, measured by the page."""
    spec = model.clean(spec)
    frame = (int(frame[0]), int(frame[1]))
    comp_frames = max(1, int(comp_frames))
    comp = _Comp(spec, frame, fps, comp_frames, metrics)
    comp.add(fc.clear("Canvas", frame, comp_frames, (0, 330)))
    under = "Canvas"
    shown = [page for page in spec["pages"] if comp.samples["pages"][page["id"]]["shown"]] \
        or [model.page_of(spec, spec["start"])]
    # Every name a page's tools start with, kept before any is used.
    window_label = comp.stem("Window")
    comp.stem("Cursor")
    labels = {page["id"]: comp.stem(_label(page["name"], "Page")) for page in shown}

    # The window: what every page's chrome has, then what differs, faded with its page.
    chromes = [styles.chrome(spec, page["id"]) for page in shown]
    differs = _differs(chromes)
    if differs is None:
        differs = set(range(len(chromes[0])))
    window = _Page(comp, shown[0])
    window.label = window_label
    window.moving = set()
    under = window.layer([p for i, p in enumerate(chromes[0]) if i not in differs], under)
    if differs:
        for page, prims in zip(shown, chromes):
            mine = [p for i, p in enumerate(prims) if i in differs] if len(prims) == len(chromes[0]) else prims
            if not mine:
                continue
            label = comp.stem(f"{labels[page['id']]}_Title")
            x = comp.column()
            comp.add(fc.clear(f"{label}Canvas", frame, comp_frames, (x, 198)))
            top = f"{label}Canvas"
            for p in mine:
                top = comp.put(label, p, top)
            blend = comp.keyed(f"{label}On", "Blend", comp.samples["pages"][page["id"]]["alpha"], ALPHA_TOL)
            under = comp.merge(f"{label}On", under, top, (x, 330), [] if blend == fc.input_("Blend", 1.0) else [blend])

    clip = _inside(comp)
    over = _over(comp, shown)
    tops = {}

    def page_on(name, top, sampled, alphas):
        # The page's slide is its Merge's Center, its fade the Blend; the window's inside keeps it.
        extra = [fc.mask(clip)]
        centre = comp.keyed_point(name, "Center", [0.5 + comp.u.wide(v) for v in sampled["dx"]], 0.5, (comp.column(), 297))
        if centre != fc.input_("Center", (0.5, 0.5)):
            extra.append(centre)
        return comp.merge(name, under, top, (comp.column(), 330), extra + comp.blend(name, alphas))
    for page in shown:
        sampled = comp.samples["pages"][page["id"]]
        here = _Page(comp, page)
        label = here.label = labels[page["id"]]
        prims = here.drawn[len(styles.chrome(spec, page["id"])):]
        x = comp.column()
        comp.add(fc.clear(f"{label}Canvas", frame, comp_frames, (x, 198)))
        top = tops[page["id"]] = here.layer(prims, f"{label}Canvas")
        lifted = over.get(page["id"])
        alphas = sampled["alpha"] if lifted is None else [0.0 if up else a for a, up in zip(sampled["alpha"], lifted)]
        under = page_on(f"{label}On", top, sampled, alphas)
    # A page arriving over one that comes after it here is put on again, above them all,
    # on the frames it does (and not by its own Merge then): pose's back to front.
    for page in shown:
        lifted = over.get(page["id"])
        if lifted is not None:
            sampled, label = comp.samples["pages"][page["id"]], labels[page["id"]]
            under = page_on(f"{label}OnTop", tops[page["id"]], sampled,
                            [a if up else 0.0 for a, up in zip(sampled["alpha"], lifted)])

    if spec["cursor"] != "none":
        under = _cursor(comp, under)
    comp.add(fc.media_out(under, (comp.column() + COLUMN, 330)))
    return fc.composition(comp.tools, comp_frames, {TAG: model.panel_id(spec, frame, fps)})
