# IMPLEMENTATION — Code Layout, Modules & Build Plan

Read `docs/TECH.md` first (protocol and semantics). This file covers *where code goes* and *how each
piece is built and tested*. Owners are listed in `docs/TODO.md`.

## 1. Repository layout
```
cn-course-project/
├── README.md
├── requirements.txt          # runtime deps
├── requirements-dev.txt      # pytest, pytest-asyncio, pyinstaller
├── src/lanboard/
│   ├── common/
│   │   ├── protocol.py       # frame encode / incremental FrameDecoder, PROTO_VERSION, MAX_FRAME
│   │   ├── messages.py       # MsgType enum (u8), payload builders + validators
│   │   ├── model.py          # BoardObject, Board.apply(op) -> Change, tombstones, snapshot()/load()
│   │   ├── geometry.py       # RDP simplification, hit-testing helpers, bounds
│   │   └── config.py         # ports, timeouts, limits (single source of truth)
│   ├── server/
│   │   ├── __main__.py       # `python -m lanboard.server --port 5050 --data ./data [--tls]`
│   │   ├── server.py         # asyncio.start_server, ClientConnection (reader/writer tasks)
│   │   ├── room.py           # Room: members, roles, Board, op log, seq, chat, broadcast
│   │   ├── auth.py           # scrypt hashing, challenge nonces, HMAC verify, resume tokens
│   │   ├── persistence.py    # snapshot.json + ops.jsonl per room, compaction, named saves
│   │   ├── discovery.py      # UDP responder for LAN discovery
│   │   └── metrics.py        # counters (msgs/bytes per client/room)
│   └── client/
│       ├── __main__.py       # `python -m lanboard.client`
│       ├── net.py            # NetClient(QObject): QTcpSocket, FrameDecoder, signals, reconnect, ping
│       ├── discovery.py      # UDP broadcast probe -> list of servers
│       ├── controller.py     # BoardController: pending/confirmed model, undo/redo, op submit
│       ├── canvas/
│       │   ├── scene.py      # CanvasScene: object id -> QGraphicsItem, incremental updates
│       │   ├── view.py       # CanvasView: pan/zoom, cursor overlays, follow mode
│       │   ├── items.py      # StrokeItem, ShapeItem, TextItem, StickyItem, ImageItem
│       │   └── tools.py      # Pen, Eraser, Line, Arrow, Rect, Ellipse, Text, Sticky, Select
│       └── ui/
│           ├── main_window.py
│           ├── connect_dialog.py   # Nearby servers + manual IP, username, room, password, host/join
│           ├── toolbar.py          # tools, colour, width, undo/redo, clear
│           ├── participants.py     # list with colour, role, owner actions
│           ├── chat.py             # history + input
│           ├── history.py          # version timeline slider
│           └── hud.py              # traffic / RTT overlay
├── tests/
│   ├── test_protocol.py      # framing edge cases
│   ├── test_model.py         # apply/undo determinism, conflict rules
│   ├── test_room.py          # seq, dedupe, roles, reject
│   └── test_integration.py   # spins up server + N headless asyncio clients
├── tools/
│   ├── fake_clients.py       # load generator: N bots scribbling (stress & benchmark)
│   └── bench_traffic.py      # batching on/off comparison -> CSV for report
├── packaging/
│   ├── server.spec
│   └── client.spec
└── docs/                     # SPEC, IDEA, TECH, IMPLEMENTATION, TODO, RESEARCH, spec PDF
```

## 2. Module details

### 2.1 `common/protocol.py` (Jeevanandham)
- `encode(msg_type: int, payload: dict) -> bytes`: `struct.pack(">IB", len(body)+1, msg_type) + body`
  where `body = json.dumps(payload, separators=(",", ":")).encode()`.
- `class FrameDecoder`: `feed(data: bytes) -> list[tuple[int, dict]]`. Internal `bytearray` buffer;
  loops while ≥ 4 bytes and full frame available; raises `ProtocolError` on oversize/invalid JSON.
- Server side can use the same decoder (`await reader.read(65536)` → `feed`) so both ends share one
  tested implementation.
- Tests: byte-by-byte feeding, 3 frames in one chunk, a frame split across 3 chunks, oversize,
  bad UTF-8.

