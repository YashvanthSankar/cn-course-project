"""Message catalogue of the LANBoard protocol (v1).

Every frame carries a one-byte ``MsgType`` and a JSON object payload. The
high nibble groups messages: 0x0_ control, 0x1_ presence/ephemeral,
0x2_ board ops, 0x3_ chat, 0x4_ admin, 0x5_ saves/history, 0x6_ blobs.

Direction legend: C->S client to server, S->C server to one client,
S->* server broadcast to everyone in the room.

Payload contracts (all keys required unless marked ``?``):

Control
  HELLO        C->S {proto, app, username, client_id}
  CHALLENGE    S->C {nonce, server_id, server_name, proto}
  LIST_ROOMS   C->S {}
  ROOMS        S->C {rooms:[{code, name, users, protected, locked}]}
  JOIN         C->S {room, create:bool, name?, password?:{salt, verifier}  (create only),
                     proof?, resume_token?, last_seq?}
               proof = hex HMAC-SHA256(key=verifier_bytes, msg=nonce) where
               verifier = scrypt(password, salt) (see common/auth.py)
  JOINED       S->C {user_id, username, color, role, resume_token, resumed:bool,
                     room:RoomInfo, participants:[Participant], chat:[Chat] (tail),
                     server_time}
               JOINED is always followed by SNAPSHOT (fresh join, or resume too old)
               or OP_BATCH (resume: ops with seq > last_seq, possibly empty).
  SNAPSHOT     S->C {seq, board:{seq, objects:[obj]}, hash, part?, parts?, xid?}
               a big board comes as several parts (see "Multi-part messages")
  OP_BATCH     S->C {ops:[OpMsg]}                 catch-up after resume / RESYNC; a long
               catch-up is several OP_BATCH frames (<= 500 ops and ~PART_BYTES each),
               each applied as it arrives (no reassembly needed: ops carry their seq)
  ERROR        S->C {code, msg, salt?, nonce?, hash?}    see ``Err``; salt + nonce come with
               AUTH_REQUIRED / BAD_PASSWORD, hash (sha256 hex) with image (BLOB_*) errors
  PING         C->S {t, rtt?}   PONG S->C {t, server_time}
               rtt = the client's last measured RTT in ms (or null), shown on the dashboard
  LEAVE        C->S {}
  RESYNC       C->S {last_seq}                    client detected a gap; the server answers
               with OP_BATCH (ops after last_seq) or SNAPSHOT. last_seq -1 = full SNAPSHOT
               (used to heal a HASH mismatch)
  HASH         S->* {seq, hash}                   consistency checkpoint

Presence / ephemeral (never logged, no seq)
  USER_JOINED  S->* {participant:Participant}
  USER_LEFT    S->* {user_id, reason}             reason in ``Reason``
  USER_UPDATE  S->* {participant:Participant}     role/online/hand changes
  CURSOR       C->S {x, y}           S->* {user_id, x, y}
  STROKE_LIVE  C->S {id, color, width, pts, tool?}  S->* {user_id, ...} (pts: new points only)
  STROKE_END   C->S {id}             S->* {user_id, id}    preview finished/cancelled
  LASER        C->S {pts}            S->* {user_id, pts}   fading pointer trail
  VIEWPORT     C->S {x, y, w, h}     S->* {user_id, x, y, w, h}  presenter view (owner/presenter)
  SIGNAL       C->S {kind}           S->* {user_id, kind}  kind: "hand_up"|"hand_down"|"ping_here"...

Board ops (logged, ordered by seq)
  OP_SUBMIT    C->S {op_id, op}
  OP           S->* OpMsg = {seq, user_id, op_id, ts, op}  (echo to sender == ack)
  OP_REJECT    S->C {op_id, code, msg}
  Permissions: viewers cannot submit; a locked room accepts ops only from the owner;
  ``clear`` needs owner (or editors_can_clear); ``replace`` (restore a version) is owner only.
  Size: an op whose OP frame would exceed ``MAX_OP_BYTES`` (4 MiB) is refused with
  OP_REJECT {code: TOO_LARGE}; the connection stays up and no seq is consumed. (A frame
  above ``MAX_FRAME`` cannot be parsed at all and still closes the connection.)

Chat
  CHAT_SEND    C->S {cmid, text}
  CHAT         S->* Chat = {id, user_id, username, color, ts, text, kind:"user"|"system", cmid?}

Admin (owner only, enforced by server)
  SET_ROLE     C->S {user_id, role}
  KICK         C->S {user_id}
  ROOM_SETTINGS C->S {locked?, name?, editors_can_clear?, password?:{salt,verifier}|null,
                      presenter?:user_id|null}
  ROOM_UPDATE  S->* {room:RoomInfo}

Saves / history
  SAVE         C->S {name}                  -> SAVES reply to sender, CHAT system note to all
  LIST_SAVES   C->S {}                      SAVES S->C {saves:[{name, ts, objects}]}
  LOAD         C->S {name}                  owner: server submits a ``replace`` op
  HISTORY_REQ  C->S {}                      HISTORY S->C {base_seq, base:{seq, objects},
                                                          ops:[OpMsg], part?, parts?, xid?}
               base = the board at base_seq; ops = every op after it, in seq order.
               The history is durable (survives server restarts) and capped per room
               (HISTORY_MAX_OPS / HISTORY_MAX_BYTES): base_seq == 0 means it is complete
               since the room was created, base_seq > 0 means older ops were dropped and
               the history starts at seq base_seq + 1. Long histories come in parts.

Blobs (images; content-addressed by sha256 hex)
  BLOB_PUT     C->S {hash, mime, size, index, count, data(b64)}
  BLOB_GET     C->S {hash}
  BLOB_DATA    S->C {hash, mime, size, index, count, data(b64)}
  BLOB_STORED  S->C {hash}                  all chunks received and verified

Shared shapes
  RoomInfo     {code, name, locked, protected, editors_can_clear, owner_id, presenter}
  Participant  {user_id, username, color, role, online, hand}

Multi-part messages (SNAPSHOT, HISTORY)
  A payload larger than ``PART_BYTES`` (1 MiB) is sent as ``parts`` consecutive frames
  of the same type, each with ``part`` (0-based index), ``parts`` (count) and ``xid``
  (transfer id, unique per server process). Every part carries all the payload's scalar
  fields (seq, hash, base_seq) and a slice of its big lists: SNAPSHOT board.objects;
  HISTORY base.objects, then ops. The receiver concatenates the slices in ``part`` order
  and processes the merged payload only once every part of that ``xid`` has arrived, so
  a board is never applied half-loaded (see ``protocol.PartAssembler``). Parts must be
  tolerated in any order and twice. Small payloads are a single frame without these
  fields, exactly as before. The server queues all parts of a SNAPSHOT in one go, so no
  OP of the room is interleaved between them.
"""

