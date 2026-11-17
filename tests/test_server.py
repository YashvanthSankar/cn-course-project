"""Room logic and permissions, without sockets (fake connections capture frames)."""

from __future__ import annotations

import json

import pytest

from lanboard.common import config as C
from lanboard.common.messages import Err, MsgType, Reason
from lanboard.common.model import Board
from lanboard.common.protocol import FrameDecoder
from lanboard.server.persistence import RoomStore, Storage, safe_name
from lanboard.server.room import Room, TokenBucket


class FakeConn:
    """Stands in for ClientConnection: decodes and records everything sent to it."""

    def __init__(self) -> None:
        self.frames: list[tuple[int, dict]] = []
        self.session = None
        self.room = None
        self.uploads: dict = {}
        self.rtt = None
        self.closed: str | None = None
        self._dec = FrameDecoder()

    def send(self, t, payload):
        self.frames.append((int(t), json.loads(json.dumps(payload))))

    def send_frame(self, frame: bytes):
        self.frames.extend(self._dec.feed(frame))

    def close(self, reason, flush=False):
        self.closed = reason

    def of(self, t) -> list[dict]:
        return [p for (tt, p) in self.frames if tt == t]


@pytest.fixture
def room(tmp_path):
    store = RoomStore(tmp_path / "r")
    store.ensure()
    r = Room("R", "Room", store)
    yield r
    store.close()


_uid = iter(range(1, 10_000))


def join(room: Room, name: str, role: str = "editor") -> tuple:
    s = room.create_session(f"u{next(_uid)}", "#000000", name, role)
    c = FakeConn()
    room.admit(c, s, resumed=False)
    return s, c


def stroke(oid: str) -> dict:
    return {"k": "add", "obj": {"id": oid, "type": "stroke", "geom": {"pts": [0, 0, 5, 5]}}}


def test_admit_sends_joined_then_snapshot(room):
    s, c = join(room, "alice", "owner")
    assert [t for t, _ in c.frames[:2]] == [MsgType.JOINED, MsgType.SNAPSHOT]
    joined = c.frames[0][1]
    assert joined["role"] == "owner" and joined["room"]["owner_id"] == s.user_id
    assert joined["resumed"] is False and joined["resume_token"]
    assert c.frames[1][1]["hash"] == Board().hash()


def test_pipeline_assigns_seq_and_broadcasts_to_all(room):
    a, ca = join(room, "alice", "owner")
    b, cb = join(room, "bob")
    room.submit(a, "a:1", stroke("x1"))
    room.submit(b, "b:1", stroke("x2"))
    for c in (ca, cb):
        ops = c.of(MsgType.OP)
        assert [o["seq"] for o in ops] == [1, 2]
        assert ops[0]["user_id"] == a.user_id and ops[0]["op"]["obj"]["owner"] == a.user_id
    assert room.seq == 2 and len(room.history) == 2
    assert [json.loads(line)["seq"] for line in room.store.ops_path.read_text().splitlines()] == [1, 2]


def test_owner_is_forced_by_server(room):
    a, _ = join(room, "alice", "owner")
    op = stroke("x1")
    op["obj"]["owner"] = "somebody-else"
    room.submit(a, "a:1", op)
    assert room.board.get("x1")["owner"] == a.user_id


def test_dedupe_resends_original_only_to_sender(room):
    a, ca = join(room, "alice", "owner")
    b, cb = join(room, "bob")
    room.submit(a, "a:1", stroke("x1"))
    assert room.submit(a, "a:1", stroke("x1")) is None
    assert [o["seq"] for o in ca.of(MsgType.OP)] == [1, 1]
    assert [o["seq"] for o in cb.of(MsgType.OP)] == [1]
    assert room.seq == 1


