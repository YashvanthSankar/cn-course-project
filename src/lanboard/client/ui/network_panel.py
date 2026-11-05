"""Network tab ("mission control"): HUD (S4), Inspector (S1) and Chaos Lab (S2).

The three tools are stacked in one scrollable column, each on its own console
card with a collapsible header, so a presenter can, for example, fold the HUD
away and bring the inspector and the chaos sliders on screen together. A live
header shows the endpoint and the current frame rate, and its dot blinks once
per stats sample (1 Hz), so the audience can see the link is alive.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from . import theme
from .chaos_panel import ChaosPanel
from .hud import HudPanel, human_bytes
from .inspector import InspectorPanel
from .widgets import Section


class LiveHeader(QWidget):
    """"● LIVE  ·  TCP 127.0.0.1:5050  ·  42 msg/s  ·  3.1 KB/s" on a gradient strip."""

    def __init__(self, net, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.net = net
        self.rate = ""
        self._blink = False
        self.setFixedHeight(64)
        net.stats.connect(self._on_stats)
        theme.on_theme_change(self, self.update)

    def _on_stats(self, s: dict) -> None:
        msgs = (s.get("msgs_in", 0) or 0) + (s.get("msgs_out", 0) or 0)
        byts = (s.get("bytes_in", 0) or 0) + (s.get("bytes_out", 0) or 0)
        self.rate = f"{msgs:.0f} msg/s · {human_bytes(byts)}/s"
        self._blink = True
        self.update()
        QTimer.singleShot(350, self._unblink)

    def _unblink(self) -> None:
        self._blink = False
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(12.5, 6.5, -12.5, -4.5)
        g = theme.accent_gradient(r, t)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 12, 12)
        # faint "packet" ticks across the strip: decoration that hints at a wire
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        x = r.left() + 8
        while x < r.right() - 4:
            p.drawLine(int(x), int(r.bottom() - 7), int(x), int(r.bottom() - 4))
            x += 6
        p.setFont(theme.ui_font(14, QFont.Weight.Bold))
        p.setPen(QColor("white"))
        p.drawText(QRectF(r.left() + 14, r.top() + 6, r.width() - 90, 22), Qt.AlignmentFlag.AlignVCenter,
                   "Network mission control")
        host, port = getattr(self.net, "host", ""), getattr(self.net, "port", "")
        p.setFont(theme.mono_font(11))
        p.setPen(QColor(255, 255, 255, 215))
        line = QRectF(r.left() + 14, r.top() + 29, r.width() - 28, 18)
        p.drawText(line, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"TCP {host}:{port}")
        p.drawText(line, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.rate)
        # LIVE chip
        chip = QRectF(r.right() - 70, r.top() + 7, 58, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 46))
        p.drawRoundedRect(chip, 11, 11)
        p.setBrush(QColor("#5dffb1") if self._blink else QColor(255, 255, 255, 150))
        p.drawEllipse(QRectF(chip.left() + 9, chip.center().y() - 4, 8, 8))
        p.setFont(theme.ui_font(10, QFont.Weight.Bold))
        p.setPen(QColor("white"))
        p.drawText(chip.adjusted(22, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "LIVE")
        p.end()


class _ConsoleCard(QFrame):
    """A rounded card that holds one section in the mission-control column."""

    def __init__(self, section: Section, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 4)
        lay.addWidget(section)


class NetworkPanel(QWidget):
    def __init__(self, net, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePage")
        self.hud = HudPanel(net, controller)
        self.inspector = InspectorPanel(net)
        self.inspector.setMinimumHeight(500)        # table + detail pane always usable
        self.chaos = ChaosPanel(net)

        column = QWidget()
        column.setObjectName("SidePage")
        column.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cl = QVBoxLayout(column)
        cl.setContentsMargins(0, 0, 4, 12)
        cl.setSpacing(10)
        self.header = LiveHeader(net)
        cl.addWidget(self.header)
        self.sections = [Section("Live HUD", self.hud), Section("Inspector", self.inspector),
                         Section("Chaos Lab", self.chaos)]
        for sec in self.sections:
            card = _ConsoleCard(sec)
            wrap = QVBoxLayout()
            wrap.setContentsMargins(12, 0, 8, 0)
            wrap.addWidget(card)
            cl.addLayout(wrap)
        cl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(column)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)
