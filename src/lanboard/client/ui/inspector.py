"""Network Inspector (S1): a Wireshark-style live list of every frame.

``net.frame_logged`` emits one dict per frame sent or received::

    {t, dir: "in"|"out", type: int, name: str, size: int, payload: dict, chaos: str|None}

Design for heavy traffic (cursor + live strokes can be hundreds of frames/s):

* The slot only appends to a plain Python list (O(1), no Qt work).
* A 100 ms timer moves the pending frames into :class:`FrameModel` in one batch.
* :class:`FrameModel` keeps a ring buffer (``deque(maxlen=2000)``) of all frames
  and a filtered row list on top of it. A batch costs one ``beginRemoveRows``
  for frames that fell off the ring and one ``beginInsertRows`` for new rows, so
  the view never resets during capture and memory stays bounded.
"""

from __future__ import annotations

import json
import time
from collections import deque

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QPlainTextEdit,
                               QSplitter, QTableView, QToolButton, QVBoxLayout, QWidget)

from lanboard.common.messages import EPHEMERAL, MsgType, type_name

from . import theme
from .widgets import ToggleSwitch

RING_SIZE = 2000
FLUSH_MS = 100
COLUMNS = ("Time", "", "Type", "Size", "Seq", "Chaos")
#: Frames hidden by "hide cursor/live frames": high-rate presence traffic.
NOISY_TYPES = frozenset(int(t) for t in EPHEMERAL if t != MsgType.SIGNAL) | {int(MsgType.PING), int(MsgType.PONG)}


def frame_seq(payload) -> str:
    """The seq shown in the table: payload seq, or the range of an OP_BATCH."""
    if not isinstance(payload, dict):
        return ""
    if "seq" in payload:
        return str(payload["seq"])
    ops = payload.get("ops")
    if isinstance(ops, list) and ops and isinstance(ops[0], dict) and "seq" in ops[0]:
        return f"{ops[0]['seq']}–{ops[-1].get('seq', '')}"
    if "last_seq" in payload:
        return f"≥{payload['last_seq']}"
    return ""


