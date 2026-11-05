"""Interactive tools for the canvas.

The golden rule: **tools never mutate board items**. While dragging they show
a local preview (an overlay item). When the gesture ends they call a
controller method (``add_object``, ``update_many``, ``delete_objects``…). The
controller applies the op optimistically to its view board, and the scene
re-renders from that, exactly as it would for a remote user's op.

Each tool receives scene coordinates and the original Qt mouse event from
:class:`~lanboard.client.canvas.view.CanvasView`, which also takes care of
panning, zooming, permissions and the ``send_cursor`` presence updates.
"""

from __future__ import annotations

import copy
import math
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QCursor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsRectItem

from lanboard.common import config as C
from lanboard.common import geometry as G
from lanboard.common.model import set_key

from .items import (ACCENT, HANDLE_CURSORS, OVERLAY_Z, STICKY_FILL, STICKY_TEXT, LaserItem, LiveStrokeItem,
                    PulseItem, ShapeItem, StickyItem, TextItem, cosmetic_pen, qcolor)
from .scene import item_hit

if TYPE_CHECKING:                                    # pragma: no cover
    from .view import CanvasView

HOLD_MS = 450                       # Smart Ink: hold the pen still this long before release
HOLD_SLOP_PX = 4.0                  # "still" = moved less than this many screen pixels
DRAG_SLOP_PX = 3.0                  # a click that moves less than this is not a drag
MIN_RESIZE_PX = 8.0                 # a resized selection never gets smaller than this on screen
HIGHLIGHTER_OPACITY = 0.35
HIGHLIGHTER_MIN_WIDTH = 14


def dot_cursor(color: str, radius: int = 6) -> QCursor:
    """A round cursor (used by the laser pointer)."""
    size = radius * 2 + 6
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = qcolor(color, "#ff3b30")
    glow = QColor(c)
    glow.setAlpha(70)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(glow)
    p.drawEllipse(QPointF(size / 2, size / 2), radius + 2, radius + 2)
    p.setBrush(c)
    p.setPen(QPen(QColor("#ffffff"), 1.5))
    p.drawEllipse(QPointF(size / 2, size / 2), radius - 1, radius - 1)
    p.end()
    return QCursor(pix, size // 2, size // 2)


class Tool:
    """Base class. ``edits`` tools are disabled when ``controller.can_edit()`` is False."""

    name = ""
    edits = True

    def __init__(self, view: "CanvasView") -> None:
        self.view = view

    @property
    def ctrl(self):
        return self.view.ctrl

    @property
    def scene(self):
        return self.view.canvas

    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.CrossCursor)

    # gesture hooks: ``pos`` is in scene coordinates
    def press(self, pos: QPointF, ev) -> None: ...
    def move(self, pos: QPointF, ev) -> None: ...
    def release(self, pos: QPointF, ev) -> None: ...
    def hover(self, pos: QPointF, ev) -> None: ...
    def double_click(self, pos: QPointF, ev) -> None: ...

    def activate(self) -> None: ...

    def deactivate(self) -> None:
        self.cancel()

    def cancel(self) -> None:
        """Abort an in-progress gesture without committing anything."""


# ===========================================================================
# freehand: pen + highlighter (with Smart Ink)
# ===========================================================================

