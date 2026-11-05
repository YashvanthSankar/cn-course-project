"""Small reusable widgets that give the client a consistent, modern look.

* :class:`ToggleSwitch`: an animated iOS-style on/off switch (a QCheckBox underneath,
  so ``toggled``/``isChecked`` work as usual).
* :class:`Segmented`: a two-or-more option pill selector ("Join room | Host new room").
* :class:`ToastHost`: stacks short-lived notifications in a corner of a parent widget.
* :class:`Banner`: a persistent strip across the top of a parent widget (e.g. "reconnecting").
* :class:`Section`: a titled block whose body can be collapsed.
* :func:`hline`, :func:`label`: tiny helpers for building layouts.
"""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPointF, QPropertyAnimation, QRectF,
                            QSize, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QToolButton, QVBoxLayout, QWidget)

from . import theme


def label(text: str = "", role: str | None = None, parent: QWidget | None = None,
          word_wrap: bool = False) -> QLabel:
    """A QLabel with an optional QSS ``role`` (h1, h2, section, muted, hint, error, mono...)."""
    lab = QLabel(text, parent)
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(word_wrap)
    return lab


def hline() -> QFrame:
    """A thin horizontal divider."""
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {theme.tokens()['border']};")
    theme.manager().changed.connect(lambda t: f.setStyleSheet(f"background: {t['border']};"))
    return f


# ---------------------------------------------------------------------------
# toggle switch
# ---------------------------------------------------------------------------

