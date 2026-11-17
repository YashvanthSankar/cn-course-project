# LANBoard explanation video: scripts for all four members

One script per member, 3 to 4 minutes each (about 500 spoken words), written against the 10-slide deck (`gamma_10_slides.md`). Order: Yashvanth (opens) → Jeevanandham → Akula Sai Teja → Nithillakrishi (closes). Total about 15 minutes.

How to use: keep your face and name visible; follow the `[SLIDE: ...]` and `[SHOW CODE: ...]` cues; open the listed files at the given lines before recording. Say it in your own words if a sentence feels unnatural. The viva questions at the end of each script are for the in-person demo and are not read aloud.



<div style="page-break-before: always"></div>

# Explanation video script: Yashvanth S

- **Name:** Yashvanth S
- **Roll no.:** CS24I1029
- **Area:** Server core and state
- **Target length:** 3–4 minutes, including the opening of the whole video.
- **Spoken text:** 496 spoken words (cue lines, header and viva list excluded), about 3 min 50 s at 130 words per minute, plus a few seconds for each of the 4 code switches.
- **Slides covered (10-slide deck):** "LANBoard: a LAN-only collaborative whiteboard" (opens the video), "The problem and what we delivered", "Architecture: one server, many clients, one LAN", "Server core: one order for every edit", and the "Server crash" row of "Reliability and recovery".
- **Before recording:** open the four files below in the editor, at the line numbers given, so each switch takes one click. Line numbers were checked against the code on 2026-11-17.
- **On camera:** face and name visible the whole time. No live demo is needed.

Lines in *italics* are stage directions. Everything else is spoken.

---

## 1. Opening

`[SLIDE: LANBoard: a LAN-only collaborative whiteboard]`

Hello. I am Yashvanth S, roll number CS24I1029. LANBoard is a shared whiteboard that runs on the local network only, with no internet.

My area is the server core and state. Jeevanandham covers networking, Sai Teja the canvas, and Nithillakrishi the user interface.

---

## 2. The problem and what we delivered

`[SLIDE: The problem and what we delivered]`

The brief asks for several people to draw and chat on one canvas in real time, over a LAN, with every screen showing the same board. All five core modules are done, plus extras such as roles, password rooms and a time-lapse history.

---

## 3. Architecture

`[SLIDE: Architecture: one server, many clients, one LAN]`

One machine runs the server, and every laptop runs the client. All traffic goes over TCP port 5050. UDP port 5051 is only used to find servers.

Inside the server, each socket gets a `ClientConnection`, and each room is a `Room` object that acts as the sequencer. The server and every client run the same `Board.apply` code, so the same ops in the same order give the same board.

`[SHOW CODE: src/lanboard/server/server.py → ClientConnection.send_frame (lines 107–117), then ClientConnection._writer_loop (lines 122–142)]`

Each connection has a writer loop that awaits `drain()`, so it follows TCP flow control. Its queue holds at most 4000 frames, and `send_frame` never waits. If a slow client's queue fills, only that client is dropped as "lagging", and the room keeps going.

---

## 4. Server core: one order for every edit

`[SLIDE: Server core: one order for every edit]`

The rule is: the server's sequence number decides. Clients never apply each other's edits directly.

`[SHOW CODE: src/lanboard/server/room.py → Room.submit, lines 529–587]`

`Room.submit` checks each edit in order. Is the user in the room? Do they have permission? A viewer cannot edit. Are they under 120 ops per second? Is the op valid? Is it a duplicate `op_id`? A resent op is acknowledged again, never applied twice.

At line 569 the op gets the next seq and is applied. At line 572 it is written to disk. Only then, at line 580, is it broadcast to everyone, the sender included. There is no lock: the server runs on one asyncio thread, and this function has no `await`.

`[SHOW CODE: src/lanboard/common/model.py → Board.apply (line 251), then Board.hash (lines 327–339)]`

`Board.apply` is deterministic. An update to an erased object is ignored. Every 25 ops the server sends `Board.hash`, so each client can check its board matches.

