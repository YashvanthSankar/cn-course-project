"""NetClient / FaultInjector / Metrics / DiscoveryBrowser tests.

The real server is developed separately, so these tests run against a small
**fake server**: a plain blocking-socket TCP server on background threads
that speaks the real framing (``common.protocol``) and implements just enough
of the handshake (HELLO, CHALLENGE, JOIN with password auth, JOINED +
SNAPSHOT, PING -> PONG, LEAVE). It can also drop every connection or stop
answering PINGs. The Qt side runs on an offscreen QCoreApplication, and
``wait_until`` pumps its event loop.
"""

from __future__ import annotations

import json
import os
import random
import socket
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtNetwork import QAbstractSocket  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from lanboard.client.chaos import FaultInjector  # noqa: E402
from lanboard.client.discovery import DiscoveryBrowser  # noqa: E402
from lanboard.client.metrics import Metrics  # noqa: E402
from lanboard.client.net import NetClient, _FrameSizer  # noqa: E402
from lanboard.common import auth  # noqa: E402
from lanboard.common.config import DISCOVERY_MAGIC, PROTO_VERSION  # noqa: E402
from lanboard.common.messages import Err, MsgType  # noqa: E402
from lanboard.common.protocol import FrameDecoder, encode  # noqa: E402


# ============================================================ helpers
@pytest.fixture(scope="module")
def qapp():
    """The networking layer only needs a QCoreApplication. But Qt allows one
    application object per process and widget tests in the same pytest run
    need a QApplication, so create the richer one when QtWidgets exists."""
    app = QCoreApplication.instance()
    if app is None:
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
        except ImportError:
            app = QCoreApplication([])
    return app


def wait_until(pred, timeout: float = 10.0) -> bool:   # generous: slow CI / Windows refused-connect delays
    """Pump the Qt event loop until ``pred()`` is true or ``timeout`` passes."""
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            return False
        QTest.qWait(5)
    return True


class Recorder(list):
    """Slot that records the arguments of every emission."""

    def __call__(self, *args):
        self.append(args)


