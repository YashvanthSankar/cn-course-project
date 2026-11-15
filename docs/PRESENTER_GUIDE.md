# Presenter guide: LANBoard explanation videos and live demo

This is study material for the four explanation videos and the in-person viva. It goes with
`docs/LANBoard_Presentation.pptx`. Every slide has speaker notes, and a coloured chip in the top
right shows who presents it.

**Use your own words.** The brief says each member must explain **their own** contribution, with
their name and face visible. The talking points below are a checklist of what to cover. They are
not a script, so do not read them out. Open the files listed for you and walk through the code
yourself.

**Be accurate about authorship.** The git history shows who committed each change, and AI-assisted
commits carry a `Co-Authored-By: Claude` trailer. Present your slides as **your area**, and describe
what you personally built, tested or changed. Do not claim code you did not write. Before the videos,
every member should make and commit real work in their own area from their own account.

| Slides | Presenter | Topic |
|---|---|---|
| 1 | anyone (all on camera, or each video opens with it) | title and team |
| 2–9 | Yashvanth S | goals, requirements, architecture, layers, server core, ordering, security, persistence |
| 10–17 | Jeevanandham T | protocol and networking, TCP/UDP, framing, join, reliability, reconnect, performance, Chaos Lab |
| 18–21 | Akula Sai Teja | canvas, drawing tools, objects, images/PDF, undo/redo |
| 22–26 | Nithillakrishi P Y | UI, chat and people, time-lapse, Network Inspector, badge and dark mode |
| 27–30 | all (Yashvanth can take Testing) | testing, challenges, demo plan, Q&A |

`client/controller.py` is shared by Yashvanth (the board replica and the ordering rules) and
Jeevanandham (resync, resume and multi-part handling). Each of you explains your own half.

Numbers you can quote (all checked against the code on 2026-11-06):
- 318 tests, all passing in about 90 s (`.venv/bin/python -m pytest -q`).
- `config.py`: PING every 3 s and a 12 s idle timeout, HASH every 25 ops, live strokes flushed every 33 ms, cursors at most every 50 ms, 120 ops/s per client, a send queue of 4000 frames, MAX_FRAME 8 MiB, PART_BYTES 1 MiB, autosave every 50 ops or 10 s.
- Measured traffic: a 1-second stroke costs 121 frames and 10,497 B unbatched, or 31 frames and 3,927 B batched. The committed stroke goes from 121 points (1,851 B) to 26 points (499 B) after RDP. A cursor frame is 26 B, so 3,120 B/s at 120 Hz and 520 B/s at 20 Hz.
- Stress test (`python -m lanboard.tools.bot --bots 8 --rate 20 --seconds 12`, on loopback): 1,771 ops in 12.1 s, RTT about 0.2 ms, 0 resyncs, 0 rejects, 70 hash checks per bot, ALL REPLICAS CONVERGED.

`docs/ARCHITECTURE.md` §3.4 uses the same values as the code: PING every 3 s, a drop after 12 s of silence, and the `JOIN` message.

---

## 1. Yashvanth S (CS24I1029): server core and state

**Slides:** 2–9, plus 27 (Testing) if you take it.

### Talking points (3–4 minutes)
1. **Goals and constraints** (slides 2–3). LAN only, no internet at the demo, standalone executables. Map the 5 core modules to what you built.
2. **Architecture** (slide 4). One server and N clients. TCP 5050 carries everything. UDP 5051 is used only for discovery. Inside the server: the listener, one `ClientConnection` per socket, one `Room` per room, and persistence.
3. **Layers and the shared model** (slide 5). Server and client both run `Board.apply` from `common/model.py`. The same ops in the same order give identical boards, and `Board.hash()` proves it.
4. **Concurrency model.** One asyncio loop. Each connection has a reader loop and a writer loop. All room state changes on the loop thread, so there are no locks and no races. The outbound queue is bounded: if it fills, the client is disconnected as "lagging" and the room never waits for it.
5. **Op pipeline** (slides 6–7). Walk through `Room.submit` step by step: joined, permission, rate limit, validate, dedupe by op_id, normalise, size check, seq + apply, log to disk, broadcast to everyone including the sender, then a HASH every 25 ops. Go through the two-client recolour example.
6. **Security** (slide 8). scrypt verifier, a nonce per connection, an HMAC proof, roles, lock and kick. Every check happens on the server.
7. **Persistence** (slide 9). The op is written to `ops.jsonl` before it is broadcast. An atomic snapshot (tmp + fsync + `os.replace`) is taken every 50 ops or 10 s. `history.jsonl` keeps the time-lapse. After a restart the server loads the snapshot and replays the tail.