*Back to the slide for the example.*

Alice turns a sticky note red and Bob turns it blue at the same moment. Each sees their own colour at once. The server gets Alice's op first, so hers is seq 101 and Bob's is 102.

On Alice's screen, 101 comes back with her own `op_id`. That echo is the acknowledgement. Then 102 arrives and the note turns blue. Bob's screen shows his pending blue on top until his own 102 is confirmed. Both end on blue, and the hashes match. No clocks are needed.

The server also enforces every access rule, and a room password never crosses the network.

---

## 5. Persistence: the "Server crash" row

`[SLIDE: Reliability and recovery]` *(point at the "Server crash" row only; Jeevanandham explains the rest of the table)*

`[SHOW CODE: src/lanboard/server/persistence.py → _AppendLog.append (lines 246–252), then atomic_write_bytes (lines 155–170)]`

Each accepted op is appended to `ops.jsonl` before it is broadcast, so killing the server loses nothing a client has seen. Every 50 ops or 10 seconds, a snapshot is written to a temporary file, synced, and swapped in with `os.replace`. On restart the server loads the snapshot and replays the log.

---

## 6. Hand-over

Next, Jeevanandham will explain our protocol and networking.

---

## If asked in the viva (not read aloud)

1. **How are concurrent operations ordered?**
   The room gives each accepted op `seq = room.seq + 1` in `Room.submit`, and every client applies ops strictly in seq order. For an update to the same key, the later seq wins.

2. **What is the acknowledgement for an op?**
   The broadcast itself. The server sends the OP to everyone, the sender included, and when the sender sees its own `op_id` come back, `_apply_confirmed` in `client/controller.py` removes it from `pending`.

3. **Why are there no locks on the server?**
   All room state is changed on one asyncio event-loop thread, and coroutines only switch at an `await`. `Room.submit` has no `await`, and the slow disk writes run through `asyncio.to_thread` on a copy of the data.

4. **What happens with a slow client?**
   Its writer blocks in `drain()` because of TCP flow control, so its 4000-frame queue fills up. `send_frame` then drops that client as "lagging"; it can resume, and nobody else waits.

5. **What if the server crashes mid-session?**
   Every accepted op was appended to `ops.jsonl` before it was broadcast. On restart `Room._load_board` loads `snapshot.json`, replays the ops after its seq, and skips a torn last line. (The append is flushed, not fsynced, so it survives a killed process; a power cut could lose the last few lines.)

6. **How is the room password protected?**
   The server stores only `scrypt(password, salt)`. Each connection gets a fresh nonce, the client sends `HMAC-SHA256(verifier, nonce)`, and the nonce is replaced after use, so a captured proof cannot be replayed.

7. **Can a client fake its user id or role?**
   No. The server assigns the user id and role at JOIN, stamps `user_id` on every OP it broadcasts, and forces the owner of every new object to the sender in `Board.normalise`.


<div style="page-break-before: always"></div>

# Explanation video script: Jeevanandham T

- **Name:** Jeevanandham T
- **Roll no.:** CS24I1035
- **Area:** Protocol and networking
- **Target length:** 3 to 4 minutes
- **Spoken word count:** 499 words (cue lines, header and viva list excluded), about 3 min 50 s at 130 words per minute
- **Slides covered:** "Protocol: our own message format over TCP" and "Reliability and recovery" (10-slide deck, `docs/scripts/gamma_10_slides.md`)

How to read this script: lines in square brackets are cues for what is on screen. Everything else is
spoken. Line numbers were checked against the code on 2026-11-17. Keep your face and name visible the
whole time. Add your own personal details in your own words where you want to.

---

## 1. Introduction

[SLIDE: Protocol: our own message format over TCP]

Hello, I am Jeevanandham T, roll number CS24I1035. My part of the project is protocol and networking:
how the client and server talk over the LAN, and how they recover when the network fails.

## 2. Why TCP

