# LANBoard: LAN-only real-time collaborative whiteboard

A standalone, internet-free whiteboard for a local network. One machine runs the **server**, and any
number of **clients** join a room to draw, write, chat and present together. Everything travels over
our own framed TCP protocol.

Computer Networks Lab Project 2026.

## Team
| Name | Roll no. | Area |
|---|---|---|
| Yashvanth S | CS24I1029 | Server core & state |
| Jeevanandham T | CS24I1035 | Protocol & networking |
| Akula Sai Teja | CS24I1054 | Canvas & drawing tools |
| Nithillakrishi P Y | CS24I1039 | UI & collaboration features |

## Highlights
- **Server-ordered op log**: every edit gets a global sequence number, so every replica converges. Duplicates, gaps and reordering are all handled.
- **Recovery**: late joiners get a snapshot. Reconnecting clients get only the ops they missed. Offline edits are queued and replayed.
- **Consistency Verifier**: the server broadcasts board hashes, and clients show a live ✓ *In sync* badge and heal themselves if their copy diverges.
- **Network Inspector, Chaos Lab and live HUD**: watch the protocol frame by frame, inject latency, drops and duplicates, or "cut the cable", and watch the board stay consistent.
- **Collaboration**: per-user undo/redo, live cursors, chat, roles (owner/editor/viewer), password rooms, lock and kick, shapes, text, sticky notes with voting, images, PDF slide annotation, laser pointer, follow-the-presenter, time-lapse history, and export to PNG, SVG or PDF.

See [`docs/FEATURES.md`](docs/FEATURES.md) for the full list.

## Run from source
```bash
uv venv --python 3.12 .venv            # or: python3.12 -m venv .venv
uv pip install --python .venv/bin/python -e . pytest pytest-asyncio
```

**Server** (on the host machine):
```bash
.venv/bin/python -m lanboard.server --port 5050            # add --dashboard for a live terminal view
```
At startup it prints the LAN address that clients should use. It also answers LAN discovery on UDP 5051.

**Client** (on every participant's machine):
```bash
.venv/bin/python -m lanboard.client
```
- To **host**, pick *Host new room* and tick *Start a server on this computer* if no server is running yet.
- To **join**, pick the server under *Nearby servers*, or type its IP and port, then enter the room code.
- Quick multi-client testing without the dialog: `--user alice --room DEMO --create`.

**Stress test**: `python -m lanboard.tools.bot --port 5050 --room DEMO --bots 8 --seconds 30`

**Tests**: `.venv/bin/python -m pytest -q` (276 tests, including end-to-end runs of the real server and client).

## Standalone executables
```bash
uv pip install --python .venv/bin/python pyinstaller
.venv/bin/pyinstaller --noconfirm packaging/lanboard.spec
```
This produces `dist/LANBoard/lanboard-server` and `dist/LANBoard/lanboard-client`, which share one runtime folder.

**Firewall notes:**
- Windows: allow the server through Defender, and make sure the network profile is *Private*.
- macOS: allow incoming connections when prompted.
- Campus Wi-Fi often blocks traffic between clients, so use a hotspot or a switch for demos.

## Docs
| Doc | Contents |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | the project brief |
| [`docs/IDEA.md`](docs/IDEA.md) | concept, scope, demo story |
| [`docs/FEATURES.md`](docs/FEATURES.md) | feature list incl. signature features |
| [`docs/TECH.md`](docs/TECH.md) | architecture, wire protocol, sync & undo semantics |
| [`docs/CLIENT_API.md`](docs/CLIENT_API.md) | internal contracts between client modules |
| [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) | code layout, testing, build |
| [`docs/TODO.md`](docs/TODO.md) | plan, owners, checklist |
| [`docs/RESEARCH.md`](docs/RESEARCH.md), [`docs/REFERENCES.md`](docs/REFERENCES.md) | prior-art study |

The authoritative message catalogue is the docstring of [`src/lanboard/common/messages.py`](src/lanboard/common/messages.py).

## Third-party software
- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/), LGPL-3.0
- [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) (PDF page rendering), Apache-2.0 / BSD-3-Clause
- [PyInstaller](https://pyinstaller.org/) (build only), GPL-2.0 with bootloader exception
- [pytest](https://pytest.org/) / pytest-asyncio (tests only), MIT / Apache-2.0
- [Inter](https://rsms.me/inter/) and [JetBrains Mono](https://www.jetbrains.com/lp/mono/) fonts (bundled in `src/lanboard/client/assets/fonts`), SIL OFL 1.1

AI assistance (Claude) was used during development. Commits made with it carry a `Co-Authored-By` trailer.
