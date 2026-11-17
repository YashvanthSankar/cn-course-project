"""Minimap and zoom controls: small overlay widgets for the canvas corner.

:class:`Minimap` paints a scaled-down overview of the whole board plus the
rectangle the main view currently shows. Clicking or dragging on it pans the
main view. That counts as a *user* pan, so it also ends follow mode (see
``CanvasView.pan_to``).

Keeping it cheap on boards with thousands of objects:
  * the objects are drawn once into a cached pixmap. It is rebuilt only when
    the board changes or the overview's world rect has to change, never on
    every repaint. The viewport rectangle is drawn on top of the cache.
  * all updates go through one single-shot timer, so the minimap repaints at
    most 10 times per second however fast ops or scroll events arrive
  * level of detail: an object smaller than ~1.5 px in the overview is drawn
    as a dot, and a long stroke is decimated to at most ~24 points

:class:`ZoomControls` is a compact pill with  -  zoom%  +  fit.

Both widgets follow the board theme (``CanvasView.board_theme_changed``). The
UI places them as overlays in the canvas' bottom-right corner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from .items import ACCENT, ACCENT_DARK, STICKY_FILL, display_color, qcolor

if TYPE_CHECKING:                                    # pragma: no cover
    from .view import CanvasView

REFRESH_MS = 100
MAX_STROKE_SAMPLES = 24


def _palette(dark: bool) -> dict[str, QColor]:
    """Colours of the overlay card for the light / dark board."""
    if dark:
        return {"card": QColor(29, 32, 40, 232), "border": QColor(255, 255, 255, 28),
                "text": QColor("#e7e9ef"), "muted": QColor("#7d8597"), "image": QColor("#3a3f4b"),
                "hover": QColor(255, 255, 255, 22), "accent": qcolor(ACCENT_DARK)}
    return {"card": QColor(255, 255, 255, 236), "border": QColor(27, 33, 48, 30),
            "text": QColor("#1b2130"), "muted": QColor("#8b93a5"), "image": QColor("#c9ced8"),
            "hover": QColor(27, 33, 48, 16), "accent": qcolor(ACCENT)}


class Minimap(QWidget):
    """Overview of the board. Click or drag to pan the main view."""

    W, H = 200, 130
    RADIUS = 10.0
    INSET = 7.0

    def __init__(self, view: "CanvasView", parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Overview: click or drag to move around the board")
        self.setMouseTracking(False)

        self._cache: QPixmap | None = None
        self._cache_world = QRectF()
        self._content = QRectF()
        self._content_dirty = True
        self._world = QRectF()
        self._drag_world: QRectF | None = None
        self._grab = QPointF()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)

        view.canvas.content_changed.connect(self._on_content)
        view.view_rect_changed.connect(self.schedule)
        view.board_theme_changed.connect(lambda *_: self._on_content())
        self._refresh()

    def _on_content(self) -> None:
        self._content_dirty = True
        self.schedule()

    def schedule(self) -> None:
        """Ask for a refresh. Bursts of calls collapse into one (<= 10 Hz)."""
        if not self._timer.isActive():
            self._timer.start()

    def _refresh(self) -> None:
        if self._content_dirty:
            self._content = self.view.canvas.content_rect()
        world = self._drag_world or self._compute_world()
        if (self._content_dirty or self._cache is None or not _same_rect(world, self._cache_world)):
            self._world = world
            self._render_cache()
        self._world = world
        self._content_dirty = False
        self.update()

    def _inner(self) -> QRectF:
        i = self.INSET
        return QRectF(i, i, self.width() - 2 * i, self.height() - 2 * i)

    def _compute_world(self) -> QRectF:
        """Content united with the visible rect, padded and widened to our aspect ratio."""
        vis = self.view.visible_rect()
        r = self._content.united(vis) if not self._content.isNull() else QRectF(vis)
        pad = max(r.width(), r.height()) * 0.06 + 20
        r = r.adjusted(-pad, -pad, pad, pad)
        inner = self._inner()
        aspect = inner.width() / max(inner.height(), 1.0)
        if r.width() / max(r.height(), 1e-6) < aspect:
            w = r.height() * aspect
            r = QRectF(r.center().x() - w / 2, r.top(), w, r.height())
        else:
            h = r.width() / aspect
            r = QRectF(r.left(), r.center().y() - h / 2, r.width(), h)
        return r

    def _k(self, world: QRectF | None = None) -> float:
        world = world or self._world
        return self._inner().width() / max(world.width(), 1e-6)

    def scene_to_widget(self, p: QPointF) -> QPointF:
        inner, k = self._inner(), self._k()
        return QPointF(inner.left() + (p.x() - self._world.left()) * k,
                       inner.top() + (p.y() - self._world.top()) * k)

    def widget_to_scene(self, p: QPointF) -> QPointF:
        inner, k = self._inner(), self._k()
        return QPointF(self._world.left() + (p.x() - inner.left()) / k,
                       self._world.top() + (p.y() - inner.top()) / k)

    def viewport_box(self) -> QRectF:
        """The main view's visible rect, in widget coordinates."""
        vis = self.view.visible_rect()
        return QRectF(self.scene_to_widget(vis.topLeft()), self.scene_to_widget(vis.bottomRight()))

    def _render_cache(self) -> None:
        """Draw every visible board object, simplified, into ``self._cache``."""
        dpr = max(1.0, self.devicePixelRatioF())
        pix = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        self._cache, self._cache_world = pix, QRectF(self._world)
        items = self.view.canvas.items_by_id
        if not items or self._world.isEmpty():
            return
        dark = self.view.board_dark
        pal = _palette(dark)
        inner, k = self._inner(), self._k()
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setClipRect(inner)
        p.translate(inner.left(), inner.top())
        p.scale(k, k)
        p.translate(-self._world.left(), -self._world.top())
        tiny = 1.5 / k
        for it in sorted(items.values(), key=lambda i: i.zValue()):
            if not it.isVisible():
                continue
            obj, br = it.obj, it.sceneBoundingRect()
            t = obj.get("type")
            style = obj.get("style") or {}
            color = qcolor(style.get("color"))
            if dark and t != "sticky":
                color = display_color(color)
            if br.width() < tiny and br.height() < tiny:
                p.fillRect(QRectF(br.center().x() - tiny / 2, br.center().y() - tiny / 2, tiny, tiny), color)
                continue
            p.save()
            p.translate(it.pos())
            self._draw_object(p, obj, t, style, color, pal, dark)
            p.restore()
        p.end()

    @staticmethod
    def _draw_object(p: QPainter, obj: dict, t, style: dict, color: QColor, pal: dict, dark: bool) -> None:
        g = obj.get("geom") or {}
        pen = QPen(color, 1.3)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if t == "stroke":
            pts = g.get("pts") or []
            n = len(pts) // 2
            step = max(1, n // MAX_STROKE_SAMPLES)
            poly = QPolygonF([QPointF(pts[2 * i], pts[2 * i + 1]) for i in range(0, n, step)])
            if n and (n - 1) % step:
                poly.append(QPointF(pts[-2], pts[-1]))
            if style.get("tool") == "highlighter":
                c = QColor(color)
                c.setAlpha(110)
                pen.setColor(c)
                pen.setWidthF(2.5)
                p.setPen(pen)
            p.drawPolyline(poly)
        elif t in ("line", "arrow"):
            p.drawLine(QPointF(g.get("x1", 0), g.get("y1", 0)), QPointF(g.get("x2", 0), g.get("y2", 0)))
        elif t in ("rect", "ellipse"):
            r = QRectF(g.get("x", 0), g.get("y", 0), g.get("w", 0), g.get("h", 0)).normalized()
            if style.get("fill"):
                fill = qcolor(style["fill"])
                p.setBrush(display_color(fill) if dark else fill)
            (p.drawRect if t == "rect" else p.drawEllipse)(r)
        elif t == "sticky":
            r = QRectF(g.get("x", 0), g.get("y", 0), g.get("w", 0), g.get("h", 0)).normalized()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(style.get("fill") or STICKY_FILL, STICKY_FILL))
            p.drawRect(r)
        elif t == "image":
            r = QRectF(g.get("x", 0), g.get("y", 0), g.get("w", 0), g.get("h", 0)).normalized()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(pal["image"])
            p.drawRect(r)
        elif t == "text":
            size = float(style.get("font_size") or 20)
            lines = (g.get("text") or " ").split("\n")
            c = QColor(color)
            c.setAlpha(150)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            for i, line in enumerate(lines[:20]):
                w = max(1, len(line)) * size * 0.55
                p.drawRect(QRectF(g.get("x", 0), g.get("y", 0) + i * size * 1.3 + size * 0.3, w, size * 0.6))

    def paintEvent(self, ev) -> None:
        dark = self.view.board_dark
        pal = _palette(dark)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        card = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(card, self.RADIUS, self.RADIUS)
        p.fillPath(path, pal["card"])
        p.setPen(QPen(pal["border"], 1))
        p.drawPath(path)
        p.setClipPath(path)
        if self._cache is not None:
            p.drawPixmap(0, 0, self._cache)
        box = self.viewport_box().intersected(self._inner().adjusted(-2, -2, 2, 2))
        if not box.isEmpty():
            wash = QColor(pal["accent"])
            wash.setAlpha(34)
            p.setBrush(wash)
            p.setPen(QPen(pal["accent"], 1.5))
            p.drawRoundedRect(box, 2.5, 2.5)
        p.end()

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = ev.position()
        self._drag_world = QRectF(self._world)
        vis = self.view.visible_rect()
        here = self.widget_to_scene(pos)
        self._grab = vis.center() - here if self.viewport_box().contains(pos) else QPointF()
        self.view.pan_to(here + self._grab)
        self.update()

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_world is None:
            return
        self.view.pan_to(self.widget_to_scene(ev.position()) + self._grab)
        self.update()

    def mouseReleaseEvent(self, ev) -> None:
        self._drag_world = None
        self.schedule()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._content_dirty = True
        self.schedule()


