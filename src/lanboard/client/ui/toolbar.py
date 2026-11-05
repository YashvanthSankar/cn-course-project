"""Left tool rail: drawing tools, colour palette, stroke width, fill and Smart Ink.

The rail only talks to the canvas through the public CanvasView API
(``set_tool``, ``set_color``, ``set_width``, ``set_fill``, ``set_smart_ink``) and
listens to ``tool_changed`` so the highlighted button always matches the
canvas, even when the canvas switches tool by itself (e.g. after placing text).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QScrollArea, QSlider, QToolButton, QVBoxLayout, QWidget)

from . import theme

#: (tool name passed to canvas.set_tool, label, shortcut key, icon name, needs edit rights)
TOOLS: list[tuple[str, str, str, str, bool]] = [
    ("select", "Select", "V", "select", False),
    ("pan", "Pan", "H", "hand", False),
    ("pen", "Pen", "P", "pen", True),
    ("highlighter", "Highlighter", "M", "highlighter", True),
    ("eraser", "Eraser", "E", "eraser", True),
    ("line", "Line", "L", "line", True),
    ("arrow", "Arrow", "A", "arrow", True),
    ("rect", "Rectangle", "R", "rect", True),
    ("ellipse", "Ellipse", "O", "ellipse", True),
    ("text", "Text", "T", "text", True),
    ("sticky", "Sticky note", "N", "sticky", True),
    ("laser", "Laser pointer (not saved)", "K", "laser", False),
]

WIDTH_MIN, WIDTH_MAX, WIDTH_DEFAULT = 1, 40, 4


class _Swatch(QToolButton):
    """A round colour chip. Checked = currently selected colour."""

    def __init__(self, hex_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hex = hex_color
        self.setCheckable(True)
        self.setFixedSize(21, 21)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(hex_color)

    def set_hex(self, hex_color: str) -> None:
        self.hex = hex_color
        self.setToolTip(hex_color)
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(3, 3, self.width() - 6, self.height() - 6)
        if self.isChecked():
            p.setPen(QPen(QColor(t["accent"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(1, 1, self.width() - 2, self.height() - 2))
        p.setPen(QPen(QColor(t["border_strong"]), 1))
        p.setBrush(QColor(self.hex))
        p.drawEllipse(r)
        p.end()


class _WidthPreview(QWidget):
    """Shows the current stroke width as a dot in the current colour."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.width_px = WIDTH_DEFAULT
        self.color = theme.SWATCHES[0]
        self.setFixedHeight(46)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        # paper-coloured background so the dot reads as it will on the canvas
        p.setPen(QPen(QColor(t["border"]), 1))
        p.setBrush(QColor(theme.LIGHT["canvas"]))
        p.drawRoundedRect(QRectF(2.5, 2.5, self.width() - 5, self.height() - 5), 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        d = max(2.0, min(float(self.width_px), self.height() - 10.0))
        p.setBrush(QColor(self.color))
        p.setPen(QPen(QColor(t["border_strong"]), 1) if QColor(self.color).lightness() > 235 else Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d))
        p.end()