We chose TCP on port 5050 because it gives reliable, in-order delivery on each connection, with flow
control, and rebuilding that on UDP would gain nothing on a LAN. What TCP cannot cover is a connection
that dies, so our sequence numbers and op ids remove duplicates across reconnects. UDP port 5051 is used
only to discover servers, because only UDP can broadcast.

## 3. Frame format

[SLIDE: Protocol: our own message format over TCP, the frame layout line]

TCP is a byte stream with no message boundaries, so every message is framed: four bytes of length,
big-endian, one type byte, then the payload as JSON. Here is a real HELLO frame from our encoder. The
first four bytes are 00 00 00 45. Hex 45 is 69: one type byte plus 68 bytes of JSON. The fifth byte,
01, means HELLO. Then the JSON with the protocol version, app name, username and client id. 73 bytes in
total.

[SHOW CODE: src/lanboard/common/protocol.py → FrameDecoder.feed, lines 72–99]

One socket read can hold half a frame or several frames. `feed` adds the bytes to a buffer and reads
the length. If the frame is not complete, it waits for the next read. Otherwise it cuts the frame out
and parses the JSON. A length of zero, or above 8 megabytes, raises a ProtocolError and the connection
is closed.

## 4. Join flow

The client sends HELLO, the server answers CHALLENGE with a fresh nonce, and the client sends JOIN with
the room code and, for a password room, a proof. The server replies JOINED, then a SNAPSHOT of the whole
board, then live OPs.

## 5. Reliability and recovery

[SLIDE: Reliability and recovery]

[SHOW CODE: src/lanboard/client/controller.py → SessionController._on_op, lines 512–532]

Every OP carries a sequence number. If it is at or below what we have applied, it is a
duplicate, and we drop it. If it is the next one, we apply it. If it skips ahead, there is a gap, so we
buffer it and start a 400 millisecond timer.

[SHOW CODE: src/lanboard/client/controller.py → _on_gap_timeout and _request_resync, lines 576–587]

If the gap is still open, the client sends RESYNC with its last sequence number,
and the server replays the missing operations. Every 25 operations the server also broadcasts a board
hash. If ours differs, the client asks for a fresh snapshot.

A pulled cable sends no FIN or reset, so the client pings every 3 seconds, and after 12 seconds of
silence either side treats the link as dead. The client then reconnects with growing delays and some
random jitter.

[SHOW CODE: src/lanboard/client/net.py → _send_join, resume branch, lines 479–484]

The reconnect JOIN carries a resume token and the last sequence number. So the user keeps the same id
and only receives what was missed. Edits made offline are resent, and the server ignores any op id it
has already accepted. The server crash row is Yashvanth's part.

Finally, the Chaos Lab drops, duplicates and delays incoming frames on purpose, or cuts the cable, so we
can watch each of these recovery steps work.

## 6. Hand-over

Thank you. Next, Akula Sai Teja will explain the canvas and the drawing tools.

---

## If asked in the viva (not read aloud)

1. **Why not UDP for drawing?** We would have to rebuild acknowledgements, retransmission and ordering
   ourselves, and TCP already does that with negligible cost on a LAN. UDP is only used for the
   discovery broadcast, which TCP cannot do.
2. **How do you find message boundaries in TCP?** Every frame starts with a 4-byte big-endian length.
   `FrameDecoder.feed` buffers bytes until a whole frame is there, so half frames and several frames per
   read are both handled.
3. **What stops a huge or malicious frame?** A length of 0 or above `MAX_FRAME` (8 MiB) raises
   `ProtocolError` and the connection is closed. A single op whose OP frame would exceed 4 MiB gets
   `OP_REJECT TOO_LARGE`, and the connection stays up.
4. **What exactly happens when an OP is lost?** The next OP has a seq above `board.seq + 1`, so it is
   buffered and a 400 ms timer starts. If the hole is still open, the client sends `RESYNC{last_seq}` and
   gets an OP_BATCH of the missing ops, or a SNAPSHOT if that tail is gone or longer than 5000 ops.
