# Explanation video script: Jeevanandham T

- **Name:** Jeevanandham T
- **Roll no.:** CS24I1035
- **Area:** Protocol and networking
- **Target length:** 3 to 4 minutes
- **Spoken word count:** 495 words (cue lines, header and viva list excluded), about 3 min 48 s at 130 words per minute
- **Slides covered:** "Protocol: our own message format over TCP" and "Reliability and recovery" (10-slide deck, `docs/LANBoard_Presentation.pptx`)

How to read this script: lines in square brackets are cues for what is on screen. Everything else is
spoken. Line numbers were checked against the code on 2026-11-17. Keep your face and name visible the
whole time. Add your own personal details in your own words where you want to.

---

## 1. Introduction

[SLIDE: Protocol: our own message format over TCP]

Hello, I am Jeevanandham T, roll number CS24I1035. My part of the project is protocol and networking:
how the client and server talk over the LAN and recover from network failures.

## 2. Why TCP

We use TCP on port 5050. It gives reliable, in-order delivery with flow control, so rebuilding that on
UDP gains nothing on a LAN. TCP cannot handle a dead connection, so our sequence numbers and op ids
remove duplicates after reconnects. UDP port 5051 only finds servers, because only UDP can broadcast.

## 3. Frame format

[SLIDE: Protocol: our own message format over TCP, the frame layout line]

TCP does not mark where one message ends, so every message is framed: four bytes of length in
big-endian, meaning the biggest byte comes first, then one type byte, then the payload as JSON. Here is
a real HELLO frame. The first four bytes are 00 00 00 45. Hex 45 is 69: one type byte plus 68 bytes of
JSON. The fifth byte, 01, means HELLO. Then the JSON with the protocol version, app name, username and
client id. 73 bytes in total.

[SHOW CODE: src/lanboard/common/protocol.py → FrameDecoder.feed, lines 72–99]

One socket read can hold half a frame or several frames. `feed` buffers the bytes and reads the length.
If the frame is incomplete, it waits. Otherwise it cuts it out and parses the JSON. A length of zero, or
above 8 megabytes, raises a ProtocolError and closes the connection.

## 4. Join flow

The client sends HELLO, and the server answers CHALLENGE with a nonce, a one-time random value. The
client sends JOIN with the room code, plus a password proof if needed. The server replies JOINED, then a
SNAPSHOT of the whole board, then live OPs.

## 5. Reliability and recovery

[SLIDE: Reliability and recovery]

[SHOW CODE: src/lanboard/client/controller.py → SessionController._on_op, lines 512–532]

Every OP carries a sequence number, a running count. If it is at or below what we have applied, it is a
duplicate, and we drop it. If it is the next one, we apply it. If it jumps ahead, there is a gap, so we
buffer it and start a 400 millisecond timer.

[SHOW CODE: src/lanboard/client/controller.py → _on_gap_timeout and _request_resync, lines 576–587]

If the gap stays open, the client sends RESYNC with its last sequence number, and the server resends
what is missing. Every 25 operations the server also sends a board hash, a short fingerprint. If ours
differs, we ask for a fresh snapshot.

A pulled cable sends no goodbye message, so the other side cannot tell the link is dead. So the client
sends a PING every 3 seconds, and after 12 silent seconds either side drops the link. The client then
reconnects with growing delays plus jitter, meaning a small random wait.

[SHOW CODE: src/lanboard/client/net.py → _send_join, resume branch, lines 479–484]

The reconnect JOIN carries a resume token from the first JOINED, plus the last sequence number. So the
user keeps the same id and gets only the missed part. Edits made offline are resent, and the server
ignores any op id it already accepted. The server crash row is Yashvanth's part.

Finally, the Chaos Lab drops, duplicates and delays incoming frames on purpose, or cuts the cable, to
show each recovery step working.

## 6. Hand-over

Thank you. Next, Akula Sai Teja will explain the canvas and the drawing tools.

---

## If asked in the viva (not read aloud)

1. **Why not UDP for drawing?** With UDP we would have to rebuild acknowledgements, resending and
   ordering ourselves, and TCP already does all that at almost no cost on a LAN. We use UDP only for the
   discovery broadcast, because TCP cannot broadcast.
2. **How do you find message boundaries in TCP?** Every frame starts with a 4-byte big-endian length.
   `FrameDecoder.feed` keeps collecting bytes until a whole frame is there, so half frames and several
   frames in one read both work.
3. **What stops a huge or malicious frame?** A length of 0 or above `MAX_FRAME` (8 MiB) raises
   `ProtocolError` and closes the connection. If a single op would make an OP frame bigger than 4 MiB, the
   server answers `OP_REJECT TOO_LARGE` and the connection stays up.
4. **What exactly happens when an OP is lost?** The next OP has a seq above `board.seq + 1`, so the client
   keeps it aside and starts a 400 ms timer. If the hole is still open, it sends `RESYNC{last_seq}` and gets
   an OP_BATCH of the missing ops, or a SNAPSHOT if that tail is gone or longer than 5000 ops.
5. **Why is resending after a reconnect safe?** Each op has an `op_id` of the form
   `<client_id>:<counter>`. The server remembers the accepted op_ids per session (up to 4096), and for a
   repeat it sends back the original OP instead of applying it again.
6. **What is TCP_NODELAY and why set it?** It turns off Nagle's algorithm, which holds back small
   segments until earlier data is acknowledged. Together with delayed ACK, that can add up to about 200 ms
   to cursor and stroke frames.
7. **How does discovery work?** Every 2 s the client sends `{"magic": "LANBOARD_DISCOVER_V1"}` on UDP 5051
   to 255.255.255.255, to each interface's directed broadcast address and to 127.0.0.1. The server replies
   directly to that client, the client takes the IP from the reply's sender address, and it forgets a
   server after 6 s of silence.
