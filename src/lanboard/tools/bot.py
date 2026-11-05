"""Headless LANBoard client ("bot") and a stress-test command line tool.

``BotClient`` speaks the full client side of the protocol with plain asyncio
streams (no Qt): HELLO/CHALLENGE/JOIN with password proofs, a replica
:class:`Board` fed by SNAPSHOT / OP / OP_BATCH with **gap detection** (an OP
whose seq skips ahead is buffered and a RESYNC is sent) and **duplicate
suppression** (an OP with ``seq <= board.seq`` is ignored), pending op tracking
(an op is pending until its echo arrives), PING/RTT, and session resume.
It is used by the integration and convergence tests and by the stress tool.

Stress tool::

    python -m lanboard.tools.bot --host 127.0.0.1 --port 5050 --room LOAD \\
        --bots 8 --rate 20 --seconds 30 [--password secret]

Each bot draws random strokes/shapes and edits at ``--rate`` ops/s. Once a
second the tool prints throughput, and at the end it checks that every bot's
replica hash equals the hash of the server's board (from a fresh SNAPSHOT).
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import random
import socket
import ssl as ssl_mod
import time
import uuid
from typing import Any, Callable

from lanboard.common import auth
from lanboard.common import config as C
from lanboard.common.messages import Err, MsgType, Reason
from lanboard.common.model import Board
from lanboard.common.protocol import FrameDecoder, encode


class JoinError(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg


class BotClient:
    """A scriptable protocol client with a consistent board replica."""

    def __init__(self, host: str, port: int, username: str = "bot", room: str = "TEST", *,
                 password: str | None = None, create: bool = False, room_name: str | None = None,
                 ssl: ssl_mod.SSLContext | None = None, ping: bool = True,
                 inbox_size: int = 20000) -> None:
        self.host, self.port = host, port
        self.username = username
        self.room_code = room
        self.password = password
        self.create = create
        self.room_name = room_name
        self.ssl = ssl
        self.ping_enabled = ping
        self.client_id = uuid.uuid4().hex[:8]

        # session state
        self.user_id: str | None = None
        self.role: str | None = None
        self.resume_token: str | None = None
        self.joined: dict | None = None
        self.room: dict | None = None
        self.participants: dict[str, dict] = {}
        self.chat: list[dict] = []
        self.board = Board()
        self.pending: collections.OrderedDict[str, dict] = collections.OrderedDict()
        self.rejects: list[dict] = []
        self.errors: list[dict] = []
        self.hash_checks = 0
        self.hash_mismatches = 0
        self.kicked = False
        self.rtt: float | None = None
        self.acked = 0
        self.dup_ops = 0
        self.resyncs = 0
        self.snapshots = 0

        self._buffer: dict[int, dict] = {}       # out-of-order OPs waiting for a gap to fill
        self._resync_asked = False
        self._op_counter = 0
        self._obj_counter = 0
        self._verifier_cache: dict[str, str] = {}
        self.inbox: collections.deque[tuple[int, dict]] = collections.deque(maxlen=inbox_size)
        self._changed = asyncio.Event()
        self._synced = asyncio.Event()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tasks: list[asyncio.Task] = []
        self.connected = False
        self.frames_in = self.frames_out = self.bytes_in = self.bytes_out = 0

    # ------------------------------------------------------------------ plumbing
    def _notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def send(self, msg_type: int, payload: dict | None = None) -> bool:
        if self._writer is None or not self.connected:
            return False
        frame = encode(msg_type, payload or {})
        self._writer.write(frame)
        self.frames_out += 1
        self.bytes_out += len(frame)
        return True

    async def wait_for(self, types: int | tuple[int, ...],
                       pred: Callable[[dict], bool] | None = None,
                       timeout: float = 5.0) -> dict:
        """Return (and consume) the first inbox message of ``types`` matching ``pred``."""
        if isinstance(types, int):
            types = (types,)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for i, (t, p) in enumerate(self.inbox):
                if t in types and (pred is None or pred(p)):
                    del self.inbox[i]
                    return p
            remaining = deadline - loop.time()
            if remaining <= 0:
                names = ", ".join(MsgType(t).name for t in types)
                raise asyncio.TimeoutError(f"{self.username}: no {names} within {timeout}s")
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                pass

    async def wait_until(self, cond: Callable[[], bool], timeout: float = 5.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not cond():
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"{self.username}: condition not met in {timeout}s")
            try:
                await asyncio.wait_for(self._changed.wait(), min(remaining, 0.25))
            except asyncio.TimeoutError:
                pass

    def received(self, msg_type: int) -> list[dict]:
        """All inbox messages of one type (not consumed)."""
        return [p for t, p in self.inbox if t == msg_type]

    # ------------------------------------------------------------------ connect
    def _verifier(self, salt: str) -> str:
        if salt not in self._verifier_cache:
            self._verifier_cache[salt] = auth.make_verifier(self.password or "", salt)
        return self._verifier_cache[salt]

    async def connect(self, timeout: float = 5.0) -> dict:
        """Connect, handshake and join. Resumes if a resume token is known."""
        await self._close_transport()
        self.inbox.clear()
        self._synced.clear()
        self._buffer.clear()
        self._resync_asked = False
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=self.ssl), timeout)
        sock = self._writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connected = True
        self._tasks = [asyncio.create_task(self._read_loop())]
        self.send(MsgType.HELLO, {"proto": C.PROTO_VERSION, "app": C.APP_ID,
                                  "username": self.username, "client_id": self.client_id})
        ch = await self.wait_for(MsgType.CHALLENGE, timeout=timeout)
        join: dict[str, Any] = {"room": self.room_code, "create": self.create}
        if self.create:
            if self.room_name:
                join["name"] = self.room_name
            if self.password:
                salt = auth.new_salt()
                join["password"] = {"salt": salt, "verifier": self._verifier(salt)}
        elif self.resume_token:
            join["resume_token"] = self.resume_token
            join["last_seq"] = self.board.seq
        self.send(MsgType.JOIN, join)
        tried_proof = False
        nonce = ch["nonce"]
        while True:
            p = await self.wait_for((MsgType.JOINED, MsgType.ERROR), timeout=timeout)
            if "user_id" in p:
                break
            code = p.get("code")
            if code in (Err.AUTH_REQUIRED, Err.BAD_PASSWORD) and self.password and not tried_proof:
                nonce = p["nonce"]
                proof = auth.make_proof(self._verifier(p["salt"]), nonce)
                self.send(MsgType.JOIN, {"room": self.room_code, "create": False, "proof": proof})
                tried_proof = True
                continue
            await self._close_transport()
            raise JoinError(code, p.get("msg", ""))
        self.create = False                       # a later reconnect joins the existing room
        await asyncio.wait_for(self._synced.wait(), timeout)
        if self.ping_enabled:
            self._tasks.append(asyncio.create_task(self._ping_loop()))
        return self.joined or {}

    async def resume(self, timeout: float = 5.0) -> dict:
        """Simulate a network drop and reconnect with resume_token + last_seq."""
        self.drop()
        return await self.connect(timeout)

    def drop(self) -> None:
        """Abort the socket without LEAVE (like a pulled cable, but the peer sees RST)."""
        self.connected = False
        if self._writer is not None and self._writer.transport is not None:
            self._writer.transport.abort()

    async def close(self) -> None:
        """Leave the room politely and close the socket."""
        if self.connected:
            self.send(MsgType.LEAVE, {})
            try:
                await asyncio.wait_for(self._writer.drain(), 1)   # type: ignore[union-attr]
            except Exception:
                pass
        await self._close_transport()

    async def _close_transport(self) -> None:
        self.connected = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        if self._writer is not None:
            self._writer.close()
            try:
                await asyncio.wait_for(self._writer.wait_closed(), 1)
            except Exception:
                pass
            self._writer = None

    async def _read_loop(self) -> None:
        dec = FrameDecoder()
        reader = self._reader
        assert reader is not None
        try:
            while True:
                data = await reader.read(C.READ_CHUNK)
                if not data:
                    break
                self.bytes_in += len(data)
                for t, p in dec.feed(data):
                    self.frames_in += 1
                    self._on_message(t, p)
                    self.inbox.append((t, p))
                self._notify()
        except (ConnectionError, OSError):
            pass
        finally:
            self.connected = False
            self._notify()

    async def _ping_loop(self) -> None:
        while self.connected:
            payload: dict[str, Any] = {"t": time.monotonic()}
            if self.rtt is not None:
                payload["rtt"] = round(self.rtt, 2)
            self.send(MsgType.PING, payload)
            await asyncio.sleep(C.PING_INTERVAL)

    # ------------------------------------------------------------------ inbound
    def _on_message(self, t: int, p: dict) -> None:
        if t == MsgType.OP:
            self._on_op(p)
        elif t == MsgType.OP_BATCH:
            for m in p.get("ops", []):
                self._on_op(m)
            self._after_catch_up()
        elif t == MsgType.SNAPSHOT:
            self.snapshots += 1
            self.board = Board.from_snapshot(p["board"])
            for seq in [s for s in self._buffer if s <= self.board.seq]:
                del self._buffer[seq]
            self._drain_buffer()
            self._after_catch_up()
        elif t == MsgType.OP_REJECT:
            self.pending.pop(p.get("op_id"), None)
            self.rejects.append(p)
        elif t == MsgType.HASH:
            if p.get("seq") == self.board.seq:
                self.hash_checks += 1
                if p.get("hash") != self.board.hash():
                    self.hash_mismatches += 1
                    self.send(MsgType.RESYNC, {"last_seq": -1})   # heal: forces a SNAPSHOT
        elif t == MsgType.JOINED:
            self.joined = p
            self.user_id, self.role = p["user_id"], p["role"]
            self.resume_token = p["resume_token"]
            self.room = p["room"]
            self.participants = {x["user_id"]: x for x in p["participants"]}
            self.chat = list(p.get("chat", []))
        elif t in (MsgType.USER_JOINED, MsgType.USER_UPDATE):
            part = p["participant"]
            self.participants[part["user_id"]] = part
            if part["user_id"] == self.user_id:
                self.role = part["role"]
        elif t == MsgType.USER_LEFT:
            uid = p.get("user_id")
            if p.get("reason") in (Reason.LEFT, Reason.KICKED, Reason.EXPIRED):
                self.participants.pop(uid, None)
            elif uid in self.participants:
                self.participants[uid]["online"] = False
        elif t == MsgType.ROOM_UPDATE:
            self.room = p["room"]
        elif t == MsgType.CHAT:
            self.chat.append(p)
        elif t == MsgType.PONG:
            if isinstance(p.get("t"), (int, float)):
                self.rtt = (time.monotonic() - p["t"]) * 1000.0
        elif t == MsgType.ERROR:
            self.errors.append(p)
            if p.get("code") == Err.KICKED:
                self.kicked = True

    def _after_catch_up(self) -> None:
        """Called after SNAPSHOT / OP_BATCH: finish the join or re-ask for a remaining gap."""
        if not self._synced.is_set():
            self._synced.set()
            # Offline edits are re-sent in the same callback, before any new submit can
            # interleave, so the server sees them in the order the user made them.
            # Ops the server already accepted are deduplicated by op_id.
            for op_id, op in list(self.pending.items()):
                self.send(MsgType.OP_SUBMIT, {"op_id": op_id, "op": op})
        if self._buffer:
            self.resyncs += 1
            self._resync_asked = True
            self.send(MsgType.RESYNC, {"last_seq": self.board.seq})

    @property
    def synced(self) -> bool:
        """Joined and caught up (safe to submit)."""
        return self.connected and self._synced.is_set()

    def _on_op(self, m: dict) -> None:
        if self.pending.pop(m.get("op_id"), None) is not None:
            self.acked += 1
        seq = m["seq"]
        if seq <= self.board.seq:
            self.dup_ops += 1                     # duplicate or already in our snapshot
            return
        if seq == self.board.seq + 1:
            self.board.apply(m["op"], seq)
            self._drain_buffer()
            return
        self._buffer[seq] = m                     # gap: hold it and ask for the missing tail
        if not self._resync_asked:
            self._resync_asked = True
            self.resyncs += 1
            self.send(MsgType.RESYNC, {"last_seq": self.board.seq})

    def _drain_buffer(self) -> None:
        while self.board.seq + 1 in self._buffer:
            m = self._buffer.pop(self.board.seq + 1)
            self.board.apply(m["op"], m["seq"])
        for seq in [s for s in self._buffer if s <= self.board.seq]:
            del self._buffer[seq]
        if not self._buffer:
            self._resync_asked = False

    # ------------------------------------------------------------------ actions
    def new_id(self) -> str:
        self._obj_counter += 1
        return f"{self.user_id}-{self.client_id}-{self._obj_counter}"

    def submit(self, op: dict, op_id: str | None = None) -> str:
        """Queue an op (sent now if connected, else on the next connect)."""
        if op_id is None:
            self._op_counter += 1
            op_id = f"{self.client_id}:{self._op_counter}"
        self.pending[op_id] = op
        if self.synced:
            self.send(MsgType.OP_SUBMIT, {"op_id": op_id, "op": op})
        return op_id

    def send_chat(self, text: str) -> str:
        cmid = uuid.uuid4().hex[:10]
        self.send(MsgType.CHAT_SEND, {"cmid": cmid, "text": text})
        return cmid

    chat_send = send_chat

    async def settle(self, timeout: float = 10.0) -> None:
        """Wait until every submitted op is acknowledged or rejected."""
        await self.wait_until(lambda: not self.pending and not self._buffer, timeout)


# ---------------------------------------------------------------------------
# random workload
# ---------------------------------------------------------------------------

def random_stroke(bot: BotClient, rng: random.Random) -> dict:
    x, y = rng.randint(0, 1600), rng.randint(0, 900)
    pts = [x, y]
    for _ in range(rng.randint(2, 30)):
        x += rng.randint(-25, 25)
        y += rng.randint(-25, 25)
        pts += [x, y]
    return {"id": bot.new_id(), "type": "stroke", "z": rng.randint(0, 1000),
            "style": {"color": rng.choice(C.USER_COLORS), "width": rng.choice((2, 4, 8))},
            "geom": {"pts": pts}}


def random_op(bot: BotClient, rng: random.Random) -> dict:
    """A random but valid op against the bot's current replica."""
    live = bot.board.live_ids()
    dead = [i for i, o in bot.board.objects.items() if o["deleted"]]
    r = rng.random()
    if r < 0.45 or not live:
        return {"k": "add", "obj": random_stroke(bot, rng)}
    if r < 0.55:
        x, y = rng.randint(0, 1500), rng.randint(0, 800)
        return {"k": "add", "obj": {"id": bot.new_id(), "type": rng.choice(("rect", "ellipse")),
                                    "z": rng.randint(0, 1000), "style": {"color": "#000000"},
                                    "geom": {"x": x, "y": y, "w": rng.randint(5, 300),
                                             "h": rng.randint(5, 300)}}}
    if r < 0.75:
        oid = rng.choice(live)
        o = bot.board.get(oid) or {}
        if rng.random() < 0.5:
            new = [rng.randint(-200, 200), rng.randint(-200, 200)]
            return {"k": "update", "id": oid, "set": {"off": new}, "prev": {"off": o.get("off")}}
        return {"k": "update", "id": oid, "set": {"style.color": rng.choice(C.USER_COLORS)},
                "prev": {"style.color": o.get("style", {}).get("color")}}
    if r < 0.85:
        return {"k": "delete", "ids": rng.sample(live, min(len(live), rng.randint(1, 3)))}
    if r < 0.90 and dead:
        return {"k": "restore", "ids": [rng.choice(dead)]}
    return {"k": "batch", "ops": [{"k": "add", "obj": random_stroke(bot, rng)}
                                  for _ in range(rng.randint(2, 4))]}