### Show on screen
- `src/lanboard/server/room.py`: `Room.submit` (the numbered steps), `check_permission`, `send_snapshot` / `send_catch_up`, `on_resync`, `on_set_role` / `on_kick` / `on_room_settings`, `TokenBucket`.
- `src/lanboard/server/server.py`: `Server._on_client` (TCP_NODELAY, SO_KEEPALIVE), `ClientConnection.run` (the reader loop), `_writer_loop` (the `drain()` back-pressure), `send_frame` (the bounded queue and the lagging drop), `on_join` (the password proof check).
- `src/lanboard/common/model.py`: `validate_op`, `Board.normalise`, `Board.apply`, `Board.hash`.
- `src/lanboard/server/persistence.py`: `atomic_write_bytes`, `RoomStore.append_op`, `write_checkpoint`, and the loader that skips torn lines.
- `src/lanboard/server/dashboard.py`: `Dashboard.render`. Optionally run `python -m lanboard.server --dashboard` live.
- `src/lanboard/client/controller.py` (your half): `_on_op`, `_apply_confirmed`, `_rebuild_view` (board + pending = view).

### Likely viva questions
1. **How are concurrent operations ordered?** The room gives each accepted op `seq = room.seq + 1` and applies ops in that order. Every client applies OPs strictly in seq order, so all replicas agree. For an `update`, the last writer by server order wins, key by key.
2. **What is the acknowledgement for an op?** The broadcast itself. The server sends the OP to everyone, the sender included. When the sender sees its own `op_id` come back, the op leaves `pending`.
3. **Why don't you need locks on the server?** All room state is changed on one asyncio event-loop thread. Coroutines only switch at `await`, and `Room.submit` contains no `await`. Disk snapshots run through `asyncio.to_thread` on a copy of the data.
4. **What happens with a slow client?** Its per-connection queue (4000 frames) fills up because `drain()` blocks on TCP flow control. The server then drops that client with reason "lagging". It can resume, and nobody else is slowed down.
5. **What if the server crashes mid-session?** Every accepted op was appended to `ops.jsonl` before it was broadcast. On restart the server loads `snapshot.json` and replays the ops after its seq. A torn last line is skipped.
6. **How does a late joiner get the board?** `JOINED` is followed by a `SNAPSHOT {seq, board, hash}`. A board larger than 1 MiB is split into parts, and the server queues all of them at once so no OP lands in between.
7. **How is the password protected?** The server stores only `scrypt(password, salt)`. Each connection gets a fresh nonce, and the client sends `HMAC-SHA256(verifier, nonce)`. The password never travels on the network, and a recorded proof is useless next time.
8. **What can a viewer do? Who enforces it?** A viewer can select, pan and use the laser pointer, but `OP_SUBMIT` from a viewer is rejected with FORBIDDEN by `check_permission`. The UI only greys out the tools.
9. **Can a client fake its user_id or role?** No. The server assigns the user_id and role at JOIN and stamps `user_id` on every OP. It ignores any value the client sends.
10. **What is in the board hash?** A sha256 over every object's canonical JSON (id, type, owner, z, style, geom, off, deleted), sorted by id and cut to 16 hex characters. It is computed the same way on the server and on every client.

