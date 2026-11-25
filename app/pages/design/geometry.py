"""A design's shapes as outlines: every vector layer as bezier points on the canvas,
turned about its middle - what a PolylineMask is written from (comp.py). The canvas in
Buddy draws the same shapes by the same rules (web/design.js: outlineOf). No Qt, no
Resolve.

An outline is {"closed", "points": [(x, y, ix, iy, ox, oy)]}: each point's place, and
its handles as offsets from it (0, 0 for a corner) - the curve arriving uses (ix, iy),
the one leaving (ox, oy).

A layer with "flipx" or "flipy" has its own picture mirrored about its middle, across or
up and down, before it is turned by "rot" (flipped() says how a flip gets there).
"""

import math

KAPPA = 0.5522847498            # a quarter circle's handle, as a share of its radius


def radius_of(layer):
    """A rectangle's corner radius as drawn: never more than half its shorter side."""
    return max(0.0, min(float(layer.get("radius") or 0.0), layer["w"] / 2, layer["h"] / 2))


def radii_of(layer):
    """A box's four corner radii as drawn - top left, top right, bottom right, bottom left
    of its own picture: its "radii" when it has them, else its "radius" at every corner.
    Two corners on one side never take more than the side between them: all four are
    made smaller together until they fit, as CSS does (so one radius is radius_of's)."""
    w, h = layer["w"], layer["h"]
    raw = layer.get("radii")
    radii = [max(0.0, float(r)) for r in raw] if raw else [max(0.0, float(layer.get("radius") or 0.0))] * 4
    tl, tr, br, bl = radii
    k = min([1.0] + [side / pair for side, pair in ((w, tl + tr), (w, bl + br), (h, tl + bl), (h, tr + br)) if pair > 0])
    return [r * k for r in radii]


def _box(a, b, radii):
    """A box's outline, its corners rounded: from the end of the top left corner's curve,
    round clockwise. A corner not rounded is one point."""
    tl, tr, br, bl = radii
    out = []
    if tl > 0:
        out.append((-a + tl, -b, -tl * KAPPA, 0, 0, 0))
    else:
        out.append((-a, -b, 0, 0, 0, 0))
    for (x, y), r, (fx, fy), (tx, ty) in (((a, -b), tr, (-1, 0), (0, 1)), ((a, b), br, (0, -1), (-1, 0)),
                                          ((-a, b), bl, (1, 0), (0, -1))):
        if r > 0:
            # (fx, fy): back along the side arriving; (tx, ty): on along the side leaving.
            k = r * KAPPA
            out.append((x + fx * r, y + fy * r, 0, 0, -fx * k, -fy * k))
            out.append((x + tx * r, y + ty * r, -tx * k, -ty * k, 0, 0))
        else:
            out.append((x, y, 0, 0, 0, 0))
    if tl > 0:
        out.append((-a, -b + tl, 0, 0, 0, -tl * KAPPA))
    return out


def rounded(points, r):
    """Straight-sided corners [(x, y), ...] (a closed outline) rounded by r px: each
    corner cut back along both its sides and joined by a circle's arc as a bezier, as
    far as half the shorter side lets it. design.js's roundedPoints is the same."""
    if r <= 0:
        return [(x, y, 0, 0, 0, 0) for x, y in points]
    n, out = len(points), []
    for i, (vx, vy) in enumerate(points):
        (px, py), (nx, ny) = points[i - 1], points[(i + 1) % n]
        l1, l2 = math.hypot(px - vx, py - vy), math.hypot(nx - vx, ny - vy)
        if l1 < 1e-9 or l2 < 1e-9:
            out.append((vx, vy, 0, 0, 0, 0))
            continue
        u1x, u1y, u2x, u2y = (px - vx) / l1, (py - vy) / l1, (nx - vx) / l2, (ny - vy) / l2
        theta = math.acos(max(-1.0, min(1.0, u1x * u2x + u1y * u2y)))      # the angle in the corner
        if theta < 1e-6 or theta > math.pi - 1e-6:
            out.append((vx, vy, 0, 0, 0, 0))
            continue
        half = math.tan(theta / 2)
        cut = min(r / half, l1 / 2, l2 / 2)
        k = 4 / 3 * math.tan((math.pi - theta) / 4) * cut * half
        out.append((vx + u1x * cut, vy + u1y * cut, 0, 0, -u1x * k, -u1y * k))
        out.append((vx + u2x * cut, vy + u2y * cut, -u2x * k, -u2y * k, 0, 0))
    return out


def _local(layer):
    """The layer's outline about its own middle, before it is turned."""
    kind, w, h = layer["kind"], layer["w"], layer["h"]
    a, b = w / 2, h / 2
    if kind in ("rect", "image"):
        return True, _box(a, b, radii_of(layer))
    if kind == "ellipse":
        ka, kb = a * KAPPA, b * KAPPA
        return True, [(0, -b, -ka, 0, ka, 0), (a, 0, 0, -kb, 0, kb), (0, b, ka, 0, -ka, 0), (-a, 0, 0, kb, 0, -kb)]
    if kind == "line":
        return False, [(-a, 0, 0, 0, 0, 0), (a, 0, 0, 0, 0, 0)]
    if kind in ("polygon", "star"):
        n = layer["sides"]
        steps = n * 2 if kind == "star" else n
        corners_ = []
        for i in range(steps):
            angle = -math.pi / 2 + 2 * math.pi * i / steps
            reach = layer["inner"] / 100 if kind == "star" and i % 2 else 1.0
            corners_.append((a * reach * math.cos(angle), b * reach * math.sin(angle)))
        return True, rounded(corners_, float(layer.get("radius") or 0.0))
    if kind == "path":
        return layer["closed"], [((p["x"] - 0.5) * w, (p["y"] - 0.5) * h, p["ix"] * w, p["iy"] * h,
                                  p["ox"] * w, p["oy"] * h) for p in layer["points"]]
    raise ValueError(f"A {kind} has no outline.")