class PenTool(Tool):
    name = "pen"
    highlighter = False

    def __init__(self, view) -> None:
        super().__init__(view)
        self.sid: str | None = None
        self.pts: list[float] = []
        self.preview: LiveStrokeItem | None = None
        self.hold = QTimer()
        self.hold.setSingleShot(True)
        self.hold.timeout.connect(self._on_hold)
        self._anchor = QPointF()          # screen position the hold timer measures from
        self._snap: dict | None = None    # recognised shape, waiting for release
        self._ghost: ShapeItem | None = None
        self._pulse: PulseItem | None = None

    # style of the stroke being drawn
    def _style(self) -> tuple[str, float, float]:
        v = self.view
        if self.highlighter:
            return v.color, float(max(HIGHLIGHTER_MIN_WIDTH, v.pen_width * 4)), HIGHLIGHTER_OPACITY
        return v.color, float(v.pen_width), 1.0

    def press(self, pos, ev) -> None:
        color, width, opacity = self._style()
        self.sid = self.ctrl.new_id()
        self.pts = [round(pos.x(), 2), round(pos.y(), 2)]
        self.preview = LiveStrokeItem(color, width, opacity)
        self.scene.addItem(self.preview)
        self.preview.append(self.pts)
        self.ctrl.send_live(self.sid, color, width, list(self.pts), self.name)
        self._anchor = ev.position()
        self.hold.start(HOLD_MS)

    def move(self, pos, ev) -> None:
        if self.sid is None:
            return
        # skip sub-pixel jitter: at 120 Hz the mouse often reports the same spot
        min_d = 0.6 / self.view.scale_factor()
        if math.hypot(pos.x() - self.pts[-2], pos.y() - self.pts[-1]) < min_d:
            return
        if len(self.pts) >= C.MAX_STROKE_POINTS * 4:
            return                                   # absurdly long stroke: stop growing
        new = [round(pos.x(), 2), round(pos.y(), 2)]
        self.pts += new
        self.preview.append(new)
        color, width, _ = self._style()
        self.ctrl.send_live(self.sid, color, width, new, self.name)   # controller batches ~30 Hz
        d = ev.position() - self._anchor
        if abs(d.x()) + abs(d.y()) > HOLD_SLOP_PX:
            self._anchor = ev.position()
            self._clear_snap()
            self.hold.start(HOLD_MS)                 # restart the "held still" countdown

    def _on_hold(self) -> None:
        """The pen has been still for HOLD_MS: preview the Smart Ink snap."""
        if self.sid is None or self.highlighter or not self.view.smart_ink or len(self.pts) < 8:
            return
        shape = G.recognize_shape(self.pts)
        if not shape:
            return
        self._snap = shape
        color, width, _ = self._style()
        obj = self._shape_obj(shape, color, width)
        self._ghost = ShapeItem(obj, self.scene)
        self._ghost.setZValue(OVERLAY_Z - 5)
        self._ghost.is_overlay = True               # type: ignore[attr-defined]
        self.scene.addItem(self._ghost)
        self.preview.setOpacity(0.18)
        self._pulse = PulseItem(ACCENT)
        self._pulse.setPos(self.pts[-2], self.pts[-1])
        self.scene.add_animated(self._pulse)

    def _clear_snap(self) -> None:
        self._snap = None
        if self._ghost is not None:
            self.scene.removeItem(self._ghost)
            self._ghost = None
        if self._pulse is not None:
            self.scene.remove_animated(self._pulse)
            self._pulse = None
        if self.preview is not None:
            self.preview.setOpacity(self._style()[2])

    def _shape_obj(self, shape: dict, color: str, width: float) -> dict:
        fill = self.view.fill if shape["type"] in ("rect", "ellipse") else None
        return {"id": self.sid, "type": shape["type"], "geom": dict(shape["geom"]),
                "style": {"color": color, "width": width, "fill": fill}, "off": [0, 0],
                "ts": round(time.time(), 1)}

    def release(self, pos, ev) -> None:
        if self.sid is None:
            return
        self.hold.stop()
        color, width, opacity = self._style()
        if self._snap is not None:
            obj = self._shape_obj(self._snap, color, width)
        else:
            obj = {"id": self.sid, "type": "stroke", "geom": {"pts": self._simplified()},
                   "style": {"color": color, "width": width, "opacity": opacity}, "off": [0, 0],
                   "ts": round(time.time(), 1)}
            if self.highlighter:
                obj["style"]["tool"] = "highlighter"
        sid = self.sid
        self.ctrl.add_object(obj)
        self.ctrl.end_live(sid)
        self._finish()

    def _simplified(self) -> list[float]:
        """RDP with ~0.6 screen px tolerance, rounded to 0.1 to keep ops small."""
        eps = 0.6 / self.view.scale_factor()
        pts = G.round_pts(G.rdp(self.pts, eps), 1)
        while len(pts) > C.MAX_STROKE_POINTS:        # very long stroke: simplify harder
            eps *= 2
            pts = G.round_pts(G.rdp(self.pts, eps), 1)
        return pts if len(pts) >= 2 else self.pts[:2]

    def _finish(self) -> None:
        self._clear_snap()
        if self.preview is not None:
            self.scene.removeItem(self.preview)
        self.preview, self.sid, self.pts = None, None, []

    def cancel(self) -> None:
        if self.sid is not None:
            self.ctrl.end_live(self.sid)
        self.hold.stop()
        self._finish()


class HighlighterTool(PenTool):
    name = "highlighter"
    highlighter = True


# ===========================================================================
# eraser
# ===========================================================================

