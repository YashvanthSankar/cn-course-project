"""End-to-end tests over real TCP sockets on an ephemeral localhost port."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from lanboard.common import auth
from lanboard.common import config as C
from lanboard.common.messages import Err, MsgType, Reason
from lanboard.common.model import Board
from lanboard.common.protocol import FrameDecoder, encode
from lanboard.server import Server
from lanboard.tools.bot import BotClient, JoinError

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
async def server(tmp_path):
    srv = Server("127.0.0.1", 0, str(tmp_path / "data"), "test", discovery=False)
    await srv.start()
    yield srv
    await srv.stop()


async def make(srv: Server, name: str, room: str = "R", **kw) -> BotClient:
    b = BotClient("127.0.0.1", srv.port, name, room, **kw)
    await b.connect()
    return b


def stroke(bot: BotClient, x: int = 0) -> dict:
    return {"k": "add", "obj": {"id": bot.new_id(), "type": "stroke",
                                "style": {"color": "#000000", "width": 2},
                                "geom": {"pts": [x, 0, x + 10, 10]}}}


async def caught_up(srv: Server, *bots: BotClient, room: str = "R") -> None:
    for b in bots:
        await b.settle()                       # every op acked => the server has sequenced it
    seq = srv.rooms[room].seq
    for b in bots:
        await b.wait_until(lambda b=b: b.board.seq == seq)


# ---------------------------------------------------------------------------
# handshake & join
# ---------------------------------------------------------------------------

async def test_create_join_and_errors(server):
    a = await make(server, "alice", create=True, room_name="Lab")
    assert a.role == "owner" and a.room["name"] == "Lab" and a.user_id.startswith("u")
    with pytest.raises(JoinError) as e:
        await make(server, "x", create=True)
    assert e.value.code == Err.ROOM_EXISTS
    with pytest.raises(JoinError) as e:
        await make(server, "x", room="NOPE")
    assert e.value.code == Err.ROOM_NOT_FOUND
    b = await make(server, "alice")
    assert b.role == "editor" and b.joined["username"] == "alice (2)"
    assert b.joined["color"] != a.joined["color"] and b.user_id != a.user_id
    p = await a.wait_for(MsgType.USER_JOINED)
    assert p["participant"]["user_id"] == b.user_id
    await a.close(); await b.close()


async def test_list_rooms_and_ping_before_join(server):
    a = await make(server, "alice", create=True)
    r, w = await asyncio.open_connection("127.0.0.1", server.port)
    dec = FrameDecoder()
    w.write(encode(MsgType.HELLO, {"proto": C.PROTO_VERSION, "app": "lanboard",
                                   "username": "probe", "client_id": "p"}))
    w.write(encode(MsgType.LIST_ROOMS, {}))
    w.write(encode(MsgType.PING, {"t": 1.5}))
    frames = []
    while len(frames) < 3:
        frames += dec.feed(await asyncio.wait_for(r.read(65536), 2))
    types = [t for t, _ in frames]
    assert types == [MsgType.CHALLENGE, MsgType.ROOMS, MsgType.PONG]
    assert frames[1][1]["rooms"] == [{"code": "R", "name": "R", "users": 1,
                                      "protected": False, "locked": False}]
    assert frames[2][1]["t"] == 1.5 and "server_time" in frames[2][1]
    w.close()
    await a.close()


async def test_proto_mismatch_and_bad_frames(server):
    for payload in (encode(MsgType.HELLO, {"proto": 999, "username": "x", "client_id": "c"}),
                    b"\x00\x00\x00\x05\x01notjson",
                    encode(MsgType.JOIN, {"room": "R"})):          # JOIN before HELLO
        r, w = await asyncio.open_connection("127.0.0.1", server.port)
        w.write(payload)
        dec, frames = FrameDecoder(), []
        while True:
            data = await asyncio.wait_for(r.read(65536), 2)
            if not data:
                break                                             # server closed the socket
            frames += dec.feed(data)
        assert frames[-1][0] == MsgType.ERROR
        assert frames[-1][1]["code"] in (Err.PROTO_MISMATCH, Err.BAD_MESSAGE)
        w.close()


async def test_late_join_gets_snapshot(server):
    a = await make(server, "alice", create=True)
    for i in range(5):
        a.submit(stroke(a, i))
    await a.settle()
    a.send_chat("before you came")
    await a.wait_for(MsgType.CHAT, lambda p: p["kind"] == "user")
    b = await make(server, "bob")
    assert b.snapshots == 1 and b.board.seq == 5
    assert b.board.hash() == a.board.hash() == server.rooms["R"].board.hash()
    assert any(m["text"] == "before you came" for m in b.joined["chat"])
    await a.close(); await b.close()


# ---------------------------------------------------------------------------
# ordering, dedupe, resume
# ---------------------------------------------------------------------------

async def test_concurrent_ops_are_totally_ordered(server):
    bots = [await make(server, "alice", create=True)]
    bots += [await make(server, f"b{i}") for i in range(3)]
    for i in range(40):
        for b in bots:
            b.submit(stroke(b, i))
    await caught_up(server, *bots)
    assert server.rooms["R"].seq == 160
    ref = server.rooms["R"].board.hash()
    for b in bots:
        seqs = [p["seq"] for p in b.received(MsgType.OP)]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)   # in order, no gaps/dups
        assert b.board.hash() == ref
    for b in bots:
        await b.close()


async def test_duplicate_submit_is_deduplicated(server):
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    op = stroke(a)
    for _ in range(3):
        a.send(MsgType.OP_SUBMIT, {"op_id": "dup-1", "op": op})
    await a.wait_until(lambda: len(a.received(MsgType.OP)) == 3)
    seqs = [p["seq"] for p in a.received(MsgType.OP)]
    assert seqs == [1, 1, 1]                      # original OP re-sent to the sender
    await asyncio.sleep(0.1)
    assert [p["seq"] for p in b.received(MsgType.OP)] == [1]
    assert server.rooms["R"].seq == 1 and a.dup_ops == 2
    await a.close(); await b.close()


async def test_resume_gets_op_batch_and_same_identity(server):
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    uid, token = b.user_id, b.resume_token
    snaps_before = b.snapshots
    a.submit(stroke(a))
    await caught_up(server, a, b)
    b.drop()
    left = await a.wait_for(MsgType.USER_LEFT)
    assert left == {"user_id": uid, "reason": Reason.DISCONNECTED}
    for i in range(5):
        a.submit(stroke(a, i))
    offline_op = b.submit(stroke(b, 99))          # made while offline: queued, sent on resume
    await a.settle()
    joined = await b.connect()
    assert joined["resumed"] is True and b.user_id == uid and b.resume_token == token
    assert b.snapshots == snaps_before            # healed with OP_BATCH, not a snapshot
    await b.settle()
    assert offline_op not in b.pending
    await caught_up(server, a, b)
    assert b.board.hash() == a.board.hash() == server.rooms["R"].board.hash()
    back = await a.wait_for(MsgType.USER_JOINED)
    assert back["participant"]["user_id"] == uid
    await a.wait_for(MsgType.CHAT, lambda p: p["kind"] == "system" and "reconnected" in p["text"])
    await a.close(); await b.close()


async def test_resume_too_old_gets_snapshot(server, monkeypatch):
    monkeypatch.setattr(C, "HISTORY_MAX_OPS", 10)
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    b.drop()
    for i in range(30):
        a.submit(stroke(a, i))
    await a.settle()
    await b.connect()
    assert b.joined["resumed"] is True and b.snapshots == 2
    assert b.board.hash() == server.rooms["R"].board.hash()
    await a.close(); await b.close()


async def test_unknown_resume_token_is_fresh_join(server):
    a = await make(server, "alice", create=True)
    b = BotClient("127.0.0.1", server.port, "bob", "R")
    b.resume_token = "not-a-real-token"
    await b.connect()
    assert b.joined["resumed"] is False and b.snapshots == 1
    await a.close(); await b.close()


async def test_resync_fills_gap(server):
    a = await make(server, "alice", create=True)
    for i in range(6):
        a.submit(stroke(a, i))
    await a.settle()
    a.send(MsgType.RESYNC, {"last_seq": 2})
    batch = await a.wait_for(MsgType.OP_BATCH)
    assert [m["seq"] for m in batch["ops"]] == [3, 4, 5, 6]
    await a.close()


# ---------------------------------------------------------------------------
# auth & permissions
# ---------------------------------------------------------------------------

async def test_password_room(server):
    owner = await make(server, "alice", create=True, password="hunter2")
    assert owner.room["protected"] is True
    with pytest.raises(JoinError) as e:
        await make(server, "eve")
    assert e.value.code == Err.AUTH_REQUIRED
    with pytest.raises(JoinError) as e:
        await make(server, "eve", password="wrong")
    assert e.value.code == Err.BAD_PASSWORD
    ok = await make(server, "bob", password="hunter2")
    assert ok.role == "editor"

    # raw: the nonce changes after each failure, so an old proof cannot be replayed
    r, w = await asyncio.open_connection("127.0.0.1", server.port)
    dec = FrameDecoder()
    queue: list = []

    async def recv():
        while not queue:
            queue.extend(dec.feed(await asyncio.wait_for(r.read(65536), 2)))
        return queue.pop(0)
    w.write(encode(MsgType.HELLO, {"proto": 1, "app": "x", "username": "m", "client_id": "m"}))
    _, ch = await recv()
    w.write(encode(MsgType.JOIN, {"room": "R", "create": False}))
    _, err1 = await recv()
    assert err1["code"] == Err.AUTH_REQUIRED and err1["salt"] and err1["nonce"] != ch["nonce"]
    good_for_old = auth.make_proof(auth.make_verifier("hunter2", err1["salt"]), ch["nonce"])
    w.write(encode(MsgType.JOIN, {"room": "R", "create": False, "proof": good_for_old}))
    _, err2 = await recv()
    assert err2["code"] == Err.BAD_PASSWORD and err2["nonce"] != err1["nonce"]
    proof = auth.make_proof(auth.make_verifier("hunter2", err2["salt"]), err2["nonce"])
    w.write(encode(MsgType.JOIN, {"room": "R", "create": False, "proof": proof}))
    t, joined = await recv()
    while t != MsgType.JOINED:
        t, joined = await recv()
    assert joined["role"] == "editor"
    w.close()
    await owner.close(); await ok.close()


async def test_viewer_cannot_edit(server):
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    a.send(MsgType.SET_ROLE, {"user_id": b.user_id, "role": "viewer"})
    await b.wait_until(lambda: b.role == "viewer")
    op_id = b.submit(stroke(b))
    rej = await b.wait_for(MsgType.OP_REJECT)
    assert rej["op_id"] == op_id and rej["code"] == Err.FORBIDDEN
    b.send(MsgType.CURSOR, {"x": 3, "y": 4})
    b.send(MsgType.STROKE_LIVE, {"id": "s", "color": "#000", "width": 2, "pts": [0, 0]})
    cur = await a.wait_for(MsgType.CURSOR)
    assert cur == {"x": 3, "y": 4, "user_id": b.user_id}
    await asyncio.sleep(0.1)
    assert not a.received(MsgType.STROKE_LIVE)
    assert server.rooms["R"].seq == 0
    await a.close(); await b.close()


async def test_lock_room(server):
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    a.send(MsgType.ROOM_SETTINGS, {"locked": True})
    upd = await b.wait_for(MsgType.ROOM_UPDATE)
    assert upd["room"]["locked"] is True
    b.submit(stroke(b))
    assert (await b.wait_for(MsgType.OP_REJECT))["code"] == Err.LOCKED
    a.submit(stroke(a))
    await a.settle()
    assert server.rooms["R"].seq == 1
    b.send(MsgType.ROOM_SETTINGS, {"locked": False})          # editors cannot unlock
    assert (await b.wait_for(MsgType.ERROR))["code"] == Err.FORBIDDEN
    await b.wait_for(MsgType.CHAT, lambda p: "locked" in p["text"])
    await a.close(); await b.close()


async def test_clear_permission(server):
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    for i in range(3):
        a.submit(stroke(a, i))
    await a.settle()
    b.submit({"k": "clear"})
    assert (await b.wait_for(MsgType.OP_REJECT))["code"] == Err.FORBIDDEN
    a.send(MsgType.ROOM_SETTINGS, {"editors_can_clear": True})
    await b.wait_until(lambda: b.room["editors_can_clear"])
    b.submit({"k": "clear"})
    await caught_up(server, a, b)
    last = a.received(MsgType.OP)[-1]
    assert last["op"]["k"] == "delete" and last["op"]["clear"] is True
    assert len(a.board) == 0 and a.board.hash() == server.rooms["R"].board.hash()
    await a.close(); await b.close()


async def test_kick(server):
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    a.send(MsgType.KICK, {"user_id": b.user_id})
    err = await b.wait_for(MsgType.ERROR)
    assert err["code"] == Err.KICKED
    await b.wait_until(lambda: not b.connected)              # socket closed by the server
    left = await a.wait_for(MsgType.USER_LEFT)
    assert left["reason"] == Reason.KICKED
    with pytest.raises(JoinError) as exc:                     # kick = ban: the same app install
        await b.connect(timeout=2)                            # cannot simply rejoin
    assert exc.value.code == Err.KICKED
    other = await make(server, "bob")                         # a different client still can
    assert other.joined["resumed"] is False
    await a.close(); await b.close(); await other.close()


# ---------------------------------------------------------------------------
# liveness
# ---------------------------------------------------------------------------

async def test_silent_client_times_out(server, monkeypatch):
    monkeypatch.setattr(C, "CLIENT_TIMEOUT", 0.6)
    monkeypatch.setattr(C, "PING_INTERVAL", 0.1)
    a = await make(server, "alice", create=True)              # pings every 0.1 s
    b = await make(server, "bob", ping=False)                 # silent
    left = await a.wait_for(MsgType.USER_LEFT, timeout=4)
    assert left == {"user_id": b.user_id, "reason": Reason.TIMEOUT}
    await b.wait_until(lambda: not b.connected, timeout=2)
    assert a.connected and a.rtt is not None
    await asyncio.sleep(0.2)
    assert server.rooms["R"].sessions[a.user_id].rtt is not None   # client-reported RTT kept
    await a.close(); await b.close()


async def test_owner_expiry_promotes_editor(server, monkeypatch):
    monkeypatch.setattr(C, "SESSION_TTL", 0.2)
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    a.drop()
    upd = await b.wait_for(MsgType.ROOM_UPDATE, lambda p: p["room"]["owner_id"] == b.user_id,
                           timeout=4)
    assert upd and b.role == "owner"
    await b.close()


async def test_slow_reader_is_dropped_as_lagging(server, monkeypatch):
    monkeypatch.setattr(C, "SEND_QUEUE_MAX", 4)
    a = await make(server, "alice", create=True)
    slow = socket.socket()
    slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    slow.connect(("127.0.0.1", server.port))
    slow.sendall(encode(MsgType.HELLO, {"proto": 1, "app": "x", "username": "slow",
                                        "client_id": "s"})
                 + encode(MsgType.JOIN, {"room": "R", "create": False}))
    await a.wait_for(MsgType.USER_JOINED)
    big = [n % 900_000 + 100_000 for n in range(C.MAX_STROKE_POINTS)]
    for i in range(60):
        a.submit({"k": "add", "obj": {"id": a.new_id(), "type": "stroke", "geom": {"pts": big}}})
    left = await a.wait_for(MsgType.USER_LEFT, timeout=10)
    assert left["reason"] == Reason.LAGGING
    await a.settle(15)
    slow.close()
    await a.close()


# ---------------------------------------------------------------------------
# consistency checkpoints, history, saves
# ---------------------------------------------------------------------------

async def test_hash_broadcast(server):
    a = await make(server, "alice", create=True)
    for i in range(C.HASH_EVERY_OPS):
        a.submit(stroke(a, i))
    h = await a.wait_for(MsgType.HASH)
    assert h["seq"] == C.HASH_EVERY_OPS and h["hash"] == a.board.hash()
    assert a.hash_checks == 1 and a.hash_mismatches == 0
    await a.close()


async def test_history_request_replays_to_board(server):
    a = await make(server, "alice", create=True)
    for i in range(8):
        a.submit(stroke(a, i))
    a.submit({"k": "delete", "ids": [a.board.live_ids()[0] if a.board.live_ids() else "x"]})
    await a.settle()
    a.send(MsgType.HISTORY_REQ, {})
    h = await a.wait_for(MsgType.HISTORY)
    assert h["base_seq"] == 0 and len(h["ops"]) == server.rooms["R"].seq
    replay = Board.from_snapshot({"seq": h["base_seq"], "objects": h["base"]["objects"]})
    for m in h["ops"]:
        replay.apply(m["op"], m["seq"])
    assert replay.hash() == server.rooms["R"].board.hash()
    await a.close()


async def test_save_list_load(server):
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    for i in range(4):
        a.submit(stroke(a, i))
    await a.settle()
    saved_hash = server.rooms["R"].board.hash()
    a.send(MsgType.SAVE, {"name": "draft 1"})
    saves = await a.wait_for(MsgType.SAVES)
    assert saves["saves"][0]["name"] == "draft 1" and saves["saves"][0]["objects"] == 4
    a.submit({"k": "clear"})
    await a.settle()
    b.send(MsgType.LOAD, {"name": "draft 1"})
    assert (await b.wait_for(MsgType.ERROR))["code"] == Err.FORBIDDEN
    a.send(MsgType.LOAD, {"name": "nope"})
    assert (await a.wait_for(MsgType.ERROR))["code"] == Err.NOT_FOUND
    a.send(MsgType.LOAD, {"name": "draft 1"})
    h = await b.wait_for(MsgType.HASH)                     # a replace always gets a checkpoint
    await caught_up(server, a, b)
    assert b.received(MsgType.OP)[-1]["op"]["k"] == "replace"
    assert h["hash"] == b.board.hash() == server.rooms["R"].board.hash()
    assert len(b.board) == 4 and server.rooms["R"].board.hash() == saved_hash   # restored exactly
    b.send(MsgType.LIST_SAVES, {})
    assert [s["name"] for s in (await b.wait_for(MsgType.SAVES))["saves"]] == ["draft 1"]
    await a.wait_for(MsgType.CHAT, lambda p: "loaded" in p["text"])
    await a.close(); await b.close()


async def test_chat(server):
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    cmid = a.send_chat("  hello world \n")
    m = await b.wait_for(MsgType.CHAT, lambda p: p["kind"] == "user")
    assert m["text"] == "hello world" and m["user_id"] == a.user_id and m["username"] == "alice"
    assert m["color"] == a.joined["color"] and isinstance(m["ts"], float)
    mine = await a.wait_for(MsgType.CHAT, lambda p: p["kind"] == "user")
    assert mine["cmid"] == cmid                           # sender correlates its echo
    await a.close(); await b.close()


# ---------------------------------------------------------------------------
# blobs
# ---------------------------------------------------------------------------

def blob_chunks(data: bytes, mime: str = "image/png") -> list[dict]:
    sha = hashlib.sha256(data).hexdigest()
    n = max(1, -(-len(data) // C.BLOB_CHUNK))
    return [{"hash": sha, "mime": mime, "size": len(data), "index": i, "count": n,
             "data": base64.b64encode(data[i * C.BLOB_CHUNK:(i + 1) * C.BLOB_CHUNK]).decode()}
            for i in range(n)]


async def test_blob_upload_and_download(server):
    a = await make(server, "alice", create=True)
    b = await make(server, "bob")
    data = os.urandom(2 * C.BLOB_CHUNK + 1234)
    chunks = blob_chunks(data)
    for c in reversed(chunks):                             # any order works
        a.send(MsgType.BLOB_PUT, c)
    stored = await a.wait_for(MsgType.BLOB_STORED)
    assert stored == {"hash": chunks[0]["hash"]}
    b.send(MsgType.BLOB_GET, {"hash": chunks[0]["hash"]})
    got = []
    while len(got) < 3:
        got.append(await b.wait_for(MsgType.BLOB_DATA))
    assert [g["index"] for g in got] == [0, 1, 2] and got[0]["mime"] == "image/png"
    assert b"".join(base64.b64decode(g["data"]) for g in got) == data
    b.send(MsgType.BLOB_GET, {"hash": "0" * 64})
    assert (await b.wait_for(MsgType.ERROR))["code"] == Err.NOT_FOUND

    # corrupted upload: sha256 does not match
    bad = blob_chunks(b"hello")[0]
    bad["hash"] = "f" * 64
    a.send(MsgType.BLOB_PUT, bad)
    assert (await a.wait_for(MsgType.ERROR))["code"] == Err.BAD_MESSAGE
    # too large
    big = dict(bad, size=C.MAX_BLOB + 1, hash="e" * 64)
    a.send(MsgType.BLOB_PUT, big)
    assert (await a.wait_for(MsgType.ERROR))["code"] == Err.TOO_LARGE
    # viewers cannot upload
    a.send(MsgType.SET_ROLE, {"user_id": b.user_id, "role": "viewer"})
    await b.wait_until(lambda: b.role == "viewer")
    b.send(MsgType.BLOB_PUT, blob_chunks(b"x")[0])
    assert (await b.wait_for(MsgType.ERROR))["code"] == Err.FORBIDDEN
    await a.close(); await b.close()


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

async def test_restart_restores_board_and_sessions(tmp_path):
    data = str(tmp_path / "data")
    srv = Server("127.0.0.1", 0, data, discovery=False)
    await srv.start()
    a = await make(srv, "alice", create=True, password="pw")
    for i in range(7):
        a.submit(stroke(a, i))
    a.send_chat("remember me")
    await a.settle()
    await a.wait_for(MsgType.CHAT, lambda p: p["kind"] == "user")
    want = srv.rooms["R"].board.hash()
    a.drop()
    await srv.stop()

    srv2 = Server("127.0.0.1", 0, data, discovery=False)
    await srv2.start()
    try:
        room = srv2.rooms["R"]
        assert room.seq == 7 and room.board.hash() == want and room.protected
        a.port = srv2.port
        await a.connect()                                 # resume token survived the restart
        assert a.joined["resumed"] is True and a.role == "owner"
        assert a.board.hash() == want
        assert any(m["text"] == "remember me" for m in a.joined["chat"])
        b = await make(srv2, "bob", password="pw")
        assert int(b.user_id[1:]) > int(a.user_id[1:])    # user ids are never reused
        await a.close(); await b.close()
    finally:
        await srv2.stop()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(sys.platform == "win32", reason="uses SIGKILL")
async def test_killed_server_recovers_from_log(tmp_path):
    data, port = tmp_path / "data", _free_port()
    env = dict(os.environ, PYTHONPATH=str(SRC))
    proc = subprocess.Popen([sys.executable, "-m", "lanboard.server", "--host", "127.0.0.1",
                             "--port", str(port), "--data", str(data), "--no-discovery"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                _, w = await asyncio.open_connection("127.0.0.1", port)
                w.close()
                break
            except OSError:
                await asyncio.sleep(0.05)
        a = BotClient("127.0.0.1", port, "alice", "K", create=True)
        await a.connect()
        for i in range(C.AUTOSAVE_EVERY_OPS + 13):         # past one autosave, plus a tail
            a.submit(stroke(a, i))
        await a.settle()
        want, seq = a.board.hash(), a.board.seq
    finally:
        proc.send_signal(signal.SIGKILL)                   # no chance to flush anything
        proc.wait(5)
    srv = Server("127.0.0.1", 0, str(data), discovery=False)
    await srv.start()
    try:
        assert srv.rooms["K"].seq == seq and srv.rooms["K"].board.hash() == want
    finally:
        await srv.stop()


@pytest.mark.parametrize("flush", [True, False], ids=["clean-stop", "crash-stop"])
async def test_history_after_restart_starts_at_seq_1(tmp_path, flush):
    """The time-lapse covers the whole session even after the server restarted."""
    data = str(tmp_path / "data")
    srv = Server("127.0.0.1", 0, data, discovery=False)
    await srv.start()
    a = await make(srv, "alice", create=True)
    n = 2 * C.AUTOSAVE_EVERY_OPS + 7                      # past two compactions, plus a tail
    for i in range(n):
        a.submit(stroke(a, i))
        if i % 100 == 99:
            await a.settle()                              # stay under the ops/s rate limit
    await a.settle()
    room = srv.rooms["R"]
    for _ in range(100):                                  # let the autosave task finish
        if not room._saving and room.archived_seq >= C.AUTOSAVE_EVERY_OPS:
            break
        await asyncio.sleep(0.02)
    assert room.archived_seq >= C.AUTOSAVE_EVERY_OPS      # ops really left ops.jsonl
    assert len(room.store.load_ops()) < n
    want = room.board.hash()
    await a.close()
    await srv.stop(flush=flush)                           # flush=False: no final snapshot

    srv2 = Server("127.0.0.1", 0, data, discovery=False)
    await srv2.start()
    try:
        b = await make(srv2, "bob")
        b.send(MsgType.HISTORY_REQ, {})
        h = await b.wait_for(MsgType.HISTORY)
        assert h["base_seq"] == 0 and h["base"]["objects"] == []
        assert [m["seq"] for m in h["ops"]] == list(range(1, n + 1))
        replay = Board.from_snapshot({"seq": h["base_seq"], "objects": h["base"]["objects"]})
        for m in h["ops"]:
            replay.apply(m["op"], m["seq"])
        assert replay.hash() == want == srv2.rooms["R"].board.hash() == b.board.hash()
        await b.close()
    finally:
        await srv2.stop()


async def test_late_join_when_snapshot_exceeds_max_frame(server, monkeypatch):
    """A board bigger than MAX_FRAME still reaches late joiners (SNAPSHOT parts),
    and so do a long resume (several OP_BATCH frames) and the HISTORY."""
    from lanboard.common import protocol
    monkeypatch.setattr(protocol, "MAX_FRAME", 64 * 1024)  # every encode() now refuses > 64 KiB
    a = await make(server, "alice", create=True)
    c = await make(server, "carol")
    c.drop()                                               # will resume after a long gap
    pts = [v for i in range(150) for v in (i * 7, 1000 + i * 3)]
    for batch in range(12):                               # 12 batches x 25 strokes of ~1 KB
        a.submit({"k": "batch", "ops": [
            {"k": "add", "obj": {"id": a.new_id(), "type": "stroke",
                                 "style": {"color": "#123456", "width": 3}, "geom": {"pts": pts}}}
            for _ in range(25)]})
    await a.settle()
    room = server.rooms["R"]
    assert not a.rejects and len(room.board) == 300
    whole = len(json.dumps(room.board.snapshot()))
    assert whole > protocol.MAX_FRAME                      # one SNAPSHOT frame could not carry it
    b = await make(server, "bob")                          # late join: SNAPSHOT in parts
    assert len(room.snapshot_frames()) > 1
    assert b.board.seq == room.seq and b.board.hash() == room.board.hash()
    await c.connect()                                      # resume: 12 big ops, split by bytes
    assert c.joined["resumed"] is True and c.snapshots == 1  # healed by OP_BATCH frames
    await c.wait_until(lambda: c.board.seq == room.seq)
    assert c.board.hash() == room.board.hash()
    b.send(MsgType.HISTORY_REQ, {})
    h = await b.wait_for(MsgType.HISTORY)
    assert len(h["ops"]) == room.seq
    await a.close(); await b.close(); await c.close()


async def test_oversized_op_rejected_connection_stays_up(server):
    a = await make(server, "alice", create=True)
    # ~5 MiB: fits in one frame (MAX_FRAME 8 MiB) but exceeds MAX_OP_BYTES (4 MiB)
    objs = [{"id": f"t{i}", "type": "text", "geom": {"x": i, "y": 0, "text": "x" * 180}}
            for i in range(25_000)]
    op_id = a.submit({"k": "replace", "objects": objs})
    rej = await a.wait_for(MsgType.OP_REJECT)
    assert rej["op_id"] == op_id and rej["code"] == Err.TOO_LARGE and "too large" in rej["msg"]
    assert server.rooms["R"].seq == 0
    a.submit(stroke(a))                                    # same connection still works
    await a.settle()
    assert a.connected and server.rooms["R"].seq == 1
    await a.close()


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

async def test_udp_discovery(tmp_path):
    srv = Server("127.0.0.1", 0, str(tmp_path), "disco", discovery=True, discovery_port=0)
    await srv.start()
    try:
        assert srv._discovery is not None
        a = await make(srv, "alice", create=True)
        loop = asyncio.get_running_loop()
        got: asyncio.Future = loop.create_future()

        class Probe(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                if not got.done():
                    got.set_result(json.loads(data))

        tr, _ = await loop.create_datagram_endpoint(Probe, local_addr=("127.0.0.1", 0))
        tr.sendto(b'{"magic": "nope"}', ("127.0.0.1", srv.discovery_port))
        tr.sendto(json.dumps({"magic": C.DISCOVERY_MAGIC}).encode(), ("127.0.0.1", srv.discovery_port))
        info = await asyncio.wait_for(got, 2)
        tr.close()
        assert info["magic"] == C.DISCOVERY_MAGIC and info["port"] == srv.port
        assert info["name"] == "disco" and info["proto"] == C.PROTO_VERSION
        assert info["host"] == "127.0.0.1" and info["server_id"] == srv.server_id
        assert info["rooms"] == [{"code": "R", "name": "R", "users": 1,
                                  "protected": False, "locked": False}]
        await a.close()
    finally:
        await srv.stop()