class FakeServer:
    """Minimal LANBoard server on 127.0.0.1 (one thread per connection)."""

    def __init__(self, password: str | None = None, port: int = 0) -> None:
        self.salt = auth.new_salt()
        self.verifier = auth.make_verifier(password, self.salt) if password else None
        self.missing_rooms = {"missing"}
        self.answer_pings = True
        self.hellos: list[dict] = []
        self.joins: list[dict] = []
        self.pings: list[dict] = []
        self.leaves = 0
        self.sent_sizes: dict[int, list[int]] = {}
        self._conns: list[tuple[socket.socket, threading.Lock]] = []
        self._stop = threading.Event()
        self._lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._lsock.bind(("127.0.0.1", port))
        self._lsock.listen()
        self._lsock.settimeout(0.1)
        self.port = self._lsock.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()

    # ---------------------------------------------------------- control
    def push(self, t: int, payload: dict) -> None:
        for conn, lock in list(self._conns):
            self._send(conn, lock, t, payload)

    def drop_all(self) -> None:
        """Abruptly close every client connection (server-side link loss)."""
        for conn, _ in list(self._conns):
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        self._conns.clear()

    def close(self) -> None:
        self._stop.set()
        self.drop_all()
        self._lsock.close()

    # -------------------------------------------------------- internals
    def _send(self, conn, lock, t, payload) -> None:
        data = encode(t, payload)
        self.sent_sizes.setdefault(int(t), []).append(len(data))
        try:
            with lock:
                conn.sendall(data)
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._lsock.accept()
            except (socket.timeout, OSError):
                continue
            conn.settimeout(0.1)
            entry = (conn, threading.Lock())
            self._conns.append(entry)
            threading.Thread(target=self._serve, args=entry, daemon=True).start()

    def _serve(self, conn: socket.socket, lock: threading.Lock) -> None:
        dec = FrameDecoder()
        state = {"nonce": None, "username": "?"}
        while not self._stop.is_set():
            try:
                data = conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            for t, p in dec.feed(data):
                self._handle(conn, lock, state, t, p)

    def _handle(self, conn, lock, st, t, p) -> None:
        send = lambda mt, pl: self._send(conn, lock, mt, pl)  # noqa: E731
        if t == MsgType.HELLO:
            self.hellos.append(p)
            st["username"] = p.get("username")
            st["nonce"] = auth.new_nonce()
            send(MsgType.CHALLENGE, {"nonce": st["nonce"], "server_id": "srv-1",
                                     "server_name": "Fake", "proto": PROTO_VERSION})
        elif t == MsgType.JOIN:
            self.joins.append(p)
            room = p.get("room")
            if room in self.missing_rooms:
                send(MsgType.ERROR, {"code": Err.ROOM_NOT_FOUND, "msg": f"no room {room}"})
                return
            if self.verifier and not p.get("create"):
                proof = p.get("proof")
                if proof is None or not auth.check_proof(self.verifier, st["nonce"], proof):
                    st["nonce"] = auth.new_nonce()      # fresh nonce for every attempt
                    code = Err.AUTH_REQUIRED if proof is None else Err.BAD_PASSWORD
                    send(MsgType.ERROR, {"code": code, "msg": "password", "salt": self.salt,
                                         "nonce": st["nonce"]})
                    return
            resumed = bool(p.get("resume_token"))
            send(MsgType.JOINED, {
                "user_id": "u1", "username": st["username"], "color": "#e6194b",
                "role": "owner" if p.get("create") else "editor",
                "resume_token": p.get("resume_token") or "tok-1", "resumed": resumed,
                "room": {"code": room, "name": p.get("name") or room, "locked": False,
                         "protected": bool(self.verifier), "editors_can_clear": False,
                         "owner_id": "u1", "presenter": None},
                "participants": [], "chat": [], "server_time": time.time()})
            if resumed:
                send(MsgType.OP_BATCH, {"ops": []})
            else:
                send(MsgType.SNAPSHOT, {"seq": 0, "board": {"seq": 0, "objects": []}, "hash": ""})
        elif t == MsgType.PING:
            self.pings.append(p)
            if self.answer_pings:
                send(MsgType.PONG, {"t": p.get("t"), "server_time": time.time()})
        elif t == MsgType.LEAVE:
            self.leaves += 1


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


@pytest.fixture
def pw_server():
    s = FakeServer(password="secret")
    yield s
    s.close()


@pytest.fixture
def net(qapp):
    n = NetClient()
    n.ping_interval = 60.0          # tests that need heartbeats shorten it
    n.backoff = (0.05,)
    n.states = Recorder()
    n.joins = Recorder()
    n.messages = Recorder()
    n.errors = Recorder()
    n.pw_req = Recorder()
    n.frames = Recorder()
    n.state_changed.connect(n.states)
    n.joined.connect(n.joins)
    n.message.connect(n.messages)
    n.error.connect(n.errors)
    n.password_required.connect(n.pw_req)
    n.frame_logged.connect(n.frames)
    yield n
    n.close()
    n.deleteLater()
    QTest.qWait(1)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def states(net) -> list[str]:
    return [a[0] for a in net.states]


# ======================================================== handshake
def test_handshake_reaches_joined(net, server):
    assert net.send(MsgType.CHAT_SEND, {"cmid": "x", "text": "hi"}) is False   # not joined yet
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(net.is_joined)
    assert states(net) == ["connecting", "authenticating", "joined"]
    hello = server.hellos[0]
    assert hello == {"proto": PROTO_VERSION, "app": "lanboard", "username": "alice",
                     "client_id": net.client_id}
    assert server.joins[0] == {"room": "r1", "create": False}
    assert net.user_id == "u1" and net.resume_token == "tok-1"
    assert net.joins[0][0]["resumed"] is False
    assert net.server_info["server_id"] == "srv-1"
    # TCP_NODELAY is on (getsockopt returns non-zero; macOS reports the flag bit, 4).
    assert int(net._sock.socketOption(QAbstractSocket.SocketOption.LowDelayOption)) != 0
    # SNAPSHOT (after JOINED) arrives through `message`. Handshake frames do not.
    assert wait_until(lambda: any(m[0] == MsgType.SNAPSHOT for m in net.messages))
    assert not any(m[0] in (MsgType.CHALLENGE, MsgType.JOINED) for m in net.messages)
    assert net.send(MsgType.CHAT_SEND, {"cmid": "x", "text": "hi"}) is True


