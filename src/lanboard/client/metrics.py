"""Live traffic statistics for the Network HUD (feature S4).

NetClient calls ``count_in`` / ``count_out`` once per frame with the frame's
on-wire size (4-byte length prefix + type byte + JSON payload). It calls
``add_rtt`` once per PONG. A 1 Hz ``QTimer`` turns the per-second counters into
a ``stats`` dict and resets them, which gives true per-second *rates* (not
moving averages) that are cheap to compute: a few integer additions per
frame.

``stats`` payload::

    {
      "msgs_in": int,  "msgs_out": int,      # frames in the last second
      "bytes_in": int, "bytes_out": int,     # bytes in the last second (= B/s)
      "rtt_ms": float,                       # latest round-trip time, 0.0 until the first PONG
      "rtt_hist": [float, ...],              # last 60 RTT samples, oldest first
      "total_in": int, "total_out": int,     # frames since construction
      "total_bytes_in": int, "total_bytes_out": int,
      "by_type": {name: count},              # frames per type (in + out) in the last second
    }

The bytes counted are application-layer bytes. TCP/IP headers (about 40-52
bytes per segment) are not counted, and with TCP_NODELAY small frames
usually go out as one segment each.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal

RTT_HISTORY = 60


class Metrics(QObject):
    stats = Signal(object)

    def __init__(self, parent: QObject | None = None, interval_ms: int = 1000,
                 autostart: bool = True) -> None:
        super().__init__(parent)
        self.rtt_ms: float = 0.0
        self.rtt_hist: deque[float] = deque(maxlen=RTT_HISTORY)
        self.total_in = 0
        self.total_out = 0
        self.total_bytes_in = 0
        self.total_bytes_out = 0
        self._reset_window()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.tick)
        if autostart:
            self._timer.start()

    def count_in(self, name: str, size: int) -> None:
        self._msgs_in += 1
        self._bytes_in += size
        self.total_in += 1
        self.total_bytes_in += size
        self._by_type[name] = self._by_type.get(name, 0) + 1

    def count_out(self, name: str, size: int) -> None:
        self._msgs_out += 1
        self._bytes_out += size
        self.total_out += 1
        self.total_bytes_out += size
        self._by_type[name] = self._by_type.get(name, 0) + 1

    def add_rtt(self, ms: float) -> None:
        self.rtt_ms = float(ms)
        self.rtt_hist.append(self.rtt_ms)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def snapshot(self) -> dict:
        """Current window as a stats dict (does not reset the window)."""
        return {
            "msgs_in": self._msgs_in,
            "msgs_out": self._msgs_out,
            "bytes_in": self._bytes_in,
            "bytes_out": self._bytes_out,
            "rtt_ms": self.rtt_ms,
            "rtt_hist": list(self.rtt_hist),
            "total_in": self.total_in,
            "total_out": self.total_out,
            "total_bytes_in": self.total_bytes_in,
            "total_bytes_out": self.total_bytes_out,
            "by_type": dict(self._by_type),
        }

    def tick(self) -> dict:
        """Close the current one-second window: emit ``stats`` and reset."""
        s = self.snapshot()
        self._reset_window()
        self.stats.emit(s)
        return s

    def _reset_window(self) -> None:
        self._msgs_in = self._msgs_out = 0
        self._bytes_in = self._bytes_out = 0
        self._by_type: dict[str, int] = {}
