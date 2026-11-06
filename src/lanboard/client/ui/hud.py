"""Live Network HUD (S4): RTT sparkline, message and byte rates, pending ops.

Fed once per second by ``net.stats``::

    {msgs_in, msgs_out, bytes_in, bytes_out (per second), rtt_ms, rtt_hist, total_in, total_out}

and by ``controller.sync_status`` for the number of optimistic ops that are still
waiting for the server's echo. Charts are drawn with QPainter over the last
60 samples (one minute): a gradient-filled area, a soft glow under the line and
a marker on the newest sample. They repaint once per second, so they cost
nothing between samples. The "Batch live strokes" switch flips
``net.batching_enabled`` so the audience can see msgs/s jump when every pen
sample is sent on its own instead of being coalesced every ``LIVE_FLUSH_MS``.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from lanboard.common import config as C

from . import theme
from .widgets import ElidedLabel, ToggleSwitch

HISTORY = 60


def human_bytes(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def compact_bytes(n: float) -> str:
    """Like :func:`human_bytes` but at most 3 significant digits ("31 KB", "9.8 MB", "812 B").

    Used where a narrow side panel has to fit two values in one tile.
    """
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1000 or unit == "GB":
            if unit == "B" or abs(n) >= 10:
                return f"{n:.0f} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.0f} GB"


class Chart(QWidget):
    """A compact multi-series line chart with gradient area fills."""

    def __init__(self, title: str, series: list[tuple[str, str]], fmt=lambda v: f"{v:.0f}",
                 parent: QWidget | None = None, height: int = 86) -> None:
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
        p.setPen(QPen(QColor(t["console_line"]), 1))
        p.setBrush(QColor(t["console"]))
        p.drawRoundedRect(r, 12, 12)
        # title + current values
        p.setFont(theme.ui_font(10, QFont.Weight.Bold))
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(r.left() + 12, r.top() + 7, r.width() - 24, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title.upper())
        p.setFont(theme.mono_font(11, QFont.Weight.Bold))
        x = r.right() - 12
        for (label, tok), d in reversed(list(zip(self.series_meta, self.data))):
            txt = f"{label} {self.fmt(d[-1])}" if label else self.fmt(d[-1])
            w = p.fontMetrics().horizontalAdvance(txt)
            p.setPen(QColor(t[tok]))
            p.drawText(QRectF(x - w, r.top() + 7, w + 1, 16), Qt.AlignmentFlag.AlignVCenter, txt)
            x -= w + 12
        # plot area with a faint grid
        plot = QRectF(r.left() + 10, r.top() + 30, r.width() - 20, r.height() - 38)
        peak = max((max(d) for d in self.data), default=0.0)
        peak = peak * 1.2 if peak > 0 else 1.0
        grid = QPen(QColor(t["console_line"]), 1, Qt.PenStyle.DotLine)
        p.setPen(grid)
        for k in (0.0, 0.5, 1.0):
            y = plot.top() + plot.height() * k
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        p.setFont(theme.mono_font(9))
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(plot.left(), plot.top() - 1, plot.width(), 11),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, self.fmt(peak / 1.2))
        n = HISTORY - 1
        for (_label, tok), d in zip(self.series_meta, self.data):
            c = QColor(t[tok])
            path = QPainterPath()
            last = QPointF()
            for i, v in enumerate(d):
                last = QPointF(plot.left() + plot.width() * i / n, plot.bottom() - plot.height() * (v / peak))
                if i == 0:
                    path.moveTo(last)
                else:
                    path.lineTo(last)
            area = QPainterPath(path)
            area.lineTo(plot.right(), plot.bottom())
            area.lineTo(plot.left(), plot.bottom())
            area.closeSubpath()
            g = QLinearGradient(plot.topLeft(), plot.bottomLeft())
            g.setColorAt(0.0, theme.alpha(c, 0.38 if theme.manager().dark else 0.28))
            g.setColorAt(1.0, theme.alpha(c, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawPath(area)
            p.setBrush(Qt.BrushStyle.NoBrush)
            glow = QPen(theme.alpha(c, 0.22), 5)
            glow.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(glow)
            p.drawPath(path)
            line = QPen(c, 1.8)
            line.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(line)
            p.drawPath(path)
            # newest sample marker
            p.setPen(QPen(QColor(t["console"]), 2))
            p.setBrush(c)
            p.drawEllipse(last, 3.6, 3.6)
        p.end()


class MetricTile(QWidget):
    """A headline number on a console card with a coloured accent bar."""

    def __init__(self, caption: str, token: str, parent: QWidget | None = None, min_px: int = 0) -> None:
        super().__init__(parent)
        self.token = token
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        lay.setSpacing(1)
        self.caption = ElidedLabel(caption)
        self.caption.setProperty("role", "section")
        self.caption.setStyleSheet("font-size: 10px;")
        lay.addWidget(self.caption)
        self.value = ElidedLabel("–", min_px=min_px)    # min_px: shrink this far before eliding
        self.value.setProperty("role", "metric")
        self.value.setStyleSheet("font-size: 16px;")
        # never let a long value widen the panel: the tile elides (full text in the tooltip)
        lay.addWidget(self.value)
        self.setMinimumHeight(56)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(t["console_line"]), 1))
        p.setBrush(QColor(t["console"]))
        p.drawRoundedRect(r, 12, 12)
        c = QColor(t[self.token])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(r.left() + 5, r.top() + 11, 3, r.height() - 22), 1.5, 1.5)
        p.end()


class HudPanel(QWidget):
    """RTT/throughput charts and headline numbers."""

    def __init__(self, net, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConsoleBody")
        self.net = net
        self.controller = controller
        self.last_stats: dict = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 8)
        lay.setSpacing(8)

        # headline metrics
        row = QHBoxLayout()
        row.setSpacing(8)
        self.metric_labels: dict[str, QLabel] = {}
        self.tiles: dict[str, MetricTile] = {}
        for key, caption, tok, stretch in (("rtt", "RTT", "chart_rtt", 3), ("pending", "PENDING", "warn", 3),
                                           ("total", "TOTAL ↓ / ↑", "chart_in", 4)):
            tile = MetricTile(caption, tok, min_px=10 if key == "total" else 0)
            tile.setToolTip({"rtt": "Round-trip time of the last PING/PONG",
                             "pending": "Your ops sent but not yet confirmed by the server",
                             "total": "Bytes received / sent on this connection"}[key])
            row.addWidget(tile, stretch)
            self.metric_labels[key] = tile.value
            self.tiles[key] = tile
        self.metric_labels["total"].setStyleSheet("font-size: 12px; padding-top: 4px;")
        lay.addLayout(row)

        self.rtt_chart = Chart("Round-trip time", [("", "chart_rtt")], fmt=lambda v: f"{v:.1f} ms")
        self.msg_chart = Chart("Messages / s", [("↓", "chart_in"), ("↑", "chart_out")])
        self.byte_chart = Chart("Bytes / s", [("↓", "chart_in"), ("↑", "chart_out")], fmt=human_bytes)
        for ch in (self.rtt_chart, self.msg_chart, self.byte_chart):
            lay.addWidget(ch)

        brow = QHBoxLayout()
        self.batch_switch = ToggleSwitch("Batch live strokes")
        self.batch_switch.setChecked(bool(getattr(net, "batching_enabled", True)))
        self.batch_switch.setToolTip(f"On: pen samples are coalesced every {C.LIVE_FLUSH_MS} ms "
                                     f"(~{1000 // C.LIVE_FLUSH_MS} msgs/s while drawing).\n"
                                     "Off: one STROKE_LIVE frame per mouse move. Compare msgs/s!")
        self.batch_switch.toggled.connect(self._set_batching)
        brow.addWidget(self.batch_switch)
        self.batch_hint = ElidedLabel(f"every {C.LIVE_FLUSH_MS} ms")
        self.batch_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.batch_hint.setProperty("role", "mono")
        self.batch_hint.setStyleSheet("font-size: 11px;")
        brow.addWidget(self.batch_hint, 1)
        lay.addLayout(brow)

        net.stats.connect(self.on_stats)
        if hasattr(net, "rtt_changed"):
            net.rtt_changed.connect(self.on_rtt)
        controller.sync_status.connect(self.on_sync)
        theme.on_theme_change(self, self.update)

    def _set_batching(self, on: bool) -> None:
        self.net.batching_enabled = bool(on)
        self.batch_hint.setText(f"every {C.LIVE_FLUSH_MS} ms" if on else "every sample")

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
            self.metric_labels["rtt"].setText(self._ms(rtt))
        tin, tout = s.get("total_bytes_in", 0), s.get("total_bytes_out", 0)
        # compact units so both totals fit the tile at the default 400 px side panel
        self.metric_labels["total"].setText(f"{compact_bytes(tin)} / {compact_bytes(tout)}")
        self.tiles["total"].setToolTip(f"Bytes received / sent on this connection\n"
                                       f"{human_bytes(tin)} / {human_bytes(tout)}")

    @staticmethod
    def _ms(ms: float) -> str:
        return f"{ms:.1f} ms" if ms < 100 else f"{ms:.0f} ms"

    def on_rtt(self, ms: float) -> None:
        self.metric_labels["rtt"].setText(self._ms(ms))

    def on_sync(self, st: dict) -> None:
        self.metric_labels["pending"].setText(str(st.get("pending", 0)))