def test_create_room_with_password_sends_verifier_not_password(net, server):
    net.connect_to("127.0.0.1", server.port, "alice", "new", create=True,
                   password="hunter2", room_name="My Room")
    assert wait_until(net.is_joined)
    j = server.joins[0]
    assert j["create"] is True and j["name"] == "My Room" and "proof" not in j
    assert set(j["password"]) == {"salt", "verifier"}
    assert j["password"]["verifier"] == auth.make_verifier("hunter2", j["password"]["salt"])
    assert "hunter2" not in json.dumps(j)


def test_password_auto_retry_after_auth_required(net, pw_server):
    net.connect_to("127.0.0.1", pw_server.port, "bob", "r1", password="secret")
    assert wait_until(net.is_joined)
    assert len(pw_server.joins) == 2
    assert "proof" not in pw_server.joins[0]      # salt unknown at first
    assert "proof" in pw_server.joins[1]          # retried once with HMAC proof
    assert not net.pw_req and not net.errors


def test_bad_password_prompts_then_succeeds(net, pw_server):
    net.connect_to("127.0.0.1", pw_server.port, "bob", "r1", password="wrong")
    assert wait_until(lambda: len(net.pw_req) == 1)
    assert net.pw_req[0] == ("r1", True)          # wrong previous attempt
    assert net.state == "authenticating"
    assert len(pw_server.joins) == 2              # first JOIN + one automatic retry, then stop
    net.provide_password("secret")
    assert wait_until(net.is_joined)
    assert len(pw_server.joins) == 3 and len(net.pw_req) == 1


def test_password_required_without_password(net, pw_server):
    net.connect_to("127.0.0.1", pw_server.port, "bob", "r1")
    assert wait_until(lambda: net.pw_req)
    assert net.pw_req[0] == ("r1", False)
    QTest.qWait(100)
    assert len(pw_server.joins) == 1 and not net.is_joined()   # waits for the user
    net.provide_password("wrong")
    assert wait_until(lambda: len(net.pw_req) == 2)
    assert net.pw_req[1] == ("r1", True)          # user-typed password: no silent retry
    net.provide_password("secret")
    assert wait_until(net.is_joined)


def test_fatal_error_stops(net, server):
    net.connect_to("127.0.0.1", server.port, "alice", "missing")
    assert wait_until(lambda: net.state == "closed")
    assert net.errors == [(Err.ROOM_NOT_FOUND, "no room missing")]
    QTest.qWait(300)
    assert len(server.hellos) == 1                # no reconnect
    assert states(net)[-1] == "closed"


def test_fatal_kicked_after_join(net, server):
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(net.is_joined)
    server.push(MsgType.ERROR, {"code": Err.FORBIDDEN, "msg": "viewers cannot draw"})
    assert wait_until(lambda: net.errors)
    assert net.is_joined()                        # FORBIDDEN after join is not fatal
    server.push(MsgType.ERROR, {"code": Err.KICKED, "msg": "bye"})
    assert wait_until(lambda: net.state == "closed")
    QTest.qWait(200)
    assert len(server.hellos) == 1 and net.errors[-1][0] == Err.KICKED


def test_connect_failed_does_not_retry(net, qapp):
    net.connect_to("127.0.0.1", free_port(), "alice", "r1")
    assert wait_until(lambda: net.state == "closed")
    assert net.errors and net.errors[0][0] == "CONNECT_FAILED"
    assert "reconnecting" not in states(net)


