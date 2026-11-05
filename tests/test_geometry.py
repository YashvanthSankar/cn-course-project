"""Unit tests for lanboard.common.geometry (pure Python, no Qt)."""

import math
import random

import pytest

from lanboard.common import geometry as G


# ---------------------------------------------------------------------------
# synthetic stroke generators (flat point lists, like a mouse would produce)
# ---------------------------------------------------------------------------

def circle(cx=200, cy=150, rx=80, ry=80, n=90, start=0.3, sweep=2 * math.pi * 1.02, noise=0.0, seed=1):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        t = start + sweep * i / (n - 1)
        out += [cx + rx * math.cos(t) + rnd.gauss(0, noise), cy + ry * math.sin(t) + rnd.gauss(0, noise)]
    return out


def polyline(corners, per_side=25, noise=0.0, seed=2):
    rnd = random.Random(seed)
    out = []
    for (ax, ay), (bx, by) in zip(corners, corners[1:]):
        for i in range(per_side):
            t = i / per_side
            out += [ax + (bx - ax) * t + rnd.gauss(0, noise), ay + (by - ay) * t + rnd.gauss(0, noise)]
    out += list(corners[-1])
    return out


def rect_stroke(x=50, y=40, w=220, h=140, noise=0.0, seed=3):
    c = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x + 3, y + 2)]   # closes near the start
    return polyline(c, noise=noise, seed=seed)


# ---------------------------------------------------------------------------
# rdp / smoothing
# ---------------------------------------------------------------------------

def test_rdp_collinear_collapses_to_endpoints():
    pts = [float(i) for x in range(11) for i in (x, 2 * x)]           # points on y = 2x
    assert G.rdp(pts, 0.5) == [0.0, 0.0, 10.0, 20.0]


def test_rdp_keeps_corner():
    pts = polyline([(0, 0), (100, 0), (100, 100)], per_side=20)
    out = G.pairs(G.rdp(pts, 1.0))
    assert out[0] == (0, 0) and out[-1] == (100, 100)
    assert (100.0, 0.0) in out and len(out) == 3


def test_rdp_short_and_degenerate_inputs():
    assert G.rdp([], 1) == []
    assert G.rdp([1, 2], 1) == [1, 2]
    assert G.rdp([1, 2, 3, 4], 1) == [1, 2, 3, 4]
    long = [float(v) for i in range(20000) for v in (i, math.sin(i / 50) * 30)]
    assert len(G.rdp(long, 0.5)) < len(long)                            # no recursion error


def test_chaikin_and_smooth_preserve_ends():
    pts = [0, 0, 10, 0, 10, 10]
    c = G.chaikin(pts, 2)
    assert c[:2] == [0, 0] and c[-2:] == [10, 10] and len(c) > len(pts)
    s = G.smooth(polyline([(0, 0), (50, 0)], noise=2.0))
    assert s[:2] != [] and len(s) % 2 == 0


# ---------------------------------------------------------------------------
# bounds and hit testing
# ---------------------------------------------------------------------------

def _obj(t, geom, **style):
    return {"id": t, "type": t, "geom": geom, "style": style, "off": [0, 0]}


def test_bbox_includes_off():
    o = _obj("rect", {"x": 10, "y": 20, "w": 30, "h": 40}, width=0)
    o["off"] = [5, -5]
    assert G.bbox(o) == (15, 15, 45, 55)
    s = _obj("stroke", {"pts": [0, 0, 10, 10]}, width=4)
    assert G.bbox(s) == (-2, -2, 12, 12)


def test_hit_test_stroke_uses_segment_distance():
    s = _obj("stroke", {"pts": [0, 0, 100, 0, 100, 100]}, width=4)
    assert G.hit_test(s, 50, 3, 2)          # near first segment (within radius + width/2)
    assert not G.hit_test(s, 50, 10, 2)
    assert G.hit_test(s, 102, 50, 1)
    assert not G.hit_test(s, 50, 50, 5)     # inside the corner but far from ink
    s["off"] = [0, 50]
    assert G.hit_test(s, 50, 52, 2)


def test_hit_test_shapes():
    r = _obj("rect", {"x": 0, "y": 0, "w": 100, "h": 50}, width=2)
    assert G.hit_test(r, 0, 25, 2) and not G.hit_test(r, 50, 25, 2)   # outline only
    r["style"]["fill"] = "#ff0000"
    assert G.hit_test(r, 50, 25, 2)                                     # filled -> inside hits
    e = _obj("ellipse", {"x": 0, "y": 0, "w": 100, "h": 50}, width=2)
    assert G.hit_test(e, 100, 25, 2) and G.hit_test(e, 50, 0.5, 2)
    assert not G.hit_test(e, 50, 25, 2) and not G.hit_test(e, 98, 2, 2)
    ln = _obj("line", {"x1": 0, "y1": 0, "x2": 100, "y2": 100}, width=2)
    assert G.hit_test(ln, 50, 51, 2) and not G.hit_test(ln, 50, 70, 2)
    st = _obj("sticky", {"x": 0, "y": 0, "w": 200, "h": 150, "text": "hi"})
    assert G.hit_test(st, 100, 75, 0) and not G.hit_test(st, 250, 75, 10)
    tx = _obj("text", {"x": 0, "y": 0, "text": "hello"}, font_size=20)
    assert G.hit_test(tx, 10, 10, 0)


