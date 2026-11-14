"""Wrap dist/LANBoard.app into dist/LANBoard-<version>-macOS.dmg (macOS only).

Build the app first (the spec builds LANBoard.app on macOS)::

    .venv/bin/python packaging/make_icns.py          # once, makes the icon
    .venv/bin/pyinstaller --noconfirm packaging/lanboard.spec
    .venv/bin/python packaging/make_dmg.py

The disk image holds LANBoard.app, an /Applications shortcut to drag it onto,
and a short text file explaining the first-launch Gatekeeper prompt and how to
run the bundled server from Terminal. Only built-in tools are used (hdiutil),
so no extra dependency is needed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dist" / "LANBoard.app"

FIRST_RUN = """\
LANBoard for macOS
==================

1. Drag LANBoard onto the Applications shortcut.
2. First launch: the app is not notarised by Apple, so macOS may say it
   "cannot be opened". Right-click LANBoard in Applications > Open > Open,
   or in Terminal:
       xattr -dr com.apple.quarantine /Applications/LANBoard.app
3. Click "Allow" when macOS asks about finding devices on your local network
   and about incoming connections. LANBoard only talks to your LAN.

Hosting a room: in the connect window choose "Host new room". The app starts
its own server on this Mac and shows the address to share.

Running a stand-alone server in Terminal (optional):
    /Applications/LANBoard.app/Contents/MacOS/lanboard-server --help
"""


def version() -> str:
    text = (ROOT / "src" / "lanboard" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def main() -> int:
    if sys.platform != "darwin":
        print("make_dmg.py: macOS only", file=sys.stderr)
        return 1
    if not (APP / "Contents" / "MacOS" / "lanboard-client").exists():
        print(f"{APP} not found: build it with pyinstaller packaging/lanboard.spec", file=sys.stderr)
        return 1
    out = ROOT / "dist" / f"LANBoard-{version()}-macOS.dmg"
    out.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "LANBoard"
        stage.mkdir()
        # symlinks=True keeps the .app's internal Frameworks/Resources links
        shutil.copytree(APP, stage / "LANBoard.app", symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        (stage / "Read Me First.txt").write_text(FIRST_RUN, encoding="utf-8")
        subprocess.run(["hdiutil", "create", "-volname", "LANBoard", "-srcfolder", str(stage),
                        "-fs", "HFS+", "-format", "UDZO", "-imagekey", "zlib-level=9",
                        "-ov", "-quiet", str(out)], check=True)
    subprocess.run(["hdiutil", "verify", "-quiet", str(out)], check=True)
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