def _same_rect(a: QRectF, b: QRectF, tol: float = 0.01) -> bool:
    """True when the two world rects differ by less than ``tol`` of their size."""
    if a.isNull() or b.isNull():
        return a.isNull() and b.isNull()
    s = max(a.width(), a.height(), 1e-6)
    return (abs(a.left() - b.left()) < tol * s and abs(a.top() - b.top()) < tol * s
            and abs(a.width() - b.width()) < tol * s and abs(a.height() - b.height()) < tol * s)


def _glyph(kind: str, color: QColor, size: int = 16) -> QIcon:
    """Tiny vector icons for the zoom pill: "minus", "plus" and "fit" (four corners)."""
    pix = QPixmap(size * 2, size * 2)
    pix.setDevicePixelRatio(2.0)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    c, r = size / 2, size * 0.3
    if kind in ("minus", "plus"):
        p.drawLine(QPointF(c - r, c), QPointF(c + r, c))
        if kind == "plus":
            p.drawLine(QPointF(c, c - r), QPointF(c, c + r))
    else:
        a, b, L = size * 0.2, size * 0.8, size * 0.22
        for x, y, dx, dy in ((a, a, 1, 1), (b, a, -1, 1), (a, b, 1, -1), (b, b, -1, -1)):
            p.drawPolyline(QPolygonF([QPointF(x + dx * L, y), QPointF(x, y), QPointF(x, y + dy * L)]))
    p.end()
    return QIcon(pix)