---

## 2. Jeevanandham T (CS24I1035): protocol and networking

**Slides:** 10–17.

### Talking points (3–4 minutes)
1. **Your files** (slide 10). The codec, the catalogue, NetClient, discovery, the Chaos Lab, and your half of the controller.
2. **Why TCP** (slide 11). The op log needs reliable, in-order delivery with flow control, so TCP. Rebuilding that on UDP would bring no benefit on a LAN. UDP is used only for discovery, because only UDP can broadcast. Explain TCP_NODELAY and why Nagle's algorithm hurts small frames.
3. **Framing** (slide 12). A u32 big-endian length, a u8 type, then JSON. The decoder is incremental and handles half frames and several frames per read. Read the real HELLO hex dump. The type nibble groups the messages. Ephemeral messages have no seq.
4. **Handshake** (slide 13). HELLO, CHALLENGE (nonce), JOIN (proof / resume_token / last_seq), JOINED, then a SNAPSHOT or an OP_BATCH.
5. **Reliability** (slide 14). A duplicate (seq ≤ last) is dropped. A gap is buffered for 400 ms, then the client sends RESYNC{last_seq}. On a HASH mismatch it sends RESYNC{-1} and gets a fresh SNAPSHOT. Show the real inspector capture.
6. **Reconnect and offline** (slide 15). A heartbeat plus the idle timeout detects a half-open link. Backoff is 0.5/1/2/4/5 s with ±20 % jitter. Resume sends the token and last_seq. Pending ops are resent, and the server dedupes them by op_id.
7. **Performance** (slide 16). Quote the measured numbers above.
8. **Chaos Lab** (slide 17). It injects faults on inbound frames, and each fault exercises one recovery mechanism.

### Show on screen
- `src/lanboard/common/protocol.py`: `encode`, `FrameDecoder.feed`, `encode_parts`, `PartAssembler.add`.
- `src/lanboard/common/messages.py`: the module docstring (the catalogue) and `MsgType`.
- `src/lanboard/client/net.py`: the docstring state machine, `connect_to`, `_on_connected` (LowDelayOption, KeepAlive), `_on_ready_read`, `_on_challenge` / `_send_join`, `_on_link_lost` (backoff), `cut_link`.
- `src/lanboard/client/controller.py` (your half): `_on_op` (dup / gap / reorder), `_on_gap_timeout`, `_request_resync`, `_on_hash` / `_check_hash`, `_on_snapshot` with the PartAssembler, `_flush_live` and `_flush_cursor`.
- `src/lanboard/client/chaos.py`: `FaultInjector.process`.
- `src/lanboard/server/discovery.py`: `DiscoveryProtocol.datagram_received`. `src/lanboard/client/discovery.py`: `DiscoveryBrowser.probe` and `_targets`.