# ======================================================== reconnect
def test_reconnect_resumes_with_token_and_last_seq(net, server):
    net.last_seq_provider = lambda: 42
    net.connect_to("127.0.0.1", server.port, "alice", "r1", create=True)
    assert wait_until(net.is_joined)
    server.drop_all()
    assert wait_until(lambda: len(net.joins) == 2)
    assert states(net) == ["connecting", "authenticating", "joined", "reconnecting", "joined"]
    j = server.joins[1]
    assert j["resume_token"] == "tok-1" and j["last_seq"] == 42 and j["create"] is False
    assert server.hellos[0]["client_id"] == server.hellos[1]["client_id"] == net.client_id
    assert net.joins[1][0]["resumed"] is True
    assert wait_until(lambda: any(m[0] == MsgType.OP_BATCH for m in net.messages))


def test_cut_link_triggers_reconnect(net, server):
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(net.is_joined)
    net.chaos.cut_link()
    assert net.state == "reconnecting"
    assert wait_until(lambda: len(net.joins) == 2)
    assert "resume_token" in server.joins[1]


def test_reconnect_backoff_keeps_retrying_while_server_down(net, server):
    net.backoff = (0.02, 0.04)
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(net.is_joined)
    port = server.port
    server.close()                                # listener gone: connects are refused
    assert wait_until(lambda: net._attempt >= 4, timeout=30)   # Windows: each refused connect takes ~2 s
    assert net.state == "reconnecting" and not net.errors
    revived = FakeServer(port=port)                    # server comes back on the same port
    try:
        assert wait_until(lambda: len(net.joins) == 2, timeout=4)
    finally:
        revived.close()


def test_close_sends_leave_and_does_not_reconnect(net, server):
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(net.is_joined)
    net.close()
    assert net.state == "closed" and not net.is_joined()
    assert net.send(MsgType.CHAT_SEND, {"cmid": "1", "text": "x"}) is False
    assert wait_until(lambda: server.leaves == 1)
    QTest.qWait(200)
    assert len(server.hellos) == 1


# ======================================================== heartbeat
def test_ping_pong_rtt(net, server):
    net.ping_interval = 0.05
    rtts = Recorder()
    net.rtt_changed.connect(rtts)
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(lambda: len(rtts) >= 3)
    assert all(0.0 <= r[0] < 1000.0 for r in rtts)
    assert set(server.pings[0]) == {"t", "rtt"}
    assert server.pings[0]["rtt"] is None and server.pings[-1]["rtt"] is not None
    assert net.last_rtt == rtts[-1][0]
    assert list(net.metrics.rtt_hist)[-1] == net.last_rtt


def test_silent_link_is_declared_dead(net, server):
    """Half-open TCP: the socket looks fine, but nothing arrives any more."""
    server.answer_pings = False
    net.ping_interval = 0.05
    net.client_timeout = 0.4
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(net.is_joined)
    assert wait_until(lambda: "reconnecting" in states(net), timeout=2)
    assert wait_until(lambda: len(server.hellos) >= 2)


# ======================================================== inspector
def test_frame_log_sizes_and_fields(net, server):
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(lambda: any(f[0]["name"] == "SNAPSHOT" for f in net.frames))
    by_name = {f[0]["name"]: f[0] for f in net.frames}
    hello = by_name["HELLO"]
    assert hello["dir"] == "out" and hello["type"] == MsgType.HELLO and hello["chaos"] is None
    assert hello["size"] == len(encode(MsgType.HELLO, hello["payload"]))
    for name in ("CHALLENGE", "JOINED", "SNAPSHOT"):
        f = by_name[name]
        assert f["dir"] == "in"
        assert f["size"] == server.sent_sizes[int(MsgType[name])][0]
        assert isinstance(f["t"], float)


def test_log_ephemeral_off_hides_cursor_but_delivers(net, server):
    net.log_ephemeral = False
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(net.is_joined)
    server.push(MsgType.CURSOR, {"user_id": "u2", "x": 1, "y": 2})
    server.push(MsgType.CHAT, {"id": 1, "text": "hi"})
    assert wait_until(lambda: any(m[0] == MsgType.CHAT for m in net.messages))
    assert any(m[0] == MsgType.CURSOR for m in net.messages)
    names = [f[0]["name"] for f in net.frames]
    assert "CURSOR" not in names and "CHAT" in names


