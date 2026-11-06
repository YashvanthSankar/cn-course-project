"""SessionController sync rules, tested against a fake NetClient (no sockets)."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest

from lanboard.client.controller import GAP_TIMEOUT_MS, SessionController
from lanboard.common.messages import MsgType
from lanboard.common.model import Board



class FakeNet(QObject):
    state_changed = Signal(str)
    joined = Signal(dict)
    message = Signal(int, dict)
    error = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.sent = []
        self.client_id = "abcd1234"
        self.user_id = "u1"
        self.batching_enabled = True
        self._joined = False
        self.last_seq_provider = lambda: 0

    def is_joined(self):
        return self._joined

    def send(self, t, payload):
        if not self._joined:
            return False
        self.sent.append((MsgType(t), payload))
        return True

    def join(self, role="editor", room=None):
        self._joined = True
        self.state_changed.emit("joined")
        self.joined.emit({"user_id": "u1", "username": "me", "color": "#f00", "role": role,
                          "room": room or {"code": "R", "locked": False}, "resumed": False,
                          "participants": [], "chat": []})

    def drop(self):
        self._joined = False
        self.state_changed.emit("reconnecting")

    def of(self, t):
        return [p for (tt, p) in self.sent if tt == t]


class Server:
    """Minimal ordering server: assigns seq and echoes ops."""

    def __init__(self):
        self.board = Board()
        self.log = []

    def accept(self, op, user="u1", op_id=None):
        op = self.board.normalise(op, user)
        seq = len(self.log) + 1
        self.board.apply(op, seq)
        m = {"seq": seq, "user_id": user, "op_id": op_id, "ts": 0, "op": op}
        self.log.append(m)
        return m


def stroke(oid):
    return {"id": oid, "type": "stroke", "geom": {"pts": [0, 0, 5, 5]}, "style": {}}


@pytest.fixture
def env():
    net = FakeNet()
    ctl = SessionController(net)
    net.join()
    statuses = []
    ctl.sync_status.connect(statuses.append)
    return net, ctl, Server(), statuses


def ack_all(net, ctl, srv):
    for p in net.of(MsgType.OP_SUBMIT):
        net.message.emit(int(MsgType.OP), srv.accept(p["op"], op_id=p["op_id"]))
    net.sent.clear()


def test_optimistic_then_ack(env):
    net, ctl, srv, statuses = env
    ctl.add_object(stroke("a"))
    assert ctl.view.is_live("a") and not ctl.board.is_live("a")
    assert statuses[-1]["state"] == "pending"
    ack_all(net, ctl, srv)
    assert ctl.board.is_live("a") and not ctl.pending
    assert statuses[-1]["state"] == "synced"


def test_foreign_op_while_pending_keeps_both(env):
    net, ctl, srv, _ = env
    ctl.add_object(stroke("mine"))
    net.message.emit(int(MsgType.OP), srv.accept({"k": "add", "obj": stroke("theirs")}, user="u2", op_id="x:1"))
    assert ctl.view.is_live("mine") and ctl.view.is_live("theirs")
    ack_all(net, ctl, srv)
    assert ctl.board.hash() == srv.board.hash()


def test_duplicates_dropped_and_reorder(env):
    net, ctl, srv, _ = env
    msgs = [srv.accept({"k": "add", "obj": stroke(f"s{i}")}, user="u2", op_id=f"x:{i}") for i in range(5)]
    for m in [msgs[0], msgs[2], msgs[1], msgs[1], msgs[4], msgs[3], msgs[0]]:
        net.message.emit(int(MsgType.OP), m)
    assert ctl.board.seq == 5 and len(ctl.board) == 5
    assert not net.of(MsgType.RESYNC)


def test_gap_triggers_resync(env):
    net, ctl, srv, statuses = env
    msgs = [srv.accept({"k": "add", "obj": stroke(f"s{i}")}, user="u2") for i in range(3)]
    net.message.emit(int(MsgType.OP), msgs[0])
    net.message.emit(int(MsgType.OP), msgs[2])           # seq 2 "lost"
    assert statuses[-1]["state"] == "healing"
    QTest.qWait(GAP_TIMEOUT_MS + 150)
    assert net.of(MsgType.RESYNC) == [{"last_seq": 1}]
    net.message.emit(int(MsgType.OP_BATCH), {"ops": msgs[1:]})
    assert ctl.board.seq == 3 and statuses[-1]["state"] == "synced"


def test_reject_rolls_back(env):
    net, ctl, _, _ = env
    ctl.add_object(stroke("a"))
    op_id = net.of(MsgType.OP_SUBMIT)[0]["op_id"]
    net.message.emit(int(MsgType.OP_REJECT), {"op_id": op_id, "code": "LOCKED", "msg": ""})
    assert not ctl.view.is_live("a") and not ctl.pending


def test_offline_edits_replayed_on_rejoin(env):
    net, ctl, srv, _ = env
    net.drop()
    ctl.add_object(stroke("off1"))
    ctl.add_object(stroke("off2"))
    assert not net.sent
    net.join()
    assert [p["op"]["obj"]["id"] for p in net.of(MsgType.OP_SUBMIT)] == ["off1", "off2"]
    ack_all(net, ctl, srv)
    assert len(ctl.board) == 2


def test_ack_after_snapshot_healed_resume_settles_pending(env):
    """Op accepted, echo lost with the link, resume healed by SNAPSHOT, then the dedupe re-echo."""
    net, ctl, srv, statuses = env
    ctl.add_object(stroke("a"))
    sub = net.of(MsgType.OP_SUBMIT)[0]
    echo = srv.accept(sub["op"], op_id=sub["op_id"])     # server applied it as seq 1 ...
    net.drop()                                            # ... but the echo never arrived
    net.sent.clear()
    net.join()                                            # pending op is resent on rejoin
    assert [p["op_id"] for p in net.of(MsgType.OP_SUBMIT)] == [sub["op_id"]]
    net.message.emit(int(MsgType.SNAPSHOT), {"seq": 1, "board": srv.board.snapshot(), "hash": srv.board.hash()})
    net.message.emit(int(MsgType.OP), echo)               # server de-duplicates and re-echoes seq 1
    assert not ctl.pending and ctl.view.is_live("a")
    assert statuses[-1]["state"] == "synced"


def test_hash_mismatch_heals(env):
    net, ctl, srv, _ = env
    net.message.emit(int(MsgType.OP), srv.accept({"k": "add", "obj": stroke("a")}, user="u2"))
    net.message.emit(int(MsgType.HASH), {"seq": 1, "hash": srv.board.hash()})
    assert not net.of(MsgType.RESYNC)
    net.message.emit(int(MsgType.HASH), {"seq": 1, "hash": "deadbeefdeadbeef"})
    assert net.of(MsgType.RESYNC) == [{"last_seq": -1}]
    net.message.emit(int(MsgType.SNAPSHOT), {"seq": 1, "board": srv.board.snapshot(), "hash": srv.board.hash()})
    assert ctl.board.hash() == srv.board.hash()


def test_undo_redo_roundtrip(env):
    net, ctl, srv, _ = env
    ctl.add_object(stroke("a"))
    ack_all(net, ctl, srv)
    ctl.undo()
    ack_all(net, ctl, srv)
    assert not ctl.board.is_live("a")
    ctl.redo()
    ack_all(net, ctl, srv)
    assert ctl.board.is_live("a")


def test_clear_sent_as_clear_but_undoable(env):
    net, ctl, srv, _ = env
    net.join(role="owner")
    ctl.add_object(stroke("a"))
    ack_all(net, ctl, srv)
    ctl.clear_board()
    assert net.of(MsgType.OP_SUBMIT)[0]["op"] == {"k": "clear"}       # server checks clear permission
    ack_all(net, ctl, srv)
    assert len(ctl.board) == 0
    ctl.undo()
    ack_all(net, ctl, srv)
    assert ctl.board.is_live("a")


def test_viewer_cannot_submit(env):
    net, ctl, _, _ = env
    net.join(role="viewer")
    net.sent.clear()
    ctl.add_object(stroke("a"))
    assert not net.of(MsgType.OP_SUBMIT) and not ctl.view.is_live("a")


def test_live_strokes_batched(env):
    net, ctl, _, _ = env
    for i in range(20):
        ctl.send_live("s1", "#000", 3, [i, i])
    assert not net.of(MsgType.STROKE_LIVE)
    deadline = time.monotonic() + 3                       # one 33 ms flush; CI runners can be slow
    while not net.of(MsgType.STROKE_LIVE) and time.monotonic() < deadline:
        QTest.qWait(10)
    live = net.of(MsgType.STROKE_LIVE)
    assert len(live) == 1 and len(live[0]["pts"]) == 40
    net.batching_enabled = False
    ctl.send_live("s1", "#000", 3, [1, 1])
    assert len(net.of(MsgType.STROKE_LIVE)) == 2


def test_cursor_throttled(env):
    net, ctl, _, _ = env
    for i in range(50):
        ctl.send_cursor(i, i)
    QTest.qWait(120)
    cursors = net.of(MsgType.CURSOR)
    assert 1 <= len(cursors) <= 4 and cursors[-1] == {"x": 49, "y": 49}


def test_update_many_single_batch(env):
    net, ctl, srv, _ = env
    for oid in "abc":
        ctl.add_object(stroke(oid))
    ack_all(net, ctl, srv)
    ctl.update_many({oid: {"off": [10, 0]} for oid in "abc"})
    subs = net.of(MsgType.OP_SUBMIT)
    assert len(subs) == 1 and subs[0]["op"]["k"] == "batch"
    ack_all(net, ctl, srv)
    ctl.undo()
    ack_all(net, ctl, srv)
    assert all(ctl.board.get(o)["off"] == [0, 0] for o in "abc")


def _parts(msg_type, payload, monkeypatch, budget=300):
    """Encode ``payload`` as multi-part frames (small budget) and decode them again."""
    from lanboard.common import config as C
    from lanboard.common.protocol import FrameDecoder, encode_parts
    monkeypatch.setattr(C, "PART_BYTES", budget)
    dec = FrameDecoder()
    out = []
    for f in encode_parts(msg_type, payload):
        out += dec.feed(f)
    return [p for _, p in out]


def test_multipart_snapshot_applied_atomically(env, monkeypatch):
    net, ctl, srv, _ = env
    for i in range(30):
        srv.accept({"k": "add", "obj": stroke(f"s{i}")}, user="u2")
    parts = _parts(MsgType.SNAPSHOT, {"seq": srv.board.seq, "board": srv.board.snapshot(),
                                      "hash": srv.board.hash()}, monkeypatch)
    assert len(parts) > 3 and all(p["parts"] == len(parts) for p in parts)
    resets = []
    ctl.reset.connect(lambda: resets.append(len(ctl.board)))
    # out of order, with a duplicate: nothing is applied until the last part arrives
    order = parts[1:] + parts[:1]
    for p in [order[0]] + order[:-1]:
        net.message.emit(int(MsgType.SNAPSHOT), p)
        assert ctl.board.seq == 0 and not resets
    net.message.emit(int(MsgType.SNAPSHOT), order[-1])
    assert resets == [30] and ctl.board.hash() == srv.board.hash() and ctl.board.seq == 30


def test_multipart_history_emitted_once_merged(env, monkeypatch):
    net, ctl, srv, _ = env
    base = Board()
    base.apply({"k": "add", "obj": stroke("b0")}, 1)
    ops = []
    for i in range(20):
        m = srv.accept({"k": "add", "obj": stroke(f"h{i}")}, user="u2")
        ops.append(m)
    payload = {"base_seq": 1, "base": base.snapshot(), "ops": ops}
    parts = _parts(MsgType.HISTORY, payload, monkeypatch)
    got = []
    ctl.history_ready.connect(got.append)
    for p in reversed(parts):
        net.message.emit(int(MsgType.HISTORY), p)
    assert len(got) == 1
    assert got[0]["base_seq"] == 1 and [o["id"] for o in got[0]["base"]["objects"]] == ["b0"]
    assert [m["seq"] for m in got[0]["ops"]] == [m["seq"] for m in ops]
    assert "part" not in got[0] and "xid" not in got[0]


def test_unsendable_op_is_rolled_back(env):
    from lanboard.common.messages import Err
    from lanboard.common.protocol import ProtocolError
    net, ctl, _, _ = env
    notices = []
    ctl.notice.connect(lambda level, text: notices.append(text))

    def too_big(t, payload):
        raise ProtocolError("frame too large")
    net.send = too_big
    ctl.add_object(stroke("huge"))
    assert not ctl.pending and not ctl.view.is_live("huge")
    assert notices and "too large" in notices[-1]
    ctl._on_reject({"op_id": "x:1", "code": Err.TOO_LARGE, "msg": "edit too large"})
    assert notices[-1] == "That edit is too large to share."