class EraserTool(Tool):
    """Drag a circle. Touched objects vanish at once; release = one delete op (one undo)."""

    name = "eraser"

    def __init__(self, view) -> None:
        super().__init__(view)
        self.ring = QGraphicsEllipseItem()
        self.ring.setZValue(OVERLAY_Z)
        self.apply_theme(False)
        self.ring.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.ring.is_overlay = True                  # type: ignore[attr-defined]
        self.hits: list[str] = []
        self.active = False
        self._last = QPointF()

    def apply_theme(self, dark: bool) -> None:
        """Dark ring on the light board, light ring on the dark board."""
        if dark:
            self.ring.setPen(cosmetic_pen(QColor(225, 229, 238, 210), 1.4))
            self.ring.setBrush(QBrush(QColor(255, 255, 255, 28)))
        else:
            self.ring.setPen(cosmetic_pen(QColor(60, 64, 72, 200), 1.4))
            self.ring.setBrush(QBrush(QColor(255, 255, 255, 90)))

    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.BlankCursor)

    def radius_px(self) -> float:
        return 6.0 + self.view.pen_width * 1.6

    def _radius(self) -> float:
        return self.radius_px() / self.view.scale_factor()

    def _show_ring(self, pos: QPointF) -> None:
        if self.ring.scene() is None:
            self.scene.addItem(self.ring)
        r = self._radius()
        self.ring.setRect(QRectF(pos.x() - r, pos.y() - r, 2 * r, 2 * r))
        self.ring.setVisible(True)

    def activate(self) -> None:
        self.ring.setVisible(False)

    def deactivate(self) -> None:
        self.cancel()
        if self.ring.scene() is not None:
            self.scene.removeItem(self.ring)

    def hover(self, pos, ev) -> None:
        self._show_ring(pos)

    def press(self, pos, ev) -> None:
        self.active, self.hits, self._last = True, [], pos
        self._show_ring(pos)
        self._erase(pos, pos)

    def move(self, pos, ev) -> None:
        self._show_ring(pos)
        if self.active:
            self._erase(self._last, pos)
            self._last = pos

    def _erase(self, a: QPointF, b: QPointF) -> None:
        r = self._radius()
        sweep = QRectF(a, b).normalized().adjusted(-r, -r, r, r)
        board = self.ctrl.view                     # hit test against the authoritative view board
        newly = []
        for it in self.scene.candidates_in(sweep):
            obj = board.get(it.oid) or it.obj
            if it.obj.get("type") == "text":
                hit = item_hit(it, b.x(), b.y(), r) or item_hit(it, a.x(), a.y(), r)
            else:
                hit = G.hit_test_segment(obj, a.x(), a.y(), b.x(), b.y(), r)
            if hit:
                newly.append(it.oid)
        if newly:
            self.hits += newly
            self.scene.hide_ids(newly)

    def release(self, pos, ev) -> None:
        if not self.active:
            return
        self.active = False
        if self.hits:
            self.ctrl.delete_objects(list(dict.fromkeys(self.hits)))
        self.hits = []
        self.scene.clear_hidden()

    def cancel(self) -> None:
        self.active = False
        self.hits = []
        self.scene.clear_hidden()


# ===========================================================================
# shapes: line, arrow, rect, ellipse
# ===========================================================================