def flips(layer):
    """(sx, sy): -1 the way a layer's own picture is mirrored, 1 the way it isn't."""
    return -1.0 if layer.get("flipx") else 1.0, -1.0 if layer.get("flipy") else 1.0


def outline(layer):
    """The layer's outline on the canvas, design px: mirrored as it is flipped, then turned."""
    closed, points = _local(layer)
    fx, fy = flips(layer)
    turn = math.radians(layer["rot"])
    c, s = math.cos(turn), math.sin(turn)

    def turned(x, y):
        x, y = x * fx, y * fy
        return x * c - y * s, x * s + y * c
    out = []
    for x, y, ix, iy, ox, oy in points:
        px, py = turned(x, y)
        out.append((layer["x"] + px, layer["y"] + py, *turned(ix, iy), *turned(ox, oy)))
    return {"closed": closed, "points": out}


def flipped(layer, axis, middle):
    """The layer mirrored about a line on the canvas - the same rule as design.js's flip().
    axis "x" flips it across (left for right) about the upright line x = middle; "y" up and
    down about the level line y = middle.

    A layer is drawn as its own picture, mirrored by its flips, turned by rot, moved to its
    place: P = place + R(rot) F p. A mirror M across the canvas (diag(-1, 1) or diag(1, -1))
    turns the other way, M R(r) = R(-r) M, and M F is F with that flip toggled, so the
    mirrored layer is the same layer with
        its place mirrored         x -> 2 * middle - x (or y)
        its turn negated           rot -> -rot (mod 360)
        that flip toggled          flipx (or flipy)
    whichever axis is flipped. Flipped both ways a picture is turned half round,
    diag(-1, -1) = R(180): flipx and flipy together are kept as neither, with rot + 180
    (model.clean does the same). So a layer flipped twice the same way is as it was - or,
    one flipped the other way before, the same picture written the other way round (flipx
    turned r is flipy turned r + 180).
    A line is the same mirrored (only its turn changes), so it keeps no flips.

    Anything with children has each mirrored about the same line; a boolean's own
    gradient (drawn in its straight box) turns to match: angle -> 180 - angle across,
    -angle up and down (mod 360)."""
    out = dict(layer)
    if "children" in layer:
        out["children"] = [flipped(child, axis, middle) for child in layer["children"]]
        if isinstance(layer.get("fill"), dict):
            angle = layer["fill"].get("angle", 0.0)
            out["fill"] = {**layer["fill"], "angle": round(((180.0 - angle) if axis == "x" else -angle) % 360.0, 3)}
        return out
    out[axis] = round(2 * middle - layer[axis], 3)
    out["rot"] = round(-layer["rot"] % 360.0, 3)
    if layer["kind"] != "line":
        key = "flip" + axis
        if out.get(key):
            del out[key]
        else:
            out[key] = True
        if out.get("flipx") and out.get("flipy"):
            del out["flipx"], out["flipy"]
            out["rot"] = round((out["rot"] + 180.0) % 360.0, 3)
    return out


def corners(layer):
    """The four corners of a layer's box on the canvas, turned."""
    turn = math.radians(layer["rot"])
    c, s = math.cos(turn), math.sin(turn)
    a, b = layer["w"] / 2, layer["h"] / 2
    return [(layer["x"] + x * c - y * s, layer["y"] + x * s + y * c) for x, y in ((-a, -b), (a, -b), (a, b), (-a, b))]


def bounds(layers):
    """(left, top, right, bottom) round the boxes of some layers."""
    xs, ys = zip(*[p for layer in layers for p in corners(layer)])
    return min(xs), min(ys), max(xs), max(ys)


def gradient_line(box, rot, angle, flip=(1.0, 1.0)):
    """((x0, y0), (x1, y1)) on the canvas: where a linear fill starts and ends. box: the
    layer's (x, y, w, h); the fill runs at `angle` (0 left to right, 90 top to bottom)
    across the box - from one side of it to the other, as CSS does - and is mirrored and
    turned with it (flip: the layer's flips())."""
    x, y, w, h = box
    a = math.radians(angle)
    dx, dy = math.cos(a) * flip[0], math.sin(a) * flip[1]
    half = (abs(w * dx) + abs(h * dy)) / 2
    turn = math.radians(rot)
    c, s = math.cos(turn), math.sin(turn)
    ex, ey = dx * half, dy * half
    tx, ty = ex * c - ey * s, ex * s + ey * c
    return (x - tx, y - ty), (x + tx, y + ty)


def fit(size, frame):
    """(scale, dx, dy): how a design of `size` sits on a frame - as large as fits,
    centred. A design made at the timeline's own size is 1, 0, 0."""
    k = min(frame[0] / size[0], frame[1] / size[1])
    return k, (frame[0] - size[0] * k) / 2, (frame[1] - size[1] * k) / 2
