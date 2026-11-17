# IMPLEMENTATION — Code Layout, Modules & Build Plan

Read `docs/ARCHITECTURE.md` first (protocol and semantics). This file covers *where code goes* and *how each
piece is built and tested*. The area each team member presents is listed in `TEAM.md`.

## 1. Repository layout
```
cn-course-project/
├── README.md
├── pyproject.toml            # package metadata, entry points lanboard-server / lanboard-client
├── requirements.txt          # runtime deps (PySide6-Essentials/Addons, pypdfium2)
├── requirements-dev.txt      # pytest, pytest-asyncio, pyinstaller
├── src/lanboard/
│   ├── common/
│   │   ├── protocol.py       # encode(), incremental FrameDecoder, multi-part encode_parts / PartAssembler
│   │   ├── messages.py       # MsgType enum (u8), Err / Reason codes; docstring = message catalogue
│   │   ├── model.py          # Board (objects as dicts): normalise / apply(op) / snapshot / hash, validate_op
│   │   ├── undo.py           # per-user UndoManager, inverse() + applicable() filtering
│   │   ├── auth.py           # scrypt verifiers, salts, nonces, HMAC proofs, resume tokens
│   │   ├── geometry.py       # RDP simplification, point rounding, hit-testing helpers, bounds
│   │   └── config.py         # ports, timeouts, limits (single source of truth)
│   ├── server/
│   │   ├── __main__.py       # `python -m lanboard.server [--host] [--port 5050] [--data DIR] [--name]
│   │   │                     #   [--no-discovery] [--dashboard] [--tls-cert F --tls-key F] [-v]`
│   │   ├── server.py         # Server (listener, user-id allocation, housekeeping/watchdog),
│   │   │                     #   ClientConnection (reader/writer tasks, handshake)
│   │   ├── room.py           # Room (sequencer: op pipeline, roles, chat, blobs, saves, history), Session
│   │   ├── persistence.py    # Storage / RoomStore: snapshot.json + ops.jsonl + history archive, saves, blobs
│   │   ├── discovery.py      # UDP responder for LAN discovery, local address helpers
│   │   └── dashboard.py      # live terminal dashboard (--dashboard)
│   ├── client/
│   │   ├── __main__.py       # `python -m lanboard.client`
│   │   ├── net.py            # NetClient(QObject): QTcpSocket, FrameDecoder, handshake, reconnect, ping
│   │   ├── discovery.py      # DiscoveryBrowser: UDP broadcast probe -> list of servers
│   │   ├── controller.py     # SessionController: confirmed board + pending view, undo/redo, op submit
│   │   ├── chaos.py          # FaultInjector (Chaos Lab): delay/jitter/duplicate/drop inbound frames
│   │   ├── metrics.py        # traffic counters + RTT for the Network HUD
│   │   ├── canvas/
│   │   │   ├── scene.py      # CanvasScene: object id -> QGraphicsItem, incremental updates
│   │   │   ├── view.py       # CanvasView: pan/zoom, cursor overlays, follow mode, board theme, grid
│   │   │   ├── items.py      # StrokeItem, ShapeItem, TextItem, StickyItem, ImageItem + overlay items
│   │   │   ├── tools.py      # Pen, Highlighter, Eraser, Shape (line/arrow/rect/ellipse), Text, Sticky,
│   │   │   │                 #   Select, Laser, Pan
│   │   │   └── minimap.py    # Minimap and ZoomControls overlays
│   │   └── ui/               # main_window, connect_dialog, server_launcher, topbar, toolbar, side_panel,
│   │                         #   participants, chat, history, network_panel, hud, inspector, chaos_panel,
│   │                         #   dialogs, overlays, widgets, theme
│   └── tools/
│       └── bot.py            # headless bot client + stress test (`python -m lanboard.tools.bot`)
├── tests/                    # conftest.py + test_auth, test_canvas, test_controller, test_convergence,
│                             #   test_demo_robustness, test_e2e_client, test_geometry, test_integration,
│                             #   test_model, test_net_client, test_protocol, test_server, test_ui_smoke,
│                             #   test_undo
├── packaging/
│   ├── lanboard.spec         # PyInstaller: both executables into dist/LANBoard/
│   ├── server_entry.py, client_entry.py
│   ├── smoke_test.py         # runs the frozen bundle (server + headless client + bots)
│   └── make_release.py       # assembles the submission zip
├── .github/workflows/build.yml   # Windows/Linux/macOS: tests -> build -> smoke test -> artifact
└── docs/                     # technical documentation (PDF + source), presentation, CONCEPT, FEATURES, RESEARCH, ARCHITECTURE, IMPLEMENTATION, CLIENT_API, brief/ (PDF)
```

## 2. Module details

### 2.1 `common/protocol.py` (Jeevanandham)
- `encode(msg_type: int, payload: dict) -> bytes`: `struct.pack(">IB", len(body)+1, msg_type) + body`
  where `body = json.dumps(payload, separators=(",", ":")).encode()`.
- `class FrameDecoder`: `feed(data: bytes) -> list[tuple[int, dict]]`. Internal `bytearray` buffer;
  loops while ≥ 4 bytes and full frame available; raises `ProtocolError` on oversize/invalid JSON.
- The server uses the same decoder (`await reader.read(READ_CHUNK)` → `feed`) so both ends share one
  tested implementation.
- Tests: byte-by-byte feeding, 3 frames in one chunk, a frame split across 3 chunks, oversize,
  bad UTF-8.

### 2.2 `common/model.py` + `common/undo.py` (Yashvanth + Akula)
```python
# an object is a plain dict:
#   {id, type, owner, z, style:{…}, geom:{pts | x,y,w,h | x1,y1,x2,y2 | text | blob},
#    off:[dx,dy], deleted:bool, rev:int (seq of its last change)}
class Board:
    objects: dict[str, dict]; seq: int
    def normalise(self, op: dict, user_id: str) -> dict    # e.g. clear -> delete{ids, clear:true}
    def apply(self, op: dict, seq: int | None = None) -> set[str]   # ids changed (empty = no-op)
    def snapshot(self) -> dict; @classmethod from_snapshot(snap) -> Board
    def copy(self) -> Board; def hash(self) -> str
