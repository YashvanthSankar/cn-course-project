# cn-course-project — LANBoard

A LAN-only, real-time, multi-user collaborative whiteboard. A central server talks to many clients
over our own framed TCP protocol, and nothing needs the internet.

Computer Networks Lab Project 2026.

## Team
| Name | Roll no. | Area |
|---|---|---|
| Yashvanth S | CS24I1029 | Server core & state |
| Jeevanandham T | CS24I1035 | Protocol & networking |
| Akula Sai Teja | CS24I1054 | Canvas & drawing tools |
| Nithillakrishi P Y | CS24I1039 | UI & collaboration features |

## Docs
- [`docs/SPEC.md`](docs/SPEC.md): the project brief (transcribed from the PDF in `docs/spec/`)
- [`docs/IDEA.md`](docs/IDEA.md): concept, scope, demo story
- [`docs/TECH.md`](docs/TECH.md): stack, architecture, wire protocol, sync and undo semantics
- [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md): code layout, modules, testing, build
- [`docs/TODO.md`](docs/TODO.md): owners, day-by-day plan, mandatory checklist
- [`docs/RESEARCH.md`](docs/RESEARCH.md): prior-art study and design rationale
- [`docs/REFERENCES.md`](docs/REFERENCES.md): open-source repos to study, by owner

## Quick start (once the code lands)
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m lanboard.server --port 5050
python -m lanboard.client
```

## Third-party software
- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/), LGPL-3.0
- [PyInstaller](https://pyinstaller.org/), GPL-2.0 with bootloader exception
- [pytest](https://pytest.org/) / pytest-asyncio, MIT / Apache-2.0
