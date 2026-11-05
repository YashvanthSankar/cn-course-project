"""Offscreen Qt tests for the canvas (scene rendering + tools) using a FakeController."""

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, Signal    # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent                             # noqa: E402
from PySide6.QtTest import QTest                                          # noqa: E402

from lanboard.common.model import Board                                    # noqa: E402


# ---------------------------------------------------------------------------
# a controller stand-in: same signals and methods, ops applied straight to a Board
# ---------------------------------------------------------------------------

class FakeController(QObject):
    reset = Signal()
    objects_changed = Signal(object)
    participants_changed = Signal()
    room_changed = Signal(dict)
    me_changed = Signal(dict)
    cursor_moved = Signal(str, float, float)
    live_stroke = Signal(str, dict)
    live_stroke_end = Signal(str, str)
    laser = Signal(str, list)
    viewport = Signal(str, dict)
    signal_received = Signal(str, str)
    blob_ready = Signal(str)
    notice = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.view = Board()
        self.user_id = "u1"
        self.me = {"user_id": "u1", "username": "alice", "color": "#e6194b", "role": "owner"}
        self.participants = {"u1": self.me, "u2": {"user_id": "u2", "username": "bob", "color": "#4363d8"}}
        self.calls = []                       # (method name, args)
        self.editable = True
        self.blobs = {}
        self._n = 0

    def _log(self, name, *args):
        self.calls.append((name, copy.deepcopy(args)))

    def called(self, name):
        return [a for n, a in self.calls if n == name]

    # identity / permissions
    def color_of(self, uid): return self.participants.get(uid, {}).get("color", "#888888")
    def name_of(self, uid): return self.participants.get(uid, {}).get("username", uid)
    def can_edit(self): return self.editable

    # editing
    def new_id(self):
        self._n += 1
        return f"u1-t{self._n}"

    def next_z(self): return self.view.max_z() + 1

    def submit(self, op, undoable=True):
        self._log("submit", op)
        ch = self.view.apply(op)
        if ch:
            self.objects_changed.emit(ch)
        return "op"

    def add_object(self, obj):
        self._log("add_object", obj)
        obj = copy.deepcopy(obj)
        obj.setdefault("id", self.new_id())
        obj.setdefault("z", self.next_z())
        obj["owner"] = self.user_id
        ch = self.view.apply({"k": "add", "obj": obj})
        if ch:
            self.objects_changed.emit(ch)

    def _update_op(self, oid, changes):
        return {"k": "update", "id": oid, "set": copy.deepcopy(changes), "prev": {}}

    def update_object(self, oid, changes):
        self._log("update_object", oid, changes)
        ch = self.view.apply(self._update_op(oid, changes))
        if ch:
            self.objects_changed.emit(ch)

    def update_many(self, changes):
        self._log("update_many", changes)
        ch = self.view.apply({"k": "batch", "ops": [self._update_op(o, c) for o, c in changes.items()]})
        if ch:
            self.objects_changed.emit(ch)

    def delete_objects(self, ids):
        self._log("delete_objects", list(ids))
        ch = self.view.apply({"k": "delete", "ids": list(ids)})
        if ch:
            self.objects_changed.emit(ch)

    # presence
    def send_cursor(self, x, y): self._log("send_cursor", x, y)
    def send_live(self, sid, color, width, pts, tool="pen"): self._log("send_live", sid, pts)
    def end_live(self, sid): self._log("end_live", sid)
    def send_laser(self, pts): self._log("send_laser", pts)
    def send_viewport(self, rect): self._log("send_viewport", rect)
    def send_signal(self, kind): self._log("send_signal", kind)

    # blobs
    def put_blob(self, data, mime):
        import hashlib
        h = hashlib.sha256(data).hexdigest()
        self.blobs[h] = data
        return h

    def blob_bytes(self, h): return self.blobs.get(h)


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    inst = QtWidgets.QApplication.instance()
    if inst is not None and not isinstance(inst, QtWidgets.QApplication):
        # Another test module created a bare QCoreApplication at import time;
        # widgets would abort the interpreter, so skip loudly instead.
        pytest.skip("a QCoreApplication already exists; canvas tests need a QApplication "
                    "(create QApplication in conftest.py or in that test module)")
    return inst or QtWidgets.QApplication([])