@pytest.mark.parametrize("role,locked,kind,expected", [
    ("viewer", False, "add", Err.FORBIDDEN),
    ("editor", False, "add", None),
    ("editor", True, "add", Err.LOCKED),
    ("owner", True, "add", None),
    ("editor", False, "clear", Err.FORBIDDEN),
    ("owner", False, "clear", None),
    ("editor", False, "replace", Err.FORBIDDEN),
    ("owner", False, "replace", None),
])
def test_permission_matrix(room, role, locked, kind, expected):
    owner, _ = join(room, "own", "owner")
    s = owner if role == "owner" else join(room, "x", role)[0]
    room.locked = locked
    op = {"add": stroke("x"), "clear": {"k": "clear"}, "replace": {"k": "replace", "objects": []}}[kind]
    denied = room.check_permission(s, op)
    assert (denied[0] if denied else None) == expected


def test_editors_can_clear_setting(room):
    join(room, "own", "owner")
    e, ce = join(room, "ed")
    room.submit(e, "e:1", stroke("a"))
    room.submit(e, "e:2", {"k": "clear"})
    assert ce.of(MsgType.OP_REJECT)[-1]["code"] == Err.FORBIDDEN
    room.editors_can_clear = True
    msg = room.submit(e, "e:3", {"k": "clear"})
    assert msg["op"] == {"k": "delete", "ids": ["a"], "clear": True}
    assert len(room.board) == 0
    assert any("cleared" in m["text"] for m in ce.of(MsgType.CHAT))


def test_invalid_op_rejected(room):
    a, ca = join(room, "alice", "owner")
    room.submit(a, "a:1", {"k": "add", "obj": {"id": "x", "type": "nope", "geom": {}}})
    rej = ca.of(MsgType.OP_REJECT)[0]
    assert rej["code"] == Err.INVALID_OP and rej["op_id"] == "a:1"
    assert room.seq == 0


def test_rate_limit(room, monkeypatch):
    monkeypatch.setattr(C, "OPS_PER_SEC_LIMIT", 5)
    a, ca = join(room, "alice", "owner")
    for i in range(8):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    assert room.seq == 5
    assert {r["code"] for r in ca.of(MsgType.OP_REJECT)} == {Err.RATE_LIMITED}


def test_token_bucket_refills():
    tb = TokenBucket(rate=10, burst=2)
    assert tb.take() and tb.take() and not tb.take()
    tb.stamp -= 0.5
    assert tb.take()


def test_hash_every_n_ops(room, monkeypatch):
    monkeypatch.setattr(C, "HASH_EVERY_OPS", 3)
    a, ca = join(room, "alice", "owner")
    for i in range(7):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    assert [h["seq"] for h in ca.of(MsgType.HASH)] == [3, 6]
    room.submit(a, "a:r", {"k": "replace", "objects": []})
    assert ca.of(MsgType.HASH)[-1] == {"seq": 8, "hash": room.board.hash()}


def test_unique_usernames(room):
    s1, _ = join(room, "sam")
    s2, _ = join(room, "sam")
    s3, _ = join(room, "sam")
    assert [s1.username, s2.username, s3.username] == ["sam", "sam (2)", "sam (3)"]
    long = "x" * C.MAX_USERNAME
    join(room, long)
    assert len(join(room, long)[0].username) <= C.MAX_USERNAME


def test_catch_up_tail_or_snapshot(room, monkeypatch):
    a, _ = join(room, "alice", "owner")
    for i in range(10):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    c = FakeConn()
    a.conn = c
    room.send_catch_up(a, 7)
    assert [o["seq"] for o in c.of(MsgType.OP_BATCH)[0]["ops"]] == [8, 9, 10]
    room.send_catch_up(a, 10)
    assert c.of(MsgType.OP_BATCH)[-1]["ops"] == []
    room.send_catch_up(a, 99)
    room.send_catch_up(a, None)
    assert len(c.of(MsgType.SNAPSHOT)) == 2


def test_history_trim_keeps_base_consistent(room, monkeypatch):
    monkeypatch.setattr(C, "HISTORY_MAX_OPS", 20)
    a, _ = join(room, "alice", "owner")
    for i in range(50):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    assert len(room.history) <= 20
    assert room.history[0]["seq"] == room.base_seq + 1
    replay = room.history_base.copy()
    for m in room.history:
        replay.apply(m["op"], m["seq"])
    assert replay.hash() == room.board.hash()
    c = FakeConn()
    a.conn = c
    room.send_catch_up(a, 5)
    assert c.of(MsgType.SNAPSHOT) and not c.of(MsgType.OP_BATCH)
    h = room.history_payload()
    assert h["base_seq"] == room.base_seq and len(h["ops"]) == len(room.history)