### 2.2 `common/model.py` (Yashvanth + Akula)
```python
@dataclass
class BoardObject:
    id: str; type: str; owner: str; z: int
    style: dict          # color, width, fill, font…
    geom: dict           # pts / x,y,w,h / x1,y1,x2,y2 / text / image_ref
    deleted: bool = False
    rev: int = 0         # seq of last change (for stale-update detection)

class Board:
    objects: dict[str, BoardObject]
    def apply(self, op: dict, seq: int) -> Change | None   # None = no-op (e.g. delete of tombstone)
    def inverse(self, op: dict) -> dict                    # for undo
    def snapshot(self) -> dict; @classmethod load(d) -> Board
```
- Pure Python, no Qt → used by server, client and tests.
- Property-based test: random op sequences applied on two Boards in the same order → equal snapshots.

### 2.3 Server (Yashvanth)
- `ClientConnection`: `reader_loop` (decode → dispatch), `writer_loop` (drain bounded `asyncio.Queue`),
  `last_seen` updated on any frame; a room-level `watchdog` task drops connections silent > 15 s.
- `Room.submit(conn, op_id, op)`:
  1. permission check (role, lock) → `OP_REJECT`
  2. dedupe: `op_id in conn.session.accepted_ids` → re-send the original `OP` (idempotent ack)
  3. validate shape/bounds → `seq += 1` → `board.apply` → append to log → broadcast `OP`
  4. every 200 ops: `persistence.append`; every 2 000 ops: snapshot + truncate log
- Sessions outlive connections: `Session(user_id, username, color, role, resume_token, accepted_ids)`
  is kept for 10 min after disconnect so a reconnect resumes as the same user.
- Broadcast never awaits a slow client: `queue.put_nowait`; `QueueFull` → drop that client.

### 2.4 Client networking (Jeevanandham)
- `NetClient(QObject)` signals: `connected`, `disconnected`, `message(int, dict)`, `rtt(float)`, `state(str)`.
- `QTcpSocket.setSocketOption(LowDelayOption, 1)`; `readyRead` → `FrameDecoder.feed`.
- Reconnect: `QTimer` backoff 0.5 s → 1 → 2 → 4 → max 5 s; on connect send HELLO + JOIN_ROOM with
  `resume_token` and `last_seq`; then resend all unacked pending ops (server dedupes).
- Discovery: `QUdpSocket` broadcast `{"t":"DISCOVER"}` to port 5051, collect replies for 1 s.

### 2.5 Controller & canvas (Akula)
- Two layers: **confirmed** `Board` (only server-ordered ops) and **pending** ops (local, unacked).
  Render = confirmed + pending overlay. On echo → pop from pending. On reject → drop + re-render.
- Undo stack holds `(op, inverse)` for *my confirmed* ops. `undo()` walks back skipping entries whose
  inverse is a no-op under current state (rules in TECH §5).
- Tools emit ops; never mutate the scene directly → one code path for local and remote changes.
- Live preview: pen collects points, a 33 ms `QTimer` flushes `STROKE_LIVE`; on release → RDP →
  `OP_SUBMIT add stroke`.
- Export: `QGraphicsScene.render` onto `QImage` (PNG), `QSvgGenerator` (SVG), `QPdfWriter` (PDF).

### 2.6 UI (Nithillakrishi)
- Main window: left toolbar, centre `CanvasView`, right dock tabs (Participants | Chat | History),
  status bar (connection state, room, role, RTT).
- Owner context menu on participant: make editor/viewer, kick. Toolbar lock toggle.
- Chat: `QListView` + model; each row `[HH:MM] username: text`, system messages in grey.
- History: slider over `seq`; preview builds a temp `Board` from snapshot + ops ≤ N (read-only);
  "Restore to here" (owner) → server op.

## 3. Testing strategy
| Level | What | How |
|---|---|---|
| Unit | framing, model apply/inverse, auth | `pytest` |
| Room logic | seq, dedupe, roles, compaction | `pytest-asyncio`, in-memory fake connections |
| Integration | 5 headless clients draw concurrently → identical snapshots; kill & resume a client mid-stream | `tests/test_integration.py` |
| Fault injection | delay/duplicate/drop frames (test-only proxy) → still converges | `tools/flaky_proxy.py` |
| LAN manual | 3–4 laptops, internet off, Wi-Fi pull test | checklist in TODO.md |
| Performance | msgs/s & bytes/s with batching on/off; RTT p50/p95 with 8 bots | `tools/bench_traffic.py` → charts for PPT |

## 4. Build & run
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m lanboard.server --port 5050          # host
python -m lanboard.client                      # each participant
pytest -q
pyinstaller packaging/server.spec && pyinstaller packaging/client.spec   # → dist/
```

## 5. Definition of done (per feature)
- Works across ≥ 2 machines on LAN with internet off
- Has a test (unit or integration) where feasible
- Server enforces it (never only the client) if it touches permissions
- Documented in TECH.md (protocol) and user guide notes
- Code commented (spec requires commented source)
