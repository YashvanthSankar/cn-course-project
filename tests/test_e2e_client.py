"""End-to-end: the real asyncio server + real NetClient + SessionController.

The server runs on its own asyncio loop in a background thread (exactly like a
separate machine on the LAN). The clients run on the Qt event loop.
"""
import asyncio
import threading
import time

import pytest
from PySide6.QtTest import QTest

from lanboard.client.controller import SessionController
from lanboard.client.net import NetClient
from lanboard.server import Server


class ServerThread:
    def __init__(self, data_dir):
        self.loop = asyncio.new_event_loop()
        self.server = Server("127.0.0.1", 0, str(data_dir), "e2e", discovery=False)
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.server.start())
        self.ready.set()
        self.loop.run_forever()

    @property
    def port(self):
        return self.server.port

    def room_board(self, code):
        return asyncio.run_coroutine_threadsafe(self._board(code), self.loop).result(5)

    async def _board(self, code):
        return self.server.rooms[code].board.copy()

    def stop(self):
        asyncio.run_coroutine_threadsafe(self.server.stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        QTest.qWait(20)
    return cond()


@pytest.fixture
def srv(tmp_path):
    s = ServerThread(tmp_path / "data")
    yield s
    s.stop()


def client(srv, name, room="E2E", create=False, password=None):
    net = NetClient()
    ctl = SessionController(net)
    net.connect_to("127.0.0.1", srv.port, name, room, create=create, password=password, room_name="Demo")
    assert wait_until(net.is_joined), f"{name} failed to join"
    return net, ctl


def stroke(x):
    return {"type": "stroke", "style": {"color": "#123456", "width": 3}, "geom": {"pts": [x, 0, x + 10, 10]}}


def test_two_clients_sync_and_converge(srv):
    na, a = client(srv, "alice", create=True)
    nb, b = client(srv, "bob")
    assert a.is_owner() and not b.is_owner()
    for i in range(10):
        a.add_object(stroke(i))
        b.add_object(stroke(100 + i))
    assert wait_until(lambda: len(a.board) == 20 and len(b.board) == 20 and not a.pending and not b.pending)
    server_hash = srv.room_board("E2E").hash()
    assert a.board.hash() == b.board.hash() == server_hash
    # chat round trip with server timestamp
    b.send_chat("hello from bob")
    assert wait_until(lambda: any(m.get("text") == "hello from bob" for m in a.chat))
    na.close(); nb.close()


def test_late_joiner_and_undo_isolation(srv):
    na, a = client(srv, "alice", create=True)
    a.add_object(stroke(1))
    assert wait_until(lambda: len(a.board) == 1 and not a.pending)
    nb, b = client(srv, "bob")
    assert wait_until(lambda: len(b.board) == 1)          # late joiner got the snapshot
    b.add_object(stroke(2))
    assert wait_until(lambda: len(a.board) == 2)
    a.undo()                                              # removes only alice's stroke
    assert wait_until(lambda: len(b.board) == 1)
    assert all(o["owner"] == b.user_id for o in b.board.live())
    na.close(); nb.close()


def test_offline_edits_survive_cut_cable(srv):
    na, a = client(srv, "alice", create=True)
    nb, b = client(srv, "bob")
    na.cut_link()                                         # simulate pulling the cable
    a.add_object(stroke(5))                               # drawn while offline
    b.add_object(stroke(6))
    assert wait_until(lambda: na.is_joined(), timeout=10)
    assert wait_until(lambda: len(a.board) == 2 and len(b.board) == 2 and not a.pending, timeout=10)
    assert a.board.hash() == b.board.hash()
    na.close(); nb.close()


def test_chaos_drop_and_duplicate_still_converge(srv):
    na, a = client(srv, "alice", create=True)
    nb, b = client(srv, "bob")
    nb.chaos.enabled = True
    nb.chaos.drop_pct = 30
    nb.chaos.dup_pct = 30
    nb.chaos.latency_ms = 20
    nb.chaos.jitter_ms = 40
    for i in range(40):
        a.add_object(stroke(i))
        QTest.qWait(5)
    nb.chaos.enabled = False
    a.add_object(stroke(999))                             # a clean op exposes any trailing gap
    assert wait_until(lambda: len(b.board) == 41, timeout=10)
    assert b.board.hash() == srv.room_board("E2E").hash()
    na.close(); nb.close()


def test_password_and_viewer_permissions(srv):
    na, a = client(srv, "alice", room="LOCKED", create=True, password="s3cret")
    nb, b = client(srv, "bob", room="LOCKED", password="s3cret")
    assert wait_until(lambda: b.user_id in a.participants)
    a.set_role(b.user_id, "viewer")
    assert wait_until(lambda: not b.can_edit())
    b.add_object(stroke(1))                               # blocked locally
    a.set_room(locked=True)
    assert wait_until(lambda: bool(b.room and b.room.get("locked")))
    assert len(srv.room_board("LOCKED")) == 0
    na.close(); nb.close()