@pytest.fixture
def ctrl(app):
    return FakeController()


@pytest.fixture
def view(app, ctrl):
    from lanboard.client.canvas.view import CanvasView
    v = CanvasView(ctrl)
    v.resize(800, 600)
    v.show()
    QTest.qWaitForWindowExposed(v)
    v.centerOn(0, 0)
    yield v
    v.close()
    v.deleteLater()


def stroke(oid, pts, z=1, owner="u2"):
    return {"id": oid, "type": "stroke", "owner": owner, "z": z, "geom": {"pts": pts},
            "style": {"color": "#000000", "width": 4}, "off": [0, 0]}


def rect(oid, x, y, w, h, z=1):
    return {"id": oid, "type": "rect", "owner": "u2", "z": z, "geom": {"x": x, "y": y, "w": w, "h": h},
            "style": {"color": "#000000", "width": 3, "fill": "#ffcc00"}, "off": [0, 0]}


def vp_point(view, x, y) -> QPointF:
    """Scene point -> viewport position (float)."""
    return QPointF(view.mapFromScene(QPointF(x, y)))


def send_mouse(view, kind, pos: QPointF, button=Qt.MouseButton.LeftButton,
               buttons=Qt.MouseButton.LeftButton, mods=Qt.KeyboardModifier.NoModifier):
    vp = view.viewport()
    ev = QMouseEvent(kind, pos, vp.mapToGlobal(pos), button, buttons, mods)
    QtWidgets.QApplication.sendEvent(vp, ev)


def drag(view, pts, mods=Qt.KeyboardModifier.NoModifier):
    """Press at pts[0], move through the rest, release at the last point (scene coords)."""
    first = vp_point(view, *pts[0])
    send_mouse(view, QEvent.Type.MouseButtonPress, first, mods=mods)
    for x, y in pts[1:]:
        send_mouse(view, QEvent.Type.MouseMove, vp_point(view, x, y), button=Qt.MouseButton.NoButton, mods=mods)
    send_mouse(view, QEvent.Type.MouseButtonRelease, vp_point(view, *pts[-1]),
               buttons=Qt.MouseButton.NoButton, mods=mods)


def load(ctrl, objs):
    ctrl.view.apply({"k": "replace", "objects": objs}, seq=1)
    ctrl.reset.emit()


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def test_reset_builds_items(view, ctrl):
    from lanboard.client.canvas.items import ShapeItem, StickyItem, StrokeItem, TextItem, ImageItem
    load(ctrl, [stroke("a", [0, 0, 10, 10, 20, 0]), rect("b", 50, 50, 100, 60, z=2),
                {"id": "c", "type": "text", "z": 3, "geom": {"x": 0, "y": 100, "text": "hi"}, "style": {}},
                {"id": "d", "type": "sticky", "z": 4, "geom": {"x": 200, "y": 0, "w": 200, "h": 150, "text": "x"},
                 "style": {}, "votes": ["u2"]},
                {"id": "e", "type": "image", "z": 5, "geom": {"x": 0, "y": 300, "w": 50, "h": 50, "blob": "ff"},
                 "style": {}}])
    items = view.canvas.items_by_id
    assert set(items) == {"a", "b", "c", "d", "e"}
    assert isinstance(items["a"], StrokeItem) and isinstance(items["b"], ShapeItem)
    assert isinstance(items["c"], TextItem) and isinstance(items["d"], StickyItem)
    assert isinstance(items["e"], ImageItem)
    assert items["a"].zValue() < items["b"].zValue() < items["e"].zValue()     # paint order
    view.viewport().repaint()                                                   # paints without errors


def test_objects_changed_updates_and_removes(view, ctrl):
    load(ctrl, [stroke("a", [0, 0, 10, 10]), rect("b", 0, 0, 10, 10, z=2)])
    item_b = view.canvas.items_by_id["b"]
    ctrl.update_object("b", {"off": [30, 40], "style.color": "#ff0000"})
    assert view.canvas.items_by_id["b"] is item_b                              # updated in place
    assert (item_b.pos().x(), item_b.pos().y()) == (30, 40)
    assert item_b.obj["style"]["color"] == "#ff0000"
    ctrl.update_object("a", {"z": 10})
    assert view.canvas.items_by_id["a"].zValue() > item_b.zValue()             # restacked
    ctrl.delete_objects(["a"])
    assert "a" not in view.canvas.items_by_id
    ctrl.add_object(stroke("n", [5, 5, 6, 6]))
    assert "n" in view.canvas.items_by_id


