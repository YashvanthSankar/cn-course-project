# TODO — Plan, Owners & Schedule

**Deadline:** 15 Nov 2026, 23:55 IST (no extensions). **Internal target:** submit 15 Nov by 18:00.
**Core freeze:** 10 Nov. Everything after that is extras + deliverables.

## Status (6 Nov)

The application code is complete and pushed. It was written by Yashvanth with AI assistance (Claude); those
commits carry a `Co-Authored-By` trailer. The per-day plan further down is kept as the map of **who owns which
area**: each owner must study that code until they can explain and modify it, and present it in the video and
the viva (see `docs/PRESENTER_GUIDE.md`). Its boxes are left unticked on purpose. Ticking them would claim that
the listed member wrote that code.

Done and verified on one machine (276 automated tests, including end-to-end, chaos and convergence tests, plus
the bot stress tool):
- [x] Server, protocol, sync controller, canvas, UI redesign, persistence with full history, multi-part snapshots
- [x] Standalone server and client executables (PyInstaller, macOS)
- [x] Technical Documentation PDF (`docs/LANBoard_Technical_Documentation.pdf`, 42 pages, with requirements traceability)
- [x] PPT (`docs/LANBoard_Presentation.pptx`, 30 slides) and `docs/PRESENTER_GUIDE.md`
- [x] Windows / Linux / macOS builds (GitHub Actions, tests + smoke test green on all three)

Still needs the team:
- [ ] **Each member**: read your area's code and `docs/PRESENTER_GUIDE.md`, then make at least one real improvement or test yourself and commit it from your own account
- [ ] **All**: test on 3+ real machines over a hotspot or switch with the internet **off** (discovery, firewall prompts, cut cable)
- [ ] **All**: run the Windows build on a lab PC
- [ ] **All**: record the explanation video (each member explains their own area, face and name visible)
- [ ] Build the submission zip with `packaging/make_release.py`, test it on a fresh machine, then one member submits

## Team & ownership

Each member owns a vertical slice end-to-end (code + tests + their section of the docs, PPT and video).
Everyone commits their own work from their own machine and account. Reviews go through PRs.

| Member | Roll no. | Owns | Branch prefix |
|---|---|---|---|
| **Yashvanth S** | CS24I1029 | **Server core & state**: asyncio server, rooms, op log/`seq`, snapshot & resync, persistence, concurrency/back-pressure, packaging, integration lead | `server/` |
| **Jeevanandham T** | CS24I1035 | **Protocol & networking**: framing codec, message schema/validation, client `NetClient`, heartbeats, reconnect/resume, dedupe & gap handling, UDP discovery, metrics HUD, (opt.) TLS | `net/` |
| **Akula Sai Teja** | CS24I1054 | **Canvas & tools**: `QGraphicsScene` canvas, pen/eraser/colour/width, shapes, text, sticky notes, images, select/move, client undo/redo stacks, export PNG/SVG/PDF | `canvas/` |
| **Nithillakrishi P Y** | CS24I1039 | **UI & collaboration**: main window, host/join dialogs, participant list, chat panel, live cursors, roles/admin UI (lock, kick), version-history timeline, presentation mode, docs PDF + PPT lead | `ui/` |

Shared: `common/model.py` (board model + `apply(op)`) is co-owned by Yashvanth & Akula. The protocol
spec in `docs/TECH.md` is co-owned by Jeevanandham & Yashvanth.

## Schedule

### Day 0 — Thu 5 Nov ✅
- [x] Read spec, research prior art (`docs/RESEARCH.md`)
- [x] Idea, tech design, implementation plan, task split
- [ ] Everyone: clone repo, set up Python 3.12 venv, `pip install -r requirements.txt`, run hello-world PySide6 window

### Day 1 — Fri 6 Nov: foundations
- [ ] **Jeevanandham**: `common/protocol.py` framing (encode, incremental `FrameDecoder`) + unit tests (partial reads, multi-frame reads, oversize frames)
- [ ] **Jeevanandham**: `common/messages.py` message type constants + payload validators
- [ ] **Yashvanth**: `common/model.py` objects + `Board.apply(op)` for add/update/delete/restore/clear + tests
- [ ] **Yashvanth**: server skeleton: accept, HELLO handshake, per-client reader/writer tasks
- [ ] **Akula**: client canvas skeleton: `QGraphicsView` with pan/zoom, local freehand pen (no network yet)
- [ ] **Nithillakrishi**: main window layout: canvas centre, toolbar, right dock (participants + chat), status bar

### Day 2 — Sat 7 Nov: first end-to-end stroke
- [ ] **Yashvanth**: rooms (create/join by code), `seq` assignment, broadcast, OP_REJECT
- [ ] **Jeevanandham**: `client/net.py` QTcpSocket client (LowDelay), signals per message type
- [ ] **Akula**: stroke commit → `OP_SUBMIT`; render confirmed ops from the model; eraser (object hit-test → delete)
- [ ] **Nithillakrishi**: host/join dialog (server IP, port, username, room code, password)
- [ ] 🎯 **Milestone**: two clients on two laptops see each other's strokes