def test_owner_promotion_prefers_earliest_online_editor(room):
    owner, _ = join(room, "own", "owner")
    viewer, _ = join(room, "v", "viewer")
    ed1, _ = join(room, "e1")
    ed2, c2 = join(room, "e2")
    room.detach(ed1, Reason.DISCONNECTED)
    room.remove_session(owner, Reason.EXPIRED)
    assert room.owner_id == ed2.user_id and ed2.role == "owner"
    assert c2.of(MsgType.ROOM_UPDATE)[-1]["room"]["owner_id"] == ed2.user_id
    assert viewer.role == "viewer"


def test_owner_promotion_falls_back_to_anyone_online(room):
    owner, _ = join(room, "own", "owner")
    viewer, _ = join(room, "v", "viewer")
    room.remove_session(owner, Reason.LEFT)
    assert room.owner_id == viewer.user_id and viewer.role == "owner"


def test_session_expiry(room, monkeypatch):
    monkeypatch.setattr(C, "SESSION_TTL", 10)
    a, ca = join(room, "alice", "owner")
    b, _ = join(room, "bob")
    room.detach(b, Reason.TIMEOUT)
    room.expire_sessions(b.disconnected_at + 5)
    assert b.user_id in room.sessions
    room.expire_sessions(b.disconnected_at + 11)
    assert b.user_id not in room.sessions and b.resume_token not in room.by_token
    assert ca.of(MsgType.USER_LEFT)[-1] == {"user_id": b.user_id, "reason": Reason.EXPIRED}


async def test_admin_requires_owner(room):
    join(room, "own", "owner")
    e, ce = join(room, "ed")
    for t, p in [(MsgType.SET_ROLE, {"user_id": e.user_id, "role": "viewer"}),
                 (MsgType.KICK, {"user_id": e.user_id}),
                 (MsgType.ROOM_SETTINGS, {"locked": True}),
                 (MsgType.LOAD, {"name": "x"})]:
        await room.handle(e, t, p)
    assert [x["code"] for x in ce.of(MsgType.ERROR)] == [Err.FORBIDDEN] * 4
    assert not room.locked and e.role == "editor"


async def test_set_role_and_ownership_transfer(room):
    o, co = join(room, "own", "owner")
    e, _ = join(room, "ed")
    await room.handle(o, MsgType.SET_ROLE, {"user_id": e.user_id, "role": "viewer"})
    assert e.role == "viewer"
    assert co.of(MsgType.USER_UPDATE)[-1]["participant"]["role"] == "viewer"
    await room.handle(o, MsgType.SET_ROLE, {"user_id": e.user_id, "role": "owner"})
    assert room.owner_id == e.user_id and e.role == "owner" and o.role == "editor"


async def test_kick_invalidates_session(room):
    o, co = join(room, "own", "owner")
    e, ce = join(room, "ed")
    token = e.resume_token
    await room.handle(o, MsgType.KICK, {"user_id": e.user_id})
    assert ce.of(MsgType.ERROR)[-1]["code"] == Err.KICKED and ce.closed == Reason.KICKED
    assert e.user_id not in room.sessions and token not in room.by_token
    assert co.of(MsgType.USER_LEFT)[-1]["reason"] == Reason.KICKED


async def test_room_settings_validated_before_applying(room):
    o, co = join(room, "owner", "owner")
    await room.handle(o, MsgType.ROOM_SETTINGS, {"locked": True, "name": "New", "presenter": "nobody"})
    assert co.of(MsgType.ERROR)[-1]["code"] == Err.NOT_FOUND
    assert room.locked is False and room.name != "New"


