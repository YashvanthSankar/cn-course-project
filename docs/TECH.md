# TECH — Stack, Architecture & Protocol

## 1. Stack
| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.12** (pin via venv) | Team already writes socket labs in Python; PySide6 wheels exist for 3.12 (system Python 3.15 may not have wheels yet). |
| Server networking | `asyncio` streams (`asyncio.start_server`) over raw TCP | Thousands of concurrent connections in one thread; no locks needed for room state; TCP_NODELAY on by default. |
| Client GUI | **PySide6** (Qt 6) — `QGraphicsScene/QGraphicsView` | Hardware-accelerated 2D scene graph: items, hit-testing, zoom/pan, selection, export to PNG/SVG/PDF built in. |
| Client networking | `QTcpSocket` (signal-driven, `LowDelayOption`) | Native to the Qt event loop → no Qt/asyncio thread juggling. Speaks the same framed protocol as the server. |
| Discovery | UDP broadcast (`socket` SOCK_DGRAM) | Zero-config "Nearby servers" list on the LAN. |
| Security | `hashlib.scrypt`, `hmac`, `secrets`; optional `ssl` | Salted password hashes, challenge–response so passwords never cross the wire, optional TLS. |
| Persistence | JSON snapshot + JSONL op log per room | Human-readable, crash-safe (append-only), trivially replayable for history. |
| Tests | `pytest`, `pytest-asyncio` | Protocol codec, op application, server room logic, multi-client integration. |
| Packaging | **PyInstaller** one-dir builds (`server`, `client`) | Standalone executables; one-dir avoids Qt plugin & macOS start-up issues of `--onefile`. |

Third-party dependencies are credited in `README.md` (PySide6 LGPL-3.0, PyInstaller GPL-2.0 w/ bootloader exception, pytest MIT).

## 2. Architecture

```
          ┌───────────────────────────── LAN ─────────────────────────────┐
          │                                                               │
 ┌────────┴────────┐   TCP :5050 (framed protocol)    ┌──────────────────┐ │
 │  Client (Qt)    │◀────────────────────────────────▶│  Server (asyncio)│ │
 │ ─ CanvasView    │                                  │ ─ Listener        │ │
 │ ─ Tools         │   UDP :5051 discovery bcast      │ ─ Session/auth    │ │
 │ ─ ChatPanel     │◀ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ▶│ ─ Room actors     │ │
 │ ─ Participants  │                                  │   • Board model   │ │
 │ ─ NetClient     │                                  │   • Op log (seq)  │ │
 │ ─ LocalModel    │                                  │   • Chat log      │ │
 └─────────────────┘                                  │ ─ Persistence     │ │
        × N clients                                   │ ─ Discovery resp. │ │
                                                      └──────────────────┘ │
          └───────────────────────────────────────────────────────────────┘
```

- **Shared core (`common/`)**: framing codec, message schema/validation, and the **board model**
  (`apply(op)`). Server and client run the *same* `apply` → deterministic convergence.
- **Server**: one `ClientConnection` per socket (reader task + writer task with bounded queue);
  `Room` objects hold board, op log, chat, members, roles. All room mutations happen on the event
  loop thread → no data races by construction.
- **Client**: `NetClient` (QTcpSocket) → emits Qt signals with decoded messages → `BoardController`
  applies to `LocalModel` → `CanvasScene` re-renders affected items only.

## 3. Wire protocol (LANBoard Protocol v1)

### 3.1 Transport & framing
- **TCP** because the op log needs exactly what TCP gives: reliable, in-order, byte-stream delivery.
  Re-implementing ordering + retransmission over UDP would add complexity for no LAN benefit.
  UDP is used only for discovery (loss-tolerant, broadcast-capable).
- TCP has **no message boundaries**, so every message is framed:

```
 0               4        5                       5+N
 +---------------+--------+-------------------------+
 | length N (u32,| type   | payload (N-1 bytes)     |
 | big-endian)   | (u8)   | UTF-8 JSON              |
 +---------------+--------+-------------------------+
```
  `length` counts type + payload. Max frame 8 MiB (reject + disconnect above). Decoder is
  incremental (handles partial reads and multiple frames per read).
- `type` byte groups messages: `0x0_` control, `0x1_` presence, `0x2_` board ops, `0x3_` chat,
  `0x4_` admin, `0x5_` history/persistence. JSON payload keeps it debuggable; point arrays are
  compact integer lists.

### 3.2 Handshake & join
```
C→S HELLO        {proto:1, client:"lanboard/0.1", username, client_id}
S→C CHALLENGE    {nonce}                         (always sent; used if room has password)
C→S JOIN_ROOM    {room, create?:bool, proof?, resume_token?, last_seq?}
                   proof = HMAC-SHA256(scrypt(password, room_salt), nonce)
S→C JOINED       {user_id, color, role, resume_token, room_info, participants}
S→C SNAPSHOT     {seq, objects, chat_tail}       ─┐ either full snapshot …
S→C OP_BATCH     {ops:[…]}                       ─┘ … or only ops after last_seq (resume)
S→C ERROR        {code, msg}   e.g. BAD_PASSWORD, ROOM_NOT_FOUND, FORBIDDEN, PROTO_MISMATCH
```