def test_remote_live_stroke_replaced_by_commit(view, ctrl):
    ctrl.live_stroke.emit("u2", {"id": "u2-9", "color": "#00f", "width": 3, "pts": [0, 0, 5, 5]})
    ctrl.live_stroke.emit("u2", {"id": "u2-9", "color": "#00f", "width": 3, "pts": [9, 9]})
    assert ("u2", "u2-9") in view.canvas.live
    assert len(view.canvas.live[("u2", "u2-9")].points) == 6
    ctrl.view.apply({"k": "add", "obj": stroke("u2-9", [0, 0, 9, 9])})
    ctrl.objects_changed.emit({"u2-9"})
    assert ("u2", "u2-9") not in view.canvas.live
    ctrl.cursor_moved.emit("u2", 10.0, 20.0)
    assert "u2" in view.canvas.cursors
    ctrl.signal_received.emit("u2", "ping:100,200")
    assert view.canvas.pings and view.canvas.pings[0].pos() == QPointF(100, 200)


def test_spotlight_dims_other_users(view, ctrl):
    load(ctrl, [stroke("a", [0, 0, 10, 10], owner="u2"), stroke("b", [0, 0, 5, 5], owner="u1")])
    view.set_spotlight("u1")
    assert view.canvas.items_by_id["a"].opacity() == pytest.approx(0.15)
    assert view.canvas.items_by_id["b"].opacity() == pytest.approx(1.0)
    view.set_spotlight(None)
    assert view.canvas.items_by_id["a"].opacity() == pytest.approx(1.0)


def test_image_placeholder_then_blob_ready(view, ctrl):
    load(ctrl, [{"id": "img", "type": "image", "z": 1, "style": {},
                 "geom": {"x": 0, "y": 0, "w": 40, "h": 40, "blob": "abc"}}])
    item = view.canvas.items_by_id["img"]
    assert view.canvas.pixmap_for("abc", "img") is None
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.red)
    from lanboard.client.canvas.view import encode_image
    data, mime, _ = encode_image(img)
    ctrl.blobs["abc"] = data
    ctrl.blob_ready.emit("abc")
    assert view.canvas.pixmap_for("abc", "img") is not None
    item.update()


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def test_pen_drag_adds_stroke(view, ctrl):
    view.set_tool("pen")
    view.set_color("#123456")
    drag(view, [(0, 0), (10, 5), (20, 12), (30, 15), (40, 30)])
    adds = ctrl.called("add_object")
    assert len(adds) == 1
    obj = adds[0][0]
    assert obj["type"] == "stroke" and obj["style"]["color"] == "#123456"
    assert len(obj["geom"]["pts"]) >= 4 and len(obj["geom"]["pts"]) % 2 == 0
    assert "ts" in obj
    assert ctrl.called("send_live") and ctrl.called("end_live") == [(obj["id"],)]
    assert ctrl.called("send_cursor")
    assert obj["id"] in view.canvas.items_by_id                                 # rendered from the board


def test_highlighter_is_translucent(view, ctrl):
    view.set_tool("highlighter")
    drag(view, [(0, 0), (30, 0), (60, 0)])
    obj = ctrl.called("add_object")[0][0]
    assert obj["style"]["opacity"] == pytest.approx(0.35) and obj["style"]["width"] >= 14


def test_viewer_cannot_draw(view, ctrl):
    ctrl.editable = False
    ctrl.me_changed.emit({})
    view.set_tool("pen")
    drag(view, [(0, 0), (30, 30)])
    assert not ctrl.called("add_object")
    assert view.viewport().cursor().shape() == Qt.CursorShape.ForbiddenCursor


def test_eraser_drag_deletes(view, ctrl):
    load(ctrl, [stroke("a", [-50, 0, 50, 0]), stroke("b", [-50, 200, 50, 200], z=2)])
    view.set_tool("eraser")
    drag(view, [(0, -40), (0, -10), (0, 10), (0, 40)])
    assert ctrl.called("delete_objects") == [(["a"],)]
    assert "a" not in view.canvas.items_by_id and "b" in view.canvas.items_by_id