async def test_room_settings_and_presenter_viewport(room):
    o, co = join(room, "own", "owner")
    e, ce = join(room, "ed")
    await room.handle(e, MsgType.VIEWPORT, {"x": 0, "y": 0, "w": 10, "h": 10})
    assert not co.of(MsgType.VIEWPORT)
    await room.handle(o, MsgType.ROOM_SETTINGS, {"presenter": e.user_id, "name": "Lab 3",
                                                 "password": {"salt": "ab" * 16, "verifier": "cd" * 32}})
    assert room.presenter == e.user_id and room.name == "Lab 3" and room.protected
    await room.handle(e, MsgType.VIEWPORT, {"x": 0, "y": 0, "w": 10, "h": 10})
    assert co.of(MsgType.VIEWPORT)[-1]["user_id"] == e.user_id
    await room.handle(o, MsgType.ROOM_SETTINGS, {"password": None})
    assert not room.protected
    assert ce.of(MsgType.ROOM_UPDATE)[-1]["room"]["protected"] is False


async def test_ephemeral_relay_rules(room):
    o, co = join(room, "own", "owner")
    v, cv = join(room, "view", "viewer")
    live = {"id": "s1", "color": "#000", "width": 2, "pts": [1, 2, 3, 4]}
    await room.handle(v, MsgType.CURSOR, {"x": 1, "y": 2})
    await room.handle(v, MsgType.STROKE_LIVE, live)
    await room.handle(v, MsgType.LASER, {"pts": [1, 2]})
    assert co.of(MsgType.CURSOR) == [{"x": 1, "y": 2, "user_id": v.user_id}]
    assert not co.of(MsgType.STROKE_LIVE)
    assert co.of(MsgType.LASER) == [{"pts": [1, 2], "user_id": v.user_id}]
    await room.handle(o, MsgType.STROKE_LIVE, live)
    assert cv.of(MsgType.STROKE_LIVE)[0]["user_id"] == o.user_id
    assert not co.of(MsgType.STROKE_LIVE)
    await room.handle(v, MsgType.SIGNAL, {"kind": "hand_up"})
    assert v.hand and co.of(MsgType.USER_UPDATE)[-1]["participant"]["hand"] is True


async def test_chat_is_capped_and_stamped(room):
    a, ca = join(room, "alice", "owner")
    await room.handle(a, MsgType.CHAT_SEND, {"cmid": "m1", "text": "  hi  "})
    await room.handle(a, MsgType.CHAT_SEND, {"cmid": "m2", "text": "   "})
    await room.handle(a, MsgType.CHAT_SEND, {"cmid": "m3", "text": "y" * (C.MAX_CHAT_LEN + 50)})
    user = [m for m in ca.of(MsgType.CHAT) if m["kind"] == "user"]
    assert user[0]["text"] == "hi" and user[0]["cmid"] == "m1" and user[0]["username"] == "alice"
    assert len(user) == 2 and len(user[1]["text"]) == C.MAX_CHAT_LEN


def test_room_reload_from_disk(tmp_path):
    store = RoomStore(tmp_path / "r")
    store.ensure()
    room = Room("R", "Room", store)
    a, _ = join(room, "alice", "owner")
    for i in range(5):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    store.save_meta(room.to_meta())
    with open(store.ops_path, "a") as fh:
        fh.write('{"seq": 6, "user_id": "u1", "op_id"')
    store.close()
    again = Room.load(RoomStore(tmp_path / "r"))
    assert again.seq == 5 and again.board.hash() == room.board.hash()
    assert again.sessions[a.user_id].resume_token == a.resume_token
    assert "a:3" in again.sessions[a.user_id].accepted
    assert again.owner_id == a.user_id


async def test_snapshot_truncates_log(room):
    a, _ = join(room, "alice", "owner")
    for i in range(5):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    await room.write_snapshot()
    assert room.store.ops_path.read_text() == ""
    assert room.store.load_snapshot()["seq"] == 5
    room.submit(a, "a:9", stroke("x9"))
    assert len(room.store.load_ops()) == 1


