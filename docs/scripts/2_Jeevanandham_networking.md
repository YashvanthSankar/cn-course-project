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
