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