def test_safe_name():
    assert safe_name("lab-1") == "lab-1"
    assert safe_name("Lab-1") != safe_name("lab-1")
    assert safe_name("../../etc") .startswith("etc-")
    assert safe_name("con") != "con"
    assert "/" not in safe_name("a/b") and safe_name("a/b") != safe_name("a_b")


def test_storage_lists_rooms(tmp_path):
    st = Storage(tmp_path)
    rs = st.room_store("X 1")
    rs.ensure()
    rs.save_meta({"code": "X 1"})
    assert [r.dir for r in st.iter_room_stores()] == [rs.dir]


def _replay(base_seq: int, base_objects: list, ops: list) -> Board:
    b = Board.from_snapshot({"seq": base_seq, "objects": base_objects})
    for m in ops:
        b.apply(m["op"], m["seq"])
    return b


@pytest.mark.parametrize("final_flush", [True, False], ids=["clean-stop", "crash"])
async def test_history_survives_restart(tmp_path, final_flush):
    store = RoomStore(tmp_path / "r")
    store.ensure()
    room = Room("R", "Room", store)
    a, _ = join(room, "alice", "owner")
    for i in range(30):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    await room.write_snapshot()
    for i in range(30, 45):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    await room.write_snapshot()
    for i in range(45, 52):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    assert len(store.load_ops()) == 7
    store.save_meta(room.to_meta())
    if final_flush:
        await room.close()
    else:
        store.close()
    again = Room.load(RoomStore(tmp_path / "r"))
    assert again.seq == 52 and again.board.hash() == room.board.hash()
    h = again.history_payload()
    assert h["base_seq"] == 0 and [m["seq"] for m in h["ops"]] == list(range(1, 53))
    assert _replay(0, h["base"]["objects"], h["ops"]).hash() == room.board.hash()
    b = again.sessions[a.user_id]
    again.submit(b, "a:99", stroke("x99"))
    await again.close()
    third = Room.load(RoomStore(tmp_path / "r"))
    assert [m["seq"] for m in third.history] == list(range(1, 54))
    third.store.close()


async def test_history_cap_trims_files(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "HISTORY_MAX_OPS", 20)
    store = RoomStore(tmp_path / "r")
    store.ensure()
    room = Room("R", "Room", store)
    a, _ = join(room, "alice", "owner")
    for i in range(55):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
        if i % 10 == 9:
            await room.write_snapshot()
    store.save_meta(room.to_meta())
    await room.close()
    assert len(store.load_ops()) == 0 and len(RoomStore(tmp_path / "r").load_history()[1]) <= 20
    again = Room.load(RoomStore(tmp_path / "r"))
    assert again.base_seq > 0 and len(again.history) <= 20
    assert again.history[0]["seq"] == again.base_seq + 1
    assert again.board.hash() == room.board.hash()
    h = again.history_payload()
    assert _replay(h["base_seq"], h["base"]["objects"], h["ops"]).hash() == room.board.hash()
    again.store.close()


def test_history_byte_cap(room, monkeypatch):
    monkeypatch.setattr(C, "HISTORY_MAX_BYTES", 2000)
    a, _ = join(room, "alice", "owner")
    for i in range(40):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    assert room.history_bytes <= 2000 and room.history_bytes == sum(room.history_sizes)
    assert room.base_seq > 0 and room.history[0]["seq"] == room.base_seq + 1
    h = room.history_payload()
    assert _replay(h["base_seq"], h["base"]["objects"], h["ops"]).hash() == room.board.hash()


def test_old_data_dir_without_history_files_still_loads(tmp_path):
    """A room written before history.jsonl existed: snapshot + ops.jsonl only."""
    store = RoomStore(tmp_path / "r")
    store.ensure()
    board = Board()
    for i in range(1, 6):
        board.apply(stroke(f"x{i}"), i)
    store.write_snapshot(board.snapshot())
    for i in range(6, 9):
        op = stroke(f"x{i}")
        board.apply(op, i)
        store.append_op({"seq": i, "user_id": "u1", "op_id": f"a:{i}", "ts": 0, "op": op})
    store.save_meta({"code": "R", "name": "Room"})
    store.close()
    room = Room.load(RoomStore(tmp_path / "r"))
    assert room.seq == 8 and room.board.hash() == board.hash()
    assert room.base_seq == 5 and [m["seq"] for m in room.history] == [6, 7, 8]
    room.store.close()