### Likely viva questions
1. **Why not UDP for drawing?** You would have to rebuild acknowledgements, retransmission, ordering and congestion control yourself. TCP already provides all of them, and on a LAN its overhead is negligible. UDP is only used to broadcast a discovery probe, which TCP cannot do.
2. **How do you find message boundaries in TCP?** Every frame starts with a 4-byte big-endian length. `FrameDecoder.feed` buffers bytes until a whole frame has arrived. One `recv` can hold half a frame or several frames.
3. **What stops a malicious huge frame?** Any length above `MAX_FRAME` (8 MiB) raises `ProtocolError`, and the connection is closed. A single op whose OP frame would exceed 4 MiB is refused with `OP_REJECT TOO_LARGE`, and the connection stays up.
4. **How do you detect a pulled cable?** A pulled cable produces no FIN or RST, so the connection stays half-open. The client pings every 3 s and the server always answers. If nothing arrives for 12 s, either side declares the link dead.
5. **What exactly happens when an OP is lost?** The next OP has `seq > board.seq + 1`. The client buffers it and starts a 400 ms timer. If the hole is not filled, it sends `RESYNC{last_seq}`. The server replies with an `OP_BATCH` of the missing ops, or with a SNAPSHOT if its log no longer has them.
6. **Why is resending after a reconnect safe?** Each op has an `op_id` of the form `<client_id>:<counter>`. The server remembers accepted op_ids per session. A duplicate is answered with the original OP again and is not applied a second time.
7. **Why exponential backoff with jitter?** It avoids hammering a server that is still down. The jitter spreads out many clients that all lost the server at the same moment (the thundering herd).
8. **What is TCP_NODELAY and why set it?** It disables Nagle's algorithm, which holds small segments until earlier data is ACKed. Combined with delayed ACK, that can add up to about 200 ms to cursor and stroke frames.
9. **How big transfers work.** A payload above 1 MiB (SNAPSHOT, HISTORY) is sent as `parts` frames tagged `{part, parts, xid}`. `PartAssembler` collects them in any order, ignores duplicates, and returns the merged payload only when every part has arrived.
10. **How does discovery work?** The client sends `{"magic": ...}` to 255.255.255.255, to each interface's directed broadcast address and to 127.0.0.1 on UDP 5051. The server unicasts back its name, port and room list. The client takes the IP from the sender address of the reply. It probes every 2 s and forgets a server after 6 s of silence.

---

## 3. Akula Sai Teja (CS24I1054): canvas and drawing tools

**Slides:** 18–21.

### Talking points (3–4 minutes)
1. **Your files** (slide 18). view.py, scene.py, tools.py, items.py, geometry.py, undo.py.
2. **Why QGraphicsScene.** It gives you a scene graph, hit-testing, zoom and pan, and PNG/SVG/PDF export.
3. **The one rule:** tools build ops and hand them to the controller, and the canvas never edits the board itself. `CanvasScene` keeps one item per object id and rebuilds only the ids that changed (`objects_changed`).
4. **Pen pipeline.** Mouse points go out as STROKE_LIVE previews at about 30 Hz (ephemeral, never logged). On pen-up the stroke is simplified with RDP at 0.6 screen px, rounded to 0.1, and committed as one `add` op, which is one undo step. Smart Ink calls `recognize_shape` if you hold the pen still.
5. **Objects** (slide 20). There are 8 types. IDs are `<user_id>-<counter>`. Images and PDF pages are sha256 blobs uploaded in 192 KiB chunks. A PDF import is a single batch op.
6. **Undo/redo** (slide 21). Stacks are per user. Undo submits the inverse as a new op. The filter rules decide what happens when someone else has edited the same object. Go through the example.

### Show on screen
- `src/lanboard/client/canvas/tools.py`: the freehand tool (`_simplified`, the commit in the release handler, the highlighter), the shape tools, the eraser.
- `src/lanboard/client/canvas/scene.py`: `sync`, `_on_objects_changed`, `_on_live_stroke`, `_on_cursor`.
- `src/lanboard/client/canvas/view.py`: `insert_image_file`, `import_pdf`, `zoom_fit` / `animate_to`, export.
- `src/lanboard/common/geometry.py`: `rdp`, `hit_test`, `recognize_shape`.
- `src/lanboard/common/undo.py`: `inverse`, `applicable`, `UndoManager.undo` / `redo` / `record`.

