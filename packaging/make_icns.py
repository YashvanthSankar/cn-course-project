"""Render the LANBoard logo (ui/theme.paint_logo) into packaging/LANBoard.icns.

macOS only (uses ``iconutil``). The logo is drawn at run time by the client, so
this is the single place an icon *file* is produced; the spec uses it for the
LANBoard.app bundle. Run from the repository root::

    .venv/bin/python packaging/make_icns.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402

from lanboard.client.ui.theme import paint_logo  # noqa: E402

OUT = ROOT / "packaging" / "LANBoard.icns"
# (file name, pixel size) pairs that iconutil expects in an .iconset
SIZES = [(f"icon_{s}x{s}{suffix}.png", s * k)
         for s in (16, 32, 128, 256, 512)
         for suffix, k in (("", 1), ("@2x", 2))]


def render(px: int) -> QImage:
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    # Apple's icon grid: the tile is ~80% of the canvas, centred, so the icon
    # sits at the same visual size as other apps in the Dock and Finder.
    inset = px * 0.1
    paint_logo(p, QRectF(inset, inset, px - 2 * inset, px - 2 * inset))
    p.end()
    return img


def main() -> int:
    if sys.platform != "darwin":
        print("make_icns.py: macOS only (needs iconutil)", file=sys.stderr)
        return 1
    _app = QGuiApplication(sys.argv)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "LANBoard.iconset"
        iconset.mkdir()
        for name, px in SIZES:
            if not render(px).save(str(iconset / name), "PNG"):
                print(f"failed to write {name}", file=sys.stderr)
                return 1
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT)], check=True)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