def _shorten(value, limit: int = 160):
    """Truncate long strings (base64 blob chunks) and huge lists for the detail view."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… (+{len(value) - limit} chars)"
    if isinstance(value, list):
        if len(value) > 60:
            return [_shorten(v, limit) for v in value[:60]] + [f"… (+{len(value) - 60} items)"]
        return [_shorten(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: _shorten(v, limit) for k, v in value.items()}
    return value


class FrameModel(QAbstractTableModel):
    """Table model over a bounded ring buffer of frames, with type filters."""

    def __init__(self, capacity: int = RING_SIZE, parent=None) -> None:
        super().__init__(parent)
        self.capacity = capacity
        self.frames: deque[tuple[int, dict]] = deque(maxlen=capacity)   # (serial, frame)
        self.rows: deque[tuple[int, dict]] = deque()                    # filtered view
        self._serial = 0
        self.type_filter: int | None = None
        self.hide_noisy = False
        self._t0: float | None = None
        self._mono = QFont("Menlo")
        self._mono.setStyleHint(QFont.StyleHint.Monospace)
        self._mono.setPixelSize(11)

    # -- filtering -----------------------------------------------------------------
    def accepts(self, f: dict) -> bool:
        t = f.get("type")
        if self.type_filter is not None and t != self.type_filter:
            return False
        if self.hide_noisy and t in NOISY_TYPES:
            return False
        return True

    def set_filter(self, type_filter: int | None = None, hide_noisy: bool | None = None) -> None:
        self.type_filter = type_filter
        if hide_noisy is not None:
            self.hide_noisy = hide_noisy
        self.beginResetModel()
        self.rows = deque(item for item in self.frames if self.accepts(item[1]))
        self.endResetModel()

    # -- mutation --------------------------------------------------------------------
    def add_frames(self, batch: list[dict]) -> None:
        """Append a batch (oldest first). Evicts from the front of the ring."""
        if not batch:
            return
        if len(batch) > self.capacity:
            batch = batch[-self.capacity:]
        if self._t0 is None:
            self._t0 = float(batch[0].get("t", time.time()) or 0)
        new_items = []
        for f in batch:
            self._serial += 1
            new_items.append((self._serial, f))
        self.frames.extend(new_items)            # the deque evicts the oldest itself
        first_kept = self.frames[0][0]
        # rows whose frame fell off the ring are always at the front of ``rows``
        drop = 0
        for serial, _f in self.rows:
            if serial >= first_kept:
                break
            drop += 1
        if drop:
            self.beginRemoveRows(QModelIndex(), 0, drop - 1)
            for _ in range(drop):
                self.rows.popleft()
            self.endRemoveRows()
        accepted = [item for item in new_items if item[0] >= first_kept and self.accepts(item[1])]
        if accepted:
            start = len(self.rows)
            self.beginInsertRows(QModelIndex(), start, start + len(accepted) - 1)
            self.rows.extend(accepted)
            self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.frames.clear()
        self.rows.clear()
        self._t0 = None
        self.endResetModel()

    def frame(self, row: int) -> dict | None:
        return self.rows[row][1] if 0 <= row < len(self.rows) else None

    # -- Qt model API --------------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def _time_text(self, t) -> str:
        try:
            t = float(t)
        except (TypeError, ValueError):
            return ""
        if t > 1e9:                                   # wall clock
            return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"
        return f"{t - (self._t0 or 0):9.3f}"           # monotonic: seconds since capture start

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        f = self.rows[index.row()][1]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return self._time_text(f.get("t"))
            if col == 1:
                return "↑" if f.get("dir") == "out" else "↓"
            if col == 2:
                return f.get("name") or type_name(f.get("type", 0))
            if col == 3:
                return f"{f.get('size', 0):,}"
            if col == 4:
                return frame_seq(f.get("payload"))
            if col == 5:
                return f.get("chaos") or ""
        elif role == Qt.ItemDataRole.ForegroundRole:
            t = theme.tokens()
            if col == 1:
                return QColor(t["chart_out"] if f.get("dir") == "out" else t["chart_in"])
            if col == 5 and f.get("chaos"):
                return QColor(t["danger"] if "drop" in str(f.get("chaos")) else t["warn"])
            if col == 0:
                return QColor(t["muted"])
        elif role == Qt.ItemDataRole.BackgroundRole:
            if f.get("chaos"):
                t = theme.tokens()
                return QColor(t["danger_soft"] if "drop" in str(f.get("chaos")) else t["warn_soft"])
        elif role == Qt.ItemDataRole.FontRole:
            if col in (0, 3, 4):
                return self._mono
            if col == 2:
                bf = QFont()
                bf.setPixelSize(12)
                bf.setWeight(QFont.Weight.DemiBold)
                return bf
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (1,):
                return int(Qt.AlignmentFlag.AlignCenter)
            if col in (3, 4):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class InspectorPanel(QWidget):
    """Filter bar + frame table + pretty JSON detail pane."""

    def __init__(self, net, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePage")
        self.net = net
        self.pending: list[dict] = []
        self.paused = False
        self.total_seen = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.type_combo = QComboBox()
        self.type_combo.addItem("All types", None)
        for mt in sorted(MsgType, key=lambda m: m.name):
            self.type_combo.addItem(mt.name, int(mt))
        self.type_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.type_combo, 1)
        self.pause_btn = QToolButton()
        self.pause_btn.setCheckable(True)
        self.pause_btn.setToolTip("Pause capture")
        self.pause_btn.toggled.connect(self.set_paused)
        bar.addWidget(self.pause_btn)
        self.clear_btn = QToolButton()
        self.clear_btn.setToolTip("Clear captured frames")
        self.clear_btn.clicked.connect(self.clear)
        bar.addWidget(self.clear_btn)
        lay.addLayout(bar)
        self.hide_switch = ToggleSwitch("Hide cursor / live / ping frames")
        self.hide_switch.setChecked(True)
        self.hide_switch.toggled.connect(self._apply_filter)
        lay.addWidget(self.hide_switch)

        self.model = FrameModel(RING_SIZE, self)
        self.model.hide_noisy = True
        self.table = QTableView()
        self.table.setMinimumHeight(220)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        for col, width in enumerate((94, 20, 76, 46, 50)):
            hh.resizeSection(col, width)
        hh.setStretchLastSection(True)
        self.table.selectionModel().currentRowChanged.connect(self._show_detail)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Select a frame to see its decoded JSON payload")
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPixelSize(11)
        self.detail.setFont(mono)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(FLUSH_MS)
        self.timer.timeout.connect(self.flush)
        self.timer.start()
        net.frame_logged.connect(self.on_frame)
        theme.manager().changed.connect(lambda *_: self.retheme())
        self.retheme()

    def retheme(self) -> None:
        t = theme.tokens()
        self.pause_btn.setIcon(theme.icon("play" if self.paused else "pause", t["text2"], 16))
        self.clear_btn.setIcon(theme.icon("trash", t["text2"], 16))

    # -- capture --------------------------------------------------------------------
    def on_frame(self, frame: dict) -> None:
        """Slot for ``net.frame_logged``: O(1), real work happens in :meth:`flush`."""
        if self.paused:
            return
        self.pending.append(frame)
        self.total_seen += 1
        if len(self.pending) > RING_SIZE * 2:        # UI stalled: keep only what fits
            del self.pending[:-RING_SIZE]

    def flush(self) -> None:
        if not self.pending:
            return
        batch, self.pending = self.pending, []
        sb = self.table.verticalScrollBar()
        follow = sb.value() >= sb.maximum() - 2
        self.model.add_frames(batch)
        if follow and not self.table.selectionModel().hasSelection():
            self.table.scrollToBottom()

    def set_paused(self, on: bool) -> None:
        self.paused = bool(on)
        self.pause_btn.setToolTip("Resume capture" if on else "Pause capture")
        self.retheme()

    def clear(self) -> None:
        self.pending.clear()
        self.model.clear()
        self.detail.clear()

    def _apply_filter(self, *_args) -> None:
        self.model.set_filter(self.type_combo.currentData(), self.hide_switch.isChecked())

    def _show_detail(self, current, _previous) -> None:
        f = self.model.frame(current.row()) if current.isValid() else None
        if f is None:
            self.detail.clear()
            return
        head = (f"{'OUT ↑' if f.get('dir') == 'out' else 'IN ↓'}  {f.get('name') or type_name(f.get('type', 0))}"
                f" (0x{int(f.get('type', 0)):02X})  ·  {f.get('size', 0)} bytes on the wire"
                f"{'  ·  chaos: ' + str(f['chaos']) if f.get('chaos') else ''}\n\n")
        try:
            body = json.dumps(_shorten(f.get("payload")), indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            body = repr(f.get("payload"))
        self.detail.setPlainText(head + body)
