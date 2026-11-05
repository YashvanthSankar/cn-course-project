"""The collapsible right-hand side panel: segmented tabs with unread badges.

:class:`TabSwitcher` is a painted segmented control (icon + label per tab) with
a highlight that slides to the selected tab and small count badges ("3 unread
chat messages", "2 raised hands"). :class:`SideTabs` pairs it with a
``QStackedWidget`` and offers the subset of the ``QTabWidget`` API the main
window and tests use (``addTab``, ``count``, ``currentIndex``,
``setCurrentIndex``, ``tabText``, ``widget``, ``currentChanged``), plus
:meth:`SideTabs.set_badge` / :meth:`SideTabs.badge`.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from . import theme


class TabSwitcher(QWidget):
    """Painted segmented tabs. Emits ``selected(index)`` on click."""

    selected = Signal(int)
    H = 40

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tabs: list[dict] = []           # {text, icon, badge, kind}
        self.current = 0
        self._hover = -1
        self._x = 0.0                        # animated highlight position (in tab units)
        self._anim = QVariantAnimation(self, duration=220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_x)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.on_theme_change(self, self.update)

    def _set_x(self, v) -> None:
        self._x = float(v)
        self.update()

    def add(self, text: str, icon_name: str) -> int:
        self.tabs.append({"text": text, "icon": icon_name, "badge": 0, "kind": "accent"})
        self.update()
        return len(self.tabs) - 1

    def set_current(self, i: int, animate: bool = True) -> None:
        if not 0 <= i < len(self.tabs):
            return
        self.current = i
        self._anim.stop()
        if animate and self.isVisible():
            self._anim.setStartValue(self._x)
            self._anim.setEndValue(float(i))
            self._anim.start()
        else:
            self._set_x(float(i))

    def set_badge(self, i: int, n: int, kind: str = "accent") -> None:
        self.tabs[i]["badge"] = int(n)
        self.tabs[i]["kind"] = kind
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, self.H)

    def _seg_rect(self, i: float) -> QRectF:
        n = max(1, len(self.tabs))
        track = QRectF(self.rect()).adjusted(2, 3, -2, -3)
        w = (track.width() - 6) / n
        return QRectF(track.left() + 3 + w * i, track.top() + 3, w, track.height() - 6)

    def _index_at(self, x: float) -> int:
        for i in range(len(self.tabs)):
            if self._seg_rect(i).left() <= x < self._seg_rect(i).right():
                return i
        return -1

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        h = self._index_at(ev.position().x())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, _ev) -> None:  # noqa: N802
        self._hover = -1
        self.update()

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        i = self._index_at(ev.position().x())
        if i >= 0:
            self.selected.emit(i)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(self.rect()).adjusted(2, 3, -2, -3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["surface3"] if not theme.manager().dark else t["bg"]))
        p.drawRoundedRect(track, 11, 11)
        # sliding highlight
        hl = self._seg_rect(self._x)
        theme.paint_shadow(p, hl, 8, spread=4, strength=0.6, dy=1)
        p.setBrush(QColor(t["surface"] if not theme.manager().dark else t["surface3"]))
        p.drawRoundedRect(hl, 8, 8)
        f = theme.ui_font(12, QFont.Weight.Medium)
        bf = theme.ui_font(10, QFont.Weight.Bold)
        for i, tab in enumerate(self.tabs):
            r = self._seg_rect(i)
            active = i == self.current
            col = QColor(t["text"] if active else (t["text"] if i == self._hover else t["text2"]))
            icol = QColor(t["accent"] if active else t["muted"])
            p.setFont(f if not active else theme.ui_font(12, QFont.Weight.DemiBold))
            tw = p.fontMetrics().horizontalAdvance(tab["text"])
            badge_txt = (str(tab["badge"]) if tab["badge"] < 100 else "99+") if tab["badge"] else ""
            bw = max(16, QFontMetrics(bf).horizontalAdvance(badge_txt) + 9) if badge_txt else 0
            show_text = r.width() >= tw + 30 + (bw + 4 if bw else 0)
            # icon, label and badge are centred together as one group
            total = 16 + (6 + tw if show_text else 0) + (4 + bw if bw else 0)
            x0 = r.center().x() - total / 2
            theme.icon(tab["icon"], icol, 16).paint(p, QRectF(x0, r.center().y() - 8, 16, 16).toRect())
            x = x0 + 16
            if show_text:
                p.setPen(col)
                p.drawText(QRectF(x + 6, r.top(), tw + 2, r.height()), Qt.AlignmentFlag.AlignVCenter, tab["text"])
                x += 6 + tw
            if bw:
                p.setFont(bf)
                br = QRectF(x + 4, r.center().y() - 8, bw, 16)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t[tab["kind"]]))
                p.drawRoundedRect(br, 8, 8)
                p.setPen(QColor("#1b1300") if tab["kind"] == "warn" and theme.manager().dark else QColor("white"))
                p.drawText(br, Qt.AlignmentFlag.AlignCenter, badge_txt)
        p.end()


class SideTabs(QWidget):
    """Segmented tab header + stacked pages (a small QTabWidget replacement)."""

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 0)
        lay.setSpacing(6)
        self.switcher = TabSwitcher()
        lay.addWidget(self.switcher)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self._texts: list[str] = []
        self.switcher.selected.connect(self.setCurrentIndex)

    # -- QTabWidget-like API ------------------------------------------------------
    def addTab(self, widget: QWidget, text: str, icon_name: str = "dot") -> int:  # noqa: N802
        self.stack.addWidget(widget)
        self._texts.append(text)
        return self.switcher.add(text, icon_name)

    def count(self) -> int:
        return self.stack.count()

    def currentIndex(self) -> int:  # noqa: N802
        return self.stack.currentIndex()

    def currentWidget(self) -> QWidget:  # noqa: N802
        return self.stack.currentWidget()

    def widget(self, i: int) -> QWidget:
        return self.stack.widget(i)

    def setCurrentIndex(self, i: int) -> None:  # noqa: N802
        if not 0 <= i < self.count() or i == self.currentIndex():
            return
        self.stack.setCurrentIndex(i)
        self.switcher.set_current(i)
        self.currentChanged.emit(i)

    def tabText(self, i: int) -> str:  # noqa: N802
        return self._texts[i]

    # -- badges -------------------------------------------------------------------
    def set_badge(self, i: int, n: int, kind: str = "accent") -> None:
        self.switcher.set_badge(i, n, kind)

    def badge(self, i: int) -> int:
        return int(self.switcher.tabs[i]["badge"])
