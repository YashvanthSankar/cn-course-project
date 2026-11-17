# Explanation video script: Yashvanth S

- **Name:** Yashvanth S
- **Roll no.:** CS24I1029
- **Area:** Server core and state
- **Target length:** 3–4 minutes, including the opening of the whole video.
- **Spoken text:** 498 spoken words (cue lines, header and viva list excluded), about 3 min 50 s at 130 words per minute, plus a few seconds for each of the 4 code switches.
- **Slides covered (10-slide deck):** "LANBoard: a LAN-only collaborative whiteboard" (opens the video), "The problem and what we delivered", "Architecture: one server, many clients, one LAN", "Server core: one order for every edit", and the "Server crash" row of "Reliability and recovery".
- **Before recording:** open the four files below in the editor, at the line numbers given, so each switch takes one click. Line numbers were checked against the code on 2026-11-17.
- **On camera:** face and name visible the whole time. No live demo is needed.

Lines in *italics* are stage directions. Everything else is spoken.

---

## 1. Opening

`[SLIDE: LANBoard: a LAN-only collaborative whiteboard]`

Hello, I am Yashvanth S, roll number CS24I1029. LANBoard is a shared whiteboard for a local network, with no internet.

My part is the server core and state. Jeevanandham covers networking, Sai Teja the canvas, and Nithillakrishi the user interface.

---

## 2. The problem and what we delivered

`[SLIDE: The problem and what we delivered]`

The brief: several people draw and chat live on one canvas over a LAN, all seeing the same board. All five core modules work, plus extras like roles, password rooms and time-lapse history.

---

## 3. Architecture

`[SLIDE: Architecture: one server, many clients, one LAN]`

One machine runs the server, and every laptop runs a client. All traffic uses TCP port 5050. UDP port 5051 is only for finding servers.

Each connection gets a `ClientConnection`, and each room is a `Room`, the sequencer that orders all edits, or ops. Server and clients share `Board.apply`, so the same ops in the same order give the same board.

`[SHOW CODE: src/lanboard/server/server.py → ClientConnection.send_frame (lines 107–117), then ClientConnection._writer_loop (lines 122–142)]`

Each connection's writer loop calls `drain()`, which waits until the network has sent the data. That respects TCP flow control. `send_frame` never waits; it just queues, up to 4000 frames. If a slow client's queue fills, only that client is dropped as "lagging".

---

## 4. Server core: one order for every edit

`[SLIDE: Server core: one order for every edit]`

The rule is: the server's sequence number, or seq, decides. It goes up by one for each accepted op. Clients never apply each other's edits directly.

`[SHOW CODE: src/lanboard/server/room.py → Room.submit, lines 529–587]`

`Room.submit` checks each edit. The user must be in the room and have permission, since a viewer cannot edit. They must stay under 120 ops per second. The op must be valid and not a repeat. A resent op, with the same `op_id`, is acknowledged again but never applied twice.

At line 569 the op gets the next seq and is applied, at 572 it is written to disk, and only then, at 580, is it broadcast to everyone, the sender too. No lock is needed. asyncio lets one thread handle many connections, switching only at an `await`, and this function has none.

`[SHOW CODE: src/lanboard/common/model.py → Board.apply (line 251), then Board.hash (lines 327–339)]`

`Board.apply` is deterministic: same input, same result. An update to an erased object is ignored. Every 25 ops the server sends `Board.hash`, a fingerprint of the board, so clients can check they match.

*Back to the slide for the example.*

Alice turns a sticky note red while Bob turns it blue. Each sees their own colour at once. The server gets Alice's op first, so hers is seq 101 and Bob's is 102.

Alice gets 101 back with her own `op_id`. That echo is her acknowledgement. Then 102 arrives and the note turns blue. Bob's blue stays on top as pending, meaning unconfirmed, until his 102 returns. Both end on blue, and the hashes match. No clocks are needed.

The server also checks every access rule, and room passwords never cross the network.

---

## 5. Persistence: the "Server crash" row

`[SLIDE: Reliability and recovery]` *(point at the "Server crash" row only; Jeevanandham explains the rest of the table)*

`[SHOW CODE: src/lanboard/server/persistence.py → _AppendLog.append (lines 246–252), then atomic_write_bytes (lines 155–170)]`

Each accepted op goes into `ops.jsonl`, the op log, before the broadcast, so killing the server loses nothing a client has seen. Every 50 ops or 10 seconds, a snapshot, a full copy of the board, is written to a temporary file, synced, and swapped in with `os.replace`. On restart it loads the snapshot and replays the log.

---

## 6. Hand-over

Next, Jeevanandham will explain our protocol and networking.

---

## If asked in the viva (not read aloud)

1. **How are concurrent operations ordered?**
   In `Room.submit` each accepted op gets the next number, `seq = room.seq + 1`, and every client applies ops strictly in seq order. If two ops change the same key, the later seq wins.

2. **What is the acknowledgement for an op?**
   The broadcast itself is the acknowledgement. When the sender sees its own `op_id` come back, `_apply_confirmed` in `client/controller.py` removes it from `pending`.

3. **Why are there no locks on the server?**
   All room state changes on one asyncio event-loop thread, which only switches tasks at an `await`, and `Room.submit` has none. Slow disk writes run through `asyncio.to_thread` on a copy of the data.

4. **What happens with a slow client?**
   TCP flow control makes its writer wait in `drain()`, so its 4000-frame queue fills up. `send_frame` then drops only that client as "lagging"; it can resume, and nobody else waits.

5. **What if the server crashes mid-session?**
   Every accepted op is in `ops.jsonl` before it is broadcast, so on restart `Room._load_board` loads `snapshot.json`, replays the ops after its seq and skips a torn (half-written) last line. The log is flushed but not fsynced, so a killed process loses nothing, but a power cut could lose the last few lines.

6. **How is the room password protected?**
   The server stores only `scrypt(password, salt)`, never the password itself. Each connection gets a fresh nonce, the client sends `HMAC-SHA256(verifier, nonce)`, and the nonce is replaced after use, so a captured proof cannot be replayed.

7. **Can a client fake its user id or role?**
   No. The server assigns the user id and role at JOIN, stamps `user_id` on every OP it broadcasts, and in `Board.normalise` sets the owner of every new object to the sender.