class ShapeTool(Tool):
    """Rubber-band shapes. Shift snaps lines to 45 degrees and boxes to squares and circles."""

    def __init__(self, view, kind: str) -> None:
        super().__init__(view)
        self.name = kind
        self.start: QPointF | None = None
        self.ghost: ShapeItem | None = None
        self._screen_start = QPointF()

    def _obj(self, a: QPointF, b: QPointF, shift: bool) -> dict:
        v = self.view
        style = {"color": v.color, "width": float(v.pen_width), "fill": v.fill if self.name in ("rect", "ellipse") else None}
        if self.name in ("line", "arrow"):
            x2, y2 = (G.constrain_angle(a.x(), a.y(), b.x(), b.y()) if shift else (b.x(), b.y()))
            geom = {"x1": round(a.x(), 1), "y1": round(a.y(), 1), "x2": round(x2, 1), "y2": round(y2, 1)}
        else:
            x2, y2 = (G.constrain_square(a.x(), a.y(), b.x(), b.y()) if shift else (b.x(), b.y()))
            x0, y0, x1, y1 = G.normalise_rect(a.x(), a.y(), x2, y2)
            geom = {"x": round(x0, 1), "y": round(y0, 1), "w": round(x1 - x0, 1), "h": round(y1 - y0, 1)}
        return {"id": "__ghost__", "type": self.name, "geom": geom, "style": style, "off": [0, 0]}

    def press(self, pos, ev) -> None:
        self.start = pos
        self._screen_start = ev.position()
        self.ghost = ShapeItem(self._obj(pos, pos, False), self.scene)
        self.ghost.setZValue(OVERLAY_Z - 5)
        self.ghost.is_overlay = True               # type: ignore[attr-defined]
        self.scene.addItem(self.ghost)

    def move(self, pos, ev) -> None:
        if self.start is None:
            return
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.ghost.set_object(self._obj(self.start, pos, shift))

    def release(self, pos, ev) -> None:
        if self.start is None:
            return
        d = ev.position() - self._screen_start
        if math.hypot(d.x(), d.y()) >= DRAG_SLOP_PX:
            shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            obj = self._obj(self.start, pos, shift)
            obj["id"] = self.ctrl.new_id()
            obj["ts"] = round(time.time(), 1)
            self.ctrl.add_object(obj)
        self.cancel()

    def cancel(self) -> None:
        if self.ghost is not None:
            self.scene.removeItem(self.ghost)
        self.ghost, self.start = None, None


# ===========================================================================
# text and sticky notes
# ===========================================================================

class TextTool(Tool):
    name = "text"

    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.IBeamCursor)

    def press(self, pos, ev) -> None:
        if self.view.editor_open():
            self.view.commit_editor()
            return
        it = self.scene.object_item_at(pos, 3 / self.view.scale_factor())
        if isinstance(it, (TextItem, StickyItem)):
            self.view.edit_object(it.oid)
        else:
            self.view.open_new_text(pos)


class StickyTool(Tool):
    name = "sticky"
    W, H = 200.0, 150.0

    def press(self, pos, ev) -> None:
        if self.view.editor_open():
            self.view.commit_editor()
            return
        it = self.scene.object_item_at(pos, 2 / self.view.scale_factor())
        if isinstance(it, StickyItem):
            self.view.edit_object(it.oid)
            return
        oid = self.ctrl.new_id()
        fill = self.view.fill or STICKY_FILL
        self.ctrl.add_object({
            "id": oid, "type": "sticky", "off": [0, 0], "ts": round(time.time(), 1), "votes": [],
            "geom": {"x": round(pos.x() - self.W / 2, 1), "y": round(pos.y() - self.H / 2, 1),
                     "w": self.W, "h": self.H, "text": ""},
            "style": {"fill": fill, "color": STICKY_TEXT, "font_size": 16},
        })
        # the add was applied to the view synchronously, so the item exists now
        QTimer.singleShot(0, lambda: self.view.edit_object(oid))


# ===========================================================================
# select / move / resize
# ===========================================================================

