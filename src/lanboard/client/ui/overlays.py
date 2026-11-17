"""Window overlays and chrome pieces used by the main window.

* :class:`InvitePopover`: opens under the top bar's *Invite* button and shows the
  server address and the room code in large type, readable across a lab.
* :class:`ShortcutsOverlay`: the "?" cheat sheet that lists every keyboard shortcut.
* :class:`NetStrip`: the slim status strip at the bottom of the window: connection
  dot, host:port, RTT, seq, board hash, pending ops and zoom.
* :class:`ZoomPill`: a small zoom control (− 100% +, fit) used only when the canvas
  package does not provide its own ``ZoomControls`` widget.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout,
                               QWidget)

from . import theme
from .widgets import FloatingCard, Popover


class _BigValue(QWidget):
    """A large monospace value (address or code) on a tinted panel, painted once per change."""

    def __init__(self, px: int, gradient: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.text = ""
        self.px = px
        self.gradient = gradient
        self.setMinimumHeight(px + 26)

    def set_text(self, text: str) -> None:
        self.text = text
        fm = QFontMetrics(self._font())
        self.setMinimumWidth(fm.horizontalAdvance(text) + 36)
        self.update()

    def _font(self) -> QFont:
        f = theme.mono_font(self.px, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
        return f

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(t["border"]), 1))
        p.setBrush(QColor(t["surface2"]))
        p.drawRoundedRect(r, 12, 12)
        p.setFont(self._font())
        if self.gradient:
            p.setPen(QPen(theme.accent_gradient(r, t), 1))
        else:
            p.setPen(QColor(t["text"]))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text)
        p.end()


class InvitePopover(Popover):
    """How to join this room: server address + room code, big, with copy buttons."""

    copied = Signal(str)

    def __init__(self, window: QWidget) -> None:
        super().__init__(window, radius=18, padding=(22, 20, 22, 18))
        self.setObjectName("InvitePopover")
        self.setFixedWidth(470)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*self.inner_margins())
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.icon = QLabel()
        self.icon.setFixedSize(36, 36)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self.icon)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Invite people to this board")
        title.setProperty("role", "h2")
        titles.addWidget(title)
        self.sub = QLabel("Same Wi-Fi or switch. Open LANBoard → Join, pick this server "
                          "under Nearby servers, or type:")
        self.sub.setProperty("role", "hint")
        self.sub.setWordWrap(True)
        titles.addWidget(self.sub)
        head.addLayout(titles, 1)
        lay.addLayout(head)
        lay.addSpacing(6)

        self.addr_caption = QLabel("SERVER ADDRESS")
        self.addr_caption.setProperty("role", "section")
        lay.addWidget(self.addr_caption)
        self.addr = _BigValue(26)
        lay.addWidget(self.addr)
        self.others = QLabel("")
        self.others.setProperty("role", "mono")
        self.others.setWordWrap(True)
        lay.addWidget(self.others)
        lay.addSpacing(4)
        cap = QLabel("ROOM CODE")
        cap.setProperty("role", "section")
        lay.addWidget(cap)
        self.code = _BigValue(40, gradient=True)
        lay.addWidget(self.code)
        lay.addSpacing(8)

        row = QHBoxLayout()
        self.note = QLabel("Found automatically via LAN discovery (UDP broadcast).")
        self.note.setProperty("role", "muted")
        self.note.setStyleSheet("font-size: 11px;")
        self.note.setWordWrap(True)
        row.addWidget(self.note, 1)
        self.copy_btn = QPushButton("Copy invite")
        self.copy_btn.setProperty("role", "primary")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self.copy)
        row.addWidget(self.copy_btn)
        lay.addLayout(row)
        self.share_text = ""
        self.retheme()
        theme.on_theme_change(self, self.retheme)

    def retheme(self) -> None:
        t = theme.tokens()
        self.icon.setPixmap(theme.icon("invite", t["accent"], 20).pixmap(20, 20))
        self.icon.setStyleSheet(f"background: {t['accent_soft']}; border-radius: 18px;")
        self.copy_btn.setIcon(theme.icon("copy", "#ffffff", 16))

    def set_info(self, host: str, port: int, code: str, lan: list[str] | None = None) -> None:
        lan = list(lan or [])
        main = lan[0] if lan else host
        if main in ("127.0.0.1", "localhost", "::1") and not lan:
            self.addr_caption.setText("SERVER ADDRESS (this computer — share its LAN IP)")
        else:
            self.addr_caption.setText("SERVER ADDRESS")
        self.addr.set_text(f"{main} : {port}" if main else "–")
        self.code.set_text(code or "–")
        self.others.setText("also: " + "   ".join(f"{a}:{port}" for a in lan[1:4]) if len(lan) > 1 else "")
        self.others.setVisible(len(lan) > 1)
        self.share_text = f"LANBoard room {code} at {main}:{port}" if code else ""
        self.adjustSize()

    def copy(self) -> None:
        if self.share_text:
            QGuiApplication.clipboard().setText(self.share_text)
            self.copied.emit(self.share_text)
        self.close_popover()


def _keys(seq: str) -> str:
    """Show Ctrl as ⌘ on macOS, like Qt's native text for shortcuts."""
    import sys
    if sys.platform == "darwin":
        return seq.replace("Ctrl+", "⌘").replace("Shift+", "⇧").replace("Alt+", "⌥")
    return seq


