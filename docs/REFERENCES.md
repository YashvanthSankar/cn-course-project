# REFERENCES — Open-Source Repos to Study

A reading list for specific sub-problems. **We write all our own code.** Nothing here is forked or
copied. Repos marked GPL or *no license* are for reading only. Checked on GitHub, 5 Nov 2026.
High-level product research is in [`RESEARCH.md`](RESEARCH.md).

> ⚠️ **Academic-integrity note.** Two repos below (bhanu07052007/LAN-Collaborative-Whiteboard and
> wxxxh-k/CollabBoard) were pushed on 1–2 Nov 2026 with feature lists that match this brief almost
> exactly. They are probably other teams' submissions for the same assignment. **Do not open their
> source.** Our design is already fixed in `ARCHITECTURE.md`, and any similarity would look bad. They are
> listed only so we know they exist.

## Study first, by owner

| Owner | Repo | Read these | License |
|---|---|---|---|
| **Yashvanth** (server core) | [Octovia7/collab-whiteboard](https://github.com/Octovia7/collab-whiteboard) (TS) | `docs/system-design.md`, `docs/algorithms.md`, `packages/server/src/ws/Room.ts`, `persistence/OpWriter.ts`, `Snapshotter.ts` | none → read only |
| **Yashvanth** (server core) | [Anura1401/Collaborative_whiteboard](https://github.com/Anura1401/Collaborative_whiteboard) (TS) | `packages/shared/src/applyOp.ts`, `apps/server/src/util/serialQueue.ts`, `packages/sim/` (fault-injection simulator idea) | no LICENSE file → read only |
| **Jeevanandham** (protocol/net) | [lucas-lima-s/tcp-ip-connector](https://github.com/lucas-lima-s/tcp-ip-connector) | `src/tcpconn/framing.py`, `heartbeat.py`, `backoff.py`, `tests/test_short_reads.py`, `tests/test_reconnect.py` | MIT |
| **Jeevanandham** (protocol/net) | [bhowiebkr/client-server-socket-example](https://github.com/bhowiebkr/client-server-socket-example) | `client.py`: QTcpSocket signal wiring | Apache-2.0 |
| **Akula** (canvas/tools) | [rbreu/beeref](https://github.com/rbreu/beeref) | `beeref/scene.py`, `selection.py`, `commands.py`, `view.py`, `fileio/export.py`, `tests/` | GPL-3.0 → read only |
| **Akula** (canvas/tools) | [Taron686/drawsvg-ui](https://github.com/Taron686/drawsvg-ui) (PySide6) | `src/items/shapes/*`, `src/scene_codec.py`, `src/export_renderer.py` | GPL-2.0 → read only |
| **Nithillakrishi** (UI/collab) | [jchanvfx/NodeGraphQt](https://github.com/jchanvfx/NodeGraphQt) | `widgets/viewer.py` (zoom/pan/rubber-band), `base/model.py` vs `qgraphics/*` (model/view split) | MIT |
| **Nithillakrishi** (UI/collab) | [VedDevs/InkFlow](https://github.com/VedDevs/InkFlow) (PySide6) | `ui/canvas.py`, `tools/base_tool.py`: a tool-registry pattern in 4 files | none → read only |

## Full list by topic

### Python asyncio servers: framing, rooms, heartbeats
- [lucas-lima-s/tcp-ip-connector](https://github.com/lucas-lima-s/tcp-ip-connector) (MIT): uses 4-byte length + kind byte, the same as our framing. Has an incremental decoder, heartbeats, and jittered exponential backoff.
- [7WebPages/snakepit-game](https://github.com/7WebPages/snakepit-game) (Unlicense): `simple/game_loop_*.py` shows six asyncio loop designs, each better than the last. Useful for explaining "one event loop, no locks."
- [Ganapati/Simple-Game-Server](https://github.com/Ganapati/Simple-Game-Server) (GPL-2.0): `rooms.py`, whose error taxonomy (RoomFull, RoomNotFound) maps onto our `ERROR {code}` list.
- [johnandersen777/asyncirc](https://github.com/johnandersen777/asyncirc) (MIT): asyncio IRC-style rooms (join/leave) with tests.
- Gists: [Cartroo asyncio chat room](https://gist.github.com/Cartroo/063f0c03808e9622d33b41f140a63f6a) and [jcrist readexactly framing](https://gist.github.com/jcrist/d7271415011cdc528ba82d2e5f328808).

### Qt client networking & LAN discovery
- [bhowiebkr/client-server-socket-example](https://github.com/bhowiebkr/client-server-socket-example) (Apache-2.0): a QTcpSocket/QTcpServer pair. The author explains why Qt networking beat threads, the same argument as our ARCHITECTURE.md.
- [CabbageDevelopment/qasync](https://github.com/CabbageDevelopment/qasync) (BSD-2): asyncio on the Qt loop. Fallback only. We use QTcpSocket.
- [python-zeroconf/python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) (LGPL-2.1+): mDNS alternative to our UDP broadcast. We keep our own broadcast because it is simpler and makes a better CN write-up.
- [jholtmann/ip_discovery](https://github.com/jholtmann/ip_discovery) (no license): 30-line UDP broadcast discovery that shows `SO_BROADCAST`/`SO_REUSEADDR` setup.

### PySide/PyQt canvas patterns
- [rbreu/beeref](https://github.com/rbreu/beeref) (GPL-3.0): the best-tested QGraphicsScene app in Python. Covers selection, rubber-band, QUndoCommand subclasses, and export.
- [jchanvfx/NodeGraphQt](https://github.com/jchanvfx/NodeGraphQt) (MIT): viewer zoom-to-cursor, and a serialisable model kept separate from the items.
- [Taron686/drawsvg-ui](https://github.com/Taron686/drawsvg-ui) (GPL-2.0): one item class per shape type, a scene↔JSON codec, autosave/recovery.
- [Artificial-Sweetener/CuteCanvas](https://github.com/Artificial-Sweetener/CuteCanvas) (GPL-3.0): very large. Only read the `composition/edit_history.py` design and the tablet-pen example.
- [VedDevs/InkFlow](https://github.com/VedDevs/InkFlow) (no license): a minimal tool registry.
- [bootchk/freehandTool](https://github.com/bootchk/freehandTool) (no license, Qt4): an idea for an incremental freehand smoothing pipeline. We will write our own Chaikin/Catmull-Rom smoothing. The algorithm reference is perfect-freehand (MIT, TS).

### Op log, snapshots, multi-user undo (any language)
- [Octovia7/collab-whiteboard](https://github.com/Octovia7/collab-whiteboard) (no license, TS) implements our model:
  - server `seq`, with the log written before the ack
  - snapshot pruning and tombstone garbage collection
  - `lastSeq` catch-up on reconnect
  - per-user inverse-op undo with "skip if overwritten"
  - an offline queue and stroke coalescing, plus a convergence test over 500 seeds
- [Anura1401/Collaborative_whiteboard](https://github.com/Anura1401/Collaborative_whiteboard) (no LICENSE file, TS) has:
  - a shared `applyOp` used by both client and server
  - optimistic apply with rollback on NACK
  - a lossy-channel simulator that checks invariants, a good idea for our pytest
- [Operational-Transformation/ot.py](https://github.com/Operational-Transformation/ot.py) (MIT): the client state machine (Synchronized → AwaitingConfirm → AwaitingWithBuffer) matches our "pending until echo" layer.
- [y-crdt/pycrdt](https://github.com/y-crdt/pycrdt) (MIT): its `UndoManager` scoped to local origin is the CRDT version of our per-user undo. Reading only.

### Python socket whiteboards (baselines)
- [punitkumar4871/RealTimeWhiteboard_using_python](https://github.com/punitkumar4871/RealTimeWhiteboard_using_python) (MIT): a simple broadcast server, no framing and no tests. Treat it as the baseline we beat.
- [Meeraja-K/Collaborative-Whiteboard](https://github.com/Meeraja-K/Collaborative-Whiteboard), [mschandana16/collaborative-whiteboard-CN](https://github.com/mschandana16/collaborative-whiteboard-CN) (no license): two-file Tkinter CN projects. The only thing worth reading is the minimal TLS server.
- [kevinjacobson/networked-p2p-whiteboard](https://github.com/kevinjacobson/networked-p2p-whiteboard) (MIT, Py2/pygame, 2009): historical interest only.
- ⚠️ bhanu07052007/LAN-Collaborative-Whiteboard and wxxxh-k/CollabBoard: likely concurrent course submissions. **Don't read them** (see the note at the top).

## Ideas we're adopting from this pass
1. **Deterministic fault-injection simulator** in tests: N fake clients talking through a channel that delays, duplicates, and drops messages, then checking that all snapshots are identical. (Idea from Anura1401 `packages/sim` and Octovia7's convergence test.) → `tests/test_convergence.py`
2. **Short-read tests** for the frame decoder (feed byte by byte, several frames per read). (From tcpconn.)
3. **Jittered exponential backoff** for reconnect.
4. **Serialisable model kept separate from QGraphicsItems**: confirms our `common/model.py` ↔ `CanvasScene` split.
5. **Client sync as an explicit state machine** (Synchronized / AwaitingConfirm / Buffering), drawn as a diagram in the docs and PPT.
6. **Our own stroke smoothing** (Chaikin or Catmull-Rom, ~30 lines) applied when a stroke is committed.

## Libraries we may depend on (credit in README if used)
| Library | License | Decision |
|---|---|---|
| PySide6 | LGPL-3.0 | ✅ used |
| PyInstaller | GPL-2.0 + bootloader exception | ✅ used (build only) |
| pytest, pytest-asyncio | MIT / Apache-2.0 | ✅ used (dev only) |
| qasync | BSD-2 | ⏸ fallback only |
| python-zeroconf | LGPL-2.1+ | ⏸ optional, not by default |
| pycrdt | MIT | ❌ not needed (we use an ordered op log) |