def validate_op(op) -> None                                # raises InvalidOp

# common/undo.py
def inverse(op) -> dict | None                             # None = not undoable (replace)
def applicable(op, board) -> dict | None                   # filter an inverse against the board
class UndoManager: record(op); undo(board); redo(board)
```
- Pure Python, no Qt → used by server, client and tests.
- Property-based test: random op sequences applied on two Boards in the same order → equal snapshots.

### 2.3 Server (Yashvanth)
- `ClientConnection`: `run` (read `READ_CHUNK` → decode → `dispatch`), `_writer_loop` (drain bounded `asyncio.Queue`),
  `last_seen` updated on any frame; the server's housekeeping task (several times per
  `CLIENT_TIMEOUT`) drops connections silent > 12 s (`CLIENT_TIMEOUT`).
- `Room.submit(session, op_id, op)`: joined → permission (role, lock, clear/replace rights) →
  rate limit → validate → dedupe (`op_id in session.accepted` → re-send the original `OP`,
  idempotent ack) → normalise → size check (`MAX_OP_BYTES`) → `seq += 1`, `board.apply` →
  log → broadcast `OP` (+ `HASH` every 25 ops).
- Persistence: **every** accepted op is appended to `ops.jsonl` before it is broadcast; the board
  is snapshotted (and `ops.jsonl` compacted into `history.jsonl`) every `AUTOSAVE_EVERY_OPS` (50)
  ops, or by the housekeeping task once `AUTOSAVE_INTERVAL` (10 s) has passed since the last
  snapshot and at least one op is unsaved.
- Sessions outlive connections: `Session(user_id, username, color, role, resume_token, accepted, …)`
  is kept for `SESSION_TTL` (10 min) after disconnect so a reconnect resumes as the same user.
- Broadcast never awaits a slow client: `queue.put_nowait`; `QueueFull` → drop that client.

### 2.4 Client networking (Jeevanandham)
- `NetClient(QObject)` signals: `state_changed(str)`, `joined`, `message(int, dict)`, `error(str, str)`,
  `password_required(str, bool)`, `rtt_changed(float)`, `frame_logged`, `stats` (see CLIENT_API.md).
- `QTcpSocket.setSocketOption(LowDelayOption, 1)`; `readyRead` → `FrameDecoder.feed`.
- Reconnect: `QTimer` backoff 0.5 s → 1 → 2 → 4 → max 5 s (+ jitter); on connect send HELLO, then
  JOIN with `resume_token` and `last_seq`; the controller then resends all unacked pending ops
  (server dedupes).
- Heartbeat: `PING {t, rtt}` every `PING_INTERVAL` (3 s); RTT from the echoed `t` in `PONG`.
- Discovery (`DiscoveryBrowser`): `QUdpSocket` sends `{"magic":"LANBOARD_DISCOVER_V1"}` to UDP 5051
  (limited + directed broadcast + 127.0.0.1) every 2 s; a server not heard from for 6 s is dropped.

### 2.5 Controller & canvas (Akula)
- Two layers: **confirmed** `Board` (only server-ordered ops) and **pending** ops (local, unacked).
  Render = confirmed + pending overlay. On echo → pop from pending. On reject → drop + re-render.
- `UndoManager` records *my* ops when they are submitted (the normalised op). `undo()` walks back,
  skipping entries whose filtered inverse is empty under the current view (rules in ARCHITECTURE.md §5).
- Tools emit ops; never mutate the scene directly → one code path for local and remote changes.
- Live preview: pen collects points, a 33 ms `QTimer` flushes `STROKE_LIVE`; on release → RDP →
  `OP_SUBMIT add stroke`.
- Export: `QGraphicsScene.render` onto `QImage` (PNG), `QSvgGenerator` (SVG), `QPdfWriter` (PDF).

### 2.6 UI (Nithillakrishi)
- Main window: top bar (room, consistency badge, people, session actions), floating tool palette
  and style bar over the centre `CanvasView` (+ minimap and zoom controls), collapsible right side
  panel with tabs People | Chat | History | Network.
- Owner context menu on participant: make editor/viewer, kick. Lock/unlock via the "Lock board"
  action or the room settings dialog.
- Chat: `QListView` + `ChatModel` with a bubble delegate; system messages styled separately.
- History: slider over `seq`; preview builds a temp `Board` from snapshot + ops ≤ N (read-only);
  "Restore to here" (owner) → `replace` op.

## 3. Testing strategy
| Level | What | How |
|---|---|---|
| Unit | framing, model apply/inverse, auth | `pytest` |
| Unit (client) | controller sync rules, NetClient/chaos/metrics/discovery, canvas, UI smoke | `tests/test_controller.py`, `test_net_client.py`, `test_canvas.py`, `test_ui_smoke.py` (offscreen Qt) |
| Room logic | seq, dedupe, roles, compaction | `tests/test_server.py` (fake connections, no sockets) |
| Integration | real TCP server + headless clients; real NetClient + SessionController | `tests/test_integration.py`, `test_e2e_client.py` |
| Convergence / faults | 6 bots with random concurrent ops, link loss, offline edits, resume → identical board hashes | `tests/test_convergence.py`; manual: Chaos Lab (`client/chaos.py`) |
| LAN manual | 3–4 laptops, internet off, Wi-Fi pull test | checklist in the technical documentation, section 13 |
| Load | N bots drawing; reports convergence | `python -m lanboard.tools.bot --bots 8 --rate 20 --seconds 30` |
| Frozen bundle | server + headless client + bots against `dist/LANBoard` | `packaging/smoke_test.py` (also in CI) |

## 4. Build & run
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m lanboard.server --port 5050          # host
python -m lanboard.client                      # each participant
pytest -q
pyinstaller --noconfirm packaging/lanboard.spec   # → dist/LANBoard/{lanboard-server,lanboard-client}
python packaging/smoke_test.py                    # check the frozen bundle
python packaging/make_release.py                  # → release/CS24I1029_CN_PROJECT.zip
```

## 5. Definition of done (per feature)
- Works across ≥ 2 machines on LAN with internet off
- Has a test (unit or integration) where feasible
- Server enforces it (never only the client) if it touches permissions
- Documented in ARCHITECTURE.md (protocol) and user guide notes
- Code commented (spec requires commented source)
