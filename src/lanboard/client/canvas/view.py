"""CanvasView: the interactive whiteboard widget (see docs/CLIENT_API.md).

Responsibilities
  * routes mouse/keyboard input to the active tool (``tools.py``)
  * pan (pan tool, middle mouse, space+drag, trackpad scroll) and zoom
    (wheel around the cursor, pinch, ``zoom_fit``/``zoom_reset``), clamped 0.1x-8x
  * presence: ``send_cursor`` on every move, Alt+click "look here" ping
  * presentation mode (S10): broadcast my viewport / smoothly follow someone's.
    Any pan or zoom the *user* makes (hand tool, space/middle drag, wheel,
    pinch, keyboard, scrollbars, minimap, zoom buttons) ends follow mode; the
    follow animation itself does not. ``follow_changed`` reports both.
  * board look: light/dark paper and dot/line/no grid (``set_board_theme``,
    ``set_grid``). The dark remap is display-only, exports stay light.
  * authorship lens (S11): hover text + spotlight dimming
  * inline text / sticky editors
  * image insert, PDF import (S9), export PNG/SVG/PDF, time-lapse preview (S6)

Board items are never created here. Everything goes through controller
methods and is rendered by :class:`~lanboard.client.canvas.scene.CanvasScene`.
"""

from __future__ import annotations

import math
import os
import time

from PySide6.QtCore import (QBuffer, QByteArray, QCoreApplication, QEasingCurve, QEvent, QEventLoop, QIODevice,
                            QMarginsF, QPointF, QRectF, QSize, QSizeF, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QImage, QPainter, QPageLayout, QPageSize, QPdfWriter, QPixmap, QTextCursor,
                           QTransform)
from PySide6.QtWidgets import QGraphicsTextItem, QGraphicsView, QLabel, QStyle

from lanboard.common import config as C

from .items import (ACCENT, OVERLAY_Z, STICKY_PAD, STICKY_TEXT, StickyItem, TextItem, display_color, qcolor,
                    ui_font)
from .scene import CanvasScene
from .tools import Tool, build_tools

MIN_ZOOM, MAX_ZOOM = 0.1, 8.0
ZOOM_STEP = 1.25
MAX_IMAGE_SIDE = 2048
EXPORT_MARGIN = 40.0
PDF_RENDER_SCALE = 1.5
DEFAULT_COLOR = "#1e1e1e"


def encode_image(img: QImage, max_side: int = MAX_IMAGE_SIDE, limit: int = C.MAX_BLOB) -> tuple[bytes, str, QImage]:
    """Scale ``img`` to at most ``max_side`` px and encode it under ``limit`` bytes.

    PNG is used for small or simple images (it is lossless and keeps transparency).
    If the PNG is large, as photos and scanned pages usually are, we switch to
    JPEG on a white background and lower the quality or size until it fits.
    """
    if max(img.width(), img.height()) > max_side:
        img = img.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

    def save(image: QImage, fmt: str, quality: int = -1) -> bytes:
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, fmt, quality)
        buf.close()
        return bytes(ba.data())

    png = save(img, "PNG")
    if len(png) <= min(limit, 1_500_000):
        return png, "image/png", img
    flat = img
    if img.hasAlphaChannel():
        flat = QImage(img.size(), QImage.Format.Format_RGB32)
        flat.fill(QColor("#ffffff"))
        p = QPainter(flat)
        p.drawImage(0, 0, img)
        p.end()
    quality = 88
    while True:
        jpg = save(flat, "JPEG", quality)
        if len(jpg) <= limit or (quality <= 40 and max(flat.width(), flat.height()) < 400):
            return jpg, "image/jpeg", flat
        if quality > 50:
            quality -= 12
        else:
            flat = flat.scaled(int(flat.width() * 0.8), int(flat.height() * 0.8),
                               Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def pdfium_page_to_qimage(page, scale: float) -> QImage | None:
    """Render a pypdfium2 page to a QImage on white paper, without going through PIL.

    pdfium writes bytes in B,G,R(,X/A) order. On little-endian machines that
    is exactly QImage's 32-bit RGB/ARGB memory layout, and 3-channel output maps
    to Format_BGR888. We copy the buffer so the QImage owns its pixels.
    """
    bitmap = page.render(scale=scale, prefer_bgrx=True, fill_color=(255, 255, 255, 255))
    try:
        w, h, stride = int(bitmap.width), int(bitmap.height), int(bitmap.stride)
        mode = str(getattr(bitmap, "mode", "") or "").upper()
        n = int(getattr(bitmap, "n_channels", 4) or 4)
        if n == 4:
            fmt = QImage.Format.Format_ARGB32 if mode == "BGRA" else QImage.Format.Format_RGB32
        elif n == 3:
            fmt = QImage.Format.Format_BGR888
        elif n == 1:
            fmt = QImage.Format.Format_Grayscale8
        else:
            return None
        buf = bytes(bitmap.buffer)
        return QImage(buf, w, h, stride, fmt).copy()
    finally:
        try:
            bitmap.close()
        except Exception:                            # pragma: no cover
            pass


class InlineEditor(QGraphicsTextItem):
    """Editable text placed exactly over a text object or inside a sticky note.

    Focus-out or Ctrl/Cmd+Enter commits, Esc cancels.
    """

    is_overlay = True

    def __init__(self, view: "CanvasView", oid: str | None, kind: str, origin: QPointF,
                 font_px: float, color: str, text: str = "", width: float | None = None) -> None:
        super().__init__()
        self.view, self.oid, self.kind, self.origin = view, oid, kind, origin
        self.font_px = font_px
        self.setZValue(OVERLAY_Z - 1)
        self.document().setDocumentMargin(0)
        self.setFont(ui_font(font_px))
        self.set_ink(color)
        if width is not None:
            self.setTextWidth(max(10.0, width))
        self.setPlainText(text)
        self.setPos(origin)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cur)

    def set_ink(self, color: str) -> None:
        """Text colour, remapped on the dark board like committed text (sticky text stays dark)."""
        c = qcolor(color)
        if self.kind == "text" and self.view.board_dark:
            c = display_color(c)
        self.setDefaultTextColor(c)

    def focusOutEvent(self, ev) -> None:
        super().focusOutEvent(ev)
        QTimer.singleShot(0, lambda: self.view.commit_editor(self))

    def sceneEvent(self, ev) -> bool:  # noqa: N802
        if ev.type() == QEvent.Type.ShortcutOverride:
            mods = ev.modifiers() & ~(Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier)
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape) or (
                    not mods and (ev.text().isprintable() and ev.text()
                                  or ev.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete))):
                ev.accept()
                return True
        return super().sceneEvent(ev)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            self.view.cancel_editor()
            return
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and ev.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self.view.commit_editor(self)
            return
        super().keyPressEvent(ev)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self.kind == "text":
            r = self.boundingRect().adjusted(-6, -4, 6, 4)
            s = max(self.view.scale_factor(), 1e-3)
            pen = painter.pen()
            pen.setColor(qcolor(ACCENT))
            pen.setCosmetic(True)
            pen.setWidthF(1.2)
            painter.setPen(pen)
            c = QColor("#1d2027" if self.view.board_dark else "#ffffff")
            c.setAlpha(210 if self.view.board_dark else 200)
            painter.setBrush(c)
            painter.drawRoundedRect(r, 4 / s, 4 / s)
        try:
            option.state &= ~QStyle.StateFlag.State_HasFocus
            option.state &= ~QStyle.StateFlag.State_Selected
        except Exception:                            # pragma: no cover - binding differences
            pass
        super().paint(painter, option, widget)