def test_frame_sizer_byte_by_byte():
    frames = [encode(1, {"a": 1}), encode(0x21, {"seq": 7, "s": "é" * 50}), encode(9, {})]
    stream = b"".join(frames)
    sizer, sizes = _FrameSizer(), []
    for i in range(len(stream)):
        sizes += sizer.feed(stream[i:i + 1])
    assert sizes == [len(f) for f in frames]
    assert _FrameSizer().feed(stream) == [len(f) for f in frames]


# ============================================================ chaos
def test_chaos_drop_in_client_only_affects_ops(net, server):
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(net.is_joined)
    net.chaos.enabled = True
    net.chaos.drop_pct = 100
    server.push(MsgType.OP, {"seq": 1, "user_id": "u2", "op_id": "a", "ts": 0, "op": {}})
    server.push(MsgType.CHAT, {"id": 1, "text": "hi"})
    assert wait_until(lambda: any(m[0] == MsgType.CHAT for m in net.messages))
    assert not any(m[0] == MsgType.OP for m in net.messages)
    op_log = [f[0] for f in net.frames if f[0]["name"] == "OP"]
    assert op_log and op_log[0]["chaos"] == "dropped"
    assert net.chaos.dropped == 1


def _sink():
    out = []
    return out, lambda t, p: out.append((t, p))


def test_fault_injector_disabled_passthrough(qapp):
    fi = FaultInjector(rng=random.Random(0))
    fi.drop_pct = fi.dup_pct = 100
    fi.latency_ms = 500
    out, deliver = _sink()
    assert fi.process(MsgType.OP, {"seq": 1}, deliver) is None
    assert out == [(MsgType.OP, {"seq": 1})]                     # synchronous, untouched


def test_fault_injector_drop_only_ops(qapp):
    fi = FaultInjector(rng=random.Random(0))
    fi.enabled, fi.drop_pct = True, 100
    out, deliver = _sink()
    assert fi.process(MsgType.OP, {"seq": 1}, deliver) == "dropped"
    assert fi.process(MsgType.CHAT, {"id": 1}, deliver) is None
    assert fi.process(MsgType.OP_BATCH, {"ops": []}, deliver) is None
    assert [t for t, _ in out] == [MsgType.CHAT, MsgType.OP_BATCH]
    assert fi.dropped == 1


def test_fault_injector_duplicate(qapp):
    fi = FaultInjector(rng=random.Random(0))
    fi.enabled, fi.dup_pct = True, 100
    out, deliver = _sink()
    assert fi.process(MsgType.OP, {"seq": 5}, deliver) == "duplicated"
    assert out == [(MsgType.OP, {"seq": 5})] * 2 and fi.duplicated == 1


def test_fault_injector_delay(qapp):
    fi = FaultInjector(rng=random.Random(0))
    fi.enabled, fi.latency_ms = True, 60
    out, deliver = _sink()
    assert fi.process(MsgType.OP, {"seq": 1}, deliver) == "delayed 60 ms"
    assert out == []                                            # not yet
    QTest.qWait(20)
    assert out == []
    assert wait_until(lambda: len(out) == 1, timeout=1)
    assert fi.delayed == 1


def test_fault_injector_jitter_reorders(qapp):
    fi = FaultInjector(rng=random.Random(1234))
    fi.enabled, fi.jitter_ms = True, 80
    out, deliver = _sink()
    for seq in range(20):
        fi.process(MsgType.OP, {"seq": seq}, deliver)
    assert wait_until(lambda: len(out) == 20, timeout=1)
    seqs = [p["seq"] for _, p in out]
    assert sorted(seqs) == list(range(20)) and seqs != list(range(20))


def test_fault_injector_dup_and_delay_tag_and_cut(qapp):
    cuts = []
    fi = FaultInjector(rng=random.Random(0), cut_link=lambda: cuts.append(1))
    fi.enabled, fi.dup_pct, fi.latency_ms = True, 100, 10
    out, deliver = _sink()
    assert fi.process(MsgType.CHAT, {}, deliver) == "duplicated, delayed 10 ms"
    assert wait_until(lambda: len(out) == 2, timeout=1)
    fi.cut_link()
    assert cuts == [1]