class SelectTool(Tool):
    """Click, shift-click and rubber-band selection, then drag to move or resize.

    Resizing works from any of the 8 handles of the selection frame, for one
    object or many. The frame's logical box ``F0`` is mapped onto the dragged
    box ``F1`` (:func:`geometry.resize_box`) and every selected object is
    scaled with the same mapping (:func:`geometry.scale_changes`): boxes change
    x/y/w/h, strokes / lines / arrows scale their points, text scales its font.
    During the drag only the local items are updated (a preview). On release
    all changes go out as one ``update_many`` batch, so it is one undo step.
    """

    name = "select"
    edits = False                                   # viewers may select (but not move)

    def __init__(self, view) -> None:
        super().__init__(view)
        self.mode: str | None = None                # "move" | "band" | "resize" | None
        self.origin = QPointF()
        self._screen_origin = QPointF()
        self.start_pos: dict[str, QPointF] = {}
        self.band = QGraphicsRectItem()
        self.band.setZValue(OVERLAY_Z)
        self.band.setPen(cosmetic_pen(ACCENT, 1.0, Qt.PenStyle.DashLine))
        fill = qcolor(ACCENT)
        fill.setAlpha(28)
        self.band.setBrush(QBrush(fill))
        self.band.is_overlay = True                 # type: ignore[attr-defined]
        self._base_sel: list[str] = []
        self._handle: str | None = None              # "nw" | "n" | … while resizing
        self._resize_src: dict[str, dict] = {}        # oid -> object before the resize
        self._box0: G.Rect = (0.0, 0.0, 0.0, 0.0)     # frame box before the resize

    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.ArrowCursor)

    def _r(self) -> float:
        return 4.0 / self.view.scale_factor()

    def hover(self, pos, ev) -> None:
        h = self.scene.selection.handle_at(pos) if self.view.can_edit() else None
        if h is not None:
            self.view.viewport().setCursor(HANDLE_CURSORS[h])
            return
        it = self.scene.object_item_at(pos, self._r())
        if it is not None and it.oid in self.scene.selected_ids and self.view.can_edit():
            self.view.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.view.viewport().setCursor(self.cursor())

    def press(self, pos, ev) -> None:
        self.origin, self._screen_origin = pos, ev.position()
        can_edit = self.view.can_edit()
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        # 1) one of the 8 resize handles of the selection frame
        h = self.scene.selection.handle_at(pos) if can_edit else None
        if h is not None:
            self.mode = "resize"
            self._handle = h
            self._resize_src = {it.oid: copy.deepcopy(self.ctrl.view.get(it.oid) or it.obj)
                                for it in self.scene.selected_items()}
            f = self.scene.selection.frame_rect()
            self._box0 = (f.left(), f.top(), f.right(), f.bottom())
            return
        it = self.scene.object_item_at(pos, self._r())
        # 2) vote badge on a sticky note (S12)
        if isinstance(it, StickyItem) and it.badge_rect().contains(it.mapFromScene(pos)):
            self.view.toggle_vote(it.oid)
            self.mode = None
            return
        # 3) object: select it and get ready to move
        if it is not None:
            sel = self.scene.selected_ids
            if shift:
                sel = [i for i in sel if i != it.oid] if it.oid in sel else sel + [it.oid]
            elif it.oid not in sel:
                sel = [it.oid]
            self.scene.set_selection(sel)
            if can_edit and it.oid in sel and not shift:
                self.mode = "move"
                self.start_pos = {i.oid: i.pos() for i in self.scene.selected_items()}
            else:
                self.mode = None
            return
        # 4) empty space: rubber band
        self._base_sel = self.scene.selected_ids if shift else []
        if not shift:
            self.scene.set_selection([])
        self.mode = "band"
        self.band.setRect(QRectF(pos, pos))
        if self.band.scene() is None:
            self.scene.addItem(self.band)

    def move(self, pos, ev) -> None:
        if self.mode == "move":
            d = pos - self.origin
            for it in self.scene.selected_items():
                p0 = self.start_pos.get(it.oid)
                if p0 is not None:
                    it.setPos(p0 + d)
            self.scene.selection.refresh()
        elif self.mode == "band":
            self.band.setRect(QRectF(self.origin, pos).normalized())
        elif self.mode == "resize" and self._handle is not None:
            # live preview: re-render the local items only, nothing is sent yet
            for oid, ch in self._resize_changes(pos, ev).items():
                it = self.scene.items_by_id.get(oid)
                if it is not None:
                    it.set_object(self._with_changes(self._resize_src[oid], ch))
            self.scene.selection.refresh()

    def _resize_changes(self, pos: QPointF, ev) -> dict[str, dict]:
        """Update keys per object for the handle dragged to ``pos`` (see the class docstring)."""
        d = pos - self.origin
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        types = {o.get("type") for o in self._resize_src.values()}
        # images and lone text keep their proportions on corners; Shift does it for anything
        keep = shift or (len(self._resize_src) == 1 and bool(types & {"image", "text"}))
        box1 = G.resize_box(self._box0, self._handle, d.x(), d.y(), keep_aspect=keep,
                            min_size=MIN_RESIZE_PX / self.view.scale_factor())
        return {oid: G.scale_changes(obj, self._box0, box1, self._handle)
                for oid, obj in self._resize_src.items()}

    @staticmethod
    def _with_changes(obj: dict, changes: dict) -> dict:
        out = copy.deepcopy(obj)
        for key, value in changes.items():
            set_key(out, key, value)
        return out

    def release(self, pos, ev) -> None:
        mode, self.mode = self.mode, None
        d = ev.position() - self._screen_origin
        moved = math.hypot(d.x(), d.y()) >= DRAG_SLOP_PX
        if mode == "move":
            if moved:
                delta = pos - self.origin
                changes = {}
                for oid in self.start_pos:
                    obj = self.ctrl.view.get(oid)
                    if obj is None:
                        continue
                    ox, oy = (obj.get("off") or [0, 0])
                    changes[oid] = {"off": [round(ox + delta.x(), 1), round(oy + delta.y(), 1)]}
                if changes:
                    self.ctrl.update_many(changes)        # one batch op = one undo step
            self._restore_positions()
            self.start_pos = {}
        elif mode == "band":
            rect = self.band.rect()
            self.scene.removeItem(self.band)
            if rect.width() > 0 or rect.height() > 0:
                self.scene.set_selection(self._base_sel + [i for i in self.scene.ids_in_rect(rect)
                                                           if i not in self._base_sel])
        elif mode == "resize" and self._handle is not None:
            if moved:
                changes = {oid: ch for oid, ch in self._resize_changes(pos, ev).items()
                           if self.ctrl.view.is_live(oid)}
                if changes:
                    self.ctrl.update_many(changes)        # one batch op = one undo step
            self._end_resize()
        self.scene.selection.refresh()

    def _end_resize(self) -> None:
        """Snap the previewed items back to the board's truth (after the update, if any)."""
        for oid in self._resize_src:
            it = self.scene.items_by_id.get(oid)
            src = self.ctrl.view.get(oid)
            if it is not None and src is not None and not src.get("deleted"):
                it.set_object(src)
        self._resize_src, self._handle = {}, None

    def _restore_positions(self) -> None:
        """Re-sync item positions to the board (the update above already moved them)."""
        for oid in self.start_pos:
            it = self.scene.items_by_id.get(oid)
            obj = self.ctrl.view.get(oid)
            if it is not None and obj is not None:
                off = obj.get("off") or [0, 0]
                it.setPos(float(off[0]), float(off[1]))

    def double_click(self, pos, ev) -> None:
        if not self.view.can_edit():
            return
        it = self.scene.object_item_at(pos, self._r())
        if isinstance(it, (TextItem, StickyItem)):
            self.view.edit_object(it.oid)

    def cancel(self) -> None:
        if self.mode == "move":
            self._restore_positions()
        if self.band.scene() is not None:
            self.scene.removeItem(self.band)
        if self._handle is not None:
            self._end_resize()
            self.scene.selection.refresh()
        self.mode = None


