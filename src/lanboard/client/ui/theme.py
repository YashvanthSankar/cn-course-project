"""Visual theme for the LANBoard client: palettes, fonts, Qt stylesheet and painted icons.

Everything visual that is shared between widgets lives here so the app looks
consistent:

* :data:`LIGHT` / :data:`DARK` are colour palettes (plain dicts of design tokens).
  Both share one vivid accent: an indigo → violet gradient used for primary
  buttons, the active tool and the brand mark.
* :func:`load_fonts` registers the bundled fonts (Inter for the UI, JetBrains Mono
  for numbers and JSON) from ``client/assets/fonts`` with ``QFontDatabase``. It
  works from a source checkout and from the PyInstaller bundle (``sys._MEIPASS``)
  and silently falls back to system fonts if the files are missing.
* :func:`apply_theme` installs a Qt palette plus a QSS stylesheet built from the
  active tokens and notifies every widget through :func:`manager` ``.changed``.
* :func:`paint_card` / :func:`accent_gradient` are the shared painting helpers
  for the floating "cards" (tool palette, style bar, toasts, popovers).
* :func:`icon` paints vector icons with ``QPainter`` at runtime. There are no image
  files, no icon fonts and nothing is downloaded, so the client works on an
  isolated LAN. Icons are drawn on a 24x24 grid in a stroke style (round caps,
  1.8 px lines), similar to popular open icon sets, and cached per colour/size.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPainterPathStroker, QPalette, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# palettes (design tokens)
# ---------------------------------------------------------------------------

LIGHT: dict[str, str] = {
    "name": "light",
    "bg": "#eceef4",          # window background behind panels
    "surface": "#ffffff",     # cards, panels, top bar
    "surface2": "#f4f5f9",    # inputs, hovered rows, secondary buttons
    "surface3": "#e8eaf1",    # pressed / selected neutral
    "float": "#ffffff",       # floating cards over the canvas
    "border": "#e3e6ee",
    "border_strong": "#cdd2df",
    "text": "#121626",
    "text2": "#474f66",
    "muted": "#868ea5",
    "accent": "#5b5cf0",      # indigo
    "accent2": "#9b5cf6",     # violet (gradient end)
    "accent_hover": "#4b4ce0",
    "accent_soft": "#ececfe",
    "accent_text": "#ffffff",
    "success": "#12a150",
    "success_soft": "#e2f6ea",
    "warn": "#d97706",
    "warn_soft": "#fff3dc",
    "info": "#2f7cf6",
    "info_soft": "#e4efff",
    "danger": "#e5484d",
    "danger_soft": "#fde7e7",
    "neutral_soft": "#eceef3",
    "canvas": "#f8f9fc",
    "shadow": "#141a3340",
    "console": "#f7f8fc",     # Network tab "mission control" panels
    "console_line": "#e6e8f0",
    "chart_in": "#5b5cf0",
    "chart_out": "#f2711c",
    "chart_rtt": "#0fa968",
}

DARK: dict[str, str] = {
    "name": "dark",
    "bg": "#090c14",
    "surface": "#121725",
    "surface2": "#191f30",
    "surface3": "#232a3f",
    "float": "#161c2c",
    "border": "#222a3d",
    "border_strong": "#323b54",
    "text": "#e9ecf6",
    "text2": "#a9b1c8",
    "muted": "#6d7690",
    "accent": "#7c7dff",
    "accent2": "#b07cff",
    "accent_hover": "#9091ff",
    "accent_soft": "#262a5c",
    "accent_text": "#ffffff",
    "success": "#34d399",
    "success_soft": "#0f3327",
    "warn": "#fbbf24",
    "warn_soft": "#3a2c0c",
    "info": "#60a5fa",
    "info_soft": "#132a4a",
    "danger": "#f87171",
    "danger_soft": "#3d1a20",
    "neutral_soft": "#1f2536",
    "canvas": "#0e121d",
    "shadow": "#00000099",
    "console": "#0c111d",
    "console_line": "#1c2335",
    "chart_in": "#8b8cff",
    "chart_out": "#fb923c",
    "chart_rtt": "#34d399",
}

#: The ten colour swatches of the drawing palette (chosen to stay readable on
#: both a white and a dark canvas).
SWATCHES = ["#1b2130", "#e5484d", "#f76b15", "#f5b400", "#30a46c",
            "#12a594", "#0090ff", "#4f63e8", "#8e4ec6", "#ffffff"]

#: Colours for message families in the Network Inspector (by MsgType name).
#: Families: handshake, ordered ops, recovery, presence, chat, liveness, errors, blobs.
MSG_FAMILY = {
    "HELLO": "info", "CHALLENGE": "info", "JOIN": "info", "JOINED": "info", "LEAVE": "info",
    "OP": "accent", "OP_SUBMIT": "accent", "OP_BATCH": "accent", "OP_REJECT": "danger",
    "SNAPSHOT": "accent2", "RESYNC": "accent2", "HASH": "accent2", "HISTORY": "accent2",
    "HISTORY_REQ": "accent2", "SAVE": "accent2", "SAVES": "accent2", "LIST_SAVES": "accent2", "LOAD": "accent2",
    "CHAT": "success", "CHAT_SEND": "success",
    "PING": "chart_rtt", "PONG": "chart_rtt",
    "ERROR": "danger", "KICK": "danger",
    "BLOB_PUT": "warn", "BLOB_GET": "warn", "BLOB_DATA": "warn", "BLOB_STORED": "warn",
}


class ThemeManager(QObject):
    """Holds the active palette and tells widgets when it changes."""

    changed = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.palette: dict[str, str] = LIGHT

    @property
    def dark(self) -> bool:
        return self.palette is DARK


_manager: ThemeManager | None = None


class _ThemeRelay(QObject):
    """Forwards ``changed`` to a callback; a child of the widget it serves.

    Qt removes a connection when the receiving QObject is destroyed, but not
    when a plain lambda's captured widget is. Routing through a child object
    means a deleted window or toast silently stops listening instead of
    raising "Internal C++ object already deleted" on the next theme switch.
    """

    def __init__(self, owner: QObject, fn: Callable) -> None:
        super().__init__(owner)
        self._fn = fn

    def fire(self, *_args) -> None:
        self._fn()


def on_theme_change(owner: QObject, fn: Callable[[], None]) -> None:
    """Call ``fn()`` after every theme switch for as long as ``owner`` exists."""
    manager().changed.connect(_ThemeRelay(owner, fn).fire)


def manager() -> ThemeManager:
    """The process-wide :class:`ThemeManager` (created on first use)."""
    global _manager
    if _manager is None:
        _manager = ThemeManager()
    return _manager


def tokens() -> dict[str, str]:
    """Shortcut for the active palette."""
    return manager().palette


def color(token: str) -> QColor:
    """A ``QColor`` for a palette token, e.g. ``color("accent")``."""
    return QColor(tokens()[token])


def alpha(c: str | QColor, a: float) -> QColor:
    """``c`` with opacity ``a`` (0..1)."""
    q = QColor(c)
    q.setAlphaF(max(0.0, min(1.0, a)))
    return q


def rgba(c: str, a: float) -> str:
    """``rgba(r,g,b,a)`` for QSS (which has no 8-digit hex with alpha first)."""
    q = QColor(c)
    return f"rgba({q.red()},{q.green()},{q.blue()},{int(a * 255)})"


# ---------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------

UI_FAMILY = "Inter"
MONO_FAMILY = "JetBrains Mono"
_SYSTEM_MONO = ["Menlo", "Consolas", "DejaVu Sans Mono", "Courier New"]
_fonts_loaded: dict[str, bool] | None = None


def assets_dir() -> Path:
    """``client/assets``: next to the package in a checkout, under ``sys._MEIPASS`` when frozen."""
    candidates = []
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        candidates.append(Path(frozen) / "lanboard" / "client" / "assets")
    candidates.append(Path(__file__).resolve().parent.parent / "assets")
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[-1]


def load_fonts() -> dict[str, bool]:
    """Register the bundled fonts once. Returns ``{"ui": bool, "mono": bool}``.

    Needs a ``QGuiApplication``. Paths must be absolute (Qt's CoreText backend
    ignores relative ones). Missing files are not an error: the UI then uses
    the platform's default sans-serif and monospace fonts.
    """
    global _fonts_loaded, UI_FAMILY, MONO_FAMILY
    if _fonts_loaded is not None:
        return _fonts_loaded
    families: set[str] = set()
    font_dir = assets_dir() / "fonts"
    if font_dir.is_dir():
        for f in sorted(font_dir.glob("*.ttf")):
            fid = QFontDatabase.addApplicationFont(str(f.resolve()))
            if fid >= 0:
                families.update(QFontDatabase.applicationFontFamilies(fid))
    ui_ok, mono_ok = "Inter" in families, "JetBrains Mono" in families
    if not ui_ok:
        UI_FAMILY = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    if not mono_ok:
        MONO_FAMILY = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    _fonts_loaded = {"ui": ui_ok, "mono": mono_ok}
    return _fonts_loaded


def ui_font(px: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """The UI font (Inter) at a pixel size and weight."""
    f = QFont(UI_FAMILY)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def mono_font(px: int = 11, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """The monospace font used for addresses, seq numbers, hashes and JSON."""
    f = QFont(MONO_FAMILY)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def mono_css() -> str:
    """A QSS/HTML ``font-family`` list for monospace text."""
    fams = [MONO_FAMILY] + [f for f in _SYSTEM_MONO if f != MONO_FAMILY]
    return ", ".join(f'"{f}"' for f in fams) + ", monospace"


# ---------------------------------------------------------------------------
# shared painting helpers
# ---------------------------------------------------------------------------

def accent_gradient(r: QRectF, t: dict[str, str] | None = None, vertical: bool = False) -> QLinearGradient:
    """The brand gradient (accent → accent2) across ``r``."""
    t = t or tokens()
    g = QLinearGradient(r.topLeft(), r.bottomLeft() if vertical else r.topRight())
    g.setColorAt(0.0, QColor(t["accent"]))
    g.setColorAt(1.0, QColor(t["accent2"]))
    return g


def paint_shadow(p: QPainter, r: QRectF, radius: float, spread: int = 10, strength: float = 1.0,
                 dy: float = 3.0) -> None:
    """A soft drop shadow made of a few stacked translucent rounded rects.

    Much cheaper than ``QGraphicsDropShadowEffect`` (no offscreen blur pass), so
    floating cards can repaint every frame while the user drags a slider. The
    layers grow outwards and get fainter, which reads as a blurred shadow.
    """
    base = QColor(tokens()["shadow"])
    p.setPen(Qt.PenStyle.NoPen)
    steps = max(2, spread // 2)
    for i in range(steps):
        k = (i + 1) / steps                       # 0..1 from the card edge outwards
        grow = spread * k
        c = QColor(base)
        c.setAlphaF(min(1.0, base.alphaF() * strength * 0.55 * (1.0 - k) ** 1.5 + 0.004))
        p.setBrush(c)
        rr = r.adjusted(-grow * 0.7, -grow * 0.45 + dy * 0.4, grow * 0.7, grow * 0.8 + dy)
        p.drawRoundedRect(rr, radius + grow * 0.7, radius + grow * 0.7)


def paint_card(p: QPainter, r: QRectF, radius: float = 14, fill: str | QColor | None = None,
               border: str | QColor | None = None, shadow: bool = True) -> None:
    """A rounded floating card with a soft shadow and a hairline border."""
    t = tokens()
    if shadow:
        paint_shadow(p, r, radius)
    p.setPen(QPen(QColor(border or t["border"]), 1))
    p.setBrush(QColor(fill or t["float"]))
    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


# ---------------------------------------------------------------------------
# stylesheet
# ---------------------------------------------------------------------------

def stylesheet(t: dict[str, str]) -> str:
    """Build the application QSS from palette tokens.

    Widgets opt into special looks through ``objectName`` (``#TopBar``) or
    dynamic properties (``[role="primary"]``) so code never hard-codes colours.
    """
    grad = (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {t['accent']}, stop:1 {t['accent2']})")
    grad_hover = (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {t['accent_hover']}, stop:1 {t['accent']})")
    mono = mono_css()
    return f"""
    * {{ outline: 0; }}
    QWidget {{ color: {t['text']}; font-size: 13px; }}
    QMainWindow, QDialog {{ background: {t['bg']}; }}
    QToolTip {{ background: {t['text']}; color: {t['surface']}; border: none;
                padding: 6px 9px; border-radius: 7px; font-size: 12px; }}

    /* ---- chrome: top bar, side panel, network strip -------------------- */
    #TopBar {{ background: {t['surface']}; border-bottom: 1px solid {t['border']}; }}
    #NetStrip {{ background: {t['surface']}; border-top: 1px solid {t['border']}; }}
    #NetStrip QLabel {{ color: {t['text2']}; font-size: 11px; }}
    #NetStrip QLabel[role="mono"] {{ font-family: {mono}; font-size: 11px; }}
    #SidePanel {{ background: {t['surface']}; border-left: 1px solid {t['border']}; }}
    #SidePage {{ background: {t['surface']}; }}
    #Stage {{ background: {t['canvas']}; }}
    QFrame#VSep {{ background: {t['border']}; }}

    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 9px;
                   padding: 5px; color: {t['text2']}; }}
    QToolButton:hover {{ background: {t['surface2']}; }}
    QToolButton:pressed {{ background: {t['surface3']}; }}
    QToolButton:checked {{ background: {t['accent_soft']}; color: {t['accent']}; }}
    QToolButton:disabled {{ color: {t['muted']}; }}
    QToolButton[textBeside="true"] {{ padding: 5px 10px; font-weight: 500; }}
    QToolButton[warnOn="true"]:checked {{ background: {t['warn_soft']}; color: {t['warn']}; }}
    QToolButton::menu-indicator {{ image: none; width: 0; }}
    QToolButton[role="chip"] {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 8px;
                   padding: 3px 9px; color: {t['text2']}; font-family: {mono}; font-size: 12px; }}
    QToolButton[role="chip"]:hover {{ border: 1px solid {t['accent']}; color: {t['accent']}; }}

    /* ---- room title ------------------------------------------------------ */
    QLabel#RoomName {{ font-size: 15px; font-weight: 600; }}
    QLabel#RoomSub {{ color: {t['muted']}; font-size: 11px; }}

    QLabel[role="h1"] {{ font-size: 26px; font-weight: 700; }}
    QLabel[role="h2"] {{ font-size: 15px; font-weight: 600; }}
    QLabel[role="section"] {{ color: {t['muted']}; font-size: 11px; font-weight: 600; }}
    QLabel[role="muted"] {{ color: {t['muted']}; }}
    QLabel[role="hint"] {{ color: {t['text2']}; font-size: 12px; }}
    QLabel[role="error"] {{ color: {t['danger']}; font-weight: 500; }}
    QLabel[role="mono"] {{ font-family: {mono}; font-size: 12px; }}
    QLabel[role="metric"] {{ font-size: 18px; font-weight: 600; font-family: {mono}; }}
    QLabel[role="card"] {{ background: {t['accent_soft']}; color: {t['text']}; border-radius: 10px;
                          padding: 10px 12px; }}

    /* ---- inputs --------------------------------------------------------- */
    QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 9px;
        padding: 7px 10px; selection-background-color: {t['accent']}; selection-color: {t['accent_text']}; }}
    QLineEdit:hover, QComboBox:hover {{ border: 1px solid {t['border_strong']}; }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
        border: 1px solid {t['accent']}; background: {t['surface']}; }}
    QLineEdit[invalid="true"] {{ border: 1px solid {t['danger']}; background: {t['danger_soft']}; }}
    QLineEdit:disabled, QSpinBox:disabled {{ color: {t['muted']}; background: {t['surface3']}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
    QComboBox {{ padding: 5px 10px; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox::down-arrow {{ width: 0; height: 0; border-left: 4px solid transparent;
        border-right: 4px solid transparent; border-top: 5px solid {t['text2']}; margin-right: 8px; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']};
        border-radius: 8px; padding: 4px; outline: 0;
        selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}

    /* ---- buttons -------------------------------------------------------- */
    QPushButton {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 9px;
                   padding: 7px 14px; font-weight: 500; }}
    QPushButton:hover {{ background: {t['surface3']}; border: 1px solid {t['border_strong']}; }}
    QPushButton:disabled {{ color: {t['muted']}; }}
    QPushButton[role="primary"] {{ background: {grad}; color: {t['accent_text']}; border: none;
                                   font-weight: 600; padding: 8px 16px; }}
    QPushButton[role="primary"]:hover {{ background: {grad_hover}; }}
    QPushButton[role="primary"]:disabled {{ background: {t['border_strong']}; color: {t['surface']}; }}
    QPushButton[role="danger"] {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f0484e,
                                  stop:1 #c81e5b); color: white; border: none; font-weight: 700; }}
    QPushButton[role="danger"]:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff5a60,
                                  stop:1 #dc2a6a); }}
    QPushButton[role="danger"]:pressed {{ background: #b81d3f; }}
    QPushButton[role="ghost"] {{ background: transparent; border: none; color: {t['accent']}; padding: 4px 8px; }}
    QPushButton[role="ghost"]:hover {{ background: {t['accent_soft']}; }}
    QPushButton[role="soft"] {{ background: {t['accent_soft']}; color: {t['accent']}; border: none;
                                font-weight: 600; }}
    QPushButton[role="soft"]:hover {{ background: {rgba(t['accent'], 0.22)}; }}
    QPushButton[role="segment"] {{ background: transparent; border: none; border-radius: 8px; padding: 6px 12px;
                                   color: {t['text2']}; font-weight: 500; }}
    QPushButton[role="segment"]:hover {{ color: {t['text']}; }}
    QPushButton[role="segment"]:checked {{ background: {t['surface']}; color: {t['text']}; font-weight: 600;
                                   border: 1px solid {t['border']}; }}
    QFrame#Segmented {{ background: {t['surface3'] if t['name'] == 'light' else t['bg']};
                        border-radius: 10px; }}

    /* ---- lists, tables ---------------------------------------------------- */
    QListWidget, QListView, QTreeWidget, QTableView, QTextBrowser {{ background: {t['surface']};
        border: 1px solid {t['border']}; border-radius: 10px; alternate-background-color: {t['surface2']}; }}
    QListWidget::item, QTreeWidget::item {{ padding: 5px 4px; border-radius: 7px; }}
    QListWidget::item:hover, QTreeWidget::item:hover {{ background: {t['surface2']}; }}
    QListWidget::item:selected, QTreeWidget::item:selected, QTableView::item:selected {{
        background: {t['accent_soft']}; color: {t['text']}; }}
    QTableView {{ gridline-color: transparent; font-size: 12px; }}
    QHeaderView {{ background: transparent; }}
    QHeaderView::section {{ background: {t['console']}; color: {t['muted']}; border: none;
        border-bottom: 1px solid {t['console_line']}; padding: 5px 6px; font-size: 10px; font-weight: 600; }}
    QTabWidget::pane {{ border: none; background: {t['surface']}; }}
    QFrame#Card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 12px; }}
    QFrame#Console {{ background: {t['console']}; border: 1px solid {t['console_line']}; border-radius: 12px; }}
    QFrame#Hero {{ border: none; }}

    /* ---- sliders, checkboxes, scrollbars ------------------------------- */
    QSlider {{ min-height: 20px; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t['surface3']}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {grad}; border-radius: 2px; }}
    QSlider::sub-page:horizontal:disabled {{ background: {t['border_strong']}; }}
    QSlider::handle:horizontal {{ background: white; border: 2px solid {t['accent']};
        width: 12px; height: 12px; margin: -6px 0; border-radius: 8px; }}
    QSlider::handle:horizontal:hover {{ border: 3px solid {t['accent']}; }}
    QSlider::handle:horizontal:disabled {{ border: 2px solid {t['border_strong']}; }}
    QSlider::groove:vertical {{ width: 4px; background: {t['surface3']}; border-radius: 2px; }}
    QSlider::handle:vertical {{ background: white; border: 2px solid {t['accent']};
        width: 12px; height: 12px; margin: 0 -6px; border-radius: 8px; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {t['border_strong']};
                            background: {t['surface']}; }}
    QCheckBox::indicator:checked {{ background: {t['accent']}; border: 1px solid {t['accent']}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 2px 3px 2px; }}
    QScrollBar::handle:vertical {{ background: {rgba(t['muted'], 0.35)}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {rgba(t['muted'], 0.6)}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 3px 2px 3px; }}
    QScrollBar::handle:horizontal {{ background: {rgba(t['muted'], 0.35)}; border-radius: 3px; min-width: 30px; }}
    QScrollBar::handle:horizontal:hover {{ background: {rgba(t['muted'], 0.6)}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QScrollArea {{ border: none; background: transparent; }}
    QSplitter::handle {{ background: {t['border']}; }}
    QSplitter::handle:vertical {{ height: 1px; }}
    QSplitter#BodySplit::handle {{ background: {t['border']}; width: 1px; }}

    /* ---- menus ------------------------------------------------------------ */
    QMenu {{ background: {t['float']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 6px; }}
    QMenu::item {{ padding: 7px 26px 7px 10px; border-radius: 7px; color: {t['text']}; }}
    QMenu::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
    QMenu::item:disabled {{ color: {t['muted']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 8px; }}
    QMenu::icon {{ padding-left: 8px; }}
    QMenu::indicator {{ width: 14px; height: 14px; margin-left: 4px; border-radius: 7px; }}
    QMenu::indicator:checked {{ background: {t['accent']}; border: 3px solid {t['float']}; }}
    QMenu::right-arrow {{ width: 0; height: 0; border-top: 4px solid transparent;
        border-bottom: 4px solid transparent; border-left: 5px solid {t['muted']}; margin-right: 8px; }}
    QMessageBox QLabel {{ font-size: 13px; }}
    QInputDialog QLabel {{ font-size: 13px; }}
    """


def apply_theme(app: QApplication | None = None, mode: str = "light") -> dict[str, str]:
    """Activate the ``"light"`` or ``"dark"`` theme for the whole application."""
    app = app or QApplication.instance()
    pal_tokens = DARK if mode == "dark" else LIGHT
    m = manager()
    m.palette = pal_tokens
    _ICON_CACHE.clear()
    if app is not None:
        load_fonts()
        app.setStyle("Fusion")             # consistent base look on every OS
        base_font = QFont(UI_FAMILY)
        base_font.setPixelSize(13)
        app.setFont(base_font)
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(pal_tokens["bg"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(pal_tokens["surface"]))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(pal_tokens["surface2"]))
        pal.setColor(QPalette.ColorRole.Text, QColor(pal_tokens["text"]))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(pal_tokens["text"]))
        pal.setColor(QPalette.ColorRole.Button, QColor(pal_tokens["surface2"]))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(pal_tokens["text"]))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(pal_tokens["accent"]))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(pal_tokens["accent_text"]))
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(pal_tokens["muted"]))
        pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(pal_tokens["text"]))
        pal.setColor(QPalette.ColorRole.ToolTipText, QColor(pal_tokens["surface"]))
        pal.setColor(QPalette.ColorRole.Link, QColor(pal_tokens["accent"]))
        pal.setColor(QPalette.ColorRole.Mid, QColor(pal_tokens["border"]))
        pal.setColor(QPalette.ColorRole.Midlight, QColor(pal_tokens["surface2"]))
        pal.setColor(QPalette.ColorRole.Dark, QColor(pal_tokens["border_strong"]))
        app.setPalette(pal)
        app.setStyleSheet(stylesheet(pal_tokens))
    m.changed.emit(pal_tokens)
    return pal_tokens


def repolish(widget) -> None:
    """Re-apply QSS after a dynamic property (e.g. ``state``) changed."""
    st = widget.style()
    st.unpolish(widget)
    st.polish(widget)
    widget.update()


# ---------------------------------------------------------------------------
# painted icons
# ---------------------------------------------------------------------------

IconFn = Callable[[QPainter, QColor], None]
_ICONS: dict[str, IconFn] = {}
_ICON_CACHE: dict[tuple, QIcon] = {}


def _register(name: str):
    def deco(fn: IconFn) -> IconFn:
        _ICONS[name] = fn
        return fn
    return deco


def _pen(c: QColor, w: float = 1.8) -> QPen:
    pen = QPen(c, w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _poly(*pts: tuple[float, float]) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _capsule(x1, y1, x2, y2, width) -> QPainterPath:
    """A rounded bar from (x1,y1) to (x2,y2): used to build fingers etc."""
    line = QPainterPath(QPointF(x1, y1))
    line.lineTo(x2, y2)
    st = QPainterPathStroker()
    st.setWidth(width)
    st.setCapStyle(Qt.PenCapStyle.RoundCap)
    return st.createStroke(line)


@_register("select")
def _i_select(p, c):
    path = QPainterPath()
    path.addPolygon(_poly((6, 3.5), (6, 19), (10, 15.2), (12.8, 20.8), (15.2, 19.7), (12.5, 14), (17.8, 14)))
    path.closeSubpath()
    p.setPen(_pen(c))
    p.drawPath(path)


@_register("hand")
def _i_hand(p, c):
    # silhouette = union of palm, four fingers and a thumb, outlined once
    path = QPainterPath()
    path.addRoundedRect(QRectF(6.2, 10.5, 12.3, 10.5), 4.5, 4.5)
    for x, top in ((8.0, 6.5), (10.9, 4.0), (13.8, 4.5), (16.7, 7.0)):
        path = path.united(_capsule(x, top, x, 13, 2.7))
    path = path.united(_capsule(7.0, 16.5, 3.8, 12.6, 2.7))
    p.setPen(_pen(c, 1.5))
    p.drawPath(path.simplified())


@_register("pan")
def _i_pan(p, c):
    p.setPen(_pen(c))
    p.drawLine(QPointF(12, 3), QPointF(12, 21))
    p.drawLine(QPointF(3, 12), QPointF(21, 12))
    for a, b, d in (((9.5, 5.5), (12, 3), (14.5, 5.5)), ((9.5, 18.5), (12, 21), (14.5, 18.5)),
                    ((5.5, 9.5), (3, 12), (5.5, 14.5)), ((18.5, 9.5), (21, 12), (18.5, 14.5))):
        p.drawPolyline(_poly(a, b, d))


@_register("pen")
def _i_pen(p, c):
    p.setPen(_pen(c))
    p.drawPolygon(_poly((4, 20), (5, 15.5), (15.8, 4.7), (19.3, 8.2), (8.5, 19)))
    p.drawLine(QPointF(13.6, 6.9), QPointF(17.1, 10.4))


@_register("highlighter")
def _i_highlighter(p, c):
    p.save()
    p.translate(13, 10.5)
    p.rotate(40)
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(-3.2, -9, 6.4, 11), 1.5, 1.5)
    p.drawPolygon(_poly((-3.2, 2), (3.2, 2), (1.6, 6.5), (-1.6, 6.5)))
    p.restore()
    hl = QColor(c)
    hl.setAlphaF(0.45)
    p.setPen(_pen(hl, 3))
    p.drawLine(QPointF(3.5, 20.5), QPointF(12, 20.5))


@_register("eraser")
def _i_eraser(p, c):
    p.save()
    p.translate(12.5, 11)
    p.rotate(-45)
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(-8, -4.5, 16, 9), 2, 2)
    p.drawLine(QPointF(-1.5, -4.5), QPointF(-1.5, 4.5))
    p.restore()
    p.setPen(_pen(c))
    p.drawLine(QPointF(9, 20.5), QPointF(20, 20.5))


@_register("line")
def _i_line(p, c):
    p.setPen(_pen(c))
    p.drawLine(QPointF(5, 19), QPointF(19, 5))
    p.setBrush(c)
    p.drawEllipse(QPointF(5, 19), 1.4, 1.4)
    p.drawEllipse(QPointF(19, 5), 1.4, 1.4)


@_register("arrow")
def _i_arrow(p, c):
    p.setPen(_pen(c))
    p.drawLine(QPointF(5, 19), QPointF(19, 5))
    p.drawPolyline(_poly((10.5, 5), (19, 5), (19, 13.5)))


@_register("rect")
def _i_rect(p, c):
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(4, 6, 16, 12), 2.2, 2.2)


@_register("ellipse")
def _i_ellipse(p, c):
    p.setPen(_pen(c))
    p.drawEllipse(QRectF(3.5, 5.5, 17, 13))


@_register("text")
def _i_text(p, c):
    p.setPen(_pen(c, 2))
    p.drawPolyline(_poly((5, 7), (5, 4.5), (19, 4.5), (19, 7)))
    p.drawLine(QPointF(12, 4.5), QPointF(12, 19.5))
    p.drawLine(QPointF(9, 19.5), QPointF(15, 19.5))


@_register("sticky")
def _i_sticky(p, c):
    p.setPen(_pen(c))
    p.drawPolygon(_poly((4.5, 4.5), (19.5, 4.5), (19.5, 14), (14, 19.5), (4.5, 19.5)))
    p.drawPolyline(_poly((14, 19.5), (14, 14), (19.5, 14)))
    p.drawLine(QPointF(8, 9), QPointF(16, 9))
    p.drawLine(QPointF(8, 12.5), QPointF(12, 12.5))


@_register("laser")
def _i_laser(p, c):
    # a glowing dot leaving a fading trail
    pts = [(21, 3), (18.5, 7.5), (14.5, 9), (12.5, 12.5)]
    for i in range(len(pts) - 1):
        tc = QColor(c)
        tc.setAlphaF(0.3 + 0.25 * i)
        p.setPen(_pen(tc, 1.6 + 0.3 * i))
        p.drawLine(QPointF(*pts[i]), QPointF(*pts[i + 1]))
    glow = QColor(c)
    glow.setAlphaF(0.22)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(glow)
    p.drawEllipse(QPointF(9, 15.5), 6.5, 6.5)
    p.setBrush(c)
    p.drawEllipse(QPointF(9, 15.5), 3.4, 3.4)


def _undo_path() -> QPainterPath:
    path = QPainterPath(QPointF(4, 9.5))
    path.lineTo(14, 9.5)
    path.arcTo(QRectF(8.5, 9.5, 11, 11), 90, -180)
    path.lineTo(10, 20.5)
    return path


@_register("undo")
def _i_undo(p, c):
    p.setPen(_pen(c))
    p.drawPath(_undo_path())
    p.drawPolyline(_poly((8.5, 5), (4, 9.5), (8.5, 14)))


@_register("redo")
def _i_redo(p, c):
    p.save()
    p.translate(24, 0)
    p.scale(-1, 1)
    _i_undo(p, c)
    p.restore()


@_register("trash")
def _i_trash(p, c):
    p.setPen(_pen(c))
    p.drawLine(QPointF(4, 7), QPointF(20, 7))
    p.drawPolyline(_poly((9, 7), (9, 4.2), (15, 4.2), (15, 7)))
    p.drawPolyline(_poly((6, 7), (7, 20), (17, 20), (18, 7)))
    p.drawLine(QPointF(10, 11), QPointF(10, 16))
    p.drawLine(QPointF(14, 11), QPointF(14, 16))


@_register("lock")
def _i_lock(p, c):
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(5, 11, 14, 9.5), 2, 2)
    path = QPainterPath(QPointF(8, 11))
    path.lineTo(8, 8)
    path.arcTo(QRectF(8, 4, 8, 8), 180, -180)
    path.lineTo(16, 11)
    p.drawPath(path)
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 15.5), 0.9, 0.9)


@_register("unlock")
def _i_unlock(p, c):
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(5, 11, 14, 9.5), 2, 2)
    path = QPainterPath(QPointF(8, 11))
    path.lineTo(8, 8)
    path.arcTo(QRectF(8, 4, 8, 8), 180, -150)
    p.drawPath(path)


@_register("lock_small")
def _i_lock_small(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(5.5, 11, 13, 10), 2.5, 2.5)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_pen(c, 2.2))
    path = QPainterPath(QPointF(8.5, 11))
    path.lineTo(8.5, 8)
    path.arcTo(QRectF(8.5, 4.5, 7, 7), 180, -180)
    path.lineTo(15.5, 11)
    p.drawPath(path)


@_register("present")
def _i_present(p, c):
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(3, 4, 18, 12.5), 2, 2)
    p.drawLine(QPointF(12, 16.5), QPointF(12, 20))
    p.drawLine(QPointF(8, 20.5), QPointF(16, 20.5))
    p.setBrush(c)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(_poly((10.2, 7.6), (10.2, 12.8), (14.6, 10.2)))


@_register("image")
def _i_image(p, c):
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2, 2)
    p.drawEllipse(QPointF(8.5, 9.8), 1.6, 1.6)
    p.drawPolyline(_poly((3.8, 17), (9, 12.5), (13, 15.8), (15.8, 13.4), (20.2, 17)))


@_register("pdf")
def _i_pdf(p, c):
    p.setPen(_pen(c))
    p.drawPolygon(_poly((6, 3), (14, 3), (19, 8), (19, 21), (6, 21)))
    p.drawPolyline(_poly((14, 3), (14, 8), (19, 8)))
    f = QFont()
    f.setPixelSize(6)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(6, 11, 13, 8), Qt.AlignmentFlag.AlignCenter, "PDF")


@_register("export")
def _i_export(p, c):
    p.setPen(_pen(c))
    p.drawPolyline(_poly((4, 14), (4, 19.5), (20, 19.5), (20, 14)))
    p.drawLine(QPointF(12, 15), QPointF(12, 4))
    p.drawPolyline(_poly((8, 8), (12, 4), (16, 8)))


@_register("save")
def _i_save(p, c):
    p.setPen(_pen(c))
    p.drawPolygon(_poly((4.5, 4), (16, 4), (19.5, 7.5), (19.5, 20), (4.5, 20)))
    p.drawPolyline(_poly((8, 4), (8, 8.5), (15, 8.5), (15, 4)))
    p.drawRoundedRect(QRectF(7.5, 13, 9, 7), 1, 1)


@_register("folder")
def _i_folder(p, c):
    p.setPen(_pen(c))
    p.drawPolygon(_poly((3.5, 6), (9.5, 6), (11.5, 8.5), (20.5, 8.5), (20.5, 19), (3.5, 19)))


@_register("settings")
def _i_settings(p, c):
    poly = []
    teeth = 8
    for i in range(teeth * 4):
        a = math.radians(i * 360 / (teeth * 4) + 360 / (teeth * 8))
        r = 8.6 if (i % 4) in (0, 1) else 6.6
        poly.append((12 + r * math.cos(a), 12 + r * math.sin(a)))
    p.setPen(_pen(c, 1.6))
    p.drawPolygon(_poly(*poly))
    p.drawEllipse(QPointF(12, 12), 2.8, 2.8)


@_register("copy")
def _i_copy(p, c):
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(8.5, 8.5, 11.5, 11.5), 2, 2)
    p.drawPolyline(_poly((15.5, 8.5), (15.5, 5.5), (4, 5.5), (4, 16), (8.5, 16)))


@_register("users")
def _i_users(p, c):
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(9, 8.5), 3.4, 3.4)
    path = QPainterPath(QPointF(3, 20))
    path.cubicTo(3, 15, 6, 13.5, 9, 13.5)
    path.cubicTo(12, 13.5, 15, 15, 15, 20)
    p.drawPath(path)
    p.drawArc(QRectF(13.5, 4.8, 6.6, 6.6), -60 * 16, 230 * 16)
    path2 = QPainterPath(QPointF(17, 13.6))
    path2.cubicTo(19.5, 14, 21, 16, 21, 19.5)
    p.drawPath(path2)


@_register("user")
def _i_user(p, c):
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(12, 8.5), 3.6, 3.6)
    path = QPainterPath(QPointF(5, 20))
    path.cubicTo(5, 15.5, 8.5, 14, 12, 14)
    path.cubicTo(15.5, 14, 19, 15.5, 19, 20)
    p.drawPath(path)


@_register("chat")
def _i_chat(p, c):
    p.setPen(_pen(c))
    path = QPainterPath()
    path.addRoundedRect(QRectF(3.5, 4.5, 17, 12), 3.5, 3.5)
    tail = QPainterPath()
    tail.addPolygon(_poly((7.5, 15), (7.5, 20.5), (12.5, 15)))
    path = path.united(tail)
    p.drawPath(path.simplified())


@_register("history")
def _i_history(p, c):
    p.setPen(_pen(c))
    p.drawArc(QRectF(4, 4, 16, 16), 200 * 16, -320 * 16)
    p.drawPolyline(_poly((3.4, 7.5), (4.6, 11.3), (8.2, 9.8)))
    p.drawPolyline(_poly((12, 8), (12, 12), (15, 14)))


@_register("network")
def _i_network(p, c):
    p.setPen(_pen(c))
    nodes = [(12, 5), (5, 18.5), (19, 18.5)]
    p.drawLine(QPointF(12, 5), QPointF(5, 18.5))
    p.drawLine(QPointF(12, 5), QPointF(19, 18.5))
    p.drawLine(QPointF(5, 18.5), QPointF(19, 18.5))
    p.setBrush(QColor(tokens()["surface"]))
    for x, y in nodes:
        p.drawEllipse(QPointF(x, y), 2.6, 2.6)


@_register("play")
def _i_play(p, c):
    p.setPen(_pen(c, 1.5))
    p.setBrush(c)
    p.drawPolygon(_poly((8, 5), (8, 19), (19, 12)))


@_register("pause")
def _i_pause(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(6.5, 5, 4, 14), 1.2, 1.2)
    p.drawRoundedRect(QRectF(13.5, 5, 4, 14), 1.2, 1.2)


@_register("scissors")
def _i_scissors(p, c):
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(6.5, 6.5), 2.8, 2.8)
    p.drawEllipse(QPointF(6.5, 17.5), 2.8, 2.8)
    p.drawLine(QPointF(8.6, 8.4), QPointF(20, 17))
    p.drawLine(QPointF(8.6, 15.6), QPointF(20, 7))


@_register("sun")
def _i_sun(p, c):
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(12, 12), 4, 4)
    for i in range(8):
        a = math.radians(i * 45)
        p.drawLine(QPointF(12 + 6.8 * math.cos(a), 12 + 6.8 * math.sin(a)),
                   QPointF(12 + 9 * math.cos(a), 12 + 9 * math.sin(a)))


@_register("moon")
def _i_moon(p, c):
    p.setPen(_pen(c))
    full = QPainterPath()
    full.addEllipse(QPointF(12, 12), 8, 8)
    bite = QPainterPath()
    bite.addEllipse(QPointF(16.5, 8), 6.5, 6.5)
    p.drawPath(full.subtracted(bite))


@_register("fill")
def _i_fill(p, c):
    p.save()
    p.translate(11, 12)
    p.rotate(45)
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(-5.5, -5.5, 11, 11), 1.5, 1.5)
    soft = QColor(c)
    soft.setAlphaF(0.35)
    p.setBrush(soft)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(QRectF(-5.5, 0, 11, 5.5))
    p.restore()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    drop = QPainterPath(QPointF(19.5, 14))
    drop.cubicTo(21.5, 17, 21.5, 19.5, 19.5, 19.5)
    drop.cubicTo(17.5, 19.5, 17.5, 17, 19.5, 14)
    p.drawPath(drop)


def _sparkle(cx, cy, r) -> QPainterPath:
    path = QPainterPath(QPointF(cx, cy - r))
    k = r * 0.18
    path.quadTo(cx + k, cy - k, cx + r, cy)
    path.quadTo(cx + k, cy + k, cx, cy + r)
    path.quadTo(cx - k, cy + k, cx - r, cy)
    path.quadTo(cx - k, cy - k, cx, cy - r)
    return path


@_register("smart")
def _i_smart(p, c):
    p.setPen(_pen(c, 1.5))
    p.drawPath(_sparkle(13.5, 10, 7.5))
    p.setBrush(c)
    p.drawPath(_sparkle(6, 18, 3.2))
    p.drawPath(_sparkle(19.5, 19, 2.2))


@_register("plus")
def _i_plus(p, c):
    p.setPen(_pen(c, 2))
    p.drawLine(QPointF(12, 5.5), QPointF(12, 18.5))
    p.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))


@_register("close")
def _i_close(p, c):
    p.setPen(_pen(c, 2))
    p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
    p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))


@_register("check")
def _i_check(p, c):
    p.setPen(_pen(c, 2.2))
    p.drawPolyline(_poly((5, 12.5), (10, 17.5), (19, 7)))


@_register("warn")
def _i_warn(p, c):
    p.setPen(_pen(c))
    p.drawPolygon(_poly((12, 3.8), (21, 19.5), (3, 19.5)))
    p.drawLine(QPointF(12, 9.5), QPointF(12, 13.8))
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 16.6), 0.6, 0.6)


@_register("info")
def _i_info(p, c):
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 7.8), 0.6, 0.6)


@_register("refresh")
def _i_refresh(p, c):
    p.setPen(_pen(c))
    p.drawArc(QRectF(4.5, 4.5, 15, 15), 30 * 16, 270 * 16)
    p.drawPolyline(_poly((19.8, 3.8), (19.2, 8.3), (14.8, 7.6)))


@_register("eye")
def _i_eye(p, c):
    p.setPen(_pen(c))
    path = QPainterPath(QPointF(2.5, 12))
    path.quadTo(12, 1.5, 21.5, 12)
    path.quadTo(12, 22.5, 2.5, 12)
    p.drawPath(path)
    p.drawEllipse(QPointF(12, 12), 3, 3)


@_register("follow")
def _i_follow(p, c):
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(12, 12), 7.5, 7.5)
    p.drawEllipse(QPointF(12, 12), 2.5, 2.5)
    for a, b in (((12, 1.5), (12, 4.5)), ((12, 19.5), (12, 22.5)), ((1.5, 12), (4.5, 12)), ((19.5, 12), (22.5, 12))):
        p.drawLine(QPointF(*a), QPointF(*b))


@_register("spotlight")
def _i_spotlight(p, c):
    p.setPen(_pen(c))
    soft = QColor(c)
    soft.setAlphaF(0.25)
    p.setBrush(soft)
    p.drawEllipse(QRectF(5, 15, 14, 5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(10, 4), QPointF(5.5, 17))
    p.drawLine(QPointF(14, 4), QPointF(18.5, 17))
    p.drawLine(QPointF(10, 4), QPointF(14, 4))


@_register("leave")
def _i_leave(p, c):
    p.setPen(_pen(c))
    p.drawPolyline(_poly((10, 4), (5, 4), (5, 20), (10, 20)))
    p.drawLine(QPointF(9.5, 12), QPointF(20, 12))
    p.drawPolyline(_poly((16, 8), (20, 12), (16, 16)))


@_register("server")
def _i_server(p, c):
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(4, 4, 16, 7), 2, 2)
    p.drawRoundedRect(QRectF(4, 13, 16, 7), 2, 2)
    p.setBrush(c)
    p.drawEllipse(QPointF(8, 7.5), 0.8, 0.8)
    p.drawEllipse(QPointF(8, 16.5), 0.8, 0.8)


@_register("kick")
def _i_kick(p, c):
    _i_user(p, c)
    p.setPen(_pen(c, 2))
    p.drawLine(QPointF(17, 4), QPointF(21, 8))
    p.drawLine(QPointF(21, 4), QPointF(17, 8))


@_register("filter")
def _i_filter(p, c):
    p.setPen(_pen(c))
    p.drawPolygon(_poly((4, 5), (20, 5), (14, 12.5), (14, 19), (10, 17), (10, 12.5)))


@_register("dot")
def _i_dot(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 12), 5, 5)


@_register("more")
def _i_more(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    for x in (6, 12, 18):
        p.drawEllipse(QPointF(x, 12), 1.7, 1.7)


@_register("panel")
def _i_panel(p, c):
    # window with a right-hand side panel (toggle the side panel)
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(3.5, 4.5, 17, 15), 2.5, 2.5)
    p.drawLine(QPointF(14.5, 4.5), QPointF(14.5, 19.5))
    soft = QColor(c)
    soft.setAlphaF(0.3)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(soft)
    p.drawRect(QRectF(14.5, 5.5, 5, 13))


@_register("keyboard")
def _i_keyboard(p, c):
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(2.5, 6, 19, 12), 2.5, 2.5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    for row, y in enumerate((9.5, 12.5)):
        for i in range(5):
            p.drawRoundedRect(QRectF(5.2 + i * 2.95 + row * 0.8, y - 0.8, 1.6, 1.6), 0.4, 0.4)
    p.drawRoundedRect(QRectF(8, 14.8, 8, 1.6), 0.6, 0.6)


@_register("grid")
def _i_grid(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    for x in (6, 12, 18):
        for y in (6, 12, 18):
            p.drawEllipse(QPointF(x, y), 1.5, 1.5)


@_register("map")
def _i_map(p, c):
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2.5, 2.5)
    soft = QColor(c)
    soft.setAlphaF(0.3)
    p.setBrush(soft)
    p.drawRoundedRect(QRectF(11, 9.5, 6.5, 6), 1.2, 1.2)


@_register("invite")
def _i_invite(p, c):
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(10, 8.5), 3.5, 3.5)
    path = QPainterPath(QPointF(3.5, 20))
    path.cubicTo(3.5, 15.5, 6.5, 14, 10, 14)
    path.cubicTo(12, 14, 13.5, 14.5, 14.5, 15.3)
    p.drawPath(path)
    p.drawLine(QPointF(18.5, 13), QPointF(18.5, 20))
    p.drawLine(QPointF(15, 16.5), QPointF(22, 16.5))


@_register("zoom_in")
def _i_zoom_in(p, c):
    p.setPen(_pen(c, 2))
    p.drawLine(QPointF(12, 6), QPointF(12, 18))
    p.drawLine(QPointF(6, 12), QPointF(18, 12))


@_register("zoom_out")
def _i_zoom_out(p, c):
    p.setPen(_pen(c, 2))
    p.drawLine(QPointF(6, 12), QPointF(18, 12))


@_register("fit")
def _i_fit(p, c):
    p.setPen(_pen(c))
    for (x, y, dx, dy) in ((4, 4, 1, 1), (20, 4, -1, 1), (4, 20, 1, -1), (20, 20, -1, -1)):
        p.drawPolyline(_poly((x, y + 5 * dy), (x, y), (x + 5 * dx, y)))
    p.drawRoundedRect(QRectF(8.5, 8.5, 7, 7), 1.5, 1.5)


@_register("chevron_down")
def _i_chevron_down(p, c):
    p.setPen(_pen(c, 2))
    p.drawPolyline(_poly((7, 9.5), (12, 14.5), (17, 9.5)))


@_register("chevron_right")
def _i_chevron_right(p, c):
    p.setPen(_pen(c, 2))
    p.drawPolyline(_poly((9.5, 7), (14.5, 12), (9.5, 17)))


@_register("signal")
def _i_signal(p, c):
    p.setPen(_pen(c))
    for i, r in enumerate((3.5, 7.5, 11.5)):
        p.drawArc(QRectF(12 - r, 18 - r, 2 * r, 2 * r), 50 * 16, 80 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 18), 1.6, 1.6)


@_register("insert")
def _i_insert(p, c):
    p.setPen(_pen(c))
    p.drawRoundedRect(QRectF(4, 4, 16, 16), 4, 4)
    p.drawLine(QPointF(12, 8.5), QPointF(12, 15.5))
    p.drawLine(QPointF(8.5, 12), QPointF(15.5, 12))


@_register("bolt")
def _i_bolt(p, c):
    p.setPen(_pen(c, 1.6))
    soft = QColor(c)
    soft.setAlphaF(0.25)
    p.setBrush(soft)
    p.drawPolygon(_poly((13.5, 3), (5.5, 13.5), (11.5, 13.5), (10.5, 21), (18.5, 10.5), (12.5, 10.5)))


def _paint_icon(name: str, c: QColor, size: int) -> QPixmap:
    dpr = 3.0                                    # crisp on Retina / 4K screens
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.scale(size / 24.0, size / 24.0)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _ICONS.get(name, _i_dot)(p, c)
    p.end()
    return pm


def icon(name: str, color_: str | QColor | None = None, size: int = 20,
         on_color: str | QColor | None = None) -> QIcon:
    """Return a painted ``QIcon``.

    ``color_`` defaults to the theme's secondary text colour. A muted variant is
    added for the disabled state and ``on_color`` (if given) for checked buttons.
    """
    t = tokens()
    c = QColor(color_) if color_ is not None else QColor(t["text2"])
    key = (name, c.name(QColor.NameFormat.HexArgb), size,
           QColor(on_color).name() if on_color is not None else None, t["name"])
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    ic = QIcon()
    ic.addPixmap(_paint_icon(name, c, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(_paint_icon(name, QColor(t["muted"]), size), QIcon.Mode.Disabled, QIcon.State.Off)
    if on_color is not None:
        ic.addPixmap(_paint_icon(name, QColor(on_color), size), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(_paint_icon(name, QColor(on_color), size), QIcon.Mode.Active, QIcon.State.On)
    _ICON_CACHE[key] = ic
    return ic


def icon_names() -> list[str]:
    return sorted(_ICONS)


def paint_logo(p: QPainter, rect: QRectF) -> None:
    """The LANBoard mark: a gradient tile with a hand-drawn squiggle and a node."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, QColor("#6d6dff"))
    grad.setColorAt(1, QColor("#a35cf7"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    r = rect.width() * 0.26
    p.drawRoundedRect(rect, r, r)
    s = rect.width() / 24.0
    p.translate(rect.topLeft())
    p.scale(s, s)
    pen = _pen(QColor("white"), 2.2)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)   # stroke only: the tile's gradient brush would fill the curve
    path = QPainterPath(QPointF(5, 15.5))
    path.cubicTo(7.5, 8, 10, 8, 11.2, 12.5)
    path.cubicTo(12.4, 17, 15, 16.5, 17.2, 9.5)
    p.drawPath(path)
    p.setBrush(QColor("#ffd166"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(18, 7.5), 2.3, 2.3)
    p.restore()


def app_icon() -> QIcon:
    """Window / dock icon painted from :func:`paint_logo`."""
    ic = QIcon()
    for size in (16, 32, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        paint_logo(p, QRectF(0, 0, size, size))
        p.end()
        ic.addPixmap(pm)
    return ic
