"""Pure-Python 2D geometry used by the canvas (no Qt, so it can be unit tested).

Every point list here is a *flat* list ``[x0, y0, x1, y1, ...]``, which is the
same format a stroke's ``geom["pts"]`` uses on the wire. Rectangles are
``(x0, y0, x1, y1)`` tuples with ``x0 <= x1`` and ``y0 <= y1``.

Contents
  * polyline simplification: :func:`rdp` (Ramer-Douglas-Peucker), :func:`chaikin`, :func:`smooth`
  * object bounds and hit testing: :func:`bbox`, :func:`hit_test`, :func:`objects_in_rect`
  * Smart Ink shape recognition: :func:`recognize_shape`
  * small helpers the tools use: :func:`constrain_angle`, :func:`constrain_square`
  * resizing from the selection handles: :func:`resize_box`, :func:`scale_changes`
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

Rect = tuple[float, float, float, float]


def pairs(pts: Sequence[float]) -> list[tuple[float, float]]:
    """Flat list -> list of (x, y) tuples (a trailing odd number is ignored)."""
    return [(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]


def flatten(points: Iterable[tuple[float, float]]) -> list[float]:
    """List of (x, y) tuples -> flat list."""
    out: list[float] = []
    for x, y in points:
        out.append(x)
        out.append(y)
    return out


def dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


def dist_point_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Shortest distance from point P to the segment AB."""
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    if len2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_point_polyline(px: float, py: float, pts: Sequence[float]) -> float:
    """Shortest distance from a point to a flat polyline (a single point is allowed)."""
    n = len(pts) // 2
    if n == 0:
        return math.inf
    if n == 1:
        return math.hypot(px - pts[0], py - pts[1])
    best = math.inf
    for i in range(0, 2 * n - 2, 2):
        d = dist_point_segment(px, py, pts[i], pts[i + 1], pts[i + 2], pts[i + 3])
        if d < best:
            best = d
    return best


def path_length(pts: Sequence[float]) -> float:
    total = 0.0
    for i in range(0, len(pts) - 3, 2):
        total += math.hypot(pts[i + 2] - pts[i], pts[i + 3] - pts[i + 1])
    return total


def points_bounds(pts: Sequence[float]) -> Rect:
    xs, ys = pts[0::2], pts[1::2]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def rdp(pts: Sequence[float], eps: float) -> list[float]:
    """Ramer-Douglas-Peucker polyline simplification on a flat point list.

    Keeps the end points and every point that is further than ``eps`` from the
    simplified line. Implemented iteratively (an explicit stack) so very long
    strokes cannot hit Python's recursion limit.
    """
    n = len(pts) // 2
    if n <= 2 or eps <= 0:
        return list(pts[: 2 * n])
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay, bx, by = pts[2 * a], pts[2 * a + 1], pts[2 * b], pts[2 * b + 1]
        best_d, best_i = -1.0, -1
        for i in range(a + 1, b):
            d = dist_point_segment(pts[2 * i], pts[2 * i + 1], ax, ay, bx, by)
            if d > best_d:
                best_d, best_i = d, i
        if best_d > eps:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    out: list[float] = []
    for i in range(n):
        if keep[i]:
            out.append(pts[2 * i])
            out.append(pts[2 * i + 1])
    return out


def chaikin(pts: Sequence[float], iterations: int = 1, closed: bool = False) -> list[float]:
    """Chaikin corner cutting: each pass replaces every segment by its 1/4 and 3/4 points.

    Open polylines keep their first and last point so the stroke does not shrink.
    """
    p = pairs(pts)
    for _ in range(max(0, iterations)):
        if len(p) < 3:
            break
        q: list[tuple[float, float]] = [] if closed else [p[0]]
        m = len(p) if closed else len(p) - 1
        for i in range(m):
            (x0, y0), (x1, y1) = p[i], p[(i + 1) % len(p)]
            q.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            q.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        if not closed:
            q.append(p[-1])
        p = q
    return flatten(p)


def smooth(pts: Sequence[float], window: int = 2) -> list[float]:
    """Light moving-average smoothing that removes hand jitter. End points are kept."""
    p = pairs(pts)
    n = len(p)
    if n < 3 or window < 1:
        return list(pts[: 2 * n])
    out = [p[0]]
    for i in range(1, n - 1):
        lo, hi = max(0, i - window), min(n, i + window + 1)
        sx = sum(q[0] for q in p[lo:hi])
        sy = sum(q[1] for q in p[lo:hi])
        out.append((sx / (hi - lo), sy / (hi - lo)))
    out.append(p[-1])
    return flatten(out)