5. **Why is resending after a reconnect safe?** Each op has an `op_id` of the form
   `<client_id>:<counter>`. The server keeps the accepted op_ids per session (up to 4096) and answers a
   repeat with the original OP instead of applying it again.
6. **What is TCP_NODELAY and why set it?** It turns off Nagle's algorithm, which holds small segments
   until earlier data is acknowledged. Together with delayed ACK that can add up to about 200 ms to
   cursor and stroke frames.
7. **How does discovery work?** The client sends `{"magic": "LANBOARD_DISCOVER_V1"}` to
   255.255.255.255, to each interface's directed broadcast address and to 127.0.0.1 on UDP 5051, every
   2 s. The server answers by unicast, the client takes the IP from the reply's sender address, and it
   forgets a server after 6 s of silence.


<div style="page-break-before: always"></div>

# Explanation video script: Akula Sai Teja

- **Name:** Akula Sai Teja
- **Roll no.:** CS24I1054
- **Area:** Canvas and drawing tools (including undo and redo)
- **Spoken length:** 493 words, about 3 min 48 s at 130 words per minute (cue lines, this header and the viva list are not counted)
- **Slides covered (10-slide deck, `docs/scripts/gamma_10_slides.md`):** "Canvas and drawing tools", "Undo and redo with many people editing"
- **No live demo in the video.** Show the slides and the source code only. Keep your face and name visible.

Lines in `[brackets]` are cues for the screen and are not spoken. Everything else is spoken. Function names and line numbers were checked against the code on 2026-11-17. If the code moves, search for the function name. Change the wording to your own wherever you like, and add what you personally did or tested.

---

`[ON CAMERA: face and name visible]`

Hello, I am Akula Sai Teja, roll number CS24I1054. My part of the project is the canvas and the drawing tools, including undo and redo.

---

`[SLIDE: Canvas and drawing tools]`

The canvas is built on Qt's QGraphicsScene, which already gives us hit-testing, zoom, pan and export. The board is a set of objects, each with an id, a type, a style and a geometry.

The main rule is that every tool produces ops. A tool never changes the board directly. It builds an op and gives it to the controller, which applies it on screen at once and sends it to the server.

---

`[SHOW CODE: src/lanboard/client/canvas/tools.py → PenTool.press (line 122), PenTool.move (line 133), PenTool.release (line 188)]`

Here is the pen. On press, it asks the controller for a new object id. The id contains the user id and a counter, so it is unique without asking the server.

While the mouse moves, points are drawn locally and passed to send_live. The controller sends them as one STROKE_LIVE preview every 33 milliseconds. The preview is relayed but never stored.

On release, the tool builds one stroke object and calls add_object. So one stroke is one add op and one undo step.

---

`[SHOW CODE: src/lanboard/client/canvas/tools.py → PenTool._simplified (line 206); src/lanboard/common/geometry.py → rdp (line 80)]`

Before the commit, _simplified runs the Ramer–Douglas–Peucker algorithm. It keeps the end points and every point further than 0.6 screen pixels from the simplified line, then rounds to one decimal place. For our test stroke, 121 points become 26, and the op shrinks from 1,851 bytes to 499.

Smart Ink is a pen mode: if you hold the pen still for 450 milliseconds, recognize_shape can replace the stroke with a clean line, arrow, rectangle or ellipse.

---

`[SHOW CODE: src/lanboard/client/canvas/scene.py → CanvasScene.sync (line 158)]`

CanvasScene keeps one graphics item per object id. The controller reports only the ids that changed, and sync rebuilds just those items.

Images and PDF pages are uploaded once, by SHA-256 hash, in 192 KiB chunks, and the object carries only the hash. A whole PDF goes out as one batch op, so it is one undo step.

---

`[SLIDE: Undo and redo with many people editing]`

Undo is per user and only touches your own work. It does not rewind the board. It sends the inverse of your last action as a new op, and the server orders it like any other edit.

---

`[SHOW CODE: src/lanboard/common/undo.py → inverse (line 34), applicable (line 62), UndoManager.undo (line 121)]`

