"""Chaos Lab (S2): break the network on purpose and watch the protocol cope.

The sliders write straight to ``net.chaos`` (the client's inbound
``FaultInjector``): ``enabled``, ``latency_ms``, ``jitter_ms``, ``drop_pct``,
``dup_pct``. Faults are applied to frames *received* by this client only, so one
laptop can misbehave while the others stay clean, which makes for a clear demo:

* latency: every inbound frame waits ``latency_ms``; the RTT chart climbs;
* jitter: a random extra delay per frame, so frames arrive *out of order*; the
  controller still applies ops strictly by seq;
* drop: OP frames silently vanish, the controller sees a seq gap and sends RESYNC;
* duplicate: frames arrive twice; ops with an already applied seq are discarded;
* cut the cable: the TCP socket is aborted; the client reconnects with its resume
  token and catches up with only the ops it missed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from . import theme
from .widgets import ToggleSwitch

KNOBS = [
    ("latency_ms", "Latency", 1000, "ms", "Delays every inbound frame. Watch RTT rise in the HUD."),
    ("jitter_ms", "Jitter", 500, "ms", "Random extra delay, so frames arrive out of order. Ops are still applied by seq."),
    ("drop_pct", "Drop OPs", 50, "%", "Loses OP frames. The seq gap is detected and RESYNC fetches the missing ops."),
    ("dup_pct", "Duplicate", 50, "%", "Delivers frames twice. Already-applied seqs are ignored (idempotent)."),
]


class ChaosPanel(QWidget):
    """Enable switch, four sliders and the cut-the-cable button."""

    def __init__(self, net, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConsoleBody")
        self.net = net
        self.sliders: dict[str, QSlider] = {}
        self.value_labels: dict[str, QLabel] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 12)
        lay.setSpacing(8)
        intro = QLabel("Inject faults into frames this client <b>receives</b> and watch sync recover.")
        intro.setProperty("role", "hint")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.enable = ToggleSwitch("Enable fault injection")
        self.enable.setChecked(bool(getattr(self.chaos, "enabled", False)))
        self.enable.toggled.connect(self._set_enabled)
        lay.addWidget(self.enable)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)
        row = 0
        for attr, text, maximum, unit, help_text in KNOBS:
            name = QLabel(text)
            name.setStyleSheet("font-weight: 600;")
            grid.addWidget(name, row, 0)
            val = QLabel()
            val.setObjectName("ChaosValue")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setMinimumWidth(64)
            grid.addWidget(val, row, 2)
            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(0, maximum)
            s.setSingleStep(1)
            s.setPageStep(max(1, maximum // 10))
            s.setValue(int(getattr(self.chaos, attr, 0) or 0))
            s.valueChanged.connect(lambda v, a=attr, u=unit: self._set_value(a, v, u))
            grid.addWidget(s, row, 1)
            self.sliders[attr] = s
            self.value_labels[attr] = val
            self._show_value(attr, s.value(), unit)
            hint = QLabel(help_text)
            hint.setProperty("role", "muted")
            hint.setStyleSheet("font-size: 11px;")
            hint.setWordWrap(True)
            grid.addWidget(hint, row + 1, 0, 1, 3)
            row += 2
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        self.reset_btn = QPushButton("Reset all to zero")
        self.reset_btn.setProperty("role", "ghost")
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self.cut_btn = QPushButton("  Cut the cable")
        self.cut_btn.setProperty("role", "danger")
        self.cut_btn.setMinimumHeight(46)
        self.cut_btn.setStyleSheet("font-size: 14px;")
        self.cut_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cut_btn.setToolTip("Abort the TCP connection right now. The client reconnects, resumes its session "
                                "and receives only the ops it missed. Edits made meanwhile are queued.")
        self.cut_btn.clicked.connect(self.cut)
        lay.addWidget(self.cut_btn)
        self.counters = QLabel("")
        self.counters.setProperty("role", "mono")
        self.counters.setStyleSheet("font-size: 11px;")
        lay.addWidget(self.counters)
        self._counter_timer = QTimer(self)
        self._counter_timer.setInterval(1000)
        self._counter_timer.timeout.connect(self.update_counters)
        self._counter_timer.start()
        self.update_counters()
        cut_hint = QLabel("Keep drawing while offline: your edits queue up and replay on reconnect.")
        cut_hint.setProperty("role", "hint")
        cut_hint.setWordWrap(True)
        lay.addWidget(cut_hint)
        lay.addStretch(1)

        self._sync_enabled_ui()
        theme.on_theme_change(self, self.retheme)
        self.retheme()

    @property
    def chaos(self):
        return getattr(self.net, "chaos", None)

    def retheme(self) -> None:
        t = theme.tokens()
        self.cut_btn.setIcon(theme.icon("scissors", "#ffffff", 20))
        for attr, lab in self.value_labels.items():
            active = self.enable.isChecked() and self.sliders[attr].value() > 0
            tok = "warn" if active else "muted"
            lab.setStyleSheet(f"QLabel#ChaosValue {{ font-family: {theme.mono_css()}; font-size: 11px;"
                              f" font-weight: 700; color: {t[tok]}; background: {theme.rgba(t[tok], 0.13)};"
                              f" border-radius: 6px; padding: 2px 6px; }}")

    def _set_enabled(self, on: bool) -> None:
        if self.chaos is not None:
            self.chaos.enabled = bool(on)
        self._sync_enabled_ui()

    def _sync_enabled_ui(self) -> None:
        on = self.enable.isChecked()
        for s in self.sliders.values():
            s.setEnabled(on)
        self.retheme()

    def _show_value(self, attr: str, v: int, unit: str) -> None:
        self.value_labels[attr].setText(f"{v} {unit}")

    def _set_value(self, attr: str, v: int, unit: str) -> None:
        if self.chaos is not None:
            setattr(self.chaos, attr, int(v))
        self._show_value(attr, v, unit)
        self.retheme()

    def update_counters(self) -> None:
        c = self.chaos
        if c is None or not hasattr(c, "dropped"):
            self.counters.hide()
            return
        self.counters.show()
        self.counters.setText(f"dropped {getattr(c, 'dropped', 0)} · duplicated {getattr(c, 'duplicated', 0)}"
                              f" · delayed {getattr(c, 'delayed', 0)}")

    def reset(self) -> None:
        for s in self.sliders.values():
            s.setValue(0)

    def cut(self) -> None:
        """Abort the socket (``net.cut_link()``, falling back to ``net.chaos.cut_link()``)."""
        fn = getattr(self.net, "cut_link", None)
        if not callable(fn) and self.chaos is not None:
            fn = getattr(self.chaos, "cut_link", None)
        if callable(fn):
            fn()