### Day 3 — Sun 8 Nov: sessions & chat
- [ ] **Yashvanth**: SNAPSHOT for late joiners; JOINED/USER_JOINED/USER_LEFT; per-user colours
- [ ] **Jeevanandham**: PING/PONG heartbeat, server timeout (15 s), client RTT measurement
- [ ] **Akula**: live stroke preview (STROKE_LIVE @30 Hz batched), colour & width picker
- [ ] **Nithillakrishi**: participant list (colour dot, role, "you"), chat panel with timestamp + username, join/leave system messages

### Day 4 — Mon 9 Nov: reliability
- [ ] **Jeevanandham**: auto-reconnect with backoff, `resume_token + last_seq`, resend pending ops; gap detection → RESYNC; dup drop
- [ ] **Yashvanth**: op-log tail vs full snapshot on resume; per-client `op_id` dedupe; bounded send queues; log compaction
- [ ] **Akula**: optimistic pending layer + rollback on reject; per-user undo/redo with conflict rules (TECH §5)
- [ ] **Nithillakrishi**: live cursors with name tags (CURSOR 20 Hz); "reconnecting…" banner

### Day 5 — Tue 10 Nov: persistence & access control → 🔒 CORE FREEZE
- [ ] **Yashvanth**: autosave JSON snapshot + JSONL op log, restore on server restart; SAVE/LOAD named boards
- [ ] **Jeevanandham**: password rooms: scrypt + HMAC challenge–response; rate limiting; input validation hardening
- [ ] **Nithillakrishi**: roles UI (owner/editor/viewer), lock board, kick, clear-board confirmation
- [ ] **Akula**: clear (permissioned), save/load from client menu
- [ ] **All**: run the full mandatory checklist below on 3+ machines, internet OFF

### Day 6–7 — Wed 11 / Thu 12 Nov: extras
- [ ] **Akula**: shapes (line, arrow, rect, ellipse), text, sticky notes, select/move/resize/recolour, image insert, export PNG/SVG/PDF
- [ ] **Jeevanandham**: UDP LAN discovery ("Nearby servers"), traffic/latency HUD, benchmark script (batching on vs off)
- [ ] **Nithillakrishi**: version-history timeline (scrub + restore), presentation/follow mode
- [ ] **Yashvanth**: HISTORY_RANGE, VIEWPORT relay, offline-edit queue replay, (opt.) TLS with Jeevanandham

### Day 8 — Fri 13 Nov: packaging & LAN testing
- [ ] **Yashvanth**: PyInstaller one-dir builds for server & client (macOS + Windows), launch scripts
- [ ] **All**: stress test (4+ clients scribbling, Wi-Fi pull test, server restart), fix bugs
- [ ] **Jeevanandham**: firewall notes (Windows Private profile, macOS ad-hoc codesign)

### Day 9 — Sat 14 Nov: deliverables
- [ ] **Nithillakrishi** (lead) + all: Technical Documentation PDF (architecture, message format, sockets, sync, concurrency, recovery, user guide)
- [ ] **Nithillakrishi** (lead) + all: PPT with screenshots, sequence diagrams, demo examples
- [ ] **All**: record the explanation video (each member: own slice, face + name visible)

### Day 10 — Sun 15 Nov: submit
- [ ] Fresh-machine test: download zip → run with internet OFF
- [ ] Zip: executables + source + PDF + PPT + video → one member submits by 18:00

## Mandatory checklist (✓ = verified by automated tests on one machine; still re-check on real LAN)
- [x] Freehand draw, erase, colour select
- [x] Real-time sync to all clients, low delay
- [x] Server validates & distributes ops; concurrent ops don't corrupt state
- [x] Consistent canvas for everyone
- [x] Clear (permissioned); save & restore
- [x] Host session; join via LAN IP + room code
- [x] Participant list; join/leave notifications
- [x] Usernames; per-user colours / cursors
- [x] Join/leave without disrupting others; late joiner gets current state
- [x] Defined ordering; delayed/duplicate/missing updates handled
- [x] Disconnect detection, timeouts, reconnection
- [x] Group chat, broadcast, chronological history with username + timestamp
- [x] Reconnecting client recovers latest state
- [x] Undo/redo with defined multi-user behaviour
- [ ] Works with internet disabled

## Git workflow
- `main` is always runnable. Work on `<prefix>/<feature>` branches → PR → one teammate reviews → squash/merge.
- Commit messages: `area: what changed` (e.g. `server: assign seq and broadcast ops`).
- Commit from your own account. Commit small and often: the history is part of the evidence for who did what.