# ========================================================== metrics
def test_metrics_window_and_totals(qapp):
    m = Metrics(autostart=False)
    got = Recorder()
    m.stats.connect(got)
    for _ in range(3):
        m.count_in("OP", 100)
    m.count_out("PING", 20)
    m.add_rtt(1.5)
    s = m.tick()
    assert got[0][0] == s
    assert (s["msgs_in"], s["bytes_in"], s["msgs_out"], s["bytes_out"]) == (3, 300, 1, 20)
    assert s["by_type"] == {"OP": 3, "PING": 1}
    assert s["rtt_ms"] == 1.5 and s["rtt_hist"] == [1.5]
    assert (s["total_in"], s["total_out"]) == (3, 1)
    s2 = m.tick()                                               # next second: rates reset
    assert (s2["msgs_in"], s2["bytes_in"], s2["by_type"]) == (0, 0, {})
    assert (s2["total_in"], s2["total_out"], s2["rtt_ms"]) == (3, 1, 1.5)
    for i in range(100):
        m.add_rtt(float(i))
    assert len(m.tick()["rtt_hist"]) == 60


def test_netclient_counts_traffic_and_forwards_stats(net, server):
    net.metrics.stop()                  # tick manually so the window is deterministic
    got = Recorder()
    net.stats.connect(got)
    net.connect_to("127.0.0.1", server.port, "alice", "r1")
    assert wait_until(lambda: any(m[0] == MsgType.SNAPSHOT for m in net.messages))
    s = net.metrics.tick()
    assert got and got[-1][0] == s
    assert s["msgs_in"] >= 3 and s["total_in"] >= 3            # CHALLENGE, JOINED, SNAPSHOT
    assert s["msgs_out"] >= 2                                   # HELLO, JOIN (+PING)
    assert s["by_type"]["HELLO"] == 1 and s["by_type"]["JOINED"] == 1
    expected_in = sum(sum(v) for v in server.sent_sizes.values())
    assert wait_until(lambda: net.metrics.total_bytes_in == expected_in)


# ======================================================== discovery
class FakeResponder:
    """UDP discovery responder on 127.0.0.1 that answers as two servers."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.active = True
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            if not self.active or json.loads(data).get("magic") != DISCOVERY_MAGIC:
                continue
            for sid, name in (("s-z", "Zeta lab"), ("s-a", "Alpha lab"), ("s-a", "Alpha lab")):
                reply = {"magic": DISCOVERY_MAGIC, "server_id": sid, "name": name, "port": 5050,
                         "proto": PROTO_VERSION,
                         "rooms": [{"code": "r1", "name": "R", "users": 1, "protected": False,
                                    "locked": False}]}
                self.sock.sendto(json.dumps(reply).encode(), addr)
            self.sock.sendto(b"not json", addr)                    # garbage is ignored

    def close(self) -> None:
        self._stop.set()
        self.sock.close()


def test_discovery_dedupes_sorts_and_expires(qapp):
    resp = FakeResponder()
    browser = DiscoveryBrowser(port=resp.port, interval_ms=100, ttl=0.4)
    seen = Recorder()
    browser.servers_changed.connect(seen)
    try:
        browser.start()
        assert wait_until(lambda: seen and len(seen[-1][0]) == 2)
        servers = seen[-1][0]
        assert [s["name"] for s in servers] == ["Alpha lab", "Zeta lab"]
        assert all(s["host"] == "127.0.0.1" and s["port"] == 5050 for s in servers)
        assert set(servers[0]) == {"server_id", "name", "host", "port", "proto", "rooms", "last_seen"}
        n_emits = len(seen)
        QTest.qWait(300)                       # refreshes only update last_seen: no new emits
        assert len(seen) == n_emits
        resp.active = False
        assert wait_until(lambda: seen[-1][0] == [], timeout=2)
    finally:
        browser.stop()
        resp.close()
