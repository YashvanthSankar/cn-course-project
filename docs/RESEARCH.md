# RESEARCH — Prior Art & Design Rationale

We studied existing whiteboards **for design ideas only**. No code from any of these projects is used
in LANBoard. Sources were checked on 5 Nov 2026.

## Survey
| Product | License | Sync approach | Multi-user undo |
|---|---|---|---|
| Drawpile | GPL-3.0 | Thin TCP server, server-ordered append-only command log, binary length-prefixed messages | Per-user, via undo-point markers |
| Excalidraw | MIT | WebSocket relay; per-element version + nonce tie-break; `isDeleted` tombstones | Undo stack cleared on remote update (described as "subpar" by its authors) |
| tldraw sync | tldraw SDK license (not OSI) | Server-authoritative push/pull/rebase with a monotonic server clock | Local history, remote changes merged separately |
| WBO | AGPL-3.0 | Socket.IO relay, server-side board persisted, reader/editor/moderator roles | Not provided |
| cracker0dks/whiteboard | MIT | WebSocket relay, optional access token | Per-user undo/redo |
| Figma / FigJam | Proprietary | Central server, per-property last-writer-wins by server arrival order | Undo/redo rewrite each other's histories; deleted objects live in the deleter's undo buffer |
| Yjs | MIT | CRDT with state vectors | `UndoManager` tracks only local-origin changes |

## What we learned → what we chose
1. **Systems with a central server don't need OT/CRDT.** Drawpile, tldraw and Figma all use a
   server-authoritative ordered log. Figma calls OT "unnecessarily complex for our problem space."
   → We use a **server-assigned global sequence number**.
2. **Echo as ack** (Drawpile's "local fork"): the server broadcasts your op back to you with its
   `seq`. → No separate ACK messages. We use optimistic local rendering plus a pending layer.
3. **Snapshot + log replay** for late joiners. Drawpile added session resets because replaying the
   whole history got slow. → We **compact the log into snapshots** and resume from `last_seq`.
4. **Per-user undo** is what users expect. Global undo deletes other people's work. → Undo sends
   an **inverse op**, with explicit skip rules for conflicts.
5. **Tombstones** (Excalidraw `isDeleted`) make undo/restore after an erase well-defined.
6. **Batching live points** (~30 Hz) and throttling cursors (~20 Hz) cut traffic by an order of magnitude.
7. **Server-side permission enforcement** (WBO roles, Drawpile operators). → owner/editor/viewer
   roles are checked on every op on the server.

## Engineering pitfalls collected
- Qt and asyncio both want the main loop → the client uses `QTcpSocket` (Qt-native) and the server uses asyncio.
- Nagle + delayed ACK can add 40–200 ms stalls → `TCP_NODELAY` / `LowDelayOption`.
- PyInstaller `--onefile` with Qt is unreliable/slow on macOS → one-dir builds.
- Windows "Public" network profile blocks inbound LAN connections. Unsigned macOS apps re-prompt
  for network access → ad-hoc codesign.
- Campus Wi-Fi often isolates clients from each other → demo on a hotspot or switch.

## References
- Drawpile: https://github.com/drawpile/Drawpile/wiki/How-Drawpile-works , https://docs.drawpile.net/help/development/protocol
- Excalidraw P2P collaboration: https://plus.excalidraw.com/blog/building-excalidraw-p2p-collaboration-feature
- tldraw sync: https://tldraw.dev/blog/announcing-tldraw-sync
- WBO: https://github.com/lovasoa/whitebophir
- cracker0dks/whiteboard: https://github.com/cracker0dks/whiteboard
- Figma multiplayer: https://www.figma.com/blog/how-figmas-multiplayer-technology-works/
- Liveblocks, multiplayer undo/redo: https://liveblocks.io/blog/how-to-build-undo-redo-in-a-multiplayer-environment
- Yjs UndoManager: https://docs.yjs.dev/api/undo-manager
- Python asyncio TCP_NODELAY default: https://github.com/python/asyncio/pull/373
- PySide6 + PyInstaller: https://doc.qt.io/qtforpython-6/deployment/deployment-pyinstaller.html
