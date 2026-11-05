"""Visual theme for the LANBoard client: palettes, Qt stylesheet and painted icons.

Everything visual that is shared between widgets lives here so the app looks
consistent:

* :data:`LIGHT` / :data:`DARK` are colour palettes (plain dicts of design tokens).
* :func:`apply_theme` installs a Qt palette plus a QSS stylesheet built from the
  active tokens and notifies every widget through :func:`manager` ``.changed``.
* :func:`icon` paints vector icons with ``QPainter`` at runtime. There are no image
  files, no icon fonts and nothing is downloaded, so the client works on an
  isolated LAN. Icons are drawn on a 24x24 grid in a stroke style (round caps,
  1.8 px lines), similar to popular open icon sets, and cached per colour/size.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPainterPathStroker, QPalette, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# palettes (design tokens)
# ---------------------------------------------------------------------------

LIGHT: dict[str, str] = {
    "name": "light",
    "bg": "#f4f5f9",          # window background behind panels
    "surface": "#ffffff",     # cards, docks, toolbars
    "surface2": "#f0f2f7",    # inputs, hovered rows, secondary buttons
    "surface3": "#e6e9f1",    # pressed / selected neutral
    "border": "#e1e4ec",
    "border_strong": "#cdd2de",
    "text": "#1b2130",
    "text2": "#4f586b",
    "muted": "#8b93a5",
    "accent": "#4f63e8",
    "accent_hover": "#4254d6",
    "accent_soft": "#e7eafd",
    "accent_text": "#ffffff",
    "success": "#1e9a5a",
    "success_soft": "#e2f5eb",
    "warn": "#c77a00",
    "warn_soft": "#fdf1dc",
    "danger": "#d14343",
    "danger_soft": "#fbe6e6",
    "neutral_soft": "#eceef3",
    "canvas": "#fbfbfd",
    "shadow": "#1b213022",
    "chart_in": "#4f63e8",
    "chart_out": "#e8803a",
    "chart_rtt": "#1e9a5a",
}

DARK: dict[str, str] = {
    "name": "dark",
    "bg": "#121419",
    "surface": "#1b1e25",
    "surface2": "#242832",
    "surface3": "#2e3340",
    "border": "#2c313c",
    "border_strong": "#3b4150",
    "text": "#e7e9ef",
    "text2": "#b0b6c4",
    "muted": "#7d8597",
    "accent": "#7b8bff",
    "accent_hover": "#8e9cff",
    "accent_soft": "#2a3060",
    "accent_text": "#0f1220",
    "success": "#3fcf86",
    "success_soft": "#173a29",
    "warn": "#f0b03c",
    "warn_soft": "#3d2f12",
    "danger": "#ff6b6b",
    "danger_soft": "#401f22",
    "neutral_soft": "#262a33",
    "canvas": "#1f222a",
    "shadow": "#00000066",
    "chart_in": "#7b8bff",
    "chart_out": "#ffa25c",
    "chart_rtt": "#3fcf86",
}

#: The ten colour swatches of the drawing palette (chosen to stay readable on
#: both a white and a dark canvas).
SWATCHES = ["#1b2130", "#e5484d", "#f76b15", "#f5b400", "#30a46c",
            "#12a594", "#0090ff", "#4f63e8", "#8e4ec6", "#ffffff"]


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


# ---------------------------------------------------------------------------
# stylesheet
# ---------------------------------------------------------------------------

def stylesheet(t: dict[str, str]) -> str:
    """Build the application QSS from palette tokens.

    Widgets opt into special looks through ``objectName`` (``#ToolRail``) or
    dynamic properties (``[role="primary"]``) so code never hard-codes colours.
    """
    return f"""
    * {{ outline: 0; }}
    QWidget {{ color: {t['text']}; font-size: 13px; }}
    QMainWindow, QDialog {{ background: {t['bg']}; }}
    QToolTip {{ background: {t['text']}; color: {t['surface']}; border: none;
                padding: 5px 8px; border-radius: 6px; }}

    /* ---- top bar & rail ------------------------------------------------ */
    QToolBar#TopBar {{ background: {t['surface']}; border: none;
                      border-bottom: 1px solid {t['border']}; padding: 6px 10px; spacing: 4px; }}
    QToolBar#TopBar::separator {{ background: {t['border']}; width: 1px; margin: 6px 6px; }}
    #ToolRail {{ background: {t['surface']}; border-right: 1px solid {t['border']}; }}
    #ToolRail QLabel#RailLabel {{ color: {t['muted']}; font-size: 10px; font-weight: 600;
                                  letter-spacing: 1px; }}
    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px;
                   padding: 5px; color: {t['text2']}; }}
    QToolButton:hover {{ background: {t['surface2']}; }}
    QToolButton:pressed {{ background: {t['surface3']}; }}
    QToolButton:checked {{ background: {t['accent_soft']}; color: {t['accent']}; }}
    QToolButton:disabled {{ color: {t['muted']}; }}
    QToolButton#ToolButton:checked {{ background: {t['accent']}; color: {t['accent_text']}; }}
    QToolButton[textBeside="true"] {{ padding: 5px 9px; font-weight: 500; }}
    QToolButton::menu-indicator {{ image: none; width: 0; }}

    /* ---- room title + chips ------------------------------------------- */
    QLabel#RoomName {{ font-size: 15px; font-weight: 650; }}
    QLabel#RoomCode {{ background: {t['surface2']}; color: {t['text2']}; border-radius: 6px;
                       padding: 3px 8px; font-family: "Menlo", "Consolas", monospace;
                       font-weight: 600; letter-spacing: 1px; }}
    QLabel#SyncBadge {{ border-radius: 11px; padding: 3px 11px; font-weight: 600; font-size: 12px; }}
    QLabel#SyncBadge[state="synced"] {{ background: {t['success_soft']}; color: {t['success']}; }}
    QLabel#SyncBadge[state="pending"] {{ background: {t['warn_soft']}; color: {t['warn']}; }}
    QLabel#SyncBadge[state="healing"], QLabel#SyncBadge[state="mismatch"] {{
                       background: {t['danger_soft']}; color: {t['danger']}; }}
    QLabel#SyncBadge[state="offline"] {{ background: {t['neutral_soft']}; color: {t['muted']}; }}

    QLabel[role="h1"] {{ font-size: 24px; font-weight: 700; }}
    QLabel[role="h2"] {{ font-size: 15px; font-weight: 650; }}
    QLabel[role="section"] {{ color: {t['muted']}; font-size: 11px; font-weight: 650;
                             letter-spacing: 1px; }}
    QLabel[role="muted"] {{ color: {t['muted']}; }}
    QLabel[role="hint"] {{ color: {t['text2']}; font-size: 12px; }}
    QLabel[role="error"] {{ color: {t['danger']}; font-weight: 500; }}
    QLabel[role="mono"] {{ font-family: "Menlo", "Consolas", monospace; }}
    QLabel[role="metric"] {{ font-size: 18px; font-weight: 650; }}
    QLabel[role="card"] {{ background: {t['accent_soft']}; color: {t['text']}; border-radius: 8px;
                          padding: 8px 10px; }}

    /* ---- inputs --------------------------------------------------------- */
    QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 8px;
        padding: 6px 9px; selection-background-color: {t['accent']}; selection-color: {t['accent_text']}; }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {t['accent']}; }}
    QLineEdit[invalid="true"] {{ border: 1px solid {t['danger']}; }}
    QLineEdit:disabled, QSpinBox:disabled {{ color: {t['muted']}; background: {t['surface2']}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']};
        selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}

    /* ---- buttons -------------------------------------------------------- */
    QPushButton {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 8px;
                   padding: 7px 14px; font-weight: 500; }}
    QPushButton:hover {{ background: {t['surface3']}; }}
    QPushButton:disabled {{ color: {t['muted']}; }}
    QPushButton[role="primary"] {{ background: {t['accent']}; color: {t['accent_text']}; border: none;
                                   font-weight: 600; }}
    QPushButton[role="primary"]:hover {{ background: {t['accent_hover']}; }}
    QPushButton[role="primary"]:disabled {{ background: {t['border_strong']}; color: {t['surface']}; }}
    QPushButton[role="danger"] {{ background: {t['danger']}; color: white; border: none; font-weight: 700; }}
    QPushButton[role="danger"]:hover {{ background: {t['danger']}; border: 2px solid {t['text']}; }}
    QPushButton[role="ghost"] {{ background: transparent; border: none; color: {t['accent']}; padding: 4px 6px; }}
    QPushButton[role="ghost"]:hover {{ background: {t['accent_soft']}; }}
    QPushButton[role="segment"] {{ background: transparent; border: none; border-radius: 7px; padding: 6px 12px;
                                   color: {t['text2']}; }}
    QPushButton[role="segment"]:checked {{ background: {t['surface']}; color: {t['text']}; font-weight: 600; }}
    QFrame#Segmented {{ background: {t['surface2']}; border-radius: 9px; }}

    /* ---- lists, tables, tabs, docks ------------------------------------ */
    QListWidget, QTreeWidget, QTableView, QTextBrowser {{ background: {t['surface']};
        border: 1px solid {t['border']}; border-radius: 8px; alternate-background-color: {t['surface2']}; }}
    QListWidget::item, QTreeWidget::item {{ padding: 5px 4px; border-radius: 6px; }}
    QListWidget::item:hover, QTreeWidget::item:hover {{ background: {t['surface2']}; }}
    QListWidget::item:selected, QTreeWidget::item:selected, QTableView::item:selected {{
        background: {t['accent_soft']}; color: {t['text']}; }}
    QTableView {{ gridline-color: transparent; font-size: 12px; }}
    QHeaderView::section {{ background: {t['surface2']}; color: {t['text2']}; border: none;
        border-bottom: 1px solid {t['border']}; padding: 4px 6px; font-size: 11px; font-weight: 600; }}
    QDockWidget {{ titlebar-close-icon: none; font-weight: 600; }}
    QDockWidget::title {{ background: {t['surface']}; padding: 6px 10px; border-bottom: 1px solid {t['border']}; }}
    QTabWidget::pane {{ border: none; background: {t['surface']}; }}
    QTabWidget#SideTabs > QWidget {{ background: {t['surface']}; }}
    QTabBar {{ background: {t['surface']}; }}
    QTabBar::tab {{ background: transparent; color: {t['text2']}; padding: 6px 8px; margin: 6px 1px 4px 1px;
                    border-radius: 7px; font-weight: 500; }}
    QTabBar::tab:hover {{ background: {t['surface2']}; }}
    QTabBar::tab:selected {{ background: {t['accent_soft']}; color: {t['accent']}; font-weight: 600; }}
    #SidePanel, #SidePage {{ background: {t['surface']}; }}
    QFrame#Card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; }}
    QFrame#Hero {{ border: none; }}
    QFrame#Banner {{ background: {t['warn_soft']}; border: 1px solid {t['warn']}; border-radius: 10px; }}
    QFrame#Banner[kind="info"] {{ background: {t['accent_soft']}; border: 1px solid {t['accent']}; }}
    QFrame#Toast {{ background: {t['text']}; border-radius: 10px; }}
    QFrame#Toast QLabel {{ color: {t['surface']}; }}
    QFrame#SectionHeader {{ border-top: 1px solid {t['border']}; }}

    /* ---- sliders, checkboxes, scrollbars ------------------------------- */
    QSlider::groove:horizontal {{ height: 4px; background: {t['surface3']}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {t['surface']}; border: 2px solid {t['accent']};
        width: 12px; height: 12px; margin: -6px 0; border-radius: 8px; }}
    QSlider::groove:vertical {{ width: 4px; background: {t['surface3']}; border-radius: 2px; }}
    QSlider::handle:vertical {{ background: {t['surface']}; border: 2px solid {t['accent']};
        width: 12px; height: 12px; margin: 0 -6px; border-radius: 8px; }}
    QCheckBox {{ spacing: 7px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {t['border_strong']};
                            background: {t['surface']}; }}
    QCheckBox::indicator:checked {{ background: {t['accent']}; border: 1px solid {t['accent']}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['border_strong']}; border-radius: 3px; min-height: 30px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t['border_strong']}; border-radius: 3px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QScrollArea {{ border: none; background: transparent; }}
    QSplitter::handle {{ background: {t['border']}; }}
    QSplitter::handle:vertical {{ height: 1px; }}

    /* ---- status bar & menus -------------------------------------------- */
    QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['border']}; color: {t['text2']}; }}
    QStatusBar::item {{ border: none; }}
    QStatusBar QLabel {{ color: {t['text2']}; padding: 0 8px; font-size: 12px; }}
    QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 5px; }}
    QMenu::item {{ padding: 6px 22px 6px 10px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
    QMenu::item:disabled {{ color: {t['muted']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 6px; }}
    QMenu::icon {{ padding-left: 6px; }}
    """


def apply_theme(app: QApplication | None = None, mode: str = "light") -> dict[str, str]:
    """Activate the ``"light"`` or ``"dark"`` theme for the whole application."""
    app = app or QApplication.instance()
    pal_tokens = DARK if mode == "dark" else LIGHT
    m = manager()
    m.palette = pal_tokens
    _ICON_CACHE.clear()
    if app is not None:
        app.setStyle("Fusion")             # consistent base look on every OS
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
    grad.setColorAt(0, QColor("#6a7dff"))
    grad.setColorAt(1, QColor("#3a49c9"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    r = rect.width() * 0.26
    p.drawRoundedRect(rect, r, r)
    s = rect.width() / 24.0
    p.translate(rect.topLeft())
    p.scale(s, s)
    pen = _pen(QColor("white"), 2.2)
    p.setPen(pen)
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