# ===========================================================================
# laser pointer and pan
# ===========================================================================

class LaserTool(Tool):
    """Fading laser trail (S10). Points go out at ~30 Hz. Never saved to the board."""

    name = "laser"
    edits = False

    def __init__(self, view) -> None:
        super().__init__(view)
        self.active = False
        self.pending: list[float] = []
        self.flush = QTimer()
        self.flush.setInterval(C.LIVE_FLUSH_MS)
        self.flush.timeout.connect(self._flush)

    def _color(self) -> str:
        me = self.scene.my_id()
        return self.ctrl.color_of(me) if me else "#ff3b30"

    def cursor(self) -> QCursor:
        return dot_cursor(self._color())

    def press(self, pos, ev) -> None:
        self.active = True
        self._add(pos)
        self.flush.start()

    def move(self, pos, ev) -> None:
        if self.active:
            self._add(pos)

    def _add(self, pos: QPointF) -> None:
        pt = [round(pos.x(), 1), round(pos.y(), 1)]
        self.scene.add_laser("__me__", pt, self._color())
        self.pending += pt

    def _flush(self) -> None:
        if self.pending:
            self.ctrl.send_laser(self.pending)
            self.pending = []
        if not self.active:
            self.flush.stop()

    def release(self, pos, ev) -> None:
        self.active = False
        self._flush()

    def cancel(self) -> None:
        self.active = False
        self._flush()


class PanTool(Tool):
    """Hand tool. (Panning itself is implemented by the view for every tool.)"""

    name = "pan"
    edits = False

    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.OpenHandCursor)


TOOL_NAMES = ("select", "pan", "pen", "highlighter", "eraser", "line", "arrow", "rect", "ellipse",
              "text", "sticky", "laser")


def build_tools(view) -> dict[str, Tool]:
    tools: dict[str, Tool] = {
        "select": SelectTool(view), "pan": PanTool(view), "pen": PenTool(view),
        "highlighter": HighlighterTool(view), "eraser": EraserTool(view), "text": TextTool(view),
        "sticky": StickyTool(view), "laser": LaserTool(view),
    }
    for kind in ("line", "arrow", "rect", "ellipse"):
        tools[kind] = ShapeTool(view, kind)
    return tools
