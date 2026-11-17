# FEATURES — What LANBoard ships

Three groups: signature features (what sets LANBoard apart), the mandatory features from the brief, and extras from the brief's list of advanced features.

## Signature features: the network made visible
Most whiteboards hide the network. LANBoard puts it on screen.

| # | Feature | What it does | Why it matters |
|---|---|---|---|
| S1 | **Network Inspector** | A live, Wireshark-style panel of every frame this client sends and receives: time, direction, message type, size, seq, and a decoded JSON view. Filters by type. | Shows our application-layer protocol and framing working in real time. |
| S2 | **Chaos Lab** | Fault injection in the client: added latency and jitter, duplicated frames, dropped OP frames, and a "cut the cable" button. | Proves the spec's "delayed, duplicated or missing updates" handling *live*. Drops cause a seq gap, which triggers RESYNC. Duplicates are discarded. Delayed frames are re-ordered by seq. |
| S3 | **Consistency Verifier** | The server broadcasts a board hash every N ops. Each client compares it with its own replica and shows *In sync @ seq 1234* or heals itself with a snapshot. | Turns "consistent shared canvas" from a claim into something you can watch being checked. |
| S4 | **Live Network HUD** | RTT sparkline, msgs/s, bytes/s in and out, pending ops, and the live-stroke batching interval. A toggle switches batching off to compare. | Measured evidence for the performance rubric. |
| S5 | **Server Dashboard** | The server terminal shows a live table: rooms, users, RTT, bytes, queue depth, ops/s. | Shows the standalone server managing concurrency. |
| S6 | **Time-lapse Replay** | Replays the whole session from the op log as an animation, with a scrubber and "restore to here". | Comes almost free from our snapshot + op-log design. Very visual. |
| S7 | **Offline-first editing** | Keep drawing while disconnected. Ops queue up and replay on reconnect, and the server's op-id dedupe makes resends safe. | Reconnection and recovery go beyond the minimum. |

## Signature features: collaboration
| # | Feature | What it does |
|---|---|---|
| S8 | **Smart Ink** | Hold the pen still at the end of a stroke and a rough circle, rectangle, line or arrow snaps into a clean shape. |
| S9 | **PDF / slide annotation** | Import lecture PDFs. Each page becomes a shared image that everyone can annotate, sent as content-addressed chunked blobs with progress. |
| S10 | **Classroom kit** | Laser pointer (a fading trail that is never saved), *follow the presenter* (viewport sync), raise hand, "look here" ping. |
| S11 | **Authorship lens** | Hover to see who drew an object and when. "Spotlight user" dims everyone else's work. |
| S12 | **Dot voting** | Click a sticky note to vote. Counts are shown live, which is good for brainstorming. |

## Mandatory (from the brief)
Freehand, eraser, colours · real-time sync · server validation and ordering · consistent canvas · permissioned clear · save/restore · host and join by LAN IP + room code · participant list with join/leave notices · usernames, per-user colours, live cursors · late-join state sync · ordering and reliability · timeouts and reconnects · group chat with history and timestamps · state recovery · per-user undo/redo with defined multi-user semantics.

## Extras from the brief's list
Shapes (line, arrow, rect, ellipse) · text · sticky notes · images · select/move/recolour/delete · role-based permissions (owner/editor/viewer, lock, kick, password rooms) · version history · export PNG/SVG/PDF · presentation mode (S10) · offline editing (S7) · LAN auto-discovery (UDP broadcast).
