"""SessionController: the client's replica of the shared board and its sync rules.

This is where the "ordering and reliability" requirements live on the client.

Two boards
----------
* ``board``: the **confirmed** replica. It only ever contains ops the server
  has ordered, applied strictly in ``seq`` order. Every client's ``board`` is
  identical at the same seq (verified with the server's HASH checkpoints).
* ``view``: ``board`` plus our own **pending** ops (sent but not yet echoed).
  This is what the canvas renders, so our own strokes appear instantly
  (optimistic local apply) without waiting a round trip.

Rules for incoming OP frames (``_on_op``)
-----------------------------------------
* ``seq <= board.seq``      duplicate (e.g. resent, or chaos-duplicated). Drop it.
* ``seq == board.seq + 1``  in order. Apply it, then drain any buffered successors.
* ``seq >  board.seq + 1``  a gap: something is missing or delayed. Buffer it. If
  the gap is not filled within ``GAP_TIMEOUT_MS`` we send ``RESYNC{last_seq}``
  and the server replays the missing tail (or a snapshot).
* An echoed op whose ``op_id`` is in ``pending`` is our own op coming back. The
  echo *is* the acknowledgement, so it leaves ``pending``.

Offline-first
-------------
Ops submitted while disconnected stay in ``pending`` and are resent, in order,
after every (re)join. The server de-duplicates by ``op_id``, so resending an op
it already accepted is harmless.

Large transfers
---------------
A big board's SNAPSHOT (and a long HISTORY) arrives as several parts. They are
collected by a ``PartAssembler`` and only the complete payload is applied, so
the replica jumps from the old board straight to the new one and is never
half-loaded. A catch-up of many ops arrives as several OP_BATCH frames, which
need no reassembly (each op is applied by seq as usual).

Self-healing
------------
If a HASH checkpoint does not match our confirmed board, we discard the replica
and ask for a full resync. In practice this never happens, and the UI shows a
green "in sync" badge that is backed by evidence rather than hope.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import time
from collections import OrderedDict
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from ..common import auth
from ..common import config as C
from ..common.messages import Err, MsgType
from ..common.model import Board, get_key, op_object_ids
from ..common.protocol import PartAssembler, ProtocolError
from ..common.undo import UndoManager

GAP_TIMEOUT_MS = 400        # how long to wait for a missing seq before RESYNC
MAX_REORDER = 256           # buffered out-of-order ops before forcing a RESYNC


def _differs(a: dict | None, b: dict | None) -> bool:
    """True if two object dicts render differently (``rev`` is bookkeeping only)."""
    if a is None or b is None:
        return a is not b
    keys = (a.keys() | b.keys()) - {"rev"}
    return any(a.get(k) != b.get(k) for k in keys)


class SessionController(QObject):
    # --- signals (see docs/CLIENT_API.md) ------------------------------------
    reset = Signal()
    objects_changed = Signal(object)
    participants_changed = Signal()
    chat_added = Signal(dict)
    chat_reset = Signal(list)
    room_changed = Signal(dict)
    me_changed = Signal(dict)
    cursor_moved = Signal(str, float, float)
    live_stroke = Signal(str, dict)
    live_stroke_end = Signal(str, str)
    laser = Signal(str, list)
    viewport = Signal(str, dict)
    signal_received = Signal(str, str)
    sync_status = Signal(dict)
    undo_state = Signal(bool, bool)
    history_ready = Signal(dict)
    saves_ready = Signal(list)
    blob_ready = Signal(str)
    blob_progress = Signal(str, int, int)
    notice = Signal(str, str)
    connection_state = Signal(str)

    def __init__(self, net, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.net = net
        self.board = Board()                    # confirmed replica
        self.view = Board()                     # confirmed + pending (rendered)
        self.pending: OrderedDict[str, dict] = OrderedDict()   # op_id -> local (normalised) op
        self._wire: dict[str, dict] = {}        # op_id -> op as sent on the wire
        self._reorder: dict[int, dict] = {}     # seq -> OpMsg waiting for its predecessors
        self._parts = PartAssembler()           # multi-part SNAPSHOT / HISTORY in progress
        self.undo_mgr = UndoManager()

        self.participants: dict[str, dict] = {}
        self.me: dict | None = None
        self.room: dict | None = None
        self.chat: list[dict] = []

        self._op_n = 0
        self._id_n = 0
        self._state = "offline"
        self._last_hash: tuple[int, str] | None = None     # last verified (seq, hash)
        self._hash_checks: dict[int, str] = {}              # future seq -> expected hash
        self._mismatch = False
        self._healing = False
        self._resend_queue: list[tuple[str, dict]] = []

        # blobs: content-addressed image store
        self._blobs: dict[str, bytes] = {}
        self._blob_mime: dict[str, str] = {}
        self._blob_requested: set[str] = set()
        self._blob_parts: dict[str, dict[int, bytes]] = {}
        self._blob_uploads: list[str] = []      # uploads to (re)send once joined

        # gap detection timer
        self._gap_timer = QTimer(self)
        self._gap_timer.setSingleShot(True)
        self._gap_timer.timeout.connect(self._on_gap_timeout)

        # live stroke batching: collect points, flush at ~30 Hz
        self._live_buf: dict[str, dict] = {}
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(C.LIVE_FLUSH_MS)
        self._live_timer.timeout.connect(self._flush_live)

        # cursor throttling: at most one CURSOR per CURSOR_MIN_INTERVAL_MS, latest wins
        self._cursor_last = 0.0
        self._cursor_pending: tuple[float, float] | None = None
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.timeout.connect(self._flush_cursor)

        net.last_seq_provider = lambda: self.board.seq
        net.joined.connect(self._on_joined)
        net.message.connect(self._on_message)
        net.state_changed.connect(self._on_net_state)
        net.error.connect(self._on_net_error)

    # =========================================================================
    # identity & permissions
    # =========================================================================
    @property
    def user_id(self) -> str:
        return (self.me or {}).get("user_id") or getattr(self.net, "user_id", None) or "local"

    def color_of(self, user_id: str) -> str:
        p = self.participants.get(user_id)
        if p:
            return p.get("color", "#888888")
        return C.USER_COLORS[sum(map(ord, user_id or "")) % len(C.USER_COLORS)]

    def name_of(self, user_id: str) -> str:
        p = self.participants.get(user_id)
        return p.get("username", user_id) if p else (user_id or "?")

    def is_owner(self) -> bool:
        return bool(self.me) and self.me.get("role") == "owner"

    def can_edit(self) -> bool:
        if not self.me or self.me.get("role") == "viewer":
            return False
        if self.room and self.room.get("locked") and not self.is_owner():
            return False
        return True

    def can_clear(self) -> bool:
        return self.is_owner() or (self.can_edit() and bool((self.room or {}).get("editors_can_clear")))

    # =========================================================================
    # board editing API (used by canvas tools)
    # =========================================================================
    def new_id(self) -> str:
        """Globally unique object id without a round trip.

        The server never reuses a user id (it persists its user-id counter), so
        ``user_id`` + counter is already unique. The short per-process tag (first
        4 chars of ``client_id``) is extra insurance, e.g. against a server whose
        data directory was wiped.
        """
        self._id_n += 1
        tag = (getattr(self.net, "client_id", "") or "x")[:4]
        return f"{self.user_id}-{tag}{self._id_n}"

    def next_z(self) -> int:
        return self.view.max_z() + 1

    def submit(self, op: dict, undoable: bool = True) -> str | None:
        """Optimistically apply ``op`` locally and send it to the server."""
        if not self.can_edit():
            self.notice.emit("warn", "You don't have permission to edit this board.")
            return None
        self._op_n += 1
        op_id = f"{getattr(self.net, 'client_id', 'c')}:{self._op_n}"
        local = self.view.normalise(op, self.user_id)        # e.g. clear -> delete{ids}
        self.pending[op_id] = local
        self._wire[op_id] = op
        changed = self.view.apply(local)
        if local["k"] == "replace":
            self.reset.emit()
        elif changed:
            self.objects_changed.emit(changed)
        if undoable:
            self.undo_mgr.record(local)
            self._emit_undo_state()
        if self.net.is_joined() and not self._send_op(op_id, op):
            return None
        self._emit_sync()
        return op_id

    def _send_op(self, op_id: str, op: dict) -> bool:
        """OP_SUBMIT one op. An op too big for any frame is rolled back right here.

        (Ops below MAX_FRAME but above the server's MAX_OP_BYTES come back as
        OP_REJECT TOO_LARGE and are rolled back in ``_on_reject``.)
        """
        try:
            self.net.send(MsgType.OP_SUBMIT, {"op_id": op_id, "op": op})
            return True
        except ProtocolError:
            self._on_reject({"op_id": op_id, "code": Err.TOO_LARGE,
                             "msg": "That edit is too large to send."})
            return False

    def add_object(self, obj: dict) -> None:
        obj = copy.deepcopy(obj)
        obj.setdefault("id", self.new_id())
        obj.setdefault("z", self.next_z())
        obj.setdefault("style", {})
        obj.setdefault("off", [0, 0])
        obj.setdefault("ts", round(time.time(), 1))
        obj["owner"] = self.user_id
        self.submit({"k": "add", "obj": obj})

    def _update_op(self, oid: str, changes: dict) -> dict | None:
        o = self.view.get(oid)
        if o is None or o["deleted"] or not changes:
            return None
        prev = {k: copy.deepcopy(get_key(o, k)) for k in changes}
        if all(prev[k] == v for k, v in changes.items()):
            return None                                       # nothing actually changes
        return {"k": "update", "id": oid, "set": copy.deepcopy(changes), "prev": prev}

    def update_object(self, oid: str, changes: dict) -> None:
        op = self._update_op(oid, changes)
        if op:
            self.submit(op)

    def update_many(self, changes: dict[str, dict]) -> None:
        ops = [op for oid, ch in changes.items() if (op := self._update_op(oid, ch))]
        for i in range(0, len(ops), C.MAX_BATCH_OPS):
            chunk = ops[i:i + C.MAX_BATCH_OPS]
            self.submit(chunk[0] if len(chunk) == 1 else {"k": "batch", "ops": chunk})

    def delete_objects(self, ids: list[str]) -> None:
        ids = [i for i in dict.fromkeys(ids) if self.view.is_live(i)]
        if ids:
            self.submit({"k": "delete", "ids": ids})

    def clear_board(self) -> None:
        if not self.can_clear():
            self.notice.emit("warn", "Only the room owner can clear the board.")
            return
        if len(self.view):
            self.submit({"k": "clear"})

    def undo(self) -> None:
        op = self.undo_mgr.undo(self.view)
        if op:
            self.submit(op, undoable=False)
        self._emit_undo_state()

    def redo(self) -> None:
        op = self.undo_mgr.redo(self.view)
        if op:
            self.submit(op, undoable=False)
        self._emit_undo_state()

    def _emit_undo_state(self) -> None:
        self.undo_state.emit(self.undo_mgr.can_undo, self.undo_mgr.can_redo)

    # =========================================================================
    # ephemeral presence API
    # =========================================================================
    def send_cursor(self, x: float, y: float) -> None:
        """Throttled to 20 Hz. Only the latest position matters, so intermediates are dropped."""
        self._cursor_pending = (x, y)
        now = time.monotonic() * 1000
        wait = C.CURSOR_MIN_INTERVAL_MS - (now - self._cursor_last)
        if wait <= 0:
            self._flush_cursor()
        elif not self._cursor_timer.isActive():
            self._cursor_timer.start(int(wait) + 1)

    def _flush_cursor(self) -> None:
        if self._cursor_pending and self.net.is_joined():
            x, y = self._cursor_pending
            self.net.send(MsgType.CURSOR, {"x": round(x, 1), "y": round(y, 1)})
            self._cursor_last = time.monotonic() * 1000
        self._cursor_pending = None

    def send_live(self, stroke_id: str, color: str, width: float, new_pts: list, tool: str = "pen") -> None:
        """Live stroke preview. Batched to ~30 Hz unless batching is switched off (HUD demo)."""
        if not self.net.is_joined() or not new_pts:
            return
        if not getattr(self.net, "batching_enabled", True):
            self.net.send(MsgType.STROKE_LIVE, {"id": stroke_id, "color": color, "width": width,
                                                "pts": list(new_pts), "tool": tool})
            return
        buf = self._live_buf.setdefault(stroke_id, {"id": stroke_id, "color": color, "width": width,
                                                    "pts": [], "tool": tool})
        buf["pts"].extend(new_pts)
        if not self._live_timer.isActive():
            self._live_timer.start()

    def _flush_live(self) -> None:
        for payload in self._live_buf.values():
            if payload["pts"] and self.net.is_joined():
                self.net.send(MsgType.STROKE_LIVE, payload)
        self._live_buf.clear()
        self._live_timer.stop()

    def end_live(self, stroke_id: str) -> None:
        if stroke_id in self._live_buf:
            payload = self._live_buf.pop(stroke_id)
            if payload["pts"] and self.net.is_joined():
                self.net.send(MsgType.STROKE_LIVE, payload)
        if self.net.is_joined():
            self.net.send(MsgType.STROKE_END, {"id": stroke_id})

    def send_laser(self, pts: list) -> None:
        if pts and self.net.is_joined():
            self.net.send(MsgType.LASER, {"pts": [round(p, 1) for p in pts]})

    def send_viewport(self, rect: dict) -> None:
        if self.net.is_joined():
            self.net.send(MsgType.VIEWPORT, {k: round(float(rect[k]), 1) for k in ("x", "y", "w", "h")})

    def send_signal(self, kind: str) -> None:
        if self.net.is_joined():
            self.net.send(MsgType.SIGNAL, {"kind": kind})

    # =========================================================================
    # chat, admin, saves, history
    # =========================================================================
    def send_chat(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if not self.net.is_joined():
            self.notice.emit("warn", "Offline: chat will be available after reconnecting.")
            return
        self._op_n += 1
        self.net.send(MsgType.CHAT_SEND, {"cmid": f"m{self._op_n}", "text": text[:C.MAX_CHAT_LEN]})

    def set_role(self, user_id: str, role: str) -> None:
        self.net.send(MsgType.SET_ROLE, {"user_id": user_id, "role": role})

    def kick(self, user_id: str) -> None:
        self.net.send(MsgType.KICK, {"user_id": user_id})

    def set_room(self, **settings: Any) -> None:
        """Owner settings. ``password`` is a plain string (or None to remove);
        only the scrypt verifier is sent, never the password itself."""
        payload: dict[str, Any] = {}
        for key in ("locked", "name", "editors_can_clear", "presenter"):
            if key in settings:
                payload[key] = settings[key]
        if "password" in settings:
            pw = settings["password"]
            if pw:
                salt = auth.new_salt()
                payload["password"] = {"salt": salt, "verifier": auth.make_verifier(pw, salt)}
            else:
                payload["password"] = None
        if payload:
            self.net.send(MsgType.ROOM_SETTINGS, payload)

    def save(self, name: str) -> None:
        self.net.send(MsgType.SAVE, {"name": name})

    def list_saves(self) -> None:
        self.net.send(MsgType.LIST_SAVES, {})

    def load(self, name: str) -> None:
        self.net.send(MsgType.LOAD, {"name": name})

    def request_history(self) -> None:
        self.net.send(MsgType.HISTORY_REQ, {})

    # =========================================================================
    # blobs (images / PDF pages)
    # =========================================================================
    def put_blob(self, data: bytes, mime: str) -> str:
        """Store locally and upload in chunks. Returns the sha256 content address."""
        h = hashlib.sha256(data).hexdigest()
        if len(data) > C.MAX_BLOB:
            self.notice.emit("error", "Image too large to share.")
            return h
        self._blobs[h] = data
        self._blob_mime[h] = mime
        if self.net.is_joined():
            self._upload_blob(h)
        else:
            self._blob_uploads.append(h)
        return h

    def _upload_blob(self, h: str) -> None:
        data, mime = self._blobs[h], self._blob_mime.get(h, "image/png")
        count = max(1, -(-len(data) // C.BLOB_CHUNK))
        for i in range(count):
            part = data[i * C.BLOB_CHUNK:(i + 1) * C.BLOB_CHUNK]
            self.net.send(MsgType.BLOB_PUT, {"hash": h, "mime": mime, "size": len(data), "index": i,
                                             "count": count, "data": base64.b64encode(part).decode()})
            self.blob_progress.emit(h, i + 1, count)

    def blob_bytes(self, h: str) -> bytes | None:
        data = self._blobs.get(h)
        if data is None and h not in self._blob_requested and self.net.is_joined():
            self._blob_requested.add(h)
            self.net.send(MsgType.BLOB_GET, {"hash": h})
        return data

    def _on_blob_data(self, p: dict) -> None:
        h, idx, count = p.get("hash"), int(p.get("index", 0)), int(p.get("count", 1))
        parts = self._blob_parts.setdefault(h, {})
        parts[idx] = base64.b64decode(p.get("data", ""))
        self.blob_progress.emit(h, len(parts), count)
        if len(parts) == count:
            data = b"".join(parts[i] for i in range(count))
            del self._blob_parts[h]
            if hashlib.sha256(data).hexdigest() != h:          # integrity check
                self._blob_requested.discard(h)
                self.notice.emit("warn", "Image transfer corrupted, retrying...")
                return
            self._blobs[h] = data
            self._blob_mime[h] = p.get("mime", "image/png")
            self.blob_ready.emit(h)

    # =========================================================================
    # network events
    # =========================================================================
    def _on_net_state(self, state: str) -> None:
        self._state = state
        self.connection_state.emit(state)
        if state != "joined":
            self._gap_timer.stop()
        self._emit_sync()

    def _on_net_error(self, code: str, msg: str) -> None:
        if code not in (Err.AUTH_REQUIRED,):
            self.notice.emit("error", f"{code}: {msg}" if msg else code)

    def _on_joined(self, p: dict) -> None:
        self.me = {"user_id": p.get("user_id"), "username": p.get("username"), "color": p.get("color"),
                   "role": p.get("role", "editor"), "online": True, "hand": False}
        self.room = p.get("room") or {}
        self.participants = {q["user_id"]: q for q in p.get("participants", [])}
        self.participants.setdefault(self.me["user_id"], self.me)
        self.me = self.participants[self.me["user_id"]]
        self.chat = list(p.get("chat", []))
        self._reorder.clear()
        self._parts.clear()                     # transfer ids restart on every connection
        self._healing = False
        self._mismatch = False
        self.me_changed.emit(self.me)
        self.room_changed.emit(self.room)
        self.participants_changed.emit()
        self.chat_reset.emit(self.chat)
        # offline-first: replay everything not yet acknowledged, in original order.
        # The server drops op_ids it already accepted and re-echoes them.
        self._resend_queue = list(self._wire.items())
        self._pump_resend()
        for h in self._blob_uploads:
            self._upload_blob(h)
        self._blob_uploads.clear()
        self._blob_requested.clear()                       # re-request anything still missing
        self._emit_sync()

    RESEND_PER_TICK = 100          # stay under the server's 120 ops/s token bucket

    def _pump_resend(self) -> None:
        """Replay queued offline ops in order, paced to respect the server's rate limit."""
        batch, self._resend_queue = self._resend_queue[:self.RESEND_PER_TICK], self._resend_queue[self.RESEND_PER_TICK:]
        for op_id, op in batch:
            if op_id in self._wire and self.net.is_joined():
                self._send_op(op_id, op)
        if self._resend_queue:
            QTimer.singleShot(1000, self._pump_resend)

    def _on_message(self, t: int, p: dict) -> None:
        handler = self._HANDLERS.get(t)
        if handler is not None:
            handler(self, p)

    # --- board sync -------------------------------------------------------------
    def _on_snapshot(self, p: dict) -> None:
        p = self._parts.add(MsgType.SNAPSHOT, p)
        if p is None:
            return                              # more parts to come: keep the old board for now
        snap = p.get("board") or {}
        self.board = Board.from_snapshot({"seq": p.get("seq", snap.get("seq", 0)),
                                          "objects": snap.get("objects", [])})
        self._reorder = {s: m for s, m in self._reorder.items() if s > self.board.seq}
        self._gap_timer.stop()
        self._healing = False
        self._mismatch = False
        self._rebuild_view(full_reset=True)
        if p.get("hash"):
            self._check_hash(self.board.seq, p["hash"])
        self._drain_reorder()
        self._emit_sync()

    def _on_op_batch(self, p: dict) -> None:
        for m in p.get("ops", []):
            self._on_op(m)
        self._healing = False
        self._emit_sync()

    def _on_op(self, m: dict) -> None:
        seq = int(m.get("seq", 0))
        if seq <= self.board.seq:
            # Duplicate: already applied. But it may still be the ack of one of our own
            # ops: the server accepted it, the link died before the echo arrived, and the
            # resume was healed by a SNAPSHOT that already contains it. The server then
            # de-duplicates our resend and re-echoes the original seq: settle it here,
            # or it would stay "pending" (and be resent) forever.
            op_id = m.get("op_id")
            if op_id in self.pending:
                self.pending.pop(op_id, None)
                self._wire.pop(op_id, None)
                self._rebuild_view()
                self._emit_sync()
            return
        if seq > self.board.seq + 1:
            self._reorder[seq] = m                   # gap: hold until predecessors arrive
            if len(self._reorder) > MAX_REORDER:
                self._request_resync()
            elif not self._gap_timer.isActive():
                self._gap_timer.start(GAP_TIMEOUT_MS)
            self._emit_sync()
            return
        self._apply_confirmed(m)
        self._drain_reorder()
        self._emit_sync()

    def _drain_reorder(self) -> None:
        while (nxt := self._reorder.pop(self.board.seq + 1, None)) is not None:
            self._apply_confirmed(nxt)
        self._reorder = {s: m for s, m in self._reorder.items() if s > self.board.seq}
        if not self._reorder:
            self._gap_timer.stop()

    def _apply_confirmed(self, m: dict) -> None:
        seq, op, op_id = int(m["seq"]), m["op"], m.get("op_id")
        mine = op_id in self.pending
        if not self.pending and not mine:
            # fast path: nothing pending, so view == board. Apply to both.
            self.board.apply(op, seq)
            changed = self.view.apply(op, seq)
            if op.get("k") == "replace":
                self.reset.emit()
            elif changed:
                self.objects_changed.emit(changed)
        else:
            self.board.apply(op, seq)
            if mine:
                self.pending.pop(op_id, None)
                self._wire.pop(op_id, None)
            self._rebuild_view(full_reset=op.get("k") == "replace")
        expected = self._hash_checks.pop(seq, None)
        if expected:
            self._check_hash(seq, expected)

    def _rebuild_view(self, full_reset: bool = False) -> None:
        """view := board + pending; emit only the ids that actually changed."""
        old = self.view
        new = self.board.copy()
        for op in self.pending.values():
            new.apply(op)
        self.view = new
        if full_reset:
            self.reset.emit()
            return
        changed = {oid for oid in old.objects.keys() | new.objects.keys()
                   if _differs(old.objects.get(oid), new.objects.get(oid))}
        if changed:
            self.objects_changed.emit(changed)

    def _on_gap_timeout(self) -> None:
        if self._reorder:
            self._request_resync()

    def _request_resync(self, from_scratch: bool = False) -> None:
        """Ask the server for what we are missing (tail replay or snapshot)."""
        self._healing = True
        self._reorder.clear()
        self._gap_timer.stop()
        if self.net.is_joined():
            # last_seq -1 asks the server for a full SNAPSHOT (used to heal a divergence)
            self.net.send(MsgType.RESYNC, {"last_seq": -1 if from_scratch else self.board.seq})
        self._emit_sync()

    def _on_hash(self, p: dict) -> None:
        seq, h = int(p.get("seq", 0)), p.get("hash", "")
        if seq == self.board.seq:
            self._check_hash(seq, h)
        elif seq > self.board.seq:
            self._hash_checks[seq] = h               # verify when we get there
            for s in [s for s in self._hash_checks if s < self.board.seq]:
                del self._hash_checks[s]

    def _check_hash(self, seq: int, expected: str) -> None:
        actual = self.board.hash()
        if actual == expected:
            self._last_hash = (seq, actual)
            self._mismatch = False
        else:
            # should never happen; if it does, heal by fetching a fresh replica
            self._mismatch = True
            self.notice.emit("warn", f"Board diverged at seq {seq}; resynchronising.")
            self._request_resync(from_scratch=True)
        self._emit_sync()

    def _on_reject(self, p: dict) -> None:
        op_id = p.get("op_id")
        if op_id in self.pending:
            self.pending.pop(op_id)
            self._wire.pop(op_id, None)
            self._rebuild_view()
        code = p.get("code", "")
        msgs = {Err.FORBIDDEN: "You can't do that in this room.",
                Err.LOCKED: "The board is locked by the owner.",
                Err.RATE_LIMITED: "Slow down: too many edits per second.",
                Err.INVALID_OP: "That edit was rejected as invalid.",
                Err.TOO_LARGE: "That edit is too large to share."}
        self.notice.emit("warn", msgs.get(code, p.get("msg") or code))
        self._emit_sync()

    def _emit_sync(self) -> None:
        if self._state != "joined":
            state = "offline"
        elif self._mismatch:
            state = "mismatch"
        elif self._healing or self._reorder:
            state = "healing"
        elif self.pending:
            state = "pending"
        else:
            state = "synced"
        self.sync_status.emit({
            "state": state, "seq": self.board.seq, "pending": len(self.pending),
            "hash": self._last_hash[1] if self._last_hash else self.board.hash(),
            "verified_seq": self._last_hash[0] if self._last_hash else None,
        })

    # --- presence ---------------------------------------------------------------
    def _on_user_joined(self, p: dict) -> None:
        part = p.get("participant") or {}
        if part.get("user_id"):
            self.participants[part["user_id"]] = part
            self.participants_changed.emit()

    def _on_user_left(self, p: dict) -> None:
        uid = p.get("user_id")
        if uid in self.participants and uid != self.user_id:
            del self.participants[uid]
            self.participants_changed.emit()

    def _on_user_update(self, p: dict) -> None:
        part = p.get("participant") or {}
        uid = part.get("user_id")
        if not uid:
            return
        self.participants[uid] = part
        if uid == self.user_id:
            self.me = part
            self.me_changed.emit(part)
        self.participants_changed.emit()

    def _on_room_update(self, p: dict) -> None:
        self.room = p.get("room") or self.room
        self.room_changed.emit(self.room)
        if self.me:
            self.me_changed.emit(self.me)           # permissions may have changed

    def _on_chat(self, p: dict) -> None:
        self.chat.append(p)
        if len(self.chat) > C.CHAT_HISTORY_MAX:
            del self.chat[0]
        self.chat_added.emit(p)

    def _on_error(self, p: dict) -> None:
        self.notice.emit("error", p.get("msg") or p.get("code", "error"))

    def _on_history(self, p: dict) -> None:
        """Re-emit HISTORY once complete. The payload is always the merged one:
        ``{base_seq, base:{seq, objects}, ops}`` (``base_seq > 0``: older ops were trimmed)."""
        p = self._parts.add(MsgType.HISTORY, p)
        if p is not None:
            self.history_ready.emit(p)

    _HANDLERS = {
        MsgType.SNAPSHOT: _on_snapshot,
        MsgType.OP_BATCH: _on_op_batch,
        MsgType.OP: _on_op,
        MsgType.OP_REJECT: _on_reject,
        MsgType.HASH: _on_hash,
        MsgType.USER_JOINED: _on_user_joined,
        MsgType.USER_LEFT: _on_user_left,
        MsgType.USER_UPDATE: _on_user_update,
        MsgType.ROOM_UPDATE: _on_room_update,
        MsgType.CHAT: _on_chat,
        MsgType.ERROR: _on_error,
        MsgType.CURSOR: lambda s, p: s.cursor_moved.emit(p.get("user_id", ""), float(p.get("x", 0)), float(p.get("y", 0))),
        MsgType.STROKE_LIVE: lambda s, p: s.live_stroke.emit(p.get("user_id", ""), p),
        MsgType.STROKE_END: lambda s, p: s.live_stroke_end.emit(p.get("user_id", ""), p.get("id", "")),
        MsgType.LASER: lambda s, p: s.laser.emit(p.get("user_id", ""), list(p.get("pts", []))),
        MsgType.VIEWPORT: lambda s, p: s.viewport.emit(p.get("user_id", ""), p),
        MsgType.SIGNAL: lambda s, p: s.signal_received.emit(p.get("user_id", ""), p.get("kind", "")),
        MsgType.HISTORY: _on_history,
        MsgType.SAVES: lambda s, p: s.saves_ready.emit(list(p.get("saves", []))),
        MsgType.BLOB_DATA: _on_blob_data,
        MsgType.BLOB_STORED: lambda s, p: None,
    }
