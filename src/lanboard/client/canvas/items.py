"""QGraphicsItems that draw board objects and the transient "presence" overlays.

Board object items (one per live object, see :class:`BoardItem`):
    StrokeItem   freehand pen / highlighter ink (smooth quadratic path)
    ShapeItem    line, arrow, rect, ellipse (optional fill)
    TextItem     plain text, laid out by a QTextDocument
    StickyItem   sticky note: rounded card, soft shadow, wrapped text, vote badge
    ImageItem    image blob (PNG/JPEG) or a "loading…" placeholder

Every board item draws its ``geom`` in *local* coordinates that equal scene
coordinates, and the object's ``off`` translation becomes ``setPos(off)``. A
move is therefore just a position change, and no path has to be rebuilt.

Dark board: when the scene's ``ink_dark`` flag is on, near-black ink is drawn
near-white and near-white ink near-black (:func:`display_color`). This is a
*display-only* remap done at paint time. The object dicts, the ops other users
receive and every export keep the original colours.

Overlay items (never saved, never exported, see :data:`OVERLAY_Z`):
    LiveStrokeItem   a stroke that is still being drawn (mine or a remote user's)
    CursorItem       a remote mouse cursor with a name pill
    LaserItem        a fading laser-pointer trail
    PingItem         "look here" ripple
    PulseItem        Smart Ink hint shown when the pen is held still
    SelectionOverlay dashed outline and handles around the selection
"""

from __future__ import annotations

import copy
import math
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QPixmap, QPolygonF, QTextCharFormat,
                           QTextCursor, QTextDocument, QTextOption)
from PySide6.QtWidgets import QGraphicsItem

from lanboard.common.geometry import pairs

if TYPE_CHECKING:                                    # pragma: no cover
    from .scene import CanvasScene

OVERLAY_Z = 1.0e9
ACCENT = "#4f7cff"
ACCENT_DARK = "#7b93ff"
STICKY_FILL = "#fff59d"
STICKY_TEXT = "#2b2b2b"
STICKY_PAD = 14.0
STICKY_RADIUS = 10.0
UI_FONT_FAMILIES = ["Inter", "SF Pro Text", "Segoe UI", "Helvetica Neue", "Arial"]


def qcolor(value: Any, fallback: str = "#1e1e1e") -> QColor:
    """Parse a colour string, falling back to a dark grey on bad input."""
    c = QColor(value) if isinstance(value, str) else QColor()
    return c if c.isValid() else QColor(fallback)


_remap_cache: dict[int, QColor] = {}


def luminance(c: QColor) -> float:
    """WCAG relative luminance (0 = black, 1 = white) of an sRGB colour."""
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def display_color(c: QColor) -> QColor:
    """Colour to *paint* ``c`` with on the dark board.

    * very dark ink (luminance <= 0.05: black, "#1b2130", "#222", navy…) gets its
      HSL lightness mirrored (L -> 1 - L), so it becomes a light tint of the same hue
    * near-white ink (every channel >= 0.85) is mirrored the other way
    * every other colour is returned unchanged (reds, blues, yellows… read fine
      on both backgrounds)

    The result is clamped to L in [0.07, 0.93] so nothing turns into a glaring
    pure white. Results are cached per RGBA value because this runs inside paint().
    """
    key = c.rgba()
    hit = _remap_cache.get(key)
    if hit is not None:
        return hit
    out = QColor(c)
    if luminance(c) <= 0.05 or min(c.redF(), c.greenF(), c.blueF()) >= 0.85:
        h, sat, light, a = c.getHslF()
        if h < 0:
            h, sat = 0.0, 0.0
        out = QColor.fromHslF(h, sat, max(0.07, min(0.93, 1.0 - light)), a)
    if len(_remap_cache) < 4096:
        _remap_cache[key] = out
    return out


def ink_dark(item: QGraphicsItem) -> bool:
    """True when ``item`` should paint with the dark-board remap (never during export)."""
    sc = item.scene()
    return bool(sc is not None and getattr(sc, "ink_dark", False))


def label_text_color(bg: QColor) -> QColor:
    """White or dark text, whichever reads better on a pill filled with ``bg``."""
    return QColor("#1b2130") if luminance(bg) > 0.45 else QColor("#ffffff")


