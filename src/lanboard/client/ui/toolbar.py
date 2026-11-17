"""Floating drawing controls over the canvas: the tool palette and the style bar.

* :class:`ToolPalette` is a rounded vertical card on the left of the canvas with
  one button per tool (grouped: navigate · ink · shapes · notes · present). Each
  button shows its single-key shortcut in the corner. When the window is too
  short for one column it switches to two.
* :class:`StyleBar` is a contextual card at the bottom of the canvas. It only
  shows what matters for the active tool, or for the current selection when
  the Select tool is active: colours, stroke width, fill and Smart Ink.

Both only talk to the canvas through the public CanvasView API (``set_tool``,
``set_color``, ``set_width``, ``set_fill``, ``set_smart_ink``) and follow
``tool_changed`` so the highlighted button always matches the canvas, even when
the canvas switches tool by itself (e.g. after placing text).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QGridLayout, QHBoxLayout, QLabel, QSlider,
                               QToolButton, QWidget)

from . import theme
from .widgets import FloatingCard

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
GROUP_ENDS = {"pan", "eraser", "ellipse", "sticky"}

WIDTH_MIN, WIDTH_MAX, WIDTH_DEFAULT = 1, 40, 4

TOOL_SECTIONS: dict[str, set[str]] = {
    "pen": {"color", "width", "smart"},
    "highlighter": {"color", "width"},
    "eraser": {"width"},
    "line": {"color", "width"},
    "arrow": {"color", "width"},
    "rect": {"color", "width", "fill"},
    "ellipse": {"color", "width", "fill"},
    "text": {"color"},
    "sticky": {"color", "fill"},
}
TYPE_SECTIONS: dict[str, set[str]] = {
    "stroke": {"color", "width"}, "line": {"color", "width"}, "arrow": {"color", "width"},
    "rect": {"color", "width", "fill"}, "ellipse": {"color", "width", "fill"},
    "text": {"color"}, "sticky": {"color", "fill"},
}
TOOL_TITLES = {name: text.split(" (")[0] for name, text, *_ in TOOLS}

try:
    from lanboard.client.canvas.items import display_color as _board_color
except Exception:                       # pragma: no cover - canvas package without the helper
    def _board_color(c: QColor) -> QColor:
        return c


def shown_color(hex_color: str) -> QColor:
    """The colour ``hex_color`` ink will actually *appear* as on the board.

    On the dark board the canvas shows very dark ink light and near-white ink
    dark (display only, the stored colour is unchanged). Swatches and the width
    preview are painted through the same mapping so they never lie.
    """
    c = QColor(hex_color)
    return _board_color(c) if theme.manager().dark else c


class _ToolButton(QToolButton):
    """A square tool button: gradient when active, shortcut letter in the corner."""

    SIZE = 40

    def __init__(self, name: str, key: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name, self.key, self.icon_name = name, key, icon_name
        self.setCheckable(True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        on, en = self.isChecked(), self.isEnabled()
        p.setPen(Qt.PenStyle.NoPen)
        if on:
            glow = QColor(t["accent"])
            glow.setAlphaF(0.28)
            p.setBrush(glow)
            p.drawRoundedRect(r.adjusted(-1, 0, 1, 2), 11, 11)
            p.setBrush(theme.accent_gradient(r, t))
            p.drawRoundedRect(r, 10, 10)
        elif self.underMouse() and en:
            p.setBrush(QColor(t["surface2"] if not theme.manager().dark else t["surface3"]))
            p.drawRoundedRect(r, 10, 10)
        col = t["accent_text"] if on else (t["text"] if en else t["border_strong"])
        theme.icon(self.icon_name, col, 20).paint(p, QRectF(r.center().x() - 10, r.center().y() - 10, 20, 20).toRect())
        f = theme.ui_font(8, QFont.Weight.DemiBold)
        p.setFont(f)
        kc = QColor(col)
        kc.setAlphaF(0.75 if on else 0.55)
        p.setPen(kc)
        p.drawText(QRectF(r.right() - 11, r.bottom() - 11, 9, 10), Qt.AlignmentFlag.AlignCenter, self.key)
        p.end()


class _Divider(QWidget):
    """A short hairline between tool groups (horizontal in one column, vertical in two)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(9)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setPen(QPen(QColor(theme.tokens()["border"]), 1))
        y = self.height() / 2
        p.drawLine(int(self.width() * 0.22), int(y), int(self.width() * 0.78), int(y))
        p.end()