class ZoomControls(QWidget):
    """A small pill:  [-] [100%] [+] [fit].  Clicking the percentage resets to 100 %."""

    H = 34

    def __init__(self, view: "CanvasView", parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.setFixedHeight(self.H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(0)

        def button(tip: str, slot) -> QToolButton:
            b = QToolButton(self)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            lay.addWidget(b)
            return b

        self.out_btn = button("Zoom out (Ctrl -)", view.zoom_out)
        self.pct_btn = button("Reset to 100 % (Ctrl 0)", view.zoom_reset)
        self.pct_btn.setMinimumWidth(52)
        self.in_btn = button("Zoom in (Ctrl +)", view.zoom_in)
        self.fit_btn = button("Fit the whole board (Ctrl 1)", view.zoom_fit)
        for b in (self.out_btn, self.in_btn, self.fit_btn):
            b.setIconSize(QSize(16, 16))
            b.setFixedSize(28, 28)
        self.pct_btn.setFixedHeight(28)

        view.zoom_changed.connect(self._on_zoom)
        view.board_theme_changed.connect(self.apply_theme)
        self.apply_theme(view.board_dark)
        self._on_zoom(view.scale_factor())

    def _on_zoom(self, s: float) -> None:
        self.pct_btn.setText(f"{round(float(s) * 100)}%")

    def apply_theme(self, dark: bool) -> None:
        pal = _palette(bool(dark))
        self._dark = bool(dark)
        fg = pal["text"]
        self.out_btn.setIcon(_glyph("minus", fg))
        self.in_btn.setIcon(_glyph("plus", fg))
        self.fit_btn.setIcon(_glyph("fit", fg))
        h = pal["hover"]
        self.setStyleSheet(
            "QToolButton { border: none; border-radius: 7px; background: transparent;"
            f" color: {fg.name()}; font-size: 12px; font-weight: 600; padding: 0 6px; }}"
            f" QToolButton:hover {{ background: rgba({h.red()},{h.green()},{h.blue()},{h.alpha()}); }}"
            f" QToolButton:pressed {{ background: rgba({h.red()},{h.green()},{h.blue()},{min(255, h.alpha() * 2)}); }}")
        self.update()

    def paintEvent(self, ev) -> None:
        pal = _palette(self._dark)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(pal["card"])
        p.setPen(QPen(pal["border"], 1))
        p.drawRoundedRect(r, r.height() / 2 - 2, r.height() / 2 - 2)
        p.end()
