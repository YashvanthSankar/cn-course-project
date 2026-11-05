"""Live terminal dashboard for the server (signature feature S5).

Redraws an ANSI table once per second: every room with its seq, board hash
and op rate, and every participant with role, online state, client-reported
RTT, bytes in/out and the depth of their outbound queue. A growing queue
depth is the early warning for a client that is about to be dropped as
``lagging``. Log records are captured into a ring buffer and shown at the
bottom so they do not scroll the table away.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import shutil
import sys
import time
from typing import TYPE_CHECKING, TextIO

from lanboard.common import config as C

from .discovery import lan_ipv4_addresses

if TYPE_CHECKING:
    from .server import Server

CLEAR = "\x1b[H\x1b[2J"        # cursor home + clear screen
BOLD, DIM, RESET = "\x1b[1m", "\x1b[2m", "\x1b[0m"
GREEN, YELLOW, RED, CYAN = "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m"


def human_bytes(n: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}G"


class RingLogHandler(logging.Handler):
    """Keeps the last N formatted log lines for display inside the dashboard."""

    def __init__(self, size: int = 8) -> None:
        super().__init__()
        self.lines: collections.deque[str] = collections.deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:                               # pragma: no cover
            self.handleError(record)


class Dashboard:
    def __init__(self, server: "Server", log_handler: RingLogHandler | None = None,
                 out: TextIO = sys.stdout) -> None:
        self.server = server
        self.log_handler = log_handler
        self.out = out
        self._last_seq: dict[str, tuple[float, int]] = {}
        self._rates: dict[str, float] = {}
        self.addresses = lan_ipv4_addresses()

    def _ops_rate(self, code: str, seq: int, now: float) -> float:
        prev = self._last_seq.get(code)
        self._last_seq[code] = (now, seq)
        if prev and now > prev[0]:
            inst = (seq - prev[1]) / (now - prev[0])
            # exponential moving average smooths the 1 Hz samples
            self._rates[code] = 0.5 * self._rates.get(code, inst) + 0.5 * inst
        return self._rates.get(code, 0.0)

    def render(self) -> str:
        srv = self.server
        now = time.monotonic()
        width = shutil.get_terminal_size((100, 30)).columns
        up = int(time.time() - srv.started)
        lines = [
            f"{BOLD}LANBoard server{RESET} {CYAN}{srv.name}{RESET}  id {srv.server_id}  "
            f"up {up // 3600:d}:{up // 60 % 60:02d}:{up % 60:02d}  "
            f"{'TLS ' if srv.ssl_context else ''}port {srv.port}",
            f"join at: {', '.join(f'{ip}:{srv.port}' for ip in self.addresses) or 'localhost'}"
            f"   connections {len(srv.connections)}   rooms {len(srv.rooms)}",
            "",
        ]
        header = f"  {'user':<20} {'id':<6} {'role':<7} {'state':<8} {'rtt':>7} " \
                 f"{'in':>8} {'out':>8} {'queue':>6}"
        for room in sorted(srv.rooms.values(), key=lambda r: r.code):
            rate = self._ops_rate(room.code, room.seq, now)
            flags = " ".join(f for f, on in (("locked", room.locked),
                                             ("password", room.protected)) if on)
            lines.append(f"{BOLD}room {room.code}{RESET} {room.name!r}  seq {room.seq}  "
                         f"hash {room.board.hash()}  {rate:5.1f} ops/s  "
                         f"objects {len(room.board)}  {DIM}{flags}{RESET}")
            if not room.sessions:
                continue
            lines.append(DIM + header + RESET)
            for s in sorted(room.sessions.values(), key=lambda s: s.order):
                conn = s.conn
                state = f"{GREEN}online {RESET}" if conn else f"{DIM}offline{RESET}"
                rtt = f"{s.rtt:.1f}ms" if s.rtt is not None else "-"
                bin_ = human_bytes(conn.bytes_in) if conn else "-"
                bout = human_bytes(conn.bytes_out) if conn else "-"
                depth = conn.queue_depth if conn else 0
                qcol = RED if depth > C.SEND_QUEUE_MAX // 2 else YELLOW if depth > 50 else ""
                hand = " (hand)" if s.hand else ""
                lines.append(f"  {(s.username + hand)[:20]:<20} {s.user_id:<6} {s.role:<7} "
                             f"{state} {rtt:>7} {bin_:>8} {bout:>8} "
                             f"{qcol}{depth:>6}{RESET if qcol else ''}")
            lines.append("")
        lobby = sum(1 for c in srv.connections if c.session is None)
        if lobby:
            lines.append(f"{DIM}{lobby} connection(s) not in a room yet{RESET}")
        if self.log_handler is not None and self.log_handler.lines:
            lines.append(f"{DIM}{'-' * min(width, 80)}{RESET}")
            lines.extend(line[:width] for line in self.log_handler.lines)
        return CLEAR + "\n".join(lines) + "\n"

    async def run(self, interval: float = 1.0) -> None:
        while True:
            try:
                self.out.write(self.render())
                self.out.flush()
            except Exception:                           # never let the UI kill the server
                logging.getLogger("lanboard.dashboard").exception("dashboard render failed")
            await asyncio.sleep(interval)
