"""Small reusable widgets that give the client a consistent, modern look.

* :class:`ToggleSwitch`: an animated on/off switch (a QCheckBox underneath,
  so ``toggled``/``isChecked`` work as usual).
* :class:`Segmented`: a two-or-more option pill selector ("Join room | Host new room").
* :class:`FloatingCard`: a rounded card with a soft painted shadow. Base class of
  everything that floats over the canvas (tool palette, style bar, toasts, popovers).
* :class:`ToastHost`: stacks short-lived notifications at the top centre of a widget.
* :class:`Banner`: a persistent pill across the top of a widget (e.g. "reconnecting").
* :class:`Popover`: a card that opens under a button and closes on outside click / Esc.
* :class:`Section`: a titled block whose body can be collapsed.
* :class:`ElidedLabel`: a one-line QLabel that elides instead of widening its parent.
* :func:`hline`, :func:`label`: tiny helpers for building layouts.

All animations are ``QPropertyAnimation`` / ``QVariantAnimation`` of a few
hundred milliseconds; nothing blocks the event loop and nothing animates while
idle.
"""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRectF, QSize, Qt,
                            QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFontMetrics, QIntValidator, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from . import theme


def label(text: str = "", role: str | None = None, parent: QWidget | None = None,
          word_wrap: bool = False) -> QLabel:
    """A QLabel with an optional QSS ``role`` (h1, h2, section, muted, hint, error, mono...)."""
    lab = QLabel(text, parent)
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(word_wrap)
    return lab