def test_shape_tool_adds_rect(view, ctrl):
    view.set_tool("rect")
    view.set_fill("#ff0000")
    drag(view, [(0, 0), (50, 20), (100, 60)])
    obj = ctrl.called("add_object")[0][0]
    assert obj["type"] == "rect" and obj["style"]["fill"] == "#ff0000"
    assert obj["geom"]["w"] == pytest.approx(100, abs=1) and obj["geom"]["h"] == pytest.approx(60, abs=1)


def test_select_drag_moves_with_off(view, ctrl):
    load(ctrl, [rect("r", 0, 0, 100, 100)])
    view.set_tool("select")
    drag(view, [(50, 50), (70, 60), (90, 80)])
    ups = ctrl.called("update_many")
    assert len(ups) == 1
    off = ups[0][0]["r"]["off"]
    assert off[0] == pytest.approx(40, abs=1) and off[1] == pytest.approx(30, abs=1)
    assert view.canvas.selected_ids == ["r"]
    it = view.canvas.items_by_id["r"]
    assert it.pos().x() == pytest.approx(40, abs=1)
    # recolour + delete through the selection
    view.set_color("#00ff00")
    assert ctrl.view.get("r")["style"]["color"] == "#00ff00"
    view.delete_selection()
    assert ctrl.called("delete_objects") == [(["r"],)]


def test_rubber_band_select(view, ctrl):
    load(ctrl, [rect("r1", 0, 0, 20, 20), rect("r2", 100, 100, 20, 20, z=2), rect("r3", 400, 400, 20, 20, z=3)])
    view.set_tool("select")
    drag(view, [(-10, -10), (60, 60), (150, 150)])
    assert view.canvas.selected_ids == ["r1", "r2"]


def test_sticky_vote_toggle(view, ctrl):
    load(ctrl, [{"id": "s", "type": "sticky", "z": 1, "style": {}, "votes": [],
                 "geom": {"x": 0, "y": 0, "w": 200, "h": 150, "text": "idea"}}])
    view.set_tool("select")
    it = view.canvas.items_by_id["s"]
    c = it.mapToScene(it.badge_rect().center())
    drag(view, [(c.x(), c.y())])
    assert ctrl.view.get("s")["votes"] == ["u1"]
    drag(view, [(c.x(), c.y())])
    assert ctrl.view.get("s")["votes"] == []


def test_text_tool_commit(view, ctrl):
    view.set_tool("text")
    drag(view, [(10, 10)])
    assert view.editor_open()
    view._editor.setPlainText("hello world")
    view.commit_editor()
    obj = ctrl.called("add_object")[0][0]
    assert obj["type"] == "text" and obj["geom"]["text"] == "hello world"


def test_alt_click_pings(view, ctrl):
    view.set_tool("pen")
    drag(view, [(12, 34)], mods=Qt.KeyboardModifier.AltModifier)
    assert ctrl.called("send_signal") == [("ping:12,34",)]
    assert not ctrl.called("add_object")


def test_presenting_sends_viewport(view, ctrl):
    view.set_presenting(True)
    assert ctrl.called("send_viewport")
    r = ctrl.called("send_viewport")[-1][0]
    assert r["w"] > 0 and r["h"] > 0


def test_zoom_clamped(view, ctrl):
    for _ in range(40):
        view.zoom_at(1.5, QPointF(400, 300))
    assert view.scale_factor() == pytest.approx(8.0)
    for _ in range(80):
        view.zoom_at(0.5, QPointF(400, 300))
    assert view.scale_factor() == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# preview (time-lapse) and export
# ---------------------------------------------------------------------------

def test_preview_board(view, ctrl):
    load(ctrl, [stroke("live", [0, 0, 10, 10])])
    b = Board()
    b.apply({"k": "add", "obj": rect("old", 0, 0, 10, 10)}, seq=7)
    view.show_preview_board(b)
    assert set(view.canvas.items_by_id) == {"old"}
    assert "seq 7" in view._badge.text() and view._badge.isVisible()
    # live changes are ignored while previewing, editing is disabled
    ctrl.add_object(stroke("x", [1, 1, 2, 2]))
    assert "x" not in view.canvas.items_by_id
    calls = len(ctrl.called("add_object"))
    view.set_tool("pen")
    drag(view, [(0, 0), (40, 40)])
    assert len(ctrl.called("add_object")) == calls
    # scrubbing to another board diffs incrementally
    b2 = b.copy()
    b2.apply({"k": "add", "obj": rect("old2", 20, 20, 10, 10, z=2)}, seq=8)
    view.show_preview_board(b2)
    assert set(view.canvas.items_by_id) == {"old", "old2"}
    view.show_preview_board(None)
    assert set(view.canvas.items_by_id) == {"live", "x"}
    assert not view._badge.isVisible()