### 3.3 Board ops (the op log)
```
C→S OP_SUBMIT    {op_id, op}                 op_id = "<client_id>:<counter>"
S→* OP           {seq, user_id, op_id, ts, op}   broadcast to all incl. sender (= ack)
S→C OP_REJECT    {op_id, reason}             (forbidden, invalid, board locked)
```
Op kinds (`op.k`):
| k | fields | inverse (for undo) |
|---|---|---|
| `add` | `obj` (full object: id, type, style, geometry) | `delete id` |
| `update` | `id, set:{…}, prev:{…}` | `update id, set:prev` |
| `delete` | `ids:[…]` (tombstone) | `restore ids` |
| `restore` | `ids:[…]` | `delete ids` |
| `clear` | — (tombstones all live objects; owner/permitted) | `restore <those ids>` |

Object types: `stroke`, `line`, `arrow`, `rect`, `ellipse`, `text`, `sticky`, `image`.
Object IDs are `<user_id>-<counter>` → globally unique without a round trip.

### 3.4 Ephemeral (not logged, no seq)
```
C→S STROKE_LIVE  {id, color, width, pts:[x,y,…]}   batched every 33 ms while drawing
S→* STROKE_LIVE  {user_id, …}                     preview only; replaced by the committed `add`
C→S CURSOR       {x, y}  (≤ 20 Hz)  → S→* CURSOR {user_id, x, y}
C↔S PING {t} / PONG {t}  every 5 s; server drops client after 15 s silence
```

### 3.5 Presence, chat, admin, history
```
S→* USER_JOINED {user_id, username, color, role}   S→* USER_LEFT {user_id, reason}
C→S CHAT_SEND {cmid, text}  → S→* CHAT {chat_seq, user_id, username, ts, text}
C→S SET_ROLE {user_id, role} | KICK {user_id} | LOCK {locked} | SET_PASSWORD {…}   (owner only)
C→S SAVE {name} / LOAD {name} / LIST_SAVES   → server-side named saves
C→S HISTORY_RANGE {from_seq, to_seq}         → for the timeline view
C→S VIEWPORT {x,y,zoom}  (presenter)         → S→* VIEWPORT (followers)
```

## 4. Synchronisation & consistency rules
1. **Total order**: the server assigns `seq` = room counter + 1 when it accepts an op.
2. **Optimistic local apply**: the client renders its own op immediately (pending layer); when the
   echoed `OP` with its `op_id` arrives it moves to the confirmed model. On `OP_REJECT` it rolls back.
3. **Duplicates**: client drops `OP` with `seq ≤ last_seq`; server drops `OP_SUBMIT` with an
   `op_id` already accepted (per-client set/high-water mark) → resend-on-reconnect is safe.
4. **Gaps**: if an `OP` arrives with `seq > last_seq + 1`, client sends `RESYNC {last_seq}` and
   buffers until filled.
5. **Late join / reconnect**: `resume_token + last_seq`; if the op log still contains `last_seq+1`
   → send the tail; else → full `SNAPSHOT`. Log compacted into a snapshot every 2 000 ops.
6. **Concurrent edits on the same object**: ops are applied in `seq` order; `update` is
   last-writer-wins per property *by server order*; `update`/`delete` on a tombstoned object is a no-op.

## 5. Undo / redo semantics (multi-user)
- **Per-user**: each user's undo stack contains only *their own* confirmed ops. Undo never removes
  someone else's work.
- Undo = submit the **inverse op** as a brand-new op (gets its own `seq`) → all clients converge.
- Conflict rules (documented & tested):
  - Undo of my `add` when someone already erased that object → skip, move to next entry.
  - Undo of my `update` when another user has since changed the same property → skip (stale).
  - A `clear` by someone else makes my entries targeting cleared objects no-ops; the clearer can
    undo the clear (restores everything they tombstoned).
  - New op after undo clears my redo stack (standard branching).
- One stroke (pen-down → pen-up) = one undo entry.

## 6. Concurrency model (server)
- Single asyncio event loop; each connection = reader coroutine + writer coroutine.
- Outbound: per-client `asyncio.Queue(maxsize=1000)`; if full → client is too slow → disconnect
  with `USER_LEFT{reason:"lagging"}` (it will resume via reconnect). One slow client never stalls a room.
- Disk I/O (snapshots) via `asyncio.to_thread` so the loop never blocks.

## 7. Performance plan
- Live preview batched to ~30 Hz instead of per mouse event (~120–1000 Hz).
- Ramer–Douglas–Peucker simplification on stroke commit; integer coordinates.
- Cursor updates throttled to 20 Hz and coalesced (latest wins).
- Client redraws only changed `QGraphicsItem`s.
- Metrics module counts msgs/bytes in/out and RTT → HUD + benchmark script for the report.

## 8. Security & access control
- Room password: server stores `scrypt(password, salt)`; join proves knowledge via
  HMAC challenge–response → password never sent in clear, replay-proof (fresh nonce).
- Roles: `owner` (creator; clear, lock, kick, set roles), `editor`, `viewer`. **Every op checked
  server-side**; client UI just mirrors permissions.
- Input validation: frame size cap, JSON schema checks, coordinate bounds, chat length cap, rate
  limit per client (ops/sec).
- Optional `--tls` with self-signed cert and fingerprint pinning.

## 9. Pitfalls we've planned for
- **Nagle**: set `TCP_NODELAY` on accepted sockets & `QAbstractSocket.LowDelayOption` on client.
- **PyInstaller + PySide6**: build one-dir in a clean venv; exclude QtWebEngine/QtQml etc.
- **Firewalls**: Windows "Public" network blocks inbound — use Private profile; macOS prompts for
  unsigned apps — ad-hoc `codesign -s -`. Demo on a phone hotspot/switch (campus Wi-Fi often has
  client isolation).
- Never trust client-sent `user_id`/role — server assigns them.