### Likely viva questions
1. **What does undo do when someone else changed the object?** Undo emits the inverse op filtered against the current board. For `update` it only restores keys that still hold *our* value, so another user's later change is kept. If an object we added was already erased, the entry is skipped and the next one is tried.
2. **Why is undo a new op instead of a rewind?** Rewinding would need every client to roll back the shared log. A new inverse op is ordered by the server like any other op, so every replica converges.
3. **Can I undo someone else's stroke?** No. Each user's stack contains only their own confirmed ops.
4. **What happens to redo after a new action?** It is cleared (standard branching).
5. **How do you keep stroke ops small?** Ramer–Douglas–Peucker with a tolerance of 0.6 screen pixels, coordinates rounded to 0.1 and flat `[x, y, ...]` lists. Measured: 121 points (1,851 B) become 26 points (499 B).
6. **Why can a client create object ids without the server?** The id is `<user_id>-<counter>`. The user_id is unique per server, so the combination is globally unique without a round trip.
7. **How are images shared?** The bytes are uploaded once as a content-addressed blob (sha256), with `BLOB_PUT` in 192 KiB chunks. The `add` op only carries the hash, and other clients fetch it with `BLOB_GET`.
8. **How does the eraser work?** It hit-tests strokes and shapes along the drag segment (`hit_test_segment`) and deletes everything it touched in one `delete` op.
9. **What is Smart Ink?** If you hold the pen still at the end of a stroke, `recognize_shape` looks at closure, corner count and area, and replaces the rough stroke with a clean ellipse, rectangle, line or arrow.
10. **Why doesn't the whole canvas redraw on each op?** The controller emits only the changed ids, and the scene rebuilds just those `QGraphicsItem`s.

---

## 4. Nithillakrishi P Y (CS24I1039): UI and collaboration features

**Slides:** 22–26.

### Talking points (3–4 minutes)
1. **Your files** (slide 22). main_window, chat, participants, history, network_panel / hud / inspector / chaos_panel, topbar, theme.
2. **Principle:** the UI mirrors permissions (`_update_permissions`), but the server enforces them.
3. **Chat and people** (slide 23). CHAT_SEND, then the server stamps the sender, time and id, then CHAT goes to everyone. The recent tail comes in JOINED. People shows roles, online state and hands, and the owner's context menu has role, kick and follow. Classroom kit: laser, VIEWPORT follow, raise hand, ping.
4. **Time-lapse** (slide 24). HISTORY_REQ returns HISTORY. Replay goes into a separate Board, so the live replica keeps syncing. "Restore to here" sends an owner-only `replace` op.
5. **Network Inspector and HUD** (slide 25). Every frame with direction, type, size, seq and chaos tag, plus decoded JSON. The HUD shows RTT, pending, totals and charts, and has the batching switch.
6. **Badge, toasts, dark mode** (slide 26). The four badge states and where they come from (`sync_status`). Toasts for events that should not interrupt. A full dark theme. Fonts are bundled and icons painted in code, so nothing is downloaded.

### Show on screen
- `src/lanboard/client/ui/main_window.py`: `_update_permissions`, `on_password_required`, `open_invite`, `_on_lock` / `_on_hand`.
- `src/lanboard/client/ui/chat.py` (ChatPanel), `participants.py` (`_menu`, `follow`, `set_spotlight`).
- `src/lanboard/client/ui/history.py`: `request`, the slider handler, restore.
- `src/lanboard/client/ui/inspector.py`, `hud.py`, `network_panel.py`, `chaos_panel.py`.
- `src/lanboard/client/ui/topbar.py`: `sync_text`. `theme.py`: `load_fonts`, `apply_theme`.

### Likely viva questions
1. **How is chat ordered and timestamped?** The server assigns the id and timestamp and broadcasts CHAT to everyone, so every client shows the same chronological list. New joiners get the tail in `JOINED.chat`.
2. **How do users know who joined or left?** The server broadcasts USER_JOINED / USER_LEFT (with a reason) and writes a system chat line. The client shows a toast and updates People.
3. **If I hack the client to enable the pen as a viewer, what happens?** The server rejects the op with OP_REJECT FORBIDDEN, and the client rolls back the optimistic change.
4. **How does follow-the-presenter work?** The presenter sends VIEWPORT {x, y, w, h}. The server relays it to the room, and followers animate their view to that rectangle. It is ephemeral and never logged.
5. **How does time-lapse not break live sync?** The preview renders a separate Board built from HISTORY. The confirmed replica keeps applying live OPs underneath, and "Back to live" just switches the canvas source.
6. **What does the green badge actually mean?** The last HASH checkpoint from the server matched the hash of our confirmed board at that seq.
7. **What does the Network Inspector capture?** Every inbound and outbound frame at the NetClient layer: time, direction, type, size on the wire, seq, the chaos tag and the decoded payload. Cursor, live and ping frames can be filtered out.
8. **Why toasts instead of dialogs?** Joins, exports and reconnects should not interrupt drawing. Only decisions such as a fatal disconnect or a password prompt are modal.
9. **How does the UI work offline?** Inter and JetBrains Mono are bundled and registered with `QFontDatabase`, and icons are painted by code. Nothing is fetched from the internet.
10. **What happens in the UI during a disconnect?** The badge turns red ("Offline · edits queued (n)"), a banner says it is reconnecting, drawing still works, and pending ops are counted in the status bar.

