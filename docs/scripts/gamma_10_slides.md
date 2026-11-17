# LANBoard: a LAN-only collaborative whiteboard

Computer Networks Lab Project 2026

A shared whiteboard for a classroom or lab that runs over the local network with no internet. A Python asyncio TCP server and a PySide6 (Qt) desktop client, connected by our own application-level protocol.

| Member | Roll no. | Area |
|---|---|---|
| Yashvanth S | CS24I1029 | Server core and state |
| Jeevanandham T | CS24I1035 | Protocol and networking |
| Akula Sai Teja | CS24I1054 | Canvas and drawing tools |
| Nithillakrishi P Y | CS24I1039 | UI and collaboration features |

[Image: LANBoard main window with six users drawing on one board, chat open on the right]

---

# The problem and what we delivered

**Goal:** several people draw, write and chat on one canvas in real time, over a LAN only, with every screen showing the same board.

**All five core modules are done:**
1. Multi-user drawing: pen, eraser, colours, clear (with permission), save and restore
2. Sessions: host or join with the LAN address and a room code; participant list; join and leave notices
3. Real-time events: ordered, de-duplicated delivery; gap repair; timeouts and reconnects
4. Group chat: in time order, with sender name and timestamp
5. State and recovery: a snapshot for late joiners, resume after a disconnect, per-user undo and redo

**Extras:** shapes and arrows, text, sticky notes with votes, image and PDF import, export to PNG/SVG/PDF, roles (owner/editor/viewer), lock and kick, password rooms, time-lapse version history, presentation and follow mode, offline editing, LAN auto-discovery, a live network inspector and a Chaos Lab.

**Deliverables:** standalone server and client executables for Windows, macOS (.app/.dmg) and Linux.

---

# Architecture: one server, many clients, one LAN

- **Client-server.** One server process hosts many rooms. Each laptop runs the client.
- **TCP port 5050** carries all application traffic: drawing, chat, control and state.
- **UDP port 5051** is used only to discover servers ("Nearby servers"). The address can always be typed instead.
- **Inside the server:** a TCP listener, then a `ClientConnection` per socket (with a reader task and a writer task), then a `Room` that acts as the sequencer, then persistence to disk.
- **Shared core (`common/`):** the server, the client and the test bots all run the same `Board.apply` code, so the same ops in the same order always produce the same board.
- **No internet anywhere.** It works on a phone hotspot or a switch with Wi-Fi internet turned off.

[Diagram: three client laptops connected by TCP 5050 to one server box (listener, connections, room sequencer, disk), with a dashed UDP 5051 discovery arrow]

*Presenter: Yashvanth S*

---

# Server core: one order for every edit

**The server's sequence number decides.** For every operation (`OP_SUBMIT`), the server checks, in order:
1. the user has joined the room
2. the user has permission (a viewer cannot edit; a locked board accepts the owner only)
3. the rate limit: 120 ops per second per client
4. validation: types, sizes and coordinates
5. de-duplication by `op_id`
6. then it assigns `seq = seq + 1`, applies the op, writes it to disk, and broadcasts it to everyone, including the sender

**The broadcast is the acknowledgement.** When the sender sees its own op come back, the op is confirmed.

**Example:** Alice turns a sticky note red and Bob turns it blue at the same moment. The server receives Alice's op first and gives it seq 101, then gives Bob's op seq 102. Every screen ends up blue, and every board hash matches.

**Concurrency:** one asyncio event loop with no locks. Each client has a bounded send queue, and a client that falls too far behind is dropped as "lagging", so it never stalls the room.

[Diagram: concurrent edit example - Alice and Bob recolour one sticky note, server assigns seq 101 and 102]

**Security:** password rooms use an scrypt verifier with a challenge-response HMAC proof, so the password is never sent. The server enforces roles, lock and kick.

*Presenter: Yashvanth S*

---

# Protocol: our own message format over TCP

**Why TCP:** every drawing op must arrive, in order. TCP gives us reliable, ordered delivery and flow control on each connection; our sequence numbers and op ids remove duplicates across reconnects. UDP would mean rebuilding all of that ourselves. `TCP_NODELAY` turns off Nagle's algorithm so small frames are sent immediately.

**Framing:** TCP is a byte stream with no message boundaries, so every message is length-prefixed:

`[ length: 4 bytes, big-endian ] [ type: 1 byte ] [ payload: UTF-8 JSON ]`

The decoder buffers partial reads and can split several frames out of a single `recv()`. The maximum frame size is 8 MiB.

**Message groups:** control (HELLO, CHALLENGE, JOIN, PING/PONG, ERROR), board ops (OP_SUBMIT, OP, OP_BATCH, OP_REJECT), presence (cursors, live strokes, user joined/left), chat, admin (roles, lock, kick, settings), saves and history, and image blobs.

**Join workflow:** HELLO, then CHALLENGE (nonce), then JOIN (with the password proof), then JOINED and a SNAPSHOT of the whole board, then live OPs from that point on.

[Diagram: frame layout - 4-byte length | 1-byte type | JSON payload, with the HELLO example]

*Presenter: Jeevanandham T*

---

