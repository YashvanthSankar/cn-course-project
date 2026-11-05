"""Client transport: TCP connection, handshake, heartbeat, auto-reconnect.

``NetClient`` owns the client's single TCP connection to the LANBoard server.
It runs entirely on the Qt event loop (``QTcpSocket`` is asynchronous: every
call returns at once and progress is reported by signals), so no threads or
locks are needed and every signal is emitted on the GUI thread.

Layering
--------
::

    SessionController        message(type, payload) / send(type, payload)
    -----------------        ------------------------------------------------
    NetClient                handshake state machine, heartbeat, reconnect
      FaultInjector          (Chaos Lab) optional drop / dup / delay of inbound frames
      FrameDecoder           TCP byte stream  ->  [len u32 | type u8 | JSON] frames
    -----------------        ------------------------------------------------
    QTcpSocket               TCP, a reliable ordered byte stream (no message boundaries)

Framing: TCP delivers a byte *stream*. One ``readyRead`` can contain half a
frame or ten frames. Every chunk from ``readAll()`` goes to the incremental
``FrameDecoder``, which buffers partial data and returns only complete frames
(see ``common/protocol.py``).

Handshake state machine
-----------------------
::

      idle
        | connect_to()
        v
    connecting --------- TCP connected: send HELLO{proto, app, username, client_id}
        |
        | CHALLENGE{nonce}: send JOIN{room, create, [password{salt,verifier} | proof]}
        v
    authenticating <---+ ERROR AUTH_REQUIRED / BAD_PASSWORD {salt, nonce}:
        |    |         |   have a password not yet retried -> JOIN{proof} (once)
        |    +---------+   else emit password_required(room, wrong) and wait
        |                  for provide_password() -> JOIN{proof}
        | JOINED{user_id, resume_token, ...}: emit joined(payload)
        v
      joined --- socket lost / no data for CLIENT_TIMEOUT ---> reconnecting
        ^                                                        |  QTimer: RECONNECT_BACKOFF[n] +/-20%
        |                                                        |  then HELLO, CHALLENGE,
        +------------------- JOINED (resumed=True) --------------+  JOIN{resume_token, last_seq, create=False}
                                                                    (a failed attempt -> next backoff step)

    any state --> closed  on a fatal ERROR (PROTO_MISMATCH, ROOM_NOT_FOUND, ROOM_EXISTS,
                          KICKED, FORBIDDEN during join), a failed *first* connection
                          (CONNECT_FAILED) or close(). Nothing reconnects from closed.

During a reconnect the public state stays ``reconnecting`` for the whole
handshake, so the UI shows one steady "reconnecting" banner. An internal
phase variable follows the HELLO / JOIN steps of the current socket.

* The CHALLENGE ``nonce`` is a fresh random value per connection. For a
  password-protected room the client proves it knows the password by sending
  ``proof = HMAC-SHA256(scrypt(password, salt), nonce)``, so the password
  never crosses the wire, and the proof cannot be replayed because each nonce
  is used once (see ``common/auth.py``). When the client does not yet know the
  room's salt, the server answers the first JOIN with ``AUTH_REQUIRED{salt,
  nonce}`` and the client retries once with a proof.
* The ``client_id`` is a uuid4 that stays the same for the lifetime of this
  object, so the server can recognise the same client across reconnects.

Reliability mechanisms
----------------------
* **TCP_NODELAY** (``LowDelayOption``) turns off Nagle's algorithm. Nagle
  holds back small segments until earlier data is ACKed. That suits bulk
  transfer, but it adds up to one RTT (plus delayed-ACK, up to about 200 ms)
  to small interactive frames such as cursor moves and stroke points.
* **Heartbeat.** Every ``PING_INTERVAL`` the client sends ``PING{t, rtt}``
  with a monotonic timestamp. The server echoes ``t`` in PONG, so
  ``RTT = now - t`` without any clock synchronisation. PINGs also keep the
  server's idle timer from expiring.
* **Dead-peer detection.** When a cable is pulled, neither side gets a FIN or
  RST, so the TCP connection is "half-open" and ``read`` waits forever (TCP
  keep-alive takes hours by default). Because the server answers every PING,
  a healthy link always carries data. If *nothing* arrives for
  ``CLIENT_TIMEOUT`` the client declares the link dead, aborts the socket and
  reconnects.
* **Auto-reconnect with exponential backoff + jitter.** Delays follow
  ``RECONNECT_BACKOFF`` (0.5, 1, 2, 4, 5, 5 ... s), each multiplied by a random
  factor in [0.8, 1.2]. Backoff avoids flooding a server that is still down,
  and jitter spreads out the reconnects of many clients that lost the server
  at the same moment (the "thundering herd" problem).
* **Session resume.** The reconnect JOIN carries ``resume_token`` (proves that
  this is the same session, so it keeps the same user_id and role) and
  ``last_seq`` (the last board op applied). The server then replays only the
  missing ops (OP_BATCH), or sends a SNAPSHOT if it no longer has them.
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket

from ..common import auth
from ..common.config import (APP_ID, CLIENT_TIMEOUT, PING_INTERVAL, PROTO_VERSION,
                             RECONNECT_BACKOFF)
from ..common.messages import Err, MsgType, type_name
from ..common.protocol import HEADER_SIZE, FrameDecoder, ProtocolError, encode
from .chaos import FaultInjector
from .metrics import Metrics

# Public connection states (the values of ``state_changed``).
IDLE = "idle"
CONNECTING = "connecting"
AUTHENTICATING = "authenticating"
JOINED = "joined"
RECONNECTING = "reconnecting"
CLOSED = "closed"

# Internal handshake phase of the *current* socket. The public state cannot
# be used for this, because it stays "reconnecting" for a whole resume
# handshake.
_PH_NONE, _PH_TCP, _PH_HELLO, _PH_JOIN, _PH_JOINED = range(5)

#: Errors after which retrying cannot help: stop and tell the user.
FATAL_CODES = frozenset({Err.PROTO_MISMATCH, Err.ROOM_NOT_FOUND, Err.ROOM_EXISTS, Err.KICKED})
#: During the join handshake FORBIDDEN is fatal too (for example a banned
#: user). After joining it only means one action was refused.
FATAL_DURING_JOIN = FATAL_CODES | {Err.FORBIDDEN}
_AUTH_CODES = frozenset({Err.AUTH_REQUIRED, Err.BAD_PASSWORD})

# Precomputed type names. ``type_name`` falls back to hex for unknown types.
_NAMES = {int(m): m.name for m in MsgType}

_SOCK_OPT = QAbstractSocket.SocketOption
_SOCK_STATE = QAbstractSocket.SocketState


def _mono_ms() -> float:
    """Monotonic clock in ms. It is unaffected by wall-clock (NTP) jumps,
    which makes it the right clock for measuring intervals such as RTT."""
    return time.monotonic() * 1000.0


class _FrameSizer:
    """Follows frame boundaries in the raw byte stream alongside FrameDecoder.

    This gives the exact on-wire size of every inbound frame (length prefix +
    type byte + payload) for the inspector and metrics, without re-encoding
    the JSON. Only the 4-byte headers are examined. Payload bytes are skipped
    by counting, so the cost is O(frames), not O(bytes).
    """

    __slots__ = ("_hdr", "_remaining", "_cur")

    def __init__(self) -> None:
        self._hdr = b""          # partial length prefix (fewer than 4 bytes)
        self._remaining = 0      # body bytes still missing for the current frame
        self._cur = 0            # total size of the current frame

    def feed(self, data: bytes) -> list[int]:
        sizes: list[int] = []
        pos, n = 0, len(data)
        while pos < n:
            if self._remaining:
                take = min(self._remaining, n - pos)
                pos += take
                self._remaining -= take
                if not self._remaining:
                    sizes.append(self._cur)
            else:
                need = HEADER_SIZE - len(self._hdr)
                self._hdr += data[pos:pos + need]
                pos += min(need, n - pos)
                if len(self._hdr) == HEADER_SIZE:
                    length = int.from_bytes(self._hdr, "big")
                    self._hdr = b""
                    self._cur = HEADER_SIZE + length
                    self._remaining = length
                    if not length:                 # invalid; FrameDecoder raises anyway
                        sizes.append(self._cur)
        return sizes


class NetClient(QObject):
    """One client connection to a LANBoard server (see module docstring)."""

    # Payload-carrying signals are declared with ``object`` rather than
    # ``dict``. ``Signal(dict)`` makes PySide6 deep-convert every payload to a
    # QVariantMap and back: about 130 ms for a 5000-object SNAPSHOT, paid
    # once per receiver. ``object`` passes the same Python dict by reference
    # (zero copy). Slots that receive it still get a plain ``dict``, and
    # ``@Slot(int, dict)`` slots connect fine.
    state_changed = Signal(str)
    joined = Signal(object)            # dict: JOINED payload
    message = Signal(int, object)      # (MsgType, dict payload)
    error = Signal(str, str)
    password_required = Signal(str, bool)
    rtt_changed = Signal(float)
    frame_logged = Signal(object)      # dict: see module docstring / CLIENT_API.md
    stats = Signal(object)             # dict: see metrics.py

    #: Frame types left out of ``frame_logged`` when ``log_ephemeral`` is False.
    #: Cursors arrive at 20 Hz per peer and would flood the inspector.
    noisy_types = frozenset({int(MsgType.CURSOR)})

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # --- public attributes (CLIENT_API.md) ---------------------------
        self.client_id: str = uuid.uuid4().hex
        self.user_id: str | None = None
        self.username: str = ""
        self.room_code: str = ""
        self.resume_token: str | None = None
        self.host: str = ""
        self.port: int = 0
        self.last_seq_provider: Callable[[], int] = lambda: 0
        self.batching_enabled: bool = True
        self.log_ephemeral: bool = True
        self.last_rtt: float | None = None
        self.server_info: dict = {}        # {server_id, server_name, proto} from CHALLENGE

        # --- tunables (instance attributes so that tests can shrink them) -
        self.ping_interval: float = PING_INTERVAL
        self.client_timeout: float = CLIENT_TIMEOUT
        self.backoff: tuple[float, ...] = tuple(RECONNECT_BACKOFF)
        self.backoff_jitter: float = 0.2
        self._rng = random.Random()

        # --- helpers ------------------------------------------------------
        self.chaos = FaultInjector(self, cut_link=self.cut_link)
        self.metrics = Metrics(self)
        self.metrics.stats.connect(self.stats)          # forward signal to signal

        # --- connection state --------------------------------------------
        self._state = IDLE
        self._phase = _PH_NONE
        self._sock: QTcpSocket | None = None
        self._decoder = FrameDecoder()
        self._sizer = _FrameSizer()
        self._gen = 0                    # connection generation; stale timers check it
        self._deliver: Callable[[int, dict], None] = lambda t, p: None
        self._last_rx = 0.0              # monotonic s of the last received byte

        # --- session / auth ----------------------------------------------
        self._ever_joined = False        # True after the first JOINED: drops reconnect
        self._create = False
        self._room_name: str | None = None
        self._password: str | None = None
        self._salt: str | None = None    # room salt (ours if we created it, else from ERROR)
        self._nonce: str | None = None   # latest server nonce (CHALLENGE or auth ERROR)
        self._auth_retry_used = False
        self._awaiting_password = False
        self._handshake_error = False    # an ERROR was already shown for this attempt
        self._verifier_cache: dict[tuple[str, str], str] = {}
        self._attempt = 0                # reconnect attempt counter (index into backoff)

        # --- timers ------------------------------------------------------
        self._ping_timer = QTimer(self)
        self._ping_timer.timeout.connect(self._send_ping)
        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self._check_liveness)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._reconnect_now)

    # ================================================================ API
    @property
    def state(self) -> str:
        return self._state

    def is_joined(self) -> bool:
        return self._state == JOINED

    def connect_to(self, host: str, port: int, username: str, room: str, create: bool = False,
                   password: str | None = None, room_name: str | None = None) -> None:
        """Start a new session: TCP connect, then HELLO / CHALLENGE / JOIN.

        Any existing connection is dropped first. Progress is reported through
        ``state_changed``. The outcome is ``joined`` or ``error``.
        """
        self._stop_timers()
        self._detach_socket()
        self.host, self.port = host, int(port)
        self.username, self.room_code = username, room
        self._create, self._room_name = bool(create), room_name
        self._password = password or None
        self.user_id = self.resume_token = None
        self._ever_joined = False
        self._salt = self._nonce = None
        self._auth_retry_used = self._awaiting_password = False
        self._attempt = 0
        self._set_state(CONNECTING)
        self._open_socket()

    def provide_password(self, password: str) -> None:
        """Answer a ``password_required`` prompt with the user's password."""
        self._password = password or None
        # A password typed by the user gets exactly one try. If it is wrong,
        # the user is prompted again (wrong=True) instead of auto-retrying.
        self._auth_retry_used = True
        if not self._awaiting_password or self._state == CLOSED:
            return
        self._awaiting_password = False
        sock = self._sock
        if (sock is not None and self._phase == _PH_JOIN and self._nonce
                and sock.state() == _SOCK_STATE.ConnectedState):
            self._send_join()
        else:
            # The server gave up on us while the user was typing. Run a new
            # handshake: the room's salt is already known, so the next JOIN
            # carries the proof straight away.
            self._last_rx = time.monotonic()
            self._open_socket()

    def send(self, t: MsgType | int, payload: dict) -> bool:
        """Send one frame. Returns False (and sends nothing) unless joined.

        Raises ``ProtocolError`` if the frame exceeds ``MAX_FRAME``. That is a
        caller bug (blobs must be chunked), so we raise instead of returning
        False, which would invite pointless retries.
        """
        if self._state != JOINED or self._sock is None:
            return False
        return self._write(int(t), payload)

    def close(self) -> None:
        """Leave the room gracefully: LEAVE, flush, FIN. No auto-reconnect."""
        self._stop_timers()
        self._awaiting_password = False
        sock, self._sock = self._sock, None
        self._gen += 1                                   # discard delayed chaos frames
        if sock is not None:
            if self._phase == _PH_JOINED and sock.state() == _SOCK_STATE.ConnectedState:
                self._write_to(sock, int(MsgType.LEAVE), {})
                sock.flush()                             # push LEAVE to the kernel now
            sock.disconnected.connect(sock.deleteLater)
            # disconnectFromHost() sends a FIN once the write buffer drains
            # (graceful close). abort() would drop unsent data and send RST.
            sock.disconnectFromHost()
            if sock.state() == _SOCK_STATE.UnconnectedState:
                sock.deleteLater()
        self._phase = _PH_NONE
        self._set_state(CLOSED)

    def cut_link(self) -> None:
        """Chaos Lab: abort the socket like a pulled cable, which triggers reconnect.

        ``abort()`` closes the socket immediately (RST, unsent data discarded),
        so the server sees an abrupt disconnect and keeps the session
        resumable. This client then follows exactly the path of a real link
        failure.
        """
        if self._sock is not None:
            self._on_link_lost(self._sock, "link cut (Chaos Lab)")

    # ===================================================== socket plumbing
    def _open_socket(self) -> None:
        """Create a fresh QTcpSocket and start connecting.

        Each attempt gets a new socket, decoder and generation number, so
        bytes, signals or delayed chaos frames from a previous connection
        can never leak into the new one.
        """
        self._detach_socket()
        self._gen += 1
        gen = self._gen
        sock = QTcpSocket(self)
        # Each lambda captures its own socket. A handler checks
        # ``sock is self._sock`` and ignores signals from replaced sockets.
        sock.connected.connect(lambda: self._on_connected(sock))
        sock.readyRead.connect(lambda: self._on_ready_read(sock))
        sock.disconnected.connect(lambda: self._on_link_lost(sock, "connection closed by server"))
        # errorString() is read only if this socket is still current: calling it on a
        # socket that is being destroyed (Qt emits signals from the destructor) crashes.
        sock.errorOccurred.connect(
            lambda _err: self._on_link_lost(sock, sock.errorString()) if sock is self._sock else None)
        self._sock = sock
        self._decoder = FrameDecoder()
        self._sizer = _FrameSizer()
        self._deliver = self._make_deliver(gen)
        self._phase = _PH_TCP
        self._nonce = None
        self._handshake_error = False
        self._last_rx = time.monotonic()
        # The watchdog also bounds the TCP connect itself: an unreachable host
        # can otherwise stay in SYN-SENT for over a minute.
        self._watchdog.start(max(50, int(self.client_timeout * 1000 / 4)))
        sock.connectToHost(self.host, self.port)

    def _detach_socket(self) -> None:
        """Abort and forget the current socket (no-op if there is none)."""
        sock, self._sock = self._sock, None
        self._phase = _PH_NONE
        self._ping_timer.stop()
        self._watchdog.stop()
        if sock is not None:
            # Silence the old socket first: abort() and the later C++ destructor both emit
            # disconnected/errorOccurred, and Python slots touching a half-destroyed
            # QTcpSocket can segfault.
            sock.blockSignals(True)
            sock.abort()
            sock.deleteLater()

    def _on_connected(self, sock: QTcpSocket) -> None:
        if sock is not self._sock:
            return
        sock.setSocketOption(_SOCK_OPT.LowDelayOption, 1)     # TCP_NODELAY: disable Nagle
        sock.setSocketOption(_SOCK_OPT.KeepAliveOption, 1)    # OS-level backstop to the heartbeat
        self._last_rx = time.monotonic()
        self._phase = _PH_HELLO
        self._write(int(MsgType.HELLO), {"proto": PROTO_VERSION, "app": APP_ID,
                                         "username": self.username, "client_id": self.client_id})

    def _on_ready_read(self, sock: QTcpSocket) -> None:
        if sock is not self._sock:
            return
        data = sock.readAll().data()
        if not data:
            return
        self._last_rx = time.monotonic()
        try:
            sizes = self._sizer.feed(data)
            frames = self._decoder.feed(data)
        except ProtocolError as exc:
            # A corrupt stream cannot be resynchronised: frame boundaries are
            # lost. Drop the connection. The reconnect starts from a clean stream.
            self.error.emit(Err.BAD_MESSAGE, f"protocol error from server: {exc}")
            self._on_link_lost(sock, f"protocol error: {exc}")
            return
        for (t, payload), size in zip(frames, sizes):
            if sock is not self._sock:
                break                # a handler closed or replaced the connection
            self._handle_frame(t, payload, size)

    def _on_link_lost(self, sock: QTcpSocket, reason: str) -> None:
        """Common path for FIN, RST, socket errors, watchdog timeout and cut_link()."""
        if sock is not self._sock:
            return
        was_connecting = self._phase == _PH_TCP
        self._detach_socket()
        if self._state == CLOSED:
            return
        if self._awaiting_password:
            return                   # provide_password() starts a new handshake
        if self._ever_joined:
            self._schedule_reconnect()
            return
        # The very first connection failed (refused, unreachable, or dropped
        # during the handshake). Retrying is unlikely to help and would hide
        # a typo in the address, so report it and stop.
        self._stop_timers()
        if not self._handshake_error:
            prefix = "could not connect" if was_connecting else "connection lost during handshake"
            self.error.emit("CONNECT_FAILED", f"{prefix} to {self.host}:{self.port}: {reason}")
        self._set_state(CLOSED)

    # ========================================================= framing out
    def _write(self, t: int, payload: dict) -> bool:
        sock = self._sock
        if sock is None:
            return False
        return self._write_to(sock, t, payload)

    def _write_to(self, sock: QTcpSocket, t: int, payload: dict) -> bool:
        data = encode(t, payload)
        if sock.write(data) < 0:
            return False
        size = len(data)
        name = _NAMES.get(t) or type_name(t)
        self.metrics.count_out(name, size)
        if self.log_ephemeral or t not in self.noisy_types:
            self.frame_logged.emit({"t": time.time(), "dir": "out", "type": t, "name": name,
                                    "size": size, "payload": payload, "chaos": None})
        return True

    # ========================================================== framing in
    def _handle_frame(self, t: int, payload: dict, size: int) -> None:
        name = _NAMES.get(t) or type_name(t)
        self.metrics.count_in(name, size)
        if t == MsgType.ERROR:
            self._log_in(t, name, size, payload, None)
            self._on_error_frame(payload)
            return
        if t == MsgType.CHALLENGE and self._phase != _PH_JOINED:
            self._log_in(t, name, size, payload, None)
            self._on_challenge(payload)
            return
        if t == MsgType.JOINED and self._phase != _PH_JOINED:
            self._log_in(t, name, size, payload, None)
            self._on_joined(payload)
            return
        # Everything else is application traffic: it passes the fault
        # injector, which may drop, duplicate or delay it.
        chaos = self.chaos.process(t, payload, self._deliver)
        self._log_in(t, name, size, payload, chaos)

    def _log_in(self, t: int, name: str, size: int, payload: dict, chaos: str | None) -> None:
        if self.log_ephemeral or t not in self.noisy_types:
            self.frame_logged.emit({"t": time.time(), "dir": "in", "type": t, "name": name,
                                    "size": size, "payload": payload, "chaos": chaos})

    def _make_deliver(self, gen: int) -> Callable[[int, dict], None]:
        """Final delivery step after the fault injector, bound to one connection."""
        def deliver(t: int, payload: dict) -> None:
            if gen != self._gen or self._state == CLOSED:
                return               # delayed frame of a connection that no longer exists
            if t == MsgType.PONG:
                self._on_pong(payload)
            self.message.emit(t, payload)
        return deliver

    # =========================================================== handshake
    def _on_challenge(self, p: dict) -> None:
        self._nonce = p.get("nonce")
        self.server_info = {"server_id": p.get("server_id"), "server_name": p.get("server_name"),
                            "proto": p.get("proto")}
        proto = p.get("proto")
        if proto is not None and proto != PROTO_VERSION:
            self._fatal(Err.PROTO_MISMATCH,
                        f"server speaks protocol v{proto}, this client speaks v{PROTO_VERSION}")
            return
        self._phase = _PH_JOIN
        if not self._ever_joined:
            self._set_state(AUTHENTICATING)
        self._send_join()

    def _send_join(self) -> None:
        p: dict = {"room": self.room_code, "create": False}
        if self._ever_joined and self.resume_token:
            # Resume: same session (user_id, role), catch up from last_seq.
            p["resume_token"] = self.resume_token
            try:
                p["last_seq"] = int(self.last_seq_provider())
            except Exception:        # a broken provider means "send me a snapshot"
                pass
        elif self._create:
            p["create"] = True
            if self._room_name:
                p["name"] = self._room_name
            if self._password:
                # Creating a protected room: we choose the salt and upload
                # only the scrypt verifier. The password itself never leaves.
                salt = auth.new_salt()
                p["password"] = {"salt": salt, "verifier": self._verifier(self._password, salt)}
                self._salt = salt
        if not p["create"] and self._password and self._salt and self._nonce:
            # Joining: prove knowledge of the password for this nonce only.
            p["proof"] = auth.make_proof(self._verifier(self._password, self._salt), self._nonce)
        self._write(int(MsgType.JOIN), p)

    def _verifier(self, password: str, salt: str) -> str:
        """scrypt is deliberately slow (~50 ms), so cache it per (password, salt)."""
        key = (password, salt)
        v = self._verifier_cache.get(key)
        if v is None:
            v = self._verifier_cache[key] = auth.make_verifier(password, salt)
        return v

    def _on_auth_error(self, code: str, p: dict) -> None:
        self._salt = p.get("salt") or self._salt
        self._nonce = p.get("nonce") or self._nonce
        if self._password and not self._auth_retry_used and self._salt and self._nonce:
            # We now know the salt (and have a fresh nonce): retry once.
            self._auth_retry_used = True
            self._send_join()
            return
        wrong = code == Err.BAD_PASSWORD
        if wrong:
            self._password = None    # do not reuse a known-bad password on reconnect
        if self._awaiting_password:
            return                   # already prompting; do not stack dialogs
        self._awaiting_password = True
        self.password_required.emit(self.room_code, wrong)

    def _on_joined(self, p: dict) -> None:
        self._phase = _PH_JOINED
        self.user_id = p.get("user_id")
        self.resume_token = p.get("resume_token") or self.resume_token
        self.username = p.get("username") or self.username
        room = p.get("room")
        if isinstance(room, dict) and room.get("code"):
            self.room_code = room["code"]
        self._ever_joined = True
        self._attempt = 0
        self._auth_retry_used = False
        self._awaiting_password = False
        # Set the state first so that send() works inside `joined` handlers,
        # then emit `joined` (the controller resets its replica), then
        # state_changed.
        prev, self._state = self._state, JOINED
        self.joined.emit(p)
        if prev != JOINED:
            self.state_changed.emit(JOINED)
        if self._phase == _PH_JOINED and self._sock is not None:
            self._ping_timer.start(max(10, int(self.ping_interval * 1000)))
            self._send_ping()        # first RTT sample right away

    def _on_error_frame(self, p: dict) -> None:
        code = str(p.get("code", ""))
        msg = str(p.get("msg", ""))
        if self._phase != _PH_JOINED:
            if code in _AUTH_CODES:
                self._on_auth_error(code, p)
            elif code in FATAL_DURING_JOIN:
                self._fatal(code, msg)
            else:
                # e.g. SERVER_FULL: show it. If the server then closes the
                # socket, the link-lost path decides whether to retry.
                self._handshake_error = True
                self.error.emit(code, msg)
            return
        if code in FATAL_CODES:
            self._fatal(code, msg)
        else:
            self.error.emit(code, msg)        # e.g. FORBIDDEN, RATE_LIMITED: informational

    def _fatal(self, code: str, msg: str) -> None:
        self._stop_timers()
        self._detach_socket()
        self._gen += 1
        self._awaiting_password = False
        self.error.emit(code, msg)
        self._set_state(CLOSED)

    # ============================================================ liveness
    def _send_ping(self) -> None:
        if self._phase == _PH_JOINED:
            self._write(int(MsgType.PING), {"t": round(_mono_ms(), 3), "rtt": self.last_rtt})

    def _on_pong(self, p: dict) -> None:
        t = p.get("t")
        if not isinstance(t, (int, float)) or isinstance(t, bool):
            return
        rtt = _mono_ms() - float(t)
        if not 0.0 <= rtt < 600_000.0:   # PONG from before a clock or process change
            return
        self.last_rtt = round(rtt, 3)
        self.metrics.add_rtt(self.last_rtt)
        self.rtt_changed.emit(self.last_rtt)

    def _check_liveness(self) -> None:
        """Watchdog: no inbound byte for ``client_timeout`` means the link is dead."""
        sock = self._sock
        if sock is None or self._awaiting_password:
            return
        silent = time.monotonic() - self._last_rx
        if silent > self.client_timeout:
            what = "connect timed out" if self._phase == _PH_TCP else "link presumed dead"
            self._on_link_lost(sock, f"{what}: nothing received for {silent:.1f} s")

    # ============================================================ reconnect
    def _schedule_reconnect(self) -> None:
        self._set_state(RECONNECTING)
        base = self.backoff[min(self._attempt, len(self.backoff) - 1)]
        self._attempt += 1
        j = self.backoff_jitter
        delay = base * self._rng.uniform(1.0 - j, 1.0 + j)
        self._reconnect_timer.start(max(0, int(delay * 1000)))

    def _reconnect_now(self) -> None:
        if self._state == RECONNECTING:
            self._open_socket()

    # ============================================================== helpers
    def _stop_timers(self) -> None:
        self._ping_timer.stop()
        self._watchdog.stop()
        self._reconnect_timer.stop()

    def _set_state(self, s: str) -> None:
        if s != self._state:
            self._state = s
            self.state_changed.emit(s)