---

## 5. Live demo runbook (in person, internet off)

**Before the day**
- Build the executables (`pyinstaller packaging/lanboard.spec`) and copy `dist/LANBoard/` to all 3 laptops. Also keep a source checkout with `.venv` as a fallback.
- Charge the laptops and the phone. Carry a small switch and Ethernet cables as plan B.
- Each laptop: Windows: set the network profile to **Private** and allow `lanboard-server` / `lanboard-client` in Defender Firewall. macOS: allow incoming connections when prompted (ad-hoc `codesign -s -` avoids repeated prompts).

**Setup (about 3 minutes)**
1. Turn on a phone hotspot or plug the switch in. Connect all 3 laptops. **Turn mobile data off** on the hotspot phone, or confirm the internet is off.
2. On the server laptop: `lanboard-server --dashboard` (or `.venv/bin/python -m lanboard.server --dashboard`). Note the "Clients on this LAN can connect to: IP:5050" line. Accept the firewall prompt.
3. Laptop 1: `lanboard-client`, Host new room (server already running, so leave "Start a server" off), enter a name, then Create room. Click **Invite** to show the IP and room code.
4. Laptops 2 and 3: Join room, pick the server under **Nearby servers**, then the room, then Join.

**Show (about 6 minutes)**
- Draw on each laptop: pen, highlighter, shapes, text, sticky notes with votes, the laser pointer, an imported PDF slide. Point out live cursors and the live stroke preview.
- Chat from two laptops. Raise a hand. The owner makes one user a viewer (their tools grey out), locks the board, then unlocks it.
- Password room: the owner sets a password in Room settings, a new client joins and gets the prompt.
- Undo/redo with two users editing the same sticky note.
- **Chaos Lab** (Network tab) on one laptop: latency 200 ms, drop 20 %, duplicate 15 %. Keep drawing elsewhere. The inspector shows `dropped`, then `RESYNC`, then `OP_BATCH`, and the badge returns to **In sync**.
- **Cut the cable**: keep drawing offline (the badge shows queued edits). It reconnects automatically and the edits appear everywhere. For a real test, switch Wi-Fi off for 10 s and back on.
- History tab: play the time-lapse, "Restore to here".
- Server restart: Ctrl+C the server and start it again. Clients reconnect and the board is intact. Show the dashboard.

**If something goes wrong**
- *Discovery shows nothing:* type the server IP from the dashboard and port 5050 manually. Discovery is only a convenience. Common causes: UDP broadcast blocked (campus Wi-Fi client isolation), a firewall blocking UDP 5051, or laptops on different subnets. Switch to the hotspot or switch.
- *Cannot connect at all:* `ping <server-ip>` from a client. If that fails, it is the network (client isolation, wrong Wi-Fi). If ping works but connect fails, it is the firewall: allow the app, or set the profile to Private on Windows.
- *Port 5050 busy:* start with `--port 5052` and type that port on the clients.
- *A laptop shows a mismatch badge:* that is the self-heal at work; it fetches a snapshot by itself. If it ever sticks, leave and rejoin.
- *Fonts look different:* harmless. The app falls back to system fonts if the bundled ones are missing.