def round_pts(pts: Sequence[float], ndigits: int = 1) -> list[float]:
    return [round(v, ndigits) for v in pts]


def resample(pts: Sequence[float], count: int, closed: bool = False) -> list[tuple[float, float]]:
    """``count`` points spaced evenly along the polyline (by arc length)."""
    p = pairs(pts)
    if closed and p and p[0] != p[-1]:
        p = p + [p[0]]
    if len(p) < 2:
        return p * count if p else []
    seg = [math.hypot(p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1]) for i in range(len(p) - 1)]
    total = sum(seg)
    if total == 0:
        return [p[0]] * count
    step = total / (count if closed else max(1, count - 1))
    out, i, acc = [], 0, 0.0
    for k in range(count):
        target = k * step
        while i < len(seg) - 1 and acc + seg[i] < target:
            acc += seg[i]
            i += 1
        t = 0.0 if seg[i] == 0 else min(1.0, max(0.0, (target - acc) / seg[i]))
        out.append((p[i][0] + t * (p[i + 1][0] - p[i][0]), p[i][1] + t * (p[i + 1][1] - p[i][1])))
    return out


def _off(obj: dict) -> tuple[float, float]:
    off = obj.get("off") or (0, 0)
    return float(off[0]), float(off[1])


def text_size(text: str, font_size: float) -> tuple[float, float]:
    """Rough text extent without font metrics (the canvas uses real metrics for drawing)."""
    lines = (text or " ").split("\n")
    return max(len(s) for s in lines) * font_size * 0.6 + 4, len(lines) * font_size * 1.35 + 4


def _width(style: dict) -> float:
    """Stroke width from a style dict (missing -> 2; an explicit 0 stays 0)."""
    w = style.get("width")
    return 2.0 if w is None else float(w)


def bbox(obj: dict, include_width: bool = True) -> Rect:
    """Axis-aligned bounds of an object in scene coordinates (``off`` applied).

    With ``include_width`` the stroke/outline thickness is added, so the box
    covers every painted pixel.
    """
    g, style = obj.get("geom", {}), obj.get("style", {}) or {}
    t = obj.get("type")
    dx, dy = _off(obj)
    if t == "stroke":
        x0, y0, x1, y1 = points_bounds(g.get("pts") or [0, 0])
    elif t in ("line", "arrow"):
        x0, x1 = sorted((g["x1"], g["x2"]))
        y0, y1 = sorted((g["y1"], g["y2"]))
    elif t == "text":
        w, h = text_size(g.get("text", ""), float(style.get("font_size") or 20))
        x0, y0, x1, y1 = g["x"], g["y"], g["x"] + w, g["y"] + h
    else:
        x0, x1 = sorted((g["x"], g["x"] + g["w"]))
        y0, y1 = sorted((g["y"], g["y"] + g["h"]))
    pad = 0.0
    if include_width and t in ("stroke", "line", "arrow", "rect", "ellipse"):
        width = _width(style)
        pad = width / 2
        if t == "arrow":
            pad += 4 + 3 * width
    return (x0 + dx - pad, y0 + dy - pad, x1 + dx + pad, y1 + dy + pad)


def rects_intersect(a: Rect, b: Rect) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def rect_contains(outer: Rect, inner: Rect) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def normalise_rect(x0: float, y0: float, x1: float, y1: float) -> Rect:
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _ellipse_outline_distance(px: float, py: float, cx: float, cy: float, a: float, b: float) -> float:
    """Approximate distance from P to the ellipse outline (first-order |F|/|grad F|)."""
    a, b = max(a, 1e-6), max(b, 1e-6)
    u, v = px - cx, py - cy
    f = (u * u) / (a * a) + (v * v) / (b * b) - 1.0
    gx, gy = 2 * u / (a * a), 2 * v / (b * b)
    g = math.hypot(gx, gy)
    if g < 1e-9:
        return min(a, b)
    return abs(f) / g


def _inside_ellipse(px: float, py: float, cx: float, cy: float, a: float, b: float) -> bool:
    a, b = max(a, 1e-6), max(b, 1e-6)
    return ((px - cx) / a) ** 2 + ((py - cy) / b) ** 2 <= 1.0