from __future__ import annotations

from enum import IntEnum


class MsgType(IntEnum):
    HELLO = 0x01
    CHALLENGE = 0x02
    JOIN = 0x03
    JOINED = 0x04
    SNAPSHOT = 0x05
    OP_BATCH = 0x06
    ERROR = 0x07
    PING = 0x08
    PONG = 0x09
    LEAVE = 0x0A
    RESYNC = 0x0B
    HASH = 0x0C
    LIST_ROOMS = 0x0D
    ROOMS = 0x0E
    USER_JOINED = 0x10
    USER_LEFT = 0x11
    USER_UPDATE = 0x12
    CURSOR = 0x13
    STROKE_LIVE = 0x14
    STROKE_END = 0x15
    LASER = 0x16
    VIEWPORT = 0x17
    SIGNAL = 0x18
    OP_SUBMIT = 0x20
    OP = 0x21
    OP_REJECT = 0x22
    CHAT_SEND = 0x30
    CHAT = 0x31
    SET_ROLE = 0x40
    KICK = 0x41
    ROOM_SETTINGS = 0x42
    ROOM_UPDATE = 0x43
    SAVE = 0x50
    LIST_SAVES = 0x51
    SAVES = 0x52
    LOAD = 0x53
    HISTORY_REQ = 0x54
    HISTORY = 0x55
    BLOB_PUT = 0x60
    BLOB_GET = 0x61
    BLOB_DATA = 0x62
    BLOB_STORED = 0x63


EPHEMERAL = frozenset({
    MsgType.CURSOR, MsgType.STROKE_LIVE, MsgType.STROKE_END,
    MsgType.LASER, MsgType.VIEWPORT, MsgType.SIGNAL,
})


def type_name(t: int) -> str:
    try:
        return MsgType(t).name
    except ValueError:
        return f"0x{t:02X}"


class Err:
    """ERROR / OP_REJECT codes."""
    PROTO_MISMATCH = "PROTO_MISMATCH"
    BAD_MESSAGE = "BAD_MESSAGE"
    NOT_JOINED = "NOT_JOINED"
    ROOM_NOT_FOUND = "ROOM_NOT_FOUND"
    ROOM_EXISTS = "ROOM_EXISTS"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BAD_PASSWORD = "BAD_PASSWORD"
    FORBIDDEN = "FORBIDDEN"
    LOCKED = "LOCKED"
    INVALID_OP = "INVALID_OP"
    RATE_LIMITED = "RATE_LIMITED"
    KICKED = "KICKED"
    NOT_FOUND = "NOT_FOUND"
    TOO_LARGE = "TOO_LARGE"
    SERVER_FULL = "SERVER_FULL"


class Reason:
    """USER_LEFT reasons."""
    LEFT = "left"
    TIMEOUT = "timeout"
    DISCONNECTED = "disconnected"
    KICKED = "kicked"
    LAGGING = "lagging"
    EXPIRED = "expired"