class ToolPalette(FloatingCard):
    """The floating vertical tool palette on the left of the canvas."""

    def __init__(self, canvas, parent: QWidget | None = None) -> None:
        super().__init__(parent, radius=16, padding=(5, 6, 5, 6))
        self.canvas = canvas
        self.setObjectName("ToolPalette")
        self.tool_buttons: dict[str, _ToolButton] = {}
        self.tool_actions: dict[str, QAction] = {}
        self._can_edit = True
        self._tool_before_lock: str | None = None
        self._columns = 1
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._dividers: list[_Divider] = []
        for name, text, key, icon_name, _edit in TOOLS:
            b = _ToolButton(name, key, icon_name, self)
            b.setToolTip(f"<b>{text}</b>&nbsp;&nbsp;{key}")
            b.setProperty("tool", name)
            b.clicked.connect(lambda _=False, n=name: self.select_tool(n))
            self.group.addButton(b)
            self.tool_buttons[name] = b
            act = QAction(text, self)
            act.setShortcut(QKeySequence(key))
            act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            act.triggered.connect(lambda _=False, n=name: self.select_tool(n))
            self.tool_actions[name] = act
            self.addAction(act)
            if name in GROUP_ENDS:
                self._dividers.append(_Divider(self))
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(*self.inner_margins())
        self.grid.setHorizontalSpacing(2)
        self.grid.setVerticalSpacing(2)
        self._relayout()
        theme.on_theme_change(self, self.update)

        if hasattr(canvas, "tool_changed"):
            canvas.tool_changed.connect(self.reflect_tool)
        self.select_tool("pen")

    def _relayout(self) -> None:
        """Place buttons in one column (with dividers) or two (compact windows)."""
        while self.grid.count():
            self.grid.takeAt(0)
        names = [n for n, *_ in TOOLS]
        if self._columns == 1:
            row, d = 0, 0
            for n in names:
                self.grid.addWidget(self.tool_buttons[n], row, 0)
                row += 1
                if n in GROUP_ENDS:
                    self._dividers[d].show()
                    self.grid.addWidget(self._dividers[d], row, 0)
                    d += 1
                    row += 1
        else:
            for div in self._dividers:
                div.hide()
            for i, n in enumerate(names):
                self.grid.addWidget(self.tool_buttons[n], i // 2, i % 2)
        self.adjustSize()

    def fit_height(self, available: int) -> None:
        """Switch between one and two columns so the palette fits ``available`` px."""
        one_col = len(TOOLS) * (_ToolButton.SIZE + 2) + len(GROUP_ENDS) * 11 + 40
        cols = 1 if available >= one_col else 2
        if cols != self._columns:
            self._columns = cols
            self._relayout()

    def _call(self, method: str, *args) -> None:
        fn = getattr(self.canvas, method, None)
        if callable(fn):
            fn(*args)

    def retheme(self) -> None:
        self.update()

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


ToolRail = ToolPalette


class _Swatch(QToolButton):
    """A round colour chip. Checked = currently selected colour."""

    def __init__(self, hex_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hex = hex_color
        self.setCheckable(True)
        self.setFixedSize(24, 24)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        full = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isChecked():
            p.setPen(QPen(QColor(t["accent"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(full)
            r = full.adjusted(4, 4, -4, -4)
        else:
            r = full.adjusted(3, 3, -3, -3)
        c = shown_color(self.hex)
        ring = QColor(t["border_strong"])
        p.setPen(QPen(ring, 1) if c.lightness() > 225 or c.lightness() < 45 else Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(r)
        p.end()


class _WidthPreview(QWidget):
    """Shows the current stroke width as a dot in the current colour."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.width_px = WIDTH_DEFAULT
        self.color = theme.SWATCHES[0]
        self.setFixedSize(30, 30)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["canvas"]))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        d = max(2.0, min(float(self.width_px), self.height() - 8.0))
        c = shown_color(self.color)
        p.setBrush(c)
        p.setPen(QPen(QColor(t["border_strong"]), 1) if abs(c.lightness() - QColor(t["canvas"]).lightness()) < 40
                 else Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d))
        p.end()


class _Group(QWidget):
    """One labelled section of the style bar (a horizontal row of controls)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(2)


class StyleBar(FloatingCard):
    """Contextual style controls. Call :meth:`set_context` when tool or selection changes."""

    color_changed = Signal(str)
    layout_changed = Signal()

    def __init__(self, canvas, parent: QWidget | None = None) -> None:
        super().__init__(parent, radius=14, padding=(10, 5, 10, 5))
        self.canvas = canvas
        self.setObjectName("StyleBar")
        self.current_color = theme.SWATCHES[0]
        self.fill_on = False
        self._context: tuple = ()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(*self.inner_margins())
        lay.setSpacing(10)

        self.title = QLabel("PEN")
        self.title.setStyleSheet("font-size: 10px; font-weight: 700;")
        lay.addWidget(self.title)

        self.color_group = _Group()
        self.swatch_group = QButtonGroup(self)
        self.swatch_group.setExclusive(True)
        self.swatches: list[_Swatch] = []
        for hx in theme.SWATCHES:
            s = _Swatch(hx)
            s.clicked.connect(lambda _=False, sw=s: self.set_color(sw.hex))
            self.swatch_group.addButton(s)
            self.swatches.append(s)
            self.color_group.row.addWidget(s)
        self.custom_swatch = _Swatch("#ff66aa")
        self.custom_swatch.setToolTip("Last custom colour")
        self.custom_swatch.clicked.connect(lambda: self.set_color(self.custom_swatch.hex))
        self.swatch_group.addButton(self.custom_swatch)
        self.color_group.row.addWidget(self.custom_swatch)
        self.more_btn = QToolButton()
        self.more_btn.setFixedSize(26, 26)
        self.more_btn.setIconSize(QSize(14, 14))
        self.more_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more_btn.setToolTip("Custom colour…")
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.clicked.connect(self.pick_custom_color)
        self.color_group.row.addWidget(self.more_btn)
        lay.addWidget(self.color_group)

        self.width_group = _Group()
        self.width_group.row.setSpacing(8)
        self.preview = _WidthPreview()
        self.width_group.row.addWidget(self.preview)
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(WIDTH_MIN, WIDTH_MAX)
        self.width_slider.setValue(WIDTH_DEFAULT)
        self.width_slider.setFixedWidth(110)
        self.width_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.width_slider.setToolTip("Stroke width")
        self.width_slider.valueChanged.connect(self.set_width)
        self.width_group.row.addWidget(self.width_slider)
        self.width_label = QLabel(f"{WIDTH_DEFAULT} px")
        self.width_label.setProperty("role", "mono")
        self.width_label.setFixedWidth(38)
        self.width_group.row.addWidget(self.width_label)
        lay.addWidget(self.width_group)

        self.opt_group = _Group()
        self.opt_group.row.setSpacing(4)
        self.fill_btn = QToolButton()
        self.fill_btn.setCheckable(True)
        self.fill_btn.setFixedSize(32, 32)
        self.fill_btn.setIconSize(QSize(18, 18))
        self.fill_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fill_btn.setToolTip("<b>Fill shapes</b><br>Rectangles and ellipses are filled with the current colour")
        self.fill_btn.toggled.connect(self.set_fill)
        self.opt_group.row.addWidget(self.fill_btn)
        self.smart_btn = QToolButton()
        self.smart_btn.setCheckable(True)
        self.smart_btn.setFixedSize(32, 32)
        self.smart_btn.setIconSize(QSize(18, 18))
        self.smart_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.smart_btn.setToolTip("<b>Smart Ink</b><br>Hold the pen still at the end of a stroke to snap "
                                  "rough circles, boxes, lines and arrows into clean shapes")
        self.smart_btn.toggled.connect(self.set_smart_ink)
        self.opt_group.row.addWidget(self.smart_btn)
        lay.addWidget(self.opt_group)

        self.sections = {"color": self.color_group, "width": self.width_group}
        self.retheme()
        theme.on_theme_change(self, self.retheme)

        self.swatches[0].setChecked(True)
        self._call("set_color", self.current_color)
        self._call("set_width", WIDTH_DEFAULT)
        self._call("set_fill", None)
        self._call("set_smart_ink", False)
        self.set_context("pen")

    def _call(self, method: str, *args) -> None:
        fn = getattr(self.canvas, method, None)
        if callable(fn):
            fn(*args)

    def retheme(self) -> None:
        t = theme.tokens()
        self.title.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {t['muted']};")
        self.more_btn.setIcon(theme.icon("plus", t["text2"], 14))
        for s in self.swatches + [self.custom_swatch]:
            s.update()
        self.preview.update()
        self.fill_btn.setIcon(theme.icon("fill", t["text2"], 18, on_color=t["accent"]))
        self.smart_btn.setIcon(theme.icon("smart", t["text2"], 18, on_color=t["accent"]))
        self.update()

    def set_context(self, tool: str | None, selected_types: set[str] | None = None) -> bool:
        """Show only the sections that matter. Returns False when nothing applies (bar hidden)."""
        if tool == "select":
            wanted: set[str] = set()
            for typ in selected_types or ():
                wanted |= TYPE_SECTIONS.get(typ, set())
            title = "SELECTION"
        else:
            wanted = TOOL_SECTIONS.get(tool or "", set())
            title = TOOL_TITLES.get(tool or "", "").upper()
        key = (tool, tuple(sorted(wanted)))
        if key == self._context:
            return bool(wanted)
        self._context = key
        self.title.setText(title)
        self.color_group.setVisible("color" in wanted)
        self.width_group.setVisible("width" in wanted)
        self.fill_btn.setVisible("fill" in wanted)
        self.smart_btn.setVisible("smart" in wanted)
        self.opt_group.setVisible(bool(wanted & {"fill", "smart"}))
        self._relayout()
        self.layout_changed.emit()
        return bool(wanted)

    def set_compact(self, on: bool) -> None:
        """Narrow form for small windows: no title, 6 swatches, a shorter width slider."""
        on = bool(on)
        if getattr(self, "_compact", False) == on:
            return
        self._compact = on
        self.title.setVisible(not on)
        for i, sw in enumerate(self.swatches):
            sw.setVisible(not on or i < 6 or sw.isChecked())
        self.custom_swatch.setVisible(not on or self.custom_swatch.isChecked())
        self.width_slider.setFixedWidth(52 if on else 110)
        self.width_label.setVisible(not on)
        self.layout().setSpacing(6 if on else 10)
        self._relayout()

    def _relayout(self) -> None:
        """Recompute sizes now (hidden children only shrink a layout after it is re-activated)."""
        for g in (self.color_group, self.width_group, self.opt_group):
            g.layout().invalidate()
            g.layout().activate()
            g.adjustSize()
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()

    def has_content(self) -> bool:
        return bool(self._context and self._context[1])

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
        c = QColorDialog.getColor(QColor(self.current_color), self.window(), "Pick a colour")
        if c.isValid():
            self.custom_swatch.set_hex(c.name())
            self.custom_swatch.setChecked(True)
            self.set_color(c.name())

    def set_width(self, w: int) -> None:
        if self.width_slider.value() != w:
            self.width_slider.setValue(w)
            return
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