def test_hit_test_segment_catches_fast_drag():
    s = _obj("stroke", {"pts": [50, -100, 50, 100]}, width=2)
    assert not G.hit_test(s, 0, 0, 5) and not G.hit_test(s, 100, 0, 5)
    assert G.hit_test_segment(s, 0, 0, 100, 0, 5)


def test_objects_in_rect():
    objs = [_obj("rect", {"x": 0, "y": 0, "w": 10, "h": 10}, width=0),
            dict(_obj("ellipse", {"x": 100, "y": 100, "w": 10, "h": 10}, width=0), id="e")]
    assert G.objects_in_rect(objs, (-5, -5, 20, 20)) == ["rect"]
    assert G.objects_in_rect(objs, (5, 5, 200, 200)) == ["rect", "e"]
    assert G.objects_in_rect(objs, (5, 5, 200, 200), contain=True) == ["e"]


def test_constraints():
    x, y = G.constrain_angle(0, 0, 100, 10)
    assert abs(y) < 1e-9 and x == pytest.approx(math.hypot(100, 10))
    x, y = G.constrain_angle(0, 0, 100, 90)
    assert x == pytest.approx(y)
    assert G.constrain_square(0, 0, 50, -20) == (50, -50)


# ---------------------------------------------------------------------------
# Smart Ink
# ---------------------------------------------------------------------------

def test_recognize_line():
    r = G.recognize_shape(polyline([(10, 10), (300, 120)]))
    assert r["type"] == "line"
    assert (r["geom"]["x1"], r["geom"]["y1"], r["geom"]["x2"], r["geom"]["y2"]) == (10, 10, 300, 120)


def test_recognize_noisy_line():
    r = G.recognize_shape(polyline([(0, 0), (400, 0)], noise=2.5, seed=7))
    assert r and r["type"] == "line"


@pytest.mark.parametrize("noise", [0.0, 2.0, 4.0])
def test_recognize_circle(noise):
    r = G.recognize_shape(circle(noise=noise))
    assert r and r["type"] == "ellipse"
    g = r["geom"]
    assert g["w"] == pytest.approx(160, abs=12) and g["h"] == pytest.approx(160, abs=12)
    assert g["x"] == pytest.approx(120, abs=8)


@pytest.mark.parametrize("noise", [0.0, 2.0])
def test_recognize_elongated_ellipse(noise):
    r = G.recognize_shape(circle(rx=150, ry=50, noise=noise, seed=4))
    assert r and r["type"] == "ellipse"


def test_recognize_slightly_open_circle():
    r = G.recognize_shape(circle(sweep=2 * math.pi * 0.93, noise=1.0))
    assert r and r["type"] == "ellipse"


@pytest.mark.parametrize("noise", [0.0, 2.0, 3.5])
def test_recognize_rect(noise):
    r = G.recognize_shape(rect_stroke(noise=noise, seed=int(noise * 10)))
    assert r and r["type"] == "rect"
    g = r["geom"]
    assert g["w"] == pytest.approx(220, abs=12) and g["h"] == pytest.approx(140, abs=12)


def test_recognize_arrow():
    pts = polyline([(0, 0), (300, 0), (270, -25), (300, 0), (270, 25)], per_side=20)
    r = G.recognize_shape(pts)
    assert r and r["type"] == "arrow"
    assert r["geom"]["x2"] == pytest.approx(300, abs=10) and r["geom"]["x1"] == pytest.approx(0, abs=1)


@pytest.mark.parametrize("pts", [
    polyline([(0, 0), (200, 0), (100, 170), (2, 2)]),                        # triangle
    polyline([(0, 0), (100, 0), (200, 100), (0, 100), (200, 0)]),              # zigzag
    circle(sweep=math.pi),                                                    # half circle (open arc)
    [0, 0, 3, 3, 5, 5, 6, 7],                                                 # tiny scribble
])
def test_recognize_rejects_other_strokes(pts):
    r = G.recognize_shape(pts)
    assert r is None or r["type"] not in ("rect", "ellipse")
    if pts is not None and len(pts) < 10:
        assert r is None