class ToggleSwitch(QCheckBox):
    """A painted on/off switch with a sliding knob and an optional text label."""

    TRACK_W, TRACK_H = 36, 20

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 0.0                                    # 0 = off, 1 = on (animated)
        self._anim = QVariantAnimation(self, duration=140)
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
        off_c, on_c = QColor(t["border_strong"]), QColor(t["accent"])
        mix = QColor(
            int(off_c.red() + (on_c.red() - off_c.red()) * self._pos),
            int(off_c.green() + (on_c.green() - off_c.green()) * self._pos),
            int(off_c.blue() + (on_c.blue() - off_c.blue()) * self._pos))
        if not self.isEnabled():
            mix.setAlphaF(0.45)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix)
        p.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)
        d = self.TRACK_H - 4
        x = track.left() + 2 + (self.TRACK_W - 4 - d) * self._pos
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, y0 + 2, d, d))
        if self.text():
            p.setPen(QColor(t["text"] if self.isEnabled() else t["muted"]))
            p.drawText(QRectF(track.right() + 10, 0, self.width() - track.right() - 10, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


# ---------------------------------------------------------------------------
# port field
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# segmented control
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# overlays: toasts + banner
# ---------------------------------------------------------------------------

class _Toast(QFrame):
    """One notification bubble; fades in, waits, fades out, then deletes itself."""

    def __init__(self, text: str, level: str, parent: QWidget, timeout_ms: int) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        t = theme.tokens()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 14, 9)
        lay.setSpacing(9)
        accent = {"error": t["danger"], "warn": t["warn"], "success": t["success"]}.get(level, t["accent"])
        ic = QLabel()
        name = {"error": "warn", "warn": "warn", "success": "check"}.get(level, "info")
        ic.setPixmap(theme.icon(name, accent if theme.manager().dark else
                                {"error": "#ff8a8a", "warn": "#ffc75a", "success": "#5fe0a0"}.get(level, "#9fb0ff"),
                                18).pixmap(18, 18))
        lay.addWidget(ic)
        msg = QLabel(text)
        msg.setWordWrap(len(text) > 50)          # short notices stay on one line
        msg.setMaximumWidth(340)
        lay.addWidget(msg)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._fade = QPropertyAnimation(self._fx, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        QTimer.singleShot(timeout_ms, self.dismiss)

    def mousePressEvent(self, _ev) -> None:  # noqa: N802 - click to dismiss
        self.dismiss()

    def dismiss(self) -> None:
        if self._fade.endValue() == 0.0:
            return
        self._fade.stop()
        self._fade.setStartValue(self._fx.opacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.deleteLater)
        self._fade.start()


class ToastHost(QObject):
    """Shows toasts stacked in the bottom-right corner of ``parent``."""

    MAX = 4

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._parent = parent
        self._toasts: list[_Toast] = []
        parent.installEventFilter(self)

    def show(self, text: str, level: str = "info", timeout_ms: int = 3800) -> None:
        toast = _Toast(text, level, self._parent, timeout_ms)
        toast.destroyed.connect(lambda *_: self._forget(toast))
        self._toasts.append(toast)
        while len(self._toasts) > self.MAX:
            self._toasts.pop(0).dismiss()
        toast.adjustSize()
        toast.show()
        toast.raise_()
        self._layout()

    def texts(self) -> list[str]:
        """Texts of visible toasts (used by tests)."""
        out = []
        for t in self._toasts:
            lab = t.findChildren(QLabel)
            out.append(lab[-1].text() if lab else "")
        return out

    def _forget(self, toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        try:
            self._layout()
        except RuntimeError:              # parent already destroyed (app shutting down)
            pass

    def _layout(self) -> None:
        y = self._parent.height() - 16
        for toast in reversed(self._toasts):
            try:
                toast.adjustSize()
                y -= toast.height()
                toast.move(self._parent.width() - toast.width() - 16, y)
                y -= 8
            except RuntimeError:              # C++ object already gone
                pass

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self._parent and ev.type() == QEvent.Type.Resize:
            self._layout()
        return False


class Banner(QFrame):
    """A rounded strip centred at the top of its parent, with an optional action button."""

    action = Signal()

    def __init__(self, parent: QWidget, offset: int = 12) -> None:
        super().__init__(parent)
        self.offset = offset                       # distance from the parent's top edge
        self.setObjectName("Banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 8, 8)
        lay.setSpacing(10)
        self._icon = QLabel()
        lay.addWidget(self._icon)
        self._text = QLabel()
        self._text.setStyleSheet("font-weight: 600;")
        lay.addWidget(self._text)
        self._button = QPushButton()
        self._button.setProperty("role", "ghost")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.clicked.connect(self.action)
        lay.addWidget(self._button)
        self._kind = "warn"
        self.hide()
        parent.installEventFilter(self)

    def text(self) -> str:
        return self._text.text()

    def show_message(self, text: str, kind: str = "warn", action_text: str | None = None) -> None:
        t = theme.tokens()
        self._kind = kind
        self.setProperty("kind", kind)
        theme.repolish(self)
        self._icon.setPixmap(theme.icon("warn" if kind == "warn" else "info",
                                        t["warn"] if kind == "warn" else t["accent"], 18).pixmap(18, 18))
        self._text.setText(text)
        self._button.setVisible(bool(action_text))
        self._button.setText(action_text or "")
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()

    def _place(self) -> None:
        par = self.parentWidget()
        self.adjustSize()
        self.move(max(8, (par.width() - self.width()) // 2), self.offset)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False


# ---------------------------------------------------------------------------
# collapsible section
# ---------------------------------------------------------------------------

class Section(QWidget):
    """A section with an uppercase title row (click to collapse) and a body widget."""

    def __init__(self, title: str, body: QWidget, parent: QWidget | None = None,
                 extra: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePage")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        header = QFrame()
        header.setObjectName("SectionHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(10, 6, 10, 4)
        self.toggle = QToolButton()
        self.toggle.setText(title.upper())
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.toggle.setStyleSheet("QToolButton { font-size: 11px; font-weight: 700; letter-spacing: 1px;"
                                  " background: transparent; padding: 2px; }")
        self.toggle.toggled.connect(self._on_toggle)
        hl.addWidget(self.toggle)
        hl.addStretch(1)
        if extra is not None:
            hl.addWidget(extra)
        lay.addWidget(header)
        self.body = body
        lay.addWidget(body)

    def _on_toggle(self, open_: bool) -> None:
        self.body.setVisible(open_)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if open_ else Qt.ArrowType.RightArrow)


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