class ToolRail(QWidget):
    """The vertical tool rail on the left of the main window."""

    color_changed = Signal(str)

    def __init__(self, canvas, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self.setObjectName("ToolRail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.current_color = theme.SWATCHES[0]
        self.fill_on = False
        self.tool_buttons: dict[str, QToolButton] = {}
        self.tool_actions: dict[str, QAction] = {}
        self._can_edit = True
        self._tool_before_lock: str | None = None   # tool to restore when edit rights return

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("ToolRail")
        inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(9, 12, 9, 12)
        lay.setSpacing(8)

        # --- tool buttons (2 columns) ---------------------------------------
        lay.addWidget(self._rail_label("TOOLS"))
        grid = QGridLayout()
        grid.setSpacing(4)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, (name, text, key, icon_name, _edit) in enumerate(TOOLS):
            b = QToolButton()
            b.setObjectName("ToolButton")
            b.setCheckable(True)
            b.setFixedSize(38, 38)
            b.setIconSize(QSize(20, 20))
            b.setToolTip(f"<b>{text}</b>&nbsp;&nbsp;<span style='opacity:.7'>{key}</span>")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("tool", name)
            b.clicked.connect(lambda _=False, n=name: self.select_tool(n))
            self.group.addButton(b)
            grid.addWidget(b, i // 2, i % 2)
            self.tool_buttons[name] = b
            # single-key shortcut lives on an action so it works window-wide
            act = QAction(text, self)
            act.setShortcut(QKeySequence(key))
            act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            act.triggered.connect(lambda _=False, n=name: self.select_tool(n))
            self.tool_actions[name] = act
            self.addAction(act)
        lay.addLayout(grid)

        # --- colours ---------------------------------------------------------
        lay.addSpacing(6)
        lay.addWidget(self._rail_label("COLOUR"))
        cgrid = QGridLayout()
        cgrid.setSpacing(3)
        self.swatch_group = QButtonGroup(self)
        self.swatch_group.setExclusive(True)
        self.swatches: list[_Swatch] = []
        for i, hx in enumerate(theme.SWATCHES):
            s = _Swatch(hx)
            s.clicked.connect(lambda _=False, sw=s: self.set_color(sw.hex))
            self.swatch_group.addButton(s)
            self.swatches.append(s)
            cgrid.addWidget(s, i // 4, i % 4)
        self.custom_swatch = _Swatch("#ff66aa")
        self.custom_swatch.setToolTip("Last custom colour")
        self.custom_swatch.clicked.connect(lambda: self.set_color(self.custom_swatch.hex))
        self.swatch_group.addButton(self.custom_swatch)
        n = len(theme.SWATCHES)
        cgrid.addWidget(self.custom_swatch, n // 4, n % 4)
        self.more_btn = QToolButton()
        self.more_btn.setFixedSize(21, 21)
        self.more_btn.setIconSize(QSize(14, 14))
        self.more_btn.setToolTip("Custom colour…")
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.clicked.connect(self.pick_custom_color)
        cgrid.addWidget(self.more_btn, (n + 1) // 4, (n + 1) % 4)
        lay.addLayout(cgrid)

        # --- width -----------------------------------------------------------
        lay.addSpacing(6)
        lay.addWidget(self._rail_label("WIDTH"))
        self.preview = _WidthPreview()
        lay.addWidget(self.preview)
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(WIDTH_MIN, WIDTH_MAX)
        self.width_slider.setValue(WIDTH_DEFAULT)
        self.width_slider.setToolTip("Stroke width")
        self.width_slider.valueChanged.connect(self.set_width)
        lay.addWidget(self.width_slider)
        self.width_label = QLabel(f"{WIDTH_DEFAULT} px")
        self.width_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.width_label.setProperty("role", "muted")
        lay.addWidget(self.width_label)

        # --- fill + smart ink ------------------------------------------------
        lay.addSpacing(6)
        lay.addWidget(self._rail_label("OPTIONS"))
        row = QHBoxLayout()
        row.setSpacing(4)
        self.fill_btn = QToolButton()
        self.fill_btn.setCheckable(True)
        self.fill_btn.setFixedSize(38, 38)
        self.fill_btn.setIconSize(QSize(20, 20))
        self.fill_btn.setToolTip("<b>Fill shapes</b><br>Rectangles and ellipses are filled with the current colour")
        self.fill_btn.toggled.connect(self.set_fill)
        row.addWidget(self.fill_btn)
        self.smart_btn = QToolButton()
        self.smart_btn.setCheckable(True)
        self.smart_btn.setFixedSize(38, 38)
        self.smart_btn.setIconSize(QSize(20, 20))
        self.smart_btn.setToolTip("<b>Smart Ink</b><br>Hold the pen still at the end of a stroke to snap "
                                  "rough circles, boxes, lines and arrows into clean shapes")
        self.smart_btn.toggled.connect(self.set_smart_ink)
        row.addWidget(self.smart_btn)
        lay.addLayout(row)
        lay.addStretch(1)

        self.setFixedWidth(110)
        self.retheme()
        theme.manager().changed.connect(lambda _t: self.retheme())

        # initial state pushed to the canvas so both sides agree
        self.swatches[0].setChecked(True)
        if hasattr(canvas, "tool_changed"):
            canvas.tool_changed.connect(self.reflect_tool)
        self.select_tool("pen")
        self._call("set_color", self.current_color)
        self._call("set_width", WIDTH_DEFAULT)
        self._call("set_fill", None)
        self._call("set_smart_ink", False)

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _rail_label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("RailLabel")
        return lab

    def _call(self, method: str, *args) -> None:
        fn = getattr(self.canvas, method, None)
        if callable(fn):
            fn(*args)

    def retheme(self) -> None:
        t = theme.tokens()
        for name, _text, _key, icon_name, _e in TOOLS:
            self.tool_buttons[name].setIcon(theme.icon(icon_name, t["text2"], 20, on_color=t["accent_text"]))
        self.more_btn.setIcon(theme.icon("plus", t["text2"], 14))
        self.fill_btn.setIcon(theme.icon("fill", t["text2"], 20, on_color=t["accent"]))
        self.smart_btn.setIcon(theme.icon("smart", t["text2"], 20, on_color=t["accent"]))
        self.update()

    # -- tool / style setters ----------------------------------------------------
    def select_tool(self, name: str) -> None:
        """User picked a tool: tell the canvas and highlight the button."""
        b = self.tool_buttons.get(name)
        if b is None or not b.isEnabled():
            return
        b.setChecked(True)
        self._call("set_tool", name)

    def reflect_tool(self, name: str) -> None:
        """Canvas changed tool by itself: just update the highlight."""
        b = self.tool_buttons.get(name)
        if b is not None:
            b.setChecked(True)

    def current_tool(self) -> str | None:
        b = self.group.checkedButton()
        return b.property("tool") if b else None

    def set_color(self, hex_color: str) -> None:
        self.current_color = hex_color
        for s in self.swatches + [self.custom_swatch]:
            if s.hex.lower() == hex_color.lower():
                s.setChecked(True)
                break
        self.preview.color = hex_color
        self.preview.update()
        self._call("set_color", hex_color)
        if self.fill_on:
            self._call("set_fill", hex_color)
        self.color_changed.emit(hex_color)

    def pick_custom_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.current_color), self, "Pick a colour")
        if c.isValid():
            self.custom_swatch.set_hex(c.name())
            self.custom_swatch.setChecked(True)
            self.set_color(c.name())

    def set_width(self, w: int) -> None:
        if self.width_slider.value() != w:
            self.width_slider.setValue(w)
            return                          # valueChanged re-enters with the same w
        self.preview.width_px = w
        self.preview.update()
        self.width_label.setText(f"{w} px")
        self._call("set_width", int(w))

    def set_fill(self, on: bool) -> None:
        self.fill_on = bool(on)
        if self.fill_btn.isChecked() != self.fill_on:
            self.fill_btn.setChecked(self.fill_on)
        self._call("set_fill", self.current_color if self.fill_on else None)

    def set_smart_ink(self, on: bool) -> None:
        if self.smart_btn.isChecked() != bool(on):
            self.smart_btn.setChecked(bool(on))
        self._call("set_smart_ink", bool(on))

    def set_can_edit(self, can_edit: bool) -> None:
        """Viewers keep select/pan/laser; drawing tools are disabled."""
        if can_edit == self._can_edit:
            return
        self._can_edit = can_edit
        for name, _t, _k, _i, needs_edit in TOOLS:
            if needs_edit:
                self.tool_buttons[name].setEnabled(can_edit)
                self.tool_actions[name].setEnabled(can_edit)
        if not can_edit and self.current_tool() not in ("select", "pan", "laser"):
            self._tool_before_lock = self.current_tool()
            self.select_tool("pan")
        elif can_edit and self._tool_before_lock:
            if self.current_tool() == "pan":
                self.select_tool(self._tool_before_lock)
            self._tool_before_lock = None
