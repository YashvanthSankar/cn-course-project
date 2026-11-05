"""Rooms and sessions: the authoritative state of every shared whiteboard.

Concurrency model
-----------------
The whole server runs on **one asyncio event loop thread**. Every mutation of
a :class:`Room` (board, op log, members, chat, settings) happens in a plain
synchronous method called from that thread, so two clients can never race on
room state: the event loop runs one callback at a time and a synchronous
method cannot be interrupted half way. That is why there are **no locks**
here. The few ``await`` points (disk I/O through ``asyncio.to_thread``) are
placed *after* the in-memory state has been captured or changed, and code
that resumes after an ``await`` re-checks anything that may have changed
meanwhile (for example, whether the client is still connected).

Ordering and consistency
------------------------
The room is the **sequencer** of a total-order broadcast. Each accepted op
gets ``seq = previous seq + 1`` and is applied to the server board, logged and
then broadcast as ``OP`` to every member, *including the sender* (the echo is
the acknowledgement). TCP delivers each client's copy in order, and every
replica runs the same deterministic ``Board.apply``, so all replicas that have
applied the ops up to ``seq`` hold identical boards. The ``HASH`` checkpoints
let clients verify that claim at run time.

Sessions vs connections
-----------------------
A :class:`Session` is the *logical* membership (user id, role, resume token,
op-id dedupe table). It outlives the TCP connection for ``SESSION_TTL``
seconds so a client whose Wi-Fi dropped can reconnect, present its
``resume_token`` and continue as the same user, receiving only the ops it
missed. A connection object is attached to at most one session at a time.

Durable history
---------------
``history_base`` + ``history`` is the time-lapse: a base board (empty, seq 0,
for a new room) plus every op after it, contiguous in seq. It is also the
resume log. It survives restarts: the snapshot task archives the ops it
compacts out of ``ops.jsonl`` into ``history.jsonl`` (see persistence.py), and
:meth:`Room.load` rebuilds both. It is bounded by ``HISTORY_MAX_OPS`` and
``HISTORY_MAX_BYTES``; past that the oldest ops are folded into the base, so
the history then starts at ``base_seq`` instead of 0.

The connection object is duck-typed (see ``server.ClientConnection``); the room
only uses ``send``, ``send_frame``, ``close``, and the attributes ``session``,
``room``, ``uploads``, ``rtt``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import logging
import math
import re
import time
from collections import OrderedDict, deque
from typing import Any, Callable

from lanboard.common import auth
from lanboard.common import config as C
from lanboard.common.messages import Err, MsgType, Reason
from lanboard.common.model import Board, InvalidOp, validate_op
from lanboard.common.protocol import (HEADER_SIZE, ProtocolError, encode, encode_parts,
                                     pack_ranges, part_budget)

from .persistence import RoomStore

log = logging.getLogger("lanboard.room")

DEDUPE_MAX = 4096          # remembered op_ids per session (bounded LRU)
CATCHUP_MAX_OPS = 5000     # longer gaps are healed with a SNAPSHOT instead of OP_BATCH
OP_BATCH_CHUNK = 500       # at most this many ops per OP_BATCH frame (and <= PART_BYTES)
JOIN_CHAT_TAIL = 200       # chat messages included in JOINED
LIVE_PER_SEC = 240         # ephemeral frames per client per second (excess silently dropped)
CHAT_PER_SEC = 5           # chat messages per client per second (burst 2x)
MAX_UPLOADS = 4            # concurrent partial blob uploads per connection
SYSTEM_COLOR = "#808080"

_HEX = re.compile(r"^[0-9a-f]+$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class BadMessage(ValueError):
    """A payload violates the wire contract: reply ERROR BAD_MESSAGE and close."""


# ---------------------------------------------------------------------------
# payload helpers
# ---------------------------------------------------------------------------

def get_str(p: dict, key: str, max_len: int, *, required: bool = True) -> str | None:
    v = p.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or len(v) > max_len:
        raise BadMessage(f"{key} must be a string of at most {max_len} chars")
    return v


def get_num(p: dict, key: str, *, required: bool = True) -> float | None:
    v = p.get(key)
    if v is None and not required:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
            or abs(v) > C.MAX_COORD:
        raise BadMessage(f"{key} must be a finite number")
    return v


def get_int(p: dict, key: str, lo: int, hi: int) -> int:
    v = p.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise BadMessage(f"{key} must be an integer in [{lo}, {hi}]")
    return v


def get_pts(p: dict, key: str = "pts") -> list:
    pts = p.get(key)
    if not isinstance(pts, list) or len(pts) % 2 or len(pts) > C.MAX_STROKE_POINTS:
        raise BadMessage(f"{key} must be a flat even-length list")
    for n in pts:
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) \
                or abs(n) > C.MAX_COORD:
            raise BadMessage(f"{key} must contain numbers")
    return pts


def clean_text(s: str, max_len: int) -> str:
    """Strip control characters and surrounding whitespace, cap the length."""
    return _CONTROL.sub("", s).strip()[:max_len]


def clean_password(v: Any) -> tuple[str, str] | None:
    """Validate a ``{salt, verifier}`` dict (hex strings). ``None`` = no password."""
    if v is None:
        return None
    if not isinstance(v, dict):
        raise BadMessage("password must be {salt, verifier} or null")
    salt, ver = v.get("salt"), v.get("verifier")
    for x in (salt, ver):
        if not isinstance(x, str) or not 16 <= len(x) <= 256 or len(x) % 2 or not _HEX.match(x):
            raise BadMessage("salt and verifier must be hex strings")
    return salt, ver


class TokenBucket:
    """Classic token-bucket rate limiter.

    The bucket holds up to ``burst`` tokens and refills at ``rate`` tokens per
    second. Each action takes one token; when the bucket is empty the action
    is refused. This allows short bursts (a fast scribble) while capping the
    sustained rate, which protects the room from a buggy or hostile client.
    """

    __slots__ = ("rate", "burst", "tokens", "stamp")

    def __init__(self, rate: float, burst: float | None = None) -> None:
        self.rate = float(rate)
        self.burst = float(burst if burst is not None else rate)
        self.tokens = self.burst
        self.stamp = time.monotonic()

    def take(self, n: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


class Session:
    """A user's membership of a room, independent of any particular socket."""

    def __init__(self, user_id: str, username: str, color: str, role: str,
                 resume_token: str, order: int) -> None:
        self.user_id = user_id
        self.username = username
        self.color = color
        self.role = role
        self.resume_token = resume_token
        self.order = order                      # join order; used for owner promotion
        self.conn: Any = None                   # current ClientConnection, None while offline
        self.hand = False
        self.disconnected_at: float | None = time.monotonic()
        self.rtt: float | None = None           # client-reported RTT (ms), for the dashboard
        # op_id -> OpMsg for ops already accepted from this user (idempotent resend)
        self.accepted: OrderedDict[str, dict] = OrderedDict()
        self.op_bucket = TokenBucket(C.OPS_PER_SEC_LIMIT)
        self.live_bucket = TokenBucket(LIVE_PER_SEC)
        self.chat_bucket = TokenBucket(CHAT_PER_SEC, 2 * CHAT_PER_SEC)

    @property
    def online(self) -> bool:
        return self.conn is not None

    def participant(self) -> dict:
        return {"user_id": self.user_id, "username": self.username, "color": self.color,
                "role": self.role, "online": self.online, "hand": self.hand}

    def remember(self, op_id: str, msg: dict) -> None:
        self.accepted[op_id] = msg
        if len(self.accepted) > DEDUPE_MAX:
            self.accepted.popitem(last=False)

    def to_meta(self) -> dict:
        return {"user_id": self.user_id, "username": self.username, "color": self.color,
                "role": self.role, "resume_token": self.resume_token, "order": self.order}