class ElidedLabel(QLabel):
    """One line of text that shrinks with ``…`` instead of clipping or widening the panel.

    ``text()`` is the full text; when it is elided the tooltip shows all of it.
    With ``min_px`` the font first shrinks (down to that pixel size) before eliding.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None,
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight, min_px: int = 0) -> None:
        super().__init__(text, parent)
        self._mode = mode
        self._min_px = min_px
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self._sync_tip()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._sync_tip()

    def _sync_tip(self) -> None:
        cr = self.contentsRect()
        full = self.text()
        self.setToolTip(full if self.fontMetrics().horizontalAdvance(full) > cr.width() else "")

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.minimumWidth(), super().minimumSizeHint().height())

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        cr = self.contentsRect()
        font = self.font()
        if self._min_px and font.pixelSize() > 0:
            while font.pixelSize() > self._min_px and \
                    QFontMetrics(font).horizontalAdvance(self.text()) > cr.width():
                font.setPixelSize(font.pixelSize() - 1)
        p.setFont(font)
        fm = QFontMetrics(font)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(cr, int(self.alignment()) | int(Qt.AlignmentFlag.AlignVCenter),
                   fm.elidedText(self.text(), self._mode, cr.width()))
        p.end()


def hline() -> QFrame:
    """A thin horizontal divider."""
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {theme.tokens()['border']};")
    theme.on_theme_change(f, lambda: f.setStyleSheet(f"background: {theme.tokens()['border']};"))
    return f


def vsep(height: int = 22) -> QFrame:
    """A thin vertical divider used between groups of buttons."""
    f = QFrame()
    f.setObjectName("VSep")
    f.setFixedSize(1, height)
    return f


class ToggleSwitch(QCheckBox):
    """A painted on/off switch with a sliding knob and an optional text label."""

    TRACK_W, TRACK_H = 34, 20

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 0.0
        self._anim = QVariantAnimation(self, duration=160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    def _set_pos(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # noqa: N802 (Qt naming)
        super().setChecked(on)
        self._anim.stop()
        self._set_pos(1.0 if on else 0.0)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        w = self.TRACK_W + (10 + fm.horizontalAdvance(self.text()) if self.text() else 0)
        return QSize(w + 2, max(self.TRACK_H, fm.height()) + 4)

    def hitButton(self, pos) -> bool:  # noqa: N802 - whole widget is clickable
        return self.rect().contains(pos)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y0 = (self.height() - self.TRACK_H) / 2
        track = QRectF(1, y0, self.TRACK_W, self.TRACK_H)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["border_strong"]))
        p.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)
        if self._pos > 0:
            p.setOpacity(self._pos * (1.0 if self.isEnabled() else 0.45))
            p.setBrush(theme.accent_gradient(track, t))
            p.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)
            p.setOpacity(1.0)
        d = self.TRACK_H - 4
        x = track.left() + 2 + (self.TRACK_W - 4 - d) * self._pos
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(x, y0 + 2.6, d, d))
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, y0 + 2, d, d))
        if self.text():
            p.setPen(QColor(t["text"] if self.isEnabled() else t["muted"]))
            p.drawText(QRectF(track.right() + 10, 0, self.width() - track.right() - 10, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class PortEdit(QLineEdit):
    """A TCP port field (1..65535) with a spin-box-like ``value()`` API."""

    valueChanged = Signal(int)

    def __init__(self, value: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setValidator(QIntValidator(1, 65535, self))
        self.setMaxLength(5)
        self.setValue(value)
        self.textChanged.connect(lambda *_: self.valueChanged.emit(self.value()))

    def value(self) -> int:
        try:
            return int(self.text())
        except ValueError:
            return 0

    def setValue(self, v: int) -> None:  # noqa: N802
        self.setText(str(int(v)))


class Segmented(QFrame):
    """A row of mutually exclusive pill buttons. Emits ``changed(index)``."""

    changed = Signal(int)

    def __init__(self, options: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        for i, text in enumerate(options):
            b = QPushButton(text)
            b.setProperty("role", "segment")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b, i)
            lay.addWidget(b, 1)
            self.buttons.append(b)
        self.buttons[0].setChecked(True)
        self.group.idClicked.connect(self.changed)

    def index(self) -> int:
        return self.group.checkedId()

    def set_index(self, i: int) -> None:
        if 0 <= i < len(self.buttons) and i != self.index():
            self.buttons[i].setChecked(True)
            self.changed.emit(i)


class FloatingCard(QWidget):
    """A widget that paints itself as a rounded card with a soft shadow.

    ``SHADOW`` pixels around the card are kept transparent for the shadow, so
    the content layout must use :meth:`inner_margins`. Children are laid out
    normally on top of the painted card.
    """

    SHADOW = 10

    def __init__(self, parent: QWidget | None = None, radius: float = 14, padding: tuple = (8, 8, 8, 8)) -> None:
        super().__init__(parent)
        self.radius = radius
        self.fill_token = "float"
        self.border_color: str | QColor | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._padding = padding
        theme.manager().changed.connect(self._theme_changed)

    def _theme_changed(self, *_args) -> None:
        self.update()

    def inner_margins(self) -> tuple[int, int, int, int]:
        s = self.SHADOW
        l, t, r, b = self._padding
        return l + s, t + s - 2, r + s, b + s + 2

    def card_rect(self) -> QRectF:
        s = self.SHADOW
        return QRectF(self.rect()).adjusted(s, s - 2, -s, -s - 2)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = theme.tokens()
        theme.paint_card(p, self.card_rect(), self.radius, t[self.fill_token],
                         self.border_color or t["border"])
        self.paint_extra(p)
        p.end()

    def paint_extra(self, p: QPainter) -> None:
        """Hook for subclasses that paint on top of the card background."""


LEVEL_TOKEN = {"error": "danger", "warn": "warn", "success": "success", "info": "accent"}
LEVEL_ICON = {"error": "warn", "warn": "warn", "success": "check", "info": "info"}


class _Toast(FloatingCard):
    """One notification card: slides in, waits, fades out, then deletes itself."""

    def __init__(self, text: str, level: str, parent: QWidget, timeout_ms: int) -> None:
        super().__init__(parent, radius=12, padding=(10, 8, 14, 8))
        self.level = level if level in LEVEL_TOKEN else "info"
        self.text = text
        t = theme.tokens()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(*self.inner_margins())
        lay.setSpacing(10)
        self.ic = QLabel()
        self.ic.setFixedSize(26, 26)
        self.ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tok = LEVEL_TOKEN[self.level]
        self.ic.setPixmap(theme.icon(LEVEL_ICON[self.level], t[tok], 16).pixmap(16, 16))
        self.ic.setStyleSheet(f"background: {theme.rgba(t[tok], 0.16)}; border-radius: 13px;")
        lay.addWidget(self.ic, 0, Qt.AlignmentFlag.AlignTop)
        self.msg = QLabel(text)
        self.msg.setWordWrap(len(text) > 56)
        self.msg.setMaximumWidth(420)
        self.msg.setStyleSheet("font-weight: 500;")
        lay.addWidget(self.msg, 1)
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._fade = QPropertyAnimation(self._fx, b"opacity", self)
        self._fade.setDuration(200)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._move = QPropertyAnimation(self, b"pos", self)
        self._move.setDuration(240)
        self._move.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._placed = False
        self._closing = False
        QTimer.singleShot(timeout_ms, self.dismiss)

    def paint_extra(self, p: QPainter) -> None:
        t = theme.tokens()
        r = self.card_rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t[LEVEL_TOKEN[self.level]]))
        p.drawRoundedRect(QRectF(r.left() + 6, r.top() + 10, 3, r.height() - 20), 1.5, 1.5)

    def glide_to(self, pos: QPoint) -> None:
        if not self._placed:
            self._placed = True
            self.move(pos.x(), pos.y() - 14)
        if self._move.endValue() == pos and self._move.state() == QPropertyAnimation.State.Running:
            return
        self._move.stop()
        self._move.setStartValue(self.pos())
        self._move.setEndValue(pos)
        self._move.start()

    def mousePressEvent(self, _ev) -> None:  # noqa: N802 - click to dismiss
        self.dismiss()

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._fade.stop()
        self._fade.setStartValue(self._fx.opacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.deleteLater)
        self._fade.start()


class ToastHost(QObject):
    """Stacks toasts at the top centre of ``parent`` (newest on top).

    ``top_offset`` (optional callable) returns how far below the parent's top
    edge the stack starts, so toasts never cover a visible banner.
    """

    MAX = 4

    def __init__(self, parent: QWidget, top_offset=None) -> None:
        super().__init__(parent)
        self._parent = parent
        self._toasts: list[_Toast] = []
        self._groups: dict[str, dict] = {}
        self.top_offset = top_offset or (lambda: 14)
        parent.installEventFilter(self)

    def show(self, text: str, level: str = "info", timeout_ms: int = 4200) -> None:
        for old in self._toasts:
            if old.text == text and not old._closing:
                return
        self._show(text, level, timeout_ms)

    def show_grouped(self, group: str, item: str, verb: str, level: str = "info", window_ms: int = 900) -> None:
        """Collect bursts ("Asha joined", "Ravi joined"...) into one toast.

        Items arriving within ``window_ms`` of each other are shown as
        "Asha, Ravi and 3 others joined" once the burst is over.
        """
        pend = self._groups.setdefault(group, {"items": [], "timer": None})
        pend["items"].append(item)
        if pend["timer"] is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda g=group, v=verb, lv=level: self._flush_group(g, v, lv))
            pend["timer"] = timer
        pend["timer"].start(window_ms)

    def _flush_group(self, group: str, verb: str, level: str) -> None:
        pend = self._groups.pop(group, None)
        if not pend or not pend["items"]:
            return
        items = list(dict.fromkeys(pend["items"]))
        if len(items) == 1:
            text = f"{items[0]} {verb}"
        elif len(items) == 2:
            text = f"{items[0]} and {items[1]} {verb}"
        else:
            text = f"{items[0]}, {items[1]} and {len(items) - 2} other{'s' if len(items) > 3 else ''} {verb}"
        self._show(text, level, 4200)

    def _show(self, text: str, level: str, timeout_ms: int) -> None:
        toast = _Toast(text, level, self._parent, timeout_ms)
        toast.destroyed.connect(lambda *_: self._forget(toast))
        self._toasts.insert(0, toast)
        while len(self._toasts) > self.MAX:
            self._toasts.pop().dismiss()
        toast.adjustSize()
        toast.show()
        toast.raise_()
        self._layout()

    def texts(self) -> list[str]:
        """Texts of visible toasts (used by tests)."""
        return [t.text for t in self._toasts]

    def _forget(self, toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        try:
            self._layout()
        except RuntimeError:
            pass

    def _layout(self) -> None:
        y = int(self.top_offset()) - FloatingCard.SHADOW + 2
        for toast in self._toasts:
            try:
                toast.adjustSize()
                x = (self._parent.width() - toast.width()) // 2
                toast.glide_to(QPoint(x, y))
                toast.raise_()
                y += toast.height() - FloatingCard.SHADOW - 4
            except RuntimeError:
                pass

    def relayout(self) -> None:
        self._layout()

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self._parent and ev.type() == QEvent.Type.Resize:
            self._layout()
        return False


class Banner(FloatingCard):
    """A pill centred at the top of its parent, with an optional action button."""

    action = Signal()

    def __init__(self, parent: QWidget, offset: int = 12) -> None:
        super().__init__(parent, radius=18, padding=(12, 5, 6, 5))
        self.offset = offset
        self.setObjectName("Banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(*self.inner_margins())
        lay.setSpacing(10)
        self._icon = QLabel()
        lay.addWidget(self._icon)
        self._text = QLabel()
        self._text.setStyleSheet("font-weight: 600;")
        lay.addWidget(self._text)
        self._button = QPushButton()
        self._button.setProperty("role", "soft")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.clicked.connect(self.action)
        lay.addWidget(self._button)
        self._kind = "warn"
        self.hide()
        parent.installEventFilter(self)
        theme.on_theme_change(self, lambda: self._restyle())

    def text(self) -> str:
        return self._text.text()

    def _restyle(self) -> None:
        t = theme.tokens()
        tok = "warn" if self._kind == "warn" else ("danger" if self._kind == "error" else "accent")
        self.border_color = theme.alpha(t[tok], 0.55)
        self._icon.setPixmap(theme.icon("warn" if self._kind in ("warn", "error") else "info",
                                        t[tok], 18).pixmap(18, 18))
        self.update()

    def show_message(self, text: str, kind: str = "warn", action_text: str | None = None) -> None:
        self._kind = kind
        self.setProperty("kind", kind)
        self._restyle()
        self._text.setText(text)
        self._button.setVisible(bool(action_text))
        self._button.setText(action_text or "")
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()

    def bottom(self) -> int:
        """Bottom edge of the visible card (for stacking toasts under it)."""
        return self.y() + self.height() - self.SHADOW if self.isVisible() else 0

    def _place(self) -> None:
        par = self.parentWidget()
        self.adjustSize()
        self.move(max(0, (par.width() - self.width()) // 2), self.offset - self.SHADOW + 2)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False


class Popover(FloatingCard):
    """A card shown under an anchor widget inside the same top-level window.

    It is a child widget (not a separate popup window), so it is themed like
    everything else and shows up in window screenshots. A click outside it or
    Esc closes it. It fades in for 140 ms.
    """

    closed = Signal()

    def __init__(self, window: QWidget, radius: float = 16, padding: tuple = (18, 16, 18, 16)) -> None:
        super().__init__(window, radius=radius, padding=padding)
        self.hide()
        self._anchor: QWidget | None = None
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(1.0)
        self.setGraphicsEffect(self._fx)
        self._fade = QPropertyAnimation(self._fx, b"opacity", self)
        self._fade.setDuration(140)

    def open_below(self, anchor: QWidget, align_right: bool = True) -> None:
        win = self.parentWidget()
        self._anchor = anchor
        self.adjustSize()
        a = anchor.mapTo(win, QPoint(0, anchor.height()))
        x = a.x() + anchor.width() - self.width() + self.SHADOW if align_right else a.x() - self.SHADOW
        x = max(4, min(x, win.width() - self.width() - 4))
        self.move(x, a.y() + 2)
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        QApplication.instance().installEventFilter(self)

    def close_popover(self) -> None:
        if self.isVisible():
            self.hide()
            self.closed.emit()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        et = ev.type()
        if et == QEvent.Type.MouseButtonPress and self.isVisible():
            gp = ev.globalPosition().toPoint()
            inside = self.rect().contains(self.mapFromGlobal(gp))
            on_anchor = self._anchor is not None and self._anchor.rect().contains(self._anchor.mapFromGlobal(gp))
            if not inside:
                self.close_popover()
                return on_anchor
        elif et == QEvent.Type.KeyPress and self.isVisible() and ev.key() == Qt.Key.Key_Escape:
            self.close_popover()
            return True
        return False


class Section(QWidget):
    """A section with an uppercase title row (click to collapse) and a body widget."""

    def __init__(self, title: str, body: QWidget, parent: QWidget | None = None,
                 extra: QWidget | None = None, icon_name: str | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePage")
        self.icon_name = icon_name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        header = QFrame()
        header.setObjectName("SectionHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 10, 12, 4)
        self.toggle = QToolButton()
        self.toggle.setText(title.upper())
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setIconSize(QSize(14, 14))
        self.toggle.toggled.connect(self._on_toggle)
        hl.addWidget(self.toggle)
        hl.addStretch(1)
        if extra is not None:
            hl.addWidget(extra)
        lay.addWidget(header)
        self.body = body
        lay.addWidget(body)
        self._retheme()
        theme.on_theme_change(self, lambda: self._retheme())

    def _retheme(self) -> None:
        t = theme.tokens()
        self.toggle.setStyleSheet(
            "QToolButton { font-size: 11px; font-weight: 700; background: transparent; border: none;"
            f" padding: 2px; color: {t['text2']}; }}"
            f" QToolButton:checked {{ background: transparent; color: {t['text2']}; }}"
            f" QToolButton:hover {{ color: {t['accent']}; }}")
        self.toggle.setIcon(theme.icon("chevron_down" if self.toggle.isChecked() else "chevron_right",
                                       t["muted"], 14))

    def _on_toggle(self, open_: bool) -> None:
        self.body.setVisible(open_)
        self._retheme()


class ColorDot(QWidget):
    """A filled circle (participant colour, online state ring)."""

    def __init__(self, color: str = "#888", size: int = 12, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._online = True
        self.setFixedSize(size + 4, size + 4)

    def set_color(self, c: str, online: bool = True) -> None:
        self._color = QColor(c)
        self._online = online
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(2, 2, self.width() - 4, self.height() - 4)
        if self._online:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._color)
        else:
            p.setPen(QPen(self._color, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        p.end()