def test_append_after_torn_line_does_not_glue_records(tmp_path):
    store = RoomStore(tmp_path / "r")
    store.ensure()
    room = Room("R", "Room", store)
    a, _ = join(room, "alice", "owner")
    for i in range(3):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    store.save_meta(room.to_meta())
    store.close()
    with open(store.ops_path, "a") as fh:
        fh.write('{"seq": 4, "user_id": "u1", "op_id"')
    again = Room.load(RoomStore(tmp_path / "r"))
    s = again.sessions[a.user_id]
    again.submit(s, "a:7", stroke("y"))
    again.store.close()
    third = Room.load(RoomStore(tmp_path / "r"))
    assert third.seq == 4 and third.board.is_live("y")
    third.store.close()


def test_snapshot_split_into_parts(room, monkeypatch):
    monkeypatch.setattr(C, "PART_BYTES", 1500)
    a, _ = join(room, "alice", "owner")
    for i in range(60):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    _, c = join(room, "late")
    snaps = c.of(MsgType.SNAPSHOT)
    assert len(snaps) > 3
    assert [p["part"] for p in snaps] == list(range(len(snaps)))
    assert {p["parts"] for p in snaps} == {len(snaps)} and len({p["xid"] for p in snaps}) == 1
    assert all(p["seq"] == 60 and p["hash"] == room.board.hash() for p in snaps)
    merged = Board.from_snapshot({"seq": 60, "objects": [o for p in snaps for o in p["board"]["objects"]]})
    assert merged.hash() == room.board.hash()


def test_catch_up_split_by_bytes(room, monkeypatch):
    monkeypatch.setattr(C, "PART_BYTES", 1000)
    a, _ = join(room, "alice", "owner")
    for i in range(30):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    c = FakeConn()
    a.conn = c
    room.send_catch_up(a, 0)
    batches = c.of(MsgType.OP_BATCH)
    assert len(batches) > 2
    assert [m["seq"] for b in batches for m in b["ops"]] == list(range(1, 31))


async def test_history_request_split_into_parts(room, monkeypatch):
    monkeypatch.setattr(C, "PART_BYTES", 1000)
    monkeypatch.setattr(C, "HISTORY_MAX_OPS", 30)
    a, ca = join(room, "alice", "owner")
    for i in range(40):
        room.submit(a, f"a:{i}", stroke(f"x{i}"))
    await room.handle(a, MsgType.HISTORY_REQ, {})
    parts = ca.of(MsgType.HISTORY)
    assert len(parts) > 2 and {p["parts"] for p in parts} == {len(parts)}
    from lanboard.common.protocol import PartAssembler
    asm = PartAssembler()
    done = [m for m in (asm.add(MsgType.HISTORY, p) for p in parts) if m is not None]
    assert len(done) == 1 and done[0]["base_seq"] == room.base_seq > 0
    h = done[0]
    assert _replay(h["base_seq"], h["base"]["objects"], h["ops"]).hash() == room.board.hash()


def test_oversized_op_rejected_without_side_effects(room, monkeypatch):
    monkeypatch.setattr(C, "MAX_OP_BYTES", 5000)
    a, ca = join(room, "alice", "owner")
    b, cb = join(room, "bob")
    big = {"k": "add", "obj": {"id": "big", "type": "stroke", "geom": {"pts": list(range(4000))}}}
    assert room.submit(a, "a:1", big) is None
    rej = ca.of(MsgType.OP_REJECT)
    assert rej[-1]["op_id"] == "a:1" and rej[-1]["code"] == Err.TOO_LARGE
    assert "too large" in rej[-1]["msg"]
    assert room.seq == 0 and not room.history and not cb.of(MsgType.OP)
    assert room.store.ops_path.exists() is False or room.store.ops_path.read_text() == ""
    assert room.submit(a, "a:2", stroke("ok")) is not None and room.seq == 1