@pytest.mark.parametrize("ext", [".png", ".svg", ".pdf"])
def test_export(view, ctrl, tmp_path, ext):
    load(ctrl, [stroke("a", [0, 0, 100, 50]), rect("b", 20, 20, 60, 40, z=2)])
    ctrl.cursor_moved.emit("u2", 10.0, 10.0)
    path = str(tmp_path / f"board{ext}")
    assert view.export(path)
    assert os.path.getsize(path) > 0
    if ext == ".png":
        img = QImage(path)
        assert img.width() == pytest.approx((100 + 4 + 80) * 2, abs=6)          # 2x scale, margins
        assert img.pixelColor(2, 2).name() == "#ffffff"                         # white background
    assert view.canvas.cursors["u2"].isVisible()                                 # overlays restored


def test_export_empty_board_fails_gracefully(view, ctrl):
    load(ctrl, [])
    assert view.export("/nonexistent/x.png") is False


def test_insert_image(view, ctrl, tmp_path):
    p = str(tmp_path / "pic.png")
    img = QImage(300, 200, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.blue)
    img.save(p)
    assert view.insert_image_file(p)
    obj = ctrl.called("add_object")[0][0]
    assert obj["type"] == "image" and obj["geom"]["blob"] in ctrl.blobs
    assert view.canvas.pixmap_for(obj["geom"]["blob"]) is not None
    assert not view.insert_image_file(str(tmp_path / "missing.png"))


def test_import_pdf(view, ctrl, tmp_path):
    pytest.importorskip("pypdfium2")
    from PySide6.QtGui import QPainter, QPdfWriter, QPageSize
    path = str(tmp_path / "deck.pdf")
    w = QPdfWriter(path)
    w.setPageSize(QPageSize(QPageSize.PageSizeId.A6))
    p = QPainter(w)
    p.drawText(100, 100, "page one")
    w.newPage()
    p.drawText(100, 100, "page two")
    p.end()
    notices = []
    ctrl.notice.connect(lambda lvl, txt: notices.append((lvl, txt)))
    assert view.import_pdf(path)
    batch = ctrl.called("submit")[-1][0]
    assert batch["k"] == "batch" and len(batch["ops"]) == 2              # one undo step
    a, b = (op["obj"] for op in batch["ops"])
    assert a["type"] == b["type"] == "image"
    assert b["geom"]["y"] > a["geom"]["y"] + a["geom"]["h"]                # stacked with a gap
    assert a["geom"]["w"] == pytest.approx(297.6, abs=2)                   # A6 width in points
    assert a["geom"]["blob"] in ctrl.blobs and set(view.canvas.items_by_id) >= {a["id"], b["id"]}
    pix = view.canvas.pixmap_for(a["geom"]["blob"])
    assert pix is not None and pix.width() == pytest.approx(297.6 * 1.5, abs=3)
    assert any("page 2/2" in t for _, t in notices)
    assert not view.import_pdf(str(tmp_path / "missing.pdf"))
    assert notices[-1][0] == "error"


# ---------------------------------------------------------------------------
# dark board (display-only remap), follow mode, 8-handle resize, minimap
# ---------------------------------------------------------------------------

def _pixel_near(img: QImage, x: int, y: int, r: int = 2):
    """Mean lightness of a small patch (robust against antialiasing)."""
    vals = [img.pixelColor(x + dx, y + dy).lightnessF() for dx in range(-r, r + 1) for dy in range(-r, r + 1)]
    return sum(vals) / len(vals)


