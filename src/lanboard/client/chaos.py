"""Chaos Lab (feature S2): application-layer fault injection for inbound frames.

Why this exists
---------------
The project spec asks the system to cope with "delayed, duplicated or missing
updates". On a healthy LAN those almost never happen, because **TCP already
hides them**: it numbers every byte, retransmits lost segments, discards
duplicate segments and hands data to the application strictly in order. Over a
working TCP connection the application therefore never sees loss, duplication
or reordering. The only failure it sees is the connection dying (a pulled cable
or a crashed peer).

Our protocol still has its own recovery logic on top of TCP, for the cases that
TCP cannot hide:

* every board ``OP`` carries a server-assigned ``seq``. A gap in ``seq`` (an OP
  "lost", for example across a reconnect) makes the controller send ``RESYNC``
  and catch up from the server's op log;
* an OP whose ``seq`` was already applied is a duplicate and is discarded,
  which makes delivery idempotent;
* OPs that arrive early are buffered and applied in ``seq`` order.

To demonstrate that logic live, we inject faults **above** TCP, between the
frame decoder and the controller. This is application-layer fault injection
and it does not touch the TCP stream itself::

    socket bytes -> FrameDecoder -> [FaultInjector] -> NetClient.message -> controller
                                     drop / dup / delay

Fault types
-----------
* **drop** (``drop_pct``): applies to ``OP`` frames only. Dropping an OP
  creates a seq gap, so the RESYNC path runs. Dropping control frames such as
  JOINED or PONG would only wedge the session, which demonstrates nothing.
* **duplicate** (``dup_pct``): the frame is delivered twice, so the
  controller's seq de-duplication runs.
* **latency + jitter** (``latency_ms`` + uniform random ``0..jitter_ms``): the
  frame is delivered later by a ``QTimer.singleShot``. Because every frame
  draws its own delay, a later frame can overtake an earlier one. That is
  reordering, which the controller undoes by sorting on seq. Latency also
  applies to PONG, so the measured RTT on the HUD shows the injected delay.
* **cut link** (``cut_link()``): aborts the TCP socket (like pulling the
  cable), so the reconnect and resume path runs.

``process()`` returns a short tag ("dropped", "duplicated", "delayed 120 ms"
or a combination) that the Network Inspector (S1) shows next to the frame.
"""

from __future__ import annotations

import random
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer

from ..common.messages import MsgType

Deliver = Callable[[int, dict], Any]


class FaultInjector(QObject):
    """Randomly drops, duplicates and delays inbound frames when ``enabled``.

    Percentages are on a 0..100 scale and delays are in milliseconds. ``rng``
    can be a seeded ``random.Random`` so that tests are deterministic.
    """

    def __init__(self, parent: QObject | None = None, *,
                 rng: random.Random | None = None,
                 cut_link: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.enabled: bool = False
        self.latency_ms: float = 0.0
        self.jitter_ms: float = 0.0
        self.drop_pct: float = 0.0
        self.dup_pct: float = 0.0
        self.rng = rng or random.Random()
        self._cut_link = cut_link
        # Totals since construction, read by the Chaos Lab panel.
        self.dropped = 0
        self.duplicated = 0
        self.delayed = 0

    # ------------------------------------------------------------------ API
    def process(self, t: int, payload: dict, deliver: Deliver) -> str | None:
        """Pass one inbound frame through the fault model.

        ``deliver(t, payload)`` is called zero times (dropped), once, or twice
        (duplicated), either immediately or later from a timer. Returns the
        chaos tag for the inspector, or ``None`` if the frame was untouched.
        """
        if not self.enabled:
            deliver(t, payload)
            return None

        rnd = self.rng.random
        if t == MsgType.OP and self.drop_pct > 0 and rnd() * 100.0 < self.drop_pct:
            self.dropped += 1
            return "dropped"

        copies = 1
        if self.dup_pct > 0 and rnd() * 100.0 < self.dup_pct:
            copies = 2
            self.duplicated += 1

        first_delay = 0
        for i in range(copies):
            delay = self._draw_delay()
            if i == 0:
                first_delay = delay
            if delay > 0:
                # The default argument binds this frame's values at definition
                # time, so a later loop iteration cannot overwrite them.
                QTimer.singleShot(delay, lambda t=t, p=payload: deliver(t, p))
            else:
                deliver(t, payload)
        if first_delay > 0:
            self.delayed += 1

        tags = []
        if copies == 2:
            tags.append("duplicated")
        if first_delay > 0:
            tags.append(f"delayed {first_delay} ms")
        return ", ".join(tags) or None

    def cut_link(self) -> None:
        """Simulate a pulled cable by aborting the owning NetClient's socket."""
        if self._cut_link is not None:
            self._cut_link()

    def reset_counters(self) -> None:
        self.dropped = self.duplicated = self.delayed = 0

    # ------------------------------------------------------------ internals
    def _draw_delay(self) -> int:
        """Base latency plus uniform jitter, in whole milliseconds (>= 0)."""
        delay = max(0.0, float(self.latency_ms))
        if self.jitter_ms > 0:
            delay += self.rng.uniform(0.0, float(self.jitter_ms))
        return int(round(delay))