class Room:
    """One shared whiteboard: board + op log + members + chat + settings."""

    def __init__(self, code: str, name: str, store: RoomStore, *,
                 password: tuple[str, str] | None = None) -> None:
        self.code = code
        self.name = name
        self.store = store
        self.salt, self.verifier = password if password else (None, None)
        self.locked = False
        self.editors_can_clear = False
        self.owner_id: str | None = None
        self.presenter: str | None = None
        self.created = time.time()

        self.board = Board()
        # history = every OpMsg after history_base.seq, contiguous in seq:
        # history[i]["seq"] == history_base.seq + 1 + i
        self.history_base = Board()
        self.history: list[dict] = []
        self.history_sizes: list[int] = []      # JSON bytes of each history entry (byte cap)
        self.history_bytes = 0
        self.archived_seq = 0                   # last seq already in history.jsonl
        self._rewrite_history = True            # history files must be rewritten from memory
        self._history_busy: set[str] = set()    # user_ids with a HISTORY being built

        self.sessions: dict[str, Session] = {}
        self.by_token: dict[str, Session] = {}
        self._order = 0

        self.chat: deque[dict] = deque(maxlen=C.CHAT_HISTORY_MAX)
        self.chat_seq = 0

        # persistence bookkeeping
        self.ops_since_snapshot = 0
        self.last_snapshot = time.monotonic()
        self.dirty = False                      # room.json needs rewriting
        self._saving = False
        self._meta_writing = False
        self._tasks: set[asyncio.Task] = set()
        self._snap_frames: tuple[int, list[bytes]] | None = None

        self._handlers: dict[int, Callable[[Session, dict], Any]] = {
            MsgType.OP_SUBMIT: self.on_op_submit,
            MsgType.RESYNC: self.on_resync,
            MsgType.CURSOR: self.on_cursor,
            MsgType.STROKE_LIVE: self.on_stroke_live,
            MsgType.STROKE_END: self.on_stroke_end,
            MsgType.LASER: self.on_laser,
            MsgType.VIEWPORT: self.on_viewport,
            MsgType.SIGNAL: self.on_signal,
            MsgType.CHAT_SEND: self.on_chat_send,
            MsgType.SET_ROLE: self.on_set_role,
            MsgType.KICK: self.on_kick,
            MsgType.ROOM_SETTINGS: self.on_room_settings,
            MsgType.SAVE: self.on_save,
            MsgType.LIST_SAVES: self.on_list_saves,
            MsgType.LOAD: self.on_load,
            MsgType.HISTORY_REQ: self.on_history_req,
            MsgType.BLOB_PUT: self.on_blob_put,
            MsgType.BLOB_GET: self.on_blob_get,
        }

    # ------------------------------------------------------------------ queries
    @property
    def seq(self) -> int:
        return self.board.seq

    @property
    def protected(self) -> bool:
        return self.verifier is not None

    @property
    def base_seq(self) -> int:
        return self.history_base.seq

    def online_sessions(self) -> list[Session]:
        return [s for s in self.sessions.values() if s.conn is not None]

    def info(self) -> dict:
        """RoomInfo as defined in messages.py."""
        return {"code": self.code, "name": self.name, "locked": self.locked,
                "protected": self.protected, "editors_can_clear": self.editors_can_clear,
                "owner_id": self.owner_id, "presenter": self.presenter}

    def summary(self) -> dict:
        """Entry for ROOMS and discovery replies."""
        return {"code": self.code, "name": self.name, "users": len(self.online_sessions()),
                "protected": self.protected, "locked": self.locked}

    def participants(self) -> list[dict]:
        return [s.participant() for s in sorted(self.sessions.values(), key=lambda s: s.order)]

    def is_owner(self, s: Session) -> bool:
        return s.role == "owner" and self.owner_id == s.user_id

    # ------------------------------------------------------------------ sending
    def broadcast(self, msg_type: int, payload: dict, exclude: Session | None = None) -> None:
        """Send one message to every online member (optionally except one).

        The frame is encoded **once** and the same bytes object is queued for
        every recipient. ``send_frame`` never blocks: it uses ``put_nowait`` on
        the recipient's bounded queue, so a slow receiver cannot stall the room
        (it is disconnected as ``lagging`` instead).
        """
        self.broadcast_frame(encode(msg_type, payload), exclude)

    def broadcast_frame(self, frame: bytes, exclude: Session | None = None) -> None:
        for s in list(self.sessions.values()):
            if s.conn is not None and s is not exclude:
                s.conn.send_frame(frame)

    def _send(self, s: Session, msg_type: int, payload: dict) -> None:
        if s.conn is not None:
            s.conn.send(msg_type, payload)

    def _error(self, s: Session, code: str, msg: str, **extra: Any) -> None:
        self._send(s, MsgType.ERROR, {"code": code, "msg": msg, **extra})

    def _broadcast_user(self, s: Session) -> None:
        self.broadcast(MsgType.USER_UPDATE, {"participant": s.participant()})

    def _broadcast_room(self) -> None:
        self.broadcast(MsgType.ROOM_UPDATE, {"room": self.info()})

    # ------------------------------------------------------------------ chat
    def _add_chat(self, msg: dict) -> None:
        self.chat.append(msg)
        try:
            self.store.append_chat(msg)
        except OSError as exc:
            log.error("[%s] cannot append to chat.jsonl: %s", self.code, exc)
        self.broadcast(MsgType.CHAT, msg)

    def system_chat(self, text: str) -> None:
        """Post a server-generated notice (``kind: "system"``, ``user_id: None``)."""
        self.chat_seq += 1
        self._add_chat({"id": self.chat_seq, "user_id": None, "username": "LANBoard",
                        "color": SYSTEM_COLOR, "ts": round(time.time(), 3), "text": text,
                        "kind": "system"})

    # ------------------------------------------------------------------ membership
    def unique_username(self, wanted: str, exclude: Session | None = None) -> str:
        """Return ``wanted``, or ``wanted (2)``, ``(3)``... if taken in this room."""
        taken = {s.username for s in self.sessions.values() if s is not exclude}
        if wanted not in taken:
            return wanted
        n = 2
        while True:
            suffix = f" ({n})"
            cand = wanted[:max(1, C.MAX_USERNAME - len(suffix))] + suffix
            if cand not in taken:
                return cand
            n += 1

    def create_session(self, user_id: str, color: str, username: str, role: str) -> Session:
        self._order += 1
        s = Session(user_id, self.unique_username(username), color, role, auth.new_token(),
                    self._order)
        self.sessions[user_id] = s
        self.by_token[s.resume_token] = s
        if role == "owner":
            self.owner_id = user_id
        self.dirty = True
        return s

    def admit(self, conn: Any, s: Session, *, resumed: bool, last_seq: Any = None) -> None:
        """Attach ``conn`` to ``s`` and bring the client up to date.

        Sends JOINED, then SNAPSHOT or OP_BATCH, then tells everybody else.
        Everything happens synchronously, without an ``await``, so no other
        op can be sequenced between the moment the client is registered and
        the moment its catch-up is queued: the client sees a gap-free stream.
        """
        s.conn = conn
        s.disconnected_at = None
        conn.session, conn.room = s, self
        conn.send(MsgType.JOINED, {
            "user_id": s.user_id, "username": s.username, "color": s.color, "role": s.role,
            "resume_token": s.resume_token, "resumed": resumed, "room": self.info(),
            "participants": self.participants(),
            "chat": list(self.chat)[-JOIN_CHAT_TAIL:], "server_time": time.time(),
        })
        if resumed:
            self.send_catch_up(s, last_seq)
        else:
            self.send_snapshot(s)
        self.broadcast(MsgType.USER_JOINED, {"participant": s.participant()}, exclude=s)
        self.system_chat(f"{s.username} {'reconnected' if resumed else 'joined'}")
        self.dirty = True
        log.info("[%s] %s %s as %s (%s)", self.code, s.username,
                 "resumed" if resumed else "joined", s.user_id, s.role)

    def detach(self, s: Session, reason: str) -> None:
        """The socket went away; the session stays resumable for SESSION_TTL."""
        if s.conn is None:
            return
        s.conn = None
        s.disconnected_at = time.monotonic()
        self.broadcast(MsgType.USER_LEFT, {"user_id": s.user_id, "reason": reason})
        self.system_chat(f"{s.username} disconnected ({reason})")
        log.info("[%s] %s disconnected (%s)", self.code, s.username, reason)

    def remove_session(self, s: Session, reason: str) -> None:
        """Forget a session for good (LEAVE, KICK or TTL expiry)."""
        if self.sessions.pop(s.user_id, None) is None:
            return
        self.by_token.pop(s.resume_token, None)
        s.conn = None
        self.broadcast(MsgType.USER_LEFT, {"user_id": s.user_id, "reason": reason})
        if reason == Reason.LEFT:
            self.system_chat(f"{s.username} left")
        log.info("[%s] %s removed (%s)", self.code, s.username, reason)
        room_changed = False
        if self.presenter == s.user_id:
            self.presenter = None
            room_changed = True
        if self.owner_id == s.user_id:
            self._promote_owner()               # broadcasts ROOM_UPDATE itself
        elif room_changed:
            self._broadcast_room()
        self.dirty = True

    def _promote_owner(self) -> None:
        """Hand ownership to the earliest online editor, else anyone online."""
        online = sorted(self.online_sessions(), key=lambda s: s.order)
        editors = [s for s in online if s.role == "editor"]
        pick = (editors or online or [None])[0]
        self.owner_id = pick.user_id if pick else None
        if pick is not None:
            pick.role = "owner"
            self._broadcast_user(pick)
            self.system_chat(f"{pick.username} is now the owner")
        self._broadcast_room()
        self.dirty = True

    def expire_sessions(self, now: float) -> None:
        for s in list(self.sessions.values()):
            if s.conn is None and s.disconnected_at is not None \
                    and now - s.disconnected_at > C.SESSION_TTL:
                self.remove_session(s, Reason.EXPIRED)

    # ------------------------------------------------------------------ catch-up
    def snapshot_frames(self) -> list[bytes]:
        """Encoded SNAPSHOT for the current seq (cached: many late joiners, one encode).

        A board bigger than ``PART_BYTES`` becomes several SNAPSHOT parts
        (``part``/``parts``/``xid``, see messages.py) that the client
        reassembles before applying, so a board of any size fits through
        ``MAX_FRAME``-sized frames. All parts are queued in one synchronous
        call, so no OP can be sequenced in between them.
        """
        if self._snap_frames is None or self._snap_frames[0] != self.seq:
            board = {"seq": self.seq, "objects": list(self.board.objects.values())}
            self._snap_frames = (self.seq, encode_parts(MsgType.SNAPSHOT, {
                "seq": self.seq, "board": board, "hash": self.board.hash()}))
        return self._snap_frames[1]

    def send_snapshot(self, s: Session) -> None:
        if s.conn is None:
            return
        try:
            frames = self.snapshot_frames()
        except ProtocolError:                   # one object larger than a frame: cannot happen
            log.error("[%s] board object too large for a SNAPSHOT frame", self.code)
            self._error(s, Err.TOO_LARGE, "board snapshot cannot be encoded")
            return
        for f in frames:
            s.conn.send_frame(f)

    def send_catch_up(self, s: Session, last_seq: Any) -> None:
        """Send the ops after ``last_seq`` (OP_BATCH) or, if that tail is gone, a SNAPSHOT."""
        if s.conn is None:
            return
        ok = (isinstance(last_seq, int) and not isinstance(last_seq, bool)
              and self.base_seq <= last_seq <= self.seq
              and self.seq - last_seq <= CATCHUP_MAX_OPS)
        if not ok:
            self.send_snapshot(s)
            return
        start = last_seq - self.base_seq                    # history[i].seq == base_seq + 1 + i
        tail = self.history[start:]
        # split by count *and* bytes: 500 big strokes could exceed one frame
        ranges = pack_ranges(self.history_sizes[start:], part_budget(), OP_BATCH_CHUNK)
        try:
            frames = [encode(MsgType.OP_BATCH, {"ops": tail[a:b]}) for a, b in ranges]
        except ProtocolError:
            self.send_snapshot(s)
            return
        for f in frames:
            s.conn.send_frame(f)

    # ------------------------------------------------------------------ dispatch
    async def handle(self, s: Session, msg_type: int, p: dict) -> None:
        """Route one message from a joined client to its handler."""
        handler = self._handlers.get(msg_type)
        if handler is None:
            raise BadMessage(f"unexpected message type 0x{msg_type:02X}")
        result = handler(s, p)
        if asyncio.iscoroutine(result):
            await result

    # ------------------------------------------------------------------ board ops
    def check_permission(self, s: Session, op: Any) -> tuple[str, str] | None:
        """Server-side access control for an op (step 2 of the pipeline)."""
        if s.role == "viewer":
            return Err.FORBIDDEN, "viewers cannot edit the board"
        owner = self.is_owner(s)
        if self.locked and not owner:
            return Err.LOCKED, "the board is locked by the owner"
        k = op.get("k") if isinstance(op, dict) else None
        if k == "clear" and not (owner or self.editors_can_clear):
            return Err.FORBIDDEN, "only the owner can clear the board"
        if k == "replace" and not owner:
            return Err.FORBIDDEN, "only the owner can restore a version"
        return None

    def _reject(self, s: Session, op_id: str, code: str, msg: str) -> None:
        self._send(s, MsgType.OP_REJECT, {"op_id": op_id, "code": code, "msg": msg})

    def on_op_submit(self, s: Session, p: dict) -> None:
        op_id = p.get("op_id")
        if not isinstance(op_id, str) or not 0 < len(op_id) <= 128:
            raise BadMessage("op_id must be a non-empty string")
        self.submit(s, op_id, p.get("op"))

    def submit(self, s: Session, op_id: str, op: Any) -> dict | None:
        """The op pipeline. Returns the committed OpMsg, or None if rejected/duplicate.

        Steps (see messages.py): joined -> permission -> rate limit -> validate
        -> dedupe -> normalise -> size check -> seq+apply -> log -> broadcast (+HASH).
        """
        # 1. joined
        if self.sessions.get(s.user_id) is not s:
            self._reject(s, op_id, Err.NOT_JOINED, "not a member of this room")
            return None
        # 2. permission
        denied = self.check_permission(s, op)
        if denied:
            self._reject(s, op_id, *denied)
            return None
        # 3. rate limit
        if not s.op_bucket.take():
            self._reject(s, op_id, Err.RATE_LIMITED, f"more than {C.OPS_PER_SEC_LIMIT} ops/s")
            return None
        # 4. validate
        try:
            validate_op(op)
            self._check_capacity(op)
        except InvalidOp as exc:
            self._reject(s, op_id, Err.INVALID_OP, str(exc))
            return None
        # 5. dedupe: an op re-sent after a reconnect is acknowledged again, never re-applied
        prev = s.accepted.get(op_id)
        if prev is not None:
            self._send(s, MsgType.OP, prev)
            return None
        # 6. normalise (clear -> delete of live ids, add.owner forced to the sender)
        kind = op["k"]
        op = self.board.normalise(op, s.user_id)
        # 7. size check: encode the OP frame once, before committing. An op whose
        #    frame is absurdly large is refused here with a clear error; the
        #    connection stays up and no seq is used. (Frames above MAX_FRAME never
        #    get this far: the decoder treats them as a broken stream.)
        seq = self.seq + 1
        msg = {"seq": seq, "user_id": s.user_id, "op_id": op_id,
               "ts": round(time.time(), 3), "op": op}
        try:
            frame = encode(MsgType.OP, msg)
        except ProtocolError:
            frame = None
        if frame is None or len(frame) > C.MAX_OP_BYTES:
            what = f"{len(frame):,} bytes" if frame is not None else "more than a frame"
            self._reject(s, op_id, Err.TOO_LARGE,
                         f"edit too large ({what}; the limit is {C.MAX_OP_BYTES:,} bytes)")
            return None
        # 8. sequence + apply (apply copies what it keeps, so ``msg`` stays as encoded)
        self.board.apply(op, seq)
        # 9. log: disk (crash recovery) and memory (resume/timeline) before anyone sees it.
        #    The log line is the frame's JSON payload, so nothing is encoded twice.
        raw = frame[HEADER_SIZE + 1:]
        try:
            size = self.store.append_op(msg, raw)
        except OSError as exc:                  # disk full etc.: keep serving, shout in the log
            size = len(raw) + 1
            log.error("[%s] cannot append to ops.jsonl: %s (seq %d not durable until the "
                      "next snapshot)", self.code, exc, seq)
        self._add_history(msg, size)
        s.remember(op_id, msg)
        self.ops_since_snapshot += 1
        # 10. broadcast to everyone, sender included (its echo is the ack)
        self.broadcast_frame(frame)
        if kind == "replace" or seq % C.HASH_EVERY_OPS == 0:
            self.broadcast(MsgType.HASH, {"seq": seq, "hash": self.board.hash()})
        if kind == "clear":
            self.system_chat(f"{s.username} cleared the board")
        if self.ops_since_snapshot >= C.AUTOSAVE_EVERY_OPS:
            self.schedule_snapshot()
        return msg

    def _check_capacity(self, op: dict) -> None:
        adds = 1 if op["k"] == "add" else (
            sum(1 for o in op["ops"] if o.get("k") == "add") if op["k"] == "batch" else 0)
        if adds and len(self.board.objects) + adds > C.MAX_OBJECTS:
            raise InvalidOp("board is full")

    def _add_history(self, msg: dict, size: int) -> None:
        self.history.append(msg)
        self.history_sizes.append(size)
        self.history_bytes += size
        self._trim_history()

    def _trim_history(self) -> None:
        """Enforce the history caps by folding the oldest ops into the base board.

        Caps: ``HISTORY_MAX_OPS`` ops and ``HISTORY_MAX_BYTES`` of op JSON per
        room, in memory and on disk alike. When either is exceeded, about 10 %
        more than the excess is dropped at once, so the (expensive) rewrite of
        the history files happens rarely: amortised O(1) per op. Afterwards
        ``base_seq`` > 0 tells clients that the time-lapse starts later than
        the room's first op.
        """
        max_ops, max_bytes = C.HISTORY_MAX_OPS, C.HISTORY_MAX_BYTES
        n = len(self.history)
        if n <= max_ops and self.history_bytes <= max_bytes:
            return
        keep_ops = max_ops - max(1, max_ops // 10)
        keep_bytes = max_bytes - max_bytes // 10
        drop, freed = 0, 0
        while drop < n and (n - drop > keep_ops or self.history_bytes - freed > keep_bytes):
            freed += self.history_sizes[drop]
            drop += 1
        for m in self.history[:drop]:
            self.history_base.apply(m["op"], m["seq"])
        del self.history[:drop]
        del self.history_sizes[:drop]
        self.history_bytes -= freed
        self._rewrite_history = True            # the files still start at the old base

    def on_resync(self, s: Session, p: dict) -> None:
        self.send_catch_up(s, p.get("last_seq"))

    def history_payload(self) -> dict:
        """The (logical) HISTORY reply: base board + every op after it.

        ``base_seq == 0`` means the history is complete since the room was
        created; ``base_seq > 0`` means older ops were dropped by the caps
        and the time-lapse starts from the base board at that seq.
        """
        base = self.history_base.snapshot()     # deep copy: safe to encode in a thread
        return {"base_seq": base["seq"], "base": base, "ops": list(self.history)}

    async def on_history_req(self, s: Session, p: dict) -> None:
        """Send the whole time-lapse, split into HISTORY parts when it is large.

        Encoding tens of MB of ops would stall every room for a moment, so it
        runs in a worker thread. That is safe: the payload is a deep copy of
        the base plus a list of OpMsg dicts, which are never mutated after
        they are committed. One request per user at a time (no DoS by spam).
        """
        conn = s.conn
        if conn is None or s.user_id in self._history_busy:
            return
        self._history_busy.add(s.user_id)
        try:
            payload = self.history_payload()
            sizes = {("ops",): self.history_sizes[:len(payload["ops"])]}
            try:
                frames = await asyncio.to_thread(encode_parts, MsgType.HISTORY, payload, sizes)
            except ProtocolError:
                self._error(s, Err.TOO_LARGE, "history cannot be encoded")
                return
            if s.conn is conn:                  # still the same connection after the await
                for f in frames:
                    conn.send_frame(f)
        finally:
            self._history_busy.discard(s.user_id)

    # ------------------------------------------------------------------ ephemeral relay
    def _may_draw(self, s: Session) -> bool:
        return s.role != "viewer" and (not self.locked or self.is_owner(s))

    def _relay(self, s: Session, msg_type: int, payload: dict) -> None:
        """Forward an ephemeral message to everyone else (never logged, no seq)."""
        if s.live_bucket.take():
            payload["user_id"] = s.user_id
            self.broadcast(msg_type, payload, exclude=s)

    def on_cursor(self, s: Session, p: dict) -> None:
        self._relay(s, MsgType.CURSOR, {"x": get_num(p, "x"), "y": get_num(p, "y")})

    def on_stroke_live(self, s: Session, p: dict) -> None:
        out = {"id": get_str(p, "id", 64), "color": get_str(p, "color", 32),
               "width": get_num(p, "width"), "pts": get_pts(p)}
        tool = get_str(p, "tool", 24, required=False)
        if tool is not None:
            out["tool"] = tool
        if self._may_draw(s):
            self._relay(s, MsgType.STROKE_LIVE, out)

    def on_stroke_end(self, s: Session, p: dict) -> None:
        out = {"id": get_str(p, "id", 64)}
        if self._may_draw(s):
            self._relay(s, MsgType.STROKE_END, out)

    def on_laser(self, s: Session, p: dict) -> None:
        out = {"pts": get_pts(p)}
        if s.role != "viewer":
            self._relay(s, MsgType.LASER, out)

    def on_viewport(self, s: Session, p: dict) -> None:
        out = {k: get_num(p, k) for k in ("x", "y", "w", "h")}
        if self.is_owner(s) or self.presenter == s.user_id:
            self._relay(s, MsgType.VIEWPORT, out)

    def on_signal(self, s: Session, p: dict) -> None:
        kind = get_str(p, "kind", 64)
        if kind in ("hand_up", "hand_down"):
            hand = kind == "hand_up"
            if s.hand != hand:
                s.hand = hand
                self._broadcast_user(s)
        else:
            self._relay(s, MsgType.SIGNAL, {"kind": kind})

    # ------------------------------------------------------------------ chat
    def on_chat_send(self, s: Session, p: dict) -> None:
        raw = get_str(p, "text", 4 * C.MAX_CHAT_LEN)
        cmid = get_str(p, "cmid", 64, required=False)
        text = raw.strip()[:C.MAX_CHAT_LEN]
        if not text:
            return
        if not s.chat_bucket.take():
            self._error(s, Err.RATE_LIMITED, "slow down")
            return
        self.chat_seq += 1
        msg = {"id": self.chat_seq, "user_id": s.user_id, "username": s.username,
               "color": s.color, "ts": round(time.time(), 3), "text": text, "kind": "user"}
        if cmid is not None:
            msg["cmid"] = cmid
        self._add_chat(msg)

    # ------------------------------------------------------------------ admin
    def _require_owner(self, s: Session) -> bool:
        if self.is_owner(s):
            return True
        self._error(s, Err.FORBIDDEN, "only the room owner can do that")
        return False

    def on_set_role(self, s: Session, p: dict) -> None:
        uid, role = get_str(p, "user_id", 32), get_str(p, "role", 16)
        if not self._require_owner(s):
            return
        if role not in C.ROLES:
            self._error(s, Err.BAD_MESSAGE, f"unknown role {role!r}")
            return
        t = self.sessions.get(uid)
        if t is None:
            self._error(s, Err.NOT_FOUND, "no such user")
            return
        if t is s:
            if role != "owner":
                self._error(s, Err.FORBIDDEN, "transfer ownership to someone else first")
            return
        if role == "owner":                     # ownership transfer
            s.role = "editor"
            t.role = "owner"
            self.owner_id = t.user_id
            self._broadcast_user(s)
            self._broadcast_user(t)
            self._broadcast_room()
            self.system_chat(f"{s.username} made {t.username} the owner")
        elif t.role != role:
            t.role = role
            self._broadcast_user(t)
            self.system_chat(f"{s.username} made {t.username} {'an' if role == 'editor' else 'a'} {role}")
        self.dirty = True

    def on_kick(self, s: Session, p: dict) -> None:
        uid = get_str(p, "user_id", 32)
        if not self._require_owner(s):
            return
        t = self.sessions.get(uid)
        if t is None:
            self._error(s, Err.NOT_FOUND, "no such user")
            return
        if t is s:
            self._error(s, Err.FORBIDDEN, "you cannot kick yourself")
            return
        conn = t.conn
        if conn is not None:
            # detach first so closing the socket does not broadcast "disconnected"
            t.conn = None
            conn.session = conn.room = None
            conn.send(MsgType.ERROR, {"code": Err.KICKED, "msg": f"removed by {s.username}"})
            conn.close(Reason.KICKED, flush=True)
        self.remove_session(t, Reason.KICKED)      # invalidates the resume token too
        self.system_chat(f"{s.username} removed {t.username}")

    def on_room_settings(self, s: Session, p: dict) -> None:
        if not self._require_owner(s):
            return
        notes: list[str] = []
        if "locked" in p:
            if not isinstance(p["locked"], bool):
                raise BadMessage("locked must be a bool")
            if p["locked"] != self.locked:
                self.locked = p["locked"]
                notes.append(f"{s.username} {'locked' if self.locked else 'unlocked'} the board")
        if "name" in p:
            name = clean_text(get_str(p, "name", 256), 64)
            if name and name != self.name:
                self.name = name
                notes.append(f"{s.username} renamed the room to {name!r}")
        if "editors_can_clear" in p:
            if not isinstance(p["editors_can_clear"], bool):
                raise BadMessage("editors_can_clear must be a bool")
            self.editors_can_clear = p["editors_can_clear"]
        if "password" in p:
            pw = clean_password(p["password"])
            was = self.protected
            self.salt, self.verifier = pw if pw else (None, None)
            if pw or was:
                notes.append(f"{s.username} {'set a' if pw else 'removed the'} room password")
        if "presenter" in p:
            pres = p["presenter"]
            if pres is not None and pres not in self.sessions:
                self._error(s, Err.NOT_FOUND, "presenter is not in this room")
                return
            self.presenter = pres
        self._broadcast_room()
        for n in notes:
            self.system_chat(n)
        self.dirty = True

    # ------------------------------------------------------------------ saves
    async def on_save(self, s: Session, p: dict) -> None:
        name = clean_text(get_str(p, "name", 256), 64)
        if not name:
            raise BadMessage("save name is empty")
        if s.role == "viewer":
            self._error(s, Err.FORBIDDEN, "viewers cannot save")
            return
        # capture a consistent copy on the loop thread, write it in a worker thread
        objects = copy.deepcopy([o for o in self.board.objects.values() if not o["deleted"]])
        await asyncio.to_thread(self.store.write_save, name, self.seq, objects)
        saves = await asyncio.to_thread(self.store.list_saves)
        self._send(s, MsgType.SAVES, {"saves": saves})
        self.system_chat(f"{s.username} saved the board as {name!r}")

    async def on_list_saves(self, s: Session, p: dict) -> None:
        saves = await asyncio.to_thread(self.store.list_saves)
        self._send(s, MsgType.SAVES, {"saves": saves})

    async def on_load(self, s: Session, p: dict) -> None:
        name = get_str(p, "name", 256)
        if not self._require_owner(s):
            return
        data = await asyncio.to_thread(self.store.read_save, name)
        if data is None:
            self._error(s, Err.NOT_FOUND, f"no save named {name!r}")
            return
        # the load goes through the normal pipeline as a ``replace`` op
        msg = self.submit(s, f"load:{auth.new_nonce()}",
                          {"k": "replace", "objects": data.get("objects", [])})
        if msg is not None:
            self.system_chat(f"{s.username} loaded {name!r}")

    # ------------------------------------------------------------------ blobs
    async def on_blob_put(self, s: Session, p: dict) -> None:
        sha = get_str(p, "hash", 64)
        mime = get_str(p, "mime", 100)
        size = get_int(p, "size", 0, 2 ** 40)
        # chunks are at least 1 KiB (clients use BLOB_CHUNK), so the count is bounded by the
        # size: otherwise a client could announce a million empty chunks and fill our memory
        count = get_int(p, "count", 1, max(1, -(-size // 1024)))
        index = get_int(p, "index", 0, count - 1)
        data = get_str(p, "data", 2 * C.BLOB_CHUNK + 1024)
        conn = s.conn
        if not _HEX64.match(sha):
            raise BadMessage("hash must be lowercase sha256 hex")
        if s.role == "viewer":
            self._error(s, Err.FORBIDDEN, "viewers cannot upload images", hash=sha)
            return
        if size > C.MAX_BLOB:
            self._error(s, Err.TOO_LARGE, f"image larger than {C.MAX_BLOB} bytes", hash=sha)
            return
        if conn is None:
            return
        if sha not in conn.uploads and await asyncio.to_thread(self.store.has_blob, sha):
            if index == 0 and s.conn is conn:
                self._send(s, MsgType.BLOB_STORED, {"hash": sha})   # already have it: dedupe
            return
        if s.conn is not conn:
            return
        up = conn.uploads.get(sha)
        if up is None:
            if len(conn.uploads) >= MAX_UPLOADS:
                self._error(s, Err.TOO_LARGE, "too many concurrent uploads", hash=sha)
                return
            up = conn.uploads[sha] = {"mime": mime, "size": size, "count": count,
                                      "chunks": {}, "received": 0}
        if up["size"] != size or up["count"] != count:
            del conn.uploads[sha]
            self._error(s, Err.BAD_MESSAGE, "inconsistent chunk header", hash=sha)
            return
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise BadMessage("blob data is not valid base64") from None
        if index not in up["chunks"]:
            up["chunks"][index] = raw
            up["received"] += len(raw)
        if up["received"] > size:
            del conn.uploads[sha]
            self._error(s, Err.TOO_LARGE, "more data than announced", hash=sha)
            return
        if len(up["chunks"]) < count:
            return
        # all chunks are in: assemble, verify integrity end-to-end, store
        del conn.uploads[sha]
        blob = b"".join(up["chunks"][i] for i in range(count))
        if len(blob) != size or hashlib.sha256(blob).hexdigest() != sha:
            self._error(s, Err.BAD_MESSAGE, "blob failed sha256 verification", hash=sha)
            return
        await asyncio.to_thread(self.store.write_blob, sha, blob, up["mime"])
        self._send(s, MsgType.BLOB_STORED, {"hash": sha})

    async def on_blob_get(self, s: Session, p: dict) -> None:
        sha = get_str(p, "hash", 64)
        if not _HEX64.match(sha):
            raise BadMessage("hash must be lowercase sha256 hex")
        found = await asyncio.to_thread(self.store.read_blob, sha)
        if found is None:
            self._error(s, Err.NOT_FOUND, "unknown blob", hash=sha)
            return
        blob, mime = found
        count = max(1, math.ceil(len(blob) / C.BLOB_CHUNK))
        for i in range(count):
            if s.conn is None:
                return
            chunk = blob[i * C.BLOB_CHUNK:(i + 1) * C.BLOB_CHUNK]
            self._send(s, MsgType.BLOB_DATA, {"hash": sha, "mime": mime, "size": len(blob),
                                              "index": i, "count": count,
                                              "data": base64.b64encode(chunk).decode("ascii")})

    # ------------------------------------------------------------------ persistence
    def to_meta(self) -> dict:
        return {"code": self.code, "name": self.name, "created": self.created,
                "salt": self.salt, "verifier": self.verifier, "locked": self.locked,
                "editors_can_clear": self.editors_can_clear, "owner_id": self.owner_id,
                "presenter": self.presenter, "chat_seq": self.chat_seq, "order": self._order,
                "seq": self.seq,
                "sessions": [s.to_meta() for s in sorted(self.sessions.values(),
                                                          key=lambda s: s.order)]}

    @classmethod
    def load(cls, store: RoomStore) -> "Room | None":
        """Rebuild a room from disk: meta, board, durable history, sessions, chat."""
        meta = store.load_meta()
        if not meta or not isinstance(meta.get("code"), str):
            return None
        pw = (meta["salt"], meta["verifier"]) if meta.get("verifier") else None
        room = cls(meta["code"], meta.get("name") or meta["code"], store, password=pw)
        room.created = meta.get("created", room.created)
        room.locked = bool(meta.get("locked"))
        room.editors_can_clear = bool(meta.get("editors_can_clear"))
        room.owner_id = meta.get("owner_id")
        room.presenter = meta.get("presenter")
        room._order = int(meta.get("order", 0))

        replayed = room._load_board(store)
        room.ops_since_snapshot = replayed

        # sessions survive restarts, so clients can resume as the same user/role
        for sm in meta.get("sessions", []):
            try:
                s = Session(sm["user_id"], sm["username"], sm["color"], sm["role"],
                            sm["resume_token"], int(sm.get("order", 0)))
            except (KeyError, TypeError, ValueError):
                continue
            room.sessions[s.user_id] = s
            room.by_token[s.resume_token] = s
        # rebuild the op-id dedupe tables from the replayed log
        for msg in room.history:
            s = room.sessions.get(msg.get("user_id"))
            if s is not None and isinstance(msg.get("op_id"), str):
                s.remember(msg["op_id"], msg)
        if room.owner_id not in room.sessions:
            room.owner_id = None

        room.chat.extend(store.load_chat(C.CHAT_HISTORY_MAX))
        room.chat_seq = max([int(meta.get("chat_seq", 0))] +
                            [m.get("id", 0) for m in room.chat if isinstance(m.get("id"), int)])
        log.info("[%s] loaded: seq %d (%d ops replayed), %d objects, %d sessions, "
                 "history from seq %d (%d ops)", room.code, room.seq, replayed, len(room.board),
                 len(room.sessions), room.base_seq, len(room.history))
        return room

    def _extend_history(self, records: list[tuple[dict, int]]) -> int | None:
        """Append the records that continue the history; return the seq of a gap, if any.

        Records at or below the current end are skipped: an op may be in two
        files after a crash in the middle of a compaction.
        """
        last = self.base_seq + len(self.history)
        for msg, size in records:
            seq = msg.get("seq")
            if not isinstance(seq, int) or seq <= last or not isinstance(msg.get("op"), dict):
                continue
            if seq != last + 1:
                return seq
            self.history.append(msg)
            self.history_sizes.append(size)
            self.history_bytes += size
            last = seq
        return None

    def _load_board(self, store: RoomStore) -> int:
        """Rebuild board + history from the files; return the ops not yet in snapshot.json.

        1. History: ``history_base.json`` (absent: an empty board at seq 0),
           then every op of ``history.jsonl`` and ``ops.jsonl`` that continues
           the sequence. A gap ends the history.
        2. Board: the snapshot, if it lies inside the history, plus the history
           ops after it (the fast path); else base + all history ops (snapshot
           lost, or older than a trimmed base).
        3. If the history cannot reach the snapshot (a data directory written
           before the archive existed, or a damaged history.jsonl), snapshot +
           ops.jsonl are the truth, exactly as before: the history restarts at
           the snapshot and the files are rewritten at the next compaction.
        """
        snap = store.load_snapshot()
        snap_seq = int(snap.get("seq", 0)) if snap else 0
        base_snap, archived = store.load_history()
        logged = store.load_ops_sized()
        self.history_base = Board.from_snapshot(base_snap) if base_snap else Board()
        self._rewrite_history = base_snap is None           # create the files at the next checkpoint
        gap = self._extend_history(archived)
        self.archived_seq = self.base_seq + len(self.history)
        if gap is None:
            gap = self._extend_history(logged)
        end = self.base_seq + len(self.history)

        if snap and self.base_seq <= snap_seq <= end:
            self.board = Board.from_snapshot(snap)          # fast path: replay only the tail
            start = snap_seq - self.base_seq
        elif snap is None or snap_seq < self.base_seq:
            self.board = self.history_base.copy()
            start = 0
        else:
            if base_snap is not None or archived:
                log.warning("[%s] history ends at seq %d (gap at %s) before the snapshot (%d); "
                            "the time-lapse restarts there", self.code, end, gap, snap_seq)
            self.board = Board.from_snapshot(snap)
            self.history_base = self.board.copy()
            self.history, self.history_sizes, self.history_bytes = [], [], 0
            gap = self._extend_history(logged)
            self.archived_seq = snap_seq
            self._rewrite_history = True
            start = 0
        if gap is not None:
            log.warning("[%s] gap in the op log at seq %s (have %d); stopped replay there",
                        self.code, gap, self.base_seq + len(self.history))
        for m in self.history[start:]:
            self.board.apply(m["op"], m["seq"])
        self._trim_history()                                # the caps may have been lowered
        return self.board.seq - snap_seq

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def schedule_snapshot(self) -> None:
        if self._saving or self.ops_since_snapshot <= 0:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:                    # no loop (synchronous unit test): housekeeping
            return                              # will pick it up later
        self._saving = True                     # set now so a second trigger is a no-op
        self._spawn(self.write_snapshot())

    async def write_snapshot(self) -> None:
        """Compaction: archive the logged ops, snapshot the board, truncate the log.

        The board and the ops to archive are captured on the loop thread (a
        consistent point-in-time image at ``seq``), written by a worker thread
        (history first, then the snapshot), and only then is ``ops.jsonl``
        truncated to the ops sequenced *while* the write was in flight. A
        crash at any point leaves either the old snapshot + full log or the
        new snapshot + a log whose older entries are skipped on replay; the
        history never loses an op because it is written before the log
        forgets it.
        """
        self._saving = True
        try:
            snap = self.board.snapshot()
            seq = snap["seq"]
            base_seq = self.base_seq
            upto = max(0, seq - base_seq)                   # history[:upto] has seq <= snapshot
            if self._rewrite_history:
                # trimmed (or first checkpoint): base + every op up to the snapshot
                self._rewrite_history = False               # a trim during the await sets it again
                base = self.history_base.snapshot()
                archive = self.history[:upto]
            else:
                base = None                                 # just append the new ops
                archive = self.history[max(0, self.archived_seq - base_seq):upto]
            await asyncio.to_thread(self.store.write_checkpoint, snap, archive, base)
            self.archived_seq = max(seq, base_seq)
            if self.base_seq > seq:
                # The history was trimmed past the snapshot while it was being
                # written, so memory no longer holds every op after ``seq``. The
                # log on disk still does: keep it until the next checkpoint.
                log.debug("[%s] snapshot @%d written, log kept (history moved on)", self.code, seq)
                return
            tail = self.history[seq - self.base_seq:]
            self.store.rewrite_ops(tail)
            self.ops_since_snapshot = len(tail)
            self.last_snapshot = time.monotonic()
            log.debug("[%s] snapshot @%d written, %d ops archived, %d kept in log",
                      self.code, seq, len(archive), len(tail))
        except Exception:
            self._rewrite_history = True                    # state of the files is unknown
            log.exception("[%s] snapshot failed", self.code)
        finally:
            self._saving = False

    async def write_meta(self) -> None:
        self._meta_writing = True
        try:
            self.dirty = False
            await asyncio.to_thread(self.store.save_meta, self.to_meta())
        except Exception:
            self.dirty = True
            log.exception("[%s] writing room.json failed", self.code)
        finally:
            self._meta_writing = False

    def housekeep(self, now: float) -> None:
        """Periodic work: expire sessions, autosave, flush metadata."""
        self.expire_sessions(now)
        if self.ops_since_snapshot >= C.AUTOSAVE_EVERY_OPS or (
                self.ops_since_snapshot and now - self.last_snapshot >= C.AUTOSAVE_INTERVAL):
            self.schedule_snapshot()
        if self.dirty and not self._meta_writing:
            self._meta_writing = True
            self._spawn(self.write_meta())

    async def close(self, flush: bool = True) -> None:
        """Wait for background writes; with ``flush``, write final snapshot + meta."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        if flush:
            if self.ops_since_snapshot:
                await self.write_snapshot()
            await self.write_meta()
        self.store.close()
