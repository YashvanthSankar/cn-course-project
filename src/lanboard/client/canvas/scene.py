"""CanvasScene: renders a :class:`~lanboard.common.model.Board` incrementally.

The scene is a *pure function of the board*. Tools never add board items
themselves: they call controller methods, the controller applies the op to its
``view`` board and emits ``objects_changed(ids)``, and :meth:`CanvasScene.sync`
creates, updates or removes exactly those items. Local edits and remote edits
therefore go through the same code path. That is what keeps every replica
looking identical.

The scene also owns the transient presence layer: remote cursors, remote live
strokes, laser trails and "look here" pings. One shared 60 Hz animation timer
drives them. It runs only while something is actually animating.

Board look: a light or dark "paper" (:meth:`CanvasScene.set_board_theme`) with
a dot grid, a line grid or no grid (:meth:`CanvasScene.set_grid`). On the dark
board, items remap near-black/near-white ink while painting (see
``items.display_color``). Exports always use the light look.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Iterable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

from lanboard.common.geometry import hit_test
from lanboard.common.model import Board

from .items import (BoardItem, CursorItem, LaserItem, LiveStrokeItem, PingItem, SelectionOverlay,
                    make_item)

SCENE_EXTENT = 50_000.0
GRID_STEP = 24.0
GRID_STYLES = ("dots", "lines", "none")
BG_COLORS = (QColor("#fbfbfd"), QColor("#15171d"))
DOT_COLORS = (QColor("#d3d7df"), QColor("#2f343f"))
LINE_COLORS = (QColor(27, 33, 48, 20), QColor(255, 255, 255, 14))
BG_COLOR, DOT_COLOR = BG_COLORS[0], DOT_COLORS[0]
LIVE_END_GRACE_S = 0.8
LIVE_FADE_S = 0.3


def item_hit(item: BoardItem, x: float, y: float, radius: float) -> bool:
    """Precise hit test for a rendered item (text uses real font metrics)."""
    if item.obj.get("type") == "text":
        return item.sceneBoundingRect().adjusted(-radius, -radius, radius, radius).contains(QPointF(x, y))
    return hit_test(item.obj, x, y, radius)


class CanvasScene(QGraphicsScene):
    """Board renderer + presence overlays. See the module docstring."""

    selection_changed = Signal()
    content_changed = Signal()

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.ctrl = controller
        self.setSceneRect(-SCENE_EXTENT, -SCENE_EXTENT, 2 * SCENE_EXTENT, 2 * SCENE_EXTENT)
        self.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.BspTreeIndex)

        self.items_by_id: dict[str, BoardItem] = {}
        self.view_scale = 1.0
        self.spotlight: str | None = None
        self.exporting = False
        self.board_dark = False
        self.grid_style = "dots"
        self._preview: Board | None = None
        self._live_dirty = False

        self._pixmaps: dict[str, QPixmap] = {}
        self._waiting: dict[str, set[str]] = defaultdict(set)

        self._hidden: set[str] = set()
        self._selected: list[str] = []
        self.selection = SelectionOverlay(self)
        self.selection.setVisible(False)
        self.addItem(self.selection)

        self.cursors: dict[str, CursorItem] = {}
        self.live: dict[tuple[str, str], LiveStrokeItem] = {}
        self.lasers: dict[str, LaserItem] = {}
        self.pings: list[PingItem] = []
        self.animated: set = set()
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.setTimerType(Qt.TimerType.PreciseTimer)
        self._anim.timeout.connect(self._tick)
        self._last_tick = time.monotonic()

        c = controller
        c.reset.connect(self._on_reset)
        c.objects_changed.connect(self._on_objects_changed)
        c.cursor_moved.connect(self._on_cursor)
        c.live_stroke.connect(self._on_live_stroke)
        c.live_stroke_end.connect(self._on_live_stroke_end)
        c.laser.connect(self._on_laser)
        c.signal_received.connect(self._on_signal)
        c.blob_ready.connect(self._on_blob_ready)
        c.participants_changed.connect(self._on_participants)
        c.me_changed.connect(lambda *_: self._refresh_stickies())
        self.rebuild()

    def my_id(self) -> str | None:
        uid = getattr(self.ctrl, "user_id", None)
        if isinstance(uid, str) and uid:
            return uid
        me = getattr(self.ctrl, "me", None) or {}
        return me.get("user_id")

    @property
    def source(self) -> Board:
        """The board being rendered: the preview board if any, else the live view."""
        return self._preview if self._preview is not None else self.ctrl.view

    @property
    def is_preview(self) -> bool:
        return self._preview is not None

    @property
    def ink_dark(self) -> bool:
        """Items paint with the dark-board colour remap (never while exporting)."""
        return self.board_dark and not self.exporting

    def set_board_theme(self, dark: bool) -> None:
        dark = bool(dark)
        if dark == self.board_dark:
            return
        self.board_dark = dark
        for it in self.items_by_id.values():
            if it.cacheMode() != QGraphicsItem.CacheMode.NoCache:
                it.update()
        self.invalidate(self.sceneRect(), QGraphicsScene.SceneLayer.AllLayers)
        self.update()

    def set_grid(self, style: str) -> None:
        style = style if style in GRID_STYLES else "dots"
        if style != self.grid_style:
            self.grid_style = style
            self.invalidate(self.sceneRect(), QGraphicsScene.SceneLayer.BackgroundLayer)

    def rebuild(self) -> None:
        """Throw every board item away and rebuild from the source board."""
        for it in self.items_by_id.values():
            self.removeItem(it)
        self.items_by_id.clear()
        for obj in self.source.live():
            self._create(obj)
        self._restack()
        self._prune_selection()
        self.content_changed.emit()

    def sync(self, ids: Iterable[str]) -> None:
        """Create / update / remove the items for ``ids`` only."""
        src = self.source
        restack = False
        for oid in ids:
            obj = src.get(oid)
            it = self.items_by_id.get(oid)
            if obj is None or obj.get("deleted"):
                if it is not None:
                    self.removeItem(it)
                    del self.items_by_id[oid]
                continue
            self._drop_live_for(oid)
            if it is None or it.obj.get("type") != obj.get("type"):
                if it is not None:
                    self.removeItem(it)
                self._create(obj)
                restack = True
            else:
                if it.obj.get("z") != obj.get("z"):
                    restack = True
                it.set_object(obj)
                self._decorate(it)
        if restack:
            self._restack()
        self._prune_selection()
        self.content_changed.emit()

    def sync_all(self) -> None:
        """Diff the whole source board against the items (used for preview scrubbing)."""
        src = self.source
        ids = set(self.items_by_id) | {o["id"] for o in src.live()}
        changed = [oid for oid in ids
                   if not src.is_live(oid) or oid not in self.items_by_id
                   or self.items_by_id[oid].obj != src.get(oid)]
        self.sync(changed)

    def _create(self, obj: dict) -> BoardItem:
        it = make_item(obj, self)
        self._decorate(it)
        self.addItem(it)
        self.items_by_id[obj["id"]] = it
        return it

    def _decorate(self, it: BoardItem) -> None:
        """Per-item presentation: spotlight dimming, tooltip, local hiding."""
        owner = it.obj.get("owner") or ""
        it.dimmed = self.spotlight is not None and owner != self.spotlight
        it.apply_opacity()
        it.setToolTip(self.author_text(it.obj))
        it.setVisible(it.oid not in self._hidden)

    def _restack(self) -> None:
        """Paint order = Board.live() order (z, then id), identical on every client."""
        for i, obj in enumerate(self.source.live()):
            it = self.items_by_id.get(obj["id"])
            if it is not None:
                it.setZValue(float(i))

    def author_text(self, obj: dict | None) -> str:
        """'Drawn by NAME · HH:MM' (authorship lens)."""
        if not obj:
            return ""
        owner = obj.get("owner") or ""
        name = self.ctrl.name_of(owner) if owner else "unknown"
        ts = obj.get("ts")
        if isinstance(ts, (int, float)) and ts > 0:
            return f"Drawn by {name} · {time.strftime('%H:%M', time.localtime(ts))}"
        return f"Drawn by {name}"

    def _on_reset(self) -> None:
        if self.is_preview:
            self._live_dirty = True
            return
        self.rebuild()

    def _on_objects_changed(self, ids) -> None:
        if self.is_preview:
            self._live_dirty = True
            return
        self.sync(ids)

    def set_preview(self, board: Board | None) -> None:
        was = self._preview
        self._preview = board
        self.set_selection([])
        if board is None:
            if was is not None:
                self.rebuild()
                self._live_dirty = False
        elif was is None:
            self.rebuild()
        else:
            self.sync_all()

    def set_spotlight(self, uid: str | None) -> None:
        self.spotlight = uid or None
        for it in self.items_by_id.values():
            self._decorate(it)

    def hide_ids(self, ids: Iterable[str]) -> None:
        for oid in ids:
            self._hidden.add(oid)
            it = self.items_by_id.get(oid)
            if it is not None:
                it.setVisible(False)

    def clear_hidden(self) -> None:
        ids, self._hidden = self._hidden, set()
        for oid in ids:
            it = self.items_by_id.get(oid)
            if it is not None:
                it.setVisible(True)

    @property
    def selected_ids(self) -> list[str]:
        return list(self._selected)

    def selected_items(self) -> list[BoardItem]:
        return [self.items_by_id[i] for i in self._selected if i in self.items_by_id]

    def set_selection(self, ids: Iterable[str]) -> None:
        new = [i for i in dict.fromkeys(ids) if i in self.items_by_id]
        if new != self._selected:
            self._selected = new
            self.selection_changed.emit()
        self.selection.set_targets(self.selected_items())

    def _prune_selection(self) -> None:
        self.set_selection(self._selected)

    def object_item_at(self, pos: QPointF, radius: float = 4.0) -> BoardItem | None:
        """Topmost visible board item under ``pos`` (precise hit test)."""
        r = QRectF(pos.x() - radius, pos.y() - radius, 2 * radius, 2 * radius)
        for it in self.items(r, Qt.ItemSelectionMode.IntersectsItemBoundingRect, Qt.SortOrder.DescendingOrder):
            if isinstance(it, BoardItem) and it.isVisible() and item_hit(it, pos.x(), pos.y(), radius):
                return it
        return None

    def candidates_in(self, rect: QRectF) -> list[BoardItem]:
        """Visible board items whose bounding box touches ``rect`` (BSP-indexed, fast)."""
        return [it for it in self.items(rect, Qt.ItemSelectionMode.IntersectsItemBoundingRect)
                if isinstance(it, BoardItem) and it.isVisible()]

    def ids_in_rect(self, rect: QRectF) -> list[str]:
        """Ids of items whose painted shape intersects ``rect`` (rubber-band select)."""
        out = [it.oid for it in self.items(rect.normalized(), Qt.ItemSelectionMode.IntersectsItemShape)
               if isinstance(it, BoardItem) and it.isVisible()]
        order = {o["id"]: i for i, o in enumerate(self.source.live())}
        return sorted(out, key=lambda i: order.get(i, 0))

    def content_rect(self) -> QRectF:
        r = QRectF()
        for it in self.items_by_id.values():
            if it.isVisible():
                r = r.united(it.sceneBoundingRect())
        return r

    def cache_pixmap(self, h: str, pix: QPixmap) -> None:
        """Seed the decode cache (we already have the image we just uploaded)."""
        if h and not pix.isNull():
            self._pixmaps[h] = pix

    def pixmap_for(self, h: str, oid: str | None = None) -> QPixmap | None:
        if not h:
            return None
        pix = self._pixmaps.get(h)
        if pix is not None:
            return pix
        data = self.ctrl.blob_bytes(h)
        if data is None:
            if oid:
                self._waiting[h].add(oid)
            return None
        pix = QPixmap()
        if not pix.loadFromData(bytes(data)):
            return None
        self._pixmaps[h] = pix
        return pix

    def _on_blob_ready(self, h: str) -> None:
        self._pixmaps.pop(h, None)
        for oid in self._waiting.pop(h, set()):
            it = self.items_by_id.get(oid)
            if it is not None:
                it.update()

    def _refresh_stickies(self) -> None:
        for it in self.items_by_id.values():
            if it.obj.get("type") == "sticky":
                it.update()

    def _is_me(self, uid: str) -> bool:
        me = self.my_id()
        return bool(uid) and uid == me

    def _on_cursor(self, uid: str, x: float, y: float) -> None:
        if self._is_me(uid):
            return
        cur = self.cursors.get(uid)
        if cur is None:
            cur = CursorItem(self.ctrl.name_of(uid), self.ctrl.color_of(uid))
            self.addItem(cur)
            self.cursors[uid] = cur
        cur.move_to(x, y)
        self._kick()

    def _on_participants(self) -> None:
        parts = getattr(self.ctrl, "participants", {}) or {}
        for uid in list(self.cursors):
            p = parts.get(uid)
            if p is None or p.get("online") is False:
                self.removeItem(self.cursors.pop(uid))
            else:
                self.cursors[uid].set_identity(self.ctrl.name_of(uid), self.ctrl.color_of(uid))

    def _on_live_stroke(self, uid: str, payload: dict) -> None:
        if self._is_me(uid):
            return
        sid = str(payload.get("id", ""))
        if not sid or self.ctrl.view.is_live(sid):
            return
        key = (uid, sid)
        item = self.live.get(key)
        if item is None:
            opacity = 0.35 if payload.get("tool") == "highlighter" else 1.0
            item = LiveStrokeItem(str(payload.get("color", "#1e1e1e")), float(payload.get("width", 3) or 3), opacity)
            self.addItem(item)
            self.live[key] = item
        item.append(list(payload.get("pts", [])))

    def _on_live_stroke_end(self, uid: str, sid: str) -> None:
        item = self.live.get((uid, sid))
        if item is not None and item.fade_from is None:
            item.fade_from = time.monotonic() + LIVE_END_GRACE_S
            self._kick()

    def _drop_live_for(self, oid: str) -> None:
        for key in [k for k in self.live if k[1] == oid]:
            self.removeItem(self.live.pop(key))

    def _on_laser(self, uid: str, pts: list) -> None:
        if self._is_me(uid):
            return
        self.add_laser(uid, pts, self.ctrl.color_of(uid))

    def add_laser(self, uid: str, pts: list, color: str) -> None:
        item = self.lasers.get(uid)
        if item is None:
            item = LaserItem(color, self)
            self.addItem(item)
            self.lasers[uid] = item
        item.add(pts)
        self._kick()

    def _on_signal(self, uid: str, kind: str) -> None:
        if not kind.startswith("ping:") or self._is_me(uid):
            return
        try:
            xs, ys = kind[5:].split(",", 1)
            x, y = float(xs), float(ys)
        except ValueError:
            return
        self.add_ping(x, y, self.ctrl.name_of(uid), self.ctrl.color_of(uid))

    def add_ping(self, x: float, y: float, name: str, color: str) -> PingItem:
        p = PingItem(name, color)
        p.setPos(x, y)
        self.addItem(p)
        self.pings.append(p)
        self._kick()
        return p

    def add_animated(self, item: QGraphicsItem) -> None:
        """Register an overlay with ``tick(now) -> bool`` (removed when it returns False)."""
        if item.scene() is None:
            self.addItem(item)
        self.animated.add(item)
        self._kick()

    def remove_animated(self, item: QGraphicsItem) -> None:
        self.animated.discard(item)
        if item.scene() is self:
            self.removeItem(item)

    def _kick(self) -> None:
        if not self._anim.isActive():
            self._last_tick = time.monotonic()
            self._anim.start()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now
        busy = False
        for cur in self.cursors.values():
            busy |= cur.tick(dt, now)
        for uid, item in list(self.lasers.items()):
            if item.tick(now):
                busy = True
            else:
                self.removeItem(item)
                del self.lasers[uid]
        for p in list(self.pings):
            if p.tick(now):
                busy = True
            else:
                self.removeItem(p)
                self.pings.remove(p)
        for key, item in list(self.live.items()):
            if item.fade_from is None:
                continue
            age = now - item.fade_from
            if age >= LIVE_FADE_S:
                self.removeItem(item)
                del self.live[key]
            else:
                if age > 0:
                    item.setOpacity(max(0.0, item.opacity() * (1 - age / LIVE_FADE_S)))
                busy = True
        for item in list(self.animated):
            if item.tick(now):
                busy = True
            else:
                self.remove_animated(item)
        if not busy:
            self._anim.stop()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        """Paper colour plus a subtle grid (dots or lines) that thins out when zoomed out."""
        if self.exporting:
            painter.fillRect(rect, QColor("#ffffff"))
            return
        k = 1 if self.board_dark else 0
        painter.fillRect(rect, BG_COLORS[k])
        if self.grid_style == "none":
            return
        scale = abs(painter.worldTransform().m11()) or 1.0
        step = GRID_STEP
        while step * scale < 16:
            step *= 2
        x0 = math.floor(rect.left() / step) * step
        y0 = math.floor(rect.top() / step) * step
        nx = int((rect.right() - x0) / step) + 1
        ny = int((rect.bottom() - y0) / step) + 1
        if nx <= 0 or ny <= 0 or nx * ny > 40_000:
            return
        if self.grid_style == "lines":
            pen = QPen(LINE_COLORS[k], 1.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            for i in range(nx):
                x = x0 + i * step
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            for j in range(ny):
                y = y0 + j * step
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            return
        pen = QPen(DOT_COLORS[k], 1.8 if scale >= 0.6 else 1.4)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        pts = QPolygonF([QPointF(x0 + i * step, y0 + j * step) for j in range(ny) for i in range(nx)])
        painter.drawPoints(pts)

    def render_export(self, painter: QPainter, target: QRectF, source: QRectF) -> None:
        """Render only board objects (no cursors, overlays, editors or grid)."""
        hidden = []
        for it in self.items():
            if not isinstance(it, BoardItem) and it.isVisible() and it.parentItem() is None:
                it.setVisible(False)
                hidden.append(it)
        self.exporting = True
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            self.render(painter, target, source, Qt.AspectRatioMode.KeepAspectRatio)
        finally:
            self.exporting = False
            for it in hidden:
                it.setVisible(True)

