"""Live Network HUD (S4): RTT sparkline, message and byte rates, pending ops.

Fed once per second by ``net.stats``::

    {msgs_in, msgs_out, bytes_in, bytes_out (per second), rtt_ms, rtt_hist, total_in, total_out}

and by ``controller.sync_status`` for the number of optimistic ops that are still
waiting for the server's echo. Charts are drawn with QPainter over the last
60 samples (one minute). The "Batch live strokes" switch flips
``net.batching_enabled`` so the audience can see msgs/s jump when every pen
sample is sent on its own instead of being coalesced every ``LIVE_FLUSH_MS``.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from lanboard.common import config as C

from . import theme
from .widgets import ToggleSwitch

HISTORY = 60


def human_bytes(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class Chart(QWidget):
    """A compact multi-series line chart with a soft area fill."""

    def __init__(self, title: str, series: list[tuple[str, str]], fmt=lambda v: f"{v:.0f}",
                 parent: QWidget | None = None, height: int = 58) -> None:
        super().__init__(parent)
        self.title = title
        self.series_meta = series                  # [(label, palette token)]
        self.data = [deque([0.0] * HISTORY, maxlen=HISTORY) for _ in series]
        self.fmt = fmt
        self.setMinimumHeight(height)
        self.setMinimumWidth(120)

    def push(self, *values: float) -> None:
        for d, v in zip(self.data, values):
            d.append(float(v or 0.0))
        self.update()

    def set_series(self, idx: int, values: list[float]) -> None:
        d = self.data[idx]
        d.clear()
        vals = list(values)[-HISTORY:]
        d.extend([0.0] * (HISTORY - len(vals)) + [float(v) for v in vals])
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(t["border"]), 1))
        p.setBrush(QColor(t["surface2"]))
        p.drawRoundedRect(r, 8, 8)
        # title + current values
        f = QFont(self.font())
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(r.left() + 8, r.top() + 4, r.width() - 16, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title.upper())
        x = r.right() - 8
        for (label, tok), d in reversed(list(zip(self.series_meta, self.data))):
            txt = f"{label} {self.fmt(d[-1])}" if label else self.fmt(d[-1])
            w = p.fontMetrics().horizontalAdvance(txt)
            p.setPen(QColor(t[tok]))
            p.drawText(QRectF(x - w, r.top() + 4, w, 14), Qt.AlignmentFlag.AlignVCenter, txt)
            x -= w + 10
        # plot area
        plot = QRectF(r.left() + 6, r.top() + 22, r.width() - 12, r.height() - 28)
        peak = max((max(d) for d in self.data), default=0.0)
        peak = peak * 1.15 if peak > 0 else 1.0
        p.setPen(QPen(QColor(t["border"]), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(plot.left(), plot.top() + plot.height() / 2),
                   QPointF(plot.right(), plot.top() + plot.height() / 2))
        n = HISTORY - 1
        for (label, tok), d in zip(self.series_meta, self.data):
            c = QColor(t[tok])
            path = QPainterPath()
            for i, v in enumerate(d):
                pt = QPointF(plot.left() + plot.width() * i / n, plot.bottom() - plot.height() * (v / peak))
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            area = QPainterPath(path)
            area.lineTo(plot.right(), plot.bottom())
            area.lineTo(plot.left(), plot.bottom())
            area.closeSubpath()
            fill = QColor(c)
            fill.setAlphaF(0.14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawPath(area)
            p.setPen(QPen(c, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.end()


class HudPanel(QWidget):
    """RTT/throughput charts and headline numbers."""

    def __init__(self, net, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePage")
        self.net = net
        self.controller = controller
        self.last_stats: dict = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(6)

        # headline metrics
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(0)
        self.metric_labels: dict[str, QLabel] = {}
        for col, (key, caption) in enumerate((("rtt", "RTT"), ("pending", "PENDING OPS"),
                                              ("total", "TOTAL ↓ / ↑"))):
            cap = QLabel(caption)
            cap.setProperty("role", "section")
            val = QLabel("–")
            val.setProperty("role", "metric")
            grid.addWidget(cap, 0, col)
            grid.addWidget(val, 1, col)
            self.metric_labels[key] = val
        lay.addLayout(grid)

        self.rtt_chart = Chart("Round-trip time", [("", "chart_rtt")], fmt=lambda v: f"{v:.1f} ms")
        self.msg_chart = Chart("Messages / s", [("↓", "chart_in"), ("↑", "chart_out")])
        self.byte_chart = Chart("Bytes / s", [("↓", "chart_in"), ("↑", "chart_out")], fmt=human_bytes)
        for ch in (self.rtt_chart, self.msg_chart, self.byte_chart):
            lay.addWidget(ch)

        row = QHBoxLayout()
        self.batch_switch = ToggleSwitch("Batch live strokes")
        self.batch_switch.setChecked(bool(getattr(net, "batching_enabled", True)))
        self.batch_switch.setToolTip(f"On: pen samples are coalesced every {C.LIVE_FLUSH_MS} ms "
                                     f"(~{1000 // C.LIVE_FLUSH_MS} msgs/s while drawing).\n"
                                     "Off: one STROKE_LIVE frame per mouse move. Compare msgs/s!")
        self.batch_switch.toggled.connect(self._set_batching)
        row.addWidget(self.batch_switch)
        row.addStretch(1)
        self.batch_hint = QLabel(f"flush every {C.LIVE_FLUSH_MS} ms")
        self.batch_hint.setProperty("role", "muted")
        row.addWidget(self.batch_hint)
        lay.addLayout(row)

        net.stats.connect(self.on_stats)
        if hasattr(net, "rtt_changed"):
            net.rtt_changed.connect(self.on_rtt)
        controller.sync_status.connect(self.on_sync)
        theme.manager().changed.connect(lambda *_: self.update())

    def _set_batching(self, on: bool) -> None:
        self.net.batching_enabled = bool(on)
        self.batch_hint.setText(f"flush every {C.LIVE_FLUSH_MS} ms" if on else "every sample sent")

    def on_stats(self, s: dict) -> None:
        self.last_stats = s
        self.msg_chart.push(s.get("msgs_in", 0), s.get("msgs_out", 0))
        self.byte_chart.push(s.get("bytes_in", 0), s.get("bytes_out", 0))
        hist = s.get("rtt_hist")
        if hist:
            self.rtt_chart.set_series(0, hist)
        else:
            self.rtt_chart.push(s.get("rtt_ms", 0))
        rtt = s.get("rtt_ms")
        if rtt is not None:
            self.metric_labels["rtt"].setText(f"{rtt:.1f} ms")
        self.metric_labels["total"].setText(
            f"{human_bytes(s.get('total_bytes_in', 0))} / {human_bytes(s.get('total_bytes_out', 0))}")

    def on_rtt(self, ms: float) -> None:
        self.metric_labels["rtt"].setText(f"{ms:.1f} ms")

    def on_sync(self, st: dict) -> None:
        self.metric_labels["pending"].setText(str(st.get("pending", 0)))