# Reliability and recovery

| Problem | How we detect it | How we fix it |
|---|---|---|
| Duplicate op | `seq ≤ board.seq` | drop it |
| Gap or delay | `seq > board.seq + 1` | buffer it; after 400 ms send RESYNC and the server replays what was missed |
| Replicas drift apart | a HASH checkpoint every 25 ops | request a full SNAPSHOT |
| Dead link or pulled cable | PING every 3 s; 12 s of silence | reconnect with backoff |
| Edits made while offline | queued in `pending` | resend them after reconnecting; the server de-duplicates by `op_id` |
| Server crash | the op is logged to `ops.jsonl` before it is broadcast | snapshot plus log replay on restart |

**Reconnect:** the client sends `resume_token` and `last_seq`. The server answers with only the missed ops (OP_BATCH), or with a SNAPSHOT if the client is too far behind.

**Chaos Lab:** injects delay, jitter, duplicates and dropped ops, or cuts the cable, live in the app. The sync badge turns to "healing" and then back to "In sync".

[Image: Chaos Lab panel in the app]

*Presenter: Jeevanandham T*

---

# Canvas and drawing tools

**Built on QGraphicsScene.** The board is a set of objects, each with an id, a type, a style and geometry.

**Tools:** select/move/resize (8 handles), hand (pan), pen, highlighter, eraser, line, arrow, rectangle, ellipse, text, sticky note and laser pointer. The pen also has a Smart Ink mode: hold the pen still at the end of a stroke and a rough shape snaps to a clean one.

**Every tool produces ops.** The canvas never edits the board directly. Each action goes through the controller to the server, which keeps all replicas identical.

**Less traffic:**
- Live strokes are batched and sent every 33 ms.
- A finished stroke is simplified with the Ramer–Douglas–Peucker algorithm: 121 points become 26, and 1,851 bytes become 499 (73% smaller).

**Rich content:** images are stored once by SHA-256 hash and sent in 192 KiB chunks; PDF pages are imported as images; export to PNG, SVG or PDF.

[Image: dark board with shapes, sticky notes and a PDF page being annotated]

*Presenter: Akula Sai Teja*

---

# Undo and redo with many people editing

**Rule: undo is per user and only touches your own work.**
- Each user has their own undo and redo stacks.
- Undo sends the **inverse** of your last action as a brand-new op: add becomes delete, delete becomes restore, and update becomes the old values.
- If someone else has erased that object, your undo skips it. If someone has changed it since, their newer values are kept.
- A batch (such as a moved selection) is undone in one step. Clear is undoable.

**Example:**
1. Asha moves a sticky note (seq 50).
2. Ravi recolours it (seq 51).
3. Asha presses Undo. Her client sends the inverse move as a new op, seq 52.
4. The sticky note goes back to its old place, and it stays Ravi's colour.

[Diagram: undo with two users - shared op log and each user's undo stack]

**Why this is safe:** the undo is just another op, so it gets a seq, is ordered by the server, and reaches every client like any other edit.

*Presenter: Akula Sai Teja*

---

# UI and collaboration features

- **Connect screen:** "Nearby servers" found by UDP discovery, recent rooms, and a Host mode that can start a server on this laptop.
- **Main window:** a canvas-first layout with a floating tool palette, a style bar, a top bar with a sync badge and avatars, and Invite.
- **People panel:** colours, roles and online status; raised hands; follow or spotlight a user.
- **Owner controls:** make someone an editor or viewer, lock the board, kick, room settings and password.
- **Chat:** in time order, with names and HH:MM timestamps, system notices for joins, leaves and role changes, and an unread badge.
- **Time-lapse history:** scrub through the server's op log, preview any past version, and restore it.
- **Network panel:** a live HUD (RTT, pending ops, bytes, and messages per second) and an inspector that decodes every frame sent and received.
- **Sync badge:** In sync, Syncing, Healing, Mismatch or Offline. Toasts for events, light and dark themes, and keyboard shortcuts (press F1).

[Image: main window with the people panel and chat]

*Presenter: Nithillakrishi P Y*

---

# Performance, testing and the live demo

| Measurement | Result |
|---|---|
| Live-stroke bytes, batched every 33 ms (computed with our encoder) | 63% smaller |
| Committed stroke after RDP simplification | 73% smaller |
| Round-trip time to the server (loopback) | under 1 ms |
| Stress test: 8 bots at 20 ops/s for 12 s | about 1,750 ops, all replicas converged, 0 resyncs |
| Automated tests (pytest) | 318 passing |

The tests cover the protocol, the model and undo rules, the server, real TCP integration, and every feature driven through real app windows, on Windows, macOS and Linux in CI.

**Demo, with the internet off:**
1. Start a phone hotspot.
2. Start the server, or use Host mode in the app.
3. Three laptops join from "Nearby servers".
4. Draw, chat, lock the board, use a password room.
5. Use Chaos Lab to cut the cable.
6. Everyone recovers and is back in sync.

[Image: connect screen showing a discovered server and its rooms]

**Thank you. Questions?**

*Presenter: Nithillakrishi P Y (testing: Yashvanth S)*