SHORTCUTS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Tools", [("V", "Select"), ("H", "Pan"), ("P", "Pen"), ("M", "Highlighter"), ("E", "Eraser"),
               ("L", "Line"), ("A", "Arrow"), ("R", "Rectangle"), ("O", "Ellipse"), ("T", "Text"),
               ("N", "Sticky note"), ("K", "Laser pointer")]),
    ("Edit", [("Ctrl+Z", "Undo"), ("Ctrl+Shift+Z", "Redo"), ("Ctrl+Y", "Redo"), ("Delete", "Delete selection"),
              ("Ctrl+A", "Select all"), ("Ctrl+]", "Bring forward"), ("Ctrl+[", "Send backward"),
              ("Esc", "Cancel / deselect")]),
    ("View", [("Space + drag", "Pan the board"), ("Ctrl+0", "Reset zoom"), ("Ctrl+1", "Fit board"),
              ("Ctrl+B", "Toggle side panel"), ("Ctrl+Shift+M", "Toggle minimap"), ("F11", "Full screen")]),
    ("Session", [("Ctrl+S", "Save on server"), ("Ctrl+E", "Export PNG / SVG / PDF"),
                 ("Ctrl+Enter", "Focus chat"), ("?", "This cheat sheet")]),
]


class ShortcutsOverlay(QWidget):
    """A dimmed full-window overlay with a card listing the shortcuts. Click or Esc closes."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.hide()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def open(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()

    def event(self, ev) -> bool:
        if ev.type() == QEvent.Type.ShortcutOverride:
            ev.accept()
            return True
        return super().event(ev)

    def mousePressEvent(self, _ev) -> None:  # noqa: N802
        self.hide()

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        self.hide()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(5, 8, 18, 140))
        columns = [[SHORTCUTS[0]], [SHORTCUTS[1]], [SHORTCUTS[2], SHORTCUTS[3]]]
        col_w, row_h = 250, 27
        rows = max(sum(len(s[1]) + 2 for s in col) for col in columns)
        w, h = col_w * 3 + 72, rows * row_h + 110
        card = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        theme.paint_card(p, card, 20)
        p.setFont(theme.ui_font(20, QFont.Weight.Bold))
        p.setPen(QColor(t["text"]))
        p.drawText(QRectF(card.left() + 36, card.top() + 26, w, 30), Qt.AlignmentFlag.AlignVCenter,
                   "Keyboard shortcuts")
        p.setFont(theme.ui_font(12))
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(card.left(), card.top() + 26, w - 36, 30),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "press any key to close")
        key_font = theme.mono_font(11, QFont.Weight.Normal)
        fm = QFontMetrics(key_font)
        for ci, col in enumerate(columns):
            x = card.left() + 36 + ci * col_w
            y = card.top() + 76
            for title, items in col:
                p.setFont(theme.ui_font(11, QFont.Weight.Bold))
                p.setPen(QColor(t["accent"]))
                p.drawText(QRectF(x, y, col_w, row_h), Qt.AlignmentFlag.AlignVCenter, title.upper())
                y += row_h
                for keys, desc in items:
                    txt = _keys(keys)
                    kw = fm.horizontalAdvance(txt) + 14
                    kr = QRectF(x, y + 4, kw, row_h - 8)
                    p.setPen(QPen(QColor(t["border_strong"]), 1))
                    p.setBrush(QColor(t["surface2"]))
                    p.drawRoundedRect(kr, 5, 5)
                    p.setFont(key_font)
                    p.setPen(QColor(t["text"]))
                    p.drawText(kr, Qt.AlignmentFlag.AlignCenter, txt)
                    p.setFont(theme.ui_font(12))
                    p.setPen(QColor(t["text2"]))
                    dx = 74 if col is columns[0] else 104
                    p.drawText(QRectF(x + max(kw + 10, dx), y, col_w - dx, row_h), Qt.AlignmentFlag.AlignVCenter, desc)
                    y += row_h
                y += row_h * 0.6
        p.end()


class NetStrip(QWidget):
    """The slim bottom strip. ``labels`` maps keys to the QLabel showing them."""

    KEYS = (("state", "Connection state", False), ("server", "Server address", True),
            ("me", "You and your role", False), ("rtt", "Round-trip time (PING/PONG)", True),
            ("seq", "Last applied server sequence number", True),
            ("hash", "Board hash (consistency check)", True),
            ("pending", "Your ops not yet confirmed by the server", True), ("zoom", "Zoom", True))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NetStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(28)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(4)
        self.labels: dict[str, QLabel] = {}
        self.hover_label = QLabel("")
        for key, tip, mono in self.KEYS[:3]:
            self._add(lay, key, tip, mono)
        lay.addSpacing(12)
        lay.addWidget(self.hover_label, 1)
        for key, tip, mono in self.KEYS[3:]:
            self._add(lay, key, tip, mono)

    def _add(self, lay, key: str, tip: str, mono: bool) -> None:
        lab = QLabel("")
        lab.setToolTip(tip)
        if mono:
            lab.setProperty("role", "mono")
        lab.setContentsMargins(6, 0, 6, 0)
        lay.addWidget(lab)
        self.labels[key] = lab


class ZoomPill(FloatingCard):
    """− 100% + [fit]. Only used when ``canvas.minimap.ZoomControls`` is unavailable."""

    def __init__(self, view, parent: QWidget | None = None) -> None:
        super().__init__(parent, radius=12, padding=(4, 3, 4, 3))
        self.view = view
        lay = QHBoxLayout(self)
        lay.setContentsMargins(*self.inner_margins())
        lay.setSpacing(0)
        self.btns: dict[str, QToolButton] = {}
        for name, tip, fn in (("zoom_out", "Zoom out", lambda: self._zoom(1 / 1.2)),
                              ("pct", "Reset zoom (Ctrl+0)", lambda: self._call("zoom_reset")),
                              ("zoom_in", "Zoom in", lambda: self._zoom(1.2)),
                              ("fit", "Fit board (Ctrl+1)", lambda: self._call("zoom_fit"))):
            b = QToolButton()
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(30)
            b.setIconSize(QSize(16, 16))
            if name == "pct":
                b.setText("100%")
                b.setFixedWidth(54)
                b.setStyleSheet(f"font-family: {theme.mono_css()}; font-size: 11px;")
            else:
                b.setFixedWidth(30)
            b.clicked.connect(fn)
            lay.addWidget(b)
            self.btns[name] = b
        if hasattr(view, "zoom_changed"):
            view.zoom_changed.connect(self.set_zoom)
        self.retheme()
        theme.on_theme_change(self, self.retheme)

    def retheme(self) -> None:
        t = theme.tokens()
        for name in ("zoom_out", "zoom_in", "fit"):
            self.btns[name].setIcon(theme.icon(name, t["text2"], 16))

    def _call(self, name: str) -> None:
        fn = getattr(self.view, name, None)
        if callable(fn):
            fn()

    def _zoom(self, factor: float) -> None:
        fn = getattr(self.view, "zoom_at", None)
        if callable(fn):
            vp = self.view.viewport().rect()
            fn(factor, QPointF(vp.center()))

    def set_zoom(self, z: float) -> None:
        pct = z * 100 if z <= 50 else z
        self.btns["pct"].setText(f"{pct:.0f}%")