# ---------------------------------------------------------------------------
# stress CLI
# ---------------------------------------------------------------------------

async def _join_or_create(bot: BotClient) -> None:
    try:
        await bot.connect()
    except JoinError as exc:
        if exc.code != Err.ROOM_NOT_FOUND:
            raise
        bot.create = True
        try:
            await bot.connect()
        except JoinError as exc2:          # somebody else created it in the meantime
            if exc2.code != Err.ROOM_EXISTS:
                raise
            bot.create = False
            await bot.connect()


async def stress(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    bots = [BotClient(args.host, args.port, f"bot{i + 1}", args.room, password=args.password)
            for i in range(args.bots)]
    await _join_or_create(bots[0])
    await asyncio.gather(*(b.connect() for b in bots[1:]))
    print(f"{len(bots)} bots joined room {args.room!r} at seq {bots[0].board.seq}")

    stop = asyncio.Event()

    async def drive(bot: BotClient) -> None:
        period = 1.0 / max(args.rate, 0.1)
        while not stop.is_set():
            if not bot.connected and not bot.kicked:
                try:
                    await bot.connect()           # resume after e.g. a lagging drop
                except Exception as exc:
                    print(f"{bot.username}: reconnect failed: {exc}")
                    await asyncio.sleep(1)
                    continue
            if len(bot.pending) < 200 and bot.role != "viewer":
                bot.submit(random_op(bot, rng))
            await asyncio.sleep(period * rng.uniform(0.5, 1.5))

    async def report() -> None:
        prev_acked = prev_in = 0
        t0 = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(1)
            acked = sum(b.acked for b in bots)
            fin = sum(b.frames_in for b in bots)
            rtts = [b.rtt for b in bots if b.rtt is not None]
            print(f"t={time.monotonic() - t0:5.1f}s  acked {acked - prev_acked:5d} ops/s  "
                  f"frames in {fin - prev_in:6d}/s  pending {sum(len(b.pending) for b in bots):4d}  "
                  f"seq {max(b.board.seq for b in bots):6d}  "
                  f"rtt {sum(rtts) / len(rtts) if rtts else 0:6.1f} ms  "
                  f"resyncs {sum(b.resyncs for b in bots)}")
            prev_acked, prev_in = acked, fin

    drivers = [asyncio.create_task(drive(b)) for b in bots]
    reporter = asyncio.create_task(report())
    t_start = time.monotonic()
    await asyncio.sleep(args.seconds)
    stop.set()
    await asyncio.gather(*drivers, reporter, return_exceptions=True)
    elapsed = time.monotonic() - t_start
    await asyncio.gather(*(b.settle(15) for b in bots), return_exceptions=True)

    # the server's truth: a fresh observer receives SNAPSHOT {seq, hash}
    checker = BotClient(args.host, args.port, "checker", args.room, password=args.password)
    await checker.connect()
    seq, server_hash = checker.board.seq, checker.board.hash()
    await checker.close()
    ok = True
    total = sum(b.acked for b in bots)
    print(f"\n{total} ops acknowledged in {elapsed:.1f}s = {total / elapsed:.1f} ops/s; "
          f"server seq {seq} hash {server_hash}")
    for b in bots:
        try:
            await b.wait_until(lambda b=b: b.board.seq >= seq, 5)
        except asyncio.TimeoutError:
            pass
        same = b.board.seq == seq and b.board.hash() == server_hash
        ok &= same
        print(f"  {b.username:<8} seq {b.board.seq:6d} hash {b.board.hash()} "
              f"{'OK' if same else 'MISMATCH'}  rejects {len(b.rejects)}  "
              f"hash checks {b.hash_checks} (mismatches {b.hash_mismatches})")
        await b.close()
    print("ALL REPLICAS CONVERGED" if ok else "DIVERGENCE DETECTED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m lanboard.tools.bot",
                                 description="LANBoard stress test: N bots drawing concurrently")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=C.TCP_PORT)
    ap.add_argument("--room", default="STRESS")
    ap.add_argument("--password", default=None)
    ap.add_argument("--bots", type=int, default=8)
    ap.add_argument("--rate", type=float, default=20.0, help="ops per second per bot")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)
    try:
        raise SystemExit(asyncio.run(stress(args)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
