# CONCEPT — LANBoard: LAN-only Collaborative Whiteboard

> Working name: **LANBoard**. Rename freely.

## One-line pitch
A standalone, internet-free, real-time collaborative whiteboard where one machine on a LAN hosts
rooms and any number of clients draw, chat, and recover from disconnects — built on our own
TCP application protocol with a server-ordered operation log.

## Problem (from the spec)
Multiple users on a LAN must share one consistent canvas in near real time, without internet,
through a central server using socket programming. See `docs/brief/`.

## Core idea in three sentences
1. **The server is the single source of order.** Every accepted edit gets a global sequence
   number (`seq`) and is broadcast to everyone (including the sender) in that order. Same ops in
   the same order → identical canvases. No OT/CRDT needed.
2. **The board is "snapshot + op log".** Late joiners and reconnecting clients get a snapshot plus
   only the ops they missed — so recovery is cheap and exact.
3. **Every edit is an object-level op** (`obj.add`, `obj.update`, `obj.delete`, …) on objects with
   stable IDs, which makes erase, undo/redo, shapes, sticky notes and history all the same mechanism.

## Why this design scores well
| Rubric item | How we hit it |
|---|---|
| Network architecture | Pure client–server over TCP/IP; server owns rooms, state, ordering. Zero internet deps. |
| Protocol design | Own length-prefixed framed protocol, documented message catalogue, versioned handshake, justified TCP choice. |
| Ordering & reliability | `seq` ordering, client `op_id` de-duplication, gap detection → resync, echo-as-ack, resend-on-reconnect. |
| Concurrency | asyncio single-threaded event loop per server → room state mutated without races; per-client bounded send queues (back-pressure). |
| Performance | Live strokes batched at ~30 Hz, point simplification on commit, TCP_NODELAY, live traffic/RTT HUD to *prove* it. |
| Security / access control | Room passwords (salted scrypt, challenge–response), owner/editor/viewer roles enforced **server-side**, lock board, kick. Optional TLS. |
| UI | Qt (PySide6) desktop app: canvas, toolbar, participant list, chat panel, session controls, live cursors. |
| Extras | Shapes, text & sticky notes, images, version-history timeline, export PNG/SVG/PDF, presentation/follow mode, LAN auto-discovery. |

## Feature scope

### Mandatory (must be rock-solid by 10 Nov)
- Freehand pen, eraser, colour + width picker, clear (permissioned), save/restore board
- Host room / join by server IP + room code; participant list; join/leave notices
- Usernames + per-user colours; live cursors with name tags
- Late-join state sync; reconnect recovery; heartbeat timeouts
- Concurrent editing without lost updates; defined ordering; dedupe/gap handling
- Group chat with username + timestamp, chronological history
- Per-user undo/redo with defined multi-user semantics

### Extras (ranked by marks-per-hour)
1. Live cursors + traffic/latency HUD (msgs/s, bytes/s, RTT)
2. Shapes: line, arrow, rectangle, ellipse; text boxes; sticky notes
3. Roles (owner/editor/viewer), lock board, kick, password rooms
4. LAN server discovery (UDP broadcast) — no IP typing
5. Version history timeline (scrub & restore) — nearly free from the op log
6. Export PNG / SVG / PDF; save/load `.lanboard` files
7. Select / move / resize / recolour objects
8. Image insert (paste / drag-drop)
9. Presentation mode (follow the presenter's viewport)
10. Offline editing: queue ops while disconnected, replay on reconnect
11. Optional TLS with pinned self-signed cert
12. Stretch: flowchart connectors that snap to shapes; multiple pages

## Non-goals
- Internet/cloud sync, web browser client, OT/CRDT merging, peer-to-peer mode.

## Demo story (for the in-person eval, internet OFF)
1. Start server on laptop A → it advertises on LAN.
2. Laptops B, C, D open client → server appears in "Nearby" list → join room `CN-2026` with password.
3. Everyone draws at once; cursors fly around; chat.
4. Pull the Wi-Fi on laptop C for 10 s while others draw → plug back → C catches up exactly.
5. Undo on B removes only B's last stroke; A's work remains.
6. Owner locks board → viewer can't draw (server rejects). Show traffic HUD with batching on/off.
7. Scrub version history; export PNG; restart server → board restored from disk.
