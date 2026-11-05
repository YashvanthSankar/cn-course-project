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
| Persistence | JSON snapshot + JSONL op log + JSONL history archive per room, in the per-user app-data dir | Human-readable, crash-safe (append-only), trivially replayable for history (§10). |
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
- **Multi-part messages.** A board or a time-lapse can be bigger than any one frame, so a
  `SNAPSHOT` or `HISTORY` payload above `PART_BYTES` (1 MiB) is sent as several frames of the
  same type tagged `{part, parts, xid}`. Each part repeats the scalar fields (`seq`, `hash`,
  `base_seq`) and carries a slice of the big lists (`board.objects`; `base.objects`, `ops`).
  The receiver (`protocol.PartAssembler`) buffers the parts by `xid` (any order, duplicates
  ignored) and applies the merged payload only when all have arrived, so a late joiner never
  sees a half-loaded board. A long catch-up is several `OP_BATCH` frames, split by count
  (≤ 500) and by bytes (≈ `PART_BYTES`). Small payloads stay single frames without the tags.
- **Per-op size cap.** One op's `OP` frame may not exceed `MAX_OP_BYTES` (4 MiB): the server
  answers `OP_REJECT {code: TOO_LARGE}` and the connection stays up; the client rolls the op
  back. Only a frame above `MAX_FRAME`, which cannot even be parsed, still closes the link.
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
S→C SNAPSHOT     {seq, board:{seq, objects}, hash, part?, parts?, xid?}  ─┐ either full snapshot …
S→C OP_BATCH     {ops:[…]}                       ─┘ … or only ops after last_seq (resume)
S→C ERROR        {code, msg}   e.g. BAD_PASSWORD, ROOM_NOT_FOUND, FORBIDDEN, PROTO_MISMATCH
```

### 3.3 Board ops (the op log)
```
C→S OP_SUBMIT    {op_id, op}                 op_id = "<client_id>:<counter>"
S→* OP           {seq, user_id, op_id, ts, op}   broadcast to all incl. sender (= ack)
S→C OP_REJECT    {op_id, code, msg}          (FORBIDDEN, LOCKED, INVALID_OP, RATE_LIMITED, TOO_LARGE)
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
C→S HISTORY_REQ {}  → S→C HISTORY {base_seq, base:{seq, objects}, ops:[OpMsg]}   (time-lapse, §10)
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
   → send the tail; else → full `SNAPSHOT`. The log is compacted into a snapshot every
   `AUTOSAVE_EVERY_OPS` (50) ops or `AUTOSAVE_INTERVAL` (10 s); the compacted ops move to the
   history archive, so resume and time-lapse still reach back past the snapshot (§10).
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
- Outbound: per-client `asyncio.Queue(maxsize=SEND_QUEUE_MAX)` (4 000 frames); if full → client is too slow → disconnect
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

## 10. Persistence & durable time-lapse history
Data directory: `--data DIR`, default the per-user app-data folder
(`~/Library/Application Support/LANBoard` on macOS, `%APPDATA%\LANBoard` on Windows,
`~/.local/share/lanboard` on Linux), never the current directory. Per room:

| File | Contents | Written |
|---|---|---|
| `snapshot.json` | board at seq S | atomically (tmp + fsync + `os.replace`) at each compaction |
| `ops.jsonl` | every op after S (write-ahead log) | appended *before* the op is broadcast |
| `history_base.json` | board at the start of the history, seq B (empty, B = 0, for a new room) | when the history is trimmed |
| `history.jsonl` | every op in (B, A], A ≤ S | compaction appends the ops leaving `ops.jsonl` |

`history_base + history.jsonl + ops.jsonl` is the gap-free sequence of every op since B, so
`HISTORY` after a restart still replays from the room's first op, and a resume after a restart
can still be served from the log.

- **Compaction order** (one task per room at a time): capture board + ops on the loop thread →
  worker thread appends the ops to `history.jsonl` (fsync) → writes `snapshot.json` → loop
  truncates `ops.jsonl`. A crash between any two steps loses nothing: the loader skips ops it
  already has (by seq) and stops at a gap.
- **Loader**: history = base + contiguous ops from both JSONL files; board = snapshot + history
  ops after it (or base + all ops if the snapshot is missing). A data directory from before the
  archive existed (no `history_base.json`) loads exactly as before, with the history starting at
  its snapshot; the archive files are created at the next compaction.
- **Bounds**: at most `HISTORY_MAX_OPS` (100 000) ops and `HISTORY_MAX_BYTES` (32 MiB) of op
  JSON per room, in memory and on disk. Past either cap the oldest ~10 % are folded into the base
  board and `history.jsonl` is rewritten (atomically) without them. Clients see this as
  `HISTORY.base_seq > 0`: "history starts at seq base_seq + 1".
- **Torn lines**: appends are flushed, not fsync-ed (a killed process loses nothing; a power cut
  may tear the last line). The loader skips a torn line, and before appending to such a file the
  server terminates it with a newline so the next record is not glued onto it.
- **Windows**: `os.replace` maps to `MoveFileEx(MOVEFILE_REPLACE_EXISTING)`, atomic for our
  purposes, but it fails with a sharing violation while another process (virus scanner, indexer)
  holds the target open. `replace_file()` retries for up to ~1 s; open log files are closed
  before they are replaced; temp files are deleted if a write fails.