class CanvasView(QGraphicsView):
    """Interactive canvas widget. Public API documented in docs/CLIENT_API.md."""

    tool_changed = Signal(str)
    zoom_changed = Signal(float)
    hover_info = Signal(str)
    follow_changed = Signal(object)
    board_theme_changed = Signal(bool)
    view_rect_changed = Signal()

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.ctrl = controller
        self.canvas = CanvasScene(controller, self)
        self.setScene(self.canvas)

        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                            | QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, False)

        self.color = DEFAULT_COLOR
        self.pen_width = 3
        self.fill: str | None = None
        self.smart_ink = True
        self.font_size = 20

        self.tools: dict[str, Tool] = build_tools(self)
        self.tool: Tool = self.tools["pen"]

        self._dragging = False
        self._panning = False
        self._pan_last = QPointF()
        self._space = False
        self._editor: InlineEditor | None = None
        self._editor_press = False
        self._last_hover = ""

        self._presenting = False
        self._follow: str | None = None
        self._viewports: dict[str, dict] = {}
        self._vp_timer = QTimer(self)
        self._vp_timer.setSingleShot(True)
        self._vp_timer.setInterval(100)
        self._vp_timer.timeout.connect(self._send_viewport)
        self._anim: QVariantAnimation | None = None
        self._last_zoom = 1.0
        self._safe = (0, 0, 0, 0)

        self._badge = QLabel(self)
        self._badge.hide()
        self._preview_seq: int | None = None

        controller.viewport.connect(self._on_viewport)
        controller.me_changed.connect(lambda *_: self._permissions_changed())
        controller.room_changed.connect(lambda *_: self._permissions_changed())
        controller.participants_changed.connect(self._on_participants)
        self.canvas.selection_changed.connect(self.viewport().update)
        self.horizontalScrollBar().actionTriggered.connect(lambda *_: self._user_navigated())
        self.verticalScrollBar().actionTriggered.connect(lambda *_: self._user_navigated())

        self._style_badge()
        self.set_board_theme(self._initial_dark())
        self.centerOn(0, 0)
        self._refresh_cursor()

    def scale_factor(self) -> float:
        return abs(self.transform().m11()) or 1.0

    def _map_to_scene(self, p: QPointF) -> QPointF:
        """Sub-pixel accurate viewport -> scene mapping (mapToScene rounds to ints)."""
        inv, ok = self.viewportTransform().inverted()
        return inv.map(QPointF(p)) if ok else QPointF(self.mapToScene(p.toPoint()))

    def visible_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def can_edit(self) -> bool:
        return not self.canvas.is_preview and bool(self.ctrl.can_edit())

    def _notice(self, level: str, text: str) -> None:
        try:
            self.ctrl.notice.emit(level, text)
        except Exception:                           # pragma: no cover
            pass

    def set_tool(self, name: str) -> None:
        if name not in self.tools or self.tools[name] is self.tool:
            return
        self.commit_editor()
        self.tool.deactivate()
        self._dragging = False
        if name not in ("select",):
            self.canvas.set_selection([])
        self.tool = self.tools[name]
        self.tool.activate()
        self._refresh_cursor()
        self.tool_changed.emit(name)

    def set_color(self, hex_color: str) -> None:
        self.color = hex_color
        if self._editor is not None:
            self._editor.set_ink(hex_color)
        self._restyle({"stroke", "line", "arrow", "rect", "ellipse", "text", "sticky"}, "style.color", hex_color)
        if self.tool.name == "laser":
            self._refresh_cursor()

    def set_width(self, w: int) -> None:
        self.pen_width = max(1, int(w))
        self._restyle({"stroke", "line", "arrow", "rect", "ellipse"}, "style.width", float(self.pen_width))
        eraser = self.tools.get("eraser")
        if self.tool is eraser and eraser.ring.isVisible():
            eraser.ring.setVisible(False)

    def set_fill(self, hex_color: str | None) -> None:
        self.fill = hex_color or None
        sel = self.canvas.selected_items()
        if sel and self.can_edit():
            changes = {}
            for it in sel:
                t = it.obj.get("type")
                if t in ("rect", "ellipse"):
                    changes[it.oid] = {"style.fill": self.fill}
                elif t == "sticky" and self.fill:
                    changes[it.oid] = {"style.fill": self.fill}
            if changes:
                self.ctrl.update_many(changes)

    def set_font_size(self, px: int) -> None:
        """Extra (not in CLIENT_API): font size for new text objects."""
        self.font_size = max(6, min(200, int(px)))

    def set_smart_ink(self, on: bool) -> None:
        self.smart_ink = bool(on)

    def set_spotlight(self, user_id: str | None) -> None:
        self.canvas.set_spotlight(user_id)

    @staticmethod
    def _initial_dark() -> bool:
        """Start in the app's current theme (imported lazily: ui depends on canvas)."""
        try:
            from lanboard.client.ui import theme
            return bool(theme.manager().dark)
        except Exception:                            # pragma: no cover - ui package unavailable
            return False

    @property
    def board_dark(self) -> bool:
        return self.canvas.board_dark

    def set_board_theme(self, dark: bool) -> None:
        """Light (#fbfbfd) or dark (#15171d) board. Display only: object colours,
        ops and exports are unchanged; near-black/near-white ink swaps when painted."""
        dark = bool(dark)
        changed = dark != self.canvas.board_dark
        self.canvas.set_board_theme(dark)
        eraser = self.tools.get("eraser")
        if eraser is not None and hasattr(eraser, "apply_theme"):
            eraser.apply_theme(dark)
        if self._editor is not None:
            self._editor.set_ink(self._editor_color())
        self._style_badge()
        self.viewport().update()
        if changed:
            self.board_theme_changed.emit(dark)

    def set_grid(self, style: str) -> None:
        """Background grid: ``"dots"`` (default), ``"lines"`` or ``"none"``."""
        self.canvas.set_grid(style)
        self.viewport().update()

    @property
    def grid_style(self) -> str:
        return self.canvas.grid_style

    def _editor_color(self) -> str:
        ed = self._editor
        if ed is not None and ed.oid:
            obj = self.ctrl.view.get(ed.oid) or {}
            return (obj.get("style") or {}).get("color") or (STICKY_TEXT if ed.kind == "sticky" else DEFAULT_COLOR)
        return self.color

    def _restyle(self, types: set[str], key: str, value) -> None:
        """Apply a style change to the current selection as one batch (one undo step)."""
        sel = self.canvas.selected_items()
        if not sel or not self.can_edit():
            return
        changes = {it.oid: {key: value} for it in sel if it.obj.get("type") in types}
        if changes:
            self.ctrl.update_many(changes)

    def select_all(self) -> None:
        if self.tool.name != "select":
            self.set_tool("select")
        self.canvas.set_selection([o["id"] for o in self.canvas.source.live()])

    def delete_selection(self) -> None:
        ids = self.canvas.selected_ids
        if ids and self.can_edit():
            self.ctrl.delete_objects(ids)
            self.canvas.set_selection([])

    def bring_forward(self) -> None:
        """Ctrl+]: move the selection to the top of the stack (keeps its internal order)."""
        ids = self._sel_in_paint_order()
        if ids and self.can_edit():
            top = self.canvas.source.max_z()
            self.ctrl.update_many({oid: {"z": int(top + 1 + i)} for i, oid in enumerate(ids)})

    def send_backward(self) -> None:
        """Ctrl+[: move the selection to the bottom of the stack."""
        ids = self._sel_in_paint_order()
        if ids and self.can_edit():
            bottom = min((o.get("z", 0) for o in self.canvas.source.live()), default=0)
            self.ctrl.update_many({oid: {"z": int(bottom - len(ids) + i)} for i, oid in enumerate(ids)})

    def _sel_in_paint_order(self) -> list[str]:
        sel = set(self.canvas.selected_ids)
        return [o["id"] for o in self.canvas.source.live() if o["id"] in sel]

    def toggle_vote(self, oid: str) -> None:
        """Dot voting (S12): add or remove my user id in the sticky's ``votes`` list."""
        obj = self.ctrl.view.get(oid)
        me = self.canvas.my_id()
        if obj is None or not me or self.canvas.is_preview:
            return
        votes = [str(v) for v in (obj.get("votes") or obj.get("geom", {}).get("votes") or [])]
        votes = [v for v in votes if v != me] if me in votes else votes + [me]
        self.ctrl.update_object(oid, {"votes": votes})

    def editor_open(self) -> bool:
        return self._editor is not None

    def open_new_text(self, pos: QPointF) -> None:
        if not self.can_edit():
            return
        origin = QPointF(pos.x(), pos.y() - self.font_size * 0.65)
        self._open_editor(InlineEditor(self, None, "text", origin, self.font_size, self.color))

    def edit_object(self, oid: str) -> None:
        """Open the editor on an existing text or sticky object."""
        obj = self.ctrl.view.get(oid)
        item = self.canvas.items_by_id.get(oid)
        if obj is None or obj.get("deleted") or item is None or not self.can_edit():
            return
        self.commit_editor()
        g, s = obj.get("geom", {}), obj.get("style", {})
        off = obj.get("off") or [0, 0]
        if obj["type"] == "text":
            ed = InlineEditor(self, oid, "text", QPointF(g["x"] + off[0], g["y"] + off[1]),
                              float(s.get("font_size", 20) or 20), s.get("color") or DEFAULT_COLOR, g.get("text", ""))
        elif obj["type"] == "sticky":
            ed = InlineEditor(self, oid, "sticky", QPointF(g["x"] + off[0] + STICKY_PAD, g["y"] + off[1] + STICKY_PAD),
                              float(s.get("font_size", 16) or 16), s.get("color") or STICKY_TEXT, g.get("text", ""),
                              width=float(g["w"]) - 2 * STICKY_PAD)
        else:
            return
        item.editing = True
        item.update()
        self._open_editor(ed)

    def _open_editor(self, ed: InlineEditor) -> None:
        self.commit_editor()
        self._editor = ed
        self.canvas.addItem(ed)
        self.setFocus()
        ed.setFocus(Qt.FocusReason.MouseFocusReason)

    def _close_editor(self) -> InlineEditor | None:
        ed, self._editor = self._editor, None
        if ed is None:
            return None
        if ed.oid:
            it = self.canvas.items_by_id.get(ed.oid)
            if isinstance(it, (TextItem, StickyItem)):
                it.editing = False
                it.update()
        if ed.scene() is not None:
            self.canvas.removeItem(ed)
        return ed

    def commit_editor(self, which: InlineEditor | None = None) -> None:
        if self._editor is None or (which is not None and which is not self._editor):
            return
        ed = self._close_editor()
        text = ed.toPlainText()[: C.MAX_TEXT_LEN]
        if ed.oid:
            obj = self.ctrl.view.get(ed.oid)
            if obj is not None and not obj.get("deleted"):
                if obj["type"] == "text" and not text.strip():
                    self.ctrl.delete_objects([ed.oid])
                elif text != obj.get("geom", {}).get("text"):
                    self.ctrl.update_object(ed.oid, {"geom.text": text})
        elif text.strip():
            self.ctrl.add_object({
                "id": self.ctrl.new_id(), "type": "text", "off": [0, 0], "ts": round(time.time(), 1),
                "geom": {"x": round(ed.origin.x(), 1), "y": round(ed.origin.y(), 1), "text": text},
                "style": {"color": self.color, "font_size": ed.font_px},
            })
        self.setFocus()

    def cancel_editor(self) -> None:
        self._close_editor()
        self.setFocus()

    def _permissions_changed(self) -> None:
        if not self.can_edit():
            if self.tool.edits:
                self.tool.cancel()
                self._dragging = False
            if self._editor is not None:
                self.cancel_editor()
        self._refresh_cursor()

    def _refresh_cursor(self) -> None:
        vp = self.viewport()
        if self._panning:
            vp.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._space or self.canvas.is_preview and self.tool.name != "select":
            vp.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self.tool.edits and not self.can_edit():
            vp.setCursor(Qt.CursorShape.ForbiddenCursor)
        else:
            vp.setCursor(self.tool.cursor())

    def _editor_hit(self, view_pos: QPointF) -> bool:
        if self._editor is None:
            return False
        sp = self._map_to_scene(view_pos)
        return self._editor.sceneBoundingRect().adjusted(-4, -4, 4, 4).contains(sp)

    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        vpos = ev.position()
        pos = self._map_to_scene(vpos)
        if self._editor is not None:
            if self._editor_hit(vpos):
                self._editor_press = True
                super().mousePressEvent(ev)
                return
            self.commit_editor()
            if self.tool.name in ("text", "sticky"):
                return
        btn = ev.button()
        preview_pan = self.canvas.is_preview and self.tool.name != "select"
        if btn == Qt.MouseButton.MiddleButton or (btn == Qt.MouseButton.LeftButton and (
                self._space or self.tool.name == "pan" or preview_pan)):
            self._stop_animation()
            self._panning = True
            self._pan_last = vpos
            self._refresh_cursor()
            return
        if btn != Qt.MouseButton.LeftButton:
            return
        if ev.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._ping(pos)
            return
        if self.tool.edits and not self.can_edit():
            return
        if self.canvas.is_preview and self.tool.name == "select":
            return
        self._dragging = True
        self.tool.press(pos, ev)

    def mouseMoveEvent(self, ev) -> None:
        vpos = ev.position()
        pos = self._map_to_scene(vpos)
        if not self.canvas.is_preview:
            self.ctrl.send_cursor(pos.x(), pos.y())
        if self._panning:
            d = vpos - self._pan_last
            self._pan_last = vpos
            if d.x() or d.y():
                self._user_navigated()
            self._pan_pixels(-d.x(), -d.y())
            return
        if self._editor_press:
            super().mouseMoveEvent(ev)
            return
        if self._dragging:
            self.tool.move(pos, ev)
        else:
            if not (self.tool.edits and not self.can_edit()) and not self.canvas.is_preview:
                self.tool.hover(pos, ev)
            self._update_hover(pos)

    def mouseReleaseEvent(self, ev) -> None:
        pos = self._map_to_scene(ev.position())
        if self._panning:
            self._panning = False
            self._refresh_cursor()
            return
        if self._editor_press:
            self._editor_press = False
            super().mouseReleaseEvent(ev)
            return
        if self._dragging and ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.tool.release(pos, ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        if self._editor is not None and self._editor_hit(ev.position()):
            self._editor_press = True
            super().mouseDoubleClickEvent(ev)
            return
        if self.tool.name not in ("select", "text", "sticky"):
            self.mousePressEvent(ev)
            return
        if ev.button() == Qt.MouseButton.LeftButton and not self.canvas.is_preview:
            self.tool.double_click(self._map_to_scene(ev.position()), ev)

    def leaveEvent(self, ev) -> None:
        super().leaveEvent(ev)
        if self._last_hover:
            self._last_hover = ""
            self.hover_info.emit("")
        ring = getattr(self.tools.get("eraser"), "ring", None)
        if ring is not None and not self._dragging:
            ring.setVisible(False)

    def _update_hover(self, pos: QPointF) -> None:
        """Authorship lens: 'Drawn by NAME · HH:MM' for the object under the pointer."""
        it = self.canvas.object_item_at(pos, 4.0 / self.scale_factor())
        text = self.canvas.author_text(it.obj) if it is not None else ""
        if text != self._last_hover:
            self._last_hover = text
            self.hover_info.emit(text)

    def _ping(self, pos: QPointF) -> None:
        """Alt+click: 'look here' ping, shown locally at once and broadcast to the room."""
        me = self.canvas.my_id() or ""
        self.canvas.add_ping(pos.x(), pos.y(), self.ctrl.name_of(me) if me else "You",
                             self.ctrl.color_of(me) if me else ACCENT)
        self.ctrl.send_signal(f"ping:{pos.x():.0f},{pos.y():.0f}")

    def keyPressEvent(self, ev) -> None:
        if self._editor is not None:
            super().keyPressEvent(ev)
            return
        key, mods = ev.key(), ev.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Space:
            if not ev.isAutoRepeat():
                self._space = True
                self._refresh_cursor()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selection()
        elif ctrl and key == Qt.Key.Key_BracketRight:
            self.bring_forward()
        elif ctrl and key == Qt.Key.Key_BracketLeft:
            self.send_backward()
        elif ctrl and key == Qt.Key.Key_A:
            self.select_all()
        elif key == Qt.Key.Key_Escape:
            self.tool.cancel()
            self._dragging = False
            self.canvas.set_selection([])
        elif ctrl and key in (Qt.Key.Key_0,):
            self.zoom_reset()
        elif ctrl and key in (Qt.Key.Key_1,):
            self.zoom_fit()
        elif ctrl and key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
            self.zoom_in()
        elif ctrl and key == Qt.Key.Key_Minus:
            self.zoom_out()
        else:
            super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space = False
            self._refresh_cursor()
            return
        super().keyReleaseEvent(ev)

    def wheelEvent(self, ev) -> None:
        pd, ad = ev.pixelDelta(), ev.angleDelta()
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier or pd.isNull():
            dy = ad.y() if ad.y() else pd.y()
            if dy:
                self.zoom_at(1.0015 ** dy, ev.position())
        else:
            self._user_navigated()
            self._pan_pixels(-pd.x(), -pd.y())
        ev.accept()

    def viewportEvent(self, ev) -> bool:
        if ev.type() == QEvent.Type.NativeGesture:
            try:
                if ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                    self.zoom_at(1.0 + ev.value(), ev.position())
                    return True
            except AttributeError:                       # pragma: no cover
                pass
        return super().viewportEvent(ev)

    def zoom_at(self, factor: float, view_pos: QPointF) -> None:
        """Zoom keeping the scene point under ``view_pos`` fixed (a user zoom: ends follow)."""
        self._user_navigated()
        self._stop_animation()
        s = self.scale_factor()
        ns = max(MIN_ZOOM, min(MAX_ZOOM, s * factor))
        if abs(ns - s) < 1e-9:
            return
        before = self._map_to_scene(view_pos)
        self.setTransform(QTransform.fromScale(ns, ns))
        after = self._map_to_scene(view_pos)
        self._pan_pixels((before.x() - after.x()) * ns, (before.y() - after.y()) * ns)
        self._view_changed()

    def _pan_pixels(self, dx: float, dy: float) -> None:
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() + int(round(dx)))
        v.setValue(v.value() + int(round(dy)))

    def _set_view_rect(self, r: QRectF) -> None:
        """Show scene rect ``r`` (fitted, centred)."""
        vp = self.viewport().rect()
        if r.width() <= 0 or r.height() <= 0 or vp.width() <= 0:
            return
        ns = max(MIN_ZOOM, min(MAX_ZOOM, min(vp.width() / r.width(), vp.height() / r.height())))
        self.setTransform(QTransform.fromScale(ns, ns))
        self.centerOn(r.center())
        self._view_changed()

    def _stop_animation(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

    def animate_to(self, target: QRectF, ms: int = 420) -> None:
        """Smoothly fly the camera to ``target`` (centre lerp, size in log space)."""
        start = self.visible_rect()
        self._stop_animation()
        if ms <= 0 or not self.isVisible():
            self._set_view_rect(target)
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(ms)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        sc, tc = start.center(), target.center()
        sw, sh = max(start.width(), 1e-3), max(start.height(), 1e-3)
        tw, th = max(target.width(), 1e-3), max(target.height(), 1e-3)

        def step(t) -> None:
            t = float(t)
            c = sc + (tc - sc) * t
            w = math.exp(math.log(sw) + (math.log(tw) - math.log(sw)) * t)
            h = math.exp(math.log(sh) + (math.log(th) - math.log(sh)) * t)
            self._set_view_rect(QRectF(c.x() - w / 2, c.y() - h / 2, w, h))

        anim.valueChanged.connect(step)
        anim.start()
        self._anim = anim

    def zoom_in(self) -> None:
        self.zoom_at(ZOOM_STEP, QPointF(self.viewport().rect().center()))

    def zoom_out(self) -> None:
        self.zoom_at(1 / ZOOM_STEP, QPointF(self.viewport().rect().center()))

    def pan_to(self, center: QPointF) -> None:
        """Centre the view on scene point ``center`` (used by the minimap; a user pan)."""
        self._user_navigated()
        self._stop_animation()
        self.centerOn(center)
        self._view_changed()

    def set_safe_margins(self, left: int, top: int, right: int, bottom: int) -> None:
        """Viewport pixels covered by floating overlays on each side.

        ``zoom_fit`` (and other content framing) fits the target into the rest,
        so nothing lands under the tool palette or the style bar. Defaults to 0.
        """
        self._safe = tuple(max(0, int(v)) for v in (left, top, right, bottom))

    def safe_margins(self) -> tuple[int, int, int, int]:
        return self._safe

    def _safe_frame(self, r: QRectF) -> QRectF:
        """The full view rect that shows scene rect ``r`` fitted and centred inside the safe area."""
        vp = self.viewport().rect()
        left, top, right, bottom = self._safe
        sw, sh = vp.width() - left - right, vp.height() - top - bottom
        if sw < 80 or sh < 80 or r.width() <= 0 or r.height() <= 0:
            return r
        s = max(MIN_ZOOM, min(MAX_ZOOM, min(sw / r.width(), sh / r.height())))
        c = r.center() - QPointF((left - right) / 2 / s, (top - bottom) / 2 / s)
        w, h = vp.width() / s, vp.height() / s
        return QRectF(c.x() - w / 2, c.y() - h / 2, w, h)

    def zoom_fit(self) -> None:
        self._user_navigated()
        r = self.canvas.content_rect()
        if r.isNull() or r.isEmpty():
            self.zoom_reset()
            return
        self.animate_to(self._safe_frame(r.adjusted(-60, -60, 60, 60)))

    def zoom_reset(self) -> None:
        self._user_navigated()
        c = self.visible_rect().center()
        vp = self.viewport().rect()
        self.animate_to(QRectF(c.x() - vp.width() / 2, c.y() - vp.height() / 2, vp.width(), vp.height()))

    def _view_changed(self) -> None:
        s = self.scale_factor()
        self.canvas.view_scale = s
        if abs(s - self._last_zoom) > 1e-6:
            self._last_zoom = s
            self.canvas.selection.refresh()
            self.zoom_changed.emit(s)
        self._schedule_viewport()
        self.view_rect_changed.emit()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self._schedule_viewport()
        self.view_rect_changed.emit()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._place_badge()
        self._schedule_viewport()
        self.view_rect_changed.emit()

    def set_presenting(self, on: bool) -> None:
        self._presenting = bool(on)
        if self._presenting:
            self._send_viewport()

    def _schedule_viewport(self) -> None:
        if self._presenting and not self._vp_timer.isActive():
            self._vp_timer.start()

    def _send_viewport(self) -> None:
        if not self._presenting:
            return
        r = self.visible_rect()
        self.ctrl.send_viewport({"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()})

    def set_follow(self, user_id: str | None) -> None:
        """Follow ``user_id``'s viewport (presentation mode), or stop with ``None``."""
        uid = user_id or None
        if uid is not None and uid == self.canvas.my_id():
            uid = None
        changed = uid != self._follow
        self._follow = uid
        if uid is None:
            self._stop_animation()
        self._update_badge()
        if changed:
            self.follow_changed.emit(uid)
        if self._follow and self._follow in self._viewports:
            self._follow_to(self._viewports[self._follow], 450)

    @property
    def following(self) -> str | None:
        return self._follow

    def _user_navigated(self) -> None:
        """The user panned or zoomed by themselves: that ends follow mode.

        Only input handlers and the public zoom/pan methods call this. The follow
        animation moves the camera through ``animate_to`` / ``_set_view_rect``,
        which never do, so following keeps going while the presenter moves.
        """
        if self._follow is None:
            return
        name = self.ctrl.name_of(self._follow)
        self.set_follow(None)
        self._notice("info", f"Stopped following {name}")

    def _on_participants(self) -> None:
        """Stop following someone who has left the room."""
        if self._follow is None:
            return
        p = (getattr(self.ctrl, "participants", {}) or {}).get(self._follow)
        if p is None or p.get("online") is False:
            name = self.ctrl.name_of(self._follow)
            self.set_follow(None)
            self._notice("info", f"Stopped following {name} (left the room)")

    def _on_viewport(self, uid: str, rect: dict) -> None:
        self._viewports[uid] = rect
        if uid == self._follow:
            self._follow_to(rect, 280)

    def _follow_to(self, rect: dict, ms: int) -> None:
        try:
            r = QRectF(float(rect["x"]), float(rect["y"]), float(rect["w"]), float(rect["h"]))
        except (KeyError, TypeError, ValueError):
            return
        self.animate_to(r, ms)

    def show_preview_board(self, board) -> None:
        """Render ``board`` read-only (time-lapse). ``None`` returns to the live board."""
        if board is not None:
            self.tool.cancel()
            self._dragging = False
            self.cancel_editor()
        self.canvas.set_preview(board)
        self._preview_seq = getattr(board, "seq", 0) if board is not None else None
        self._update_badge()
        self._refresh_cursor()

    def _update_badge(self) -> None:
        if self._preview_seq is not None:
            text = f"⏪  Preview — seq {self._preview_seq}  ·  read-only"
        elif self._follow:
            text = f"👁  Following {self.ctrl.name_of(self._follow)}"
        else:
            self._badge.hide()
            return
        self._badge.setText(text)
        self._badge.adjustSize()
        self._place_badge()
        self._badge.show()
        self._badge.raise_()

    def _style_badge(self) -> None:
        """Dark pill on the light board, light pill on the dark board."""
        if self.board_dark:
            css = ("QLabel { background: rgba(236,238,244,235); color: #1b2130; border-radius: 13px;"
                   " padding: 5px 14px; font-size: 12px; font-weight: 600; }")
        else:
            css = ("QLabel { background: rgba(24,26,32,205); color: white; border-radius: 13px;"
                   " padding: 5px 14px; font-size: 12px; font-weight: 600; }")
        self._badge.setStyleSheet(css)

    def _place_badge(self) -> None:
        self._badge.move(max(8, (self.width() - self._badge.width()) // 2), 12)

    def _image_object(self, h: str, x: float, y: float, w: float, hgt: float, z: int | None = None) -> dict:
        obj = {"id": self.ctrl.new_id(), "type": "image", "off": [0, 0], "style": {},
               "ts": round(time.time(), 1),
               "geom": {"x": round(x, 1), "y": round(y, 1), "w": round(w, 1), "h": round(hgt, 1), "blob": h}}
        if z is not None:
            obj["z"] = z
        return obj

    def insert_image_file(self, path: str) -> bool:
        """Insert an image at the centre of the view (blob upload via the controller)."""
        if not self.can_edit():
            self._notice("warn", "You don't have permission to edit this board.")
            return False
        img = QImage(path)
        if img.isNull():
            self._notice("error", f"Could not open image: {os.path.basename(path)}")
            return False
        data, mime, img = encode_image(img)
        if len(data) > C.MAX_BLOB:
            self._notice("error", "Image is too large to share.")
            return False
        h = self.ctrl.put_blob(data, mime)
        self.canvas.cache_pixmap(h, QPixmap.fromImage(img))
        vis = self.visible_rect()
        k = min(1.0, vis.width() * 0.6 / img.width(), vis.height() * 0.6 / img.height())
        w, hh = img.width() * k, img.height() * k
        c = vis.center()
        self.ctrl.add_object(self._image_object(h, c.x() - w / 2, c.y() - hh / 2, w, hh))
        self._notice("info", f"Image added ({len(data) // 1024} KB)")
        return True

    def import_pdf(self, path: str) -> bool:
        """Import every page of a PDF as an image object, stacked vertically (S9).

        Pages are rasterised with pypdfium2 (imported lazily, so the app still runs
        without it). Each page is uploaded as a content-addressed blob.
        """
        if not self.can_edit():
            self._notice("warn", "You don't have permission to edit this board.")
            return False
        try:
            import pypdfium2 as pdfium
        except ImportError:
            self._notice("error", "PDF import needs the 'pypdfium2' package, which is not installed.")
            return False
        try:
            pdf = pdfium.PdfDocument(path)
        except Exception:
            self._notice("error", f"Could not open PDF: {os.path.basename(path)}")
            return False
        vis = self.visible_rect()
        gap, y = 40.0, vis.top() + 40.0
        z0 = int(self.ctrl.next_z())
        me = self.canvas.my_id() or ""
        ops, first = [], None
        try:
            n = len(pdf)
            for i in range(n):
                self._notice("info", f"Importing PDF… page {i + 1}/{n}")
                page = pdf[i]
                try:
                    w, hh = page.get_size()
                    if w <= 0 or hh <= 0:
                        continue
                    img = pdfium_page_to_qimage(page, PDF_RENDER_SCALE)
                finally:
                    page.close()
                if img is None or img.isNull():
                    continue
                data, mime, enc = encode_image(img)
                h = self.ctrl.put_blob(data, mime)
                self.canvas.cache_pixmap(h, QPixmap.fromImage(enc))
                obj = self._image_object(h, vis.center().x() - w / 2, y, w, hh, z=z0 + i)
                obj["owner"] = me
                ops.append({"k": "add", "obj": obj})
                first = first or QRectF(obj["geom"]["x"], y, w, hh)
                y += hh + gap
                QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        except Exception as e:
            self._notice("error", f"PDF import failed: {e}")
            return False
        finally:
            pdf.close()
        if not ops:
            self._notice("error", "The PDF has no renderable pages.")
            return False
        for k in range(0, len(ops), C.MAX_BATCH_OPS):
            chunk = ops[k:k + C.MAX_BATCH_OPS]
            self.ctrl.submit(chunk[0] if len(chunk) == 1 else {"k": "batch", "ops": chunk})
        self._notice("info", f"Imported {len(ops)} page(s) from {os.path.basename(path)}")
        if first is not None:
            self.animate_to(self._safe_frame(first.adjusted(-40, -40, 40, 40)))
        return True

    def export(self, path: str) -> bool:
        """Export the board (objects only) to .png (2x), .svg or .pdf."""
        self.commit_editor()
        content = self.canvas.content_rect()
        if content.isNull() or content.isEmpty():
            self._notice("warn", "Nothing to export: the board is empty.")
            return False
        src = content.adjusted(-EXPORT_MARGIN, -EXPORT_MARGIN, EXPORT_MARGIN, EXPORT_MARGIN)
        ext = os.path.splitext(path)[1].lower()
        ok = False
        try:
            if ext == ".svg":
                from PySide6.QtSvg import QSvgGenerator
                gen = QSvgGenerator()
                gen.setFileName(path)
                gen.setSize(QSize(int(src.width()), int(src.height())))
                gen.setViewBox(QRectF(0, 0, src.width(), src.height()))
                gen.setTitle("LANBoard export")
                gen.setDescription("Exported from LANBoard")
                p = QPainter(gen)
                self.canvas.render_export(p, QRectF(0, 0, src.width(), src.height()), src)
                p.end()
                ok = True
            elif ext == ".pdf":
                writer = QPdfWriter(path)
                writer.setTitle("LANBoard export")
                writer.setPageSize(QPageSize(QSizeF(src.width(), src.height()), QPageSize.Unit.Point,
                                             "LANBoard", QPageSize.SizeMatchPolicy.ExactMatch))
                writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
                p = QPainter(writer)
                self.canvas.render_export(p, QRectF(0, 0, writer.width(), writer.height()), src)
                p.end()
                ok = True
            else:
                if ext != ".png":
                    path += ".png"
                scale = 2.0
                while max(src.width(), src.height()) * scale > 16000 and scale > 0.25:
                    scale /= 2
                img = QImage(int(src.width() * scale), int(src.height() * scale),
                             QImage.Format.Format_ARGB32_Premultiplied)
                img.fill(QColor("#ffffff"))
                p = QPainter(img)
                self.canvas.render_export(p, QRectF(0, 0, img.width(), img.height()), src)
                p.end()
                ok = img.save(path, "PNG")
        except Exception as e:                           # pragma: no cover - defensive
            self._notice("error", f"Export failed: {e}")
            return False
        self._notice("success" if ok else "error",
                     f"Exported to {path}" if ok else f"Could not write {path}")
        return ok