def test_display_color_remap_rules(app):
    from PySide6.QtGui import QColor
    from lanboard.client.canvas.items import display_color
    assert display_color(QColor("#000000")).lightnessF() > 0.85          # black -> near-white
    assert display_color(QColor("#1b2130")).lightnessF() > 0.8
    assert display_color(QColor("#ffffff")).lightnessF() < 0.15          # white -> near-black
    for mid in ("#e5484d", "#0090ff", "#f5b400", "#30a46c"):            # colours stay as they are
        assert display_color(QColor(mid)).name() == mid


def test_dark_board_is_display_only(view, ctrl, tmp_path):
    ink = stroke("ink", [-200, 0, 200, 0])
    ink["style"]["width"] = 12
    load(ctrl, [ink, {"id": "t", "type": "text", "z": 2, "geom": {"x": -100, "y": 60, "text": "hello"},
                      "style": {"color": "#1b2130", "font_size": 30}, "off": [0, 0]}])
    before = copy.deepcopy(ctrl.view.snapshot())
    seen = []
    view.board_theme_changed.connect(seen.append)
    view.set_board_theme(True)
    assert seen == [True] and view.board_dark and view.canvas.ink_dark
    QTest.qWait(20)
    shot = view.grab().toImage()
    p = view.mapFromScene(QPointF(0, 0))
    assert _pixel_near(shot, p.x(), p.y()) > 0.8                       # black ink painted light
    assert shot.pixelColor(5, 5).lightnessF() < 0.15                   # dark paper
    # ...but the object data, the ops and the export are untouched
    assert ctrl.view.snapshot() == before
    assert not ctrl.called("update_many") and not ctrl.called("update_object")
    assert view.canvas.items_by_id["ink"].obj["style"]["color"] == "#000000"
    path = str(tmp_path / "dark.png")
    assert view.export(path)
    img = QImage(path)
    assert img.pixelColor(2, 2).name() == "#ffffff"                     # light export
    assert _pixel_near(img, img.width() // 2, int((40 + 6) * 2)) < 0.3  # ink still black in the file
    assert view.canvas.ink_dark                                         # restored after export
    view.set_grid("lines")
    assert view.grid_style == "lines"
    view.set_grid("bogus")
    assert view.grid_style == "dots"
    view.set_board_theme(False)
    assert seen == [True, False]


def test_follow_stops_on_user_pan_not_on_follow_animation(view, ctrl):
    changes, notices = [], []
    view.follow_changed.connect(changes.append)
    ctrl.notice.connect(lambda lvl, txt: notices.append(txt))
    view.set_follow("u2")
    assert changes == ["u2"] and "Following bob" in view._badge.text()
    # the presenter moves: programmatic follow animation must keep following
    ctrl.viewport.emit("u2", {"x": 500, "y": 400, "w": 800, "h": 600})
    QTest.qWait(400)
    assert view.following == "u2"
    assert view.visible_rect().center().x() == pytest.approx(900, abs=30)
    # hand-tool drag = user pan -> follow ends
    view.set_tool("pan")
    drag(view, [(900, 700), (850, 650), (800, 600)])
    assert view.following is None and changes == ["u2", None]
    assert any("Stopped following bob" in t for t in notices)
    assert not view._badge.isVisible()
    # wheel zoom, keyboard zoom, zoom_fit and the minimap all count as user moves too
    for act in (lambda: view.zoom_at(1.2, QPointF(400, 300)), view.zoom_in, view.zoom_fit, view.zoom_reset,
                lambda: view.pan_to(QPointF(10, 10))):
        view.set_follow("u2")
        act()
        assert view.following is None
    # followed user leaves -> stop as well
    view.set_follow("u2")
    del ctrl.participants["u2"]
    ctrl.participants_changed.emit()
    assert view.following is None


def test_edge_handle_resize_is_one_undoable_op(view, ctrl):
    load(ctrl, [rect("r", 0, 0, 100, 50), stroke("s", [0, 100, 100, 150], z=2)])
    view.set_tool("select")
    view.canvas.set_selection(["r", "s"])
    sel = view.canvas.selection
    pts = sel.handle_points()
    assert set(pts) == {"nw", "n", "ne", "e", "se", "s", "sw", "w"}
    # handles keep their screen size whatever the zoom
    hs = sel.HANDLE_PX / view.scale_factor()
    # drag the east edge handle 100 scene units to the right: the frame 0..100 becomes 0..200
    e = pts["e"]
    vp = view.viewport()
    send_mouse(view, QEvent.Type.MouseMove, vp_point(view, e.x(), e.y()), button=Qt.MouseButton.NoButton,
               buttons=Qt.MouseButton.NoButton)
    assert vp.cursor().shape() == Qt.CursorShape.SizeHorCursor
    drag(view, [(e.x(), e.y()), (e.x() + 50, e.y() + 30), (e.x() + 100, e.y() + 30)])
    calls = ctrl.called("update_many")
    assert len(calls) == 1                                             # ONE batch = one undo step
    ch = calls[0][0]
    assert ch["r"] == {"geom.x": 0, "geom.y": 0, "geom.w": 200, "geom.h": 50}   # height untouched
    assert ch["s"] == {"geom.pts": [0, 100, 200, 150]}                 # points scaled along x only
    assert ctrl.view.get("r")["geom"]["w"] == 200
    assert view.canvas.items_by_id["r"].obj["geom"]["w"] == 200
    # zoomed in, the handle squares shrink in scene units (constant on screen)
    view.zoom_at(2.0, QPointF(400, 300))
    assert sel.HANDLE_PX / view.scale_factor() == pytest.approx(hs / 2, rel=0.01)


def test_resize_clamps_and_keeps_aspect(view, ctrl):
    load(ctrl, [rect("r", 0, 0, 100, 50)])
    view.set_tool("select")
    view.canvas.set_selection(["r"])
    # drag the west edge far past the east edge: clamps to a minimum width, never negative
    w = view.canvas.selection.handle_points()["w"]
    drag(view, [(w.x(), w.y()), (w.x() + 400, w.y())])
    g = ctrl.view.get("r")["geom"]
    assert 0 < g["w"] <= 10 and g["x"] + g["w"] == pytest.approx(100, abs=0.2)
    # Shift + corner keeps the aspect ratio
    load(ctrl, [rect("r", 0, 0, 100, 50)])
    view.canvas.set_selection(["r"])
    se = view.canvas.selection.handle_points()["se"]
    drag(view, [(se.x(), se.y()), (se.x() + 100, se.y() + 10)], mods=Qt.KeyboardModifier.ShiftModifier)
    g = ctrl.view.get("r")["geom"]
    assert g["w"] == pytest.approx(200, abs=0.5) and g["h"] == pytest.approx(100, abs=0.5)


def test_resize_text_scales_font(view, ctrl):
    load(ctrl, [{"id": "t", "type": "text", "z": 1, "geom": {"x": 0, "y": 0, "text": "Hi there"},
                 "style": {"color": "#000", "font_size": 20}, "off": [0, 0]}])
    view.set_tool("select")
    view.canvas.set_selection(["t"])
    f = view.canvas.selection.frame_rect()
    se = view.canvas.selection.handle_points()["se"]
    drag(view, [(se.x(), se.y()), (se.x() + f.width(), se.y() + f.height())])
    ch = ctrl.called("update_many")[-1][0]["t"]
    assert ch["style.font_size"] == pytest.approx(40, abs=0.5)
    assert ch["geom.x"] == 0 and ch["geom.y"] == 0


def test_minimap_click_pans_view(view, ctrl):
    from lanboard.client.canvas.minimap import Minimap, ZoomControls
    load(ctrl, [rect("a", -1000, -600, 200, 100), rect("b", 1500, 900, 200, 100, z=2)])
    mm = Minimap(view)
    mm._refresh()                                                     # skip the 100 ms throttle
    target = QPointF(1600, 950)
    p = mm.scene_to_widget(target)
    view.set_follow("u2")
    QTest.mouseClick(mm, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p.toPoint())
    c = view.visible_rect().center()
    tol = 2 / mm._k() + 2                                              # one widget pixel in scene units
    assert c.x() == pytest.approx(1600, abs=tol) and c.y() == pytest.approx(950, abs=tol)
    assert view.following is None                                      # minimap pan is a user pan
    img = mm.grab().toImage()
    assert img.width() == Minimap.W
    zc = ZoomControls(view)
    zc.in_btn.click()
    assert zc.pct_btn.text() == f"{round(view.scale_factor() * 100)}%"
    view.set_board_theme(True)                                         # theme switch must not crash
    mm._refresh()
    mm.grab()
    mm.deleteLater()
    zc.deleteLater()