inverse gives the opposite op. Add becomes delete, delete becomes restore, and an update swaps its new and previous values. A batch is inverted part by part.

Then applicable checks the inverse against the current board. If someone already erased the object I added, undo skips to my next entry. For an update, it only reverts keys that still hold my value.

Asha moves a sticky note, seq 50. Ravi changes its colour, seq 51. Asha presses Undo. Her client sends an update that sets only the position back, and the server gives it seq 52. On Ravi's screen, the note moves back and keeps Ravi's colour.

A new action clears the redo stack, and clear is undoable too.

---

`[ON CAMERA]`

That is the canvas and the drawing tools. Next, Nithillakrishi P Y will explain the user interface and the collaboration features.

---

## If asked in the viva (not read aloud)

1. **What does undo do if someone else changed the same object?** `applicable` filters the inverse against the current board. For an update it reverts only the keys that still hold our value, so the other user's later change stays. If our object was already erased, the entry is skipped and the next one is tried.
2. **Why is undo a new op and not a rewind?** A rewind would need every client to roll back the shared log. An inverse op gets its own seq from the server like any other op, so every replica applies it in the same order and stays identical.
3. **Can I undo someone else's stroke?** No. Each client's `UndoManager` records only the ops that this user submitted, so another user's work is never on your stack.
4. **How do you keep stroke ops small?** Live points are batched every 33 ms and never stored. The committed stroke is simplified with RDP at 0.6 screen pixels, rounded to 0.1 and sent as a flat `[x, y, ...]` list. For the one-second test stroke, 121 points (1,851 bytes) become 26 points (499 bytes).
5. **How can a client create object ids without the server?** `new_id` combines the user id, which the server never reuses, a short tag from the client id and a local counter, for example `u3-3f2a7`. That is unique without a round trip.
6. **How are images and PDFs shared?** The bytes are uploaded once as a SHA-256 content-addressed blob in 192 KiB `BLOB_PUT` chunks, and the server skips a blob it already has. The image object only carries the hash, and other clients fetch the bytes with `BLOB_GET`. A PDF becomes one image per page, submitted as one batch.
7. **Why doesn't the whole canvas redraw on each op?** `Board.apply` returns the ids it changed and the controller emits only those, so `CanvasScene.sync` rebuilds just those `QGraphicsItem`s.


<div style="page-break-before: always"></div>

# Explanation video script: Nithillakrishi P Y

- **Name:** Nithillakrishi P Y
- **Roll no.:** CS24I1039
- **Area:** UI and collaboration features
- **Spoken length:** 494 words, about 3 min 48 s at 130 words per minute (cue lines, header and viva list excluded)
- **Slides covered:** "UI and collaboration features", then "Performance, testing and the live demo" (this closes the whole video)
- **Deck:** the 10-slide deck in `docs/scripts/gamma_10_slides.md`

Lines in `[brackets]` are screen cues and are not read aloud. Face and name stay visible the whole time. There is no live demo in the video.

---

[CAMERA: face and name visible. Slide "UI and collaboration features" on screen.]

Hello. I am Nithillakrishi P Y, roll number CS24I1039. My part of the project is the UI and the collaboration features. I will also close the video for the team.

[SLIDE: UI and collaboration features]

The window never touches the socket or the board model. It only calls the session controller and reacts to its signals.

[SHOW CODE: src/lanboard/client/ui/connect_dialog.py → ConnectDialog._launch_server (line 890), then ui/server_launcher.py → ServerProcess.start (line 196)]

The connect screen has two modes. In Join, Nearby servers comes from UDP discovery, and recent rooms are remembered. Host mode can start a server on this laptop. It runs the server as a separate process, waits until the TCP port accepts, then connects to it.

[SHOW CODE: src/lanboard/client/ui/main_window.py → MainWindow._update_permissions (lines 744–765)]

This method greys out what your role cannot do. A viewer loses the drawing tools. Only the owner gets Lock and Room settings, and in People the owner can make someone an editor or viewer, or kick them. The server still checks every op, so a modified client gets OP_REJECT.