def hit_test(obj: dict, x: float, y: float, radius: float = 4.0) -> bool:
    """True if a circle of ``radius`` at (x, y) touches the painted object.

    * stroke, line, arrow: distance to the polyline <= radius + width/2
    * rect, ellipse: distance to the outline (or anywhere inside when filled)
    * sticky, image, text: anywhere inside the bounds (they are opaque blocks)
    """
    t = obj.get("type")
    g, style = obj.get("geom", {}), obj.get("style", {}) or {}
    dx, dy = _off(obj)
    px, py = x - dx, y - dy
    half = _width(style) / 2
    if t == "stroke":
        pts = g.get("pts") or []
        x0, y0, x1, y1 = points_bounds(pts)
        r = radius + half
        if px < x0 - r or px > x1 + r or py < y0 - r or py > y1 + r:
            return False
        return dist_point_polyline(px, py, pts) <= r
    if t in ("line", "arrow"):
        return dist_point_segment(px, py, g["x1"], g["y1"], g["x2"], g["y2"]) <= radius + half + (
            2 * half if t == "arrow" else 0)
    if t in ("rect", "ellipse"):
        x0, y0, x1, y1 = normalise_rect(g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"])
        filled = bool(style.get("fill"))
        if t == "rect":
            inside = x0 <= px <= x1 and y0 <= py <= y1
            if filled and inside:
                return True
            d = min(dist_point_segment(px, py, x0, y0, x1, y0), dist_point_segment(px, py, x1, y0, x1, y1),
                    dist_point_segment(px, py, x1, y1, x0, y1), dist_point_segment(px, py, x0, y1, x0, y0))
            return d <= radius + half
        cx, cy, a, b = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
        if filled and _inside_ellipse(px, py, cx, cy, a, b):
            return True
        return _ellipse_outline_distance(px, py, cx, cy, a, b) <= radius + half
    if t in ("sticky", "image", "text"):
        x0, y0, x1, y1 = bbox(obj, include_width=False)
        return x0 - radius <= x <= x1 + radius and y0 - radius <= y <= y1 + radius
    return False


def hit_test_segment(obj: dict, ax: float, ay: float, bx: float, by: float, radius: float) -> bool:
    """Hit test along a swept segment (a fast eraser drag must not skip objects)."""
    steps = max(1, int(math.hypot(bx - ax, by - ay) / max(radius * 0.75, 0.5)))
    for i in range(steps + 1):
        t = i / steps
        if hit_test(obj, ax + (bx - ax) * t, ay + (by - ay) * t, radius):
            return True
    return False


def objects_in_rect(objects: Iterable[dict], rect: Rect, contain: bool = False) -> list[str]:
    """Ids of objects whose bounds intersect (or, with ``contain``, lie fully inside) ``rect``."""
    rect = normalise_rect(*rect)
    out = []
    for o in objects:
        if o.get("deleted"):
            continue
        b = bbox(o)
        if (rect_contains(rect, b) if contain else rects_intersect(rect, b)):
            out.append(o["id"])
    return out


def constrain_angle(x1: float, y1: float, x2: float, y2: float, step_deg: float = 45.0) -> tuple[float, float]:
    """Snap the end point so the line angle is a multiple of ``step_deg``."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
        return x2, y2
    step = math.radians(step_deg)
    ang = round(math.atan2(dy, dx) / step) * step
    return x1 + length * math.cos(ang), y1 + length * math.sin(ang)


def constrain_square(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float]:
    """Make the drag box square (keeps the drag direction)."""
    dx, dy = x2 - x1, y2 - y1
    s = max(abs(dx), abs(dy))
    return x1 + math.copysign(s, dx or 1), y1 + math.copysign(s, dy or 1)


HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
MIN_FONT, MAX_FONT = 4.0, 400.0
_EPS = 1e-6


def resize_box(box: Rect, handle: str, dx: float, dy: float, keep_aspect: bool = False,
               min_size: float = 1.0) -> Rect:
    """Move the edges of ``box`` that belong to ``handle`` by the drag delta (dx, dy).

    * ``"e"`` moves the right edge, ``"nw"`` the top and left edges, and so on.
    * An edge never crosses the opposite one: the box is clamped to ``min_size``
      instead of turning inside out, so sizes can never become negative.
    * An axis with zero extent (e.g. the height of a horizontal line) is left
      alone, because there is nothing to scale along it.
    * ``keep_aspect`` (Shift, or images) only applies to corner handles: the box
      grows by the larger of the two scale factors and stays anchored at the
      opposite corner.
    """
    x0, y0, x1, y1 = box
    w0, h0 = x1 - x0, y1 - y0
    if w0 > _EPS:
        if "w" in handle:
            x0 = min(x0 + dx, x1 - min_size)
        elif "e" in handle:
            x1 = max(x1 + dx, x0 + min_size)
    if h0 > _EPS:
        if "n" in handle:
            y0 = min(y0 + dy, y1 - min_size)
        elif "s" in handle:
            y1 = max(y1 + dy, y0 + min_size)
    if keep_aspect and len(handle) == 2 and w0 > _EPS and h0 > _EPS:
        s = max((x1 - x0) / w0, (y1 - y0) / h0)
        w, h = w0 * s, h0 * s
        if "w" in handle:
            x0 = x1 - w
        else:
            x1 = x0 + w
        if "n" in handle:
            y0 = y1 - h
        else:
            y1 = y0 + h
    return (x0, y0, x1, y1)


def scale_changes(obj: dict, src: Rect, dst: Rect, handle: str = "se") -> dict:
    """Update keys (``"geom.x"`` …) that map ``obj`` from box ``src`` onto box ``dst``.

    Both boxes are in scene coordinates. The object's ``off`` is left unchanged,
    so only ``geom`` (and a text's font size) change. Every point is mapped with
    ``x' = dst.x0 + (x - src.x0) * sx``:

    * stroke: every point of ``pts`` (the ink width is not scaled)
    * line / arrow: both end points
    * rect / ellipse / sticky / image: x, y, w, h
    * text: its origin moves, and ``font_size`` is scaled by ``sx`` for the
      e/w handles, ``sy`` for n/s and ``sqrt(sx*sy)`` for corners
    """
    sw, sh = src[2] - src[0], src[3] - src[1]
    sx = (dst[2] - dst[0]) / sw if sw > _EPS else 1.0
    sy = (dst[3] - dst[1]) / sh if sh > _EPS else 1.0
    ox, oy = _off(obj)

    def mx(x: float) -> float:
        return round(dst[0] + (float(x) + ox - src[0]) * sx - ox, 1)

    def my(y: float) -> float:
        return round(dst[1] + (float(y) + oy - src[1]) * sy - oy, 1)

    g, t = obj.get("geom", {}), obj.get("type")
    if t == "stroke":
        pts = g.get("pts") or []
        return {"geom.pts": [mx(v) if i % 2 == 0 else my(v) for i, v in enumerate(pts)]}
    if t in ("line", "arrow"):
        return {"geom.x1": mx(g["x1"]), "geom.y1": my(g["y1"]), "geom.x2": mx(g["x2"]), "geom.y2": my(g["y2"])}
    if t == "text":
        f = sx if handle in ("e", "w") else sy if handle in ("n", "s") else math.sqrt(sx * sy)
        size = float((obj.get("style") or {}).get("font_size") or 20)
        return {"geom.x": mx(g["x"]), "geom.y": my(g["y"]),
                "style.font_size": round(max(MIN_FONT, min(MAX_FONT, size * f)), 1)}
    return {"geom.x": mx(g["x"]), "geom.y": my(g["y"]),
            "geom.w": round(float(g["w"]) * sx, 1), "geom.h": round(float(g["h"]) * sy, 1)}


def _turn_angle(ax, ay, bx, by, cx, cy) -> float:
    """Absolute turning angle (radians) at B when walking A -> B -> C."""
    v1x, v1y, v2x, v2y = bx - ax, by - ay, cx - bx, cy - by
    n1, n2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
    if n1 == 0 or n2 == 0:
        return 0.0
    c = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (n1 * n2)))
    return math.acos(c)


def _polygon_area(p: list[tuple[float, float]]) -> float:
    """Shoelace area (absolute) of a closed polygon given as a point list."""
    s = 0.0
    for i in range(len(p)):
        x0, y0 = p[i]
        x1, y1 = p[(i + 1) % len(p)]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2


def _corner_count(pts: Sequence[float], eps: float, closed: bool, min_turn_deg: float = 50.0) -> int:
    """Number of sharp corners after RDP simplification."""
    simp = pairs(rdp(pts, eps))
    if closed and len(simp) > 2 and math.hypot(simp[0][0] - simp[-1][0], simp[0][1] - simp[-1][1]) < eps * 3:
        simp = simp[:-1]
    n = len(simp)
    if n < 3:
        return 0
    rng = range(n) if closed else range(1, n - 1)
    lim = math.radians(min_turn_deg)
    cnt = 0
    for i in rng:
        a, b, c = simp[i - 1], simp[i], simp[(i + 1) % n]
        if _turn_angle(*a, *b, *c) > lim:
            cnt += 1
    return cnt


def _rounded(d: dict) -> dict:
    return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()}


def recognize_shape(pts: Sequence[float]) -> dict | None:
    """Classify a freehand stroke as a clean shape, or return ``None`` to keep the ink.

    Returns ``{"type": "line"|"arrow"|"rect"|"ellipse", "geom": {...}}``.

    Heuristics (all scale invariant, so they work at any zoom):
      * **line**: the end-to-end distance is almost the whole path length
        (straightness >= 0.93) and no point strays far from the chord.
      * **arrow**: after RDP, one long first segment (the shaft) followed by a
        short "V" hook at its end (the head drawn in the same stroke).
      * closed shapes need the gap between the ends to be small relative to the
        perimeter (closure ratio). Then:
          - **rect**: the stroke fills its bounding box (shoelace area / bbox area
            >= 0.86), most points hug the box edges and RDP finds about 4 corners.
            (A square's normalised radius varies by ~0.10, so it is never an ellipse.)
          - **ellipse**: the fill ratio is near pi/4 (0.785) and the normalised
            radius sqrt(((x-cx)/a)^2+((y-cy)/b)^2) barely varies (relative std <= 0.07).
      * anything else (triangles, scribbles, letters) is left alone.
    """
    p = pairs(pts)
    if len(p) < 4:
        return None
    x0, y0, x1, y1 = points_bounds(pts)
    w, h = x1 - x0, y1 - y0
    diag = math.hypot(w, h)
    length = path_length(pts)
    if diag < 12 or length < 16:
        return None
    (sx, sy), (ex, ey) = p[0], p[-1]
    chord = math.hypot(ex - sx, ey - sy)

    if chord / length >= 0.93:
        dev = max(dist_point_segment(x, y, sx, sy, ex, ey) for x, y in p)
        if dev <= max(0.08 * chord, 3.0):
            return {"type": "line", "geom": _rounded({"x1": float(sx), "y1": float(sy),
                                                      "x2": float(ex), "y2": float(ey)})}

    simp = pairs(rdp(pts, max(0.04 * diag, 2.0)))
    if len(simp) >= 4:
        (ax, ay), (bx, by) = simp[0], simp[1]
        shaft = math.hypot(bx - ax, by - ay)
        rest = path_length(flatten(simp[1:]))
        if shaft >= 0.55 * length and shaft > 0 and rest > 0.12 * shaft:
            ux, uy = (bx - ax) / shaft, (by - ay) / shaft
            near = all(math.hypot(x - bx, y - by) <= 0.45 * shaft for x, y in simp[1:])
            back = [((x - bx) * ux + (y - by) * uy) for x, y in simp[2:]]
            if near and back and min(back) < -0.08 * shaft:
                return {"type": "arrow", "geom": _rounded({"x1": float(ax), "y1": float(ay),
                                                           "x2": float(bx), "y2": float(by)})}

    closure = chord / length
    if closure > 0.2 or chord > 0.35 * diag:
        return None
    if min(w, h) < 0.08 * max(w, h):
        return None
    sm = smooth(smooth(pts, 2), 2)
    x0, y0, x1, y1 = points_bounds(sm)
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    poly = resample(sm, 96, closed=False)
    fill_ratio = _polygon_area(poly) / max(w * h, 1e-9)
    cx, cy, a, b = (x0 + x1) / 2, (y0 + y1) / 2, w / 2, h / 2
    radii = [math.sqrt(((x - cx) / a) ** 2 + ((y - cy) / b) ** 2) for x, y in poly]
    mean_r = sum(radii) / len(radii)
    r_std = math.sqrt(sum((r - mean_r) ** 2 for r in radii) / len(radii)) / max(mean_r, 1e-9)
    tol = 0.07
    edge = sum(1 for x, y in poly
               if min(x - x0, x1 - x) / w < tol or min(y - y0, y1 - y) / h < tol) / len(poly)
    corners = _corner_count(sm, max(0.05 * diag, 2.0), closed=True)
    geom = _rounded({"x": float(x0), "y": float(y0), "w": float(w), "h": float(h)})

    if fill_ratio >= 0.86 and edge >= 0.8 and 3 <= corners <= 6:
        return {"type": "rect", "geom": geom}
    if 0.66 <= fill_ratio < 0.88 and r_std <= 0.07 and edge < 0.85:
        return {"type": "ellipse", "geom": geom}
    return None
