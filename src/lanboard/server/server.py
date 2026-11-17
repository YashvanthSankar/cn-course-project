"""The LANBoard TCP server: listener, per-client connections, handshake, housekeeping.

Architecture
------------
::

    asyncio.start_server ──accept──▶ ClientConnection ──┬─ reader task: socket → FrameDecoder → dispatch
                                                        └─ writer task: Queue(SEND_QUEUE_MAX) → socket
                                       │ joined
                                       ▼
                                     Room (board, op log, members)   ◀── housekeeping task (1 Hz):
                                                                          watchdog, session TTL, autosave

* **One event loop, many sockets.** ``asyncio`` multiplexes every socket on a
  single thread with the OS readiness API (kqueue/epoll/IOCP). While one
  client's coroutine waits for bytes, the loop serves the others. Because only
  one callback runs at a time, room state needs no locks.
* **Reader / writer split.** Each connection has a reader coroutine (bytes in
  → frames → handlers) and a writer coroutine that drains a bounded
  ``asyncio.Queue`` of pre-encoded frames into the socket, ``await``-ing
  ``drain()`` for TCP flow control. Handlers and broadcasts only ever call
  ``put_nowait`` on that queue, so producing a message never waits for a
  slow network. If a client's queue overflows (it cannot keep up, e.g. a
  weak Wi-Fi link), that client alone is dropped with reason ``lagging``; it
  reconnects and resumes, and the rest of the room is unaffected.
* **Framing.** TCP is a byte stream: one ``read()`` may return half a frame or
  three frames. The shared :class:`FrameDecoder` buffers partial data and
  returns only complete length-prefixed frames.
* **Liveness.** A silent peer (pulled cable, sleeping laptop) does not close a
  TCP connection by itself; without traffic the server would never notice.
  Clients PING every ``PING_INTERVAL`` and a watchdog drops any connection
  silent for longer than ``CLIENT_TIMEOUT`` (any frame counts as activity).
* **Nagle.** ``TCP_NODELAY`` is set on every accepted socket. Our messages are
  small and latency-sensitive (cursor, live strokes); Nagle's algorithm would
  hold them back waiting to coalesce with more data.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import time
import uuid
from typing import Any

from lanboard.common import auth
from lanboard.common import config as C
from lanboard.common.messages import Err, MsgType, Reason, type_name
from lanboard.common.protocol import FrameDecoder, ProtocolError, encode

from .persistence import Storage, default_data_dir
from .room import BadMessage, Room, clean_password, clean_text

log = logging.getLogger("lanboard.server")

MAX_AUTH_FAILURES = 10
FLUSH_GRACE = 5.0
_CLOSE = None


class ClientConnection:
    """One TCP (or TLS) connection and its protocol state machine.

    States: ``hello`` (waiting for HELLO) → ``lobby`` (may LIST_ROOMS / JOIN)
    → ``joined`` (room messages are routed to :meth:`Room.handle`).
    """

    def __init__(self, server: "Server", reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter) -> None:
        self.server = server
        self.reader = reader
        self.writer = writer
        peer = writer.get_extra_info("peername") or ("?", 0)
        self.peer = f"{peer[0]}:{peer[1]}"
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(C.SEND_QUEUE_MAX)
        self.decoder = FrameDecoder()
        self.state = "hello"
        self.username = "guest"
        self.client_id = ""
        self.nonce = auth.new_nonce()
        self.auth_failures = 0
        self.session: Any = None
        self.room: Room | None = None
        self.uploads: dict[str, dict] = {}
        self.closed = False
        self.close_reason: str | None = None
        self.connected_at = time.monotonic()
        self.last_seen = time.monotonic()
        self.rtt: float | None = None
        self.bytes_out = 0
        self.frames_out = 0
        self._writer_task: asyncio.Task | None = None

    @property
    def bytes_in(self) -> int:
        return self.decoder.bytes_in

    @property
    def queue_depth(self) -> int:
        return self.queue.qsize()

    def send(self, msg_type: int, payload: dict) -> None:
        self.send_frame(encode(msg_type, payload))

    def send_frame(self, frame: bytes) -> None:
        """Queue a pre-encoded frame without blocking (safe to call from broadcasts)."""
        if self.closed:
            return
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            log.warning("%s: send queue full (%d frames), dropping as lagging",
                        self.peer, C.SEND_QUEUE_MAX)
            asyncio.get_running_loop().call_soon(self.close, Reason.LAGGING)
            self.closed = True

    def error(self, code: str, msg: str, **extra: Any) -> None:
        self.send(MsgType.ERROR, {"code": code, "msg": msg, **extra})

    async def _writer_loop(self) -> None:
        """Drain the queue into the socket, honouring TCP flow control.

        ``drain()`` suspends while the transport's buffer is above its high-water
        mark, i.e. while the kernel send buffer is full because the receiver's
        TCP window is closed. That back-pressure is what lets the bounded
        queue detect a slow client.
        """
        try:
            while True:
                frame = await self.queue.get()
                if frame is _CLOSE:
                    break
                self.writer.write(frame)
                self.bytes_out += len(frame)
                self.frames_out += 1
                await self.writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self._shutdown_transport()

    def _shutdown_transport(self) -> None:
        try:
            self.writer.close()
        except Exception:                       # pragma: no cover - best effort
            pass

    def close(self, reason: str = Reason.DISCONNECTED, *, flush: bool = False) -> None:
        """Tear the connection down (idempotent).

        The session (if any) is detached immediately so the room state is
        consistent at once; the socket itself is closed after the queued
        frames are written (``flush=True``, used after an ERROR) or right away.
        """
        if self.close_reason is not None:
            return
        self.close_reason = reason
        self.closed = True
        self.uploads.clear()
        s, room = self.session, self.room
        self.session = self.room = None
        if s is not None and room is not None and s.conn is self:
            room.detach(s, reason)
        self.server.connections.discard(self)
        if flush and self._writer_task is not None and not self._writer_task.done():
            try:
                self.queue.put_nowait(_CLOSE)
                asyncio.get_running_loop().call_later(FLUSH_GRACE, self._abort)
                return
            except asyncio.QueueFull:
                pass
        self._abort()

    def _abort(self) -> None:
        """Hard close: cancel the writer and abort the transport (unsent data is dropped)."""
        if self._writer_task is not None and not self._writer_task.done():
            self._writer_task.cancel()
        transport = self.writer.transport
        if transport is not None:
            transport.abort()

    async def run(self) -> None:
        self._writer_task = asyncio.create_task(self._writer_loop(), name=f"writer {self.peer}")
        try:
            while not self.closed:
                data = await self.reader.read(C.READ_CHUNK)
                if not data:
                    break
                self.last_seen = time.monotonic()
                for msg_type, payload in self.decoder.feed(data):
                    if self.closed:
                        break
                    await self.dispatch(msg_type, payload)
        except ProtocolError as exc:
            log.warning("%s: protocol error: %s", self.peer, exc)
            self._fail(str(exc))
        except BadMessage as exc:
            log.warning("%s: bad message: %s", self.peer, exc)
            self._fail(str(exc))
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s: internal error", self.peer)
            self._fail("internal server error")
        finally:
            self.close(Reason.DISCONNECTED)
            if self._writer_task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(self._writer_task), 5)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    self._writer_task.cancel()

    def _fail(self, msg: str) -> None:
        """Answer a protocol violation with ERROR BAD_MESSAGE, then hang up."""
        if not self.closed:
            self.error(Err.BAD_MESSAGE, msg)
        self.close(Reason.DISCONNECTED, flush=True)

    async def dispatch(self, msg_type: int, p: dict) -> None:
        if log.isEnabledFor(logging.DEBUG):
            text = str(p)
            log.debug("%s -> %s %s", self.peer, type_name(msg_type),
                      text if len(text) < 200 else text[:200] + "...")
        if msg_type == MsgType.PING:
            self.on_ping(p)
        elif msg_type == MsgType.HELLO:
            self.on_hello(p)
        elif self.state == "hello":
            raise BadMessage("send HELLO first")
        elif msg_type == MsgType.LIST_ROOMS:
            self.send(MsgType.ROOMS, {"rooms": self.server.room_summaries()})
        elif msg_type == MsgType.JOIN:
            self.on_join(p)
        elif msg_type == MsgType.LEAVE:
            self.on_leave()
        elif self.session is None or self.room is None:
            if msg_type == MsgType.OP_SUBMIT:
                self.send(MsgType.OP_REJECT, {"op_id": p.get("op_id"), "code": Err.NOT_JOINED,
                                              "msg": "join a room first"})
            else:
                self.error(Err.NOT_JOINED, "join a room first")
        else:
            await self.room.handle(self.session, msg_type, p)

    def on_ping(self, p: dict) -> None:
        rtt = p.get("rtt")
        if isinstance(rtt, (int, float)) and not isinstance(rtt, bool) and 0 <= rtt < 1e6:
            self.rtt = float(rtt)
            if self.session is not None:
                self.session.rtt = self.rtt
        self.send(MsgType.PONG, {"t": p.get("t"), "server_time": time.time()})

    def on_hello(self, p: dict) -> None:
        if self.state != "hello":
            raise BadMessage("duplicate HELLO")
        if p.get("proto") != C.PROTO_VERSION:
            log.info("%s: protocol mismatch (%r)", self.peer, p.get("proto"))
            self.error(Err.PROTO_MISMATCH, f"server speaks protocol {C.PROTO_VERSION}",)
            self.close(Reason.DISCONNECTED, flush=True)
            return
        name = p.get("username")
        name = clean_text(name, C.MAX_USERNAME) if isinstance(name, str) else ""
        self.username = name or "guest"
        cid = p.get("client_id")
        self.client_id = cid[:64] if isinstance(cid, str) else ""
        self.state = "lobby"
        self.send(MsgType.CHALLENGE, {"nonce": self.nonce, "server_id": self.server.server_id,
                                      "server_name": self.server.name, "proto": C.PROTO_VERSION})

    def _auth_error(self, code: str, msg: str, room: Room) -> None:
        self.nonce = auth.new_nonce()
        self.error(code, msg, salt=room.salt, nonce=self.nonce)

    def on_join(self, p: dict) -> None:
        if self.session is not None:
            self.error(Err.BAD_MESSAGE, "already joined; reconnect to change rooms")
            return
        code = p.get("room")
        code = clean_text(code, C.MAX_ROOM_CODE + 1) if isinstance(code, str) else ""
        if not code or len(code) > C.MAX_ROOM_CODE:
            self.error(Err.BAD_MESSAGE, f"room code must be 1..{C.MAX_ROOM_CODE} characters")
            return
        server = self.server
        room = server.rooms.get(code)

        if p.get("create"):
            if room is not None:
                self.error(Err.ROOM_EXISTS, f"room {code!r} already exists")
                return
            password = clean_password(p.get("password"))
            name = p.get("name")
            name = (clean_text(name, 64) if isinstance(name, str) else "") or code
            room = server.create_room(code, name, password)
            self._enter(room, role="owner")
            return

        if room is None:
            self.error(Err.ROOM_NOT_FOUND, f"no room {code!r} on this server")
            return
        if self.client_id and self.client_id in room.banned:
            self.error(Err.KICKED, "you were removed from this room by its owner")
            self.close(Reason.KICKED, flush=True)
            return

        token = p.get("resume_token")
        s = room.by_token.get(token) if isinstance(token, str) else None
        if s is not None:
            old = s.conn
            if old is not None and old is not self:
                s.conn = None
                old.session = old.room = None
                old.close(Reason.DISCONNECTED)
            self.state = "joined"
            room.admit(self, s, resumed=True, last_seq=p.get("last_seq"))
            return

        if room.protected:
            proof = p.get("proof")
            if proof is None:
                self._auth_error(Err.AUTH_REQUIRED, "this room needs a password", room)
                return
            ok = auth.check_proof(room.verifier, self.nonce, proof)
            if not ok:
                self.auth_failures += 1
                log.info("%s: bad password for room %s", self.peer, code)
                self._auth_error(Err.BAD_PASSWORD, "wrong password", room)
                if self.auth_failures >= MAX_AUTH_FAILURES:
                    self.close(Reason.DISCONNECTED, flush=True)
                return
            self.nonce = auth.new_nonce()
        self._enter(room, role="owner" if room.owner_id is None else "editor")

    def _enter(self, room: Room, role: str) -> None:
        user_id, color = self.server.allocate_user()
        s = room.create_session(user_id, color, self.username, role)
        self.state = "joined"
        room.admit(self, s, resumed=False)

    def on_leave(self) -> None:
        s, room = self.session, self.room
        self.session = self.room = None
        if s is not None and room is not None:
            room.remove_session(s, Reason.LEFT)
        self.close(Reason.LEFT, flush=True)


class Server:
    """Listener plus the registry of rooms and connections."""

    def __init__(self, host: str = "0.0.0.0", port: int = C.TCP_PORT,
                 data_dir: str | None = None, name: str | None = None, *,
                 discovery: bool = True, discovery_port: int | None = None,
                 ssl_context: ssl.SSLContext | None = None, max_clients: int = 512) -> None:
        self.host = host
        self.requested_port = port
        self.port = port
        self.name = name or socket.gethostname().split(".")[0] or "LANBoard"
        self.storage = Storage(data_dir or default_data_dir())
        self.discovery_enabled = discovery
        self.discovery_port = C.DISCOVERY_PORT if discovery_port is None else discovery_port
        self.ssl_context = ssl_context
        self.max_clients = max_clients
        self.rooms: dict[str, Room] = {}
        self.connections: set[ClientConnection] = set()
        self.server_id = ""
        self.started = time.time()
        self._next_user = 1
        self._color_index = 0
        self._listener: asyncio.base_events.Server | None = None
        self._discovery = None
        self._housekeeper: asyncio.Task | None = None
        self._client_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        meta = self.storage.load_server_meta()
        self.server_id = meta.get("server_id") or uuid.uuid4().hex[:12]
        self._next_user = int(meta.get("next_user", 1))
        self._color_index = int(meta.get("color_index", 0))
        for store in self.storage.iter_room_stores():
            try:
                room = Room.load(store)
            except Exception:
                log.exception("could not load room from %s", store.dir)
                continue
            if room is not None:
                self.rooms[room.code] = room
                for uid in room.sessions:
                    if uid[1:].isdigit():
                        self._next_user = max(self._next_user, int(uid[1:]) + 1)
        self._save_server_meta()

        self._listener = await asyncio.start_server(
            self._on_client, self.host, self.requested_port, ssl=self.ssl_context,
            limit=C.READ_CHUNK, reuse_address=None)
        self.port = self._listener.sockets[0].getsockname()[1]
        log.info("listening on %s:%d (%s)%s", self.host, self.port, self.name,
                 " with TLS" if self.ssl_context else "")
        if self.discovery_enabled:
            from .discovery import start_discovery
            self._discovery = await start_discovery(self, self.discovery_port)
            if self._discovery is not None:
                self.discovery_port = self._discovery.get_extra_info("sockname")[1]
        self._housekeeper = asyncio.create_task(self._housekeeping(), name="housekeeping")

    async def serve_forever(self) -> None:
        assert self._listener is not None
        async with self._listener:
            await self._listener.serve_forever()

    async def stop(self, flush: bool = True) -> None:
        """Shut down. ``flush=False`` skips the final snapshot (simulates a crash)."""
        if self._listener is not None:
            self._listener.close()
        if self._discovery is not None:
            self._discovery.close()
            self._discovery = None
        if self._housekeeper is not None:
            self._housekeeper.cancel()
            try:
                await self._housekeeper
            except asyncio.CancelledError:
                pass
        for conn in list(self.connections):
            conn.close(Reason.DISCONNECTED)
        if self._client_tasks:
            await asyncio.wait(list(self._client_tasks), timeout=5)
        for room in self.rooms.values():
            await room.close(flush=flush)
        if flush:
            self._save_server_meta()
        if self._listener is not None:
            try:
                await asyncio.wait_for(self._listener.wait_closed(), 5)
            except asyncio.TimeoutError:
                pass
            self._listener = None
        log.info("server stopped")

    def allocate_user(self) -> tuple[str, str]:
        """Next server-wide unique user id (``u<n>``) and a round-robin colour.

        The counter is persisted immediately so ids are never reused after a
        restart (object ids embed the user id, so reuse would cause clashes).
        """
        uid = f"u{self._next_user}"
        self._next_user += 1
        color = C.USER_COLORS[self._color_index % len(C.USER_COLORS)]
        self._color_index += 1
        self._save_server_meta()
        return uid, color

    def _save_server_meta(self) -> None:
        try:
            self.storage.save_server_meta({"server_id": self.server_id, "name": self.name,
                                           "next_user": self._next_user,
                                           "color_index": self._color_index})
        except OSError as exc:
            log.error("cannot write server.json: %s", exc)

    def create_room(self, code: str, name: str, password: tuple[str, str] | None) -> Room:
        store = self.storage.room_store(code)
        store.ensure()
        room = Room(code, name, store, password=password)
        store.save_meta(room.to_meta())
        self.rooms[code] = room
        log.info("room %s created (%s)%s", code, name, " [password]" if password else "")
        return room

    def room_summaries(self) -> list[dict]:
        return [r.summary() for r in sorted(self.rooms.values(), key=lambda r: r.code)]

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except OSError:
                pass
        conn = ClientConnection(self, reader, writer)
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        if len(self.connections) >= self.max_clients:
            writer.write(encode(MsgType.ERROR, {"code": Err.SERVER_FULL, "msg": "server full"}))
            try:
                await asyncio.wait_for(writer.drain(), 2)
            except Exception:
                pass
            writer.close()
            if task is not None:
                self._client_tasks.discard(task)
            return
        self.connections.add(conn)
        log.debug("%s connected", conn.peer)
        try:
            await conn.run()
        finally:
            self.connections.discard(conn)
            if task is not None:
                self._client_tasks.discard(task)
            log.debug("%s closed (%s)", conn.peer, conn.close_reason)

    async def _housekeeping(self) -> None:
        """Watchdog + session expiry + autosave, several times per CLIENT_TIMEOUT."""
        while True:
            await asyncio.sleep(max(0.05, min(1.0, C.CLIENT_TIMEOUT / 4)))
            now = time.monotonic()
            for conn in list(self.connections):
                if now - conn.last_seen > C.CLIENT_TIMEOUT:
                    log.info("%s: silent for %.1fs, dropping", conn.peer, now - conn.last_seen)
                    conn.close(Reason.TIMEOUT)
            for room in list(self.rooms.values()):
                try:
                    room.housekeep(now)
                except Exception:
                    log.exception("[%s] housekeeping failed", room.code)