def ui_font(px: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(UI_FONT_FAMILIES)
    f.setPixelSize(max(1, int(round(px))))
    if bold:
        f.setWeight(QFont.Weight.DemiBold)
    return f


def stroke_path(points: list[tuple[float, float]]) -> QPainterPath:
    """Smooth path through hand-drawn points.

    Quadratic Béziers run from midpoint to midpoint, using each sample point as the
    control point. This is the classic "smooth ink" trick: the curve is C1
    continuous and never overshoots. :class:`LiveStrokeItem` builds exactly the
    same path incrementally, so committing a stroke never makes it jump.
    """
    path = QPainterPath()
    n = len(points)
    if n == 0:
        return path
    x0, y0 = points[0]
    path.moveTo(x0, y0)
    if n == 1:
        path.lineTo(x0 + 0.01, y0)
        return path
    for i in range(1, n):
        px, py = points[i - 1]
        x, y = points[i]
        path.quadTo(px, py, (px + x) / 2, (py + y) / 2)
    path.lineTo(*points[-1])
    return path


def ink_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, max(0.5, width), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    return pen


def cosmetic_pen(color: QColor | str, width: float = 1.0, style=Qt.PenStyle.SolidLine) -> QPen:
    """A pen whose width is in screen pixels whatever the zoom."""
    pen = QPen(qcolor(color) if isinstance(color, str) else color, width, style,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    pen.setCosmetic(True)
    return pen


def text_document(text: str, font_px: float, color: QColor, width: float | None = None) -> QTextDocument:
    """A QTextDocument configured exactly like the inline text editor (margin 0)."""
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    doc.setDefaultFont(ui_font(font_px))
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    doc.setDefaultTextOption(opt)
    doc.setPlainText(text or "")
    if width is not None:
        doc.setTextWidth(max(10.0, width))
    cur = QTextCursor(doc)
    cur.select(QTextCursor.SelectionType.Document)
    fmt = QTextCharFormat()
    fmt.setForeground(QBrush(color))
    cur.mergeCharFormat(fmt)
    return doc


class BoardItem(QGraphicsItem):
    """Base class: mirrors one board object dict (a private deep copy is kept)."""

    is_overlay = False
    resizable = True

    def __init__(self, obj: dict, ctx: "CanvasScene") -> None:
        super().__init__()
        self.ctx = ctx
        self.oid: str = obj["id"]
        self.obj: dict = {}
        self.dimmed = False
        self._bounds = QRectF()
        self.set_object(obj)

    @property
    def style(self) -> dict:
        return self.obj.get("style") or {}

    @property
    def geom(self) -> dict:
        return self.obj.get("geom") or {}

    def set_object(self, obj: dict) -> None:
        """Take a new version of the object. Only rebuilds geometry when needed."""
        old = self.obj
        self.obj = copy.deepcopy(obj)
        if (not old or old.get("geom") != obj.get("geom") or old.get("style") != obj.get("style")
                or old.get("votes") != obj.get("votes") or old.get("type") != obj.get("type")):
            self.prepareGeometryChange()
            self.rebuild()
            self.update()
        off = obj.get("off") or [0, 0]
        self.setPos(float(off[0]), float(off[1]))
        self.apply_opacity()

    def rebuild(self) -> None:
        """Recompute cached paths and bounds from ``self.obj`` (subclasses)."""

    def apply_opacity(self) -> None:
        base = float(self.style.get("opacity", 1.0) or 1.0)
        self.setOpacity(max(0.02, min(1.0, base)) * (0.15 if self.dimmed else 1.0))

    def geom_rect(self) -> QRectF:
        """The object's x/y/w/h box in local coordinates (box-shaped types)."""
        g = self.geom
        return QRectF(float(g.get("x", 0)), float(g.get("y", 0)),
                      float(g.get("w", 0)), float(g.get("h", 0))).normalized()

    def local_frame(self) -> QRectF:
        """The *logical* extent in local coordinates: the box the resize handles
        transform. Box types use x/y/w/h, subclasses override it."""
        return self.geom_rect()

    def frame_rect(self) -> QRectF:
        """:meth:`local_frame` in scene coordinates (``off`` applied)."""
        return self.local_frame().translated(self.pos())

    def ink_pad(self) -> float:
        """How far painted ink sticks out of :meth:`frame_rect` (half the line width)."""
        return 0.0

    def boundingRect(self) -> QRectF:
        return self._bounds

    def shape(self) -> QPainterPath:
        p = QPainterPath()
        p.addRect(self._bounds)
        return p


class StrokeItem(BoardItem):
    """Freehand ink. The highlighter is the same thing with opacity ~0.35."""

    def rebuild(self) -> None:
        pts = pairs(self.geom.get("pts") or [0, 0])
        self._width = float(self.style.get("width", 3) or 1)
        color = qcolor(self.style.get("color"))
        self._pen = ink_pen(color, self._width)
        self._pen_dark = ink_pen(display_color(color), self._width)
        self._path = stroke_path(pts)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        self._frame = QRectF(QPointF(min(xs), min(ys)), QPointF(max(xs), max(ys)))
        pad = self._width / 2 + 1
        self._bounds = self._path.boundingRect().adjusted(-pad, -pad, pad, pad)
        self._shape: QPainterPath | None = None

    def local_frame(self) -> QRectF:
        return self._frame

    def ink_pad(self) -> float:
        return self._width / 2

    def shape(self) -> QPainterPath:
        if self._shape is None:
            st = QPainterPathStroker()
            st.setWidth(max(self._width, 6.0))
            st.setCapStyle(Qt.PenCapStyle.RoundCap)
            st.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            self._shape = st.createStroke(self._path)
        return self._shape

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setPen(self._pen_dark if ink_dark(self) else self._pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)


def arrow_head(x1: float, y1: float, x2: float, y2: float, width: float) -> tuple[QPolygonF, QPointF]:
    """Filled arrow head polygon at (x2, y2) and the point where the shaft should stop."""
    ang = math.atan2(y2 - y1, x2 - x1)
    length = min(max(12.0, width * 3.6), max(6.0, math.hypot(x2 - x1, y2 - y1) * 0.6))
    spread = math.radians(26)
    tip = QPointF(x2, y2)
    left = QPointF(x2 - length * math.cos(ang - spread), y2 - length * math.sin(ang - spread))
    right = QPointF(x2 - length * math.cos(ang + spread), y2 - length * math.sin(ang + spread))
    base = QPointF(x2 - length * 0.8 * math.cos(ang), y2 - length * 0.8 * math.sin(ang))
    return QPolygonF([tip, left, right]), base


class ShapeItem(BoardItem):
    """line, arrow, rect and ellipse."""

    def rebuild(self) -> None:
        t, g, s = self.obj.get("type"), self.geom, self.style
        self._width = float(s.get("width", 3) if s.get("width") is not None else 3)
        color = qcolor(s.get("color"))
        fill = s.get("fill")
        has_fill = bool(fill) and t in ("rect", "ellipse")
        self._pens = tuple(ink_pen(c, self._width) if self._width > 0 else QPen(Qt.PenStyle.NoPen)
                           for c in (color, display_color(color)))
        self._brushes = tuple(QBrush(c) if has_fill else QBrush(Qt.BrushStyle.NoBrush)
                              for c in ((qcolor(fill), display_color(qcolor(fill))) if has_fill else (None, None)))
        self._head_brushes = (QBrush(color), QBrush(display_color(color)))
        self._head: QPolygonF | None = None
        path = QPainterPath()
        if t in ("line", "arrow"):
            x1, y1, x2, y2 = (float(g.get(k, 0)) for k in ("x1", "y1", "x2", "y2"))
            self._frame = QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized()
            end = QPointF(x2, y2)
            if t == "arrow":
                self._head, end = arrow_head(x1, y1, x2, y2, self._width)
            path.moveTo(x1, y1)
            path.lineTo(end)
        elif t == "rect":
            self._frame = self.geom_rect()
            path.addRect(self._frame)
        else:
            self._frame = self.geom_rect()
            path.addEllipse(self._frame)
        self._path = path
        br = path.boundingRect()
        if self._head is not None:
            br = br.united(self._head.boundingRect())
        pad = self._width / 2 + 2
        self._bounds = br.adjusted(-pad, -pad, pad, pad)
        st = QPainterPathStroker()
        st.setWidth(max(self._width, 8.0))
        self._shape = st.createStroke(path)
        if has_fill:
            self._shape = self._shape.united(path)
        if self._head is not None:
            hp = QPainterPath()
            hp.addPolygon(self._head)
            self._shape = self._shape.united(hp)

    @property
    def _pen(self) -> QPen:
        return self._pens[0]

    def local_frame(self) -> QRectF:
        return self._frame

    def ink_pad(self) -> float:
        return self._width / 2

    def shape(self) -> QPainterPath:
        return self._shape

    def paint(self, painter: QPainter, option, widget=None) -> None:
        k = 1 if ink_dark(self) else 0
        painter.setPen(self._pens[k])
        painter.setBrush(self._brushes[k])
        painter.drawPath(self._path)
        if self._head is not None:
            pen = QPen(self._pens[k])
            pen.setWidthF(max(1.0, self._width * 0.6))
            painter.setPen(pen)
            painter.setBrush(self._head_brushes[k])
            painter.drawPolygon(self._head)


class TextItem(BoardItem):
    """A plain-text label. Uses a QTextDocument so it matches the inline editor."""

    def __init__(self, obj: dict, ctx: "CanvasScene") -> None:
        self.editing = False
        super().__init__(obj, ctx)

    def rebuild(self) -> None:
        s, g = self.style, self.geom
        self._doc = text_document(g.get("text", ""), float(s.get("font_size", 20) or 20), qcolor(s.get("color")))
        self._doc_dark: QTextDocument | None = None
        size = self._doc.size()
        self._origin = QPointF(float(g.get("x", 0)), float(g.get("y", 0)))
        self._frame = QRectF(self._origin, size)
        self._bounds = self._frame.adjusted(-2, -2, 2, 2)

    def local_frame(self) -> QRectF:
        return self._frame

    def _dark_doc(self) -> QTextDocument:
        if self._doc_dark is None:
            s, g = self.style, self.geom
            color = display_color(qcolor(s.get("color")))
            self._doc_dark = text_document(g.get("text", ""), float(s.get("font_size", 20) or 20), color)
        return self._doc_dark

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self.editing:
            return
        painter.save()
        painter.translate(self._origin)
        (self._dark_doc() if ink_dark(self) else self._doc).drawContents(painter)
        painter.restore()


class StickyItem(BoardItem):
    """Sticky note with a soft shadow, wrapped text and a dot-voting badge (S12).

    On the dark board a sticky keeps its own fill and dark text (paper stays paper),
    so it is deliberately *not* colour-remapped.
    """

    def __init__(self, obj: dict, ctx: "CanvasScene") -> None:
        self.editing = False
        super().__init__(obj, ctx)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)

    def votes(self) -> list[str]:
        v = self.obj.get("votes")
        if v is None:
            v = self.geom.get("votes")
        return [str(u) for u in v] if isinstance(v, list) else []

    def rebuild(self) -> None:
        s, g = self.style, self.geom
        self._rect = self.geom_rect()
        self._fill = qcolor(s.get("fill") or STICKY_FILL, STICKY_FILL)
        font_px = float(s.get("font_size", 16) or 16)
        self._doc = text_document(g.get("text", ""), font_px, qcolor(s.get("color") or STICKY_TEXT, STICKY_TEXT),
                                  width=self._rect.width() - 2 * STICKY_PAD)
        n = len(self.votes())
        self._badge_font = ui_font(13, bold=True)
        self._badge_text = f"👍 {n}" if n else "👍"
        fm = QFontMetricsF(self._badge_font)
        bw, bh = fm.horizontalAdvance(self._badge_text) + 16, 22.0
        self._badge = QRectF(self._rect.right() - bw - 8, self._rect.bottom() - bh - 8, bw, bh)
        self._bounds = self._rect.adjusted(-8, -6, 8, 14)

    def badge_rect(self) -> QRectF:
        """Badge rectangle in local coordinates (use ``mapFromScene`` to test clicks)."""
        return self._badge

    def shape(self) -> QPainterPath:
        p = QPainterPath()
        p.addRoundedRect(self._rect, STICKY_RADIUS, STICKY_RADIUS)
        return p

    def paint(self, painter: QPainter, option, widget=None) -> None:
        r = self._rect
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(6, 0, -1):
            painter.setBrush(QColor(0, 0, 0, 7))
            painter.drawRoundedRect(r.adjusted(-i * 0.8, -i * 0.4 + 3, i * 0.8, i * 1.1 + 3),
                                    STICKY_RADIUS + i, STICKY_RADIUS + i)
        painter.setBrush(self._fill)
        painter.setPen(QPen(self._fill.darker(112), 1))
        painter.drawRoundedRect(r, STICKY_RADIUS, STICKY_RADIUS)
        top = QRectF(r.left(), r.top(), r.width(), min(18.0, r.height() / 4))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 50))
        clip = QPainterPath()
        clip.addRoundedRect(r, STICKY_RADIUS, STICKY_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        painter.drawRect(top)
        painter.restore()
        if not self.editing:
            painter.save()
            painter.setClipRect(r.adjusted(STICKY_PAD, STICKY_PAD, -STICKY_PAD, -STICKY_PAD - 20))
            painter.translate(r.left() + STICKY_PAD, r.top() + STICKY_PAD)
            self._doc.drawContents(painter)
            painter.restore()
        votes = self.votes()
        me = self.ctx.my_id() if self.ctx is not None else None
        mine = me is not None and me in votes
        b = self._badge
        if mine:
            painter.setBrush(qcolor(self.ctx.ctrl.color_of(me), ACCENT))
            painter.setPen(Qt.PenStyle.NoPen)
            text_color = QColor("#ffffff")
        else:
            painter.setBrush(QColor(255, 255, 255, 170 if votes else 110))
            painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
            text_color = QColor(40, 40, 40, 230 if votes else 120)
        painter.drawRoundedRect(b, b.height() / 2, b.height() / 2)
        painter.setFont(self._badge_font)
        painter.setPen(text_color)
        painter.drawText(b, Qt.AlignmentFlag.AlignCenter, self._badge_text)


class ImageItem(BoardItem):
    """Image blob (also used for PDF pages). Shows a placeholder until the bytes
    arrive (``blob_ready``). Images are never colour-remapped."""

    def rebuild(self) -> None:
        self._rect = self.geom_rect()
        self._bounds = self._rect.adjusted(-2, -2, 2, 2)

    def shape(self) -> QPainterPath:
        p = QPainterPath()
        p.addRect(self._rect)
        return p

    def paint(self, painter: QPainter, option, widget=None) -> None:
        r = self._rect
        pix: QPixmap | None = self.ctx.pixmap_for(self.geom.get("blob", ""), self.oid) if self.ctx else None
        if pix is not None and not pix.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(r, pix, QRectF(pix.rect()))
            painter.setPen(cosmetic_pen(QColor(0, 0, 0, 28), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r)
            return
        dark = ink_dark(self)
        painter.setBrush(QColor("#23262e" if dark else "#eef0f4"))
        painter.setPen(cosmetic_pen("#4a505e" if dark else "#b9bfcb", 1.2, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(r, 6, 6)
        painter.setPen(QColor("#9aa1b0" if dark else "#7a8191"))
        painter.setFont(ui_font(max(10.0, min(28.0, min(r.width(), r.height()) / 9))))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "🖼  loading…")


ITEM_CLASSES: dict[str, type[BoardItem]] = {
    "stroke": StrokeItem, "line": ShapeItem, "arrow": ShapeItem, "rect": ShapeItem,
    "ellipse": ShapeItem, "text": TextItem, "sticky": StickyItem, "image": ImageItem,
}


def make_item(obj: dict, ctx: "CanvasScene") -> BoardItem:
    return ITEM_CLASSES.get(obj.get("type"), StrokeItem)(obj, ctx)


class OverlayItem(QGraphicsItem):
    """Base class for transient items: never exported and drawn above the board."""

    is_overlay = True

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(OVERLAY_Z)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)


class LiveStrokeItem(OverlayItem):
    """A stroke that is still being drawn. Points are appended incrementally.

    The path grows one quadratic segment per point, so each mouse event costs
    O(1) to build. Only the area around the new segment is repainted.
    """

    def __init__(self, color: str, width: float, opacity: float = 1.0) -> None:
        super().__init__()
        self.setZValue(OVERLAY_Z - 10)
        self._pen = ink_pen(qcolor(color), float(width))
        self._pen_dark = ink_pen(display_color(qcolor(color)), float(width))
        self._width = float(width)
        self.setOpacity(max(0.02, min(1.0, opacity)))
        self._pts: list[tuple[float, float]] = []
        self._path = QPainterPath()
        self._bounds = QRectF()
        self.fade_from: float | None = None

    @property
    def points(self) -> list[float]:
        return [v for p in self._pts for v in p]

    def append(self, flat: list[float]) -> None:
        new = pairs(flat)
        if not new:
            return
        dirty = QRectF()
        for x, y in new:
            if not self._pts:
                self._path.moveTo(x, y)
            else:
                px, py = self._pts[-1]
                self._path.quadTo(px, py, (px + x) / 2, (py + y) / 2)
                dirty = dirty.united(QRectF(QPointF(px, py), QPointF(x, y)).normalized())
            self._pts.append((x, y))
        pad = self._width / 2 + 2
        x, y = self._pts[-1]
        dirty = dirty.united(QRectF(x, y, 0.01, 0.01)).adjusted(-pad, -pad, pad, pad)
        if not self._bounds.contains(dirty):
            self.prepareGeometryChange()
            self._bounds = self._bounds.united(dirty) if not self._bounds.isNull() else dirty
        self.update(dirty)

    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if not self._pts:
            return
        painter.setPen(self._pen_dark if ink_dark(self) else self._pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if len(self._pts) == 1:
            x, y = self._pts[0]
            painter.drawLine(QPointF(x, y), QPointF(x + 0.01, y))
            return
        painter.drawPath(self._path)
        (px, py), (x, y) = self._pts[-2], self._pts[-1]
        painter.drawLine(QPointF((px + x) / 2, (py + y) / 2), QPointF(x, y))


class CursorItem(OverlayItem):
    """A remote user's pointer: arrow glyph + name pill, smoothly interpolated.

    Network cursor updates arrive at ~20 Hz. The scene's animation tick eases
    the drawn position towards the latest target, so movement looks continuous.
    After 5 s without updates the cursor fades out.
    """

    IDLE_FADE_S = 5.0

    def __init__(self, name: str, color: str) -> None:
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.target = QPointF()
        self.last_update = time.monotonic()
        self.set_identity(name, color)
        self.setOpacity(0.0)

    def set_identity(self, name: str, color: str) -> None:
        self.name, self.color = name, qcolor(color, ACCENT)
        self._font = ui_font(12, bold=True)
        fm = QFontMetricsF(self._font)
        self._pill = QRectF(14, 18, fm.horizontalAdvance(name) + 16, 20)
        self.prepareGeometryChange()
        self.update()

    def move_to(self, x: float, y: float) -> None:
        first = self.opacity() == 0.0 and self.target.isNull()
        self.target = QPointF(x, y)
        self.last_update = time.monotonic()
        if first:
            self.setPos(self.target)

    def tick(self, dt: float, now: float) -> bool:
        """Advance the animation. Returns True while more frames are needed."""
        busy = False
        d = self.target - self.pos()
        if abs(d.x()) + abs(d.y()) > 0.05:
            k = 1.0 - math.exp(-dt * 16.0)
            self.setPos(self.pos() + d * k)
            busy = True
        idle = now - self.last_update
        want = 1.0 if idle < self.IDLE_FADE_S else 0.0
        op = self.opacity()
        if abs(op - want) > 0.01:
            step = dt / (0.15 if want > op else 0.6)
            self.setOpacity(min(1.0, op + step) if want > op else max(0.0, op - step))
            busy = True
        return busy or idle < self.IDLE_FADE_S + 0.1

    def boundingRect(self) -> QRectF:
        return QRectF(-2, -2, 18, 22).united(self._pill).adjusted(-3, -3, 3, 4)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        arrow = QPolygonF([QPointF(0, 0), QPointF(0, 17), QPointF(4.6, 12.8), QPointF(8, 20),
                           QPointF(10.6, 18.8), QPointF(7.4, 11.8), QPointF(13, 11.6)])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawPolygon(arrow.translated(1, 1.5))
        painter.setPen(QPen(QColor("#ffffff"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(self.color)
        painter.drawPolygon(arrow)
        ring = QColor(255, 255, 255, 150) if ink_dark(self) else QColor(0, 0, 0, 40)
        painter.setPen(QPen(ring, 1))
        painter.setBrush(self.color)
        painter.drawRoundedRect(self._pill, 10, 10)
        painter.setPen(label_text_color(self.color))
        painter.setFont(self._font)
        painter.drawText(self._pill, Qt.AlignmentFlag.AlignCenter, self.name)


class LaserItem(OverlayItem):
    """Laser pointer trail (S10). Each point fades out over ``LIFE`` seconds."""

    LIFE = 1.2

    def __init__(self, color: str, ctx: "CanvasScene") -> None:
        super().__init__()
        self.ctx = ctx
        self.color = qcolor(color, "#ff3b30")
        self._pts: list[tuple[float, float, float]] = []
        self._bounds = QRectF()

    def add(self, flat: list[float], now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for x, y in pairs(flat):
            self._pts.append((x, y, now))
        self._recompute()

    def _recompute(self) -> None:
        self.prepareGeometryChange()
        if not self._pts:
            self._bounds = QRectF()
            return
        xs = [p[0] for p in self._pts]
        ys = [p[1] for p in self._pts]
        pad = 14.0 / max(self.ctx.view_scale if self.ctx else 1.0, 1e-3)
        self._bounds = QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad,
                              max(ys) - min(ys) + 2 * pad)

    def tick(self, now: float) -> bool:
        before = len(self._pts)
        self._pts = [p for p in self._pts if now - p[2] < self.LIFE]
        if len(self._pts) != before:
            self._recompute()
        self.update()
        return bool(self._pts)

    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if not self._pts:
            return
        now = time.monotonic()
        glow, core = QColor(self.color), QColor("#ffffff")
        for i in range(1, len(self._pts)):
            x0, y0, _ = self._pts[i - 1]
            x1, y1, t1 = self._pts[i]
            a = max(0.0, 1.0 - (now - t1) / self.LIFE)
            if a <= 0:
                continue
            glow.setAlphaF(a * 0.35)
            painter.setPen(cosmetic_pen(glow, 11 * (0.5 + a / 2)))
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
            c = QColor(self.color)
            c.setAlphaF(a)
            painter.setPen(cosmetic_pen(c, 4.5 * (0.4 + 0.6 * a)))
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        x, y, t = self._pts[-1]
        a = max(0.0, 1.0 - (now - t) / self.LIFE)
        if a > 0:
            c = QColor(self.color)
            c.setAlphaF(a)
            painter.setPen(cosmetic_pen(c, 7))
            painter.drawPoint(QPointF(x, y))
            core.setAlphaF(a)
            painter.setPen(cosmetic_pen(core, 3))
            painter.drawPoint(QPointF(x, y))


class PingItem(OverlayItem):
    """"Look here" ping (S10): three expanding rings and the sender's name."""

    LIFE = 2.2

    def __init__(self, name: str, color: str) -> None:
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.born = time.monotonic()
        self.color = qcolor(color, ACCENT)
        self.name = name
        self._font = ui_font(12, bold=True)

    def tick(self, now: float) -> bool:
        self.update()
        return now - self.born < self.LIFE

    def boundingRect(self) -> QRectF:
        return QRectF(-90, -100, 180, 170)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        age = time.monotonic() - self.born
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(3):
            phase = (age - k * 0.28) / 1.3
            if not 0.0 <= phase <= 1.0:
                continue
            ease = 1 - (1 - phase) ** 3
            c = QColor(self.color)
            c.setAlphaF(0.85 * (1 - phase))
            painter.setPen(QPen(c, 3.2 * (1 - phase) + 0.8))
            r = 8 + 56 * ease
            painter.drawEllipse(QPointF(0, 0), r, r)
        fade = 1.0 if age < self.LIFE - 0.5 else max(0.0, (self.LIFE - age) / 0.5)
        c = QColor(self.color)
        c.setAlphaF(fade)
        painter.setPen(QPen(QColor(255, 255, 255, int(255 * fade)), 2))
        painter.setBrush(c)
        painter.drawEllipse(QPointF(0, 0), 6, 6)
        fm = QFontMetricsF(self._font)
        label = f"{self.name} · look here"
        w = fm.horizontalAdvance(label) + 18
        pill = QRectF(-w / 2, -92, w, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pill, 11, 11)
        txt = label_text_color(self.color)
        txt.setAlpha(int(255 * fade))
        painter.setPen(txt)
        painter.setFont(self._font)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)


class PulseItem(OverlayItem):
    """Smart Ink hint: a soft pulsing ring at the pen tip while it is held still."""

    def __init__(self, color: str = ACCENT) -> None:
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.born = time.monotonic()
        self.color = qcolor(color, ACCENT)

    def tick(self, now: float) -> bool:
        self.update()
        return True

    def boundingRect(self) -> QRectF:
        return QRectF(-30, -30, 60, 60)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = (time.monotonic() - self.born) % 1.0
        c = QColor(self.color)
        c.setAlphaF(0.7 * (1 - t))
        painter.setPen(QPen(c, 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        r = 8 + 16 * t
        painter.drawEllipse(QPointF(0, 0), r, r)
        c.setAlphaF(0.25)
        painter.setBrush(c)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0, 0), 7, 7)


HANDLE_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
}


class SelectionOverlay(OverlayItem):
    """Selection frame with 8 resize handles (4 corners + 4 edges).

    * one object: a solid frame around its logical extent (``frame_rect``)
    * several: a light dashed outline per object plus one solid group frame
    * handles are only drawn when the user may edit. They keep a constant
      *screen* size whatever the zoom (sizes are divided by the view scale).
    * an edge handle is hidden when the frame is too small on screen for it
      to be grabbed separately from the corners, or when the frame has no
      extent along that axis (e.g. the height of a horizontal line)
    """

    HANDLE_PX = 9.0
    EDGE_PX = (14.0, 6.0)
    HIT_PX = 7.0
    MARGIN_PX = 6.0
    ITEM_MARGIN_PX = 2.0

    def __init__(self, ctx: "CanvasScene") -> None:
        super().__init__()
        self.ctx = ctx
        self.targets: list[BoardItem] = []
        self._bounds = QRectF()

    def set_targets(self, items: list[BoardItem]) -> None:
        self.targets = items
        self.refresh()

    def _scale(self) -> float:
        return max(self.ctx.view_scale, 1e-3)

    def group_rect(self) -> QRectF:
        r = QRectF()
        for it in self.targets:
            r = r.united(it.sceneBoundingRect())
        return r

    def frame_rect(self) -> QRectF:
        """Union of the targets' logical extents: the box that resizing maps."""
        rects = [it.frame_rect() for it in self.targets]
        if not rects:
            return QRectF()
        return QRectF(QPointF(min(r.left() for r in rects), min(r.top() for r in rects)),
                      QPointF(max(r.right() for r in rects), max(r.bottom() for r in rects)))

    def _outline(self) -> QRectF:
        """The drawn frame: logical extent + ink thickness + a small screen margin."""
        pad = max((it.ink_pad() for it in self.targets), default=0.0) + self.MARGIN_PX / self._scale()
        return self.frame_rect().adjusted(-pad, -pad, pad, pad)

    def can_resize(self) -> bool:
        if not self.targets or not all(it.resizable for it in self.targets):
            return False
        ctx = self.ctx
        if getattr(ctx, "is_preview", False):
            return False
        try:
            return bool(ctx.ctrl.can_edit())
        except Exception:                                # pragma: no cover - minimal controllers
            return True

    def handle_points(self) -> dict[str, QPointF]:
        """Scene position of every visible handle (empty when resizing is not allowed)."""
        if not self.can_resize():
            return {}
        f, g, s = self.frame_rect(), self._outline(), self._scale()
        pts = {"nw": g.topLeft(), "ne": g.topRight(), "se": g.bottomRight(), "sw": g.bottomLeft()}
        room = 3.2 * self.HANDLE_PX
        if f.width() > 1e-6 and g.height() * s >= room:
            pts["e"] = QPointF(g.right(), g.center().y())
            pts["w"] = QPointF(g.left(), g.center().y())
        if f.height() > 1e-6 and g.width() * s >= room:
            pts["n"] = QPointF(g.center().x(), g.top())
            pts["s"] = QPointF(g.center().x(), g.bottom())
        return pts

    def handle_at(self, pos: QPointF) -> str | None:
        """The handle under scene point ``pos`` (corners win over edges), or None."""
        pts = self.handle_points()
        r = self.HIT_PX / self._scale()
        for name in ("nw", "ne", "se", "sw", "n", "e", "s", "w"):
            p = pts.get(name)
            if p is not None and abs(pos.x() - p.x()) <= r and abs(pos.y() - p.y()) <= r:
                return name
        return None

    def refresh(self) -> None:
        self.prepareGeometryChange()
        pad = 16.0 / self._scale()
        g = self.group_rect().united(self._outline()) if self.targets else QRectF()
        self._bounds = g.adjusted(-pad, -pad, pad, pad) if not g.isNull() else QRectF()
        self.setVisible(bool(self.targets))
        self.update()

    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if not self.targets:
            return
        s = self._scale()
        dark = ink_dark(self)
        accent = qcolor(ACCENT_DARK if dark else ACCENT)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if len(self.targets) > 1:
            soft = QColor(accent)
            soft.setAlpha(150)
            dash = cosmetic_pen(soft, 1.0, Qt.PenStyle.DashLine)
            m = self.ITEM_MARGIN_PX / s
            painter.setPen(dash)
            for it in self.targets:
                pad = it.ink_pad() + m
                painter.drawRect(it.frame_rect().adjusted(-pad, -pad, pad, pad))
        painter.setPen(cosmetic_pen(accent, 1.3))
        painter.drawRect(self._outline())
        painter.setPen(cosmetic_pen(accent, 1.4))
        painter.setBrush(QColor("#ffffff"))
        hs = self.HANDLE_PX / s
        lng, sht = self.EDGE_PX[0] / s, self.EDGE_PX[1] / s
        for name, c in self.handle_points().items():
            if len(name) == 2:
                r = QRectF(c.x() - hs / 2, c.y() - hs / 2, hs, hs)
                painter.drawRoundedRect(r, 2.0 / s, 2.0 / s)
            else:
                w, h = (lng, sht) if name in ("n", "s") else (sht, lng)
                r = QRectF(c.x() - w / 2, c.y() - h / 2, w, h)
                painter.drawRoundedRect(r, sht / 2, sht / 2)