[SHOW CODE: src/lanboard/server/room.py → Room.on_chat_send (line 711)]

Chat goes through the server. The client sends CHAT_SEND. Here the server adds an id, the sender's name and a timestamp, then broadcasts CHAT to everyone. So every client shows the same order. Joins, leaves and role changes arrive as system lines.

[SHOW CODE: src/lanboard/client/ui/history.py → HistoryTimeline.board_at (line 80), then HistoryPanel.restore_here (line 268)]

Time-lapse reuses the server's op log. HISTORY_REQ returns a base board and every op since. HistoryTimeline replays them with the same Board.apply the replicas use, into a separate board. The canvas shows it read-only while the live board keeps syncing. Restore to here sends a normal replace op, accepted only from the owner.

[SLIDE: UI and collaboration features, point at "Network panel" and "Sync badge"]

The Network tab has a live HUD with RTT, pending ops, bytes and messages per second. Its inspector lists every frame sent and received, with the decoded JSON. The sync badge shows the controller's state. In sync means nothing is pending. Syncing means our ops await their echo. Healing repairs a gap. Mismatch means a hash check failed, so a fresh snapshot is fetched. Offline counts queued edits.

[SHOW CODE: packaging/lanboard.spec, lines 30–32 (commit c2d68e8)]

One commit is from my own account: "Fix Windows executable Qt library conflict". Qt on Windows uses the ICU library that ships with Windows. Building with Python 3.14, PyInstaller could bundle a different icuuc.dll that Qt cannot use, and the client would not start. The fix drops that file from both builds, on Windows only.

[SLIDE: Performance, testing and the live demo]

To close. By our encoder's numbers, batching live strokes saves about 63 percent of the bytes, and simplifying a finished stroke saves 73 percent. Round-trip time on loopback is under one millisecond. Eight bots made about 1,750 ops and every replica converged. Our 318 automated tests pass on Windows, macOS and Linux.

For the live demo the internet is off. We start a phone hotspot, then start the server, or use Host mode in the app. Three laptops join from Nearby servers. We draw, chat, lock the board and use a password room. Then we cut the cable in the Chaos Lab and watch everyone return to In sync.

That is LANBoard. Thank you from all four of us.

[END]

---

## If asked in the viva (not read aloud)

1. **How is chat ordered and timestamped?** The server gives each message an id and a timestamp in `Room.on_chat_send` and broadcasts CHAT to everyone, so every client shows the same order. A newcomer gets the last 200 messages in `JOINED.chat`.
2. **If I hack the client to enable the pen as a viewer, what happens?** The server rejects the op with OP_REJECT FORBIDDEN. The controller drops it from `pending` and rebuilds the view, so the local change disappears and a toast says why.
3. **How does follow-the-presenter work?** The presenter's canvas sends VIEWPORT {x, y, w, h}. The server relays it only from the owner or the presenter the owner picked, and followers animate their view to that rectangle. It is never logged, and panning yourself stops following.
4. **How does time-lapse not break live sync?** The preview is a separate `Board` built from the HISTORY payload. The confirmed replica keeps applying live OPs underneath, and "Back to live" just hands the canvas its live board again.
5. **What does the green badge mean exactly?** `_emit_sync` reports "synced" only when the client is joined, has no pending ops, no gap being healed and no HASH mismatch. A HASH checkpoint every 25 ops that does not match turns it to Mismatch and triggers a fresh snapshot.
6. **How does the Inspector cope with heavy traffic?** The slot only appends to a list; a 100 ms timer moves frames into the model in one batch, and the model keeps a 2000-frame ring buffer. The "Hide cursor / live / ping" switch filters out the high-rate presence and liveness frames.
7. **Why toasts instead of dialogs, and how does the UI work offline?** Joins, exports and reconnects should not interrupt drawing, so only decisions are modal. Inter and JetBrains Mono are bundled and registered with `QFontDatabase`, and every icon is painted with `QPainter`, so nothing is fetched from the internet.
