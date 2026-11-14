"""The server's live terminal dashboard (``lanboard-server --dashboard``) against a real server."""

from __future__ import annotations

import asyncio
import io
import logging
import re

from lanboard.server import Server
from lanboard.server.__main__ import parse_args, setup_logging
from lanboard.server.dashboard import Dashboard, RingLogHandler, human_bytes
from lanboard.tools.bot import BotClient

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text: str) -> str:
    return ANSI.sub("", text)


async def test_dashboard_renders_rooms_people_and_logs(tmp_path):
    srv = Server("127.0.0.1", 0, str(tmp_path / "data"), "dash-test", discovery=False)
    await srv.start()
    a = b = None
    try:
        a = BotClient("127.0.0.1", srv.port, "alice", "LAB", create=True, room_name="CN Lab")
        await a.connect()
        b = BotClient("127.0.0.1", srv.port, "bob", "LAB")
        await b.connect()
        for i in range(3):
            a.submit({"k": "add", "obj": {"id": a.new_id(), "type": "stroke",
                                                "style": {"color": "#000000", "width": 2},
                                                "geom": {"pts": [i, 0, i + 10, 10]}}})
        await a.settle()
        room = srv.rooms["LAB"]
        room.locked = True
        ring = RingLogHandler(size=3)
        ring.setFormatter(logging.Formatter("%(message)s"))
        for n in range(5):
            ring.emit(logging.LogRecord("x", logging.INFO, __file__, 1, f"log line {n}", None, None))
        dash = Dashboard(srv, ring, out=io.StringIO())
        text = plain(dash.render())
        assert "LANBoard server dash-test" in text and f"port {srv.port}" in text
        assert "connections 2" in text and "rooms 1" in text
        assert "room LAB 'CN Lab'  seq 3" in text and f"hash {room.board.hash()}" in text
        assert "objects 3" in text and "locked" in text
        lines = {ln.split()[0]: ln for ln in text.splitlines() if ln.startswith("  ") and ln.split()}
        assert "owner" in lines["alice"] and "online" in lines["alice"]
        assert "editor" in lines["bob"]
        # the ring buffer keeps only the newest lines
        assert "log line 4" in text and "log line 1" not in text
        # bob's cable is cut (no LEAVE): his session stays, shown offline
        bob_s = next(s for s in room.sessions.values() if s.username == "bob")
        b.drop()
        for _ in range(200):
            if bob_s.conn is None:
                break
            await asyncio.sleep(0.02)
        text = plain(dash.render())
        bob = next(ln for ln in text.splitlines() if ln.strip().startswith("bob"))
        assert "offline" in bob
        # the op-rate column is computed from successive renders
        a.submit({"k": "add", "obj": {"id": a.new_id(), "type": "stroke",
                                            "style": {"color": "#000000", "width": 2},
                                            "geom": {"pts": [0, 0, 5, 5]}}})
        await a.settle()
        await asyncio.sleep(0.05)
        assert "ops/s" in plain(dash.render())
        assert dash._rates["LAB"] > 0
        # run() keeps redrawing into its output stream until cancelled
        out = io.StringIO()
        task = asyncio.create_task(Dashboard(srv, None, out=out).run(interval=0.01))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert out.getvalue().count("\x1b[H\x1b[2J") >= 2
    finally:
        for bot in (a, b):
            if bot is not None:
                await bot.close()
        await srv.stop()


def test_dashboard_cli_flag_routes_logs_into_ring():
    args = parse_args(["--dashboard", "--port", "6000", "--no-discovery"])
    assert args.dashboard and args.port == 6000 and args.no_discovery
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        ring = setup_logging(False, True)
        assert isinstance(ring, RingLogHandler) and ring in root.handlers
        logging.getLogger("lanboard.server").info("hello dashboard")
        assert any("hello dashboard" in ln for ln in ring.lines)
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)


def test_human_bytes():
    assert human_bytes(512) == "512B"
    assert human_bytes(2048) == "2.0K"
    assert human_bytes(5 * 1024 * 1024) == "5.0M"
    assert human_bytes(3 * 1024 ** 4) == "3072.0G"
